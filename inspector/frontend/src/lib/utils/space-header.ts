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
 * We always hand `init()` the space object rather than just the id. Given an
 * id alone the package fetches `huggingface.co/api/spaces/<id>` from the
 * browser, which is unauthenticated — on a private or protected Space that
 * 401s and the package throws reading a field off the undefined response.
 * Our own `/api/public/space` returns the same three fields at any visibility.
 *
 * Two guards keep it to exactly the standalone case:
 *   - iframed  → the Space page already paints the pill above us; a second one
 *                inside the frame would double up.
 *   - no space id → running locally (or off-Space), where there is nothing to
 *                link back to.
 */

const SPACE_ENDPOINT = '/api/public/space';

/** The shape `@huggingface/space-header` accepts in place of a lookup. */
type SpaceData = { id: string; author: string; likes: number };

/** True when the app owns the whole tab rather than sitting in the Space page's iframe. */
function isStandalone(): boolean {
  try {
    return window.self === window.top;
  } catch {
    // Cross-origin frame access throws — that itself means we are embedded.
    return false;
  }
}

async function fetchSpace(): Promise<SpaceData | null> {
  try {
    const resp = await fetch(SPACE_ENDPOINT, { credentials: 'same-origin' });
    if (!resp.ok) return null;
    const data = (await resp.json()) as {
      space_id?: unknown;
      author?: unknown;
      likes?: unknown;
    } | null;

    const id = data?.space_id;
    const author = data?.author;
    if (typeof id !== 'string' || !id.includes('/') || typeof author !== 'string') return null;

    return { id, author, likes: typeof data?.likes === 'number' ? data.likes : 0 };
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

  const space = await fetchSpace();
  if (!space) return;

  try {
    const { init } = await import('@huggingface/space-header');
    // init() is sync-looking but does async work internally, so await the
    // result: a bare call would reject unobserved and surface as an uncaught
    // promise error in the console.
    await init(space);
  } catch (err) {
    console.warn('space-header: injection failed, continuing without it', err);
  }
}
