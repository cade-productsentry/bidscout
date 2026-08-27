// GET /bids/{state}/{trade}/ — the money page: one trade in one state.
import { type Env, cached, page, notFound, subscribeForm, bidList, openBids, stateName, STATES, TRADES, esc } from '../../_lib/site';

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(ctx, async () => {
  const code = String(ctx.params.state ?? '').toUpperCase();
  const trade = String(ctx.params.trade ?? '').toLowerCase();
  if (!STATES[code]) return notFound(`"${ctx.params.state}" is not a state we track.`);
  if (!TRADES[trade]) return notFound(`"${ctx.params.trade}" is not a trade we track.`);
  const bids = await openBids(ctx.env, code, trade);
  const name = stateName(code);
  const label = TRADES[trade];
  return page({
    title: `${label} bids in ${name} — ${bids.length} open federal solicitation${bids.length === 1 ? '' : 's'} — BidScout`,
    description: `Open federal ${label.toLowerCase()} solicitations in ${name}: ${bids.length} right now. Due dates, contracting agency, set-asides and a plain-English note on each. Free weekly digest.`,
    canonical: `/bids/${code.toLowerCase()}/${trade}/`,
    noindex: bids.length === 0,
    body: `
<h1>${esc(label)} bids in ${esc(name)}</h1>
<p class="lead">${bids.length} open federal ${esc(label.toLowerCase())} solicitation${bids.length === 1 ? '' : 's'} in ${esc(name)}, from SAM.gov. Most are small-business set-asides a local shop can win.</p>
${subscribeForm(trade, code)}
${bidList(bids)}
<p><a href="/bids/${code.toLowerCase()}/">← All trades in ${esc(name)}</a> · <a href="/bids/trade/${trade}/">${esc(label)} bids in every state →</a></p>`,
  });
});
