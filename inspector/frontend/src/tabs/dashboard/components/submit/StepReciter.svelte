<script lang="ts">
    /**
     * Wizard step 1 — identify the reciter.
     *
     * Tri-state toggle:
     *   • existing_combo   — reciter + already-covered (riwayah, style).
     *                        Routes to the per-delivery RequestForm flow.
     *   • existing_reciter — reciter exists; this is a new combination.
     *   • new              — reciter not in catalog.
     *
     * Picker uses an inline always-rendered list (not a popup) — simpler,
     * sidesteps focus/blur timing issues, and the picked combination row
     * pattern fits naturally below.
     *
     * Country field copies the `RequestForm.svelte` pattern verbatim
     * (datalist + focus-stash dance + ISO-2 resolution) so the UX matches.
     * TODO: extract to a shared CountryField component.
     */
    import { get, writable } from 'svelte/store';
    import { fade, fly } from 'svelte/transition';

    import { localeStore, tr } from '$lib/i18n/locale-store';
    import { vocabLabel } from '$lib/i18n/vocab';
    import * as m from '$lib/paraglide/messages';
    import CountryPicker from '../../../../lib/components/CountryPicker.svelte';
    import { countryByName } from '../../../../lib/utils/countries';
    import { channelDisplay } from '../../../../lib/utils/delivery-label';
    import { filterByFields, match } from '../../../../lib/utils/fuzzy-match';
    import { catalogData } from '../../stores/catalog-data';
    import { openDetail } from '../../stores/dashboard-state';
    import { closeSubmitWizard, submitWizard } from '../../stores/submit-wizard';

    const queryStore = writable('');

    $: lang = $localeStore;
    $: changeLabel = tr(lang, m.common_action_change());
    $: modeAria = tr(lang, m.dashboard_submit_reciter_mode_aria());
    $: modeExistingComboLabel = tr(lang, m.dashboard_submit_mode_existing_combo_label());
    $: modeExistingComboHint = tr(lang, m.dashboard_submit_mode_existing_combo_hint());
    $: modeExistingReciterLabel = tr(lang, m.dashboard_submit_mode_existing_reciter_label());
    $: modeNewLabel = tr(lang, m.dashboard_submit_mode_new_label());
    $: modeNewHint = tr(lang, m.dashboard_submit_mode_new_hint());
    $: openDetailTitle = tr(lang, m.dashboard_submit_open_detail_title());
    $: viewDetailButton = tr(lang, m.dashboard_submit_view_detail_button());
    $: pickComboLabel = tr(lang, m.dashboard_submit_pick_combo_label());
    $: combosEmpty = tr(lang, m.dashboard_submit_combos_empty());
    $: alreadyCoveredLabel = tr(lang, m.dashboard_submit_already_covered_label());
    $: reciterFieldLabel = tr(lang, m.dashboard_submit_reciter_field_label());
    $: reciterSearchPlaceholder = tr(lang, m.dashboard_submit_reciter_search_placeholder());
    $: reciterResultsAria = tr(lang, m.dashboard_submit_reciter_results_aria());
    $: reciterNoMatch = tr(lang, m.dashboard_submit_reciter_no_match());
    $: newDupHint = tr(lang, m.dashboard_submit_new_dup_hint());
    $: fieldEnglishName = tr(lang, m.dashboard_submit_field_english_name());
    $: newNameEnPlaceholder = tr(lang, m.dashboard_submit_new_name_en_placeholder());
    $: fieldArabicName = tr(lang, m.dashboard_submit_field_arabic_name());
    $: newNameArPlaceholder = tr(lang, m.dashboard_submit_new_name_ar_placeholder());
    $: fieldCountry = tr(lang, m.dashboard_submit_field_country());
    $: countryUnknown = tr(lang, m.dashboard_submit_country_unknown());
    $: countryPlaceholder = tr(lang, m.dashboard_submit_country_placeholder());
    $: dupMatchesLabel = tr(lang, m.dashboard_submit_dup_matches_label());
    $: dupUseThis = tr(lang, m.dashboard_submit_dup_use_this());

    $: state = $submitWizard;
    $: mode = state.reciterMode;
    $: reciters = $catalogData.reciters;
    $: pickedReciter = state.existingReciterSlug
        ? (reciters.find((r) => r.reciter_id === state.existingReciterSlug) ?? null)
        : null;

    // Every match is rendered (a plain button list in a 240 px scroll box — cheap
    // at catalog scale), sorted alphabetically: the catalog API returns reciters
    // in activity order, so an unsorted list would bury the un-aligned ones.
    // Only the first rows get the entry stagger (see STAGGER_ROWS) so a long list
    // does not trickle in for seconds.
    const STAGGER_ROWS = 20;
    function computeMatched(rs: typeof reciters, q: string): typeof reciters {
        const matched = filterByFields(rs, q, (r) => [r.name, r.name_ar]);
        matched.sort((a, b) => a.name.localeCompare(b.name));
        return matched;
    }
    $: filtered = computeMatched(reciters, $queryStore);

    function setMode(next: typeof mode): void {
        submitWizard.update((s) => ({
            ...s,
            reciterMode: next,
            // Clear cross-mode state to avoid stale picks bleeding across.
            existingComboSlug: null,
        }));
    }

    function pickReciter(reciter_id: string): void {
        submitWizard.update((s) => ({
            ...s,
            existingReciterSlug: reciter_id,
            existingComboSlug: null,
        }));
        queryStore.set('');
    }

    function clearReciter(): void {
        submitWizard.update((s) => ({
            ...s,
            existingReciterSlug: null,
            existingComboSlug: null,
        }));
    }

    function pickCombo(slug: string): void {
        submitWizard.update((s) => ({ ...s, existingComboSlug: slug }));
    }

    function openPickedReciterDetail(): void {
        if (!pickedReciter) return;
        closeSubmitWizard();
        openDetail(pickedReciter.reciter_id);
    }

    // ---- new-reciter fields ----
    function updateNew(field: 'name_en' | 'name_ar' | 'countryName', value: string): void {
        submitWizard.update((s) => ({
            ...s,
            newReciter: { ...s.newReciter, [field]: value },
        }));
    }

    // Live duplicate check for new reciters: fuzzy-match the typed name(s)
    // against the catalogue so near-spellings surface before submission.
    $: newNameEn = state.newReciter.name_en.trim();
    $: newNameAr = state.newReciter.name_ar.trim();
    $: dupCandidates =
        mode === 'new' && (newNameEn.length >= 2 || newNameAr.length >= 2)
            ? reciters
                  .filter(
                      (r) =>
                          (newNameEn.length >= 2 && match(r.name, newNameEn)) ||
                          (newNameAr.length >= 2 && !!r.name_ar && match(r.name_ar, newNameAr)),
                  )
                  .slice(0, 4)
            : [];

    // Country picker: CountryPicker two-way binds `countryInput`; push changes back
    // into the wizard store (guarded so a redundant tick doesn't churn the store).
    // Seed from the store synchronously — the reactive `state` is still undefined
    // during this top-level init, so reading `state.newReciter` here would throw.
    let countryInput = get(submitWizard).newReciter.countryName;
    $: if (countryInput !== state.newReciter.countryName) updateNew('countryName', countryInput);
    $: countryCode = countryByName(countryInput, lang)?.code ?? '';
</script>

<div class="step" in:fade={{ duration: 180 }}>
    <div class="mode-toggle" role="tablist" aria-label={modeAria}>
        <button
            type="button"
            class="mode-btn"
            class:active={mode === 'existing_combo'}
            role="tab"
            aria-selected={mode === 'existing_combo'}
            on:click={() => setMode('existing_combo')}
        >
            <span class="mode-label">{modeExistingComboLabel}</span>
            <span class="mode-hint">{modeExistingComboHint}</span>
        </button>
        <button
            type="button"
            class="mode-btn"
            class:active={mode === 'existing_reciter'}
            role="tab"
            aria-selected={mode === 'existing_reciter'}
            on:click={() => setMode('existing_reciter')}
        >
            <span class="mode-label">{modeExistingReciterLabel}</span>
            <span class="mode-hint"
                >{tr(lang, m.dashboard_submit_mode_existing_reciter_hint({
                    count: reciters.length,
                }))}</span>
        </button>
        <button
            type="button"
            class="mode-btn"
            class:active={mode === 'new'}
            role="tab"
            aria-selected={mode === 'new'}
            on:click={() => setMode('new')}
        >
            <span class="mode-label">{modeNewLabel}</span>
            <span class="mode-hint">{modeNewHint}</span>
        </button>
        <span class="mode-track" data-mode={mode} aria-hidden="true"></span>
    </div>

    {#if mode === 'existing_combo' || mode === 'existing_reciter'}
        <div class="pane" in:fly={{ y: 4, duration: 180 }}>
            {#if pickedReciter}
                <div class="picked">
                    <div class="picked-row">
                        <div class="picked-names">
                            <span class="picked-en">{pickedReciter.name}</span>
                            {#if pickedReciter.name_ar}
                                <span class="picked-ar" dir="rtl">{pickedReciter.name_ar}</span>
                            {/if}
                        </div>
                        <div class="picked-actions">
                            <button
                                type="button"
                                class="picked-action"
                                title={openDetailTitle}
                                on:click={openPickedReciterDetail}
                            >{viewDetailButton} <span class="picked-action-glyph" aria-hidden="true">↗</span></button>
                            <button
                                type="button"
                                class="picked-action"
                                on:click={clearReciter}
                            >{changeLabel}</button>
                        </div>
                    </div>
                </div>

                {#if mode === 'existing_combo'}
                    <div class="combos-block" in:fade={{ duration: 200 }}>
                        <span class="combos-label">{pickComboLabel}</span>
                        <ul class="combo-list">
                            {#each pickedReciter.deliveries as d, i (d.slug)}
                                <li>
                                    <button
                                        type="button"
                                        class="combo-row"
                                        class:selected={d.slug === state.existingComboSlug}
                                        on:click={() => pickCombo(d.slug)}
                                        style:--row={i}
                                    >
                                        <span class="combo-tags">
                                            <span class="ct">{vocabLabel('riwayah', d.riwayah)}</span>
                                            <span class="ct dim">·</span>
                                            <span class="ct">{vocabLabel('style', d.style)}</span>
                                            {#if d.recording_context}
                                                <span class="ct dim">·</span>
                                                <span class="ct dim"
                                                    >{vocabLabel('context', d.recording_context)}</span
                                                >
                                            {/if}
                                            {#if d.channel}
                                                <span class="ct dim">·</span>
                                                <span class="ct dim">{channelDisplay(d)}</span>
                                            {/if}
                                        </span>
                                        <span class="combo-meta">
                                            {tr(lang, m.dashboard_submit_combo_coverage({
                                                count: d.chapter_count,
                                            }))}
                                        </span>
                                    </button>
                                </li>
                            {/each}
                            {#if pickedReciter.deliveries.length === 0}
                                <li class="combo-empty">{combosEmpty}</li>
                            {/if}
                        </ul>
                    </div>
                {:else if pickedReciter.deliveries.length > 0}
                    <div class="combos-block muted" in:fade={{ duration: 200 }}>
                        <span class="combos-label">{alreadyCoveredLabel}</span>
                        <div class="combo-pills">
                            {#each pickedReciter.deliveries as d (d.slug)}
                                <span class="combo-pill">
                                    {[
                                        vocabLabel('riwayah', d.riwayah),
                                        vocabLabel('style', d.style),
                                        d.recording_context ? vocabLabel('context', d.recording_context) : null,
                                        d.channel ? channelDisplay(d) : null,
                                    ]
                                        .filter(Boolean)
                                        .join(' · ')}
                                </span>
                            {/each}
                        </div>
                    </div>
                {/if}
            {:else}
                <label class="picker">
                    <span class="picker-label">{reciterFieldLabel}</span>
                    <input
                        type="text"
                        autocomplete="off"
                        placeholder={reciterSearchPlaceholder}
                        bind:value={$queryStore}
                    />
                </label>

                <ul class="results" role="listbox" aria-label={reciterResultsAria}>
                    {#if filtered.length === 0}
                        <li class="results-empty">{reciterNoMatch}</li>
                    {:else}
                        {#each filtered as r, i (r.reciter_id)}
                            <li>
                                <button
                                    type="button"
                                    class="result"
                                    on:click={() => pickReciter(r.reciter_id)}
                                    style:--row={Math.min(i, STAGGER_ROWS)}
                                >
                                    <span class="r-name">{r.name}</span>
                                    {#if r.name_ar}
                                        <span class="r-name-ar" dir="rtl">{r.name_ar}</span>
                                    {/if}
                                    <span class="r-count">
                                        {r.deliveries.length}
                                        <span class="r-count-unit"
                                            >{tr(lang, m.dashboard_submit_reciter_combos_unit({
                                                count: r.deliveries.length,
                                            }))}</span>
                                    </span>
                                </button>
                            </li>
                        {/each}
                    {/if}
                </ul>
            {/if}
        </div>
    {:else}
        <div class="pane new" in:fly={{ y: 4, duration: 180 }}>
            <p class="dup-hint">{newDupHint}</p>
            <label>
                <span>{fieldEnglishName}</span>
                <input
                    type="text"
                    placeholder={newNameEnPlaceholder}
                    value={state.newReciter.name_en}
                    on:input={(e) =>
                        updateNew('name_en', (e.currentTarget as HTMLInputElement).value)}
                />
            </label>
            <label class="rtl">
                <span>{fieldArabicName}</span>
                <input
                    type="text"
                    dir="rtl"
                    placeholder={newNameArPlaceholder}
                    value={state.newReciter.name_ar}
                    on:input={(e) =>
                        updateNew('name_ar', (e.currentTarget as HTMLInputElement).value)}
                />
            </label>
            <label class="country-field">
                <span>
                    {fieldCountry}
                    {#if countryCode}
                        <span class="label-meta">({countryCode})</span>
                    {:else if countryInput}
                        <span class="label-meta warn">{countryUnknown}</span>
                    {/if}
                </span>
                <CountryPicker
                    bind:value={countryInput}
                    locale={lang}
                    placeholder={countryPlaceholder}
                />
            </label>

            {#if dupCandidates.length > 0}
                <div class="dup-matches" transition:fade={{ duration: 160 }}>
                    <span class="dup-matches-label">{dupMatchesLabel}</span>
                    <ul>
                        {#each dupCandidates as r (r.reciter_id)}
                            <li>
                                <button type="button" class="dup-row" on:click={() => { setMode('existing_reciter'); pickReciter(r.reciter_id); }}>
                                    <span class="dup-name">{r.name}</span>
                                    {#if r.name_ar}<span class="dup-ar" dir="rtl">{r.name_ar}</span>{/if}
                                    <span class="dup-use">{dupUseThis}</span>
                                </button>
                            </li>
                        {/each}
                    </ul>
                </div>
            {/if}
        </div>
    {/if}
</div>

<style>
    .step {
        display: flex;
        flex-direction: column;
        gap: var(--s-4);
    }

    /* tri-state toggle */
    .mode-toggle {
        position: relative;
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        padding: 3px;
    }
    .mode-btn {
        position: relative;
        z-index: 1;
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 2px;
        padding: var(--s-2) var(--s-3);
        border-radius: 4px;
        color: var(--text-muted);
        text-align: start;
        transition: color var(--t-base) var(--ease-out-quart);
    }
    .mode-btn.active {
        color: var(--text-primary);
    }
    .mode-label {
        font-size: var(--fs-meta);
        font-weight: 500;
    }
    .mode-hint {
        font-size: 10.5px;
        color: var(--text-faint);
    }
    .mode-track {
        position: absolute;
        top: 3px;
        bottom: 3px;
        width: calc(100% / 3 - 3px);
        inset-inline-start: 3px;
        background: var(--panel);
        border: 1px solid var(--border-quiet);
        border-radius: 4px;
        transition: transform var(--t-base) var(--ease-out-expo);
        pointer-events: none;
    }
    .mode-track[data-mode='existing_reciter'] {
        transform: translateX(100%);
    }
    .mode-track[data-mode='new'] {
        transform: translateX(200%);
    }
    /* Under RTL the track starts at the logical (right) edge, so later modes
       advance toward the left — negate the slide. */
    :global([dir='rtl']) .mode-track[data-mode='existing_reciter'] {
        transform: translateX(-100%);
    }
    :global([dir='rtl']) .mode-track[data-mode='new'] {
        transform: translateX(-200%);
    }

    .pane {
        display: flex;
        flex-direction: column;
        gap: var(--s-3);
    }
    .pane.new {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--s-3);
    }
    .pane.new .country-field {
        grid-column: 1 / -1;
    }
    .dup-hint {
        grid-column: 1 / -1;
        margin: 0;
        font-size: 11px;
        color: var(--text-faint);
        line-height: 1.5;
    }
    .dup-matches {
        grid-column: 1 / -1;
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
        padding: var(--s-3);
        background: oklch(0.86 0.13 75 / 0.1);
        border: 1px solid oklch(0.86 0.13 75 / 0.35);
        border-radius: var(--r-2);
    }
    .dup-matches-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--state-error-fg);
    }
    .dup-matches ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
    .dup-row {
        width: 100%;
        display: flex;
        align-items: baseline;
        gap: var(--s-2);
        padding: 5px 8px;
        border-radius: var(--r-1);
        text-align: start;
        color: var(--text-secondary);
        transition: background var(--t-fast);
    }
    .dup-row:hover { background: var(--panel); }
    .dup-name { font-size: var(--fs-body); color: var(--text-primary); }
    .dup-ar { font-size: var(--fs-meta); color: var(--text-secondary); }
    .dup-use { margin-inline-start: auto; font-size: 10.5px; color: var(--accent); }

    label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: var(--fs-meta);
        color: var(--text-muted);
    }
    label.rtl input {
        text-align: start;
    }
    input {
        background: var(--panel);
        border: 1px solid var(--border-default);
        color: var(--text-primary);
        border-radius: var(--r-2);
        padding: 8px 10px;
        font: inherit;
        transition:
            border-color var(--t-fast),
            background var(--t-fast);
    }
    input::placeholder {
        color: var(--text-faint);
    }
    input:focus {
        outline: none;
        border-color: var(--accent);
        background: var(--panel-2);
    }
    .label-meta {
        margin-inline-start: 4px;
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .label-meta.warn {
        color: var(--state-error-fg);
    }

    /* picker + inline results list */
    .picker {
        display: flex;
        flex-direction: column;
        gap: 4px;
    }
    .picker-label {
        color: var(--text-muted);
        font-size: var(--fs-meta);
    }

    .results {
        list-style: none;
        margin: 0;
        padding: 4px;
        max-height: 240px;
        overflow-y: auto;
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .results-empty {
        padding: var(--s-3);
        font-size: var(--fs-meta);
        color: var(--text-faint);
        text-align: center;
    }
    .result {
        width: 100%;
        display: grid;
        grid-template-columns: 1fr auto auto;
        align-items: baseline;
        column-gap: var(--s-3);
        padding: 6px 8px;
        border-radius: var(--r-1);
        color: var(--text-secondary);
        text-align: start;
        transition:
            background var(--t-fast),
            color var(--t-fast);
        animation: row-in 220ms var(--ease-out-quart) both;
        animation-delay: calc(var(--row) * 12ms);
    }
    @keyframes row-in {
        from {
            opacity: 0;
            transform: translateY(-2px);
        }
        to {
            opacity: 1;
            transform: none;
        }
    }
    .result:hover {
        background: var(--accent-tint-soft);
        color: var(--text-primary);
    }
    .r-name {
        font-size: var(--fs-body);
        color: var(--text-primary);
    }
    .r-name-ar {
        font-size: var(--fs-body);
        color: var(--text-secondary);
    }
    .r-count {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .r-count-unit {
        color: var(--text-faint);
        margin-inline-start: 3px;
    }

    /* picked reciter chip */
    .picked {
        padding: var(--s-3);
        background: var(--accent-tint-soft);
        border: 1px solid var(--accent-tint);
        border-radius: var(--r-2);
    }
    .picked-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
    }
    .picked-names {
        display: flex;
        align-items: baseline;
        gap: var(--s-3);
        min-width: 0;
        flex-wrap: wrap;
    }
    .picked-en {
        font-size: var(--fs-body);
        font-weight: 500;
        color: var(--text-primary);
    }
    .picked-ar {
        font-size: var(--fs-body);
        color: var(--text-secondary);
    }
    .picked-actions {
        flex-shrink: 0;
        display: inline-flex;
        align-items: center;
        gap: var(--s-1);
    }
    .picked-action {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 10.5px;
        color: var(--text-muted);
        padding: 4px 10px;
        background: transparent;
        border: 1px solid var(--border-default);
        border-radius: var(--r-2);
        cursor: pointer;
        transition:
            color var(--t-fast),
            border-color var(--t-fast);
    }
    .picked-action:hover {
        color: var(--text-primary);
        border-color: var(--accent);
    }
    .picked-action-glyph {
        font-family: var(--font-mono);
        font-size: 11px;
        line-height: 1;
        color: var(--text-faint);
        transition: color var(--t-fast), transform var(--t-fast);
    }
    .picked-action:hover .picked-action-glyph {
        color: var(--accent);
        transform: translate(1px, -1px);
    }

    /* combos block */
    .combos-block {
        display: flex;
        flex-direction: column;
        gap: var(--s-2);
    }
    .combos-block.muted {
        padding: var(--s-3);
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
    }
    .combos-label {
        font-size: 10.5px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--text-muted);
    }
    .combo-list {
        list-style: none;
        margin: 0;
        padding: 4px;
        background: var(--canvas-inset);
        border: 1px solid var(--border-quiet);
        border-radius: var(--r-2);
        display: flex;
        flex-direction: column;
        gap: 2px;
        max-height: 240px;
        overflow-y: auto;
    }
    .combo-row {
        width: 100%;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--s-3);
        padding: 8px 10px;
        border: 1px solid transparent;
        border-radius: var(--r-1);
        color: var(--text-secondary);
        text-align: start;
        transition:
            background var(--t-fast),
            border-color var(--t-fast);
        animation: row-in 200ms var(--ease-out-quart) both;
        animation-delay: calc(var(--row) * 18ms);
    }
    .combo-row:hover {
        background: var(--panel);
        border-color: var(--border-quiet);
    }
    .combo-row.selected {
        background: var(--accent-tint-soft);
        border-color: var(--accent);
        color: var(--text-primary);
    }
    .combo-tags {
        display: inline-flex;
        align-items: baseline;
        gap: 6px;
        font-family: var(--font-mono);
        font-size: 12px;
    }
    .combo-tags .ct.dim {
        color: var(--text-faint);
    }
    .combo-meta {
        font-family: var(--font-mono);
        font-size: 10.5px;
        color: var(--text-faint);
        font-variant-numeric: tabular-nums;
    }
    .combo-empty {
        padding: var(--s-3);
        font-size: var(--fs-meta);
        color: var(--text-faint);
        font-style: italic;
        text-align: center;
    }
    .combo-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
    }
    .combo-pill {
        display: inline-flex;
        padding: 2px 8px;
        background: var(--panel);
        border: 1px solid var(--border-quiet);
        border-radius: 999px;
        color: var(--text-muted);
        font-size: 10.5px;
        font-family: var(--font-mono);
    }

</style>
