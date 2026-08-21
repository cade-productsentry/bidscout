-- 001: fields needed by the SAM.gov source. Idempotent.
ALTER TABLE bids ADD COLUMN IF NOT EXISTS source_id   text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS naics       text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS city        text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS notice_type text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS set_aside   text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS poc_email   text;
ALTER TABLE bids ADD COLUMN IF NOT EXISTS updated_at  timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS bids_source_source_id_idx ON bids (source, source_id);
CREATE INDEX IF NOT EXISTS bids_updated_at_idx ON bids (updated_at);
