#!/usr/bin/env node
/**
 * Mushaf view behaviour checklist: drives the running app with Playwright through every
 * behaviour the view must keep (follow, page turns, clicks, Repeat pick, visibility prefs,
 * bands, keys, full screen, layouts, font fallbacks) and writes one JSON verdict + one
 * screenshot per item. Run once per PR that touches the view.
 *
 *   cd inspector/frontend
 *   node ../../scripts/diagnostics/mushaf_parity_checklist.mjs --url http://localhost:5173 --out DIR
 *
 * Needs a running stack with a Vite dev server (`python scripts/devenv/launch.py`); the
 * script reads and sets the app's stores through Vite's module URLs. `--reciter` must have
 * every chapter timed; `--partial` must lack surah 4 but have 3 and 5 (the inert check).
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const requireFromCwd = createRequire(resolve(process.cwd(), 'package.json'));
const pw = await import(pathToFileURL(requireFromCwd.resolve('playwright')).href);
const { chromium } = pw.default ?? pw;

const { values } = parseArgs({
    options: {
        url: { type: 'string', default: 'http://localhost:5173' },
        out: { type: 'string' },
        reciter: { type: 'string', default: 'abdulbasit_abdulsamad_tarteel' },
        partial: { type: 'string', default: 'islam_sobhi_mp3quran' },
    },
});
if (!values.out) {
    console.error('usage: mushaf_parity_checklist.mjs --out DIR [--url U] [--reciter SLUG] [--partial SLUG]');
    process.exit(2);
}
const OUT = resolve(values.out);
mkdirSync(OUT, { recursive: true });
const WAIT_MS = 60_000;
const SPREAD = { width: 1600, height: 1000 };
const ONE_PAGE = { width: 760, height: 1000 };
const SCREEN = { width: 2560, height: 1440 };

// ---- in-page helpers ---------------------------------------------------------------------------

const mod = {
    mushaf: '/src/tabs/timestamps/stores/mushaf.ts',
    player: '/src/lib/stores/player-context.ts',
    dash: '/src/lib/playback/dash-port.ts',
    repeat: '/src/tabs/timestamps/mushaf/repeat.svelte.ts',
    full: '/src/tabs/timestamps/mushaf/fullscreen.svelte.ts',
    chapter: '/src/lib/recitation-data/load-chapter.ts',
};

/** Book state, read off the element. */
function book() {
    const el = document.querySelector('qud-mushaf');
    const st = el?.state;
    return st && {
        layout: st.layout,
        pages: st.current,
        perView: st.pagesPerView,
        highlights: el.highlights,
        busy: !!el.shadowRoot.querySelector('.qm-track.qm-animate'),
        bands: el.shadowRoot.querySelectorAll('.qm-view[data-slot="current"] .qm-band').length,
        fullscreen: document.documentElement.classList.contains('mushaf-full'),
        picking: document.querySelector('.mv')?.classList.contains('picking') ?? false,
        back: !!document.querySelector('.mv-back'),
    };
}

/** Client centre (and computed style) of item `anchor` (`s:a:w` or a marker `s:a`) in view. */
function itemAt(anchor) {
    const el = document.querySelector('qud-mushaf');
    const ctl = el?.controller;
    if (!ctl) return null;
    for (const page of ctl.state.current) {
        for (const line of ctl.pageView(page).lines) {
            const i = line.words.findIndex((w) => w.anchor === anchor);
            if (i < 0) continue;
            const node = el.shadowRoot.querySelector(
                `.qm-view[data-slot="current"] .qm-page[data-page="${page}"] .qm-line[data-line="${line.index}"] > .qm-w[data-i="${i}"]`,
            );
            if (!node) return null;
            const r = node.getBoundingClientRect();
            const cs = getComputedStyle(node);
            return { x: r.left + r.width / 2, y: r.top + r.height / 2, visibility: cs.visibility, cursor: cs.cursor, opacity: cs.opacity };
        }
    }
    return null;
}

async function player() {
    const { playerContext } = await import(MOD.player);
    const { dashPort } = await import(MOD.dash);
    let ctx;
    playerContext.subscribe((v) => { ctx = v; })();
    return { surah: ctx.surahNum, slug: ctx.delivery?.slug, ms: Math.round(dashPort.currentTimeMs()), paused: dashPort.paused };
}

// ---- driver ------------------------------------------------------------------------------------

const results = [];
let page;

async function evalIn(fn, arg) {
    return page.evaluate(`(${fn.toString()})(${JSON.stringify(arg ?? null)})`.replace(/MOD\.(\w+)/g, (_, k) => JSON.stringify(mod[k])));
}

async function until(fn, arg, test, ms = WAIT_MS) {
    const deadline = Date.now() + ms;
    let last;
    while (Date.now() < deadline) {
        last = await evalIn(fn, arg);
        if (last && test(last)) return last;
        await page.waitForTimeout(250);
    }
    throw new Error(`timed out; last ${JSON.stringify(last)?.slice(0, 400)}`);
}

async function stores(code) {
    return page.evaluate(`(async () => { const M = ${JSON.stringify(mod)};
        const mushaf = await import(M.mushaf); const { playerContext } = await import(M.player);
        const { dashPort } = await import(M.dash); const { mushafRepeat } = await import(M.repeat);
        const { mushafFullscreen } = await import(M.full); ${code} })()`);
}

async function check(id, run) {
    const t = Date.now();
    try {
        const evidence = await run();
        results.push({ id, pass: true, evidence, ms: Date.now() - t });
        console.log(`PASS ${id} ${JSON.stringify(evidence).slice(0, 200)}`);
    } catch (err) {
        results.push({ id, pass: false, error: String(err?.message ?? err), ms: Date.now() - t });
        console.log(`FAIL ${id} ${String(err?.message ?? err).slice(0, 300)}`);
    }
    await page.screenshot({ path: join(OUT, `${id}.png`) }).catch(() => {});
}

async function open(browser, viewport, reciter, year = '1421', block = []) {
    const context = await browser.newContext({ viewport, screen: SCREEN, deviceScaleFactor: 1, colorScheme: 'dark' });
    await context.addInitScript(({ reciter: r, year: y }) => {
        localStorage.setItem('insp_active_tab', 'timestamps');
        localStorage.setItem('insp_ts_reciter', r);
        localStorage.setItem('ts_mushaf_mode', 'true');
        localStorage.setItem('ts_mushaf_year', y);
        localStorage.setItem('ts_mushaf_scope', 'spread');
        localStorage.setItem('ts_mushaf_show_upcoming', 'true');
    }, { reciter, year });
    for (const file of block) await context.route(`**/api/static/quran-data/${file}`, (r) => r.abort());
    page = await context.newPage();
    await page.goto(values.url, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelector('.container'), null, { timeout: WAIT_MS });
    await page.evaluate(async () => (await import('/src/lib/utils/active-tab.ts')).setActiveTab('timestamps'));
    await page.waitForFunction(() => document.querySelector('qud-mushaf')?.state, null, { timeout: WAIT_MS * 3 });
    return context;
}

/**
 * Open chapter `surah` paused at its start, with its timings in (the first verse's band drawn).
 * `band: false` for a reciter that opens with an unprinted reading (no band at 0 ms).
 */
async function chapter(surah, { band = true } = {}) {
    const deadline = Date.now() + WAIT_MS * 2; // a reciter's first chapter can be slow to fetch
    let last;
    while (Date.now() < deadline) {
        await stores(`playerContext.update((s) => ({ ...s, surahNum: ${surah}, positionMs: 0, isPlaying: false }));
            if (!dashPort.paused) dashPort.pause(); if (dashPort.currentTimeMs() > 0) dashPort.seek(0);`);
        await page.waitForTimeout(1500);
        const b = await evalIn(book);
        const p = await evalIn(player);
        last = { p, pages: b?.pages, busy: b?.busy, verse: b?.highlights.find((h) => h.id === 'verse')?.target };
        const banded = b?.highlights.some((h) => h.id === 'verse' && String(h.target).startsWith(`${surah}:`));
        if (p.surah === surah && b && !b.busy && (banded || !band)) return b;
    }
    throw new Error(`chapter ${surah} never settled; last ${JSON.stringify(last)}`);
}

/**
 * Resize the viewport but keep the emulated screen: Playwright's setViewportSize resets
 * screen to the viewport, which the view reads as the window going OS full screen.
 */
const sessions = new WeakMap();
async function resize(viewport) {
    // The override lives as long as its CDP session: keep one per page.
    if (!sessions.has(page)) sessions.set(page, await page.context().newCDPSession(page));
    await sessions.get(page).send('Emulation.setDeviceMetricsOverride', {
        ...viewport, deviceScaleFactor: 1, mobile: false, screenWidth: SCREEN.width, screenHeight: SCREEN.height,
    });
}

async function click(anchor) {
    const pt = await evalIn(itemAt, anchor);
    if (!pt) throw new Error(`${anchor} not in view`);
    await page.mouse.click(pt.x, pt.y);
    return pt;
}

async function pauseAt(test) {
    const p = await until(player, null, test);
    await stores('dashPort.pause();');
    return p;
}

async function verseStartMs(slug, surah, ref) {
    return page.evaluate(async ({ slug: s, surah: c, ref: r, m }) => {
        const { loadChapterRecitation } = await import(m);
        const data = await loadChapterRecitation(s, c);
        const units = data.units.filter((u) => (r.split(':').length === 3 ? u.location === r : u.ayahKey === r));
        return Math.round(Math.min(...units.map((u) => u.intervals[0].start)) * 1000);
    }, { slug, surah, ref, m: mod.chapter });
}

const near = (a, b, tol = 400) => Math.abs(a - b) <= tol;

async function main() {
    const browser = await chromium.launch();
    try {
        // ---- spread viewport, full reciter ----
        let ctx = await open(browser, SPREAD, values.reciter);
        const R = values.reciter;

        await check('spread-auto', async () => {
            const two = await chapter(2);
            await resize(ONE_PAGE);
            const one = await until(book, null, (b) => b.perView === 1 && !b.busy);
            await resize(SPREAD);
            const back = await until(book, null, (b) => b.perView === 2 && !b.busy);
            return { wide: two.pages, narrow: one.pages, wideAgain: back.pages };
        });

        await check('word-click-seeks', async () => {
            await chapter(2);
            const want = await verseStartMs(R, 2, '2:5:3');
            await click('2:5:3');
            const p = await pauseAt((x) => !x.paused && near(x.ms, want, 600));
            const b = await until(book, null, (x) => x.highlights.some((h) => h.id === 'active' && h.target === '2:5:3'));
            return { wordStartMs: want, playerMs: p.ms, active: '2:5:3', bands: b.bands };
        });

        await check('verse-bands-across-lines', async () => {
            const b = await until(book, null, (x) => x.highlights.some((h) => h.id === 'verse' && h.target === '2:5'));
            if (b.bands < 2) throw new Error(`verse 2:5 drew ${b.bands} band(s)`);
            return { verse: '2:5', bands: b.bands };
        });

        await check('verse-marker-seeks-verse', async () => {
            // A marker ends its verse: it seeks that verse's start.
            const want = await verseStartMs(R, 2, '2:3');
            await click('2:3');
            const p = await pauseAt((x) => near(x.ms, want, 600));
            return { marker: '2:3', verseStartMs: want, playerMs: p.ms };
        });

        await check('other-surah-click-switches-chapter', async () => {
            await chapter(2);
            await click('1:3:1');
            const p = await until(player, null, (x) => x.surah === 1 && !x.paused);
            const want = await verseStartMs(R, 1, '1:3:1');
            const at = await pauseAt((x) => x.surah === 1 && near(x.ms, want, 800));
            return { clicked: '1:3:1', surahNow: p.surah, wordStartMs: want, playerMs: at.ms };
        });

        await check('follow-auto-page-turn', async () => {
            await chapter(2);
            await click('2:5:1'); // reading on from the last verse of page 2
            const b = await until(book, null, (x) => x.pages.includes(3) && !x.busy, 90_000);
            await stores('dashPort.pause();');
            const active = b.highlights.find((h) => h.id === 'active' || h.id === 'verse');
            return { from: [1, 2], to: b.pages, recitedNow: active?.target };
        });

        await check('keys-arrows-and-back-to-reciting', async () => {
            await chapter(2);
            await page.mouse.click(5, 300); // focus the page, not the element
            await page.keyboard.press('ArrowLeft');
            const fwd = await until(book, null, (b) => b.pages.includes(3) && !b.busy);
            const back = await until(book, null, (b) => b.back);
            await page.keyboard.press('ArrowRight');
            const home = await until(book, null, (b) => b.pages.includes(1) && !b.busy && !b.back);
            return { afterLeft: fwd.pages, backToReciting: back.back, afterRight: home.pages, following: !home.back };
        });

        await check('keys-f-esc-full-screen', async () => {
            await page.mouse.click(5, 300);
            const before = await stores('document.activeElement?.blur(); return { on: mushafFullscreen.on, focus: document.activeElement?.tagName };');
            if (before.on) throw new Error('full screen already on before F');
            await page.keyboard.press('KeyF');
            const on = await until(book, null, (b) => b.fullscreen);
            await page.keyboard.press('Escape');
            const off = await until(book, null, (b) => !b.fullscreen);
            return { before, fOn: on.fullscreen, escOff: !off.fullscreen };
        });

        await check('keys-m-toggles-view', async () => {
            await page.keyboard.press('KeyM');
            await page.waitForFunction(() => !document.querySelector('qud-mushaf'), null, { timeout: WAIT_MS });
            await page.keyboard.press('KeyM');
            await page.waitForFunction(() => document.querySelector('qud-mushaf')?.state, null, { timeout: WAIT_MS });
            return { off: true, on: true };
        });

        await check('hide-upcoming', async () => {
            await chapter(2);
            await click('2:5:3');
            await pauseAt((x) => !x.paused);
            await stores('mushaf.mushafShowUpcoming.set(false);');
            const b = await until(book, null, (x) => x.highlights.some((h) => h.style === 'hidden'));
            const next = await until(itemAt, '2:5:5', (x) => x.visibility === 'hidden');
            const done = await evalIn(itemAt, '2:5:1');
            await stores('mushaf.mushafShowUpcoming.set(true);');
            return { hidden: b.highlights.filter((h) => h.style === 'hidden').map((h) => h.target), after: next.visibility, before: done.visibility };
        });

        await check('current-verse-only', async () => {
            await stores("mushaf.mushafScope.set('verse');");
            const b = await until(book, null, (x) => x.highlights.filter((h) => h.style === 'hidden').length >= 2);
            const other = await until(itemAt, '2:2:1', (x) => x.visibility === 'hidden');
            const own = await evalIn(itemAt, '2:5:1');
            await stores("mushaf.mushafScope.set('spread');");
            return { hidden: b.highlights.filter((h) => h.style === 'hidden').map((h) => h.target), otherVerse: other.visibility, ownVerse: own.visibility };
        });

        await check('repeat-pick-spotlight', async () => {
            await stores("mushafFullscreen.exit(); mushafRepeat.picking = 'from';");
            await until(book, null, (b) => b.picking && !b.fullscreen && !b.busy);
            await page.waitForTimeout(500); // the chrome settles back
            const pt = await evalIn(itemAt, '2:3:2');
            await page.mouse.move(5, 300);
            await page.mouse.move(pt.x, pt.y, { steps: 5 });
            const lit = await until(book, null, (b) => b.highlights.some((h) => h.id === 'pick'));
            const on = await until(itemAt, '2:3:2', (x) => x.opacity === '1'); // parts land a frame later
            const dim = await evalIn(itemAt, '2:1:1');
            await page.mouse.click(pt.x, pt.y);
            const picked = await page.evaluate(async (m) => {
                const { mushafRepeat } = await import(m);
                return { from: mushafRepeat.from, picking: mushafRepeat.picking };
            }, mod.repeat);
            return { pick: lit.highlights.find((h) => h.id === 'pick').target, dimOpacity: dim.opacity, litOpacity: on.opacity, picked };
        });

        for (const year of ['1405', '1441', '1421']) {
            await check(`layout-${year}`, async () => {
                await stores(`mushaf.mushafYear.set('${year}');`);
                const b = await until(book, null, (x) => x.layout === `madani-${year}` && !x.busy);
                const fonts = await page.evaluate(() => [...document.fonts].filter((f) => f.family.startsWith('qm-') && f.status === 'loaded').map((f) => f.family));
                return { layout: b.layout, pages: b.pages, fonts };
            });
        }
        await ctx.close();

        // ---- inert words and skipped pages: a reciter without surah 4 ----
        await check('inert-words-and-skipped-pages', async () => {
            ctx = await open(browser, SPREAD, values.partial);
            await chapter(3, { band: false }); // this reciter opens with the isti'adha
            await stores("document.querySelector('qud-mushaf').goTo('3:200:1');");
            await until(book, null, (b) => b.pages.includes(76) && !b.busy);
            const nextPages = (await (async () => {
                await page.mouse.click(5, 300);
                await page.keyboard.press('ArrowLeft');
                return until(book, null, (b) => !b.pages.includes(76) && !b.busy);
            })()).pages;
            // Pages 77-105 hold only surah 4: the turn skips them, landing on 106 (4:176 + 5).
            if (!nextPages.includes(106)) throw new Error(`turn from 76 landed on ${nextPages}`);
            const b = await evalIn(book);
            const word = await evalIn(itemAt, '4:176:1');
            if (!word) throw new Error('4:176:1 not in view');
            await page.mouse.click(word.x, word.y);
            await page.waitForTimeout(1500);
            const p = await evalIn(player);
            if (p.surah !== 3) throw new Error(`an inert word switched to surah ${p.surah}`);
            return { inert: b.highlights.filter((h) => h.style === 'inert').map((h) => h.target).slice(0, 4), cursor: word.cursor, surahAfterClick: p.surah, nextFrom76: nextPages };
        });
        await ctx.close();

        // ---- font fallbacks: block DK v1, the surah-name and the frame fonts ----
        await check('font-fallbacks', async () => {
            ctx = await open(browser, SPREAD, values.reciter, '1405', ['DigitalKhattV1.woff2', 'SurahNameV2.woff2', 'QuranCommon.woff2']);
            await chapter(2);
            const info = await page.evaluate(() => {
                const el = document.querySelector('qud-mushaf');
                const root = el.shadowRoot.querySelector('.qm-root');
                const surah = el.shadowRoot.querySelector('.qm-view[data-slot="current"] .qm-surah');
                return {
                    layout: el.state.layout,
                    textFont: root.style.getPropertyValue('--qm-text-font'),
                    plainFrame: surah?.classList.contains('qm-surah-plain') ?? null,
                    surahLabel: surah?.textContent?.trim() ?? null,
                };
            });
            if (!info.textFont.includes('digital-khatt-v2')) throw new Error(`text font ${info.textFont}`);
            return info;
        });
        await ctx?.close();
    } finally {
        await browser.close();
        writeFileSync(join(OUT, 'checklist.json'), JSON.stringify(results, null, 2));
        console.log(`${results.filter((r) => r.pass).length}/${results.length} passed -> ${OUT}`);
    }
}

await main();
