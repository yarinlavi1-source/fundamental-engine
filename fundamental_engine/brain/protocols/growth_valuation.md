# Two valuation lanes: when does a growth stock deserve a premium?

A base-case DCF values mature, profitable firms well (numbers dominate). For young,
hypergrowth or inflecting firms it almost always says "expensive", because much of the
value sits in a right tail. Yarin's requirement: distinguish a stock that deserves a
premium ("הנחה") despite failing a conventional valuation from one that is simply
expensive — and value large profitable firms (e.g. Nvidia) conventionally.

## Lane choice (automatic inside plain_verdict)

- **intrinsic**: mature_growth/mature_stable/decline, or high_growth that is already
  reliably profitable (operating margin >= 10%, positive FCF, profitable 3 years).
  Anchor = base-case value; above bull = expensive.
- **potential**: start_up/young_growth, unprofitable high growth, or an inflection flag.
  Anchor = probability-weighted value INCLUDING an executed `tail` scenario, and the
  premium is allowed only when expectation momentum is strong.

## What to supply for the potential lane

1. `valuation_case` with bear/base/bull AND a `tail` scenario: the large-outcome case
   with explicit drivers (market size, share, capacity, funding, dilution). Executed by
   value_company like the others. Never a hand-typed price.
2. `scenario_probabilities` + `probability_rationale`. Anchor the tail probability in
   base rates (McKinsey/Mauboussin: few firms sustain >20% growth); single-digit % is
   typical. Without them the engine uses labelled uncalibrated defaults and lowers
   confidence.
3. `recent_quarters` (8 standalone quarters), and when available
   `expectations_track` (actual vs prior guidance/consensus, raises) and
   `estimate_revisions` (dated next-year consensus). These produce the momentum
   signals: acceleration, margin expansion, beats/raises, revisions, gross margin.
4. `minority_share` when part of the operating company belongs to others.

## Verdicts (potential lane)

- 🔴 weak momentum → "expensive without evidence", whatever the story.
- 🟢 price <= base → growth stock at a reasonable price.
- 🟢 price <= probability-weighted value and strong momentum → "expensive on paper,
  but a justified growth bet" (size it as a bet: loss is possible).
- 🟡 same price zone, neutral momentum → possible bet, needs more proof.
- 🟠/🔴 price between weighted value and tail → paying today for the tail.
- 🔴 price above tail → expensive even as a bet.

Also reported: analyst-style multiple view (scenario EPS in 2030 x 15/25/40, the way
most price targets are built — a bet on future sentiment, not value) and the reverse
check (revenue needed in ~5 years for today's price at the base margin and 25x exit).

## Honesty

Palantir test (examples/real/pltr_momentum.json, point-in-time): Feb-2023 momentum
reads weak (growth was decelerating; the engine would have missed the first leg);
Aug-2024 reads strong (acceleration + margin expansion) before the largest move. This
is one case, not a backtest. No signal predicts multiple expansion or guarantees a
multi-bagger; momentum can reverse. Never convert a green lane verdict into an order.
