-- 002: where a bid's state came from. Idempotent.
--
-- geo.infer_state() already knows how it reached a state ("pop" straight from
-- SAM's placeOfPerformance, "zip", "address", "city", "title-prefix",
-- "title-name", "name"). Until now that was thrown away, so nothing downstream
-- could tell a state SAM stated from a state we guessed out of prose. Anything
-- making a specific geographic claim to a contractor ("one open bid in your
-- state right now") should only use a corroborated state.
ALTER TABLE bids ADD COLUMN IF NOT EXISTS state_method text;
CREATE INDEX IF NOT EXISTS bids_state_method_idx ON bids (state_method);
