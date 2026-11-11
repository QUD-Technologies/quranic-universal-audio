import type { HistoryBatch, Segment } from '../../../../lib/types/view-models';
import type {
    EditChain,
    EditChainOp,
    HistorySnapshot,
} from '../../types/segments';

export type { EditChain, EditChainOp };

/** Internal result type for `buildEditChains`. */
export interface BuildChainsResult {
    chains: Map<string, EditChain>;
    chainedOpIds: Set<string>;
}

export function buildEditChains(allBatches: HistoryBatch[]): BuildChainsResult {
    const chains = new Map<string, EditChain>();
    const chained = new Set<string>();
    const uidToChainId = new Map<string, string>();

    // Process oldest batches first to build forward-flowing chains
    const sortedBatches = [...allBatches].sort((a, b) => (a.saved_at_utc || '').localeCompare(b.saved_at_utc || ''));

    for (const batch of sortedBatches) {
        for (const op of (batch.operations || [])) {
            const beforeUids = ((op.targets_before || []) as Array<{ segment_uid?: string }>).map((s) => s.segment_uid).filter((u): u is string => !!u);
            
            let chainId: string | null = null;
            const foundChains = new Set<string>();
            for (const uid of beforeUids) {
                if (uidToChainId.has(uid)) {
                    foundChains.add(uidToChainId.get(uid)!);
                }
            }

            if (foundChains.size > 0) {
                // Merge all found chains into the first one
                const chainsList = Array.from(foundChains);
                chainId = chainsList[0]!;
                const mainChain = chains.get(chainId)!;
                for (let i = 1; i < chainsList.length; i++) {
                    const otherChainId = chainsList[i]!;
                    const otherChain = chains.get(otherChainId)!;
                    
                    mainChain.ops.push(...otherChain.ops);
                    for (const { op: otherOp } of otherChain.ops) {
                        const otherAfterUids = ((otherOp.targets_after || []) as Array<{ segment_uid?: string }>).map((s) => s.segment_uid).filter((u): u is string => !!u);
                        for (const u of otherAfterUids) {
                            uidToChainId.set(u, chainId);
                        }
                    }
                    if (otherChain.latestDate > mainChain.latestDate) {
                        mainChain.latestDate = otherChain.latestDate;
                    }
                    // Keep the oldest rootSnap
                    const mainRootTime = mainChain.rootSnap?.time_start ?? Infinity;
                    const otherRootTime = otherChain.rootSnap?.time_start ?? Infinity;
                    if (otherRootTime < mainRootTime) {
                        mainChain.rootSnap = otherChain.rootSnap;
                    }
                    chains.delete(otherChainId);
                }
                mainChain.ops.sort((a, b) => (a.batch.saved_at_utc || '').localeCompare(b.batch.saved_at_utc || ''));
                mainChain.ops.push({ op, batch });
                if ((batch.saved_at_utc || '') > mainChain.latestDate) {
                    mainChain.latestDate = batch.saved_at_utc || '';
                }
            } else {
                chainId = op.op_id;
                chains.set(chainId, {
                    rootSnap: op.targets_before?.[0] as HistorySnapshot | undefined,
                    rootBatch: batch,
                    ops: [{ op, batch }],
                    latestDate: batch.saved_at_utc || '',
                });
            }

            chained.add(op.op_id);

            const afterUids = ((op.targets_after || []) as Array<{ segment_uid?: string }>).map((s) => s.segment_uid).filter((u): u is string => !!u);
            for (const uid of afterUids) {
                uidToChainId.set(uid, chainId);
            }
        }
    }

    // Unwrap single-op chains back into standard ops, EXCEPT for single splits
    // (1 → N, kept in EditChainRow's split layout). Single merges (N → 1)
    // fall through to HistoryOp which already renders the N-before / 1-after
    // diff correctly with merge highlights — EditChainRow's structure assumes
    // a 1-before root and would collapse merge into a misleading 1 → 1 card.
    for (const [chainId, chain] of chains.entries()) {
        const opType = chain.ops[0]?.op.op_type;
        const isSplitChain = opType === 'split_segment';
        if (chain.ops.length <= 1 && !isSplitChain) {
            chains.delete(chainId);
            chained.delete(chain.ops[0]!.op.op_id);
        }
    }

    return { chains, chainedOpIds: chained };
}

export function computeChainLeafSnaps(chain: EditChain): HistorySnapshot[] {
    const finalSnaps = new Map<string, HistorySnapshot>();
    const beforeUids = new Set<string>();
    for (const { op } of chain.ops) {
        const afterUids = new Set<string>(((op.targets_after || []) as HistorySnapshot[]).map((s) => s.segment_uid).filter((u): u is string => !!u));
        for (const snap of ((op.targets_before || []) as HistorySnapshot[])) { if (snap.segment_uid && !afterUids.has(snap.segment_uid)) beforeUids.add(snap.segment_uid); }
        for (const snap of ((op.targets_after || []) as HistorySnapshot[])) { if (snap.segment_uid) finalSnaps.set(snap.segment_uid, snap); }
    }
    return [...finalSnaps.entries()].filter(([uid]) => !beforeUids.has(uid)).map(([, snap]) => snap).sort((a, b) => a.time_start - b.time_start);
}

export function getChainBatchIds(chain: EditChain): string[] {
    const seen = new Set<string>();
    const ids: string[] = [];
    for (let i = chain.ops.length - 1; i >= 0; i--) {
        const batchId = chain.ops[i]?.batch?.batch_id;
        if (batchId && !seen.has(batchId)) {
            seen.add(batchId);
            ids.push(batchId);
        }
    }
    return ids;
}

export function snapToSeg(snap: HistorySnapshot, chapter: number | null): Segment {
    return {
        index: snap.index_at_save ?? 0,
        entry_idx: 0,
        chapter: chapter ?? undefined,
        audio_url: snap.audio_url || '',
        time_start: snap.time_start, time_end: snap.time_end,
        matched_ref: snap.matched_ref || '', matched_text: snap.matched_text || '',
        confidence: snap.confidence ?? 0,
        join_verdicts: snap.join_verdicts,
        ...(snap.wrap_word_ranges ? { wrap_word_ranges: snap.wrap_word_ranges } : {}),
    };
}
