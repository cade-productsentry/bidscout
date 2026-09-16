// Shared code for the server-rendered /bids/ pages.
//
// Why these pages are Pages Functions and not static Astro output: the deploy
// workflow builds the site without DATABASE_URL, so the static build has no
// bid data and Astro emits no /bids/{state}/{trade} pages at all. The
// workflow file cannot be changed from the operations sandbox (the PAT has no
// "workflow" scope), but Cloudflare Pages already holds DATABASE_URL for the
// /subscribe function. So the SEO pages render here, at request time, from
// the same database, and are cached at the edge for an hour.
//
// Functions take precedence over static assets for the routes they define,
// so the (empty) static /bids/ index from the Astro build is superseded too.

import { neon } from '@neondatabase/serverless';

export interface Env {
  DATABASE_URL: string;
  ASSETS: { fetch: (req: Request) => Promise<Response> };
}

export interface BidRow {
  id: number;
  title: string;
  agency: string | null;
  trade: string | null;
  naics: string | null;
  state: string | null;
  city: string | null;
  notice_type: string | null;
  set_aside: string | null;
  posted_at: string | null;
  due_at: string | null;
  url: string;
  raw_text: string | null;
}

export const SITE = 'https://bidscout.pages.dev';
export const CACHE_SECONDS = 3600;

export const TRADES: Record<string, string> = {
  'hvac-plumbing': 'HVAC / Plumbing / Mechanical',
  electrical: 'Electrical',
  roofing: 'Roofing',
  painting: 'Painting',
  'site-work': 'Paving / Fencing / Site work',
  landscaping: 'Landscaping / Grounds',
  janitorial: 'Janitorial / Custodial',
  'general-building': 'General building construction',
};

export const STATES: Record<string, string> = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California', CO: 'Colorado',
  CT: 'Connecticut', DE: 'Delaware', DC: 'District of Columbia', FL: 'Florida', GA: 'Georgia',
  HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois', IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky',
  LA: 'Louisiana', ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota',
  MS: 'Mississippi', MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire',
  NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York', NC: 'North Carolina', ND: 'North Dakota',
  OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina',
  SD: 'South Dakota', TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia',
  WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming', PR: 'Puerto Rico', GU: 'Guam',
};

const OPEN_WHERE = `trade IS NOT NULL AND (due_at > now() OR (due_at IS NULL AND updated_at > now() - interval '30 days'))`;
const COLS = `id, title, agency, trade, naics, state, city, notice_type, set_aside, posted_at::text, due_at::text, url, left(raw_text, 600) AS raw_text`;

/** Open bids, optionally narrowed to a state code and/or trade slug. */
export async function openBids(env: Env, state?: string, trade?: string): Promise<BidRow[]> {
  const sql = neon(env.DATABASE_URL);
  let rows: Record<string, unknown>[];
  if (state && trade) {
    rows = await sql.query(`SELECT ${COLS} FROM bids_current WHERE ${OPEN_WHERE} AND state = $1 AND trade = $2 ORDER BY due_at NULLS LAST, posted_at DESC`, [state, trade]);
  } else if (state) {
    rows = await sql.query(`SELECT ${COLS} FROM bids_current WHERE ${OPEN_WHERE} AND state = $1 ORDER BY due_at NULLS LAST, posted_at DESC`, [state]);
  } else if (trade) {
    rows = await sql.query(`SELECT ${COLS} FROM bids_current WHERE ${OPEN_WHERE} AND trade = $1 ORDER BY due_at NULLS LAST, posted_at DESC`, [trade]);
  } else {
    rows = await sql.query(`SELECT ${COLS} FROM bids_current WHERE ${OPEN_WHERE} ORDER BY due_at NULLS LAST, posted_at DESC`);
  }
  return rows as unknown as BidRow[];
}

/** Counts per (state, trade) for index pages and the sitemap: one cheap query. */
export async function openCounts(env: Env): Promise<{ state: string | null; trade: string; n: number }[]> {
  const sql = neon(env.DATABASE_URL);
  const rows = await sql.query(`SELECT state, trade, count(*)::int AS n FROM bids_current WHERE ${OPEN_WHERE} GROUP BY state, trade`);
  return rows as unknown as { state: string | null; trade: string; n: number }[];
}

export function stateName(code: string): string {
  return STATES[code] ?? code;
}

export function esc(s: unknown): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function fmtDate(iso: string | null): string {
  if (!iso) return 'TBD';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

export function blurb(text: string | null): string {
  if (!text) return '';
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length > 220 ? t.slice(0, 217).replace(/\s+\S*$/, '') + '…' : t;
}

export function todayLong(): string {
  return new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

const CSS = `
:root { color-scheme: light dark; --accent: #1f6feb; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; line-height: 1.55; }
a { color: var(--accent); }
nav.top { font-size: .9rem; margin-bottom: 2rem; display: flex; gap: 1rem; align-items: baseline; }
nav.top .brand { font-weight: 700; letter-spacing: .02em; opacity: .8; text-decoration: none; color: inherit; }
h1 { font-size: 1.8rem; line-height: 1.2; margin: .5rem 0 .75rem; }
p.lead { font-size: 1.05rem; opacity: .85; margin-top: 0; }
form.sub { display: grid; gap: .6rem; margin: 1.5rem 0; padding: 1rem 1.1rem; border: 1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius: .75rem; }
form.sub .row { display: grid; grid-template-columns: 1fr auto; gap: .6rem; }
input, select, button { font: inherit; padding: .6rem .75rem; border: 1px solid color-mix(in srgb, currentColor 35%, transparent); border-radius: .45rem; background: transparent; color: inherit; width: 100%; }
button { cursor: pointer; font-weight: 600; background: var(--accent); color: #fff; border-color: var(--accent); white-space: nowrap; }
small.fine { opacity: .65; }
#status { min-height: 1.2rem; margin: 0; font-weight: 500; }
h2 { font-size: 1.2rem; margin: 2rem 0 .5rem; }
ul.bids { list-style: none; padding: 0; }
ul.bids li { padding: .9rem 0; border-top: 1px solid color-mix(in srgb, currentColor 15%, transparent); }
ul.bids li:last-child { border-bottom: 1px solid color-mix(in srgb, currentColor 15%, transparent); }
.meta { font-size: .88rem; opacity: .75; margin: .15rem 0 .3rem; }
.meta b { font-weight: 600; opacity: 1; }
.blurb { font-size: .92rem; opacity: .85; margin: 0; }
.tag { display: inline-block; font-size: .75rem; padding: .05rem .45rem; border-radius: .4rem; border: 1px solid color-mix(in srgb, currentColor 30%, transparent); margin-left: .35rem; vertical-align: middle; }
ul.grid { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: .4rem .9rem; }
footer { margin-top: 3rem; font-size: .85rem; opacity: .65; }
@media (max-width: 520px) { form.sub .row { grid-template-columns: 1fr; } }
`;

const SCRIPT = `
const form = document.getElementById('subscribe');
if (form) {
  const status = document.getElementById('status');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    status.textContent = 'Submitting…';
    const payload = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch('/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json();
      status.textContent = response.ok ? "You're on the list. First digest arrives within a week." : 'Something went wrong: ' + (result.error ?? response.status);
      if (response.ok) form.reset();
    } catch { status.textContent = 'Network error, please try again.'; }
  });
}
`;

export interface PageOpts {
  title: string;
  description: string;
  canonical: string;
  body: string;
  noindex?: boolean;
  status?: number;
}

export function page(o: PageOpts): Response {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${esc(o.title)}</title>
<meta name="description" content="${esc(o.description)}" />
<link rel="canonical" href="${SITE}${esc(o.canonical)}" />
${o.noindex ? '<meta name="robots" content="noindex,follow" />' : ''}
<style>${CSS}</style>
</head>
<body>
<nav class="top"><a class="brand" href="/">BIDSCOUT</a><a href="/bids/">All open bids</a><a href="/sample-triage/">Sample triage</a></nav>
${o.body}
<footer>BidScout · hello via <a href="mailto:cade.craft.land@gmail.com?subject=BidScout">email</a> · Bid data is sourced from public SAM.gov notices and refreshed several times a day. BidScout is not affiliated with any government agency. Always confirm details on the official notice before bidding.</footer>
<script>${SCRIPT}</script>
</body>
</html>
`;
  return new Response(html, {
    status: o.status ?? 200,
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': `public, max-age=300, s-maxage=${CACHE_SECONDS}`,
    },
  });
}

export function notFound(what: string): Response {
  return page({
    title: 'Not found — BidScout',
    description: 'That page does not exist.',
    canonical: '/bids/',
    noindex: true,
    status: 404,
    body: `<h1>Not found</h1><p class="lead">${esc(what)}</p><p><a href="/bids/">Browse all open bids by state and trade</a></p>`,
  });
}

export function subscribeForm(trade = '', state = ''): string {
  const opt = (v: string, l: string, sel: string) => `<option value="${esc(v)}"${v === sel ? ' selected' : ''}>${esc(l)}</option>`;
  return `<form id="subscribe" class="sub">
  <strong>Get these bids in your inbox every Monday — free.</strong>
  <div class="row">
    <input type="email" name="email" placeholder="you@yourcompany.com" required autocomplete="email" aria-label="Work email" />
    <button type="submit">Send me the weekly digest</button>
  </div>
  <div class="row" style="grid-template-columns:1fr 1fr">
    <select name="trade" required aria-label="Trade"><option value="">Trade…</option>${Object.entries(TRADES).map(([v, l]) => opt(v, l, trade)).join('')}</select>
    <select name="state" required aria-label="State"><option value="">State…</option>${Object.entries(STATES).map(([v, l]) => opt(v, l, state)).join('')}</select>
  </div>
  <small class="fine">One email a week. Unsubscribe with one click.</small>
  <p id="status" role="status"></p>
</form>`;
}

export function bidList(bids: BidRow[], showState = false, showTrade = false): string {
  if (!bids.length) return `<p class="lead">No open notices in this list right now. Check back tomorrow, or subscribe above and we will send new ones as they post.</p>`;
  return `<ul class="bids">${bids.map((b) => {
    const meta: string[] = [`<b>Due ${esc(fmtDate(b.due_at))}</b>`];
    if (b.agency) meta.push(esc(b.agency));
    if (showState && b.state) meta.push(esc(stateName(b.state)) + (b.city ? `, ${esc(b.city)}` : ''));
    if (!showState && b.city) meta.push(esc(b.city));
    if (showTrade && b.trade) meta.push(esc(TRADES[b.trade] ?? b.trade));
    if (b.notice_type) meta.push(esc(b.notice_type));
    const text = blurb(b.raw_text);
    return `<li><a href="${esc(b.url)}" rel="nofollow noopener" target="_blank">${esc(b.title)}</a>${b.set_aside ? `<span class="tag">${esc(b.set_aside)}</span>` : ''}<div class="meta">${meta.join(' · ')}</div>${text ? `<p class="blurb">${esc(text)}</p>` : ''}</li>`;
  }).join('\n')}</ul>`;
}

/** Edge-cache wrapper: serve from the Cloudflare cache for CACHE_SECONDS, otherwise render. */
export async function cached(ctx: EventContext<Env, string, unknown>, render: () => Promise<Response>): Promise<Response> {
  const cache = (caches as unknown as { default: Cache }).default;
  const key = new Request(new URL(ctx.request.url).toString(), { method: 'GET' });
  try {
    const hit = await cache.match(key);
    if (hit) return hit;
  } catch { /* cache unavailable (local dev) */ }
  let res: Response;
  try {
    res = await render();
  } catch (err) {
    console.error('render failed', err);
    return page({
      title: 'Temporarily unavailable — BidScout',
      description: 'Bid listings are temporarily unavailable.',
      canonical: '/bids/',
      noindex: true,
      status: 503,
      body: `<h1>Bid listings are temporarily unavailable</h1><p class="lead">The database did not answer. Try again in a minute.</p>`,
    });
  }
  if (res.status === 200) {
    try { ctx.waitUntil(cache.put(key, res.clone())); } catch { /* ignore */ }
  }
  return res;
}
