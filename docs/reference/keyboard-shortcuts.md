# Keyboard shortcuts (Segments tab)

User-editable, context-scoped keyboard shortcuts for the Segments editor. A pressed key resolves to an action through a user-overridable binding map; the active **context** (default / accordion / edit, plus a reference-only `wasl` pool) decides which pool of bindings is live. The footer popover (`ShortcutsGuide.svelte`, left of the speed control) is the reference + inline rebinder. This subsystem is Segments-only; the Timestamps tab has its own separate `TimestampsKeyboard` + `TimestampsShortcutsGuide`.

## Files

| Path | Role |
|---|---|
| `tabs/segments/shortcuts/defaults.ts` | Catalogue — `SHORTCUT_ACTIONS` (id · label · `context` · `defaultKey` · `rebindable`) + `SHORTCUT_SECTIONS` (popover grouping). Single source of truth for every key. |
| `tabs/segments/shortcuts/store.svelte.ts` | Binding store (runes `$state`). localStorage overrides (`insp_seg_shortcuts`), `keyFor` / `setBinding` / `resetAll`, `resolve(token, context)` reverse lookup, `tokenFromEvent`, `prettyKey`. |
| `tabs/segments/utils/keyboard.ts` | `handleSegmentsKey(e)` — the dispatcher. Picks context, resolves the action, runs it. Returns `true` when handled (caller `preventDefault`s). |
| `tabs/segments/stores/active-actions.ts` | `activeRowActions` registry — the focused/primary `SegmentRow`'s edit-action bundle, so row/card keys reach the right row. |
| `tabs/segments/utils/accordion-nav.ts` | `accordionSequence()` / `accordionStep(dir)` — DOM-read ordered (chapter,index) list of the open accordion's rows for ↑/↓ + autoplay. |
| `tabs/segments/components/footer/ShortcutsGuide.svelte` | Footer drop-up: grouped reference + click-to-rebind + Reset. Wired into `SegmentsFooter.svelte`'s `.transport-left`. |
| `SegmentsTab.svelte` | Mounts `<svelte:window on:keydown>` → `handleSegmentsKey`. |
| `lib/utils/keyboard-guard.ts` | `shouldHandleKey(e, 'segments')` — bails on editable targets / inactive tab. |

## Key token format

A token is `e.code` with an optional `Ctrl+` prefix (Ctrl OR Meta both normalise to `Ctrl+`): `KeyA`, `Space`, `ArrowUp`, `Comma`, `Ctrl+KeyS`. `prettyKey()` renders display labels (`Ctrl+KeyS` → "Ctrl S", `ArrowUp` → "↑"). Overrides are stored per action id; only `rebindable` actions can be remapped.

## Contexts

`handleSegmentsKey` resolves exactly one context per event:

| Context | When | Pools searched |
|---|---|---|
| `edit` | `editMode` is `'trim'` or `'split'` | `edit` only |
| `accordion` | `valUiOpenCategory !== null` (a validation accordion is open) | `accordion` then `default` (accordion overrides) |
| `default` | otherwise (main-list browsing) | `default` only |

The catalogue carries a fourth context, `wasl`, that the dispatcher never resolves: the cross-verse WASL/WAQF boundary owns those keys itself (see **Self-contained widgets** below). Its entries exist so the keys appear in the footer guide and can't be claimed by a rebind.

`default`-pool actions stay live inside an open accordion (they act on the focused card); `accordion`-pool actions are additive. Conflict groups for rebinding: `default`+`accordion` share one (they can be live together); `edit` and `wasl` are each separate.

## Keymap

**Default** (main list; also active inside an accordion, acting on the focused card):

| Key | Action | id |
|---|---|---|
| Space | Play / pause | `play_pause` * |
| ← / → | Seek ∓3 s | `seek_back` / `seek_fwd` * |
| ↑ / ↓ | Prev / next segment | `nav_prev` / `nav_next` * |
| , / . | Slower / faster (3× cap via `SEGMENTS_SPEEDS`) | `speed_down` / `speed_up` |
| J | Toggle auto-scroll | `autoscroll` |
| K | Toggle autoplay-next | `autoplay` |
| A | Adjust (trim) | `adjust` |
| S | Split / autosplit | `split` |
| E | Edit reference | `edit_ref` |
| H | Toggle history | `history` |
| Ctrl+S | Save | `save` * |

**Accordion open** (acts on the focused card; ↑/↓ + autoplay walk the accordion sequence incl. context rows):

| Key | Action | id |
|---|---|---|
| G | Go to card in surah | `goto` |
| C | Toggle context rows | `toggle_context` |
| L | Ignore issue | `ignore` |
| F | Auto-fill | `autofill` |

**Trim / split edit** (all fixed):

| Key | Action | id |
|---|---|---|
| ← / → | Nudge active boundary/cursor by `EDIT_NUDGE_MS` | `edit_step_back` / `edit_step_fwd` * |
| Tab | Cycle cursor/region (switches previewed region; zooms in multi-cursor split) | `edit_cycle` * |
| R | Replay selected region | `edit_replay` * |
| Enter / Escape | Confirm / cancel | `edit_confirm` / `edit_cancel` * |
| Space, , / . | Play preview, speed | (handled inline in `handleEditKey`) |

**Cross-verse WASL/WAQF boundary** (all fixed; owned by `WaslBoundary.svelte`, not the dispatcher):

| Key | Action | id |
|---|---|---|
| ← | Choose WASL (commits + advances the chain) | `wasl_pick_wasl` |
| → | Choose WAQF (commits + advances the chain) | `wasl_pick_waqf` |
| Tab | Highlight the other choice | `wasl_toggle` |
| Enter | Confirm the highlighted choice | `wasl_confirm` |

`*` = `rebindable: false` (structural; shown in the popover as reference only).

## Row/card action registry

Row-owned edit actions (A/S/E/G + delete) and card-owned ones (L ignore, F auto-fill, C toggle-context) can't be performed without the target row's `rowEl`, `mountId`, and `validationCategory`. The **primary** `SegmentRow` publishes a callback bundle to `activeRowActions`; `handleSegmentsKey` calls `get(activeRowActions)?.adjust?.()` etc. — identical to clicking that row's button.

- **Primary** = `!readOnly && !isContext && (isPlaying || (instanceRole === 'main' && !accordionOpen && segCurrentIdx === seg.index))`. One row is primary at a time (the main list is hidden while an accordion is open). Cleared on unmount.
- **Card callbacks** (`onCardIgnore` / `onCardAutofill` / `onCardToggleContext`) are passed by `GenericIssueCard` / `MissingWordsCard` to their main member `SegmentRow`s and forwarded into the bundle.
- **`edit_ref` fallback**: when no bundle is published (paused main-list row, or tests), E resolves the current segment from `displayedSegments` + `segCurrentIdx`. Mutating keyboard actions run `gateKeyboardEdit()` (the keyboard equivalent of `use:editGate`).

## Accordion navigation + auto-scroll

- **`accordion-nav.ts`** reads the open accordion's `.seg-row` elements straight from the DOM (the main list is hidden, so only accordion rows match), deduped by (chapter,index), in render order — **including context rows**. `accordionStep(dir)` steps relative to `playingSegmentIndex`, clamped at the ends. ↑/↓ and autoplay-advance both use it.
- **Autoplay-advance**: `playback.ts::_onRangeBoundary` — when a bounded accordion play hits its stop boundary with autoplay ON, it advances to `accordionStep(1)` (deferred via `queueMicrotask`).
- **Auto-scroll** is split by surface: the main list scrolls in `SegmentsList.svelte` (deliberately **ignores** accordion-origin plays); the open accordion scrolls itself via a driver in `ValidationPanel.svelte` that reacts to `playingSegmentIndex` (origin `accordion`) and centres whichever row is playing (main or context). A `_ctxEpoch` counter, bumped in `onCardContextChange`, forces a re-centre when Show/Hide Context shifts the focused card.

## Adding / changing a shortcut

1. Add (or edit) an entry in `SHORTCUT_ACTIONS` (`defaults.ts`): pick `context`, a `defaultKey` token, and `rebindable`.
2. Add its id to the matching `SHORTCUT_SECTIONS` group so it renders in the popover. **Easy to miss** — an action absent from `SHORTCUT_SECTIONS` works but is invisible in the guide.
3. Handle the action id in `handleSegmentsKey`'s dispatch switch (or, for fixed `edit`-pool keys, the `handleEditKey` `e.code` switch). Row/card actions go through `callRowAction(name, gate)`.
4. For a row/card action, ensure the bundle field exists in `RowActionBundle` and is published by `SegmentRow` (+ forwarded by the card if card-owned).
5. `npm run check` / `lint` / `test`. Store behaviour is covered by `tabs/segments/shortcuts/__tests__/store.test.ts`.

Structural keys (Enter / Escape / Tab / in-edit arrows / Ctrl+S / R) are intentionally `rebindable: false` so confirm/cancel/stepper stay stable.

## Self-contained widgets (outside the dispatcher)

`WaslBoundary.svelte` — the WASL/WAQF picker that pauses a cross-verse split chain after each child's ref-edit — owns its own keyboard, not the dispatcher. While the boundary is the paused step (auto-focused), `←` commits WASL and `→` commits WAQF in one keypress (positional, matching the on-screen `WASL · WAQF` order), each advancing the chain to the next child; `Tab` moves the highlight between the two and `Enter` commits the highlighted one. It `stopPropagation`s those keys so the global handler doesn't also seek / move focus; everything else (Space preview, …) falls through to `handleSegmentsKey`. The four keys are catalogued in `SHORTCUT_ACTIONS` under the `wasl` context (`rebindable: false`) purely so the footer guide lists them — `handleSegmentsKey` never selects that context.
