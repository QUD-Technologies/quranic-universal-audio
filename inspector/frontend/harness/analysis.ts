/**
 * Analysis-view render harness (dev / screenshot tool). Mounts the REAL
 * `TimedAnalysisRow` for one reciter:verse straight from shards via the real
 * `ts-source` assembly — no SPA, no audio, no waveform, no progress-bar math.
 *
 * Driven by URL params: `?reciter=<slug>&ref=45:32[&words=1-3]`. `/api` is
 * proxied to a backend (set `INSPECTOR_API_TARGET` to the dev Space so no local
 * Flask is needed). Because it imports the production component + assembly,
 * there is zero rendering drift — TypeScript fails the build if their API moves.
 *
 * Driven headlessly by `.claude/skills/inspector-playwright/scripts/shoot.mjs`,
 * which waits for `document.body.dataset.ready` and screenshots `#app`.
 */
import '../src/styles/tokens.css';
import '../src/styles/base.css';
import '@quranic-phonemizer/cells/cells.css';
import '../src/styles/highlight-constants.css';
import '../src/styles/timestamps.css';

import { mount } from 'svelte';

import { isWordShard } from '../src/lib/types/ts-client';
import {
    assembleOccasion,
    assembleWaslGroup,
    loadChapterShard,
    loadDk,
    loadManifest,
    loadQpc,
    reciterAudioFromManifest,
    shardOccasions,
} from '../src/lib/recitation-data/ts-source';
import { waslGroupOf } from '../src/lib/recitation-data/wasl';
import { editionFontStack, ensureEditionFont } from '../src/lib/refs/edition-font';
import { DEFAULT_SDK_RIWAYAH, toInspectorSlug } from '../src/lib/riwayat';
import TimedAnalysisRow from '../src/tabs/timestamps/components/TimedAnalysisRow.svelte';
import WordTimedRow from '../src/tabs/timestamps/components/WordTimedRow.svelte';
import {
    deliveryRiwayah,
    showLetters,
    showPhonemes,
    wordProfile,
} from '../src/tabs/timestamps/stores/display';
import { setRuleEnabled } from '../src/tabs/timestamps/stores/tajweed-settings';
import { focusWaslGroup, loadedVerse } from '../src/tabs/timestamps/stores/verse';
import type { TsFocusWaslGroup } from '../src/tabs/timestamps/stores/verse';
import { LEGEND_KEYS } from '../src/tabs/timestamps/utils/tajweed-rules';

async function render(): Promise<void> {
    const p = new URLSearchParams(location.search);
    const reciter = p.get('reciter');
    const ref = p.get('ref'); // e.g. "45:32"
    if (!reciter || !ref) throw new Error('need ?reciter=<slug>&ref=<surah:verse>');
    const chapter = parseInt(ref.split(':')[0] ?? '0', 10);

    const manifest = await loadManifest();
    const reciterAudio = reciterAudioFromManifest(manifest, reciter);
    if (!reciterAudio) throw new Error(`reciter not advertised by the TS manifest: ${reciter}`);

    const [shard, qpc, dk] = await Promise.all([
        loadChapterShard(reciter, chapter),
        loadQpc(),
        loadDk(),
    ]);

    // The edition the shard was stamped in decides the script the row must be
    // typeset in — same two lines the tab runs. Without them every non-Hafs
    // edition renders in DigitalKhatt, which disagrees with the packaged QPC
    // faces on ligatures and (especially) on where a lone waqf mark sits.
    // A projected edition ships the WORD profile (no cells), which the tab
    // renders with a different component — mount the same one it would, or the
    // row comes out empty for every non-Hafs delivery.
    const wordShard = isWordShard(shard);
    wordProfile.set(wordShard);
    const riwayah = shard._meta.riwayah ?? DEFAULT_SDK_RIWAYAH;
    deliveryRiwayah.set(riwayah);
    ensureEditionFont(toInspectorSlug(riwayah));
    document.documentElement.style.setProperty(
        '--font-quran',
        editionFontStack(toInspectorSlug(riwayah)),
    );

    // Find the occasion for `ref` (first occurrence). `&wasl=1` renders the
    // cross-verse waṣl GROUP it belongs to (context-merge) instead of the lone
    // verse — the exact merged data `assembleWaslGroup` feeds `TimedAnalysisRow`,
    // so the junction-idgham synthesis is screenshotted on real shard data.
    // A verse the reciter broke mid-way has several occasions; `&occ=N` picks
    // the Nth (1-based, default the first) — the pause that carries a stop sign
    // is the last boundary of an occasion, so it is reachable only this way.
    const occasions = shardOccasions(shard);
    const matches = occasions.map((o, i) => [o, i] as const).filter(([o]) => o.ref === ref);
    if (!matches.length) throw new Error(`no verse ${ref} in ${reciter} chapter ${chapter}`);
    const nth = Math.max(1, parseInt(p.get('occ') ?? '1', 10));
    const picked = matches[Math.min(nth, matches.length) - 1]!;
    const focusIdx = picked[1];
    let data;
    let group: TsFocusWaslGroup | null = null;
    if (p.get('wasl') === '1') {
        const g = waslGroupOf(occasions, focusIdx);
        const members = occasions.slice(g.fromIdx, g.toIdx + 1);
        data = assembleWaslGroup(reciter, members, ref, qpc, dk, reciterAudio, '');
        group = { data, span: [g.startMs, g.endMs] as [number, number], refs: g.refs, focusRef: ref };
    } else {
        data = assembleOccasion(reciter, occasions[focusIdx]!, qpc, dk, reciterAudio, '');
    }

    // Both tiers ON by default (the phoneme row defaults OFF in the app) — the
    // whole point of the harness is the letter↔phoneme alignment. `&letters=0` /
    // `&phonemes=0` opt out of either tier.
    showLetters.set(p.get('letters') !== '0');
    showPhonemes.set(p.get('phonemes') !== '0');

    // `&alltj=1` force-enables every tajweed rule (incl. the default-off iẓhar +
    // madd ṭabīʿī) so a screenshot shows the full underline set, not just the defaults.
    if (p.get('alltj') === '1') for (const k of LEGEND_KEYS) setRuleEnabled(k, true);

    loadedVerse.set({ data, tsSegOffset: 0, tsSegEnd: Number.MAX_SAFE_INTEGER });
    focusWaslGroup.set(group);
    mount(wordShard ? WordTimedRow : TimedAnalysisRow, {
        target: document.getElementById('app')!,
    });

    // Let web fonts + recomputeRowGap (ResizeObserver) settle before flagging ready.
    await (document as unknown as { fonts?: { ready: Promise<unknown> } }).fonts?.ready;
    requestAnimationFrame(() =>
        requestAnimationFrame(() => { document.body.dataset.ready = '1'; }),
    );
}

render().catch((e) => {
    document.body.dataset.error = String(e?.message ?? e);
     
    console.error(e);
});
