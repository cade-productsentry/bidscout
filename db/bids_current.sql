-- bids_current: the deduplicated read view over `bids`.
--
-- WHY: SAM.gov publishes every amendment to a solicitation as a NEW notice with
-- a new opportunity id. scraper/main.py upserts on source_id, so an amended
-- solicitation lands as an additional row rather than an update. Discovered
-- 2026-09-16: 861 open rows were only 559 distinct solicitations (roofing 38 ->
-- 21, site-work 76 -> 38). Every count we quoted in outreach email, in the
-- weekly digest, and on the /bids/ SEO pages was inflated by that factor, and
-- the digest listed the same job several times in a row.
--
-- FIX: dedupe at the READ layer, not the write layer. The base table keeps every
-- notice (amendment history is worth having, and nothing is destroyed); all
-- counting and listing paths read this view instead.
--
-- KEY: (title, agency). Deliberately NOT (title, agency, due_at): extending the
-- closing date is one of the most common amendment types, so including due_at
-- left 41 open solicitations still double counted on 2026-09-16. Newest
-- posted_at wins, so the surviving row carries the CURRENT deadline and its url
-- points at the most recent amendment, which is the one a contractor should open.
--
-- Deliberately NOT keyed on the solicitation number: it appears only inside
-- free-text raw_text and is not reliably parseable across notice types.
--
-- Over-merge check run 2026-09-16 against open bids: zero title+agency groups
-- spanned more than 45 days of closing dates, and zero spanned more than one
-- state, i.e. no sign of genuinely distinct solicitations being collapsed.
-- Re-run those two checks if this key ever looks suspect. The residual risk
-- undercounts, which is the safe direction for claims we put in commercial email.
create or replace view bids_current as
select distinct on (coalesce(title,''), coalesce(agency,''))
       *
from bids
order by coalesce(title,''), coalesce(agency,''), posted_at desc nulls last, id desc;
