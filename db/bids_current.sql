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
-- AMENDMENT 2026-09-29: the key now also strips a TRAILING amendment suffix
-- ("- Amendment 01", "| Amendment 002", "_Amendment 0001", " AMENDMENT 02",
-- " AMD 1") and collapses runs of internal whitespace to a single space.
-- SAM.gov contracting officers publish some amendments as a new notice whose
-- TITLE has the amendment number appended, which the 09-17 key saw as a
-- different solicitation entirely. Found 09-29 while refreshing counts for the
-- wave-7 batch A follow-ups: TX hvac-plumbing read 6 open and was really 5, the
-- extra row being "... Boiler Chiller Plant Amendment 0002" sitting beside its
-- own base notice, same agency, same 2026-10-07 deadline.
--
-- MEASURED over the whole 3,567-row bids table, not just the open set: the new
-- key merges 14 rows across 11 groups (12 rows from the amendment suffix, 2 from
-- the whitespace collapse), and every one of the 11 is a single state, a single
-- trade and a closing-date span of 14 days or less - i.e. one solicitation whose
-- deadline moved, which is exactly what this view exists to collapse.
--
-- Over-merge check re-run on the new key and compared against the OLD key on the
-- same data, which is the comparison that matters: multi-row groups 902 -> 908,
-- groups spanning more than 45 days of closing dates 13 -> 13, groups spanning
-- more than one state 3 -> 3, and the three multi-state groups are the SAME three
-- (589A7-21-132 Emergency Power KS/MO, 550-27-111 B58 Roof Repair IL/WI, and the
-- CT/ME industry-days notice). The new rule therefore adds no over-merge risk of
-- its own; it only removes double counts.
--
-- NOT extended to a bare trailing number or to "Rev 2"/"REVISED": no measured
-- instance, and a trailing integer is a building number or a phase as often as
-- it is an amendment. Extend only against a measurement, as here.

create or replace view bids_current as
select distinct on (
         lower(btrim(regexp_replace(
           regexp_replace(
             regexp_replace(coalesce(title,''), '^[A-Z0-9]{1,6}--', ''),
             '[[:space:]|,._-]*(amendment|amd)[ _#-]*[0-9]+[[:space:]]*$', '', 'i'),
           '[[:space:]]+', ' ', 'g'))),
         coalesce(agency,'')
       )
       *
from bids
order by lower(btrim(regexp_replace(
           regexp_replace(
             regexp_replace(coalesce(title,''), '^[A-Z0-9]{1,6}--', ''),
             '[[:space:]|,._-]*(amendment|amd)[ _#-]*[0-9]+[[:space:]]*$', '', 'i'),
           '[[:space:]]+', ' ', 'g'))),
         coalesce(agency,''),
         posted_at desc nulls last,
         id desc;
