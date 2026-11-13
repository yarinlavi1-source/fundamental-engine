# SPCX (SpaceX) model lane, 13.11.2026 — Claude's own valuation

Price $140.66 (IBKR pre-market 13.11.2026 09:36 UTC; 12.11 close $139.94). Files: model.py (custom segment model),
mkparams.py -> params.json, results.json, side.py -> side.json. Inputs: filings.md (10-Q Q3'26, releases, 424B4, 8-Ks)
and market.md. All values $/share on 13,191m economic diluted shares unless stated. Probabilities are Claude's
subjective judgement, not engine odds. Future values are conditional values, not price forecasts.

## 1. Company type and method
| Part | Type | 2026E rev | Method |
|---|---|---|---|
| Connectivity (Starlink) | telecom-like subscription network, still a fast grower (subs +89% y/y, ARPU falling 83->69) | ~$18.4bn | subs x ARPU x hardware factor + ent&gov growth; EBITDA-after-SBC margin; capex % of revenue; D&A from 4-year blended satellite vintages (5y broadband, 3y mobile) |
| Space (launch + NASA/NSSL) | launch infrastructure and government contractor carrying Starship R&D (expensed) | ~$4.8bn | revenue growth; core margin before Starship R&D (77-79% reported 2025/Q3) minus Starship/other R&D; 12-year asset life |
| SI (xAI/Grok, cloud, Cursor, X) | capital-heavy AI cloud (Customer B = 34.5% of group Q3 revenue, 90-day termination) + software (Cursor) + ads (X) | ~$21bn | revenue = yield x average in-service server gross (75% of capex, 5-year life) + Cursor + X; EBITDA-after-SBC margin; buildings 20 years |
| Unproven options | Starship full reuse, direct-to-cell at scale, xAI as a frontier-lab winner, Mars, AI in space, Tesla merger | 0 | separate bets (section 4), never in the base |

Why a custom model and not lean_model: three economics, and the value hinges on SI capital intensity (2026 capex ~3x SI
revenue); lean_model has one margin/conversion and cannot tie revenue to the fleet or D&A to capex vintages (NBIS lesson).
The custom model keeps lean_model's conventions: cost of equity fades from rf+beta*ERP to the mature beta over 10 years,
terminal growth = rf, reinvestment = g/ROIC (ROIC computed from 2032 NOPAT over invested capital), value = average of a DCF
and an exit-P/E path (P/E x 2032 operating EPS + PV of FCF to 2031 + cash), calibrated lens = market multiple held with the
margin floored at the first forecast year.

Discount: rf 4.94%, ERP 4.2% (same as the other 11-12.11 runs). Beta 1.9 (judgement): the locked rule (2-year weekly vs
SPY, adjusted) cannot be applied: 22 weeks since the IPO give raw 3.44 / adjusted 2.63 (corr 0.60), dominated by IPO
volatility; 1.9 sits between an AI-capex name and the TSLA-style Musk premium. Mature beta 1.2. ke0 12.9%. Segment betas
for the sum-of-parts cross-check: Starlink 1.1, Space 1.0, SI 2.2. Sensitivity: beta 1.5 -> $29.6, 2.6 -> $24.5.

Balance sheet (cash at face): net cash 30.09 +$50.9bn (liquid 89.9 - debt+finance leases 39.0, incl. $12.9bn Valor
related-party sale-leasebacks); Q4'26 burn ~$11bn (EBITDA ~9 - capex ~21 + interest) -> end-2026 +$39.5bn, plus BTC
1.57 and equity stakes 0.65 at face as investment assets (restricted cash 2.1 excluded). EchoStar: 261.8m shares in the
count and $8.5bn cash in 2027. Apollo GPU lease (~$40bn) and purchase commitments ($37.8bn, $25.6bn in 2027) are inside
SI capex (unlevered: the lease is funding, not extra value). Shares: 13,581m outstanding - 1,302m Musk restricted Class B
(vest only on Mars colony / 100 TW space compute; votes but no economics in the horizon) + 508m options (Musk's 350m strike
GAP: counted in full) + 142m RSUs + 262m EchoStar = 13,191m. Ordinary SBC (~$4.6bn/yr) is expensed in margins; Cursor
acquisition SBC ($23.4bn over 2.8y) is excluded because those shares are already in the count. Cash tax 8% 2027 rising to
21% from 2030 (NOLs, valuation allowance, bonus depreciation).

## 2. Scenarios (drivers, 2027 -> 2032)
| Driver | Bear | Base | Bull | Evidence |
|---|---|---|---|---|
| Starlink consumer subs, end-year (m) | 19.5 -> 32 | 21 -> 43.5 | 22.5 -> 54 | 13.6m Q3'26, +6.4m y/y; YE26 est 15.2 |
| Consumer ARPU $/month | 62 -> 47 (price war with Amazon Leo, China constellations; emerging-market mix) | 67 -> 60 | 69 -> 66 | 83 Q3'25 -> 66 Q2 -> 69 Q3'26 |
| Ent&gov growth | 25% -> 5% (government shift, Starshield competition) | 40% -> 12% | 50% -> 15% | +91% y/y Q3; Starshield ~$6.45bn awards (May 2026) |
| Starlink EBITDA after SBC / capex % rev | 58->50% / 30->25% (Starship delay keeps Falcon cost) | 62% / 27->20% | 65% / 24->16% | Q3 63.9% / 29%; 2025 37% capex |
| Space revenue growth / Starship+other R&D $bn | 0-3% / 5.2 -> 2.0 (delay, then program slowed) | 10-12% / 4.6 -> 2.5 | 10-20% / 4.4 -> 1.8 | rev 2025 4.1bn; R&D 3.0 (2025), ~4.4 (2026E) |
| SI capex $bn | 75 (committed), then 12-15 (wind-down) | 85, 75, 65, 65, 70, 75 | 95 -> 85 -> 95 | 2026E 57; 2027 commitments 25.6 + Apollo ~40 |
| SI yield ($ revenue per $ avg server gross) | 0.50 -> 0.33 (Customer B leaves on 90 days' notice; capacity re-let at spot) | 0.60 -> 0.48 | 0.68 -> 0.55 | Q3'26 implied ~0.6-1.0 (rich reserved-capacity contracts); NBIS saved model 0.44-0.48 |
| SI EBITDA after SBC | 48% -> 45% | 52% | 56% | Q3 54.7% |
| Cursor revenue 2027 / growth | 5.5 / 15% -> 5% (Claude Code, Codex competition) | 7.0 / 35% -> 12% | 8.5 / 45% -> 15% | $608m in 1.5 months (~$4.9bn run-rate) |
| Exit P/E (2032 EPS) / growth 2033 | 18 / 3% | 28 / 8% | 35 / 10% | judgement: no own history; LMT 21x, TMUS 16x, IRDM 30x GAAP |

Base path ($bn): revenue 2027 94.6 / 2028 133.6 / 2029 165.0 / 2030 191.4 / 2031 206.9 / 2032 215.2. EBIT margin 24% -> 20%
(Starlink 44%; SI 21% -> 12% as the server fleet depreciates; Space ~0 after Starship R&D). Capex 98 -> 92; FCF -60
(2027), -24, +2, +14, +17, +16; net cash turns to ~-$42bn net debt in 2028-29. Operating EPS 1.59 / 1.97 / 2.20 / 2.33 /
2.46 / 2.65. Our 2027 revenue sits near the low end of the FY2027 consensus range ($72-290bn, avg $180bn) because SI
revenue is tied to installed compute: $140bn of SI revenue in 2027 would need ~$200bn+ of average in-service servers at
today's yield, against ~$57bn of servers at the end of 2026 (1.7 GW nameplate).

## 3. Results
| | Bear | Base | Bull |
|---|---|---|---|
| Value today | $2.63 (SOTP with segment betas $7.16) | **$27.61** (DCF 15.19 / P/E path 40.02) | $77.71 |
| End-2027 | 2.71 | 31.38 | 88.87 |
| End-2028 (2-year: the noisiest) | 3.16 | 35.44 | 100.18 |
| End-2031 | 4.42 | 50.54 | 141.24 |
| Annual return to end-2031 from $140.66 | -49.1% | **-18.1%** | +0.1% |
| Subjective probability | 30% | 50% | 20% |
Weighted: today $30.14, end-2031 $53.52, -17.2%/yr. Price for 12%/yr (base): **$28.26**; index parity (9.1%): $32.27.

Two lenses: ours base $27.61 (-18.1%/yr, sell band); calibrated $40.66 (-11.7%/yr, sell band): P/E 35 held (the market's
multiple on FY27 consensus EPS $4.04; our own forward P/E on 2027 operating EPS is 88x and not credible to hold) and the
group EBIT margin floored at 24.1% (2027). Lenses agree: sell band. Buy test: base >= 12% both lenses NO; bull >= 17% NO;
bear >= -10% NO.

Sum of parts (DCF only, today, base): Starlink $228.6bn ($17.33; $21.64 at beta 1.1, ROIC 2032 60%), Space -$19.4bn (-$1.47:
Starship R&D outruns the Falcon/NASA profit inside the horizon), SI -$60.6bn (-$4.60: ROIC 2032 ~6%, below the cost of
capital), cash +$41.0bn ($3.11) -> $14.37 ($18.54 with segment betas). The P/E path ($40) is higher because 28x 2032 EPS
treats SI growth as free, while the DCF charges the 2027-28 capex; the base averages them.

DCF vs P/E reconciliation: the gap ($15 vs $40) is SI reinvestment: 2027-28 FCF of -$84bn buys the 2032 EPS that the P/E
path capitalises; at a terminal ROIC of 11% (group) the DCF credits little value to growth. Both are below the price.

Sensitivity (base, today / return): exit P/E 22 -> $23.2 / -20.8%; 34 -> $32.0 / -15.7%; GPU life 4y -> $16.7 / -25.6%;
SI yield held at 0.60 -> $42.6 / -10.9%; beta 1.5 -> $29.6; 2.6 -> $24.5.

Report 2 (historical-multiple report): own history does not exist (listed 12.06.2026). Direct peers at today's growth,
applied to our 2027 base: Starlink EBITDA $16.7bn x 12/16/20 (IRDM 16.6x GAAP, TMUS 7.8x), Space core EBITDA $3.8bn x
12/14.5/17 (LMT 14.5x) less 3 years of Starship R&D, SI revenue $62.2bn x 3/4.5/6 EV/sales (NBIS ~4.5x), + cash after
EchoStar $33.2bn -> **$34 / $47 / $60**. The stock's own IPO-to-date EV/NTM sales range (9.0-17.0x, median 12.2x) on our
NTM revenue ($88bn, vs consensus $158bn) -> $64 / $86 / $118: even the market's own multiple on our revenue path is below
the price. Both reports say expensive: a consistent signal.

## 4. Separate bets (NOT in the base; value if it happens = extra 2031 NOPAT x 20, discounted 5.13y at 12%)
| Bet | What must happen | Evidence so far | $/share if it happens | Subjective prob. | Expected |
|---|---|---|---|---|---|
| Starship full reuse | ship + booster catch and rapid reuse, orbital refuelling, V3 Starlink at cadence | Flight 14 = first orbital and first revenue flight (26 V3 sats, ~10x F9 capacity); Flight 15 catch test NET 13.11; no ship-to-ship refuelling yet; Mars/HLS dates slipped for years | 5.51 | 40% | 2.20 |
| Direct-to-cell at scale | EchoStar 65 MHz second closing, 800 MHz (1H27), next-gen D2C sats, carrier deals | FCC approved EchoStar 12.05.2026; ~650 D2C sats, T-Mobile partner; AT&T/Verizon/T-Mobile forming their own D2D JV (competition) | 4.83 | 30% | 1.45 |
| xAI/Grok frontier-lab winner | Grok a top-two model, SI ~$250bn at 35% EBIT by 2031 | Grok adoption cited, Customer B cloud contracts; no Grok revenue disclosed; OpenAI/Anthropic/Google competition | 46.80 | 10% | 4.68 |
| Mars | colony of 1m people | 2026 window effectively missed; next late 2028 | 0 (outside horizon; vests 1,000m Musk shares = dilution if it happens) | ~0% | 0 |
| AI in space | orbital data centres at scale | no filed milestone; 100 TW vests 302m Musk shares | not sizeable | ~3% | 0 |
| Tesla merger | special committees agree terms; Tesla majority-of-outstanding vote; CFIUS/DoD vs China exposure | committees formed (20.10); Bloomberg 12.11 (anonymous): announcement Dec 2026 - early 2027; no terms | at the market ratio (2.84 SPCX per TSLA) SPCX holders own ~54% of the combined; on our values (SPCX $27.6, TSLA draft base $34.4) intrinsic per SPCX share falls to $20.5 (-$7.1); with a 15% better ratio $22.1; market value per share ~unchanged | announcement by mid-2027 ~45%, completion ~30% | negative for value, a trading catalyst only |
Sum of expected bets ~$8.3/share; all succeeding ~$57/share. Base + all bets = ~$85, still below $140.66.

## 5. Reverse reading (what $140.66 needs, base otherwise)
- SI yield x2.83 (revenue $1.36-1.70 per $ of servers a year, vs ~0.5-0.6 now and 0.44-0.48 at NBIS): SI 2031 revenue $379bn.
- Or all growth x3.75: group 2031 revenue ~$649bn (base $207bn, bull $288bn), at the base capital intensity.
- Or a group EBIT margin of 78% from 2028 at base revenue (base 20%).
- Equivalently ~$9+ of 2032 EPS at 28x (base $2.65, bull $5.42). The FY2027 consensus EPS $4.04 (n=34, range 0.34-13.49, up
  from 1.24 ninety days ago) already sits above our bull 2027 ($2.34).

## 6. Self-check
- Guided year: no company guidance exists (filings and releases); base 2027 tied to installed compute and backlog ($63bn,
  67% within a year). Later years inside the segment trend (Starlink subs adds slow from +6.4m/yr; ARPU drifting down as reported).
- Bull vs beat record: no guidance record; bull = execution beats on every segment. Musk's dated targets (Mars, Starship,
  2017 Starlink plan) have slipped, so the bull does not add timing optimism.
- Bear carries the company-specific hits: Customer B (34.5%) leaves on 90 days' notice, ARPU war with Amazon Leo/China,
  Starship delay (R&D $5bn+), government share shift; SI wound down rather than burning cash forever. Key-person and
  controlled-company governance (Musk ~82% of votes, Class B elects 51% of the board, related-party Valor leases, Tesla
  Megapacks, Boring stake) are not priced as a number: a further discount, not added.
- Converts/puts/warrants/leases: no converts; Valor sale-leasebacks in debt; Apollo lease in capex; EchoStar shares + cash
  in; options/RSUs in the count; Musk restricted shares excluded (economic) and noted.
- Value as of today, cash at face, no double count: end-2026 value discounted 48 days; cash at face; Cursor acquisition SBC
  not expensed because the shares are counted; Apollo lease not double counted (capex only).
- Reverse DCF holds the first year (2027 base) and scales the rest.
- Probabilities labelled subjective.
- Known model bias: our walk-forward found paths biased low (margins, exit multiple). The calibrated lens ($41) addresses
  that; the gap to the price (3.5x) is far beyond that measured bias.

## 7. Verdict (Claude's view)
Not a buy. Starlink is an excellent, high-return network (~$17-22 of value per share on its own), but the price of $140.66
(~$1.86tn EV) needs either AI-cloud economics no listed cloud shows or a several-times-larger company by 2031. Both lenses
and both reports are in the sell band; even the bull and the sum of all separate bets stay below the price. Price for
12%/yr: ~$28 (base). What would change the view: SI keeping a yield near 0.6+ with 50%+ EBITDA as the 2027 fleet comes
on (watch SI revenue per GW and Customer B renewals), Starlink ARPU stabilising at $65+ with subs adds above 6m/yr,
Starship refuelling and rapid reuse in 2027, or a Tesla deal ratio that pays SPCX holders a large premium.

Gaps: no company guidance; call (20.10) outlook not read; Customer A/B unnamed; Musk's 350m option strike; Apollo lease
rate; 800 MHz price; FY2028 consensus; peers' forward P/E; Starlink segment detail (maritime/aviation/Starshield/D2C);
beta history (5 months only); TSLA value used in the merger arithmetic is a DRAFT engine base (27.10.2026).

## 8. After the checks (repair, 13.11.2026). THIS SECTION REPLACES THE NUMBERS ABOVE
The pre-repair files are in deep/pre_repair/. Sections 1-7 above are kept as the first pass. Their value ($27.61) is withdrawn.

**Primary-source check of the critical finding.** The Q3 release (8-K 20.10.2026, EX-99.1) has no numeric outlook. It points to the
call. The call transcript was read in full from Alpha Vantage's transcript service. It is a second transcript, independent of the
singjupost copy the red team used, and the two agree word for word on every number below. The IR replay at ir.spacex.com was not
fetched, so the guidance is "call transcript (2 providers)", not a filing. On the call:
- CFO: "We now see total company revenues in the range of $33 billion to $37 billion for Q1 of 2027".
- CFO: "fourth-quarter capital expenditures to now be in the range of $35 billion to $40 billion".
- CFO: "on track to deliver our previously stated goal of $100 billion in ARR by the end of this year, defined as annualized revenue based on the month of December".
- CFO: payback "of less than a year"; pricing "slightly above the high end of the $30 to $50 per watt range".
- CFO: backlog $63bn, of which ~$42bn within a year.
- Musk: "highly confident of 10" GW added in 2027 (13 GW in total); "rent at effectively the spot-market rate"; compute split ~50/50 between internal use and rental.
- Musk: SpaceX ARR "between $500 billion and $1 trillion" by end-2027. This is a CEO aspiration, not guidance, and it is not used.
So section 6 ("no company guidance exists") was wrong. **Finding 1 is accepted.**

**What changed (all in mkparams.py; model.py gains one switch, `si_cip_lag_half`)**
| Change | From | To | Source / reason |
|---|---|---|---|
| 2026 SI capex | 57.0 | 74.3 | 9M 40.0 (10-Q) + Q4 ~34.3 (CFO Q4 total $35-40bn less ~3.2 for Starlink/Space) |
| Net cash end-2026 | +39.5 | +25.0 | +50.9 at 30.09 less Q4 capex ~37.5 plus Q4 EBITDA/other ~11.6 (Q4 revenue ~$22bn on the $100bn December ARR; Q4 gross margin "consistent with Q2") (judgement) |
| SI in service | in service in the year of spend | half of each year's servers in service the next year | CIP $23.7bn at 30.09 (10-Q). D&A still starts in the year of spend (conservative) |
| SI capex 2027-32, base | 85/75/65/65/70/75 | 200/180/140/120/120/120 | Musk is "highly confident" of +10 GW in 2027. 2026's ~2.4 GW cost ~$74bn. Base takes ~2/3 of that pace because Musk's dates slip. The Apollo lease (~$40bn) is inside |
| SI yield, base (revenue per $ of in-service servers) | 0.60 -> 0.48 | 1.40 -> 0.65 (2032) | 2027 sits at the guide (below). Then it fades: rental is at spot and terminable on 90 days' notice, half the fleet serves internal use, and new GPU generations reprice the older fleet. 0.65 by 2032 is still above the NBIS saved model (0.44-0.48) |
| Bull SI | capex 95/90/85..., yield 0.68 -> 0.55 | capex 250/220/180/150..., yield 1.50 -> 0.90 | guided capacity (~13 GW) at today's claimed pricing, mostly held (finding 2). The xAI frontier-lab bet is folded in here, not added on top |
| Bear SI | capex 75 then 12-15, yield 0.50 -> 0.33 | capex 180 (committed 2027 build), then 60/30/30/35/40; yield 1.20 -> 0.45 | build-out, then a spot glut down to neocloud yields (NBIS 0.44-0.48) and Customer B leaving (finding 4) |
| Starlink base | ARPU 67 -> 60; subs to 43.5m | ARPU 66 -> 55; subs to 48m (~6m adds a year) | ARPU fell ~12%/yr (99 -> 69); adds were +6.4m y/y (finding 6) |
| Shares | 13,191m gross | 13,151m treasury method | Musk options 352.5m at $8.40 (424B4 p.238); Cursor's 389m in the outstanding count (10-Q Note 12, 8-K) |
| SI 2026 D&A target | 7.9 | 8.9 | 9M 5.851 + Q4 ~3.05 |
| Calibrated "why" | ">100x" | "~39x" (our 2027 EPS 3.61) | results.json |

**Check of the guided year.** Base 2027 revenue is $172bn: Starlink 26.8, Space 5.3, SI 140.0. Q1'27 at the guide ($33-37bn) times 4 is $132-148bn. With
10 GW coming on through 2027, a full year above Q1x4 is the guide's own direction. It sits just under the FY2027 consensus mean of $180bn. The
implied Q4'26 revenue is about $22bn (December ARR $100bn), above the lowest analyst estimate ($19.09bn).

**Results after the checks** ($ per share, 13,151m shares, price $140.66)
| | Bear | Base | Bull |
|---|---|---|---|
| Today | 1.42 | **70.06** (DCF 47.06 / P/E path 93.06) | 231.51 |
| End-2027 | 1.43 | 80.19 | 265.44 |
| End-2028 (2 years: the noisiest) | 2.23 | 90.93 | 299.72 |
| End-2031 | 4.79 | 130.29 | 422.74 |
| Annual return to end-2031 | -48.3% | **-1.5%** | +23.9% |
| Subjective probability | 30% | 50% | 20% |
- Weighted: today $81.76, end-2031 $151.13, +1.4%/yr (finding 3 minor, weighted arithmetic: now computed by results.json and copied as is).
- Base path ($bn): revenue 172 / 300 / 390 / 431 / 446 / 443 (2027-32); EBIT margin 30% -> 23%. FCF -137 (2027), -49, +32, +69, +78.
  Net debt peaks at **-$164bn in 2028**. Operating EPS 3.61 / 5.80 / 6.87 / 6.54 / 6.22 / 6.15.
- Calibrated lens (35x held, margin floored at 2027's 30%): **$112.41, +7.8%/yr (hold band)**. Our lens: -1.5%/yr (sell band).
  **The lenses disagree** (sell vs hold). The rule says no trade on a disagreement. The calibrated lens is weak evidence here: base revenue
  is flat in 2031-32, so 35x on 2032 EPS is generous.
- Buy test: base >= 12% in both lenses NO; bull >= 17% **YES** (+23.9%); bear >= -10% NO. Price for 12%/yr on the base: **$72.85**. Calibrated
  lens at 12%: $115.75. The bear passes only below ~$8: the bear is a near-wipeout of equity, so no price makes it comfortable.
- Sum of parts (DCF, base): Starlink $17.44 ($21.78 at beta 1.1), Space -$1.47, SI **+$28.22** (2032 ROIC 14%), cash +$2.04, total $46.23.
- Sensitivity (base): server life 4y $43.6; exit P/E 22 $59.9 / 34 $80.2; beta 1.5 $75.4 / 2.6 $61.7; SI yield held at 1.40 $247.9.
- Reverse reading (finding 3 accepted): at the base capex, $140.66 needs an SI yield of **1.96 in 2027 fading only to 0.91 by 2032**. That means today's
  claimed pricing (more than $50/W a year, payback under a year) holds for 5 years while the fleet grows about 5x and is rented at spot. Or
  2031 group revenue of ~$590bn (base $446bn). Or a 40% group EBIT margin at base revenue (base 23%). **This is the single swing factor of the stock.**
- Funding (finding 4, partly accepted): base net debt peaks at $164bn and bull at $171bn. Both are above an assumed debt capacity of ~$100bn (judgement; the $25bn notes'
  lien cap is 7.5% of assets). About $60-70bn would have to come from equity, vendor or customer financing. In our arithmetic, equity sold above
  our value raises value per share. It hurts only when sold below value, which is the bear world, and the bear is near zero anyway. So no
  per-share dilution is modelled. The funding need is shown instead as a risk line. **Rejected part:** "dilution lowers every scenario" does not follow when the issue price is above value.

**Report 2 (historical-multiple report, peers since there is no own history):** Starlink EBITDA x 12/16/20 (IRDM, TMUS), Space core x 12/14.5/17 (LMT) less
3 years of Starship R&D, SI revenue x 3/4.5/6 EV/sales (NBIS) -> **$51 / $73 / $94**. The stock's own IPO-to-date EV/NTM sales (9.0-17.0x, median 12.2x)
on our NTM revenue ($151bn; consensus $158bn) -> $107 / $144 / $199. The market's own multiple on our revenue is about the price, so that line
is circular and not an independent check.

**Separate bets (not in the base):** Starship full reuse $5.53 if it happens (40%, expected $2.21); direct-to-cell at scale $4.85 (30%, $1.45);
xAI frontier-lab win now folded into the bull (finding 2); Mars 0; AI in space ~0. Expected $3.66; all succeed $10.38. The claim "bull + all bets
< price" is deleted: it was false.

**Tesla merger:** at the market ratio (2.84 SPCX per TSLA) SPCX holders would own ~54% of the combined company. On our base ($70.06) and the
**DRAFT** TSLA engine base ($34.4), intrinsic value per SPCX share would fall to ~$43.6. Present it only as a mechanism: who gains depends on the
ratio against the two intrinsic values. The TSLA number is a draft, and Musk's restricted shares and Tesla's CEO award need terms.

**Findings rejected or partly rejected**
- Finding (major) "set base yield 0.67 -> 0.54": superseded. With the CIP lag, the Q3 yield on in-service servers is about 1.0, and the Q1'27 guide implies more. The base now starts at 1.40.
- Finding (critical) restated base "$45-50": rejected as a number. The red team's own capex150 variant puts 2027 at $117bn, below the Q1 guide x4,
  so it breaks the guided-year rule it cites. The base at the guide is $70.
- Finding 4, dilution: partly rejected (above).

**Gaps closed:** Musk options strike ($8.40; 424B4 p.238); Cursor shares in the outstanding count. **Gaps open:** guidance from the call transcript
(2 providers), not the IR replay; the GW behind the base capex; interest on cash (3.8%) vs debt (6%); EchoStar trust support payments; 800 MHz price;
deferred-revenue unwind if Customer B terminates; beta (1.9, judgement); TSLA draft base; Customer B unnamed.

**Verdict after the checks: not a buy at $140.66. HOLD-band territory at best, and only in the calibrated lens.** Base $70 (-1.5%/yr, sell band) vs calibrated
$112 (+7.8%/yr, hold band). The bull ($232, +24%/yr) shows real upside if AI-cloud pricing holds at 13 GW, but the bear is near zero (build-out, then
a glut, with ~$110-160bn of net debt). Starlink alone is ~$17-22 a share: excellent, but not the swing factor. Price for 12%/yr: ~$73.
