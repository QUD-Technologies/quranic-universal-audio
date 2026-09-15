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

/** Element id the package gives the pill it injects. */
const HEADER_ID = 'huggingface-space-header';

/** Breathing room between the pill and whatever we shift out from under it. */
const GAP_PX = 12;

/** Don't shove the controls so far left they leave the viewport. */
const MIN_LEFT_PX = 8;

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

/** How long to keep waiting for the package to actually insert its pill. */
const HEADER_WAIT_MS = 10_000;

/**
 * Resolve once the pill is in the DOM with a real box, or null on timeout.
 *
 * `init()` resolves before the element is laid out, so measuring on the next
 * frame finds either nothing or a zero-size box — and, worse, leaves us with
 * no element to attach the ResizeObserver to. Watch for it instead.
 */
function waitForHeader(): Promise<HTMLElement | null> {
  const ready = (): HTMLElement | null => {
    const el = document.getElementById(HEADER_ID);
    return el && el.getBoundingClientRect().width > 0 ? el : null;
  };

  const found = ready();
  if (found) return Promise.resolve(found);

  return new Promise((resolve) => {
    let timer = 0;
    const observer = new MutationObserver(() => {
      const el = ready();
      if (!el) return;
      observer.disconnect();
      window.clearTimeout(timer);
      resolve(el);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    timer = window.setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, HEADER_WAIT_MS);
  });
}

/**
 * Shift the app's own top-right cluster out from under the pill.
 *
 * The pill is `position: fixed` in the top-right corner and its width depends
 * on how long `owner/name` is, so the offset can't be a constant — we measure
 * it. `.auth-controls` is the grid's end column, which in LTR lands directly
 * beneath the pill; in RTL it sits on the left and the rects never intersect,
 * so the same measurement naturally yields no shift.
 *
 * When the row is too narrow to absorb the shift (phones), the controls drop
 * below the pill instead of sliding off-screen.
 */
function reserveRoomForHeader(): void {
  const pill = document.getElementById(HEADER_ID);
  const bar = document.querySelector<HTMLElement>('.auth-controls');
  const row = bar?.closest<HTMLElement>('header');
  if (!pill || !bar || !row) return;

  // Clear last pass before measuring, or each run compounds the previous one.
  bar.style.marginRight = '';
  row.style.marginTop = '';

  const pillBox = pill.getBoundingClientRect();
  const barBox = bar.getBoundingClientRect();

  const overlaps =
    barBox.right > pillBox.left &&
    barBox.left < pillBox.right &&
    barBox.top < pillBox.bottom &&
    barBox.bottom > pillBox.top;
  if (!overlaps) return;

  const shift = Math.ceil(barBox.right - pillBox.left + GAP_PX);
  if (barBox.left - shift >= MIN_LEFT_PX) {
    bar.style.marginRight = `${shift}px`;
  } else {
    row.style.marginTop = `${Math.ceil(pillBox.bottom + GAP_PX - barBox.top)}px`;
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
    return;
  }

  const pill = await waitForHeader();
  if (!pill) return;

  reserveRoomForHeader();

  // Re-measure on viewport changes — the shift depends on both rects, and the
  // header row reflows (and can switch to the stacked branch) as width changes.
  window.addEventListener('resize', reserveRoomForHeader, { passive: true });

  // The pill keeps growing after insertion (its avatar loads late), and each
  // size change moves the offset the controls need.
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(reserveRoomForHeader).observe(pill);
  }
}
