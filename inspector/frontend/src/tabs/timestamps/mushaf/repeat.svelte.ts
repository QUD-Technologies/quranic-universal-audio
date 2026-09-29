/**
 * Mushaf Repeat — the runtime driver + its UI state.
 *
 * Plays a verse range as clean canonical takes (see `canonical.ts`): each
 * verse × `each`, the whole range × `rounds`. With no pause (the default) the
 * recitation runs on: consecutive verses whose takes are adjacent in the
 * recording flow straight through, replays and other jumps seek at once. A
 * pause puts that much silence between every two plays — replays of a verse
 * and the step from one verse to the next alike.
 *
 * The Mushaf view calls `tick()` every frame (and on `timeupdate`, so a hidden
 * tab still honours boundaries). A seek the driver didn't make — a footer
 * skip, a scrub — ends the repeat: the listener has gone somewhere else.
 * Ranges may span surahs; the driver asks its host to switch chapter and
 * resumes once that chapter's audio + data are live.
 */
import { get } from 'svelte/store';

import { ensureDashCovering } from '../../../lib/playback/dash-covering';
import { signalDashSeekIntent } from '../../../lib/playback/dash-buffering';
import { dashPort } from '../../../lib/playback/dash-port';
import { loadChapterRecitation } from '../../../lib/recitation-data/load-chapter';
import { playerContext } from '../../../lib/stores/player-context';
import { canonicalTakes, type VerseTake } from './canonical';
import {
    firstCursor,
    nextCursor,
    type RepeatCursor,
    type RepeatRequest,
    type VerseRef,
} from './repeat-plan';

/** Next take starting within this of the current end plays on without a pause. */
const ADJACENT_MS = 400;
/** Ignore playhead drift this long after the driver's own seek. */
const SEEK_GRACE_MS = 600;
/** Playhead this far outside the step (not our seek) = the listener navigated away. */
const DRIFT_MS = 1500;

export interface RepeatStep extends VerseRef, VerseTake {}

interface Host {
    /** Surah whose audio + recitation data are loaded and playable now. */
    readyChapter(): number;
    /** Switch the shared player to `surah` (same reciter). */
    switchChapter(surah: number): void;
}

class MushafRepeat {
    running = $state(false);
    loading = $state(false);
    steps = $state<RepeatStep[]>([]);
    each = $state(1);
    rounds = $state(1);
    /** Silence between plays (ms), one of `REPEAT_PAUSES_MS`. */
    pauseMs = $state(0);
    cursor = $state<RepeatCursor | null>(null);
    /** Range being edited in the popover. `to` null = just `from`. */
    from = $state<VerseRef | null>(null);
    to = $state<VerseRef | null>(null);
    /** Popover end waiting for a verse picked on the page. */
    picking = $state<'from' | 'to' | null>(null);
    error = $state('');

    private host: Host | null = null;
    private waiting = false;
    private pendingChapter = 0;
    private gapTimer: ReturnType<typeof setTimeout> | null = null;
    private seekedAt = 0;

    current = $derived(this.cursor ? (this.steps[this.cursor.verse] ?? null) : null);

    attach(host: Host): () => void {
        this.host = host;
        return () => {
            this.stop();
            this.host = null;
        };
    }

    /** Resolve canonical takes for `verses` (loading each surah once) and start. */
    async start(slug: string, verses: VerseRef[]): Promise<boolean> {
        this.stop();
        this.loading = true;
        this.error = '';
        try {
            const takes = new Map<number, Map<string, VerseTake>>();
            for (const s of new Set(verses.map((v) => v.surah))) {
                const data = await loadChapterRecitation(slug, s);
                takes.set(s, data ? canonicalTakes(data.units) : new Map());
            }
            const steps: RepeatStep[] = [];
            for (const v of verses) {
                const t = takes.get(v.surah)?.get(`${v.surah}:${v.ayah}`);
                if (t) steps.push({ ...v, ...t });
            }
            const req: RepeatRequest = { verses: steps, each: this.each, rounds: this.rounds };
            const c = firstCursor(req);
            if (!c) return false;
            this.steps = steps;
            this.cursor = c;
            this.running = true;
            this.go(steps[0]!);
            return true;
        } catch (e) {
            console.error('Mushaf repeat: failed to resolve verse takes', e);
            return false;
        } finally {
            this.loading = false;
        }
    }

    stop(): void {
        if (this.gapTimer) clearTimeout(this.gapTimer);
        this.gapTimer = null;
        this.waiting = false;
        this.pendingChapter = 0;
        this.running = false;
        this.cursor = null;
    }

    /** Per-frame boundary check. */
    tick(): void {
        const step = this.current;
        if (!this.running || !step || this.waiting) return;
        if (this.pendingChapter) {
            if (this.host?.readyChapter() !== this.pendingChapter) return;
            this.pendingChapter = 0;
            this.seekPlay(step.startMs);
            return;
        }
        if (dashPort.paused) return;
        const ms = dashPort.currentTimeMs();
        const settled = performance.now() - this.seekedAt > SEEK_GRACE_MS;
        if (settled && (ms < step.startMs - DRIFT_MS || ms > step.endMs + DRIFT_MS)) {
            this.stop();
            return;
        }
        if (ms >= step.endMs) this.advance(step);
    }

    private advance(step: RepeatStep): void {
        const req: RepeatRequest = { verses: this.steps, each: this.each, rounds: this.rounds };
        const next = this.cursor ? nextCursor(req, this.cursor) : null;
        if (!next) {
            dashPort.pauseAndFlush();
            this.stop();
            return;
        }
        const nextStep = this.steps[next.verse]!;
        this.cursor = next;
        if (this.pauseMs > 0) {
            dashPort.pauseAndFlush();
            this.waiting = true;
            this.gapTimer = setTimeout(() => {
                this.gapTimer = null;
                this.waiting = false;
                if (this.running) this.go(nextStep);
            }, this.pauseMs);
            return;
        }
        const gap = nextStep.startMs - step.endMs;
        const adjacent = nextStep !== step && nextStep.surah === step.surah && Math.abs(gap) <= ADJACENT_MS;
        if (!adjacent) this.go(nextStep);
    }

    private go(step: RepeatStep): void {
        if (get(playerContext).surahNum !== step.surah || this.host?.readyChapter() !== step.surah) {
            this.pendingChapter = step.surah;
            this.host?.switchChapter(step.surah);
            return;
        }
        this.seekPlay(step.startMs);
    }

    private seekPlay(ms: number): void {
        this.seekedAt = performance.now();
        ensureDashCovering(ms);
        dashPort.uncut();
        dashPort.seekAndPlay(ms);
        signalDashSeekIntent();
    }
}

export const mushafRepeat = new MushafRepeat();
