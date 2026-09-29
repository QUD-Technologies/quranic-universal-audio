/**
 * Imperative page state for the Mushaf view — the recited word, the current
 * verse's band, and which words are hidden. Runs off the per-frame tick, so it
 * touches the DOM only when something changed: a new word flips two classes; a
 * new verse or visibility pref re-walks the ~300 words on the spread and
 * re-positions the per-line verse bands.
 */
import type { MushafScope } from '../stores/mushaf';
import type { WordIndex } from './layout';
import { compareRefs, type VerseRef } from './repeat-plan';

export interface HighlightState {
    /** DK id of the word being recited, 0 in silence. */
    activeId: number;
    /** "s:a" of the verse being (or last) recited, '' before any. */
    verseKey: string;
    /** Last word reached — everything after it counts as upcoming. */
    progressId: number;
    scope: MushafScope;
    showUpcoming: boolean;
}

const BAND_CLASS = 'mp-band';
const STATE_CLASSES = ['is-active', 'is-hidden', 'is-pick'];

export class PageHighlighter {
    private els: HTMLElement[] = [];
    private byId = new Map<number, HTMLElement>();
    private last: HighlightState | null = null;
    private activeEl: HTMLElement | null = null;

    private spot = '';

    /** Re-read the live word spans (after the rendered pages changed). Spans
     *  the pages re-used keep their classes, so every state class is reset. */
    rebuild(root: HTMLElement): void {
        this.els = [...root.querySelectorAll<HTMLElement>('.mv-slot [data-w]')];
        this.byId = new Map(this.els.map((el) => [Number(el.dataset.w), el]));
        for (const el of this.els) el.classList.remove(...STATE_CLASSES);
        for (const line of root.querySelectorAll('.mv-slot .' + BAND_CLASS)) line.remove();
        this.last = null;
        this.activeEl = null;
        this.spot = '';
    }

    /** Light up the verses `from`..`to` (inclusive, either order); null clears. */
    spotlight(range: [VerseRef, VerseRef] | null, words: WordIndex): void {
        const key = range ? range.map((v) => `${v.surah}:${v.ayah}`).join('-') : '';
        if (key === this.spot) return;
        this.spot = key;
        const [a, b] = range ? [...range].sort(compareRefs) : [null, null];
        for (const el of this.els) {
            const id = Number(el.dataset.w);
            const v = { surah: words.surah[id] ?? 0, ayah: words.ayah[id] ?? 0 };
            el.classList.toggle('is-pick', !!a && !!b && compareRefs(v, a) >= 0 && compareRefs(v, b) <= 0);
        }
    }

    /** Force the next `apply` to redo everything (layout moved). */
    invalidate(): void {
        this.last = null;
    }

    apply(s: HighlightState, words: WordIndex): void {
        const prev = this.last;
        const full = !prev
            || prev.verseKey !== s.verseKey
            || prev.scope !== s.scope
            || prev.showUpcoming !== s.showUpcoming
            || (!s.showUpcoming && prev.progressId !== s.progressId);
        if (full) this.applyAll(s, words);
        else if (prev.activeId !== s.activeId) this.setActive(s.activeId);
        this.last = { ...s };
    }

    private setActive(id: number): void {
        this.activeEl?.classList.remove('is-active');
        this.activeEl = id ? (this.byId.get(id) ?? null) : null;
        this.activeEl?.classList.add('is-active');
    }

    private applyAll(s: HighlightState, words: WordIndex): void {
        const inVerse: HTMLElement[] = [];
        for (const el of this.els) {
            const id = Number(el.dataset.w);
            const verse = `${words.surah[id]}:${words.ayah[id]}`;
            const hidden = (s.scope === 'verse' && !!s.verseKey && verse !== s.verseKey)
                || (!s.showUpcoming && id > s.progressId);
            el.classList.toggle('is-hidden', hidden);
            if (verse === s.verseKey && !hidden) inVerse.push(el);
        }
        this.setActive(s.activeId);
        this.placeBands(inVerse);
    }

    /** One band per line, spanning that line's visible current-verse words. */
    private placeBands(inVerse: HTMLElement[]): void {
        const spans = new Map<HTMLElement, { left: number; right: number }>();
        for (const el of inVerse) {
            const line = el.parentElement;
            if (!line) continue;
            const left = el.offsetLeft;
            const right = left + el.offsetWidth;
            const cur = spans.get(line);
            if (cur) {
                cur.left = Math.min(cur.left, left);
                cur.right = Math.max(cur.right, right);
            } else spans.set(line, { left, right });
        }
        const lines = new Set(this.els.map((el) => el.parentElement).filter((l): l is HTMLElement => !!l));
        for (const line of lines) {
            let band = line.querySelector<HTMLElement>(':scope > .' + BAND_CLASS);
            const span = spans.get(line);
            if (!span) {
                band?.remove();
                continue;
            }
            if (!band) {
                band = document.createElement('span');
                band.className = BAND_CLASS;
                band.setAttribute('aria-hidden', 'true');
                line.prepend(band);
            }
            const pad = Math.max(2, (span.right - span.left) * 0.01);
            band.style.left = `${span.left - pad}px`;
            band.style.width = `${span.right - span.left + 2 * pad}px`;
        }
    }
}
