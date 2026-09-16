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
 * Both deployed shapes end up needing the same second half of this module.
 * The pill is a floating overlay in the top-right corner, exactly where the
 * app keeps its own controls, so whoever drew it, the header row has to make
 * room. Embedded we cannot see HF's pill (it lives in the cross-origin page
 * around our iframe), so the package's pill is still built — kept hidden — and
 * measured as a stand-in for it.
 *
 * The one hard guard is the space id: without it we are running locally or off
 * Space, and there is nothing to link back to.
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

/**
 * Geometry of HF's own overlay pill on the `huggingface.co/spaces/...` page.
 *
 * There `header: mini` makes HF draw the pill in the *parent* document, over a
 * full-viewport iframe, so the app cannot see or move it: the offsets below are
 * its Tailwind `top-5` / `right-6`, read off the live page.
 */
const HF_PILL_TOP_PX = 20;
const HF_PILL_RIGHT_PX = 24;

/**
 * Slack between HF's rendering of the pill and the package's.
 *
 * Same package, same Space, but HF's page gives it a roomier treatment — 433px
 * against the package's own 411px at identical content. We size the embedded
 * reservation off a local copy of the pill, so pad it by that difference
 * (rounded up) or the cluster would sit under HF's wider one.
 */
const HF_PILL_PAD_PX = 24;

/** Element id of the stylesheet that themes the pill, or hides it when embedded. */
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

/**
 * Keep the package's pill out of sight, for the embedded case.
 *
 * `visibility` rather than `display`, because a `display: none` element has no
 * box and the width we build the reservation from would read as zero.
 */
function hideLocalPill(): void {
  if (document.getElementById(THEME_STYLE_ID)) return;

  const style = document.createElement('style');
  style.id = THEME_STYLE_ID;
  style.textContent = `
    #${HEADER_ID} {
      visibility: hidden !important;
      pointer-events: none !important;
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

/** Just the sides of a box, in viewport coordinates. */
type PillBox = { top: number; bottom: number; left: number; right: number };

/**
 * Where the pill the reader sees actually is.
 *
 * Standalone we drew it ourselves, so it can be measured *and* moved: it is
 * `position: fixed` at a height the package picked, a few pixels above our
 * header row — close enough to look like a mistake rather than a separate
 * surface — so centre it on the row first and report where it landed.
 *
 * Embedded, the pill belongs to the huggingface.co document around our iframe.
 * It is cross-origin, so it can be neither measured nor centred; what we have
 * instead is a hidden local copy of the very same pill, which gives us its
 * width. Everything else is HF's fixed corner offsets.
 */
function measurePill(row: { barTop: number; barBottom: number }): PillBox | null {
  const pill = document.getElementById(HEADER_ID);
  if (!pill) return null;

  const box = pill.getBoundingClientRect();
  if (box.width === 0) return null;

  if (!isStandalone()) {
    const right = window.innerWidth - HF_PILL_RIGHT_PX;
    return {
      top: HF_PILL_TOP_PX,
      bottom: HF_PILL_TOP_PX + box.height,
      left: right - box.width - HF_PILL_PAD_PX,
      right,
    };
  }

  const top = Math.max(MIN_TOP_PX, Math.round((row.barTop + row.barBottom - box.height) / 2));
  pill.style.top = `${top}px`;
  return pill.getBoundingClientRect();
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
  const bar = document.querySelector<HTMLElement>('.auth-controls');
  const row = bar?.closest<HTMLElement>('header');
  if (!bar || !row) return;

  const tabs = row.querySelector<HTMLElement>('.tab-bar');

  // Clear last pass before measuring, or each run compounds the previous one.
  // Safe to own outright: the header carries no padding of its own (the
  // rail-aligned insets live on its children), and nothing else transforms
  // the tab bar.
  row.style.paddingRight = '';
  row.style.marginTop = '';
  if (tabs) tabs.style.transform = '';

  const barBox = bar.getBoundingClientRect();
  const barTop = barBox.top + window.scrollY;
  const barBottom = barBox.bottom + window.scrollY;
  const barContentRight = visibleRightEdge(bar);

  const pillBox = measurePill({ barTop, barBottom });
  if (!pillBox) return;
  const overlaps =
    barContentRight > pillBox.left &&
    barBox.left < pillBox.right &&
    barTop < pillBox.bottom &&
    barBottom > pillBox.top;
  if (!overlaps) return;

  const reserve = Math.ceil(barContentRight - pillBox.left + GAP_PX);
  if (barBox.left - reserve >= MIN_LEFT_PX) {
    row.style.paddingRight = `${reserve}px`;
    recentreTabs(tabs, reserve, pillBox.left);
  } else {
    row.style.marginTop = `${Math.ceil(pillBox.bottom + GAP_PX - barTop)}px`;
  }
}

/**
 * Undo the sideways drift the reservation gives the centred tab bar.
 *
 * Padding shrinks the grid's content box from the right, so its midpoint — and
 * with it the `justify-self: center` tab bar — moves left by half the
 * reservation. The tabs are centred on the page, not on whatever room is left
 * beside the pill, so shift them back. A transform keeps this purely visual:
 * it cannot feed back into the measurements the reservation was derived from.
 *
 * Clamped so the correction never slides the tabs under the pill on a width
 * where the two would otherwise meet.
 */
function recentreTabs(tabs: HTMLElement | null, reserve: number, pillLeft: number): void {
  if (!tabs) return;
  const box = tabs.getBoundingClientRect();
  const shift = Math.min(Math.round(reserve / 2), Math.floor(pillLeft - GAP_PX - box.right));
  if (shift > 0) tabs.style.transform = `translateX(${shift}px)`;
}

/**
 * Inject the HF mini header (standalone) or a hidden ruler for HF's own
 * (embedded), and keep the header row clear of whichever one is on screen.
 *
 * Safe to call unconditionally: every failure path is a silent no-op, and the
 * dynamic import keeps the package out of the main bundle for local dev, where
 * there is no Space to name.
 */
export async function installSpaceHeader(): Promise<void> {
  const space = await fetchSpace();
  if (!space) return;

  // Land the overrides before the pill exists, so it never paints in the
  // package's own light palette first. Embedded, the pill is only a ruler:
  // hide it (HF draws the visible one) and leave it at the package's native
  // size, which is what HF_PILL_PAD_PX is calibrated against.
  if (isStandalone()) injectHeaderTheme();
  else hideLocalPill();

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
