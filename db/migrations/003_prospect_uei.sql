-- 003: store the USAspending recipient UEI we resolve for each prospect. Idempotent.
--
-- prospect_audit.py resolves a recipient UEI before it searches awards, because
-- recipient_search_text merges same-named but unrelated firms without saying so.
-- Nothing stored the answer, so every audit paid for the same resolution again
-- and an old wave could not be re-audited cheaply. Three columns, not one:
--
--   uei             the resolved identifier, or NULL when none was found
--   uei_status      'resolved' (exactly one UEI carries the name),
--                   'ambiguous' (several did; uei is the largest by dollars and
--                   must not be read as settled), or 'none' (no UEI carries it,
--                   so the audit falls back to the fuzzy name search)
--   uei_resolved_at when the lookup ran, so a stale 'none' can be retried later
--                   without re-resolving every row that is already clean
--
-- Note: the prospects table itself is not in db/schema.sql - it was created
-- during the 2026-09-08 HubSpot-to-Neon migration and has only ever been
-- altered by hand. This migration is the first written record of a change to it.
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS uei             text;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS uei_status      text;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS uei_resolved_at timestamptz;
CREATE INDEX IF NOT EXISTS prospects_uei_idx ON prospects (uei);
