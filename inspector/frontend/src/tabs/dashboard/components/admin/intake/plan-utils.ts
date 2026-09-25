/**
 * Pure helpers for the intake plan review panel: chapter-range parsing and
 * formatting, local coverage, durations, and identity dirty-checking.
 */
import type { PlanEntry, PlanIdentity } from '../../../../../lib/types/generated/schemas';

export const FIRST_CHAPTER = 1;
export const LAST_CHAPTER = 114;

const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3600;

/** Collapse sorted unique numbers into `[start, end]` runs. */
export function toRuns(nums: readonly number[]): [number, number][] {
    const runs: [number, number][] = [];
    for (const n of nums) {
        const last = runs[runs.length - 1];
        if (last && n === last[1] + 1) last[1] = n;
        else runs.push([n, n]);
    }
    return runs;
}

/** `[1,2,3,7]` → `"1-3,7"` (the editable form the chapters input accepts). */
export function formatChapters(nums: readonly number[] | null | undefined): string {
    return toRuns(sortUnique(nums ?? []))
        .map(([a, b]) => (a === b ? String(a) : `${a}-${b}`))
        .join(',');
}

/** `[3,4,5,9]` → `"3–5, 9"` (display form). */
export function formatRanges(nums: readonly number[]): string {
    return toRuns(sortUnique(nums))
        .map(([a, b]) => (a === b ? String(a) : `${a}–${b}`))
        .join(', ');
}

function sortUnique(nums: readonly number[]): number[] {
    return [...new Set(nums)].sort((a, b) => a - b);
}

function inRange(n: number): boolean {
    return Number.isInteger(n) && n >= FIRST_CHAPTER && n <= LAST_CHAPTER;
}

/**
 * Parse `"1"`, `"1-2"`, `"78-114"`, `"1,2"` (blank = skip → `[]`) into a sorted
 * unique chapter list; `null` when any part is malformed or out of 1..114.
 */
export function parseChapters(text: string): number[] | null {
    const trimmed = text.trim();
    if (!trimmed) return [];
    const out: number[] = [];
    for (const raw of trimmed.split(',')) {
        const part = raw.trim();
        const range = /^(\d+)\s*[-–]\s*(\d+)$/.exec(part);
        if (range) {
            const a = Number(range[1]);
            const b = Number(range[2]);
            if (!inRange(a) || !inRange(b) || a > b) return null;
            for (let n = a; n <= b; n++) out.push(n);
        } else if (/^\d+$/.test(part) && inRange(Number(part))) {
            out.push(Number(part));
        } else {
            return null;
        }
    }
    return sortUnique(out);
}

export function sameChapters(a: readonly number[], b: readonly number[]): boolean {
    const sa = sortUnique(a);
    const sb = sortUnique(b);
    return sa.length === sb.length && sa.every((n, i) => n === sb[i]);
}

export interface LocalCoverage {
    covered: number[];
    missing: number[];
    duplicates: Set<number>;
    combined: number;
}

/** Coverage of the current (unsaved) edits; invalid rows count as skipped. */
export function computeCoverage(parsed: readonly (number[] | null)[]): LocalCoverage {
    const seen = new Map<number, number>();
    let combined = 0;
    for (const chs of parsed) {
        if (!chs || chs.length === 0) continue;
        if (chs.length > 1) combined++;
        for (const c of chs) seen.set(c, (seen.get(c) ?? 0) + 1);
    }
    const covered = [...seen.keys()].sort((a, b) => a - b);
    const missing: number[] = [];
    for (let c = FIRST_CHAPTER; c <= LAST_CHAPTER; c++) if (!seen.has(c)) missing.push(c);
    const duplicates = new Set([...seen].filter(([, n]) => n > 1).map(([c]) => c));
    return { covered, missing, duplicates, combined };
}

/** `83` → `"1:23"`, `3723` → `"1:02:03"`, null → `""`. */
export function formatDuration(sec: number | null | undefined): string {
    if (sec === null || sec === undefined || !Number.isFinite(sec)) return '';
    const total = Math.round(sec);
    const h = Math.floor(total / SECONDS_PER_HOUR);
    const m = Math.floor((total % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE);
    const s = String(total % SECONDS_PER_MINUTE).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

const IDENTITY_FIELDS: (keyof PlanIdentity)[] = [
    'slug',
    'reciter_id',
    'name_en',
    'name_ar',
    'country',
    'channel',
    'source',
    'new_source_name',
    'new_source_url',
    'recording_year',
];

function norm(v: unknown): string {
    if (v === null || v === undefined) return '';
    if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
    return String(v).trim();
}

export function identityEqual(a: PlanIdentity, b: PlanIdentity): boolean {
    return IDENTITY_FIELDS.every((f) => norm(a[f]) === norm(b[f]));
}

/** Trim strings, blank → null for optional fields, coerce the year. */
export function cleanIdentity(id: PlanIdentity): PlanIdentity {
    const opt = (v: string | null | undefined): string | null => (v ?? '').trim() || null;
    const year = Number(id.recording_year);
    return {
        slug: (id.slug ?? '').trim(),
        reciter_id: (id.reciter_id ?? '').trim(),
        name_en: opt(id.name_en),
        name_ar: opt(id.name_ar),
        country: opt(id.country),
        channel: (id.channel ?? '').trim(),
        source: (id.source ?? '').trim(),
        new_source_name: opt(id.new_source_name),
        new_source_url: opt(id.new_source_url),
        recording_year:
            id.recording_year === null || id.recording_year === undefined || !Number.isFinite(year)
                ? null
                : year,
    };
}

/** Chapter text per entry key, seeded from the saved plan. */
export function draftsFrom(entries: readonly PlanEntry[]): Record<string, string> {
    return Object.fromEntries(entries.map((e) => [e.key, formatChapters(e.chapters)]));
}
