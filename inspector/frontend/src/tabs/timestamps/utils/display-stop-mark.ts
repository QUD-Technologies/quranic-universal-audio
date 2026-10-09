/**
 * The waqf mark a shown pause draws comes from the Digital Khatt word, not the
 * shard's producer cells.
 *
 * Shards built before the producer's 1421 stop edition carry the 1405
 * printing's marks in their `stop_sign` columns, while the word text is Digital
 * Khatt (1421). Taking the mark from the display text keeps the tile and the
 * word in agreement for shards of either edition: no mark is drawn twice, and
 * none is left glued inside the word.
 */
import { stripBoundaryMarks, type CellBoundary } from '@quranic-phonemizer/cells';

import { splitWaqf } from '../../../lib/utils/waqf';

/**
 * Move `text`'s trailing waqf mark into `boundary`'s stop column, replacing the
 * producer's waqf mark there (a sakt seen in the column is kept), and return
 * the word text with every mark the tile now draws removed.
 */
export function adoptDisplayStopMark(text: string, boundary: CellBoundary): string {
    const column = boundary.columns.find((item) => item.role === 'stop_sign');
    if (!column) return stripBoundaryMarks(text, boundary);
    column.text = splitWaqf(column.text).clean + (splitWaqf(text).mark ?? '');
    return stripBoundaryMarks(text, boundary);
}
