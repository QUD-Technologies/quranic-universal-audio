/**
 * Canonical take per verse — the clean, no-repeat recitation of each verse.
 *
 * Mirrors the release export's `_canonical` (`qua_shared/timestamps_native.py`)
 * over the frontend's word occurrences: a verse's occurrences are split into
 * occasions wherever another verse was recited in between; the first occasion
 * that covers every word wins, cut where it completes; a leading false start
 * is dropped by restarting at the last word-1 occurrence that still completes
 * the verse. Within-take lookbacks stay inside the take.
 *
 * Used by Repeat so a looped verse plays one clean take — never the reciter's
 * re-dos. Normal listening plays the audio as recorded.
 */
import type { AnimUnit } from '../../../lib/recitation-animation/types';

export interface VerseTake {
    /** Chapter-absolute ms. */
    startMs: number;
    endMs: number;
}

interface Occ {
    ayahKey: string;
    word: number;
    start: number;
    end: number;
}

function flatten(units: AnimUnit[]): Occ[] {
    const out: Occ[] = [];
    for (const u of units) {
        for (const iv of u.intervals) out.push({ ayahKey: u.ayahKey, word: u.word, start: iv.start, end: iv.end });
    }
    return out.sort((a, b) => a.start - b.start || a.word - b.word);
}

function completion(occ: Occ[], wordCount: number): number {
    const covered = new Set<number>();
    for (let i = 0; i < occ.length; i++) {
        covered.add(occ[i]!.word);
        if (covered.size >= wordCount) return i;
    }
    return -1;
}

function coversFrom(occ: Occ[], from: number, wordCount: number): boolean {
    const covered = new Set<number>();
    for (let i = from; i < occ.length; i++) covered.add(occ[i]!.word);
    return covered.size >= wordCount;
}

function splitOccasions(own: Occ[], all: Occ[], ayahKey: string): Occ[][] {
    const foreign = all.filter((o) => o.ayahKey !== ayahKey).map((o) => o.start);
    const out: Occ[][] = [];
    let cur: Occ[] = [];
    let fi = 0;
    for (const o of own) {
        const prev = cur.at(-1);
        if (prev) {
            while (fi < foreign.length && foreign[fi]! <= prev.start) fi++;
            if (fi < foreign.length && foreign[fi]! < o.start) {
                out.push(cur);
                cur = [];
            }
        }
        cur.push(o);
    }
    if (cur.length) out.push(cur);
    return out;
}

function canonicalOf(occasions: Occ[][], wordCount: number): Occ[] {
    const completing = occasions.find((o) => completion(o, wordCount) >= 0);
    const chosen = completing
        ?? occasions.reduce((best, o) => (new Set(o.map((x) => x.word)).size > new Set(best.map((x) => x.word)).size ? o : best));
    const end = completion(chosen, wordCount);
    const kept = end >= 0 ? chosen.slice(0, end + 1) : chosen;
    let restart = 0;
    for (let i = 1; i < kept.length; i++) {
        if (kept[i]!.word === 1 && coversFrom(kept, i, wordCount)) restart = i;
    }
    return kept.slice(restart);
}

/** ayahKey → its canonical take (chapter-absolute ms). */
export function canonicalTakes(units: AnimUnit[]): Map<string, VerseTake> {
    const all = flatten(units);
    const wordCount = new Map<string, number>();
    // Distinct recited words — one unit per word location, so a verse missing a
    // word still has a reachable target.
    for (const u of units) wordCount.set(u.ayahKey, (wordCount.get(u.ayahKey) ?? 0) + 1);
    const byVerse = new Map<string, Occ[]>();
    for (const o of all) {
        const list = byVerse.get(o.ayahKey);
        if (list) list.push(o);
        else byVerse.set(o.ayahKey, [o]);
    }
    const out = new Map<string, VerseTake>();
    for (const [key, own] of byVerse) {
        const take = canonicalOf(splitOccasions(own, all, key), wordCount.get(key) ?? 0);
        if (!take.length) continue;
        out.set(key, {
            startMs: Math.round(Math.min(...take.map((o) => o.start)) * 1000),
            endMs: Math.round(Math.max(...take.map((o) => o.end)) * 1000),
        });
    }
    return out;
}
