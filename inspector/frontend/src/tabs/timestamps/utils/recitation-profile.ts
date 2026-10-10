/**
 * A recitation's madd, ghunnah and pause durations laid out for the readings panel.
 *
 * The server projects `recitation_profile.json` into `TsRecitationProfile`
 * (madd types in display order, a chosen length only where Hafs allows one —
 * see `inspector/services/reference/recitation_profile.py`). `profileSections`
 * lays it out as a madd, a ghunnah and a pauses section, one row per shown
 * measure: a madd type with a length choice carries every length Hafs
 * allows it, the one read marked.
 */

import type { TsMaddRow, TsRecitationProfile } from '../../../lib/types/generated/schemas';

export type MaddKind = TsMaddRow['kind'];
export type MaddLength = NonNullable<TsMaddRow['length']>;
export type ProfileMeasure = MaddKind | 'ghunnah' | 'pauses';

/** Counted harakat of each madd length. */
export const LENGTH_COUNTS: Record<MaddLength, number> = { qasr: 2, tawassut: 4, ishbaa: 6 };

/** The lengths Hafs allows each madd type with a choice, shortest first. */
export const LENGTH_CHOICES: Partial<Record<MaddKind, readonly MaddLength[]>> = {
    munfasil: ['qasr', 'tawassut'],
    arid: ['qasr', 'tawassut', 'ishbaa'],
    leen: ['qasr', 'tawassut', 'ishbaa'],
};

export interface ProfileLength {
    length: MaddLength;
    count: number;
    chosen: boolean;
}

export interface ProfileRow {
    measure: ProfileMeasure;
    ms: number;
    /** Every allowed length when the type has a choice and one was read; else empty. */
    lengths: ProfileLength[];
}

function maddRow(row: TsMaddRow): ProfileRow {
    const choices = row.length ? (LENGTH_CHOICES[row.kind] ?? []) : [];
    return {
        measure: row.kind,
        ms: row.mean_ms,
        lengths: choices.map((length) => ({
            length,
            count: LENGTH_COUNTS[length],
            chosen: length === row.length,
        })),
    };
}

export interface ProfileSection {
    section: 'madd' | 'ghunnah' | 'pauses';
    rows: ProfileRow[];
}

/** The madd, ghunnah and pauses sections; an empty section is dropped. */
export function profileSections(profile: TsRecitationProfile | null): ProfileSection[] {
    if (!profile) return [];
    const single = (measure: 'ghunnah' | 'pauses', ms: number | null | undefined): ProfileRow[] =>
        ms == null ? [] : [{ measure, ms, lengths: [] }];
    const sections: ProfileSection[] = [
        { section: 'madd', rows: (profile.madd ?? []).map(maddRow) },
        { section: 'ghunnah', rows: single('ghunnah', profile.ghunnah_ms) },
        { section: 'pauses', rows: single('pauses', profile.pause_ms) },
    ];
    return sections.filter((s) => s.rows.length > 0);
}

/** Milliseconds as seconds with two decimals ("1.64"). */
export function formatSeconds(ms: number): string {
    return new Intl.NumberFormat('en', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(ms / 1000);
}
