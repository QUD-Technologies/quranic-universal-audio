/**
 * The Mushaf view's reading state as `<qud-mushaf>` highlights: the recited word
 * (`text`), a band behind the visible part of the current verse, words hidden by
 * the visibility prefs (`hidden`), the Repeat pick spotlight (`pick`) and the
 * surahs the reciter lacks (`inert`). The package resolves them per line; the
 * view's CSS styles the consumer tokens through `::part(hl-<token>)`.
 */
import type { Highlight, HighlightTarget } from '@qud-technologies/mushaf-core';

import type { MushafScope } from '../stores/mushaf';
import type { MushafPosition } from './position';
import { refKey, type VerseRef } from './repeat-plan';

const SURAH_COUNT = 114;

export interface ReadingState {
    pos: MushafPosition | null;
    /** The chapter's first recited word: before it is reached everything from it is upcoming. */
    firstAnchor: string;
    scope: MushafScope;
    showUpcoming: boolean;
    /** Verse range lit while picking a Repeat end (either order), or null. */
    spot: [VerseRef, VerseRef] | null;
    /** Surahs the reciter has (the others are inert). */
    playable: ReadonlySet<number>;
    /** Ayah count of a surah. */
    ayahs: (surah: number) => number;
}

const ayahRef = (surah: number, ayah: number): string => `${surah}:${ayah}`;

function lastAyah(st: ReadingState): string {
    return ayahRef(SURAH_COUNT, st.ayahs(SURAH_COUNT));
}

/** Every item before verse `s:a`, or null at 1:1. */
function before(st: ReadingState, s: number, a: number): HighlightTarget | null {
    if (a > 1) return { from: '1:1', to: ayahRef(s, a - 1) };
    return s > 1 ? { from: '1:1', to: ayahRef(s - 1, st.ayahs(s - 1)) } : null;
}

/** Every item after verse `s:a` (its marker included in the verse), or null at the end. */
function after(st: ReadingState, s: number, a: number): HighlightTarget | null {
    if (a < st.ayahs(s)) return { from: ayahRef(s, a + 1), to: lastAyah(st) };
    return s < SURAH_COUNT ? { from: ayahRef(s + 1, 1), to: lastAyah(st) } : null;
}

/** Everything after the last word reached (from the first recited word when none is). */
function upcoming(st: ReadingState, progress: string): HighlightTarget | null {
    if (!progress) return st.firstAnchor ? { from: st.firstAnchor, to: lastAyah(st) } : null;
    const [s, a, w] = progress.split(':').map(Number);
    return { from: `${s}:${a}:${w! + 1}`, to: lastAyah(st) };
}

/** Runs of consecutive surahs the reciter lacks, as ayah ranges. */
function inertRanges(st: ReadingState): HighlightTarget[] {
    const out: HighlightTarget[] = [];
    let start = 0;
    for (let s = 1; s <= SURAH_COUNT + 1; s++) {
        const inert = s <= SURAH_COUNT && !st.playable.has(s);
        if (inert && !start) start = s;
        if (!inert && start) {
            out.push({ from: ayahRef(start, 1), to: ayahRef(s - 1, st.ayahs(s - 1)) });
            start = 0;
        }
    }
    return out;
}

/** The visible part of the current verse, for its band. */
function band(verseKey: string, progress: string, showUpcoming: boolean): HighlightTarget | null {
    if (showUpcoming) return verseKey;
    if (!progress || !progress.startsWith(`${verseKey}:`)) return null;
    return { from: verseKey, to: progress };
}

export function readingHighlights(st: ReadingState): Highlight[] {
    const out: Highlight[] = inertRanges(st).map((target, i) => ({ id: `inert-${i}`, target, style: 'inert' }));
    const picking = st.spot !== null;
    const pos = st.pos;
    // Picking a verse needs every verse visible and clickable.
    const scope = picking ? 'spread' : st.scope;
    const showUpcoming = picking || st.showUpcoming;
    if (pos?.verseKey) {
        const [s, a] = pos.verseKey.split(':').map(Number) as [number, number];
        const hidden = [
            ...(scope === 'verse' ? [before(st, s, a), after(st, s, a)] : []),
            showUpcoming ? null : upcoming(st, pos.progress),
        ].filter((t): t is HighlightTarget => t !== null);
        hidden.forEach((target, i) => out.push({ id: `hidden-${i}`, target, style: 'hidden', layer: 1 }));
        const b = band(pos.verseKey, pos.progress, showUpcoming);
        if (b) out.push({ id: 'verse', target: b, style: 'band' });
    }
    if (pos?.active) out.push({ id: 'active', target: pos.active, style: 'text', layer: 2 });
    if (st.spot) {
        const [x, y] = st.spot;
        out.push({ id: 'pick', target: { from: refKey(x), to: refKey(y) }, style: 'pick', layer: 3 });
    }
    return out;
}
