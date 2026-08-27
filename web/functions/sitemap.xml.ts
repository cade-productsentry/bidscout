// GET /sitemap.xml — built from live counts so every state/trade page with bids is listed.
import { type Env, cached, openCounts, SITE, STATES, TRADES } from './_lib/site';

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(ctx, async () => {
  const counts = await openCounts(ctx.env);
  const urls = new Set<string>(['/', '/bids/', '/sample-triage/']);
  for (const t of Object.keys(TRADES)) urls.add(`/bids/trade/${t}/`);
  for (const c of counts) {
    if (!c.state || !STATES[c.state] || c.n <= 0) continue;
    urls.add(`/bids/${c.state.toLowerCase()}/`);
    if (TRADES[c.trade]) urls.add(`/bids/${c.state.toLowerCase()}/${c.trade}/`);
  }
  const today = new Date().toISOString().slice(0, 10);
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    [...urls].map((u) => `  <url><loc>${SITE}${u}</loc><lastmod>${today}</lastmod><changefreq>daily</changefreq></url>`).join('\n') +
    `\n</urlset>\n`;
  return new Response(body, { headers: { 'Content-Type': 'application/xml', 'Cache-Control': 'public, max-age=300, s-maxage=3600' } });
});
