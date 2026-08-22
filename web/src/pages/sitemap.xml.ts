import type { APIRoute } from 'astro';
import { openBids, TRADES, STATES } from '../lib/db';

export const GET: APIRoute = async () => {
  const site = 'https://bidscout.pages.dev';
  const bids = await openBids();
  const urls = new Set<string>(['/', '/bids/']);
  for (const t of Object.keys(TRADES)) urls.add(`/bids/trade/${t}/`);
  for (const b of bids) {
    if (!b.state || !STATES[b.state]) continue;
    urls.add(`/bids/${b.state.toLowerCase()}/`);
    if (b.trade && TRADES[b.trade]) urls.add(`/bids/${b.state.toLowerCase()}/${b.trade}/`);
  }
  const today = new Date().toISOString().slice(0, 10);
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    [...urls].map((u) => `  <url><loc>${site}${u}</loc><lastmod>${today}</lastmod><changefreq>daily</changefreq></url>`).join('\n') +
    `\n</urlset>\n`;
  return new Response(body, { headers: { 'Content-Type': 'application/xml' } });
};
