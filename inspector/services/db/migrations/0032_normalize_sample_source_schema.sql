-- Early sample uploads were indexed as `legacy` even though their persisted
-- source.json files use the bare alignment contract.  The wire schema only
-- exposes the two concrete source shapes, so those stale labels made the
-- entire samples listing fail validation.

UPDATE samples
SET source_schema = 'alignment'
WHERE source_schema = 'legacy';
