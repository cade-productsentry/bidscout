// GET /bids/{state}/ — all open bids in one state, grouped by trade.
import { type Env, cached, page, notFound, subscribeForm, bidList, openBids, stateName, STATES, TRADES, esc } from '../../_lib/site';

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(ctx, async () => {
  const code = String(ctx.params.state ?? '').toUpperCase();
  if (!STATES[code]) return notFound(`"${ctx.params.state}" is not a state we track.`);
  const bids = await openBids(ctx.env, code);
  const name = stateName(code);
  const byTrade = new Map<string, number>();
  for (const b of bids) if (b.trade) byTrade.set(b.trade, (byTrade.get(b.trade) ?? 0) + 1);
  const trades = Object.entries(TRADES).filter(([slug]) => byTrade.has(slug));
  return page({
    title: `Open government bids in ${name} for trade contractors (${bids.length}) — BidScout`,
    description: `${bids.length} open federal solicitations in ${name} for HVAC, electrical, roofing, painting, site work, landscaping, janitorial and general building contractors. Due dates, agencies and set-asides, updated daily.`,
    canonical: `/bids/${code.toLowerCase()}/`,
    noindex: bids.length === 0,
    body: `
<h1>Open government bids in ${esc(name)}</h1>
<p class="lead">${bids.length} open federal solicitation${bids.length === 1 ? '' : 's'} with a place of performance in ${esc(name)}. Narrow it to your trade, or get the weekly digest for your trade and state.</p>
${subscribeForm('', code)}
<h2>By trade in ${esc(name)}</h2>
<ul class="grid">${trades.map(([slug, label]) => `<li><a href="/bids/${code.toLowerCase()}/${slug}/">${esc(label)}</a> <small>(${byTrade.get(slug)})</small></li>`).join('')}</ul>
<h2>All open bids in ${esc(name)}</h2>
${bidList(bids, false, true)}
<p><a href="/bids/">← All states</a></p>`,
  });
});
