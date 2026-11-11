/**
 * Reciter-picker review signals client. Wire shape is codegen'd in
 * ``lib/types/generated/schemas.ts``.
 */
import type { SegReviewSignalsResponse } from '../types/generated/schemas';

export async function fetchReviewSignals(signal?: AbortSignal): Promise<SegReviewSignalsResponse> {
    const resp = await fetch('/api/seg/review-signals', { signal });
    if (!resp.ok) throw new Error(`fetchReviewSignals: HTTP ${resp.status}`);
    return (await resp.json()) as SegReviewSignalsResponse;
}
