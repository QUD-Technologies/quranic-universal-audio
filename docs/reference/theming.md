# Theming (light / dark)

The Inspector ships two themes from one token system. **Dark is the default**
(the original "dark by conviction" surface); **light is an opt-in peer** — a
calm, cool paper, never warm cream. A header toggle flips between them; the
choice is persisted and applied before first paint.

Open this when: adding a color anywhere, adding a token, touching a canvas that
paints color, or debugging a surface that doesn't re-skin on theme flip.

## The one rule

**Every color is a token. No raw hex/rgba/oklch literals in component CSS,
`<style>` blocks, or canvas draw code.** A surface that hardcodes a color is
dark-only and is a bug. The token's value is defined once per theme; components
are theme-agnostic and re-skin for free.

## How a flip happens

1. `index.html` has an inline `<script>` in `<head>` that reads
   `localStorage['insp_theme']` (falling back to `prefers-color-scheme`) and sets
   `document.documentElement[data-theme]` **before any stylesheet parses** — no
   flash-of-dark for light users.
2. `lib/stores/theme.svelte.ts` (`themeStore`) reads that attribute as its
   initial truth, then owns every flip: it updates its `$state`, persists to
   `localStorage` (`LS_KEYS.THEME` — the literal `insp_theme` is duplicated in
   index.html, keep them in sync), re-sets the `data-theme` attribute, and fires
   a `window` `themechange` event (`THEME_CHANGE_EVENT`).
   - Runes components read `themeStore.current` / `themeStore.isLight`.
   - Legacy (`$:` / auto-subscribe) components read the `theme$` readable store
     (`$theme$`) exported from the same module.
3. `ThemeToggle.svelte` (a sun/moon icon button in the header's `.auth-controls`)
   calls `themeStore.toggle()`.

The browser favicon follows the same resolved theme, including explicit overrides:
`index.html` chooses `favicon-light.webp` or `favicon-dark.webp` before first paint,
and `themeStore` swaps the icon on every live theme change. Both assets are the QUA
marks used by qud.dev rather than a separate Inspector symbol.

## The tokens

- `styles/tokens.css` `:root` — the **dark** values, the full set. Every new
  semantic token lives here with its shipped dark literal, so the dark theme is
  byte-for-byte unchanged by the migration.
- `styles/theme-light.css` `:root[data-theme="light"]` — the **light** values,
  overriding every color token. Imported **last** in `main.ts` so it wins the
  cascade. Non-color tokens (font, spacing, radii, motion, layout) are
  theme-invariant and are not overridden.
- `styles/base.css` `:root` and `styles/highlight-constants.css` also hold a few
  token *definitions* (`--tj-*` tajweed underlines, `--anim-highlight-color`,
  `--hl-cell-*`); their light values are overridden in `theme-light.css`.

Token families: surface ramp · borders · text · accent (+ tints/border/soft) ·
lifecycle `--state-*` · status `--ok/--warn/--bad/--info/--neutral` · action
buttons `--btn-*` · CTA `--cta-*` · waveform `--wf-*` · charts `--chart-*` ·
analysis `--ts-*` / `--hl-*` / `--tj-*` · edit-toolbar `--chrome-*` · scrims +
`--shadow-*` · brand `--brand-*`. See the comments in `tokens.css` for the map.

### Light design intent

Cool blue-violet identity preserved (soft grey paper, low chroma, softened
contrast). The accent and every state hue **darken** so they read on a light
surface; white-on-dark inks flip to dark-on-light, and the accent's button-ink
flips to white. Dark conveys depth purely by a lightness ramp and stays flat;
light adds a single soft `--shadow-*` on floating surfaces (no headroom for
lightness depth near white) and a soft `--scrim` veil instead of near-black.

## Canvas surfaces (the part CSS can't reach)

A `data-theme` flip re-skins CSS, but **not** a 2D-canvas `ctx.fillStyle` — that
color is baked in JS. Bridge:

- `lib/utils/canvas-theme.ts` — `themeColor('--token', fallback)` reads a CSS
  custom property's computed value, **cached per theme**, cache auto-cleared on
  `themechange`. Draw code calls this instead of hardcoding.
- Canvas-owning components repaint on flip: either add `themeStore.current` to an
  existing reactive redraw (TimestampsWaveform), or
  `addEventListener(THEME_CHANGE_EVENT, redraw)` in `onMount` (dashboard /
  segment waveforms, stats charts — Chart.js rebuilds its config from resolved
  vars). Waveform colors live in `--wf-*`, chart colors in `--chart-*`.

## The accent-derived analysis colors

The Timestamps analysis triad (word / letter / phoneme cells), the karaoke wipe,
the teleprompter, and the footer/filmstrip chrome all derive from the user's
chosen highlight accent (the footer droplet). That derivation is **legibility-
banded**, and the band is theme-dependent:

- `lib/utils/color-derive.ts` — pure OKLCh math (`analogousTriadCfg`,
  `legibleAccentCfg`, `deepForCfg`, `inkFor`). Takes explicit band knobs; no
  theme awareness of its own. `inkFor` auto-picks black/white ink by WCAG
  luminance and needs no theme parameter.
- `lib/utils/highlight-model.ts` — `DEFAULT_HIGHLIGHT_MODEL` (dark, legible-on-
  dark band ~L 0.66–0.86, deep-block active fill + white ink) and
  `LIGHT_HIGHLIGHT_MODEL` (darker band ~L 0.46–0.70, **soft-tint** active fill +
  **auto** ink — calm, no harsh dark slab on paper). `modelForTheme(theme)`
  selects. `TimestampsTab` feeds the themed model into `resolveHighlightVars`.
- `lib/recitation-animation/config.ts` (`cssVars(cfg, theme)`) — the teleprompter
  line: light gets the darker legible band, a dark base-text color, and drops the
  silhouette halo (dark-on-paper needs none).
- `lib/utils/accent-override.ts` (`accentVars(hex, theme)`) — remaps `--accent*`
  on the footer/player to the user's pick; light clamps a too-**light** pick
  **down** into the darker band (dark clamps a too-dark pick up) and darkens on
  hover instead of lightening.

**Muted cells** (silent grapheme, dropped diacritic, dropped implicit madd) fade
via `--ts-mute-opacity` — one token every muted cell reads (`timestamps.css` +
the `TajweedSettingsPanel` legend), so the fade is uniform and tunable in one
place. It is theme-conditional: `0.5` in dark, **`0.4`** in light — the only
non-color token overridden per theme, because a fixed alpha that reads as muted
over the near-black dark cell washes a silent glyph almost invisible over
near-white paper, so light needs a deeper fade to still read as *muted*.

## Adding color — the checklist

- Need a color in CSS? Use an existing `var(--token)`. No token fits? Add it to
  `tokens.css :root` (dark = your literal) **and** `theme-light.css` (light
  value), then use it. Never a raw literal.
- Painting on a canvas? Add a `--wf-*` / `--chart-*` token, read it via
  `themeColor()`, and make the component repaint on `themechange`.
- After adding tokens, sanity-check parity: every token in `theme-light.css` must
  have a dark definition in `tokens.css` / `base.css` / `highlight-constants.css`.
