export type ShapedPlacement = [string, number, number, number | null, string | null];

export interface ShapedWord {
    baseText: string;
    advance: number;
    tokenCount: number;
    placements: ShapedPlacement[];
}

export interface ShapedGlyphFixture {
    upem: number;
    paths: Record<string, string>;
    words: Record<string, ShapedWord>;
}

export const EMPTY_SHAPED_GLYPHS: ShapedGlyphFixture = {
    upem: 1000,
    paths: {},
    words: {},
};

let pathsPromise: Promise<{ upem: number; paths: Record<string, string> }> | undefined;

function loadPaths(): Promise<{ upem: number; paths: Record<string, string> }> {
    pathsPromise ??= fetch('/generated/shaped-glyphs-v13/paths.json')
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Shaped-glyph paths: HTTP ${response.status}`);
            }
            return response.json() as Promise<{ upem: number; paths: Record<string, string> }>;
        })
        .then((fixture) => {
            if (fixture.upem !== 1000 || !fixture.paths) {
                throw new Error('Shaped-glyph paths: invalid fixture');
            }
            return fixture;
        });
    return pathsPromise;
}

let openersPromise: Promise<Record<string, ShapedWord>> | undefined;

/** The opener words' geometry: the verse words an Isti'adha / Basmala is read
 * as (`openerSourceRef`), which a chapter's own file holds only by chance. */
function loadOpenerWords(): Promise<Record<string, ShapedWord>> {
    openersPromise ??= fetch('/generated/shaped-glyphs-v13/openers.json')
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Shaped-glyph openers: HTTP ${response.status}`);
            }
            return response.json() as Promise<Pick<ShapedGlyphFixture, 'upem' | 'words'>>;
        })
        .then((fixture) => {
            if (fixture.upem !== 1000 || !fixture.words) {
                throw new Error('Shaped-glyph openers: invalid fixture');
            }
            return fixture.words;
        });
    return openersPromise;
}

/** Load the active Hafs chapter's pre-shaped DigitalKhatt geometry. The HTTP
 * cache retains revisited chapters; the caller's signal cancels stale chapter
 * switches before they can replace the current teleprompter. */
export async function loadShapedGlyphs(
    chapter: number,
    signal?: AbortSignal,
): Promise<ShapedGlyphFixture> {
    if (!Number.isInteger(chapter) || chapter < 1 || chapter > 114) {
        throw new Error(`Invalid shaped-glyph chapter ${chapter}`);
    }
    const [pathFixture, openerWords, response] = await Promise.all([
        loadPaths(),
        loadOpenerWords(),
        fetch(`/generated/shaped-glyphs-v13/${chapter}.json`, { signal }),
    ]);
    if (!response.ok) {
        throw new Error(`Shaped-glyph chapter ${chapter}: HTTP ${response.status}`);
    }
    const fixture = await response.json() as Pick<ShapedGlyphFixture, 'upem' | 'words'>;
    if (fixture.upem !== pathFixture.upem || !fixture.words) {
        throw new Error(`Shaped-glyph chapter ${chapter}: invalid fixture`);
    }
    return { ...fixture, words: { ...openerWords, ...fixture.words }, paths: pathFixture.paths };
}
