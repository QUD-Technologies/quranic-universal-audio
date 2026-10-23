<script lang="ts">
    /**
     * Center-anchored ayah filmstrip — a by-ayah scrubber that sits above the
     * (linear) progress bar. The strip is a fixed-scale time-ruler: cell width
     * and inter-cell gap are both `seconds × filmstripPxPerSec`. A very short
     * verse is floored to `filmstripMinCellPx` so its ayah number stays legible;
     * the cursor still crosses only the verse's time-true span (`aw`) at the one
     * global velocity, the floor's surplus folding into the adjacent gap. A fixed
     * center needle marks "now"; the strip slides so the live position stays
     * centered.
     *
     * Recitation-driven, not clock-driven: the active cell follows which WORD is
     * being recited (via the shared `findActiveAt` timeline), and the progress fill
     * tracks the CURSOR's position within its cell so the lit bar's leading edge
     * sits under the center needle. An inter-take silence (forward OR backward)
     * greys the needle and scrolls it continuously across the inter-cell gap, and a
     * loopback replays each re-recited verse over its OWN occurrence (so a multi-verse
     * re-take conforms per verse, not to the loopback verse) — the strip and the
     * teleprompter line stay in lockstep. Three motion models
     * (config.filmstripMotion):
     *   - tuner:  continuous center; drag scrubs exact time.
     *   - hybrid: continuous center; drag snaps to whole ayahs on release.
     *   - snap:   center the active cell only when the ayah changes; drag = snap.
     *
     * User scrub/drag/click stays TIME-based (seeking); playback is the only
     * recitation-driven path. ALL strip motion routes through one controller
     * (`createStripScroller`, `filmstrip-scroll.svelte.ts`) — one curve, one set
     * of constants, one rAF — so every motion model and play/pause path feels the
     * same:
     *   - Forward playback (and live drag/scrub) tracks the LIVE recited position
     *     instantly (`scroll.snap` / `scroll.follow` not-discont) — the needle sits
     *     exactly on the recited word, locked to the audio.
     *   - Every NON-instant move — within-verse word rewind, multi-verse loopback,
     *     click, key, snap-center, drag-release, paused refresh — runs one eased
     *     distance-proportional Hermite glide (`scroll.glide`, or the `scroll.follow`
     *     catch-up). A catch-up chasing the still-moving replayed word re-plans each
     *     frame carrying current velocity, so it never lurches, never inherits a
     *     stale cutoff, and lands cleanly before resuming instant tracking. The
     *     controller owns its own rAF, so paused seeks ease too (never teleport).
     * Surface-agnostic: fed `units` + a prebuilt `FilmstripModel` + a time
     * accessor + an onSeek cb.
     */
    import * as m from '$lib/paraglide/messages';
    import { i18n } from '$lib/i18n/locale.svelte';
    import { toArabicNumeral } from '../utils/arabic-text';
    import { type RecitationAnimConfig } from './config';
    import { createStripScroller, GLIDE_MIN_MS, glideDur } from './filmstrip-scroll.svelte';
    import type { ActiveCellInfo, CellMissing, FilmstripModel } from './filmstrip-model';
    import { buildSortedIntervals, findActiveAt, nextIntervalAfter } from './recitation-active';
    import type { AnimUnit } from './types';

    interface Props {
        units: AnimUnit[];
        model: FilmstripModel;
        durationMs: number;
        getTimeMs: () => number;
        playing: boolean;
        config: RecitationAnimConfig;
        onSeek: (_ms: number) => void;
        /** Preview-highlight the ayah recited at this time (e.g. progress-bar
         *  hover). null = no preview. */
        hoverMs?: number | null;
        /** Active progress-bar *drag* time — the strip scroll-follows it (both
         *  motion modes) while non-null, suspending the playback driver. null =
         *  not scrubbing. */
        scrubMs?: number | null;
        /** Speculative-prewarm hook: fired with a cell's seek (ms) when the
         *  pointer enters it, so the surface can warm that position. Optional. */
        onHoverPrewarm?: (_ms: number) => void;
        /** ayah → missing reference word indices, for the `words`-cell tooltip. */
        missingWordsByAyah?: Map<number, number[]>;
        /** Fired (on change only) with the active/selected verse + its coverage
         *  status, or null when none is active. Drives a parent "missing words"
         *  pill. */
        onActiveCell?: (_info: ActiveCellInfo | null) => void;
    }

    let {
        units, model, getTimeMs, playing, config, onSeek,
        hoverMs = null, scrubMs = null, onHoverPrewarm,
        missingWordsByAyah, onActiveCell,
    }: Props = $props();

    const clamp = (lo: number, hi: number, v: number): number => Math.min(hi, Math.max(lo, v));
    const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

    /** A backward verse jump, or a forward verse skip larger than this many
     *  cells, is a loopback/seek discontinuity → glide rather than teleport. */
    const JUMP_CELL_GAP = 1;
    /** Min backward cell-fill delta (fraction) that counts as a within-verse
     *  word loopback worth gliding. */
    const JUMP_FRAC_EPS = 0.01;
    /** An audio-time jump beyond this in one frame ⇒ a seek (glide), not the
     *  normal forward creep of playback. */
    const SEEK_JUMP_MS = 400;
    /** Width (px) of an unrecited (placeholder) verse cell. It has no recited
     *  duration to scale from, and playback skips it, so it gets a fixed visible
     *  width purely to stay legible as a skipped slot. */
    const PLACEHOLDER_CELL_PX = 30;
    /** An inter-verse gap shorter than this (seconds) is connected flow, not a
     *  waqf stop: it's laid out time-true (no visual floor) and the needle holds lit
     *  straight through it instead of greying + bursting across a floored gap it has
     *  no time to cross. A real stop is far longer; loopback/waqf pauses unaffected. */
    const MICRO_GAP_SEC = 0.06;

    let containerEl = $state<HTMLDivElement | undefined>(undefined);
    let cw = $state(0); // container width
    let dragging = $state(false);

    // Reading i18n.locale makes this $derived re-run on switch.
    const scrubberAriaLabel = $derived((i18n.locale, m.common_player_filmstrip_scrubber_aria_label()));

    // Recitation-driven playback state (written by the rAF driver / refresh).
    let activeIdx = $state(-1); // cell of the recited word; holds during silence
    let cellFrac = $state(0); // word-proportional fill of the active cell
    let silent = $state(false); // in a silence gap → frozen, no highlight/cursor
    let crossingGap = $state(false); // between-verse pause → needle greys + scrolls the gap
    let frozenIdx = $state(-1); // last active cell, held while silent
    let fillIdx = $state(-1); // cell the cursor (needle) sits in — the lit fill bar
    let jumping = $state(false); // mid-glide → cell-fill eases too
    let lastActiveUnit = -1; // O(1) fast-path hint for findActiveAt
    let lastTimeMs = -1; // previous frame's audio time, for seek detection
    let fillGlideTimer: ReturnType<typeof setTimeout> | null = null;

    const reducedMotion = typeof matchMedia === 'function'
        && matchMedia('(prefers-reduced-motion: reduce)').matches;

    // The ONE controller that moves the strip `offset` — every motion model and
    // every play/pause path routes through it (instant forward tracking, eased
    // glides to a fixed point, and the velocity-continuous loopback catch-up).
    // `offset` is read as `scroll.offset` (reactive) by the track transform.
    const scroll = createStripScroller({
        get lastRight() { return lastRight; },
        get reducedMotion() { return reducedMotion; },
    });
    $effect(() => () => scroll.dispose()); // tear down its rAF on destroy

    interface Cell {
        ayah: number;
        w: number; // visible width (floored to filmstripMinCellPx)
        aw: number; // active span the cursor crosses (time-true, ≤ w)
        cumBefore: number; // px before this cell's left (cells + gaps)
        gapAfter: number; // px gap to the right (silence-scaled; 0 when merged)
        missing: CellMissing;
        /** This verse waṣl-bridges into the next present verse (member of a
         *  rail group) → its outgoing boundary is gapless. */
        waslNext: boolean;
        /** Outgoing boundary is merged (gapless) → this cell starts/continues a
         *  waṣl rail group. (= `waslNext`.) */
        mergeRight: boolean;
        /** Incoming boundary is merged → the previous verse bridges into this one
         *  (continues a rail group). */
        mergeLeft: boolean;
    }

    // The strip is a fixed-scale time-ruler at the global `filmstripPxPerSec`:
    // a verse's time-true width `aw` = canonical recited seconds × pxPerSec, and a
    // between-verse gap = silence seconds × pxPerSec, so a given silence renders to
    // the same px in every surah and for every reciter (no per-chapter
    // normalization). `aw` uses the CANONICAL recited duration, so a loopback's
    // later occurrence never inflates it. A too-short SOLO verse is floored to
    // `filmstripMinCellPx` for legibility (visible width `w`); the cursor still
    // crosses only the centered `aw` at the ruler velocity (see `offsetForReci`),
    // the surplus `w − aw` folding into the adjacent gap. That floor DISSOLVES as a
    // cell merges into a waṣl capsule (`merged` → `w = aw`): a continuous-recitation
    // group has no inter-verse gap/silence to absorb the surplus, so keeping the
    // floor would jerk the cursor at each gapless seam — a merged group is laid out
    // time-true and crossed at the one constant velocity. An unrecited placeholder
    // gets a fixed visible width; an audible silence keeps a `filmstripGapPx` floor
    // while a sub-perceptual micro-gap stays time-true (see `MICRO_GAP_SEC`).
    // Geometry is identical across motion modes, so toggling never shifts layout.
    const cells = $derived.by((): Cell[] => {
        const mc = model.cells;
        if (!mc.length) return [];
        const pxPerSec = config.filmstripPxPerSec;
        const minPx = config.filmstripMinCellPx;
        const out: Cell[] = [];
        let cum = 0;
        for (let i = 0; i < mc.length; i++) {
            const c = mc[i]!;
            const placeholder = c.missing === 'full';
            const aw = placeholder
                ? PLACEHOLDER_CELL_PX
                : Math.max(1, Math.round(c.canonDurSec * pxPerSec));
            // Cross-verse waṣl: a boundary that bridges in any take is permanently
            // gapless (a rail-group member). `mergeRight` = this verse bridges out;
            // `mergeLeft` = the previous verse bridges into this one.
            const mergeRight = c.waslNext;
            const mergeLeft = i > 0 && !!mc[i - 1]!.waslNext;
            // Visible width: the legibility floor dissolves for a rail-group member
            // so it's time-true (`w = aw`) and the group crosses at constant
            // velocity; `gapAfter` derives the same way (gapless inside a group).
            const merged = mergeRight || mergeLeft;
            const w = placeholder
                ? PLACEHOLDER_CELL_PX
                : merged ? aw : Math.round(aw + Math.max(0, minPx - aw));
            // Time-ruler gap: px ∝ silence seconds. A clearly-audible gap keeps a
            // small visual floor; a sub-perceptual micro-gap (connected flow) stays
            // time-true so the cursor never bursts across a floored gap it has no
            // time to cross (paired with the silence-hold in `drivePlayback`).
            const sec = c.nextGapSec;
            const baseGap = Math.round(
                sec < MICRO_GAP_SEC ? sec * pxPerSec : Math.max(config.filmstripGapPx, sec * pxPerSec),
            );
            const gapAfter = mergeRight ? 0 : baseGap;
            out.push({
                ayah: c.ayah, w, aw, cumBefore: cum, gapAfter, missing: c.missing,
                waslNext: c.waslNext, mergeRight, mergeLeft,
            });
            cum += w + gapAfter;
        }
        return out;
    });

    interface WaslGroup {
        leftPx: number; // group's left edge (track px, pre-pad)
        rightPx: number; // group's right edge
    }
    /** Contiguous waṣl groups — each a maximal run of merged (gapless) cells, tied
     *  together by a thin accent rail under the row. The sub-cells keep their own
     *  full borders + verse numbers; the rail is the only "one continuous-recitation
     *  span" cue (no capsule weld, no range label). A group runs from its
     *  `mergeRight`-only start through its `mergeLeft`-only end. Empty for the
     *  all-solo common case → the overlay renders nothing. */
    const waslGroups = $derived.by((): WaslGroup[] => {
        const groups: WaslGroup[] = [];
        let i = 0;
        while (i < cells.length) {
            if (cells[i]!.mergeRight && !cells[i]!.mergeLeft) {
                let j = i;
                while (j < cells.length - 1 && cells[j]!.mergeRight) j++;
                const start = cells[i]!;
                const end = cells[j]!;
                groups.push({ leftPx: start.cumBefore, rightPx: end.cumBefore + end.w });
                i = j + 1;
            } else {
                i++;
            }
        }
        return groups;
    });

    /** A cell that can be navigated to / recited. `full` placeholders (unrecited
     *  verses) are skipped by every user-nav path; playback never targets them. */
    const isPlayable = (i: number): boolean =>
        i >= 0 && i < cells.length && cells[i]!.missing !== 'full';

    /** Nearest non-placeholder cell to a strip offset (drag-release snap). */
    function nearestPlayableCell(off: number): number {
        let best = -1;
        let bestD = Infinity;
        for (let i = 0; i < cells.length; i++) {
            if (cells[i]!.missing === 'full') continue;
            const d = Math.abs(offsetForCellCenter(i) - off);
            if (d < bestD) { bestD = d; best = i; }
        }
        return best;
    }

    /** The playable cell whose visible span [cumBefore, cumBefore+w] CONTAINS the
     *  offset — the cell the cursor sits inside, used to pick the lit fill cell so
     *  its leading edge aligns with the needle. Falls back to the nearest cell when
     *  the offset is in an inter-cell gap (no cell spans it). */
    function cellSpanningOffset(off: number): number {
        for (let i = 0; i < cells.length; i++) {
            const c = cells[i]!;
            if (c.missing === 'full') continue;
            if (off >= c.cumBefore && off <= c.cumBefore + c.w) return i;
        }
        return nearestPlayableCell(off);
    }

    /** Next non-placeholder cell from `from` in direction `dir` (±1), or −1. */
    function nextPlayableCell(from: number, dir: number): number {
        for (let i = from + dir; i >= 0 && i < cells.length; i += dir) {
            if (cells[i]!.missing !== 'full') return i;
        }
        return -1;
    }

    function missingTitle(c: Cell): string | undefined {
        void i18n.locale; // re-evaluate the label on locale switch
        if (c.missing === 'full') return m.common_filmstrip_not_recited();
        if (c.missing !== 'words') return undefined;
        const mw = missingWordsByAyah?.get(c.ayah);
        if (!mw || !mw.length) return m.common_filmstrip_missing_words();
        const list = ['ar', 'ckb'].includes(i18n.locale) ? mw.map(toArabicNumeral).join('، ') : mw.join(', ');
        return m.common_filmstrip_missing_words_list({ indices: list });
    }

    const sorted = $derived(buildSortedIntervals(units));
    const lastRight = $derived(
        cells.length ? cells[cells.length - 1]!.cumBefore + cells[cells.length - 1]!.w : 0,
    );
    const pad = $derived(cw / 2); // leading/trailing spacer so edges can center

    interface Reci { unitIdx: number; idx: number; frac: number; }

    /** First index into `sorted` whose interval starts at/after `s` (bsearch). */
    function lowerBoundStart(s: number): number {
        const S = sorted;
        let lo = 0;
        let hi = S.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (S[mid]!.start < s) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

    /** The cursor's fraction across the active verse cell, measured in RECITED
     *  seconds over the verse's CURRENT take — so the cell crosses at ONE constant
     *  velocity (`aw / take-seconds`) however the take's words are paced, and a
     *  re-recited verse conforms to its OWN duration, never the loopback verse's.
     *  The take is the maximal run of consecutive timeline intervals that stay in
     *  this cell AND advance in reading order (a loopback or another verse breaks the
     *  run; a within-verse pause does not — its silence isn't summed). The covered
     *  word range maps recited progress onto the cell sub-span. For a forward first
     *  take this is identical to the canonical word-fraction crossing. */
    function takeFrac(idx: number, u: number, ivStart: number, tSec: number): number {
        // Hoist the reactive `sorted` / `model` reads into plain locals once — the
        // loops below index them O(span) times per frame, and each textual read of a
        // `$derived` / prop signal is a `get()` + dirty-check. One snapshot per call
        // keeps the value stable (synchronous) while collapsing thousands of
        // signal reads to a handful (the per-frame `is_dirty` hot spot).
        const S = sorted;
        const cou = model.cellOfUnit;
        const cell = model.cells[idx]!;
        const us = cell.unitStart;
        // Locate the active interval in the sorted timeline (bsearch by start, then
        // disambiguate by unit among equal starts).
        let k = lowerBoundStart(ivStart);
        while (k < S.length && S[k]!.start === ivStart && S[k]!.unitIdx !== u) k++;
        if (k >= S.length || S[k]!.unitIdx !== u) return cell.words[u - us]?.frac0 ?? 0;
        // Grow the take left/right over consecutive, same-cell, reading-order-rising
        // intervals (skipping nothing in between → a within-verse pause stays in, a
        // loopback / other verse cuts it).
        let lo = k;
        let hi = k;
        while (lo > 0) {
            const p = S[lo - 1]!;
            if ((cou[p.unitIdx] ?? -1) !== idx || p.unitIdx >= S[lo]!.unitIdx) break;
            lo--;
        }
        while (hi + 1 < S.length) {
            const nx = S[hi + 1]!;
            if ((cou[nx.unitIdx] ?? -1) !== idx || nx.unitIdx <= S[hi]!.unitIdx) break;
            hi++;
        }
        let before = 0; // recited secs of take intervals strictly before the active one
        let total = 0;
        for (let i = lo; i <= hi; i++) {
            const d = S[i]!.end - S[i]!.start;
            if (i < k) before += d;
            total += d;
        }
        const f0 = cell.words[S[lo]!.unitIdx - us]?.frac0 ?? 0;
        const f1 = cell.words[S[hi]!.unitIdx - us]?.frac1 ?? 1;
        const p = total > 0 ? clamp(0, 1, (before + (tSec - ivStart)) / total) : 0;
        return f0 + p * (f1 - f0);
    }

    /** Map a time (seconds) to the recited cell + its take-proportional cursor
     *  fraction, or null during a silence gap. `hint` seeds the O(1) fast-path
     *  (-1 for random-access lookups like hover/scrub). */
    function recitationAt(tSec: number, hint: number): Reci | null {
        const h = findActiveAt(units, sorted, tSec, hint);
        if (!h) return null;
        const idx = model.cellOfUnit[h.unitIdx] ?? -1;
        if (idx < 0) return null;
        return { unitIdx: h.unitIdx, idx, frac: takeFrac(idx, h.unitIdx, h.ivStart, tSec) };
    }

    function offsetForCellCenter(i: number): number {
        const c = cells[i];
        return c ? c.cumBefore + c.w / 2 : 0;
    }
    function offsetForReci(r: Reci): number {
        const c = cells[r.idx];
        // Cross only the centered time-true span `aw` (offset by the floor's left
        // margin), so a floored short cell scrolls at the ruler velocity, not faster.
        return c ? c.cumBefore + (c.w - c.aw) / 2 + r.frac * c.aw : scroll.offset;
    }
    function nearestCell(off: number): number {
        let best = -1;
        let bestD = Infinity;
        for (let i = 0; i < cells.length; i++) {
            const d = Math.abs(offsetForCellCenter(i) - off);
            if (d < bestD) {
                bestD = d;
                best = i;
            }
        }
        return best;
    }
    /** Map a strip offset back to a canonical seek time (ms) — tuner drag. A
     *  placeholder (`full`) cell has no canon time, so it resolves to the nearest
     *  playable cell's seek instead. */
    function timeAtOffset(off: number): number {
        for (let i = 0; i < cells.length; i++) {
            const c = cells[i]!;
            if (off <= c.cumBefore + c.w) {
                if (c.missing === 'full') {
                    const j = nearestPlayableCell(off);
                    return j >= 0 ? seekMsForCell(j) : 0;
                }
                const f = clamp(0, 1, (off - c.cumBefore - (c.w - c.aw) / 2) / c.aw);
                const mc = model.cells[i]!;
                return (mc.canonStartSec + f * mc.canonDurSec) * 1000;
            }
        }
        const j = nearestPlayableCell(off);
        return j >= 0 ? seekMsForCell(j) : 0;
    }
    /** The seek target for picking a cell — the verse's canonical first start. */
    function seekMsForCell(i: number): number {
        const mc = model.cells[i];
        return mc ? mc.canonStartSec * 1000 : 0;
    }
    /** Cell recited at a given time (random access) — hover/scrub preview. */
    function cellViaTime(ms: number): number {
        const h = findActiveAt(units, sorted, ms / 1000, -1);
        return h ? (model.cellOfUnit[h.unitIdx] ?? -1) : -1;
    }

    const hoverIdx = $derived(hoverMs == null ? -1 : cellViaTime(hoverMs));
    // Snap mode has no moving needle (the strip is always centered on the active
    // cell), so instead we ring the cell under where the invisible needle would
    // sit. Hidden during silence (no cursor while frozen). -1 outside snap mode.
    const cursorIdx = $derived(
        config.filmstripMotion === 'snap' && !silent ? nearestPlayableCell(scroll.offset) : -1,
    );

    // Report the active/selected verse (held cell while silent) up to the parent
    // so it can show a contextual "missing words" pill. Fires only when the
    // resolved cell changes (derived recompute), never per frame.
    const reportedIdx = $derived(silent ? frozenIdx : activeIdx);
    $effect(() => {
        const c = reportedIdx >= 0 ? cells[reportedIdx] : undefined;
        onActiveCell?.(c ? { ayah: c.ayah, missing: c.missing } : null);
    });

    // The lit fill bar shows the CURSOR's position within ITS cell (`fillIdx`),
    // not the recited word-fraction — so the bar's leading edge sits exactly under
    // the center needle. % = how far the scroll offset has crossed `fillIdx`'s
    // visible width. Inert in snap mode (no continuous needle there); the var it
    // writes is read by the `.cell.fillcell .cell-fill` rule.
    const fillPct = $derived.by((): number => {
        if (config.filmstripMotion === 'snap' || fillIdx < 0) return 0;
        const c = cells[fillIdx];
        if (!c || c.w <= 0) return 0;
        return clamp(0, 1, (scroll.offset - c.cumBefore) / c.w) * 100;
    });
    $effect(() => {
        if (config.filmstripMotion === 'snap') return;
        containerEl?.style.setProperty('--cell-active-fill', fillPct + '%');
    });

    /** Ease the active cell's fill bar across a SINGLE loopback/seek rewind, then
     *  drop the transition so subsequent forward frames track instantly. The
     *  transition is short and one-shot — holding it across the whole glide would
     *  make every per-frame `setFill` chase a moving target and visibly lag the
     *  audio (the fill would crawl, never catching up). */
    function armFillGlide(dur: number): void {
        if (reducedMotion) return;
        containerEl?.style.setProperty('--cell-glide-dur', dur + 'ms');
        jumping = true;
        if (fillGlideTimer) clearTimeout(fillGlideTimer);
        // One eased step is ~one transition; clear once it has landed so forward
        // play is instant again. `disarmFillGlide` also clears it on the next
        // non-jump frame, whichever comes first.
        fillGlideTimer = setTimeout(() => { jumping = false; }, dur);
    }
    /** Drop the eased-fill transition the moment forward tracking resumes, so a
     *  loopback's eased rewind never bleeds into the live per-frame fill. */
    function disarmFillGlide(): void {
        if (!jumping) return;
        jumping = false;
        if (fillGlideTimer) { clearTimeout(fillGlideTimer); fillGlideTimer = null; }
    }
    /** A discrete strip move (loopback / seek / user nav): glide the strip AND
     *  ease the fill over the same distance-proportional time. */
    function jumpTo(target: number): void {
        armFillGlide(glideDur(Math.abs(clamp(0, lastRight, target) - scroll.offset)));
        scroll.glide(target);
    }

    /** A recitation discontinuity (loopback / seek) vs a smooth advance. */
    function isJump(prevIdx: number, newIdx: number, prevFrac: number, newFrac: number): boolean {
        if (prevIdx < 0) return false; // first activation — no glide
        if (newIdx < prevIdx) return true; // back to an earlier verse
        if (newIdx - prevIdx > JUMP_CELL_GAP) return true; // forward verse skip
        if (newIdx === prevIdx && newFrac < prevFrac - JUMP_FRAC_EPS) return true; // word loopback
        return false;
    }

    /** The latest occurrence interval that finished at or before `tSec` — the take
     *  the audio just LEFT, whose `end` is the true start of the silence the audio
     *  is now inside and whose `unitIdx` is the word it departed from. Robust for a
     *  BACKWARD loopback gap, where the audio left from a later occurrence than the
     *  frozen verse's forward-flowing `canonEndSec`. `null` before the first word. */
    function prevInterval(tSec: number): { end: number; unitIdx: number } | null {
        let best: { end: number; unitIdx: number } | null = null;
        for (const iv of sorted) {
            if (iv.start > tSec) break; // sorted by start; nothing later can have begun
            if (iv.end <= tSec && (!best || iv.end > best.end)) best = { end: iv.end, unitIdx: iv.unitIdx };
        }
        return best;
    }

    /** Scroll the needle across the inter-cell gap proportionally to the elapsed
     *  inter-take silence, so the pause reads as continuous travel rather than a
     *  freeze-then-jump. The caller has resolved the upcoming cell. Routed through
     *  the controller's instant `snap` (continuous forward motion, same family as
     *  live tracking) — at the ruler velocity, since gap px = silence seconds ×
     *  pxPerSec. `gapStart` is the actual end of the interval the silence follows
     *  (so a backward loopback's gap measures from the looped-FROM occurrence, not
     *  the frozen verse's forward end), `gapEnd` the upcoming occurrence's start. */
    function scrollThroughGap(tSec: number, nextIv: { start: number; unitIdx: number }, nextIdx: number): void {
        const mcA = model.cells[frozenIdx];
        const mcB = model.cells[nextIdx];
        const cellA = cells[frozenIdx];
        const cellB = cells[nextIdx];
        if (!mcA || !mcB || !cellA || !cellB || mcA.canonEndSec < 0) return;
        const prev = prevInterval(tSec);
        const gapStart = prev ? prev.end : mcA.canonEndSec;
        const gapEnd = nextIv.start;
        const p = gapEnd > gapStart ? clamp(0, 1, (tSec - gapStart) / (gapEnd - gapStart)) : 1;
        // Depart from the WORD the audio actually LEFT (`prev`'s unit), not verse A's
        // end — a mid-verse loopback leaves mid-cell, so anchoring at frac 1 would
        // jerk forward to the verse end before travelling back. Symmetric with the
        // resume-word landing below. Falls back to the verse end when the departure
        // interval isn't in cell A (the canonical forward-flow case).
        const departFrac = prev && (model.cellOfUnit[prev.unitIdx] ?? -1) === frozenIdx
            ? mcA.words[prev.unitIdx - mcA.unitStart]?.frac1 ?? 1
            : 1;
        // Land on the WORD the reciter actually resumes (`nextIv`'s unit), not verse
        // B's start — a mid-verse loopback resumes mid-cell, so targeting frac 0
        // would overshoot to the verse start then jerk forward to the live word.
        const resumeFrac = mcB.words[nextIv.unitIdx - mcB.unitStart]?.frac0 ?? 0;
        const from = cellA.cumBefore + (cellA.w - cellA.aw) / 2 + departFrac * cellA.aw; // A's depart word
        const to = cellB.cumBefore + (cellB.w - cellB.aw) / 2 + resumeFrac * cellB.aw; // B's resume word
        scroll.snap(lerp(from, to, p));
    }

    /** One rAF step of recitation-driven playback. */
    function drivePlayback(): void {
        const nowMs = getTimeMs();
        const tSec = (nowMs + config.leadMs) / 1000;
        const r = recitationAt(tSec, lastActiveUnit);
        // A large audio-time jump in one frame is a seek (e.g. a linear-bar
        // click), not the normal forward creep — glide to it like any other.
        const seeked = lastTimeMs >= 0 && Math.abs(nowMs - lastTimeMs) > SEEK_JUMP_MS;
        lastTimeMs = nowMs;
        if (!r) {
            // Silence: hold the cell as frozen. Only an INTER-TAKE pause scrolls —
            // the needle stays visible, greys, and travels continuously across the
            // inter-cell gap (forward OR backward, so a loopback's pause reads as
            // travel back to the re-take, not a freeze). Within-verse / leading /
            // trailing silence is left untouched (needle hidden, held), so a
            // reciter's mid-verse pause doesn't grey or move the cursor.
            const nextIv = config.filmstripMotion !== 'snap' && frozenIdx >= 0
                ? nextIntervalAfter(sorted, tSec)
                : null;
            const nextIdx = nextIv ? (model.cellOfUnit[nextIv.unitIdx] ?? -1) : -1;
            // A sub-perceptual gap to the IMMEDIATE next verse (connected flow, not
            // a waqf stop) isn't a silence: hold the verse lit + the needle on so the
            // cursor glides straight through instead of greying/snapping for a frame
            // or two. The cells are laid out gapless for such a gap, so the next verse
            // picks up exactly where this one ended. Restricted to the forward
            // neighbour (`frozenIdx + 1`) so a backward loopback or a forward skip —
            // whose own pause can be brief — still scrolls a greyed needle.
            const microGap = nextIdx === frozenIdx + 1
                && (model.cells[frozenIdx]?.nextGapSec ?? Infinity) < MICRO_GAP_SEC;
            if (microGap) {
                silent = false;
                crossingGap = false;
                return;
            }
            silent = true;
            crossingGap = false;
            // Scroll any inter-take silence (forward OR backward), but NOT a
            // within-verse pause (nextIdx === frozenIdx → hold, needle hidden).
            if (nextIv && nextIdx >= 0 && nextIdx !== frozenIdx) {
                crossingGap = true;
                scrollThroughGap(tSec, nextIv, nextIdx);
                fillIdx = cellSpanningOffset(scroll.offset);
            }
            return;
        }
        const prevIdx = activeIdx;
        const prevFrac = cellFrac;
        lastActiveUnit = r.unitIdx;
        const discont = seeked || isJump(prevIdx, r.idx, prevFrac, r.frac);
        silent = false;
        crossingGap = false;
        activeIdx = r.idx;
        cellFrac = r.frac;
        frozenIdx = r.idx;

        const snap = config.filmstripMotion === 'snap';
        const target = snap ? offsetForCellCenter(r.idx) : offsetForReci(r);

        if (snap) {
            // Snap centers the active cell — a fixed point, not a moving one, so
            // a one-shot glide is correct. No needle, so the fill follows the recited
            // WORD fraction (not the cursor-vs-cell math the continuous modes use).
            fillIdx = r.idx;
            containerEl?.style.setProperty('--cell-active-fill', clamp(0, 1, r.frac) * 100 + '%');
            if (discont) jumpTo(target);
            else { disarmFillGlide(); if (r.idx !== prevIdx) scroll.glide(target); }
            return;
        }

        // Hybrid/tuner continuously center the LIVE recited position. Each cell is
        // crossed over ITS OWN recited occurrence (`offsetForReci` tracks the live
        // word), so a multi-verse loopback replays every verse at that verse's own
        // second-take pace — never collapsed onto the loopback verse's duration.
        // Forward play tracks instantly; a discontinuity (a backward loopback landing
        // or a seek) runs one eased, distance-proportional glide back onto the live
        // position and eases the fill once, then instant tracking resumes. A
        // loopback's pause-back is scrolled above (the inter-take gap), so by the
        // replay's first word the strip is already at the landing.
        if (discont) armFillGlide(GLIDE_MIN_MS);
        else disarmFillGlide();
        scroll.follow(target, discont);
        fillIdx = activeIdx;
    }

    // rAF while playing: drive the recitation mapping (all modes).
    $effect(() => {
        if (!playing) return;
        lastTimeMs = -1; // don't read the first frame as a seek
        let raf = 0;
        const loop = (): void => {
            if (!dragging && scrubMs == null) drivePlayback();
            raf = requestAnimationFrame(loop);
        };
        raf = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(raf);
    });

    // Progress-bar drag → scroll-follow the dragged time (both motion modes).
    // Maps the time through the recitation timeline so it tracks the recited
    // position (correct under overlap/repeats). Direct follow (no glide) — you're
    // scrubbing live. Silence in the scrub → hold (no move).
    $effect(() => {
        if (scrubMs == null || cw === 0) return;
        const r = recitationAt(scrubMs / 1000, -1);
        if (r) scroll.snap(offsetForReci(r)); // live drag → instant follow, no glide
    });

    // Reset recitation state when the chapter (units identity) changes.
    $effect(() => {
        void units; // track
        activeIdx = -1;
        cellFrac = 0;
        silent = false;
        crossingGap = false;
        frozenIdx = -1;
        fillIdx = -1;
        lastActiveUnit = -1;
        lastTimeMs = -1;
        scroll.cancel();
        if (fillGlideTimer) clearTimeout(fillGlideTimer);
        jumping = false;
        containerEl?.style.removeProperty('--cell-active-fill');
    });

    // Drag — pan the strip; release scrubs (tuner) or snaps to an ayah.
    let dragStartX = 0;
    let dragStartOffset = 0;
    let moved = false;

    function onPointerDown(e: PointerEvent): void {
        dragging = true;
        moved = false;
        dragStartX = e.clientX;
        dragStartOffset = scroll.offset;
        scroll.cancel(); // the drag owns offset now
        containerEl?.setPointerCapture(e.pointerId);
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp, { once: true });
    }
    function onPointerMove(e: PointerEvent): void {
        if (!dragging) return;
        const dx = e.clientX - dragStartX;
        if (Math.abs(dx) > 4) moved = true;
        scroll.snap(dragStartOffset - dx); // live drag → instant follow
    }
    function onPointerUp(e: PointerEvent): void {
        if (!dragging) return;
        dragging = false;
        window.removeEventListener('pointermove', onPointerMove);
        if (!moved) {
            clickToSeek(e.clientX);
            return;
        }
        if (config.filmstripMotion === 'tuner') {
            onSeek(timeAtOffset(scroll.offset));
        } else {
            const i = nearestPlayableCell(scroll.offset);
            if (i >= 0) {
                scroll.glide(offsetForCellCenter(i));
                onSeek(seekMsForCell(i));
            }
        }
    }
    function clickToSeek(clientX: number): void {
        if (!containerEl) return;
        const i = cellAtClientX(clientX);
        if (i < 0) return;
        if (!isPlayable(i)) return; // unrecited placeholder — non-clickable
        scroll.glide(offsetForCellCenter(i));
        onSeek(seekMsForCell(i));
    }

    /** Map a viewport x to the cell index under it (needle at cw/2; track
     *  translated by -offset; leading pad = cw/2). -1 when out of range. */
    function cellAtClientX(clientX: number): number {
        if (!containerEl) return -1;
        const rect = containerEl.getBoundingClientRect();
        const off = clientX - rect.left + scroll.offset - cw / 2;
        return nearestCell(clamp(0, lastRight, off));
    }

    /** Speculative-prewarm hook on pointer hover (no button): warm the seek
     *  position of the cell under the pointer. Suppressed while dragging. */
    function onHoverMove(e: PointerEvent): void {
        if (!onHoverPrewarm || dragging) return;
        const i = cellAtClientX(e.clientX);
        if (isPlayable(i)) onHoverPrewarm(seekMsForCell(i));
    }

    /** Re-sync after a seek while paused (parent calls this). */
    export function refresh(): void {
        if (cw === 0) return;
        crossingGap = false;
        const r = recitationAt((getTimeMs() + config.leadMs) / 1000, -1);
        if (!r) {
            // Landed in a gap → hold the frozen state (or nothing, pre-first).
            silent = true;
            return;
        }
        silent = false;
        activeIdx = r.idx;
        cellFrac = r.frac;
        frozenIdx = r.idx;
        fillIdx = r.idx;
        lastActiveUnit = r.unitIdx;
        lastTimeMs = -1; // resync; the next playing frame isn't a seek
        jumpTo(config.filmstripMotion === 'snap'
            ? offsetForCellCenter(r.idx)
            : offsetForReci(r));
    }

    /** Force the new chapter's first ayah into view before audio canplay. */
    export function showFirstAyah(): void {
        if (!cells.length) return;
        activeIdx = -1;
        cellFrac = 0;
        silent = false;
        crossingGap = false;
        frozenIdx = -1;
        fillIdx = -1;
        lastActiveUnit = -1;
        lastTimeMs = -1;
        scroll.cancel();
        containerEl?.style.removeProperty('--cell-active-fill');
        scroll.snap(offsetForCellCenter(0));
    }
</script>

{#if config.filmstripShow && cells.length}
    <!-- dir="ltr" island: a time-proportional scrubber (track translateX, needle
         pinned by %) — the strip must run left→right even under RTL. -->
    <div
        class="filmstrip"
        dir="ltr"
        bind:this={containerEl}
        bind:clientWidth={cw}
        style:height="{config.filmstripHeightPx}px"
        role="slider"
        tabindex="0"
        aria-label={scrubberAriaLabel}
        aria-valuemin={cells[0]!.ayah}
        aria-valuemax={cells[cells.length - 1]!.ayah}
        aria-valuenow={cells[activeIdx]?.ayah ?? cells[0]!.ayah}
        onpointerdown={onPointerDown}
        onpointermove={onHoverMove}
        onkeydown={(e) => {
            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                e.preventDefault();
                const d = e.key === 'ArrowRight' ? 1 : -1;
                // Anchor on the cell recited at the LIVE audio position, not the
                // (possibly frozen/stale) `activeIdx` — inside a dropped re-take
                // the active cell can lag, and ±1 off it would skip verses.
                const live = cellViaTime(getTimeMs());
                const base = live >= 0 ? live : (activeIdx < 0 ? 0 : activeIdx);
                // Step to the next RECITED verse in that direction — skip
                // unrecited placeholders (and don't run off either end).
                const i = nextPlayableCell(base, d);
                if (i < 0) return;
                scroll.glide(offsetForCellCenter(i));
                onSeek(seekMsForCell(i));
            }
        }}
    >
        <div
            class="track"
            style:transform="translateX({-scroll.offset}px)"
        >
            <div class="pad" style:width="{pad}px"></div>
            {#each cells as c, i (c.ayah)}
                <div
                    class="cell"
                    class:active={!silent && i === activeIdx && c.missing !== 'full'}
                    class:reached={c.missing !== 'full' && i < (silent ? frozenIdx : activeIdx)}
                    class:frozen={silent && i === frozenIdx && c.missing !== 'full'}
                    class:fillcell={i === fillIdx && c.missing !== 'full'}
                    class:preview={i === hoverIdx && i !== activeIdx && c.missing !== 'full'}
                    class:cursor={i === cursorIdx}
                    class:missing-words={c.missing === 'words'}
                    class:missing-full={c.missing === 'full'}
                    title={missingTitle(c)}
                    style:width="{c.w}px"
                    style:margin-right="{c.gapAfter}px"
                >
                    {#if c.missing !== 'full'}
                        <div class="cell-fill" class:glide={jumping && i === fillIdx}></div>
                    {/if}
                    <span class="cell-num">{['ar', 'ckb'].includes(i18n.locale) ? toArabicNumeral(c.ayah) : c.ayah}</span>
                </div>
            {/each}
            <div class="pad" style:width="{pad}px"></div>
            <!-- Waṣl rails — an overlay layer (the cells clip overflow) riding the
                 track transform: a thin accent rail under each gapless cross-verse
                 group, tying its fully-bordered sub-cells into one continuous span. -->
            {#each waslGroups as g (g.leftPx)}
                <div
                    class="wasl-rail"
                    style:left="{pad + g.leftPx}px"
                    style:width="{g.rightPx - g.leftPx}px"
                    aria-hidden="true"
                ></div>
            {/each}
        </div>
        {#if config.filmstripMotion !== 'snap' && (!silent || crossingGap)}
            <div class="needle" class:silent={crossingGap} aria-hidden="true"></div>
        {/if}
        <div class="fade fade-l" aria-hidden="true"></div>
        <div class="fade fade-r" aria-hidden="true"></div>
    </div>
{/if}

<style>
    .filmstrip {
        position: relative;
        width: 100%;
        overflow: hidden;
        touch-action: none;
        cursor: grab;
        user-select: none;
    }
    .filmstrip:active {
        cursor: grabbing;
    }
    /* The strip is a focusable slider (tabindex/role) so clicking or arrowing
       it focuses the container; suppress the browser's ring around the WHOLE
       strip — the active cell's accent border already marks position. */
    .filmstrip:focus,
    .filmstrip:focus-visible {
        outline: none;
    }
    /* The transform is driven imperatively by the JS scroll controller, so the
       track carries NO CSS transition — every scroll, near or far, runs through
       one distance-proportional, eased Hermite glide. */
    .track {
        position: absolute;
        top: 50%;
        left: 0;
        display: flex;
        align-items: center;
        transform: translateX(0);
        will-change: transform;
        translate: 0 -50%;
    }
    .pad {
        flex: 0 0 auto;
        height: 1px;
    }
    .cell {
        position: relative;
        flex: 0 0 auto;
        height: calc(100% - 12px);
        min-height: 22px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-1);
        overflow: hidden;
        transition: border-color var(--t-fast), background var(--t-fast);
    }
    .cell.reached {
        border-color: var(--border-default);
    }
    .cell.active {
        border-color: var(--accent);
        background: var(--accent-tint-soft);
    }
    /* Frozen cell — the verse held under a silence pause. De-accented (the
     *  highlight drops) but keeps its partial fill so the bar reads "held, not
     *  finished" until recitation regenerates. */
    .cell.frozen {
        border-color: var(--border-default);
    }
    /* Snap-mode cursor cell — the ayah under the invisible needle (active cell
     *  while playing, drag target while scrubbing). An inset accent ring keeps it
     *  distinct from `.active`'s tinted fill so both can show at once mid-drag. */
    .cell.cursor {
        border-color: var(--accent);
        box-shadow: inset 0 0 0 1px var(--accent);
    }
    /* Progress-bar hover preview — the verse recited at the hovered time. */
    .cell.preview {
        border-color: var(--accent-strong);
        border-style: dashed;
        background: var(--accent-tint);
    }
    .cell.preview .cell-num {
        color: var(--accent-strong);
    }
    /* Incomplete verse (present, missing some words) — a red inset ring so it
       coexists with the active/cursor accent BORDER (still recited → clickable
       + plays normally). */
    .cell.missing-words {
        box-shadow: inset 0 0 0 1.5px var(--state-missing-border-strong);
    }
    .cell.missing-words .cell-num {
        color: var(--state-missing-fg);
    }
    /* Fully-missing verse (never recited) — a red-bordered, tinted, dimmed
       placeholder. Non-clickable (the JS guards skip it); pointer-events stay so
       the "Not recited" tooltip shows. No fill / active / reached state. */
    .cell.missing-full {
        border-color: var(--state-missing-border-strong);
        background: var(--state-missing-bg);
        opacity: 0.55;
        cursor: not-allowed;
    }
    .cell.missing-full .cell-num {
        color: var(--state-missing-fg);
    }
    /* ---- Cross-verse waṣl merge -------------------------------------------
     * A continuous-recitation run renders as gapless, fully-bordered sub-cells —
     * each keeps its own number and border (the touching borders mark the verse
     * boundaries) — tied together by a thin accent rail under the row: the
     * "mega-cell" read without collapsing any cell's identity. The rail is an
     * overlay sibling of the cells (so their `overflow:hidden` doesn't clip it),
     * spans the whole group, and rides the track transform. */
    .wasl-rail {
        position: absolute;
        bottom: -2px;
        height: 2px;
        border-radius: 1px;
        background: var(--accent);
        box-shadow: 0 0 6px var(--accent-tint);
        pointer-events: none;
    }
    .cell-fill {
        position: absolute;
        inset: 0 auto 0 0;
        width: 0;
        background: var(--accent-tint);
        pointer-events: none;
    }
    /* The fill cell (the one the cursor/needle sits in) reads the per-frame fill
       var, so the bar's leading edge tracks the cursor. The fill cell is decoupled
       from `active` — during a re-tread or gap-cross the cursor (and its fill) can
       sit in a different cell than the latched-active verse. */
    .cell.fillcell .cell-fill {
        width: var(--cell-active-fill, 0%);
    }
    .cell.reached .cell-fill {
        width: 100%;
        background: var(--accent-tint-soft);
    }
    /* Eased fill ONLY during a loopback/seek glide — never a permanent
       transition (that would lag every forward-play frame). Duration is shared
       with the strip scroll (`--cell-glide-dur`) so bar + scroll move as one. */
    .cell.fillcell .cell-fill.glide {
        transition: width var(--cell-glide-dur, 320ms) ease-in-out;
    }
    .cell-num {
        position: relative;
        font-family: var(--font-mono);
        font-size: 11px;
        font-variant-numeric: tabular-nums;
        color: var(--text-muted);
    }
    .cell.active .cell-num {
        color: var(--accent);
    }
    .cell.reached .cell-num {
        color: var(--text-secondary);
    }
    .needle {
        position: absolute;
        top: 4px;
        bottom: 4px;
        left: 50%;
        width: 2px;
        transform: translateX(-50%);
        background: var(--accent);
        border-radius: 1px;
        pointer-events: none;
        box-shadow: 0 0 8px var(--accent-tint);
        transition: background var(--t-fast), box-shadow var(--t-fast);
    }
    /* Silence: the needle keeps traveling (across the gap) but greys out so the
       pause reads as "not reciting" without losing the position cursor. */
    .needle.silent {
        background: var(--text-muted);
        box-shadow: none;
    }
    @media (prefers-reduced-motion: reduce) {
        .needle {
            transition: none;
        }
    }
    .fade {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 40px;
        pointer-events: none;
    }
    .fade-l {
        left: 0;
        background: linear-gradient(to right, var(--panel), transparent);
    }
    .fade-r {
        right: 0;
        background: linear-gradient(to left, var(--panel), transparent);
    }
    /* The strip scroll is JS-driven and already applies targets instantly under
       reduced motion (`reducedMotion` in the scroller); kill the fill-glide
       transition too. */
    @media (prefers-reduced-motion: reduce) {
        .cell.fillcell .cell-fill.glide {
            transition: none;
        }
    }
</style>
