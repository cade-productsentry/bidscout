// Build-time access to the bids table.
//
// The SEO pages under /bids/ are generated statically at build time from
// whatever is in Neon right now. The deploy workflow passes DATABASE_URL into
// the build; if it is missing (local dev without a connection string) every
// query returns an empty list so the site still builds.

import { neon } from '@neondatabase/serverless';

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

let cache: Promise<BidRow[]> | null = null;

/** All open bids (due in the future, or no due date but updated in the last 30 days). */
export function openBids(): Promise<BidRow[]> {
  if (cache) return cache;
  const url = import.meta.env.DATABASE_URL ?? process.env.DATABASE_URL;
  if (!url) {
    console.warn('[bidscout] DATABASE_URL not set — building /bids/ pages empty');
    cache = Promise.resolve([]);
    return cache;
  }
  const sql = neon(url);
  cache = sql`
    SELECT id, title, agency, trade, naics, state, city, notice_type, set_aside,
           posted_at::text, due_at::text, url, left(raw_text, 600) AS raw_text
    FROM bids
    WHERE trade IS NOT NULL
      AND (due_at > now() OR (due_at IS NULL AND updated_at > now() - interval '30 days'))
    ORDER BY due_at NULLS LAST, posted_at DESC
  `.then((rows) => rows as BidRow[]);
  return cache;
}

export function stateName(code: string): string {
  return STATES[code] ?? code;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return 'TBD';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

/** First ~220 characters of the notice body, cleaned up for a listing. */
export function blurb(text: string | null): string {
  if (!text) return '';
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length > 220 ? t.slice(0, 217).replace(/\s+\S*$/, '') + '…' : t;
}
