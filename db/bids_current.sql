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
-- AMENDMENT 2026-09-17: the key now strips a leading SAM.gov classification-code
-- prefix ("N--", "Z1DA--", "Y--" ...) and lowercases the title before grouping.
-- Interior/NPS/FWS and VA notices are frequently published twice, once bare and
-- once prefixed with the PSC code, which the bare (title, agency) key treated as
-- two solicitations. On 2026-09-17 that left 13 groups still double counted: only
-- ~2% of the open set, but concentrated enough to matter in a single state and
-- trade, where AK electrical showed "2 open bids" that were one job listed twice.
-- Only the GROUPING key is normalized; the surviving row's own title is displayed
-- unchanged. Over-merge check re-run on the new key: 214 multi-row groups, zero
-- spanning more than 45 days of closing dates, and one spanning two states, which
-- is the SAME pre-existing group the old key already merged (Z1DA--550-27-111 B58
-- Roof Repair, VA NCO 12, tagged IL on one row and WI on the other by the geo
-- inference). So the new key adds no over-merge risk of its own.
--
-- Over-merge check run 2026-09-16 against open bids: zero title+agency groups
-- spanned more than 45 days of closing dates, and zero spanned more than one
-- state, i.e. no sign of genuinely distinct solicitations being collapsed.
-- Re-run those two checks if this key ever looks suspect. The residual risk
-- undercounts, which is the safe direction for claims we put in commercial email.
create or replace view bids_current as
select distinct on (
         lower(regexp_replace(coalesce(title,''), '^[A-Z0-9]{1,6}--', '')),
         coalesce(agency,'')
       )
       *
from bids
order by lower(regexp_replace(coalesce(title,''), '^[A-Z0-9]{1,6}--', '')),
         coalesce(agency,''),
         posted_at desc nulls last,
         id desc;
