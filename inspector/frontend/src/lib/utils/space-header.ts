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

/** Keep the pill off the very edge when the header row sits unusually high. */
const MIN_TOP_PX = 8;

/** Element id of the stylesheet that repaints the pill in our palette. */
const THEME_STYLE_ID = 'space-header-theme';

/**
 * Repaint the pill in the app's palette.
 *
 * The package ships a fixed light treatment — a near-white gradient with
 * grey-200 borders and grey-800 text — which reads as a foreign object dropped
 * on our dark canvas. Every rule below points at a theme token, so the pill
 * follows the light/dark toggle for free rather than needing a second set of
 * overrides.
 *
 * The selectors lean on the package's element order (avatar, author, slash,
 * name, likes) because it gives its nodes no classes. That order is the
 * package's public shape — it is what `init()` builds — and a miss degrades to
 * the package's own colours rather than breaking anything.
 */
function injectHeaderTheme(): void {
  if (document.getElementById(THEME_STYLE_ID)) return;

  const style = document.createElement('style');
  style.id = THEME_STYLE_ID;
  style.textContent = `
    #${HEADER_ID} {
      background-image: none !important;
      background-color: var(--panel) !important;
      border-color: var(--border-default) !important;
      color: var(--text-primary) !important;
    }
    /* author link */
    #${HEADER_ID} > div:first-child > a:first-of-type {
      color: var(--text-muted) !important;
    }
    /* the "/" between owner and name */
    #${HEADER_ID} > div:first-child > div {
      color: var(--text-faint) !important;
    }
    /* space name */
    #${HEADER_ID} > div:first-child > a:nth-of-type(2) {
      color: var(--text-primary) !important;
    }
    /* like button */
    #${HEADER_ID} > div:first-child > a:nth-of-type(3) {
      border-color: var(--border-default) !important;
      color: var(--text-secondary) !important;
    }
    /* trailing icon button */
    #${HEADER_ID} > div:nth-child(2) {
      color: var(--text-muted) !important;
    }
  `;
  document.head.appendChild(style);
}

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
 * Right edge of what a container actually draws, ignoring its own padding.
 *
 * The Dashboard adds `padding-inline-end: var(--gutter)` to `.auth-controls`
 * so the cluster lines up with the right rail. That padding is inside the
 * box, so measuring the box put the last button a whole gutter further from
 * the pill on the Dashboard than on every other tab. Measuring the children
 * keeps the visible gap identical across tabs.
 *
 * Taking the max rather than the last child keeps it direction-agnostic.
 */
function visibleRightEdge(container: HTMLElement): number {
  const edges = [...container.children].map((child) => child.getBoundingClientRect().right);
  return edges.length ? Math.max(...edges) : container.getBoundingClientRect().right;
}

/**
 * Line the pill up with the app's own top-right cluster and clear their overlap.
 *
 * The pill is `position: fixed` in the corner at a height the package picked,
 * which leaves it a few pixels above our header row — close enough to look
 * like a mistake rather than a separate surface. We centre it on the row
 * instead, and only then reserve the width it covers.
 *
 * Room is made with padding on the header, not a margin on the cluster. The
 * row is `grid-template-columns: 1fr auto 1fr`, and `1fr` is
 * `minmax(auto, 1fr)`: a margin counts toward the item's outer size, so it
 * grew the end track and the cluster slid left by *less* than the margin, a
 * feedback loop that needed a second pass to converge. The header's own width
 * comes from its parent, so padding shrinks the grid's content box without
 * changing anything we just measured — `justify-self: end` then lands the
 * cluster exactly at the new content edge, in one pass.
 *
 * All vertical maths happens in "page at rest" coordinates: the pill is fixed
 * (so its viewport box already is that), while the header row scrolls, so its
 * box is lifted back by `scrollY`. Without that the alignment would drift as
 * soon as the reader scrolled.
 *
 * When the row is too narrow to absorb the reservation (phones), the controls
 * drop below the pill instead of being crushed.
 */
function placeHeader(): void {
  const pill = document.getElementById(HEADER_ID);
  const bar = document.querySelector<HTMLElement>('.auth-controls');
  const row = bar?.closest<HTMLElement>('header');
  if (!pill || !bar || !row) return;

  // Clear last pass before measuring, or each run compounds the previous one.
  // Safe to own outright: the header carries no padding of its own (the
  // rail-aligned insets live on its children).
  row.style.paddingRight = '';
  row.style.marginTop = '';

  const barBox = bar.getBoundingClientRect();
  const barTop = barBox.top + window.scrollY;
  const barBottom = barBox.bottom + window.scrollY;
  const barContentRight = visibleRightEdge(bar);

  // Centre the pill on the row before measuring the overlap, so the
  // reservation is computed against where the pill actually ends up.
  const pillHeight = pill.getBoundingClientRect().height;
  const top = Math.max(MIN_TOP_PX, Math.round((barTop + barBottom - pillHeight) / 2));
  pill.style.top = `${top}px`;

  const pillBox = pill.getBoundingClientRect();
  const overlaps =
    barContentRight > pillBox.left &&
    barBox.left < pillBox.right &&
    barTop < pillBox.bottom &&
    barBottom > pillBox.top;
  if (!overlaps) return;

  const reserve = Math.ceil(barContentRight - pillBox.left + GAP_PX);
  if (barBox.left - reserve >= MIN_LEFT_PX) {
    row.style.paddingRight = `${reserve}px`;
  } else {
    row.style.marginTop = `${Math.ceil(pillBox.bottom + GAP_PX - barTop)}px`;
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

  // Land the overrides before the pill exists, so it never paints in the
  // package's own light palette first.
  injectHeaderTheme();

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

  placeHeader();

  // The pill labels the Space in a monospace face, so the webfont swap resizes
  // it after that first pass. Both hooks below fire in a background tab, where
  // rAF and ResizeObserver delivery are stalled and would otherwise leave the
  // reservation stuck at the fallback-font width until the tab is first shown.
  void document.fonts?.ready?.then(placeHeader);
  if (document.readyState !== 'complete') {
    window.addEventListener('load', placeHeader, { once: true });
  }

  // Re-measure on viewport changes — the shift depends on both rects, and the
  // header row reflows (and can switch to the stacked branch) as width changes.
  window.addEventListener('resize', placeHeader, { passive: true });

  // Both boxes move the offset and neither change fires a resize event: the
  // pill keeps growing after insertion (its avatar loads late), and the
  // controls re-flow when identity resolves and the sign-in button gives way
  // to the account cluster.
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(placeHeader);
    observer.observe(pill);
    const bar = document.querySelector('.auth-controls');
    if (bar) observer.observe(bar);
  }
}
