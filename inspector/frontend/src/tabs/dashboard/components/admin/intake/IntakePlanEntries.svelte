<script lang="ts">
    /**
     * Intake plan entries: one row per fetchable file with its editable
     * chapter assignment. Blank chapters = the file is left out.
     */
    import type { PlanEntry } from '../../../../../lib/types/generated/schemas';
    import { formatDuration, parseChapters } from './plan-utils';

    interface Props {
        entries: PlanEntry[];
        drafts: Record<string, string>;
        duplicates: Set<number>;
        disabled?: boolean;
        onEdit: (_key: string, _text: string) => void;
    }
    let { entries, drafts, duplicates, disabled = false, onEdit }: Props = $props();

    let reviewOnly = $state(false);

    const CONFIDENCE_LABEL: Record<string, string> = {
        exact: 'exact',
        high: 'high',
        low: 'low',
        manual: 'manual',
        none: 'skipped',
    };

    interface RowView {
        entry: PlanEntry;
        text: string;
        invalid: boolean;
        dup: boolean;
        review: boolean;
    }

    const rows = $derived<RowView[]>(
        entries.map((entry) => {
            const text = drafts[entry.key] ?? '';
            const parsed = parseChapters(text);
            const dup = !!parsed && parsed.some((c) => duplicates.has(c));
            const conf = entry.confidence ?? 'none';
            return {
                entry,
                text,
                invalid: parsed === null,
                dup,
                review: dup || parsed === null || conf === 'low' || conf === 'none',
            };
        }),
    );
    const reviewCount = $derived(rows.filter((r) => r.review).length);
    // Snapshot the filtered keys when the filter turns on, so fixing a row
    // does not make it vanish while it is being edited.
    let reviewKeys = $state<Set<string>>(new Set());
    const shown = $derived(reviewOnly ? rows.filter((r) => reviewKeys.has(r.entry.key)) : rows);

    function toggleReview(on: boolean): void {
        reviewKeys = new Set(rows.filter((r) => r.review).map((r) => r.entry.key));
        reviewOnly = on;
    }
</script>

<div class="entries">
    <div class="entries-head">
        <h4>Files</h4>
        <label class="filter">
            <input
                type="checkbox"
                checked={reviewOnly}
                onchange={(e) => toggleReview(e.currentTarget.checked)}
            />
            Needs review <span class="count">{reviewCount}</span>
        </label>
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th class="num">#</th>
                    <th>Title</th>
                    <th class="dur">Length</th>
                    <th class="chs">Chapters</th>
                    <th class="conf">Match</th>
                </tr>
            </thead>
            <tbody>
                {#each shown as r (r.entry.key)}
                    <tr class:dup={r.dup}>
                        <td class="num">{r.entry.index ?? ''}</td>
                        <td class="title">
                            <a
                                href={r.entry.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={r.entry.title || r.entry.url}
                            >{r.entry.title || r.entry.url}</a>
                        </td>
                        <td class="dur">{formatDuration(r.entry.duration_sec)}</td>
                        <td class="chs">
                            <input
                                type="text"
                                value={r.text}
                                class:invalid={r.invalid}
                                aria-invalid={r.invalid}
                                aria-label={`Chapters for ${r.entry.title || r.entry.key}`}
                                title={r.invalid
                                    ? 'Use 1, 1-2, 78-114 or 1,2 (chapters 1–114); blank skips the file'
                                    : r.dup
                                      ? 'Another file also covers one of these chapters'
                                      : undefined}
                                placeholder="skip"
                                spellcheck="false"
                                {disabled}
                                oninput={(e) => onEdit(r.entry.key, e.currentTarget.value)}
                            />
                        </td>
                        <td class="conf">
                            <span class="chip {r.entry.confidence ?? 'none'}">
                                {CONFIDENCE_LABEL[r.entry.confidence ?? 'none']}
                            </span>
                        </td>
                    </tr>
                {:else}
                    <tr><td class="empty" colspan="5">
                        {reviewOnly ? 'Nothing needs review.' : 'No files found in the source.'}
                    </td></tr>
                {/each}
            </tbody>
        </table>
    </div>
</div>

<style>
    .entries { display: flex; flex-direction: column; gap: var(--s-2); }
    .entries-head { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
    .entries-head h4 { margin: 0; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 500; }
    .filter { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-meta); color: var(--text-secondary); cursor: pointer; }
    .filter .count { font-family: var(--font-mono); font-size: 10.5px; color: var(--text-faint); font-variant-numeric: tabular-nums; }

    .table-wrap {
        max-height: 360px; overflow: auto;
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
    }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: var(--fs-meta); }
    thead th {
        position: sticky; top: 0; z-index: 1;
        padding: 5px var(--s-2); text-align: left; font-weight: 500;
        font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
        color: var(--text-faint); background: var(--canvas-inset);
        border-bottom: 1px solid var(--border-quiet);
    }
    td { padding: 3px var(--s-2); border-bottom: 1px solid var(--border-quiet); vertical-align: middle; }
    tbody tr:last-child td { border-bottom: none; }
    tr.dup td { background: var(--state-error-bg); }

    .num { width: 36px; text-align: right; font-family: var(--font-mono); color: var(--text-faint); font-variant-numeric: tabular-nums; }
    .dur { width: 64px; text-align: right; font-family: var(--font-mono); color: var(--text-muted); font-variant-numeric: tabular-nums; }
    .chs { width: 104px; }
    .conf { width: 72px; }
    .title a { display: block; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-decoration: none; }
    .title a:hover { color: var(--accent); }

    .chs input {
        width: 100%; padding: 3px 6px;
        background: var(--panel); color: var(--text-primary);
        border: 1px solid var(--border-default); border-radius: var(--r-2);
        font: var(--fs-meta)/1.3 var(--font-mono);
    }
    .chs input:focus { outline: none; border-color: var(--accent); }
    .chs input::placeholder { color: var(--text-faint); }
    .chs input.invalid { border-color: var(--state-error-fg); color: var(--state-error-fg); }
    .chs input:disabled { opacity: 0.6; }

    .chip { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 10px; font-weight: 500; white-space: nowrap; }
    .chip.exact, .chip.high { color: var(--text-muted); background: var(--panel); }
    .chip.low { color: var(--state-warn-fg); background: var(--state-warn-bg); }
    .chip.manual { color: var(--accent); background: var(--accent-tint); }
    .chip.none { color: var(--text-faint); font-style: italic; }
    .empty { padding: var(--s-4); text-align: center; color: var(--text-faint); font-style: italic; }
</style>
