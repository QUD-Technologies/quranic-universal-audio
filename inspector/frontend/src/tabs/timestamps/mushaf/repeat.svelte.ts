/**
 * Mushaf Repeat: the Mushaf app's repeat, driven on this app's player.
 *
 * A plan (`RepeatPlan`: verses `from`..`to`, each `verseTimes`, the whole range `rangeTimes`,
 * `Infinity` looping, `pauseMs` of silence between steps) plays the steps of the core's
 * `repeatSteps` over this reciter's chapters, each verse its canonical take (`repeat-source.ts`).
 * A step ends at the core's `stepEnded`; with no pause, a next step that starts where the audio is
 * plays straight on, any other seeks at once.
 *
 * The Mushaf view calls `tick()` every frame (and on `timeupdate`, so a hidden tab still honours
 * step ends). A seek the driver didn't make (a footer skip, a scrub, a word click) ends the repeat.
 * A plan may span surahs: the driver asks its host to switch chapter and resumes once that
 * chapter's audio and data are live. A surah's timing is fetched when the plan reaches it
 * (`RepeatQueue`), never for the whole range up front; the audio is held while it loads.
 */
import { type RepeatAt, type RepeatPlan, stepEnded, type SurahStep, surahTiming } from '@qud-technologies/mushaf-core/app';
import { get } from 'svelte/store';

import { ensureDashCovering } from '../../../lib/playback/dash-covering';
import { signalDashSeekIntent } from '../../../lib/playback/dash-buffering';
import { dashPort } from '../../../lib/playback/dash-port';
import { loadChapterRecitation } from '../../../lib/recitation-data/load-chapter';
import { playerContext } from '../../../lib/stores/player-context';
import { chapterTiming, planSurahs, RepeatQueue } from './repeat-source';

/** A next step starting within this of where the audio is plays on without a seek (ms). */
const ADJACENT_MS = 400;
/** Ignore playhead drift this long after the driver's own seek (ms). */
const SEEK_GRACE_MS = 600;
/** Playhead this far outside the step (not our seek) = the listener went somewhere else (ms). */
const DRIFT_MS = 1500;

interface Host {
    /** Surah whose audio + recitation data are loaded and playable now. */
    readyChapter(): number;
    /** Switch the shared player to `surah` (same reciter). */
    switchChapter(surah: number): void;
}

class MushafRepeat {
    /** The plan playing, or null. */
    plan = $state<RepeatPlan | null>(null);
    /** Where the plan is (round, surah, verse, repetition). */
    at = $state<RepeatAt | null>(null);
    loading = $state(false);
    /** First and last `s:a` of the spread on show (the view keeps it), for "this page". */
    spread = $state<[string, string] | null>(null);

    readonly running = $derived(this.plan !== null);

    private host: Host | null = null;
    private queue: RepeatQueue | null = null;
    private slug = '';
    /** Bumped by every start and stop: a chapter load that outlives its run is dropped. */
    private run = 0;
    private step: SurahStep | null = null;
    private waiting = false;
    private pendingChapter = 0;
    private gapTimer: ReturnType<typeof setTimeout> | null = null;
    private seekedAt = 0;

    attach(host: Host): () => void {
        this.host = host;
        return () => {
            this.stop();
            this.host = null;
        };
    }

    /** Start the plan at its first verse (`surahs`: the reciter's); false when nothing of it is recited. */
    async start(slug: string, plan: RepeatPlan, surahs: readonly number[]): Promise<boolean> {
        this.stop();
        const run = this.run;
        this.loading = true;
        this.slug = slug;
        this.queue = new RepeatQueue(plan, planSurahs(plan.from, plan.to).filter((n) => surahs.includes(n)));
        try {
            const first = await this.pull(run);
            if (!first || run !== this.run) return false;
            this.plan = plan;
            this.play(first);
            return true;
        } catch (e) {
            console.error('Mushaf repeat: failed to load the plan', e);
            return false;
        } finally {
            this.loading = false;
        }
    }

    stop(): void {
        this.run++;
        if (this.gapTimer) clearTimeout(this.gapTimer);
        this.gapTimer = null;
        this.waiting = false;
        this.pendingChapter = 0;
        this.queue = null;
        this.step = null;
        this.plan = null;
        this.at = null;
    }

    /** Per-frame step check. */
    tick(): void {
        const step = this.step;
        if (!this.plan || !step || this.waiting) return;
        if (this.pendingChapter) {
            if (this.host?.readyChapter() !== this.pendingChapter) return;
            this.pendingChapter = 0;
            this.seekPlay(step.from);
            return;
        }
        if (dashPort.paused) return;
        const ms = dashPort.currentTimeMs();
        const settled = performance.now() - this.seekedAt > SEEK_GRACE_MS;
        if (settled && (ms < step.from - DRIFT_MS || (step.to !== null && ms > step.to + DRIFT_MS))) {
            this.stop();
            return;
        }
        if (stepEnded(step, ms)) this.advance(step, ms);
    }

    /** The plan's next step, fetching each surah's timing as the plan reaches it; null at its end. */
    private async pull(run: number): Promise<SurahStep | null> {
        for (;;) {
            const next = this.queue?.next() ?? null;
            if (next === null || 'kind' in next) return next;
            const data = await loadChapterRecitation(this.slug, next.load);
            if (run !== this.run) return null;
            this.queue?.provide(next.load, data ? chapterTiming(next.load, data.units) : surahTiming(next.load, [], []));
        }
    }

    private advance(done: SurahStep, ms: number): void {
        const next = this.queue?.next() ?? null;
        if (next === null || 'kind' in next) {
            this.proceed(done, ms, next);
            return;
        }
        // The plan reached a surah not read yet: hold the audio while its timing loads.
        const run = this.run;
        dashPort.pauseAndFlush();
        this.waiting = true;
        void this.pull(run)
            .then((step) => {
                if (run !== this.run) return;
                this.waiting = false;
                this.proceed(done, ms, step);
            })
            .catch((e: unknown) => {
                console.error('Mushaf repeat: failed to load the next surah', e);
                if (run === this.run) this.stop();
            });
    }

    private proceed(done: SurahStep, ms: number, step: SurahStep | null): void {
        if (!step) {
            dashPort.pauseAndFlush();
            this.stop();
            return;
        }
        if (this.plan!.pauseMs > 0) {
            this.step = step;
            this.at = step.repeatAt ?? null;
            dashPort.pauseAndFlush();
            this.waiting = true;
            this.gapTimer = setTimeout(() => {
                this.gapTimer = null;
                this.waiting = false;
                if (this.plan) this.play(step);
            }, this.plan!.pauseMs);
            return;
        }
        // Read on: the next verse starts where the audio already is.
        if (step.surah === done.surah && Math.abs(step.from - ms) <= ADJACENT_MS) {
            this.step = step;
            this.at = step.repeatAt ?? null;
            return;
        }
        this.play(step);
    }

    private play(step: SurahStep): void {
        this.step = step;
        this.at = step.repeatAt ?? null;
        if (get(playerContext).surahNum !== step.surah || this.host?.readyChapter() !== step.surah) {
            this.pendingChapter = step.surah;
            this.host?.switchChapter(step.surah);
            return;
        }
        this.seekPlay(step.from);
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
