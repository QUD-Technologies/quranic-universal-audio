#!/usr/bin/env node
/**
 * Mushaf view look parity: screenshot the Timestamps Mushaf view over a fixed matrix, then
 * diff two runs pixel by pixel. Run once per PR that touches the Mushaf view.
 *
 *   cd inspector/frontend
 *   node ../../scripts/diagnostics/mushaf_look_parity.mjs capture --url http://localhost:5173 --out DIR/before
 *   ... change the code ...
 *   node ../../scripts/diagnostics/mushaf_look_parity.mjs capture --url http://localhost:5173 --out DIR/after
 *   node ../../scripts/diagnostics/mushaf_look_parity.mjs diff --before DIR/before --after DIR/after --out DIR/diff
 *
 * Needs a running stack (`python scripts/devenv/launch.py`) with a Vite dev server: the script
 * drives the app's own stores through Vite's module URLs (`/src/...`), so it opens the view on
 * a reciter + chapter exactly as a chapter switch does. The reciter must be Hafs with timings
 * for chapter 2 (the active-word shot seeks into it).
 *
 * Matrix: pages 1, 2, 50, 187, 582, 604 (reached by switching to surah 1, 2, 3, 9, 78, 112)
 * x layouts 1405 / 1421 / 1441 x a one-page and a two-page viewport, each paused at the
 * chapter's start (so the first verse carries its band), plus one active-word + verse-band
 * shot (word 2:5:3 clicked, then paused) per layout and viewport. The DOM is read
 * through either renderer: the in-app pages (`.mp-page`) or `<qud-mushaf>`.
 */
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const requireFromCwd = createRequire(resolve(process.cwd(), 'package.json'));
const pw = await import(pathToFileURL(requireFromCwd.resolve('playwright')).href);
const { chromium } = pw.default ?? pw;

const SURAH_OF_PAGE = { 1: 1, 2: 2, 50: 3, 187: 9, 582: 78, 604: 112 };
const LAYOUTS = ['1405', '1421', '1441'];
const VIEWPORTS = { page: { width: 760, height: 1000 }, spread: { width: 1600, height: 1000 } };
const ACTIVE_WORD = '2:5:3';
/** Larger than any viewport: a window the size of the screen enters the view's full screen. */
const SCREEN = { width: 2560, height: 1440 };
const SETTLE_MS = 1200;
const REASK_MS = 5000;
const WAIT_MS = 60_000;
/** Channel difference (0-255, any of r/g/b/a) above which a pixel counts as changed. */
const PIXEL_TOLERANCE = 8;

const { positionals, values } = parseArgs({
    allowPositionals: true,
    options: {
        url: { type: 'string', default: 'http://localhost:5173' },
        out: { type: 'string' },
        before: { type: 'string' },
        after: { type: 'string' },
        reciter: { type: 'string', default: 'abdulbasit_abdulsamad_tarteel' },
        only: { type: 'string' },
    },
});

// ---- in-page helpers (serialised into the page) ------------------------------------------------

/** Pages in view and whether the view is settled, for either renderer. */
function viewState() {
    const el = document.querySelector('qud-mushaf');
    if (el) {
        const st = el.state;
        const busy = !!el.shadowRoot?.querySelector('.qm-track.qm-animate');
        return { pages: st ? st.current : [], busy, ready: !!st };
    }
    const pages = [...document.querySelectorAll('.mv-slot > article.mp-page')].map((a) => Number(a.dataset.page));
    return { pages: pages.sort((a, b) => a - b), busy: !!document.querySelector('.mv-leaf'), ready: pages.length > 0 };
}

async function setStores({ year, surah }) {
    const mushaf = await import('/src/tabs/timestamps/stores/mushaf.ts');
    const { playerContext } = await import('/src/lib/stores/player-context.ts');
    const { dashPort } = await import('/src/lib/playback/dash-port.ts');
    mushaf.mushafScope.set('spread');
    mushaf.mushafShowUpcoming.set(true);
    if (year) mushaf.mushafYear.set(year);
    if (surah) playerContext.update((s) => ({ ...s, surahNum: surah, positionMs: 0, isPlaying: false }));
    dashPort.pause();
}

async function pause() {
    const { dashPort } = await import('/src/lib/playback/dash-port.ts');
    dashPort.pause();
}

/** Pause at the chapter's start; report whether playback is there. */
async function rewind() {
    const { dashPort } = await import('/src/lib/playback/dash-port.ts');
    if (!dashPort.paused) dashPort.pause();
    if (dashPort.currentTimeMs() > 0) dashPort.seek(0);
    return dashPort.paused && dashPort.currentTimeMs() === 0;
}

/** Verse bands drawn on the pages in view, for either renderer. */
function bandCount() {
    const el = document.querySelector('qud-mushaf');
    if (el) return el.shadowRoot.querySelectorAll('.qm-view[data-slot="current"] .qm-band').length;
    return document.querySelectorAll('.mv-slot .mp-band').length;
}

/** Client rect centre of word `anchor` on the pages in view, for either renderer. */
async function wordPoint({ anchor, year }) {
    const el = document.querySelector('qud-mushaf');
    let node = null;
    if (el) {
        const ctl = el.controller;
        for (const page of ctl.state.current) {
            const view = ctl.pageView(page);
            for (const line of view.lines) {
                const i = line.words.findIndex((w) => w.anchor === anchor);
                if (i >= 0) {
                    node = el.shadowRoot.querySelector(
                        `.qm-view[data-slot="current"] .qm-page[data-page="${page}"] .qm-line[data-line="${line.index}"] > .qm-w[data-i="${i}"]`,
                    );
                }
            }
        }
    } else {
        // The in-app pages tag each word with its Digital Khatt id.
        const { loadDk } = await import('/src/lib/recitation-data/ts-source.ts');
        const dk = await loadDk();
        node = document.querySelector(`.mv-slot [data-w="${dk[anchor]?.id}"]`);
    }
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

// ---- capture -----------------------------------------------------------------------------------

/** Wait for the view to hold pages `want`; `again` re-asks (the tab's own start-up may move it). */
async function waitForView(page, want, again) {
    const deadline = Date.now() + WAIT_MS;
    let last = null;
    let nextAsk = Date.now() + REASK_MS;
    while (Date.now() < deadline) {
        if (again && Date.now() > nextAsk) {
            await again();
            nextAsk = Date.now() + REASK_MS;
        }
        last = await page.evaluate(viewState);
        if (last.ready && !last.busy && want.every((p) => last.pages.includes(p))) {
            await page.waitForTimeout(SETTLE_MS);
            const again = await page.evaluate(viewState);
            if (!again.busy && want.every((p) => again.pages.includes(p))) return again.pages;
        }
        await page.waitForTimeout(250);
    }
    throw new Error(`view never showed pages ${want} (last ${JSON.stringify(last)})`);
}

/** Hold playback paused at 0 until the chapter's timings draw the first verse's band. */
async function settleAtStart(page) {
    const deadline = Date.now() + WAIT_MS;
    let stable = 0;
    while (Date.now() < deadline) {
        const atStart = await page.evaluate(rewind);
        stable = atStart && (await page.evaluate(bandCount)) > 0 ? stable + 1 : 0;
        if (stable >= 4) return;
        await page.waitForTimeout(250);
    }
    throw new Error('playback never settled at the chapter start with its verse band drawn');
}

async function shoot(page, file) {
    await page.mouse.move(2, 2);
    await page.evaluate(() => document.fonts.ready);
    const box = await page.evaluate(() => {
        const r = document.querySelector('.mv').getBoundingClientRect();
        return { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
    });
    await page.screenshot({ path: file, clip: box });
}

async function openApp(browser, viewport, year) {
    const context = await browser.newContext({ viewport, screen: SCREEN, deviceScaleFactor: 1, colorScheme: 'dark', reducedMotion: 'reduce' });
    await context.addInitScript(({ reciter, year: y }) => {
        localStorage.setItem('insp_active_tab', 'timestamps');
        localStorage.setItem('insp_ts_reciter', reciter);
        localStorage.setItem('ts_mushaf_mode', 'true');
        localStorage.setItem('ts_mushaf_year', y);
        localStorage.setItem('ts_mushaf_scope', 'spread');
        localStorage.setItem('ts_mushaf_show_upcoming', 'true');
    }, { reciter: values.reciter, year });
    const page = await context.newPage();
    page.on('pageerror', (e) => console.error('pageerror', e.message));
    await page.goto(values.url, { waitUntil: 'domcontentloaded' });
    const { setActiveTab } = await page.evaluate(() => ({ setActiveTab: true }));
    if (setActiveTab) {
        await page.waitForFunction(() => document.querySelector('.container'), null, { timeout: WAIT_MS });
        await page.evaluate(async () => (await import('/src/lib/utils/active-tab.ts')).setActiveTab('timestamps'));
    }
    await page.waitForFunction(() => document.querySelector('.mv'), null, { timeout: WAIT_MS * 3 });
    return { context, page };
}

async function capture() {
    const out = resolve(values.out);
    mkdirSync(out, { recursive: true });
    const browser = await chromium.launch();
    const meta = [];
    try {
        for (const [vpName, viewport] of Object.entries(VIEWPORTS)) {
            for (const year of LAYOUTS) {
                if (values.only && !`${vpName}-${year}`.includes(values.only)) continue;
                const { context, page } = await openApp(browser, viewport, year);
                for (const [p, surah] of Object.entries(SURAH_OF_PAGE)) {
                    const ask = () => page.evaluate(setStores, { year, surah });
                    await ask();
                    await waitForView(page, [Number(p)], ask);
                    await settleAtStart(page);
                    const pages = await waitForView(page, [Number(p)], ask);
                    const name = `${year}-${vpName}-p${String(p).padStart(3, '0')}.png`;
                    await shoot(page, join(out, name));
                    meta.push({ name, pages });
                    console.log(name, pages.join(','));
                }
                // Active word + verse band: click a word of 2:5 (seek + play), pause on it.
                const ask = () => page.evaluate(setStores, { year, surah: 2 });
                await ask();
                await waitForView(page, [2], ask);
                await settleAtStart(page);
                const pt = await page.evaluate(wordPoint, { anchor: ACTIVE_WORD, year });
                if (!pt) throw new Error(`word ${ACTIVE_WORD} not found`);
                await page.mouse.click(pt.x, pt.y);
                await page.waitForTimeout(150);
                await page.evaluate(pause);
                await page.waitForTimeout(SETTLE_MS);
                const name = `${year}-${vpName}-active-${ACTIVE_WORD.replaceAll(':', '-')}.png`;
                await shoot(page, join(out, name));
                meta.push({ name, pages: (await page.evaluate(viewState)).pages });
                console.log(name);
                await context.close();
            }
        }
    } finally {
        await browser.close();
        writeFileSync(join(out, 'shots.json'), JSON.stringify(meta, null, 2));
    }
}

// ---- diff --------------------------------------------------------------------------------------

/** Compare two PNGs in a page (canvas), return the changed-pixel share and a diff image. */
function comparePngs({ a, b, tolerance }) {
    const load = (src) => new Promise((ok, fail) => {
        const img = new Image();
        img.onload = () => ok(img);
        img.onerror = fail;
        img.src = src;
    });
    return Promise.all([load(a), load(b)]).then(([ia, ib]) => {
        const w = Math.max(ia.width, ib.width);
        const h = Math.max(ia.height, ib.height);
        const read = (img) => {
            const c = new OffscreenCanvas(w, h);
            const x = c.getContext('2d');
            x.drawImage(img, 0, 0);
            return x.getImageData(0, 0, w, h).data;
        };
        const da = read(ia);
        const db = read(ib);
        const out = new OffscreenCanvas(w, h);
        const ox = out.getContext('2d');
        const diff = ox.createImageData(w, h);
        let changed = 0;
        for (let i = 0; i < da.length; i += 4) {
            const inA = (i / 4) % w < ia.width && Math.floor(i / 4 / w) < ia.height;
            const inB = (i / 4) % w < ib.width && Math.floor(i / 4 / w) < ib.height;
            const d = Math.max(
                Math.abs(da[i] - db[i]), Math.abs(da[i + 1] - db[i + 1]),
                Math.abs(da[i + 2] - db[i + 2]), Math.abs(da[i + 3] - db[i + 3]),
            );
            const hit = inA !== inB || d > tolerance;
            if (hit) changed++;
            const grey = (da[i] + da[i + 1] + da[i + 2]) / 12;
            diff.data[i] = hit ? 255 : grey;
            diff.data[i + 1] = hit ? 0 : grey;
            diff.data[i + 2] = hit ? 80 : grey;
            diff.data[i + 3] = 255;
        }
        ox.putImageData(diff, 0, 0);
        return out.convertToBlob({ type: 'image/png' }).then(async (blob) => {
            const bytes = new Uint8Array(await blob.arrayBuffer());
            let bin = '';
            for (const byte of bytes) bin += String.fromCharCode(byte);
            return {
                percent: (100 * changed) / (w * h),
                size: [ia.width, ia.height, ib.width, ib.height],
                png: btoa(bin),
            };
        });
    });
}

async function diff() {
    const before = resolve(values.before);
    const after = resolve(values.after);
    const out = resolve(values.out);
    mkdirSync(out, { recursive: true });
    const browser = await chromium.launch();
    const page = await browser.newPage();
    const rows = [];
    try {
        for (const name of readdirSync(before).filter((f) => f.endsWith('.png')).sort()) {
            const uri = (dir) => `data:image/png;base64,${readFileSync(join(dir, name)).toString('base64')}`;
            const r = await page.evaluate(comparePngs, { a: uri(before), b: uri(after), tolerance: PIXEL_TOLERANCE });
            writeFileSync(join(out, name), Buffer.from(r.png, 'base64'));
            rows.push({ name, percent: Number(r.percent.toFixed(3)), size: r.size });
            console.log(`${r.percent.toFixed(3).padStart(8)}%  ${name}  ${r.size.join('x')}`);
        }
    } finally {
        await browser.close();
        writeFileSync(join(out, 'diff.json'), JSON.stringify(rows, null, 2));
    }
}

const command = positionals[0];
if (command === 'capture' && values.out) await capture();
else if (command === 'diff' && values.before && values.after && values.out) await diff();
else {
    console.error('usage: mushaf_look_parity.mjs capture --out DIR [--url U] [--reciter SLUG] [--only page-1421]\n'
        + '       mushaf_look_parity.mjs diff --before DIR --after DIR --out DIR');
    process.exit(2);
}
