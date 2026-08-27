// GET /bids/trade/{trade}/ — one trade, every state (including notices with no state).
import { type Env, cached, page, notFound, subscribeForm, bidList, openBids, TRADES, esc } from '../../_lib/site';

export const onRequestGet: PagesFunction<Env> = (ctx) => cached(ctx, async () => {
  const trade = String(ctx.params.trade ?? '').toLowerCase();
  if (!TRADES[trade]) return notFound(`"${ctx.params.trade}" is not a trade we track.`);
  const bids = await openBids(ctx.env, undefined, trade);
  const label = TRADES[trade];
  return page({
    title: `${label} government bids — ${bids.length} open federal solicitations — BidScout`,
    description: `Every open federal ${label.toLowerCase()} solicitation on SAM.gov right now (${bids.length}), with due dates, agencies and set-asides. Filter by state or get a free weekly digest.`,
    canonical: `/bids/trade/${trade}/`,
    noindex: bids.length === 0,
    body: `
<h1>${esc(label)} government bids</h1>
<p class="lead">${bids.length} open federal ${esc(label.toLowerCase())} solicitations nationwide. Notices with no place of performance listed are included here but not on state pages.</p>
${subscribeForm(trade)}
${bidList(bids, true)}
<p><a href="/bids/">← All states and trades</a></p>`,
  });
});
