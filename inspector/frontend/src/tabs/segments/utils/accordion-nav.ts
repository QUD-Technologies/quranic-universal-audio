/**
 * Accordion-scoped navigation sequence.
 *
 * When a validation accordion is open the main list is hidden, so the only
 * `.seg-row` cards in the DOM are the open category's rows — in render order.
 * This reads them straight from the DOM (cheap, and immune to the per-card
 * split-group / resolve complexity) to drive ↑/↓ navigation and autoplay-
 * advance through the accordion.
 *
 * **One stop per SEGMENT, never per card.** The sequence includes context
 * (prev/next neighbour) rows, main rows, and every piece of a multi-piece
 * card — one continuous list of actual segments, so ↑/↓ and autoplay walk
 * piece → piece and never jump card → card.
 *
 * That is why stops are keyed by row **uid**, not by (chapter, index): the
 * staged pieces of a pre-applied cross-verse split all carry their PARENT's
 * (chapter, index), so an index-keyed dedupe collapsed a whole card into a
 * single stop and made ↑/↓ skip its pieces. A segment that genuinely appears
 * twice (one card's "next" neighbour is the next card's main row) shares a
 * uid and is still deduped to a single stop in DOM order.
 */

import { get } from 'svelte/store';

import { accordionNavCursor, playingSegmentIndex, stagedPlayheadWindow } from '../stores/playback';
import { valUiOpenCategory } from '../stores/validation';

export interface AccordionRowRef {
    chapter: number;
    index: number;
    /** Row/piece identity — the dedupe + cursor key. '' when the row has no uid. */
    uid: string;
    /** The piece's own window. `startMs` is what playback must seek to, and
     *  `endMs` bounds it, so a staged piece plays as its own segment instead
     *  of running on to the parent's end. */
    startMs: number;
    endMs: number;
}

/** Ordered, uid-deduped list of the open accordion's rows (context + main +
 *  every piece), or [] when no accordion is open / nothing is rendered. */
export function accordionSequence(): AccordionRowRef[] {
    if (get(valUiOpenCategory) === null) return [];
    if (typeof document === 'undefined') return [];
    const rows = document.querySelectorAll<HTMLElement>(
        '.seg-row[data-seg-uid]:not(.mode-history)',
    );
    const out: AccordionRowRef[] = [];
    const seen = new Set<string>();
    rows.forEach((el) => {
        const idx = Number(el.dataset.segIndex);
        const ch = el.dataset.segChapter != null ? Number(el.dataset.segChapter) : NaN;
        if (!Number.isFinite(idx) || !Number.isFinite(ch)) return;
        const uid = el.dataset.segUid ?? '';
        const startMs = Number(el.dataset.segStart);
        const endMs = Number(el.dataset.segEnd);
        // Key on uid so sibling pieces sharing the parent's index stay
        // separate stops; fall back to the pair for a row without a uid.
        const key = uid || `${ch}:${idx}:${startMs}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({
            chapter: ch,
            index: idx,
            uid,
            startMs: Number.isFinite(startMs) ? startMs : 0,
            endMs: Number.isFinite(endMs) ? endMs : 0,
        });
    });
    return out;
}

/**
 * Position of the segment the user is currently ON within `seq`, or -1.
 *
 * Resolution order:
 *  1. `accordionNavCursor` — set by every playback initiation and never by an
 *     edit, so it survives a pause and a WASL/WAQF label.
 *  2. The live staged playhead window, for a piece being played right now
 *     that the cursor hasn't recorded (e.g. playback started elsewhere).
 *  3. (chapter, index) alone — the single-piece case.
 */
function currentPos(seq: AccordionRowRef[]): number {
    const cursor = get(accordionNavCursor);
    if (cursor) {
        const byUid = cursor.uid
            ? seq.findIndex((r) => r.uid === cursor.uid)
            : -1;
        if (byUid !== -1) return byUid;
        const byWindow = seq.findIndex(
            (r) => r.chapter === cursor.chapter
                && r.index === cursor.index
                && r.startMs === cursor.startMs,
        );
        if (byWindow !== -1) return byWindow;
    }

    const active = get(playingSegmentIndex);
    if (!active) return -1;

    const win = get(stagedPlayheadWindow);
    if (win) {
        const byPiece = seq.findIndex(
            (r) => r.chapter === active.chapter
                && r.index === active.index
                && r.startMs === win.start
                && r.endMs === win.end,
        );
        if (byPiece !== -1) return byPiece;
    }

    return seq.findIndex((r) => r.chapter === active.chapter && r.index === active.index);
}

/**
 * Step one SEGMENT relative to the one the user is on within the open
 * accordion sequence. Clamps at the ends. Falls back to the first/last stop
 * when nothing in the sequence is current. Returns null when there is no open
 * accordion sequence.
 */
export function accordionStep(dir: 1 | -1): AccordionRowRef | null {
    const seq = accordionSequence();
    if (seq.length === 0) return null;
    const pos = currentPos(seq);
    if (pos === -1) return dir === 1 ? seq[0]! : seq[seq.length - 1]!;
    const next = pos + dir;
    if (next < 0 || next >= seq.length) return seq[pos]!;
    return seq[next]!;
}

/** True when `ref` is the stop the user is already on — the guard the
 *  autoplay advance uses to avoid replaying the same segment. Compares piece
 *  identity, not (chapter, index), so two pieces of one parent are distinct. */
export function isCurrentStop(ref: AccordionRowRef): boolean {
    const seq = accordionSequence();
    const pos = currentPos(seq);
    if (pos === -1) return false;
    const cur = seq[pos]!;
    return cur.uid === ref.uid && cur.startMs === ref.startMs;
}
