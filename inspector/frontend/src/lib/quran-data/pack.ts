/**
 * The Talqeen Quran data pack (`@qud-technologies/quran-data`) in the browser.
 *
 * The backend serves the pinned pack and the fonts its `fonts()` names under
 * `/api/static/quran-data/` (fetched at build time, sha256-checked; see
 * `scripts/devenv/install_quran_data.py`). The pack is opened once per session.
 */
import { type Font, openPack, type QuranPack } from '@qud-technologies/quran-data';

export const QURAN_DATA_BASE = '/api/static/quran-data/';
const PACK_FILE = 'quran-core.qref';

let pending: Promise<QuranPack> | null = null;

/** The opened pack; a failed load is retried on the next call. */
export function loadQuranPack(): Promise<QuranPack> {
    if (!pending) {
        pending = fetch(`${QURAN_DATA_BASE}${PACK_FILE}`)
            .then((r) => {
                if (!r.ok) throw new Error(`${PACK_FILE}: HTTP ${r.status}`);
                return r.arrayBuffer();
            })
            .then((bytes) => openPack(bytes));
        pending.catch(() => { pending = null; });
    }
    return pending;
}

/** Where the Mushaf view's justified-line data is served (`<layout>.bin`, `outlines-<sha8>.bin`). */
export const QURAN_JUSTIFY_BASE = `${QURAN_DATA_BASE}justify/`;

/** Where a font of `pack.fonts()` is served: its woff2 when the pack lists one (a fraction of the OTF). */
export function quranFontUrl(_id: string, font: Font): string {
    const file = font.files?.find((f) => f.format === 'woff2')?.file ?? font.file;
    return `${QURAN_DATA_BASE}${file}`;
}
