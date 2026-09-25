/**
 * Hugging Face mini header injection.
 *
 * The `huggingface.co/spaces/...` page wraps our app in its own chrome, which
 * names the Space and links back to it. Serve the same app from a custom domain
 * (or the direct `*.hf.space` URL) and HF hands over the bare page — no header
 * at all. `@huggingface/space-header` is HF's own wrapper for that case: it
 * draws a floating pill from inside the app.
 *
 * We always hand `init()` the space object rather than just the id. Given an
 * id alone the package fetches `huggingface.co/api/spaces/<id>` from the
 * browser, which is unauthenticated — on a private or protected Space that
 * 401s and the package throws reading a field off the undefined response.
 * Our own `/api/public/space` returns the same three fields at any visibility.
 *
 * Two guards keep it to exactly the standalone case:
 *   - iframed  → the Space page draws its own header, and the package refuses
 *                to run anyway: it returns early when `ancestorOrigins` holds
 *                huggingface.co. (Which is also why the Space README omits
 *                `header: mini` — that overlays HF's pill on a full-bleed
 *                iframe, over this app's own top-right controls, and an
 *                embedded app can neither measure nor move it.)
 *   - no space id → running locally (or off-Space), where there is nothing to
 *                link back to.
 */

const SPACE_ENDPOINT = '/api/public/space';

/** Element id the package gives the pill it injects. */
const HEADER_ID = 'huggingface-space-header';

/** Breathing room between the pill and the controls stacked under it. */
const GAP_PX = 8;

/** Keep the pill off the viewport's top/right edge. */
const MIN_EDGE_PX = 8;

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
      color: var(--text-primary) !important;
      /* the package only pads the leading edge, and the trailing control that
         balanced it is hidden below */
      padding-right: 1rem !important;
    }
    /* Every rule inside is painted in the package's own grey-200, including
       borders we never target individually (the like-count separator). */
    #${HEADER_ID},
    #${HEADER_ID} * {
      border-color: var(--border-default) !important;
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
    /* The trailing control expands the pill into HF's full Space header, which
       only exists on the huggingface.co page. Served standalone there is
       nothing to expand into, so all it does is set display:none on the pill
       with no way to bring it back. Hide it, and the divider that set it off. */
    #${HEADER_ID} > div:nth-child(2) {
      display: none !important;
    }
    #${HEADER_ID} > div:first-child {
      border-right-width: 0 !important;
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
 * box, so measuring the box would misalign the pill by a gutter on the
 * Dashboard only. Taking the max rather than the last child keeps it
 * direction-agnostic.
 */
function visibleRightEdge(container: HTMLElement): number {
  const edges = [...container.children].map((child) => child.getBoundingClientRect().right);
  return edges.length ? Math.max(...edges) : container.getBoundingClientRect().right;
}

/**
 * Stack the pill above the app's own top-right cluster.
 *
 * The pill is `position: fixed` in the corner; the controls sit underneath it
 * rather than beside it. The pill's right edge is lined up with the cluster's
 * drawn right edge, and the header row gets a top margin that clears the pill.
 *
 * Vertical maths happens in "page at rest" coordinates: the pill is fixed (so
 * its viewport box already is that), while the header row scrolls, so its box
 * is lifted back by `scrollY`.
 */
function placeHeader(): void {
  const pill = document.getElementById(HEADER_ID);
  const bar = document.querySelector<HTMLElement>('.auth-controls');
  const row = bar?.closest<HTMLElement>('header');
  if (!pill || !bar || !row) return;

  // Clear last pass before measuring, or each run compounds the previous one.
  row.style.marginTop = '';

  const barTop = bar.getBoundingClientRect().top + window.scrollY;
  const right = Math.max(MIN_EDGE_PX, Math.round(document.documentElement.clientWidth - visibleRightEdge(bar)));
  pill.style.top = `${MIN_EDGE_PX}px`;
  pill.style.right = `${right}px`;

  const pillBottom = pill.getBoundingClientRect().bottom;
  const push = Math.ceil(pillBottom + GAP_PX - barTop);
  if (push > 0) row.style.marginTop = `${push}px`;
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

  // Re-measure on viewport changes — the pill tracks the cluster's right edge.
  window.addEventListener('resize', placeHeader, { passive: true });

  // The pill keeps changing size after insertion, and none of it fires a
  // resize event.
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(placeHeader).observe(pill);
  }

  // Our own two reflows are watched with a MutationObserver rather than by
  // observing the cluster's box, because mutations are delivered in a
  // background tab where ResizeObserver is not:
  //   - switching tabs adds/removes `rail-aligned` on the row, which moves the
  //     cluster's drawn edge by one gutter;
  //   - the cluster itself re-flows when identity resolves and the sign-in
  //     button gives way to the account chip.
  // Neither filter sees what placeHeader writes (inline styles on the row and
  // the pill), so this cannot feed back on itself.
  const bar = document.querySelector<HTMLElement>('.auth-controls');
  const row = bar?.closest<HTMLElement>('header');
  if (row) {
    new MutationObserver(placeHeader).observe(row, { attributeFilter: ['class'] });
  }
  if (bar) {
    new MutationObserver(placeHeader).observe(bar, { childList: true, subtree: true });
  }
}
