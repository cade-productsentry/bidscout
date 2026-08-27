// GET /bids/ — every state and trade with open federal bids, rendered from Neon.
import { type Env, cached, page, subscribeForm, openCounts, stateName, STATES, TRADES, esc, todayLong } from '../_lib/site';

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(ctx, async () => {
  const counts = await openCounts(ctx.env);
  const byState = new Map<string, number>();
  const byTrade = new Map<string, number>();
  let total = 0;
  for (const c of counts) {
    total += c.n;
    if (c.state && STATES[c.state]) byState.set(c.state, (byState.get(c.state) ?? 0) + c.n);
    if (TRADES[c.trade]) byTrade.set(c.trade, (byTrade.get(c.trade) ?? 0) + c.n);
  }
  const states = [...byState.entries()].sort((a, b) => stateName(a[0]).localeCompare(stateName(b[0])));
  return page({
    title: 'Open federal bids for trade contractors, by state and trade — BidScout',
    description: `${total} open federal solicitations for HVAC, electrical, roofing, painting, site work, landscaping, janitorial and general building contractors, organised by state. Updated ${todayLong()}.`,
    canonical: '/bids/',
    body: `
<h1>Open federal bids for trade contractors</h1>
<p class="lead">${total} open solicitations right now across ${states.length} states, pulled from SAM.gov and sorted by trade. Pick your state to see what's bidding.</p>
${subscribeForm()}
<h2>By state</h2>
<ul class="grid">${states.map(([code, n]) => `<li><a href="/bids/${code.toLowerCase()}/">${esc(stateName(code))}</a> <small>(${n})</small></li>`).join('')}</ul>
<h2>By trade (all states)</h2>
<ul class="grid">${Object.entries(TRADES).map(([slug, label]) => `<li><a href="/bids/trade/${slug}/">${esc(label)}</a> <small>(${byTrade.get(slug) ?? 0})</small></li>`).join('')}</ul>`,
  });
});
