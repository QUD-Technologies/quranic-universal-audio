/**
 * Icon registry — hand-authored SVGs imported as raw text and inlined.
 *
 * Each .svg file uses `stroke="currentColor"` / `fill="currentColor"` so the
 * parent's CSS color cascades into the glyph. Add a new icon by dropping a
 * .svg into this folder and adding an entry below.
 *
 * The `?raw` query is Vite-specific: at build time the file contents are
 * imported as a string, which `<Icon>` then renders via `{@html}`. Files
 * remain bundled (not emitted to /public), so they're tree-shakable and
 * hashed for cache-busting.
 */

import arrowLeft from './arrow-left.svg?raw';
import autoplay from './autoplay.svg?raw';
import autoscroll from './autoscroll.svg?raw';
import bolt from './bolt.svg?raw';
import caretDown from './caret-down.svg?raw';
import check from './check.svg?raw';
import history from './history.svg?raw';
import keyboard from './keyboard.svg?raw';
import mail from './mail.svg?raw';
import pause from './pause.svg?raw';
import play from './play.svg?raw';
import replay from './replay.svg?raw';
import spin from './spin.svg?raw';
import whistle from './whistle.svg?raw';

export const ICONS = {
    'arrow-left': arrowLeft,
    autoplay,
    autoscroll,
    bolt,
    'caret-down': caretDown,
    check,
    history,
    keyboard,
    mail,
    pause,
    play,
    replay,
    spin,
    whistle,
} as const;

export type IconName = keyof typeof ICONS;
