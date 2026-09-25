<script lang="ts">
    /**
     * Intake plan entries: one row per file of the source with its include
     * flag. Which surahs a file holds is detected from the audio when
     * aligning; only typed links carry contributor-given chapters (read-only).
     */
    import type { PlanEntry } from '../../../../../lib/types/generated/schemas';
    import { formatDuration, formatRanges } from './plan-utils';

    interface Props {
        entries: PlanEntry[];
        includes: Record<string, boolean>;
        /** Host `links`: show the contributor-typed chapters column. */
        showChapters: boolean;
        disabled?: boolean;
        onToggle: (_key: string, _include: boolean) => void;
    }
    let { entries, includes, showChapters, disabled = false, onToggle }: Props = $props();

    let excludedOnly = $state(false);

    const excludedCount = $derived(entries.filter((e) => !includes[e.key]).length);
    // Snapshot the filtered keys when the filter turns on, so re-including a
    // row does not make it vanish while it is being reviewed.
    let excludedKeys = $state<Set<string>>(new Set());
    const shown = $derived(excludedOnly ? entries.filter((e) => excludedKeys.has(e.key)) : entries);
    const colCount = $derived(showChapters ? 5 : 4);

    function toggleExcluded(on: boolean): void {
        excludedKeys = new Set(entries.filter((e) => !includes[e.key]).map((e) => e.key));
        excludedOnly = on;
    }
</script>

<div class="entries">
    <div class="entries-head">
        <h4>Files</h4>
        {#if excludedCount > 0 || excludedOnly}
            <label class="filter">
                <input
                    type="checkbox"
                    checked={excludedOnly}
                    onchange={(e) => toggleExcluded(e.currentTarget.checked)}
                />
                Show excluded only <span class="count">{excludedCount}</span>
            </label>
        {/if}
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th class="num">#</th>
                    <th>Title</th>
                    <th class="dur">Length</th>
                    {#if showChapters}<th class="chs">Chapters</th>{/if}
                    <th class="inc">Include</th>
                </tr>
            </thead>
            <tbody>
                {#each shown as entry (entry.key)}
                    {@const included = !!includes[entry.key]}
                    <tr class:excluded={!included}>
                        <td class="num">{entry.index ?? ''}</td>
                        <td class="title">
                            <a
                                href={entry.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={entry.title || entry.url}
                            >{entry.title || entry.url}</a>
                        </td>
                        <td class="dur">{formatDuration(entry.duration_sec)}</td>
                        {#if showChapters}
                            <td class="chs">{formatRanges(entry.chapters ?? [])}</td>
                        {/if}
                        <td class="inc">
                            <input
                                type="checkbox"
                                checked={included}
                                aria-label={`Include ${entry.title || entry.key}`}
                                {disabled}
                                onchange={(e) => onToggle(entry.key, e.currentTarget.checked)}
                            />
                        </td>
                    </tr>
                {:else}
                    <tr><td class="empty" colspan={colCount}>
                        {excludedOnly ? 'No excluded files.' : 'No files found in the source.'}
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
    tr.excluded .title a, tr.excluded .dur, tr.excluded .chs { color: var(--text-faint); }
    tr.excluded .title a { text-decoration: line-through; }

    .num { width: 36px; text-align: right; font-family: var(--font-mono); color: var(--text-faint); font-variant-numeric: tabular-nums; }
    .dur { width: 64px; text-align: right; font-family: var(--font-mono); color: var(--text-muted); font-variant-numeric: tabular-nums; }
    .chs { width: 88px; font-family: var(--font-mono); color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .inc { width: 64px; text-align: center; }
    .inc input { accent-color: var(--accent); cursor: pointer; }
    .inc input:disabled { cursor: not-allowed; opacity: 0.6; }
    .title a { display: block; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-decoration: none; }
    .title a:hover { color: var(--accent); }

    .empty { padding: var(--s-4); text-align: center; color: var(--text-faint); font-style: italic; }
</style>
