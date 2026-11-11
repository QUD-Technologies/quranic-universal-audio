-- Inspector SQLite substrate — migration 0035.
--
-- Recitation quality status: a maintainer's manual verdict on how clean a
-- delivery's recitation is (clean | almost_clean | needs_work). NULL = never
-- rated. Set and cleared through the audit-only `reciter.quality_set`
-- transition, so every change carries its actor on the `transitions` row.

ALTER TABLE delivery_states ADD COLUMN quality_status TEXT
    CHECK (quality_status IS NULL OR quality_status IN ('clean', 'almost_clean', 'needs_work'));
