/**
 * Pure structure builder — extracted verbatim from the timestamps-tab
 * `AnimationDisplay.buildStructure()` so the same char-grouping + MFA
 * letter-alignment + cross-word group-ID merge logic drives every animation
 * surface. No Svelte / store dependency.
 *
 * Output `AnimWord[]` is rendered declaratively; per-frame highlight updates
 * are applied imperatively via `engine/highlight.ts`.
 */

import { charsMatch, isCombiningMark, splitIntoCharGroups, ZWSP } from '../../utils/arabic-text';
import { type Decorator, splitDecorators } from '../decorators';
import type { TimeSpan } from '../types';

/** Non-recited symbols rendered in place but never highlighted (no MFA letter):
 *  rub-el-hizb (U+06DE) and the place-of-sajdah mark (U+06E9). */
const INERT_SYMBOLS = new Set([0x06de, 0x06e9]);

/** Minimal word shape the builder needs. Both `TsWord` and `AnimUnit`
 *  (mapped) satisfy it — keeps the builder reusable across surfaces. */
export interface AnimSourceWord {
    text: string;
    start: number;
    end: number;
    letters: {
        char: string;
        start: number | null;
        end: number | null;
        tokenId?: number;
        paintCharacterIds?: number[];
        silent?: boolean;
    }[];
}

export interface AnimChar {
    text: string;
    start: number;
    end: number;
    groupId: string;
    /** A non-recited symbol (rub-el-hizb, sajdah): rendered in place, never lit. */
    inert: boolean;
    /** Producer animation-token identity; present in schema-v13 readings. */
    tokenId?: number;
    /** A timing borrower with no producer-owned or presented sound. */
    silent: boolean;
    /** Per-occurrence [start,end] for this cell, index-aligned to the unit's
     *  `intervals` (filled by the caller from `occurrenceLetters`). A repeat uses
     *  the entry for the take being recited; an `undefined` entry (or absent
     *  array) means that take had no per-letter data → fall back to the canonical
     *  `start`/`end` remap. */
    occIntervals?: (TimeSpan | undefined)[];
    /** Silent ownership for each occurrence. Boundary context can make the same
     * source token sound in one take and borrow timing in another. */
    occSilent?: (boolean | undefined)[];
}

export interface AnimWord {
    word: AnimSourceWord;
    wordIndex: number;
    start: number;
    end: number;
    /** Display text with all non-recited decorator marks removed — what the
     *  reveal actually animates. Decorators are pulled out to `leading`/`trailing`
     *  so they never take a highlight cell (see `../decorators`). */
    clean: string;
    /** Non-recited decorators rendered before the word's letters (e.g. rub-el-hizb). */
    leading: Decorator[];
    /** Non-recited decorators rendered after the word's letters (waqf stop, sajdah). */
    trailing: Decorator[];
    /** Characters split for character-granularity animation (from `clean`). */
    chars: AnimChar[];
    /** Whether the word has any char groups (empty text → render it directly). */
    hasChars: boolean;
}

/** Walk display char-clusters + MFA letters in reading order and return each
 *  cluster's [start,end], index-aligned to `chars`. A matched base cluster folds
 *  in the timing of any following combining-mark letters (small hamza, mini-yaa,
 *  dagger alef) so they ride their base grapheme. Untimed clusters inherit the
 *  nearest stamped neighbour (the orphan safety net); if NOTHING is stamped, the
 *  whole word keeps its word span (lit together — correct with no per-letter
 *  data). Pure — no mutation of `chars`. Reused for both the canonical pass and
 *  each repeat occurrence's own letter timings. */
export function stampCharTimes(
    chars: { text: string; inert: boolean }[],
    letters: AnimSourceWord['letters'],
    wordStart: number,
    wordEnd: number,
): TimeSpan[] {
    const out: TimeSpan[] = chars.map(() => ({ start: wordStart, end: wordEnd }));
    let mfaIdx = 0;
    const timed = new Set<number>();
    for (let di = 0; di < chars.length; di++) {
        const span = chars[di];
        if (!span || span.inert) continue;
        while (mfaIdx < letters.length && !letters[mfaIdx]) mfaIdx++;
        if (mfaIdx >= letters.length) break;
        const lt = letters[mfaIdx]!;
        if (!charsMatch(lt.char || '', span.text)) continue; // mismatch -> orphan pass
        out[di]!.start = lt.start != null ? lt.start : wordStart;
        let endSec = lt.end != null ? lt.end : wordEnd;
        mfaIdx++;
        while (mfaIdx < letters.length) {
            const nxt = letters[mfaIdx];
            const cp = nxt?.char ? nxt.char.codePointAt(0) : undefined;
            // Stop at the dagger alef — it has its own cell to match it.
            if (cp === undefined || !isCombiningMark(cp) || cp === 0x0670) break;
            if (nxt!.end != null) endSec = nxt!.end;
            mfaIdx++;
        }
        out[di]!.end = endSec;
        timed.add(di);
    }
    if (timed.size > 0) {
        for (let di = 0; di < chars.length; di++) {
            if (timed.has(di)) continue;
            let src: TimeSpan | null = null;
            for (let p = di - 1; p >= 0 && !src; p--) if (timed.has(p)) src = out[p]!;
            for (let n = di + 1; n < chars.length && !src; n++) if (timed.has(n)) src = out[n]!;
            if (src) {
                out[di]!.start = src.start;
                out[di]!.end = src.end;
            }
        }
    }
    return out;
}

export function buildAnimStructure(words: AnimSourceWord[]): AnimWord[] {
    if (!words.length) return [];

    let groupIdCounter = 0;
    const out: AnimWord[] = [];

    words.forEach((word, wi) => {
        // Pull all non-recited decorator marks (waqf stops, rub-el-hizb, sajdah)
        // out before grouping — they carry no MFA timing and must never join the
        // reveal as a highlight cell.
        const { clean, leading, trailing } = splitDecorators(word.text);
        const letters = word.letters || [];
        const directTokens = letters.length > 0
            && letters.every((letter) => letter.tokenId !== undefined);
        const charGroups = directTokens ? [] : splitIntoCharGroups(clean);

        // One cell per grapheme cluster (base letter + all its combining marks).
        // A cluster whose base is a non-recited symbol (rub-el-hizb, sajdah) is
        // `inert`: rendered in place but never highlighted, and it takes no MFA
        // letter.
        const chars: AnimChar[] = directTokens ? letters.map((letter) => ({
            text: letter.char,
            start: letter.start ?? word.start,
            end: letter.end ?? word.end,
            groupId: `g${groupIdCounter++}`,
            inert: letter.start === null || letter.end === null,
            tokenId: letter.tokenId,
            silent: letter.silent ?? false,
        })) : charGroups.map((group) => {
            const base = group.codePointAt(0);
            // The dagger alef is its own cell — anchor it on an invisible word
            // joiner so the lone superscript shapes into its own run.
            return {
                text: base === 0x0670 ? ZWSP + group : group,
                start: word.start,
                end: word.end,
                groupId: `g${groupIdCounter++}`,
                inert: base !== undefined && INERT_SYMBOLS.has(base),
                silent: false,
            };
        });

        // Stamp each cluster's canonical [start,end] from the MFA letters (the
        // first-occurrence timeline). The same routine drives each repeat
        // occurrence's per-take timings (attached later by the surface).
        if (!directTokens) {
            const times = stampCharTimes(chars, letters, word.start, word.end);
            for (let di = 0; di < chars.length; di++) {
                const span = chars[di];
                if (!span) continue;
                span.start = times[di]!.start;
                span.end = times[di]!.end;
            }
        }

        out.push({
            word,
            wordIndex: wi,
            start: word.start,
            end: word.end,
            clean,
            leading,
            trailing,
            chars,
            hasChars: chars.length > 0,
        });
    });

    // Cross-word group-ID merge: chars sharing identical (start, end) share a
    // group so cross-boundary idgham/ghunna timing highlights together.
    const timingMap: Record<string, string> = {};
    for (const w of out) {
        for (const ch of w.chars) {
            const key = `${ch.start}|${ch.end}`;
            const existing = timingMap[key];
            if (existing) ch.groupId = existing;
            else timingMap[key] = ch.groupId;
        }
    }
    return out;
}
