# Widget straddle

Sell an at-the-money call and an at-the-money put on the same widget expiry when
implied volatility is in the top decile of its trailing year. The position is
short volatility: it profits when realised movement is smaller than the price
paid for it.

Exit at fifty percent of maximum profit, or at twenty-one days to expiry,
whichever comes first. Do not hold a short straddle through a scheduled
earnings release.

The dominant risk near expiry is gamma. A widget that pins the strike leaves the
position with an exploding delta and no time to hedge it.
