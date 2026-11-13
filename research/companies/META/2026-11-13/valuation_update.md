# META model update, 13.11.2026 (company research copy): baseline rerun, changes, lenses and the sell-price ladder

Price **$634.68** (Yahoo chart API, 14:58 UTC 13.11; IBKR returned 502). Every per-share figure is on today's basis, **before**
the Class C 1-for-1 stock dividend (see section 6 for the divisor).
Arithmetic: `deep/model/model13.py` (stdout in `deep/model/model13_stdout.txt`) -> `deep/model_result.json`. The script
imports copies of the deep3 lane models (`dcf_redteam_model.py`, `pe.py`, reverse via `scripts/lean_model.py`); none of them was edited.
Labels: FACT (source), CONSENSUS, ESTIMATE (ours). Probabilities are SUBJECTIVE (Claude's judgement, not engine odds).

## 1. Baseline reproduced

| | Base today | Base end-2031 | Base return | Bear today | Bull today |
|---|---|---|---|---|---|
| deep3 final (5.11) | 507.8 | 850.9 | 6.18% | 222.9 | 919.4 |
| Rerun here | **507.8** | **850.9** | **6.18%** | **222.9** | **919.4** |

All three lanes were rerun from their own code and gave the same figures: DCF $509.9, P/E $565.6, reverse $447.4.
The reproduction is exact.

## 2. Changes, applied one at a time (stable-valuation rule: only a new fact or a locked rule)

| # | Change | Why | Base today | Base end-2031 | Bear / bull today |
|---|---|---|---|---|---|
| C1 | Beta 1.35 (DCF) and 1.40 (P/E, reverse) changed to **1.434** in all three lanes | Locked rule. The 2-year weekly beta vs SPY is 1.648 raw, which adjusts to 1.434 (market lane, recomputed 13.11; baseline beta.json 1.435). The deep3 lanes did not follow the rule | **-$5.7** | -$1.8 | -$2.9 / -$10.0 |
| C2a | P/E lane base exit 20.0x changed to **20.35x** | Locked rule: META's own long-run forward P/E median. The 10-year median is 20.35x (market lane); 2016-24 gives 20.4x (deep3 analysis.json) | **+$2.9** | +$5.0 | 0 / 0 |
| C2b | Reverse lane multiple half 18.0x changed to **20.35x** (its DCF half is unchanged) | Same locked rule. deep3 itself called 18x "harsh". The capex-heavy period would argue for less, but EPS already carries the depreciation, and the DCF lane already captures the cash cost | **+$9.6** | +$16.3 | 0 / 0 |
| C3 | **New Mexico Unfair Practices Act judgment** treated as a non-deductible one-off claim paid around mid-2028 | **New fact the baseline missed.** FACT, Q3 10-Q Note 10 (re-read here in raw_news/10q.txt): on 21.10.2026 the court imposed a $25B civil penalty with 15% post-judgment interest. Meta "accrued an amount" (undisclosed; at most ~$1.5B going by Q3 G&A) and will appeal. Amounts assumed: base $8B expected after appeal; bear $25B x 1.15^2 = $33.1B plus the DC AG's $1.7B; bull $2B | **-$2.5** | -$4.4 | **-$11.2** / -$0.6 |
| C4 | Value date moved to 13.11, price $634.68, horizon 5.13 years, 2027 consensus EPS 33.97 (was 33.93) | Time roll | +$1.1 | 0 | +$0.5 / +$2.0 |
| | **Total** | | **+$5.4 ($507.8 -> $513.2)** | +$15.1 | -$13.6 / -$8.6 |

The beta and multiple changes come from the locked rules. Without them the base would be $505.3 today: the baseline, plus
the New Mexico claim, plus the time roll.

**Considered but not applied:**
- **Legal accruals of about $7.2B that are neither AG nor the new New Mexico amount** (the 30.9 balance is $19.88B; deep3 nets only
  the AG installments). These are not a new fact. As a sensitivity, treating them as debt costs **-$2.75 per share**
  (GAP: whether part of this is already in the recurring legal charge).
- **New Mexico at full face** ($33B with interest) in the base: **-$8.1 per share** against the $8B assumption.
- **Forward-sale proceeds of $28.7B** come from an unverified headline, so they are not used. deep3 found the option worth about $0
  because it was struck near the price.
- **Unchanged inputs:**
  - consensus 2027 revenue and EPS (+0.1%);
  - capex: the street's 2027 figure is $211.8B, inside the $200-230B "as modelled" band;
  - S&P's negative outlook: no change to the 6% marginal funding rate;
  - EUR notes: already in;
  - insider sales: 10b5-1 plans and an a16z fund exit;
  - the change of infrastructure chief.
- **Not modelled:** an appeal bond on the New Mexico judgment could tie up ~$29B of collateral at ~0.5-1% a year, about
  -$0.6 to -$1.1 per share. ESTIMATE only; New Mexico's bond rules are a GAP.

## 3. Valuation (weighted DCF 0.40 / P/E 0.30 / reverse 0.30; USD per share, pre-Class-C basis; ESTIMATE)

| Scenario | Today | End-2027 | End-2028 | End-2029 | End-2030 | End-2031 | 5-yr return/yr (incl. ~$12.3 div.) | Probability (SUBJECTIVE) |
|---|---|---|---|---|---|---|---|---|
| Bear | 209 | 234 | 259 | 288 | 319 | 353 | -10.2% | 25% |
| Base | **513** | 576 | 638 | 707 | 783 | 866 | **+6.5%** | 50% |
| Bull | 911 | 1,023 | 1,133 | 1,256 | 1,390 | 1,537 | +19.0% | 25% |
| Probability-weighted | 537 | | | | | 906 | +7.5% | |

**The 2-year range** runs to end-2028: $259 / $638 / $1,133. This is the noisiest number in the table.

**Value by lane, base case:**

| Lane | Value today |
|---|---|
| DCF | $500.4 |
| P/E | $571.6 |
| Reverse | $476.7 |

The spread across methods is $477-572, so the model noise is about ±$50.

**Band and buy test:**
- The base sits in **hold**, 0.5 points above the lagging line, which is within noise.
- Buy test today:
  - base ≥ 12%: fails;
  - bull ≥ 17%: passes;
  - bear ≥ -10%: fails narrowly (-10.2%, through the New Mexico bear).
- At the $491 add price: bear -5.6%, bull +25.1%.

## 4. The two lenses (base) and the depreciation-wave line

The calibrated lenses hold 18.68x, today's forward P/E: $634.68 / 33.97, the 2027 consensus. It equals META's own 5-year median of 18.4x, and
2027 earnings are not depressed, so holding it is credible. The New Mexico claim ($4.4 per share by 2031) is deducted from every lens.

| Lens | 2032 EPS | End-2031 value | Return/yr | Band |
|---|---|---|---|---|
| Fundamental: weighted base as built | (weighted 44.1) | 866 | **6.5%** | hold |
| Calibrated, per the rule: 18.68x held, margin floored at 2027 (32.6%) | 56.1 | 1,044 | **10.4%** | hold |
| Calibrated with the full depreciation wave as its own line (the rule's exception: D&A to ~25% of revenue) | 44.1 | 820 | **5.4%** | lagging |
| Judgement line between the two: only the committed part of the wave (floor 1.5 points lower; D&A ~16-17% of revenue even if capex is cut to ~$90B from 2028) | 52.8 | 983 | **9.2%** | hold |

**How hard is the wave?**
- **Committed:** 2026 capex of ~$140B, plus 2027 capex backed by $121.4B of commitments due in 2027 (FACT Q3 10-Q).
- **Not evidenced:** 2028+ capex. The quarter to 25% of revenue rests on that.

The lenses agree on the action today. Fundamental 6.5% and calibrated 10.4% are both in hold. The dep-wave line (5.4%) is within the
3-point noise of the fundamental.

## 5. Sell-price ladder (today's basis; the price at which the base return to end-2031 equals the threshold)

| Threshold (meaning) | Fundamental | Calibrated (rule) | Calibrated, full dep wave | Judgement: committed wave only | **Both lenses (rule: fundamental + calibrated)** |
|---|---|---|---|---|---|
| 12%: add (buy test) | 491 | 590 | 465 | 557 | **add only below $491** (both ≥ 12%) |
| 9.14%: index parity | 561 | 674 | 532 | 636 | above $674 both are below the index |
| 6%: lagging line | 651 | 783 | 617 | 738 | **lagging above $783** |
| 3%: sell line | 755 | 907 | 715 | 855 | **sell above $907** |

**Both-lens prices under the owner's rule:**
- **Sell:** the lowest price at which both lenses are below 3% is **$907**.
- **Lagging line (6%):** the lowest price at which both are below 6% is **$783**.
- **Add:** the highest price at which both are at or above 12% is **$491**.

If the full-wave line replaces the calibrated lens, the pair gives sell $755, lagging $651, add $465. With the
committed-wave judgement line it gives sell $855, lagging $738.

**My view (ESTIMATE, Claude's judgement):**
- **The rule's full calibrated lens is too generous for a sell decision.** It ignores depreciation that is already committed.
- **Selling:** sell in two steps. Start at about **$855**, which is the fundamental 3% line with the committed wave. Finish at the rule's
  **$907**.
- **Lagging zone:** from about **$740-785**.
- **Today's $635 is a hold.** Act on reports, not reruns.
- **The historical-multiple lens agrees with this zone** (market lane):
  - above about $694 now, META is over its 5-year p75 forward P/E on 2027 EPS;
  - by late 2027 that line is about $811 on 2028 EPS.

**These prices drift up over time.** A fixed 5-year return line rises as the horizon rolls and value grows. Rerun the ladder
after each report (next: Q4'26 in late January 2027), not on price moves.

## 6. After the Class C dividend (19.1.2027 + up to 5 business days)

Each existing share (A or B) receives exactly one Class C share, so the share count doubles. **The divisor is exactly 2.000**
for the A+C pair (FACT DEF 14C 2.11). If C, which is non-voting, trades below A, an A share is worth a little more than half of
today's number and a C share a little less.

| Line | Today's basis | Post-dividend (÷2) |
|---|---|---|
| Add | $491 | $245.5 |
| Index parity (both lenses) | $674 | $337 |
| Lagging line (6%) | $783 | $391.5 |
| Sell (rule) | $907 | $453.5 |
| Sell, first step (judgement) | $855 | $427.5 |
| Base today | $513 | $256.6 |

## 7. Falsifiers: sell at any price

1. **Revenue growth below 12% y/y for two quarters** while the capex guide stays at or above $200B.
2. **The AI ad gains fade:** price per ad below +8% and impressions below +5% for two quarters.
3. **Margin guide:** the 2027 total-expense guide implies an operating margin below 27% before legal charges.
4. **Capex:** the 2027 capex guide is at or above $240B without a matching revenue step-up, and 2028 is signalled above 2027. The base then
   falls toward ~$400, which is below 3%/yr.
5. **Youth and consumer-protection litigation:** a material adverse outcome. That means one of:
   - a bellwether verdict (JCCP 5255; the second LA trial is under way) that sets per-claim liability across the 200k+ claims;
   - New Mexico injunctive relief that forces product changes beyond NM;
   - the $25B affirmed on appeal while other states file copycat UPA cases. That is the bear's legal path, about -$11 to -$15
     a share by 2031, and above it if repeated.

## 8. Gaps

- How much Meta accrued for New Mexico; the size of the NM appeal bond.
- Whether the $7.2B of non-AG accruals is covered by the recurring legal charge.
- Consensus EPS for 2029-30 was not refreshed.
- Forward-option exercise ($28.7B headline) not verified.
- IBKR price unavailable.
- Beta and exit-multiple changes follow locked rules, not new facts. Their net effect is +$6.8 today.

---

## 9. After the checks (FINISH lane, 13.11.2026): red-team findings applied

Arithmetic: runs/tickers/META_2026-11-13/deep/finish/finish.py -> finish_result.json (copy here: finish_result.json). It imports `model13.py` and `pe.py` unchanged.
The red-team variants are in `deep/redteam/variants_result.json`. The valuation table in section 3 does not change:
base $513 today, $866 at end-2031, +6.5%/yr. Only the sell ladder, its labels and the falsifiers change. All figures are
ESTIMATES, per share, before the Class C dividend.

### What was applied, and what was not

| # | Finding | Decision | Effect |
|---|---|---|---|
| 1 | The literal calibrated lens ("today's P/E held") can never say sell, because the held P/E rises with the price | **Applied.** For the ladder, the calibrated lens freezes the multiple at today's 18.68x (2027 consensus; equal to META's 5-year median of 18.4x). Disclosed: at the locked 10-year median of 20.35x, sell $988 and trim $853 | Labels only; $907 and $783 stand |
| 2 | The sell line drifts up about 11% a year | **Applied.** Lines are dated. At end-2027 (horizon end-2032) the both-lens sell line is about **$1,023** (about $511 after the split) and trim about **$886** ($443). The fundamental line then is about $837 (approximate: scaled) | Dated ladder below |
| 3 | The $855 first step was not modelled; the $715 wave line is biased low | **Applied.** The rule's own "hard evidenced cost" line is now modelled on the P/E lane, whose margin cut is exactly the named D&A and lease cost. 2032 is the peak-D&A year (EPS +22% in 2033), so end-2031 is valued on 2033 EPS of 57.58, discounted one year at 10.96%. Line: 3% = **$840**, 6% = **$725**, 12% = $547, today +8.8%/yr. The $855 judgement step is dropped. The peak-year version ($765) and the 12-year lease version ($714) are shown as sensitivities | First sell step $855 -> **$840** |
| 4 | The calibrated margin floor implies a pre-D&A, pre-lease margin of 61.2% in 2032, against 48.8% in 2027 and META's history of about 46-52% | **Applied as a label.** $907 is the generous lens's full-sell line; the depreciation-wave exception of the rule applies to META, so selling starts at the modelled $840 | Labels |
| 5 | The base capex path (32-34% of revenue in 2031-32) does not fade toward the pre-surge share (~22%) | **Not applied to the base; disclosed as upside risk.** The locked rule allows a stated fact to override the fade, and there are three: (a) $355.7B of leases not yet commenced, running to 2036 (FACT, Q3 10-Q), and $454.5B of purchase commitments (FACT); (b) S&P projects negative free cash flow into 2027-28 on the capex plan (WEB); (c) the lanes do not link revenue to capex, so fading capex while keeping revenue is a free lunch. Under the literal fade, the P/E lane today goes $572 -> $726 and the wave sell line to $972 | Upward risk on the sell line |
| 6 | The held multiple is on consensus EPS, the path EPS is 8% lower | **Applied as disclosure.** Upward bound sell $984 | Disclosure |
| 7 | Falsifiers #2, #3, #5 are not measurable or not dated | **Applied.** Rewritten below | Falsifiers |
| 8 | Divisor 2 is right for the A+C pair; the A quote alone can overstate | **Applied.** After the split, test the lines against the average of the A and C quotes; dividends halve too | Note |

**Upward alternatives** (none is used; all lift the sell line): locked 10-year multiple $988; consensus-scaled EPS $984;
literal capex fade $972. **Downward:** the peak-year wave line $765; with a 12-year lease term $714.

### Final ladder (today's basis; post-dividend ÷2)

| Line | Fundamental | Calibrated (18.68x frozen) | Evidenced-wave line (P/E lane) | Rule: both lenses | Claude's view |
|---|---|---|---|---|---|
| 12%/yr: add (buy test) | 491 | 590 | 547 | **add below $491** ($245.5) | same |
| 9.14%: index parity | 561 | 674 | 624 | $674 ($337) | $624 ($312) |
| 6%: trim to the 6% cap | 651 | 783 | 725 | **trim above $783** ($391.5) | **trim from $725** ($362.5) |
| 3%: sell | 755 | 907 | 840 | **sell above $907** ($453.5) | **start selling at $840** ($420), **finish at $907** ($453.5) |
| Return at $634.68 | 6.5% | 10.4% | 8.8% | hold | hold |

**Dated lines (re-price after every report, not on price moves):**

| Date of the line | Trim from | Start selling | Finish selling |
|---|---|---|---|
| Now (13.11.2026) | $725-783 ($362.5-391.5) | $840 ($420) | $907 ($453.5) |
| End-2027 (approx.) | ~$886 (~$443) | ~$1,020 (~$511) | ~$1,023 (~$511) |

By end-2027 the evidenced-wave line and the calibrated line converge (~$1,022), because the exit year has passed the
depreciation peak. These end-2027 figures assume the base path holds; they are not forecasts of the price.

**Historical-multiple cross-check** (market lane): fair value on 2027 EPS 33.97 x the 5-year median 18.4x = **$624**
(p25-p75 $537-694). Above about $694 now, and about $811 by late 2027, META is above its own 5-year p75.

### Falsifiers after the checks (sell at any price)

1. Revenue growth below 12% y/y for two quarters in a row while the capex guide stays at or above $200B.
2. In 2027-28: price per ad below +8% and impressions below +5% y/y for two quarters in a row (AI ad gains fading).
3. At the Q4 report (late January 2027): the 2027 total-expense guide midpoint, set against the consensus 2027 revenue on
   the guide date (today $312.6B), implies an operating margin below 27% before legal charges.
4. The 2027 capex guide at or above $240B without a matching revenue step-up, with 2028 signalled above 2027 (the base then
   falls toward ~$400, below 3%/yr).
5. Youth and consumer-protection litigation beyond what is already counted (the $18.59B AG agreement and New Mexico's $25B):
   new judgments, accruals or settlements above about $40B in total; or two more state UPA judgments above $5B each; or New
   Mexico injunctive relief that forces product changes beyond New Mexico.

### Bottom line after the checks

- **Today, $634.68: hold.** Fundamental 6.5%, calibrated 10.4%, evidenced-wave 8.8%: all in hold. No trade between
  reports. Add only below $491.
- **The sell price (Claude's view):** begin selling at about **$840** and finish at about **$907** (after the Class C
  dividend: $420 and $453.5, tested on the A+C average). Trim to the 6% cap from about **$725-785** ($362.5-391.5).
- These lines rise about 11% a year. Rerun them after the Q4 report in late January 2027.
- **Unverified:** the $28.7B forward-sale headline; the New Mexico accrual and appeal bond; whether the ~$7.2B of other
  legal accruals is already in the recurring legal charge; 2029-30 consensus EPS (not refreshed); IBKR price (502).
