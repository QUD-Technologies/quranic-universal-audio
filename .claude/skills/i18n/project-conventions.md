# i18n Engineering Conventions (Inspector Frontend)

Authoritative engineering contract for internationalizing the Inspector SPA with **Paraglide JS 2.x** (inlang message-format). Every extraction/wiring agent follows this exactly so the work is mechanical and collision-free. English (`en`) is the source locale; Arabic (`ar`) is the first target. This doc covers **string** conventions; the Arabic linguistic spec is `arabic-conventions.md`; the library/install specifics are `paraglide.md`.

> **State of the repo:** Paraglide/inlang is **already installed and wired** (`@inlang/paraglide-js@2.20.1`, `@inlang/plugin-message-format@4.4.0`). You **add keys + wire components** — you do not re-scaffold `project.inlang/`, the message dirs, or the Vite plugin. The Svelte tree is **mixed**: ~62 runes files and ~55 legacy Svelte-4 files. Both modes are wired here.

---

## 1. Key naming — flat, area-prefixed, snake_case

Paraglide compiles **every** message into one flat module namespace: each key becomes `m.<key>()`. There is **no nesting** — `dashboard.search.placeholder` and a key named `dashboard` both flatten, and the loader resolves duplicate ids **last-wins, silently**. The area prefix is the *only* thing preventing a `title` in dashboard from clobbering a `title` in segments.

### Rules

1. **Message id casing is `snake_case`.** Lowercase, underscore-separated, ASCII. No camelCase, no dots, no dashes in ids.
2. **Every id starts with an area prefix.** One of:
   `common_` · `errors_` · `dashboard_` · `ts_` · `segments_`
   (`ts_` = Timestamps tab, `segments_` = Segments tab. Exact tokens — they map 1:1 to the file layout in §2 and the commit scopes `board`/`ts`/`segs`/`global`.)
3. **Pattern: `area_component_purpose`.** Read left→right as *where it lives → which UI element → what it is*. Add granularity segments as needed; keep it descriptive, not numbered.
4. **`common_` is for genuinely cross-tab chrome only** — Save / Cancel / Close / Loading / generic toasts / the `common_signin_*` slice. If a string lives in exactly one tab, prefix it with that tab, never `common_`.
5. **No bare/ambiguous ids.** `m.save` is illegal; `m.common_action_save` is correct.

### Do / Don't

```jsonc
// DO
"common_action_save":                   "Save",
"common_action_cancel":                 "Cancel",
"dashboard_catalog_search_placeholder": "Search reciters…",
"dashboard_admin_users_promote_button": "Promote to owner",
"ts_waveform_play_aria_label":          "Play recitation",
"segments_validation_failed_title":     "Failed Alignments",
"segments_footer_mark_ready_button":    "Mark ready for publish",
"errors_auth_required":                 "Please sign in to continue.",

// DON'T
"Save":             "Save",   // not snake_case, no prefix
"saveButton":       "Save",   // camelCase
"dashboard.search": "…",      // dots — Paraglide flattens; collides
"title":            "…",      // bare id — WILL be clobbered last-wins
"search_placeholder":"…",     // no area prefix — ambiguous + collision-prone
"common_dashboard_search":"…" // double prefix; pick ONE area
```

**Collision check:** ids are globally unique across *all* message files. Before adding `area_x_y`, grep the whole `src/**/messages/` tree for that id. Two files defining the same id is the silent last-wins bug this convention exists to prevent.

---

## 2. File layout — split by area, co-located, hand-authored

Messages are **not** one monolithic `en.json`. They split by tab/area and live next to the code they serve. The message-format plugin reads a `pathPattern` **array** and merges all matching files into the flat namespace at compile time.

```
src/lib/i18n/messages/common/{en,ar}.json   ← common_*  (cross-tab chrome only)
src/lib/i18n/messages/errors/{en,ar}.json    ← errors_*  (friendly.ts code copy)
src/tabs/dashboard/messages/{en,ar}.json      ← dashboard_*
src/tabs/timestamps/messages/{en,ar}.json     ← ts_*
src/tabs/segments/messages/{en,ar}.json       ← segments_*
```

`project.inlang/settings.json` wires them via the array form (already present — `common` is last; see `paraglide.md`):

```jsonc
"plugin.inlang.messageFormat": {
  "pathPattern": [
    "./src/tabs/dashboard/messages/{locale}.json",
    "./src/tabs/timestamps/messages/{locale}.json",
    "./src/tabs/segments/messages/{locale}.json",
    "./src/lib/i18n/messages/errors/{locale}.json",
    "./src/lib/i18n/messages/common/{locale}.json"
  ]
}
```

**Invariant — every file in the array must exist for every locale.** A missing `ar.json` for one area silently drops those keys. Each new area adds **two** files (en + ar) and **one** `pathPattern` line. JSON shape is plain `{ "$schema": "…", "id": "text" }`; keep en and ar **key-for-key identical** (same id set, same order — author the en key first, then the ar key in the same position).

### 🚨 HAND-AUTHOR THE JSON. NEVER ROUND-TRIP THROUGH THE INLANG EDITOR / SHERLOCK.

This is the loudest rule in this document.

- The inlang web editor and the **Sherlock VS Code extension** do not understand the split `pathPattern` array on **write-back**. On any save, they **collapse all messages into the *last* file in the array** and blank the others.
- That silently destroys the entire per-area split, produces a colossal unreviewable diff, and (because ids stay valid) **passes `npm run build`** — the regression ships.
- Therefore: **edit the `{en,ar}.json` files by hand (Write/Edit tools). Do not open the inlang editor. Do not install/trigger Sherlock to write. Do not let any tool "extract message" into these files.**

---

## 3. What to translate vs what NOT to translate

### Translate (UI chrome — everything the app authored)

- Visible text nodes in buttons, labels, headings, tab names, menu items, empty-states.
- Accessibility + presentation attributes: `aria-label`, `title`, `placeholder`, `alt`.
- Validation accordion **card titles and descriptions** (the human-facing copy — see §7 for the registry caveat).
- Error copy (`friendly.ts`), toasts, confirmation modals, sign-in prompts.
- Keyboard-shortcut **labels/descriptions** (not the key bindings themselves).
- Enum-derived **pills/badges** (state labels, severity labels, role names) — the *display* strings, never the underlying enum value sent to the backend.

### Do NOT translate (data — content the API owns)

- **Reciter names.** Catalog data; rendered as-is.
- **Surah / chapter names.** The API returns both `name_ar` and `name_en` — **pick the field by locale, never translate client-side.** There is no `m.*` key for a surah name.
- **Quran text, tokens, ayah glyphs, waveform shard letters.** Sacred content — rendered verbatim from data, never keyed.

### The domain-word nuance

Words like **ayah, surah, riwayah, juz, mushaf** are *translatable chrome* when used as **UI labels** ("Surah", "Ayah 5", "Riwayah:") even though the entity they point at is Arabic data. Key the label; leave the data value alone.

```svelte
<!-- "Ayah" is a chrome label → keyed. The number is data → interpolated, not keyed. -->
<span>{m.ts_label_ayah()} {ayahNumber}</span>

<!-- Surah NAME is data → choose API field by locale, NOT a message key -->
<h2>{i18n.locale === 'ar' ? chapter.name_ar : chapter.name_en}</h2>
```

(Full Arabic glossary + do-not-translate list: `arabic-conventions.md` §5–6.)

---

## 4. Wiring pattern — per file mode

The repo has both runes and legacy files. **A file is fully one mode or the other — never mix.** (Existing invariant; i18n does not relax it.)

Both wirings rest on one fact: **Paraglide message functions read the *ambient* locale** set by `setLocale()`. The only per-mode question is *how the component re-renders when the ambient locale changes.* The single switch entry point is **`switchLocale(next)`** in `src/lib/i18n/locale-store.ts` — it updates Paraglide's ambient locale (`setLocale(next, { reload: false })`), the legacy `localeStore`, the rune cell, and `<html dir/lang>` together, so runes and legacy files stay in lockstep.

### (a) Svelte 5 runes files — call `m.*()` directly

Import the message functions and call them inline. Reactivity comes from reading the **`i18n` rune** (`src/lib/i18n/locale.svelte.ts`), whose `locale` getter is a `$state`-backed cell that re-runs the template when the ambient locale flips.

```svelte
<script lang="ts">
  import * as m from '$lib/paraglide/messages';
  import { i18n } from '$lib/i18n/locale.svelte';

  let { count }: { count: number } = $props();

  // Reading i18n.locale makes this $derived re-run on switch.
  const heading = $derived((i18n.locale, m.dashboard_catalog_heading()));
</script>

<h1>{heading}</h1>
<input placeholder={m.dashboard_catalog_search_placeholder()} />
<button>{m.common_action_save()}</button>
```

Calling `m.x()` directly in markup re-renders correctly as long as **something** in the component reads `i18n.locale` (a top-level read is enough). When in doubt, gate a `$derived` on it as shown.

### (b) Legacy Svelte-4 files (e.g. `App.svelte`) — store-subscription idiom

You **cannot** introduce runes into a legacy file (mixing is forbidden). Legacy files already re-render off **store subscriptions** (`App.svelte` does `$: activeTab = $activeTabStore`). Use the **identical pattern** for locale: subscribe to **`localeStore`** and reference `$localeStore` in a reactive statement so Svelte-4 reactivity re-evaluates the message calls.

```svelte
<script lang="ts">
  import * as m from '$lib/paraglide/messages';
  import { localeStore } from '$lib/i18n/locale-store';

  // Referencing $localeStore in a reactive block re-runs these on switch.
  $: lang = $localeStore;
  $: saveLabel = (lang, m.common_action_save());
  $: searchPh  = (lang, m.dashboard_catalog_search_placeholder());
</script>

<button on:click={onSave}>{saveLabel}</button>
<input placeholder={searchPh} />
```

The `(lang, m.x())` comma-sequence is the load-bearing trick: it makes the reactive statement *depend* on `$localeStore` while still evaluating to the message string. The repo also ships a **`tr<T>(dep, value)`** helper in `locale-store.ts` for exactly this — `$: saveLabel = tr($localeStore, m.common_action_save())` is equivalent and more legible.

### The rule when a legacy file can't react cleanly

If a legacy file is too tangled to thread the store dependency through every message call, you have **exactly two** sanctioned options — **never** a third:

1. **Migrate the file to runes first**, then use pattern (a); or
2. **Keep it legacy and use the `$localeStore` / `tr()` idiom** above for every message read.

**Never** introduce a single rune into a legacy file, and **never** leave message calls in a legacy file that don't transitively depend on `$localeStore` (they'll show stale copy after a switch). The exempt imperative/canvas components (`TimestampsWaveform.svelte` et al. — see the migration plan) render no localizable text nodes; if one ever needs a label, pass it in as a prop computed by a reactive parent rather than calling `m.*()` inside the canvas component.

---

## 5. Interpolation & plurals — params + ICU, no English-isms

### Parameters

Pass data through Paraglide **message params**, never string concatenation that splices chrome and data:

```jsonc
// en.json
"dashboard_catalog_result_count": "Showing {shown} of {total} reciters"
```
```svelte
{m.dashboard_catalog_result_count({ shown, total })}
```

### Plurals — ICU variants with **all six Arabic categories**

English has 2 plural forms; **Arabic has 6** (`zero`, `one`, `two`, `few`, `many`, `other`). Use the message-format **match/variant** syntax so each locale supplies its own complete set. A missing category falls through to `other` and reads wrong in Arabic — supply all six in `ar.json`. (Arabic category boundaries are in `arabic-conventions.md` §10.)

> 🚨 **The plural value is an ARRAY containing one variant object** — the exact shape below, verified against `@inlang/plugin-message-format@4.4.0`. Do **not** use the flat `{ "match": { "count": "plural" }, "count=one": … }` form: the plugin does **not** recognize it as a variant, silently flattens each `count=…` sub-key into a *separate* message (`…_count_one`, `…_match_count`), and the wired `m.key()` function is **never generated** — `paraglide:compile` succeeds, so the breakage only surfaces as a downstream type/resolve error on the call site. Every plural uses `declarations` (`input <var>` for each placeholder + `local countPlural = count: plural`), `selectors`, and a `match` keyed by `countPlural=<category>`.

```jsonc
// en.json — English categories: one, other
"segments_validation_issue_count": [
  {
    "declarations": ["input count", "local countPlural = count: plural"],
    "selectors": ["countPlural"],
    "match": {
      "countPlural=one":   "{count} issue",
      "countPlural=other": "{count} issues"
    }
  }
]

// ar.json — all 6 Arabic categories: zero, one, two, few, many, other
"segments_validation_issue_count": [
  {
    "declarations": ["input count", "local countPlural = count: plural"],
    "selectors": ["countPlural"],
    "match": {
      "countPlural=zero": "لا توجد مشاكل",
      "countPlural=one":  "مشكلة واحدة",
      "countPlural=two":  "مشكلتان",
      "countPlural=few":  "{count} مشاكل",
      "countPlural=many": "{count} مشكلة",
      "countPlural=other":"{count} مشكلة"
    }
  }
]
```
For a message with extra interpolation alongside the count, declare each one: `"declarations": ["input count", "input ranges", "local countPlural = count: plural"]` and reference `{ranges}` inside the variant strings.
```svelte
<span>{m.segments_validation_issue_count({ count })}</span>
```

### FORBIDDEN

```svelte
<!-- English ternary pluralization — untranslatable, wrong in Arabic -->
{count} issue{count === 1 ? '' : 's'}

<!-- concatenating chrome + data — word order can't flip for RTL/other languages -->
{'Showing ' + shown + ' of ' + total + ' reciters'}

<!-- building a sentence from fragments -->
{m.segments_prefix() + ' ' + name + ' ' + m.segments_suffix()}
```

---

## 6. Attributes

`aria-label`, `title`, `placeholder`, `alt` take the message **function call** directly — they're string-valued attributes:

```svelte
<button aria-label={m.ts_waveform_play_aria_label()}>▶</button>
<input  placeholder={m.dashboard_catalog_search_placeholder()} />
<img    alt={m.dashboard_logo_alt()} src={logo} />
<span   title={m.segments_footer_mark_ready_hint()}>…</span>
```

In **legacy** files these still need the `$localeStore` dependency to update on switch — bind them to a reactive `$:` value (`$: playLabel = tr($localeStore, m.ts_waveform_play_aria_label())`) rather than calling `m.*()` raw in the attribute, or they go stale after a locale switch.

---

## 7. Seams first — where the strings already live

These are the high-leverage extraction points. Convert the **copy** to keys; keep the **structure** (the maps, the code keys) intact.

| Seam | File | Convention |
|---|---|---|
| **Error UX** (all FE-owned; backend returns codes only) | `src/lib/errors/friendly.ts` | Each `CODE_COPY[CODE]` + `STATUS_FALLBACK[status]` entry → an `errors_*` key. Keep the `code`→key mapping; replace the string/function value with `m.errors_<code_lower>()`. Functions that interpolate `ctx` (e.g. `NOT_EDITABLE_STATE`, `REASON_REQUIRED`) become **param messages**: `m.errors_reason_required({ min_chars })` with an ICU variant for the "no count" branch. `body.error` stays raw (telemetry only — never keyed). |
| **Sign-in prompts** | `src/lib/sign-in-messages.ts` | Each `SIGN_IN_MESSAGES.<k>.{title,body}` → `common_signin_<k>_title` / `common_signin_<k>_body`. Keep the object keys (`edit`/`claim`/`request`/`save`/`claimExpired`) as the lookup contract; swap the literal strings for **per-message deep-imported** getters (`$lib/paraglide/messages/common_signin_<k>_title`) so the `satisfies Record<...>` shape and all 5 call sites stay untouched. This seam is already done as the bootstrap proof — mirror its pattern. |
| **`*_LABELS` enum→display maps** | state pills, role labels, job-status, history-op labels (`StatePill.svelte`, `PickerStateTabs.svelte`, `StateTimeline.svelte`, `Jobs*`, segments `history/*`, `shortcuts/store.svelte.ts`, etc.) | Map the **enum value** (the data) to a **message function**, not a literal: `const STATE_LABELS = { published: m.common_state_published, … }` then call `STATE_LABELS[state]()`. The enum value stays the wire contract; only the display string is keyed. |
| **Segments issue registry** | `src/tabs/segments/domain/registry.ts` | ⚠️ **See the hard warning below — do NOT localize in place.** |

### ⚠️ Segments `registry.ts` — do NOT localize in place

`registry.ts` `displayTitle` (and `description`) are mirrored in the Python registry (`inspector/services/validation/registry.py`) under a **parity CI guard** (`__tests__/registry/parity.test.ts`). The test asserts the **literal English string**:

```ts
// parity.test.ts pins displayTitle by exact value:
expect((row as IssueDefinition)[key]).toBe(value); // value = 'Failed Alignments', etc.
```

**If you replace `displayTitle: 'Failed Alignments'` with a `m.*()` call or any non-literal, the parity test fails and CI goes red.** `description` is intentionally *not* pinned (known TS↔Py drift), but `displayTitle` **is**.

**Rule:** leave `registry.ts` `displayTitle`/`description` **exactly as-is** (they remain the parity anchor and the English source). Add a **separate** locale lookup keyed by the registry `kind`, consumed at the render site:

```ts
// src/tabs/segments/i18n/validation-labels.ts  (NEW — does not touch registry.ts)
import * as m from '$lib/paraglide/messages';
export const VALIDATION_TITLE = {
  failed:         m.segments_validation_failed_title,
  missing_verses: m.segments_validation_missing_verses_title,
  // …one per IssueRegistry kind
} as const;
export const VALIDATION_DESC = { /* same keying, for descriptions */ } as const;
```
```svelte
<!-- render site: was {def.displayTitle} -->
<h3>{VALIDATION_TITLE[def.kind]()}</h3>
```

The English `m.segments_validation_*_title` values must match the registry `displayTitle` strings **verbatim**, so en chrome is unchanged and the parity snapshot is untouched. `ERROR_CAT_LABELS` (derived from `displayTitle`) — repoint UI consumers to `VALIDATION_TITLE`; don't re-key the registry-derived map. **Verify before and after:** run the FE test suite (or `registry/parity` specifically) and confirm it's green both times.

---

## 8. Guide prose — defer (translate-as-documents)

The Segments **accordion guides** (~2,900 LOC of authored prose) and the **mark-ready `.md`** content are **documents, not atomic UI keys.** Do **not** shatter them into hundreds of `m.*` keys. They are a **separate workstream**, translated as whole documents (parallel `*.ar.md` / `*.ar.ts` guide modules selected by locale), and are **out of scope** for the chrome-extraction pass. Note their existence, leave them in English, and **defer.** A wiring agent that hits a guide module skips it and moves on.

---

## 9. Verification — every agent runs this, every change

Run after each batch of extraction/wiring. The bar is **green and key-synced.**

```bash
cd inspector/frontend
npm run check     # paraglide:compile + svelte-check — type + template safety
npm run build     # paraglide:compile + tsc + Vite — must stay green
```

- **`npm run check`** runs `paraglide:compile` then svelte-check; it catches bad `m.*` calls, missing imports, and attribute typing. Must pass with no new errors. The `.d.ts` companions (emitted by the compiler) give real types for every `$lib/paraglide/*` import — if you see "Could not find a declaration file", the generated dir is stale; the compile step fixes it.
- **`npm run build`** runs the full Paraglide compile + Vite. A **missing key in any locale** (id in `en.json` absent from `ar.json`, or vice versa) surfaces here — treat any missing-key warning as a failure and fix the imbalance.
- **en ↔ ar key parity** — the two files for an area must have the **identical id set**. Quick check (Git Bash):
  ```bash
  diff <(jq -r 'keys[]' src/tabs/dashboard/messages/en.json | sort) \
       <(jq -r 'keys[]' src/tabs/dashboard/messages/ar.json | sort)
  ```
  Empty diff = synced. (If `jq` is unavailable, eyeball — files are hand-authored and ordered identically by rule.)
- **Segments parity** — if you touched anything near `registry.ts`, run the FE test suite and confirm `registry/parity` stays green.
- **End-to-end smoke** — toggle the `LocaleSwitcher` (or set `localStorage['insp_locale']='ar'`) and confirm the strings you wired actually flip and don't visually mangle the LTR layout (bidi-isolation per `arabic-conventions.md` §8).

### Do NOT run a full lint pass

**Do not run `npm run lint`.** Linting is gated by the **pre-commit hook** (lint-staged runs `eslint --fix` on staged FE files and re-stages; `src/lib/paraglide/**` is excluded). A manual full `npm run lint` in a fresh worktree fails with a **native-binding error** until `node_modules` is reinstalled, and burns time for no signal. Let the hook handle eslint on commit; only invoke lint manually to diagnose a *specific* failure it surfaced.

---

## Quick reference card

- **id** = `area_component_purpose`, `snake_case`, globally unique, area-prefixed (`common_`/`errors_`/`dashboard_`/`ts_`/`segments_`).
- **files** = split by area, co-located, en+ar per area, wired via `pathPattern` array. **Hand-author JSON — never the inlang editor/Sherlock** (collapses splits to the last file silently).
- **imports** = `import * as m from '$lib/paraglide/messages'` (barrel); deep-import a single message only for a structural seam.
- **translate** chrome (text, `aria-label`/`title`/`placeholder`/`alt`, validation titles+descriptions, errors, toasts, shortcut labels, enum pills, domain *labels* like "Ayah"). **Never** data (reciter/surah names — use API `name_ar`/`name_en` — Quran text/tokens).
- **runes** → call `m.x()`, reactivity via reading `i18n.locale` (`$lib/i18n/locale.svelte`).
- **legacy** → same `m.x()` but gate on `$localeStore` in a `$:` block (or `tr($localeStore, m.x())`); never add runes; migrate-or-store, never mix.
- **switch** → one entry point `switchLocale()` in `$lib/i18n/locale-store` (updates ambient + store + rune + `<html dir/lang>`).
- **plurals** → ICU variants, **all 6 Arabic categories**; forbid `n===1?'':'s'` and chrome+data concatenation.
- **seams** → `friendly.ts`→`errors_*`, `sign-in-messages.ts`→`common_signin_*` (deep-imported getters), `*_LABELS` maps (key the value, not the enum). **`registry.ts` displayTitle is parity-pinned — localize at the render site via a `kind`-keyed lookup, never edit the registry literals.**
- **guides** = documents, deferred separate workstream.
- **verify** → `npm run check` + `npm run build` green, en/ar keys synced; **no full `npm run lint`**.
