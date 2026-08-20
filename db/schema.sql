-- BidScout database schema
-- Apply with:  psql "$DATABASE_URL" -f db/schema.sql
-- Safe to re-run: every statement is idempotent.

CREATE TABLE IF NOT EXISTS bids (
    id          bigserial PRIMARY KEY,
    source      text        NOT NULL,
    agency      text,
    title       text        NOT NULL,
    trade       text,
    state       text,
    county      text,
    posted_at   timestamptz,
    due_at      timestamptz,
    url         text        UNIQUE,
    raw_text    text,
    summary     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bids_trade_state_idx ON bids (trade, state);
CREATE INDEX IF NOT EXISTS bids_due_at_idx      ON bids (due_at);

CREATE TABLE IF NOT EXISTS subscribers (
    id          bigserial PRIMARY KEY,
    email       text        NOT NULL UNIQUE,
    trade       text,
    state       text,
    tier        text        NOT NULL DEFAULT 'free',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clients (
    id                  bigserial PRIMARY KEY,
    name                text        NOT NULL,
    email               text        NOT NULL UNIQUE,
    trade               text,
    state               text,
    stripe_customer_id  text,
    onboarding_json     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);
