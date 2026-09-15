/**
 * Hugging Face mini header injection.
 *
 * `header: mini` in the Space README only styles the `huggingface.co/spaces/...`
 * page, which renders our app inside an iframe below its own chrome. Serve the
 * same app from a custom domain (or the direct `*.hf.space` URL) and HF hands
 * over the bare page — no header at all. `@huggingface/space-header` is HF's
 * own wrapper for that case: it draws the same floating pill from inside the
 * app.
 *
 * Two guards keep it to exactly the standalone case:
 *   - iframed  → the Space page already paints the pill above us; a second one
 *                inside the frame would double up.
 *   - no space id → running locally (or off-Space), where there is nothing to
 *                link back to.
 */

const SPACE_ENDPOINT = '/api/public/space';

/** True when the app owns the whole tab rather than sitting in the Space page's iframe. */
function isStandalone(): boolean {
  try {
    return window.self === window.top;
  } catch {
    // Cross-origin frame access throws — that itself means we are embedded.
    return false;
  }
}

async function fetchSpaceId(): Promise<string | null> {
  try {
    const resp = await fetch(SPACE_ENDPOINT, { credentials: 'same-origin' });
    if (!resp.ok) return null;
    const data: unknown = await resp.json();
    const id = (data as { space_id?: unknown } | null)?.space_id;
    return typeof id === 'string' && id.includes('/') ? id : null;
  } catch {
    // Offline, blocked, or the route is missing on an older deploy. The header
    // is decorative; never let it surface as an error.
    return null;
  }
}

/**
 * Inject the HF mini header, when this deploy is a Space served standalone.
 *
 * Safe to call unconditionally: every failure path is a silent no-op, and the
 * dynamic import keeps the package out of the main bundle for local dev and
 * for the iframed Space page.
 */
export async function installSpaceHeader(): Promise<void> {
  if (!isStandalone()) return;

  const spaceId = await fetchSpaceId();
  if (!spaceId) return;

  try {
    const { init } = await import('@huggingface/space-header');
    init(spaceId);
  } catch (err) {
    console.warn('space-header: injection failed, continuing without it', err);
  }
}
