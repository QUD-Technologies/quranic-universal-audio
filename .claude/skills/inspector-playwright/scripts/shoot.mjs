#!/usr/bin/env node
/**
 * Screenshot the REAL Timestamps analysis view (`UnifiedDisplay`) for one or
 * more reciter:verse refs — by serving the render harness
 * (`inspector/frontend/harness/analysis.html`) through Vite with `/api` proxied
 * to a backend, and driving headless Chromium. No SPA, no audio, no OAuth, no
 * progress-bar pixel math; the harness imports the production component +
 * assembly, so there is no rendering drift.
 *
 * Run FROM inspector/frontend (so `vite` + `playwright` resolve from its
 * node_modules):
 *   node <skill>/scripts/shoot.mjs --reciter <slug> --ref 45:32 [--ref 11:57] \
 *       [--words 1-3] [--out DIR] [--api https://…hf.space] [--port 5199]
 *
 * A ref may name an occasion: `--ref 2:26@3` shoots the THIRD time the reciter
 * started that verse (default the first). A verse the reciter broke mid-way has
 * one occasion per run, and a stop sign lives on an occasion's last boundary.
 */
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

// `vite` + `playwright` live in inspector/frontend/node_modules, but this script
// sits in the skill dir — resolve them against the frontend cwd, not here.
const requireFromCwd = createRequire(resolve(process.cwd(), 'package.json'));
const viteMod = await import(pathToFileURL(requireFromCwd.resolve('vite')));
const pwMod = await import(pathToFileURL(requireFromCwd.resolve('playwright')));
const createServer = viteMod.createServer ?? viteMod.default?.createServer;
const chromium = pwMod.chromium ?? pwMod.default?.chromium;

const { values } = parseArgs({
    options: {
        reciter: { type: 'string' },
        ref: { type: 'string', multiple: true },
        words: { type: 'string' },
        out: { type: 'string', default: '.' },
        api: { type: 'string', default: 'https://hetchyy-quranic-inspector-dev.hf.space' },
        port: { type: 'string', default: '5199' },
        width: { type: 'string', default: '1600' },
        alltj: { type: 'boolean', default: false },
        wasl: { type: 'boolean', default: false },
    },
});

if (!values.reciter || !values.ref?.length) {
    console.error('usage: --reciter <slug> --ref <surah:verse[@occasion]> [--ref …] [--words a-b] [--out DIR] [--api URL] [--port N]');
    process.exit(2);
}

const port = Number(values.port);
const out = resolve(process.cwd(), values.out);
mkdirSync(out, { recursive: true });

// The Vite config reads these — proxy `/api` at the remote Space, fixed port.
process.env.INSPECTOR_API_TARGET = values.api;
process.env.INSPECTOR_VITE_PORT = String(port);

const server = await createServer({ server: { port, strictPort: true } });
await server.listen();

const browser = await chromium.launch();
const page = await browser.newPage({
    viewport: { width: Number(values.width), height: 600 },
    deviceScaleFactor: 2,
});

let ok = 0;
for (const spec of values.ref) {
    const [ref, occ] = spec.split('@');
    const qs = new URLSearchParams({ reciter: values.reciter, ref });
    if (occ) qs.set('occ', occ);
    if (values.words) qs.set('words', values.words);
    if (values.alltj) qs.set('alltj', '1');
    if (values.wasl) qs.set('wasl', '1');
    const url = `http://localhost:${port}/harness/analysis.html?${qs}`;
    try {
        await page.goto(url, { waitUntil: 'load' });
        await page.waitForFunction(
            () => document.body.dataset.ready === '1' || Boolean(document.body.dataset.error),
            { timeout: 30_000 },
        );
        const err = await page.evaluate(() => document.body.dataset.error);
        if (err) { console.error(`✗ ${spec}: ${err}`); continue; }
        const file = resolve(
            out,
            `${values.reciter}_${ref.replace(/:/g, '-')}${occ ? `_occ${occ}` : ''}.png`,
        );
        await (await page.$('#app')).screenshot({ path: file });
        console.log(`✓ ${spec} → ${file}`);
        ok += 1;
    } catch (e) {
        console.error(`✗ ${spec}: ${e?.message ?? e}`);
    }
}

await browser.close();
await server.close();
console.log(`done: ${ok}/${values.ref.length}`);
process.exit(ok === values.ref.length ? 0 : 1);
