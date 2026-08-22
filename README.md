# BidScout

BidScout — open government bids for small trade contractors (HVAC, electrical,
roofing, painting, site work, landscaping, janitorial, general building), filtered
by trade and state and triaged weekly. Every service used here is on a free tier.

**Status (2026-08-22):** scraper is live against SAM.gov (federal notices, no API
key needed) and writes to Neon; landing page with digest signup is live;
`/bids/{state}/{trade}/` SEO pages (≈230) are generated from the database at
build time and rebuilt twice a day; `scraper/digest.py` renders the weekly
digest text. Digest sending (from the ops mailbox) and triage summaries are next.

## Layout

| Folder | What it is | Where it runs |
| --- | --- | --- |
| `scraper/` | Python 3.12 job that pulls open bids from SAM.gov into Postgres (stdlib only; talks to Neon over HTTPS) | GitHub Actions (cron) |
| `web/` | Astro static site + email capture form | Cloudflare Pages |
| `web/functions/` | Cloudflare Pages Function that writes to Postgres | Cloudflare's edge |
| `db/` | `schema.sql` — the database tables | Neon Postgres |

## How the pieces fit together

```
GitHub Actions (every 6h)  ──runs──>  scraper/main.py  ──writes──>  ┐
                                                                    ├──> Neon Postgres
Browser ──POST /subscribe──> Cloudflare Pages Function ──writes──>  ┘
```

There is no API server. The only dynamic endpoint is the single Pages Function.

---

## 1. Database — Neon

Neon's free tier gives one Postgres project. Apply the schema:

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

`schema.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`), so re-running it is safe.

The same connection string is stored in two places:

- **GitHub → Settings → Secrets and variables → Actions →** secret `DATABASE_URL`
- **Cloudflare → Pages project → Settings → Environment variables →** `DATABASE_URL`

## 2. Scraper — GitHub Actions

`.github/workflows/scrape.yml` runs `scraper/main.py --days 3` on a `0 */6 * * *`
cron (every 6 hours) and on manual `workflow_dispatch`. It reads `DATABASE_URL`
from repo secrets.

Sources live in `scraper/sources/`. `sam_gov.py` uses the public JSON endpoints
behind sam.gov's search UI (`/api/prod/sgs/v1/search/` + `/api/prod/opps/v2/opportunities/{id}`),
filtered to the NAICS codes in `sam_gov.TRADES`, notice types that can still be
bid on (no award notices), and modified within `--days`. Notices already refreshed
in the last 24h are skipped so runs stay short. Flags: `--dry-run`, `--limit N`, `--days N`.

Schema changes go in `db/migrations/NNN_*.sql` (idempotent) and are applied by hand
through Neon's HTTPS SQL endpoint; `db/schema.sql` is the original base.

`scraper/digest.py` renders the weekly digest (one per subscriber, or
`--trade X --state YY` for a preview) from the same table. It only prints text;
the operator sends it from the BidScout mailbox.

Run the scraper by hand from the repo's **Actions** tab → *scrape* → **Run workflow**.

Local development:

```bash
cd scraper
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env      # then paste your real connection string in
python main.py
```

> Note: scheduled workflows on free GitHub are paused after 60 days of no repo
> activity, and cron start times can drift under load. Neither matters for a
> placeholder job.

## 3. Website — Cloudflare Pages

The Pages project is connected to this repo, so **any push to `main`
auto-deploys**. Build settings:

| Setting | Value |
| --- | --- |
| Root directory | `web` |
| Build command | `npm run build` |
| Output directory | `dist` |

Cloudflare picks up `web/functions/` automatically and turns each file into a
route — `functions/subscribe.ts` becomes `POST /subscribe`.

The form on the index page posts `{ email, trade, state }` there as JSON, and the
function inserts a row into `subscribers`. Re-subscribing with the same email
updates the stored trade/state rather than erroring.

### Programmatic SEO pages

`web/src/lib/db.ts` queries Neon **at build time** (the deploy workflow passes
`DATABASE_URL` into `npm run build`) and `src/pages/bids/**` turns the rows into
static pages: `/bids/` (index), `/bids/tx/` (state), `/bids/tx/electrical/`
(state × trade) and `/bids/trade/electrical/` (trade, all states), plus
`/sitemap.xml` and `robots.txt`. Without `DATABASE_URL` the pages build empty.
The deploy workflow also runs on a 05:30/17:30 UTC schedule so listings and
due dates stay fresh between code pushes.

### Why the Neon serverless driver?

Cloudflare Workers cannot open raw TCP sockets, so a normal Postgres client
(`node-postgres`, `psycopg`) will not run there. `@neondatabase/serverless` talks
to Neon over plain HTTPS instead, which works inside a Worker.

Local preview of the site (static only, the function will not run):

```bash
cd web
npm install
npm run dev
```

## Deployed URLs

- Repo: https://github.com/cade-productsentry/bidscout
- Site: https://bidscout.pages.dev
