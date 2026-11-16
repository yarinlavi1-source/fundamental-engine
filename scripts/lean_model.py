"""Lean two-method valuation for the 5-minute ticker check (owner preference 04.11.2026).

Method 1: DCF of free cash flow after SBC. The cost of equity is rf + beta x ERP and fades to a mature beta over
10 years. Growth fades to terminal growth = rf over 2033-2041, with reinvestment = g / terminal ROIC. Cash is added.
Method 2: exit multiple. The end-of-horizon value is P/E x next-year operating earnings (ex cash), plus the PV of the
FCF to the horizon, plus cash today.
Value = average of the two. Year-end values are conditional values on each path, not price forecasts.

Usage: python scripts/lean_model.py params.json [--out result.json] [--summary]
                                    [--price P] [--as-of YYYY-MM-DD] [--params-out repriced_params.json]
                                    [--worlds worlds.json] [--calibrated-rule calibrated_rule.json]
--summary prints a short plain table (values, 2-year and 5-year ranges, values in exactly 1, 2 and 5 years, returns, the
return with distributions, ladder, buy test, method gap, sensitivities, warnings) instead of the JSON; --out still writes
the full JSON.
--price and --as-of reprice a saved params file without copying it by hand: the price is replaced, and the dates are
recomputed only when the file has base_year_end. Without base_year_end only the price changes and the output says that
the dates were not moved (inputs.repriced and a warning). --params-out writes the repriced params (with a "repriced"
record) so they can be saved next to the result. Every other input stays the file's.
The params file has keys: ticker, as_of, price, rf, erp, beta, beta_mature, shares_m, net_cash_m (end of the base year;
negative = net debt), rev0_m (base-year revenue), base_year, tax, cash_yield, debt_rate, roic_terminal,
days_to_base_year_end, years_to_horizon, buy_return, probabilities {bear, base, bull}, and scenarios
{bear|base|bull: {g[6], m[6] (operating margin after SBC), conv[6] (FCF / NOPAT), pe (exit P/E on next-year
operating EPS), charge {year index 0-5: $M after tax}, optional shares_m}}.
Optional base_year_end (ISO date of the base fiscal year-end): with as_of it gives days_to_base_year_end (the days
between them) and years_to_horizon (5 + days / 365, two decimals, the convention of the saved files), so the two are no
longer typed by hand; hand-entered values that differ from the derived ones by more than 0.02 year raise a warning and
the derived ones are used. Without base_year_end nothing changes. Keep the two hand keys in the file as well: the
tracker, the freeze and the golden test still read them.

Input checks (15.11.2026, check_params; they run before the model): required keys, six numbers in every g/m/conv
list, numeric probabilities for the scenarios that sum to 1 (tolerance 1e-6), positive price, shares and base revenue,
scenario names bear/base/bull with a base, fractions where fractions are meant (rf, erp, tax, margins), charge keys
0-5. A broken input stops the run with every problem listed (ParamsError; the command exits with code 2). Suspicious
but usable inputs are warnings in the output (inputs.warnings, repeated in warnings).
Converts, puts, warrants, leases and patent cliffs are NOT modelled here. Put them into the inputs (net cash, shares,
growth/margin paths, charges) from the filings fine-print lane, or use a custom model.

Two lenses (owner preference 05.11.2026, CLAUDE.md): the result also carries a calibrated lens of the base case. It keeps
the base's revenue, conversion and charges but holds today's forward P/E (price / first forecast year's operating EPS) as
the exit multiple and does not let the margin fall below the first forecast year. Reason: in the 2016-2025 price-path
walk-forward (docs/PRICE_PATH_V2_RESULTS_2026-10-25.md) fading the multiple and cutting margins were the two largest
sources of a low bias. Optional params key "calibrated": {"pe": exit P/E to use when today's multiple is not credible
(e.g. a GAAP P/E inflated by temporarily depressed earnings), "m": [6 margins] when a margin cut is hard and evidenced
(e.g. a quantified depreciation wave), "why": text}. The two lenses are not averaged: both historical analogues erred low
in the same direction (3-year median bias x0.73 and x0.89), so an average cannot remove that shared bias. A buy, sell or
trim call is made only where both lenses fall in the same band ("bands": buy/hold/lagging thresholds in % a year).

Reporting keys (15.11.2026; they read the model above and change none of its numbers):
- price_ladder: the price today at which the base 5-year return equals each hurdle, for both lenses, always as
  year-end value at the horizon / (1 + hurdle) ^ years_to_horizon. Every rung carries its hurdle (buy test, the
  margin-of-safety return = buy_return, the index = rf + erp, hold floor, sell line), so two hurdles cannot be mixed.
- buy_test: the owner's rule as numbers (base >= 12% a year in both lenses, bull >= 17%, bear >= -10%), each leg with
  its return, threshold, pass and highest passing price; max_price_passing is the lowest of the four, with the leg named.
  The legs compare returns rounded to 0.1 point (as the bands do); on_the_line marks a pass at a price above that price.
  buy_test.rerun: the calibrated leg holds today's multiple, so at a lower price a rerun holds a lower one; rerun gives
  the price a rerun confirms (absent when calibrated.pe is set).
- ranges: bear/base/bull value today, at the 2-year and at the 5-year year-end. The 1-2 year number is the noisiest.
- calibrated_diagnostics: whether the margin floor binds, the held multiple against the base exit multiple, how much of
  the gap between the lenses comes from the multiple and how much from the margin, and a warning when the held multiple
  looks inflated (above 1.5x the base exit multiple, or a positive first-year operating EPS under 2% of the price).
- funding: the model's own net-cash path by year in each scenario, its low point, and a flag when net debt at the low
  point is above 3x that year's after-tax operating profit or the path borrows more than today's balance sheet holds.
  equity_wiped marks a scenario whose value today is zero or less (the value is floored at 0.01, never negative).
- sensitivity: the change in the base value today and in the base 5-year return for one input changed at a time.
  It is the model's own arithmetic, not a forecast. sensitivity_base (older, wider steps) is kept as it was.
- reverse_reading: the growth the price needs on the base margin path (= market_implied_growth_pct) and at a margin
  held flat at the first forecast year, so the reading is not dominated by the margin path.
- warnings: every flag above in one list, plus the input warnings.

Reporting keys of the engine upgrade (15.11.2026, second set; additive, no earlier key changes its value):
- inputs: the dates the run used (as_of, base_year_end and where it came from, days_to_base_year_end, years_to_horizon,
  whether they were derived, the horizon date), the input warnings, and "repriced" when --price / --as-of were used.
- dated_values: an ISO date on every year-end value, and the bear/base/bull (and calibrated) values at exactly as_of + 1,
  2 and 5 years, by log-linear (geometric) interpolation between the dated points, the same rule as
  forecast_tracker.value_at. "In one year" therefore always means one year. A date after the last year-end (as_of after
  the base year-end) is extended at the last year's rate and marked beyond_last_year_end_days.
- method_gap: per scenario, what each method gives alone (value today, value at the horizon, 5-year return, band), the
  exit P/E next to the P/E the DCF itself implies at the horizon, and a warning when the two methods land in different
  bands. The published value stays the plain average; the warnings are listed in method_gap.warnings.
- sensitivity_terminal: the same one-at-a-time table for the terminal inputs (mature beta, terminal ROIC, terminal
  growth), which sensitivity does not cover; moves_most_overall names the largest row of both tables.
- return_with_distributions: the 5-year return if the free cash the model accumulates above today's net cash were paid
  out each year and reinvested at the cost of equity (the heavy engine's 0.14.1 entry: cash left at the cash yield reads
  low against an index that reinvests). A second line beside the headline return, never a replacement for it.
- price_ladder.rungs.margin_of_safety_13: a fixed 13% rung, the handoff's margin_of_safety_price. The older
  margin_of_safety rung is the company hurdle (buy_return): its key, label and prices are unchanged and "meaning" says so.
In these new keys a wiped scenario is value 0 and return null (the older keys keep the 0.01 floor).

Fix B (16.11.2026; new keys only, no earlier key changes its value):
- buy_test.margin_of_safety_price: the margin-of-safety price = the buy test's four legs with the base at 13% a year in
  both lenses (bull >= 17%, bear >= -10% unchanged), the highest price at which all pass, and the rerun price when a rerun
  at that price holds a lower multiple (margin_of_safety_rerun.used), exactly as the buy-test price at 12%. It is never
  above the buy-test price. With margin_of_safety_binding_leg, margin_of_safety_stated_price, margin_of_safety_rerun and
  margin_of_safety_rule; the weighted worlds' buy test carries the same keys. price_ladder.rungs.margin_of_safety_13 keeps
  its name, label and prices (the base alone at 13%) and gains "meaning" and "note" saying it is not that price.
- Optional calibrated.pe_rule "net_of_cash": the second lens holds (price - net cash per share at the base year-end) /
  first-year operating EPS, recomputed at any price (the buy-test rerun and --price move it), because the exit value adds
  the cash at face on top of a multiple of earnings before interest. Absent = the default lens, unchanged. pe and pe_rule
  together are refused. --calibrated-rule FILE (a sidecar such as calibrated_rule.json beside the params, {"pe_rule",
  "why"}) applies the rule to this run without editing the params: a fixed calibrated.pe is dropped (kept as
  calibrated.replaced_pe). The lens then also reports pe_rule, pe_held_exact and net_cash_per_share.

Optional inputs of the engine upgrade (15.11.2026, P3b; absent = the output is identical, key for key):
- Per-scenario "funding": scenarios.<k>.funding = {"equity_raise_m": {year index: $M}, "issue_price": number or "model",
  "debt_raise_m": {year index: $M}, "debt_rate": r, "why": text}. The year index is the charge index (0 = base year + 1)
  and the money arrives at the start of that year (the year-end before it; key 0 = the base year-end, where net_cash_m
  is measured), so it pays for that year's spending and earns or saves interest from then on. Equity: the proceeds go
  into net cash and the new shares (amount / issue_price) into the share count. "model" = the scenario's own value per
  share at that year-end, after the money (closed form: shares = shares before / (1 - sum of raise / total value at its
  year-end)), so a raise at the model's value moves no value from the holders. Every per-share value is on the full
  share count of the plan, with a later raise's proceeds discounted at the cost of equity (fully diluted, which is the
  holders' value when the shares are sold at the issue price); EPS uses the shares out in that year. Debt: borrowing
  leaves net cash unchanged at issue (cash and debt rise together); the named debt pays debt_rate to the horizon while
  the cash it brought earns cash_yield until it is spent. debt_rate (funding) replaces the file's debt_rate for this
  scenario, on the named debt and on any other net debt. The scenario's result gains "funding" (shares before and after,
  issue prices, the money by the year-end it lands); the funding key lists the plan and flags only the outside money the
  plan does not name. Use it in place of a side script that edits net_cash_m and shares_m by hand.
- "worlds": a params key or a separate file given with --worlds: {"worlds": [{"name", "file", "weight"}]} with the
  files relative to the file that names them and weights that sum to 1. Every world is a full params file (same ticker,
  price, as_of, base year and dates as the main file; with --price / --as-of each world is repriced the same way). The
  output gains "worlds": per world its bear/base/bull (and calibrated) value today, dated year-end values, values at
  exactly 1, 2 and 5 years and 5-year returns, buy test and 13% price; and the weighted values (sum of weight x value,
  for each scenario, today, at each year-end and at 1, 2 and 5 years), the weighted 5-year return of each scenario from
  the weighted horizon value against the price, the weighted price ladder and the weighted buy test (its rerun price
  holds each world's own multiple at the trial price). The existing keys describe the main file only.
"""
import argparse
import copy
import datetime
import hashlib
import json
import math
import os
import re
import sys


# ---- input checks and dates (15.11.2026): they run before the model and change no number of a valid file ----

SCENARIO_NAMES = ('bear', 'base', 'bull')
REQUIRED = ('price', 'rf', 'erp', 'beta', 'beta_mature', 'shares_m', 'net_cash_m', 'rev0_m', 'base_year', 'tax',
            'cash_yield', 'roic_terminal', 'buy_return', 'probabilities', 'scenarios')
NUMERIC = ('price', 'rf', 'erp', 'beta', 'beta_mature', 'shares_m', 'net_cash_m', 'rev0_m', 'tax', 'cash_yield',
           'roic_terminal', 'buy_return', 'debt_rate', 'days_to_base_year_end', 'years_to_horizon')
DATE_TOLERANCE_YEARS = 0.02
_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class ParamsError(ValueError):
    """The params cannot give meaningful numbers. problems: every reason, in plain words."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(f'invalid lean_model params ({len(self.problems)} problem(s)): ' + '; '.join(self.problems))


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _date(x):
    """An ISO date string (YYYY-MM-DD) as a date, else None."""
    if isinstance(x, str) and _ISO.match(x):
        try:
            return datetime.date.fromisoformat(x)
        except ValueError:
            return None
    return None


def _shift_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                       # 29 February
        return d.replace(year=d.year + years, day=28)


def _dates(p):
    """The dates the run uses: (info, errors, warnings). With base_year_end and as_of the two hand keys are derived."""
    errors, warns = [], []
    as_of, end = _date(p.get('as_of')), _date(p.get('base_year_end'))
    days, years = p.get('days_to_base_year_end'), p.get('years_to_horizon')
    info = {'as_of': p.get('as_of') if as_of else None, 'base_year_end': None, 'base_year_end_source': None,
            'days_to_base_year_end': days, 'years_to_horizon': years, 'dates_derived': False, 'horizon_date': None}
    if 'as_of' not in p:
        warns.append('no as_of: the values carry no dates (dated_values is not available)')
    elif as_of is None:
        warns.append(f'as_of {p.get("as_of")!r} is not an ISO date (YYYY-MM-DD): the values carry no dates')
    if p.get('base_year_end') is not None and end is None:
        errors.append(f'base_year_end {p.get("base_year_end")!r} is not an ISO date (YYYY-MM-DD)')
    elif end is not None and as_of is None:
        errors.append('base_year_end needs an ISO as_of to derive the days and years from')
    elif end is not None:
        d_days = (end - as_of).days
        d_years = round(5 + d_days / 365, 2)
        entered = {}
        if _num(days):
            entered['days_to_base_year_end'] = days
            if abs(days - d_days) / 365 > DATE_TOLERANCE_YEARS:
                warns.append(f'days_to_base_year_end {days:g} in the file differs from the {d_days} days between as_of '
                             f'{p["as_of"]} and base_year_end {p["base_year_end"]}: the derived value is used')
        if _num(years):
            entered['years_to_horizon'] = years
            if abs(years - d_years) > DATE_TOLERANCE_YEARS:
                warns.append(f'years_to_horizon {years:g} in the file differs from the derived {d_years:g} (5 + {d_days} '
                             'days / 365): the derived value is used')
        info.update(base_year_end=p['base_year_end'], base_year_end_source='base_year_end', days_to_base_year_end=d_days,
                    years_to_horizon=d_years, dates_derived=True, entered=entered)
        if _num(p.get('base_year')) and abs(end.year - p['base_year']) > 1:
            warns.append(f'base_year_end {p["base_year_end"]} is more than a year away from base_year {p["base_year"]:g}')
        days, years = d_days, d_years
    else:
        for key, val in (('days_to_base_year_end', days), ('years_to_horizon', years)):
            if key not in p:
                errors.append(f'missing key: {key} (or give base_year_end and as_of, from which it is derived)')
        if as_of is not None and _num(days):
            info.update(base_year_end=(as_of + datetime.timedelta(days=days)).isoformat(),
                        base_year_end_source='as_of + days_to_base_year_end')
    if _num(years) and years <= 0:
        errors.append(f'years_to_horizon must be positive (got {years:g})')
    if _num(days) and abs(days) > 366:
        warns.append(f'days_to_base_year_end is {days:g}: the base year-end is more than a year from as_of')
    if info['base_year_end']:
        info['horizon_date'] = _shift_years(_date(info['base_year_end']), 5).isoformat()
    return info, errors, warns


def check_params(p):
    """(errors, warnings, dates) for a params object. Errors are inputs that make the numbers meaningless."""
    if not isinstance(p, dict):
        return ['the params are not a JSON object'], [], {}
    errors = [f'missing key: {k}' for k in REQUIRED if k not in p]
    warns = []
    for k in NUMERIC:
        if k in p and not _num(p[k]):
            errors.append(f'{k} must be a number (got {p[k]!r})')

    def ok(k):
        return _num(p.get(k))

    for k in ('price', 'shares_m', 'rev0_m'):
        if ok(k) and p[k] <= 0:
            errors.append(f'{k} must be positive (got {p[k]:g})')
    if 'base_year' in p and not (isinstance(p['base_year'], int) and not isinstance(p['base_year'], bool)):
        errors.append(f'base_year must be a whole year (got {p["base_year"]!r})')
    for k, hi in (('rf', 0.25), ('erp', 0.25), ('buy_return', 1.0)):
        if ok(k) and not 0 < p[k] < hi:
            errors.append(f'{k} must be a fraction between 0 and {hi:g}, e.g. 0.049 for 4.9% (got {p[k]:g})')
    for k, hi in (('cash_yield', 0.25), ('debt_rate', 0.5)):
        if ok(k) and not 0 <= p[k] < hi:
            errors.append(f'{k} must be a fraction from 0 to under {hi:g}, e.g. 0.036 for 3.6% (got {p[k]:g})')
    if ok('tax') and not 0 <= p['tax'] < 1:
        errors.append(f'tax must be a fraction from 0 to under 1 (got {p["tax"]:g})')
    for k in ('beta', 'beta_mature'):
        if ok(k) and p[k] <= 0:
            errors.append(f'{k} must be positive (got {p[k]:g}): the cost of equity has to be above the risk-free rate')
    if ok('roic_terminal') and ok('rf') and p['roic_terminal'] <= p['rf']:
        errors.append(f'roic_terminal {p["roic_terminal"]:g} must be above rf {p["rf"]:g} (the terminal growth): at or below '
                      'it the terminal year reinvests all of its profit or more')

    sc = p.get('scenarios')
    names = []
    if 'scenarios' in p:
        if not isinstance(sc, dict) or not sc:
            errors.append('scenarios must be an object with the scenarios bear, base and bull')
        else:
            names = list(sc)
            unknown = [k for k in names if k not in SCENARIO_NAMES]
            if unknown:
                errors.append(f'unknown scenario name(s) {unknown}: the names are {", ".join(SCENARIO_NAMES)}')
            if 'base' not in sc:
                errors.append('there is no base scenario')
            missing = [k for k in SCENARIO_NAMES if k not in sc and k != 'base']
            if missing and not unknown:
                warns.append(f'no {" and no ".join(missing)} scenario: the buy test cannot be complete')
            for k, d in sc.items():
                errors += _check_scenario(k, d, warns)
    pr = p.get('probabilities')
    if 'probabilities' in p:
        if not isinstance(pr, dict):
            errors.append('probabilities must be an object {bear, base, bull}')
        elif isinstance(sc, dict) and sc:
            bad = [k for k in sc if not _num(pr.get(k))]
            if bad:
                errors.append(f'probabilities must hold a number for every scenario (missing or not a number: {bad})')
            else:
                out = [k for k in sc if not 0 <= pr[k] <= 1]
                if out:
                    errors.append(f'probabilities must be between 0 and 1 (got {", ".join(f"{k} {pr[k]:g}" for k in out)})')
                total = sum(pr[k] for k in sc)
                if abs(total - 1) > 1e-6:
                    errors.append(f'the probabilities of {", ".join(sc)} sum to {total:.6g}, not 1')
                extra = [k for k in pr if k not in sc and _num(pr[k])]
                if extra:
                    warns.append(f'probabilities for {extra} have no scenario and are not used')
    cal = p.get('calibrated')
    if cal is not None:
        if not isinstance(cal, dict):
            errors.append('calibrated must be an object {pe, m, why}')
        else:
            if 'pe' in cal and not (_num(cal['pe']) and cal['pe'] > 0):
                errors.append(f'calibrated.pe must be a positive number (got {cal["pe"]!r})')
            if 'pe_rule' in cal:
                if cal['pe_rule'] not in PE_RULES:
                    errors.append(f'calibrated.pe_rule must be one of {", ".join(PE_RULES)} (got {cal["pe_rule"]!r})')
                elif 'pe' in cal:
                    errors.append('calibrated: give pe (a fixed multiple) or pe_rule (a multiple tied to the price), not both')
            if cal.get('m') and not (isinstance(cal['m'], list) and len(cal['m']) == 6 and all(_num(x) for x in cal['m'])):
                errors.append('calibrated.m must be a list of six numbers')
    bands = p.get('bands')
    if bands is not None:
        if not (isinstance(bands, dict) and all(_num(bands.get(k)) for k in ('buy', 'hold', 'lagging'))):
            errors.append('bands must be an object with the numbers buy, hold and lagging (% a year)')
        elif not bands['buy'] > bands['hold'] > bands['lagging']:
            warns.append('bands are not in the order buy > hold > lagging')
    dates, d_err, d_warn = _dates(p)
    return errors + d_err, warns + d_warn, dates


def _check_scenario(name, d, warns):
    if not isinstance(d, dict):
        return [f'scenario {name} must be an object']
    errors = []
    for k in ('g', 'm', 'conv'):
        v = d.get(k)
        if k not in d:
            errors.append(f'scenario {name}: missing key {k}')
        elif not isinstance(v, list) or len(v) != 6:
            n = f'{len(v)} values' if isinstance(v, list) else repr(v)
            errors.append(f'scenario {name}: {k} must be a list of six numbers, base year + 1 to base year + 6 (got {n})')
        elif not all(_num(x) for x in v):
            errors.append(f'scenario {name}: {k} holds a value that is not a number')
    if 'pe' not in d:
        errors.append(f'scenario {name}: missing key pe')
    elif not (_num(d['pe']) and d['pe'] > 0):
        errors.append(f'scenario {name}: pe must be a positive number (got {d["pe"]!r})')
    if 'shares_m' in d and not (_num(d['shares_m']) and d['shares_m'] > 0):
        errors.append(f'scenario {name}: shares_m must be a positive number (got {d["shares_m"]!r})')
    if errors:
        return errors
    if min(d['g']) <= -1:
        errors.append(f'scenario {name}: a growth of {min(d["g"]):g} takes revenue to zero or below (growth is a fraction: 0.15 = 15%)')
    if max(d['m']) > 1 + 1e-9:
        errors.append(f'scenario {name}: an operating margin of {max(d["m"]):g} is above 100% (the margin is a fraction: 0.45 = 45%)')
    if max(d['g']) > 5:
        warns.append(f'scenario {name}: growth of {max(d["g"]):g} is above 500% a year: check that growth is a fraction (0.15 = 15%)')
    if min(d['m']) < -1:
        warns.append(f'scenario {name}: an operating margin of {min(d["m"]):g} is a loss larger than revenue: check the unit')
    if max(abs(x) for x in d['conv']) > 5:
        warns.append(f'scenario {name}: a free-cash-flow conversion of {max(d["conv"], key=abs):g} is above 5x the after-tax profit: check the unit')
    charge = d.get('charge')
    if charge is not None:
        if not isinstance(charge, dict):
            errors.append(f'scenario {name}: charge must be an object {{year index 0-5: $M after tax}}')
        else:
            for k, v in charge.items():
                try:
                    i = int(k)
                except (TypeError, ValueError):
                    i = None
                if i is None or not 0 <= i <= 5:
                    errors.append(f'scenario {name}: charge key {k!r} is not a year index 0-5 (0 = base year + 1, not a calendar '
                                  'year); the model would drop the charge')
                elif not _num(v):
                    errors.append(f'scenario {name}: charge {k!r} must be a number (got {v!r})')
    if d.get('funding') is not None:
        errors += _check_funding(name, d['funding'], warns)
    return errors


FUNDING_KEYS = ('equity_raise_m', 'issue_price', 'debt_raise_m', 'debt_rate', 'why', 'note')


def _check_funding(name, f, warns):
    """Errors of a scenario's funding plan (see the docstring); warnings go to warns."""
    tag = f'scenario {name}: funding'
    if not isinstance(f, dict):
        return [f'{tag} must be an object {{equity_raise_m, issue_price, debt_raise_m, debt_rate}}']
    errors = [f'{tag}: unknown key {k!r} (the keys are {", ".join(FUNDING_KEYS)})' for k in f if k not in FUNDING_KEYS]
    for key in ('equity_raise_m', 'debt_raise_m'):
        sched = f.get(key)
        if sched is None:
            continue
        if not isinstance(sched, dict):
            errors.append(f'{tag}.{key} must be an object {{year index 0-5: $M}}')
            continue
        for k, v in sched.items():
            try:
                i = int(k)
            except (TypeError, ValueError):
                i = None
            if i is None or not 0 <= i <= 5:
                errors.append(f'{tag}.{key}: key {k!r} is not a year index 0-5 (0 = base year + 1, the money arrives at the '
                              'base year-end; not a calendar year)')
            elif not (_num(v) and v >= 0):
                errors.append(f'{tag}.{key}: {k!r} must be an amount of $M, zero or more (got {v!r}); buybacks are not '
                              'modelled here')
    raises = any(_num(v) and v > 0 for v in (f.get('equity_raise_m') or {}).values()) if isinstance(f.get('equity_raise_m'), dict) else False
    px = f.get('issue_price')
    if raises and px is None:
        errors.append(f'{tag}: equity_raise_m needs issue_price (a price per share, or "model")')
    elif px is not None and px != 'model' and not (_num(px) and px > 0):
        errors.append(f'{tag}: issue_price must be a positive number or "model" (got {px!r})')
    if 'debt_rate' in f and not (_num(f['debt_rate']) and 0 <= f['debt_rate'] < 0.5):
        errors.append(f'{tag}: debt_rate must be a fraction from 0 to under 0.5, e.g. 0.085 for 8.5% (got {f["debt_rate"]!r})')
    if not errors and not raises and 'debt_rate' not in f and not any((f.get('debt_raise_m') or {}).values()):
        warns.append(f'{tag} names no money and no rate: it changes nothing')
    return errors


def prepare(p):
    """The params the model runs on (a copy with the derived dates) and the inputs record. Raises ParamsError."""
    errors, warns, dates = check_params(p)
    if errors:
        raise ParamsError(errors)
    q = dict(p)
    if dates['dates_derived']:
        q['days_to_base_year_end'], q['years_to_horizon'] = dates['days_to_base_year_end'], dates['years_to_horizon']
    rp = p.get('repriced')
    if isinstance(rp, dict):
        dates['repriced'] = rp
        if rp.get('as_of_requested') and not rp.get('dates_moved'):
            warns.append(f'repriced: the dates were not moved to {rp["as_of_requested"]} (the file has no base_year_end); '
                         f'as_of, days_to_base_year_end and years_to_horizon are those of the file ({p.get("as_of")})')
    dates['warnings'] = warns
    return q, dates


def reprice(p, price=None, as_of=None):
    """A copy of the params at another price and/or date, with a "repriced" record. Nothing else is changed.

    The dates move only when the file has base_year_end: then as_of is replaced and the two hand keys are rewritten from
    it. Without base_year_end only the price changes and the record says the dates were not moved."""
    q = copy.deepcopy(p)
    info = {'price_from': p.get('price'), 'as_of_from': p.get('as_of')}
    problems = []
    if price is not None:
        if not (_num(price) and price > 0):
            problems.append(f'--price must be a positive number (got {price!r})')
        q['price'] = price
    if as_of is not None and _date(as_of) is None:
        problems.append(f'--as-of {as_of!r} is not an ISO date (YYYY-MM-DD)')
    if problems:
        raise ParamsError(problems)
    info['price'] = q.get('price')
    moved = False
    if as_of is not None:
        info['as_of_requested'] = as_of
        if _date(p.get('base_year_end')) is not None:
            q['as_of'] = as_of
            dates, _, _ = _dates(q)
            q['days_to_base_year_end'], q['years_to_horizon'] = dates['days_to_base_year_end'], dates['years_to_horizon']
            moved = True
    info['as_of'] = q.get('as_of')
    info['dates_moved'] = moved
    if moved:
        info['note'] = (f'as_of moved from {p.get("as_of")} to {as_of}; days_to_base_year_end and years_to_horizon '
                        f'recomputed from base_year_end {p["base_year_end"]}')
    elif as_of is not None:
        what = 'only the price was changed' if price is not None else 'nothing was changed'
        info['note'] = (f'{what}: the file has no base_year_end, so the dates were not moved to {as_of} '
                        f'(as_of, days_to_base_year_end and years_to_horizon are those of the file, {p.get("as_of")})')
    else:
        info['note'] = f'only the price was changed; the dates are those of the file ({p.get("as_of")})'
    info['held'] = ('every other input is the file\'s (net cash, shares, growth, margins, exit multiples); without '
                    'calibrated.pe the calibrated lens holds the multiple of the new price')
    q['repriced'] = info
    return q


CALIBRATED_RULE_NAME = 'calibrated_rule.json'   # a sidecar beside lean_params.json, read like worlds.json (16.11.2026)


def apply_calibrated_rule(p, rule):
    """A copy of the params with a calibrated-rule sidecar applied: calibrated.pe_rule = rule["pe_rule"]. A fixed
    calibrated.pe of the file is dropped from the copy (kept as calibrated.replaced_pe, with its why as replaced_why),
    because a fixed multiple cannot move with the price. calibrated.m stays. The saved params file is never edited.
    Raises ParamsError when the sidecar names no known rule."""
    if not isinstance(rule, dict) or rule.get('pe_rule') not in PE_RULES:
        raise ParamsError([f'the calibrated rule must be {{"pe_rule": one of {", ".join(PE_RULES)}, "why": text}} '
                           f'(got {rule.get("pe_rule") if isinstance(rule, dict) else rule!r})'])
    q = copy.deepcopy(p)
    cal = dict(q.get('calibrated') or {})
    if 'pe' in cal:
        cal['replaced_pe'] = cal.pop('pe')
        cal['replaced_why'] = cal.get('why', '')
    cal['pe_rule'] = rule['pe_rule']
    cal['why'] = rule.get('why') or cal.get('why', '')
    q['calibrated'] = cal
    return q


def _ke_path(p, n):
    k0 = p['rf'] + p['beta'] * p['erp']
    km = p['rf'] + p['beta_mature'] * p['erp']
    return [k0 + (km - k0) * min(i, 10) / 10 for i in range(n)]


def _operating(p, d, tg=None):
    """The operating path of one scenario: revenue, operating profit, NOPAT, free cash flow (6 forecast years and 9
    fade years), the cost-of-equity path and the terminal value. tg: None = the risk-free rate."""
    tax = p['tax']
    rev = [p['rev0_m']]
    opi, nopat, fcf = [], [], []
    charge = {int(k): v for k, v in (d.get('charge') or {}).items()}
    for i, g in enumerate(d['g']):
        r = rev[-1] * (1 + g)
        rev.append(r)
        oi = r * d['m'][i]
        opi.append(oi)
        n = oi * (1 - tax)
        nopat.append(n)
        fcf.append(n * d['conv'][i] - charge.get(i, 0.0))
    tg, roic = (p['rf'] if tg is None else tg), p['roic_terminal']
    g_last, n_last = d['g'][-1], opi[-1] * (1 - tax)
    for j in range(1, 10):
        g = g_last + (tg - g_last) * j / 9
        n_last *= 1 + g
        fcf.append(n_last * (1 - g / roic))
    ke = _ke_path(p, len(fcf) + 1)
    tv = n_last * (1 + tg) * (1 - tg / roic) / (ke[len(fcf)] - tg)
    return rev, opi, nopat, fcf, ke, tv


def run_scenario(p, d, detail=False, tg=None):
    """One scenario. Revenue index 0 = base year; g/m/conv index i = base year + 1 + i (6 years).

    tg: terminal growth for the sensitivity rows only; None = the risk-free rate, the model's rule.
    A scenario with a "funding" plan runs in _run_funded (absent = this path, unchanged)."""
    if d.get('funding'):
        return _run_funded(p, d, detail, tg)
    tax, sh = p['tax'], d.get('shares_m', p['shares_m'])  # per-scenario shares (e.g. warrants vesting with the deals)
    rev, opi, nopat, fcf, ke, tv = _operating(p, d, tg)
    eps_op = [n / sh for n in nopat]
    cash = [p['net_cash_m']]
    for i in range(5):
        c = cash[-1]
        rate = p['cash_yield'] if c >= 0 else p.get('debt_rate', p['cash_yield'])
        cash.append(c + fcf[i] + c * rate * (1 - tax))

    def dcf_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, len(fcf)):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + tv / disc + cash[t]) / sh

    exit_op = d['pe'] * eps_op[5] * sh

    def mult_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, 5):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + exit_op / disc + cash[t]) / sh

    dcf = [dcf_at(t) for t in range(6)]
    mul = [mult_at(t) for t in range(6)]
    val = [max((a + b) / 2, 0.01) for a, b in zip(dcf, mul)]   # equity cannot go below zero: a negative path means the scenario needs a restructuring the model does not show
    today = val[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    ret = (val[5] / p['price']) ** (1 / p['years_to_horizon']) - 1
    y0 = p['base_year']
    res = {
        'revenue_m': {y0 + i: round(x) for i, x in enumerate(rev)},
        'op_margin': {y0 + 1 + i: m for i, m in enumerate(d['m'])},
        'eps_operating': {y0 + 1 + i: round(x, 2) for i, x in enumerate(eps_op)},
        'eps_with_interest': {y0 + 1 + i: round((opi[i] + cash[i] * (p['cash_yield'] if cash[i] >= 0 else p.get('debt_rate', p['cash_yield']))) * (1 - tax) / sh, 2) for i in range(6)},
        'dcf_today': round(dcf[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'multiple_today': round(mul[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'value_today': round(today, 2),
        'year_end_values': {y0 + t: round(val[t], 2) for t in range(1, 6)},
        'annual_return_pct': round(ret * 100, 1),
        'ke0_pct': round(ke[0] * 100, 2),
    }
    if detail:   # unrounded internals for the reporting keys; the result above is unchanged
        return res, {'shares_m': sh, 'nopat': [x * (1 - tax) for x in opi], 'fcf': fcf[:5], 'cash': cash,
                     'val_raw': [(a + b) / 2 for a, b in zip(dcf, mul)], 'val': val, 'today': today, 'ret': ret,
                     'dcf': dcf, 'mul': mul, 'eps_op': eps_op, 'ke': ke}
    return res


def _schedule(f, key):
    return {int(k): float(v) for k, v in (f.get(key) or {}).items() if v}


def _run_funded(p, d, detail=False, tg=None):
    """run_scenario with a funding plan (docstring, "funding"): the same operating path; the named equity and debt are
    in the net-cash path, the new shares in the share count. Values are totals first, then per share on the plan's
    full share count."""
    f = d['funding']
    tax, sh0, y0 = p['tax'], d.get('shares_m', p['shares_m']), p['base_year']
    rd = f.get('debt_rate', p.get('debt_rate', p['cash_yield']))
    eq, dt = _schedule(f, 'equity_raise_m'), _schedule(f, 'debt_raise_m')
    rev, opi, nopat, fcf, ke, tv = _operating(p, d, tg)
    # bal = net cash before the named debt (earns cash_yield, or pays rd below zero); debt = the named debt (pays rd)
    bal, debt = p['net_cash_m'] + eq.get(0, 0.0) + dt.get(0, 0.0), dt.get(0, 0.0)
    cash, owed, interest = [bal - debt], [debt], []
    for i in range(6):
        rate = p['cash_yield'] if bal >= 0 else rd
        interest.append((bal * rate - debt * rd) * (1 - tax))   # after tax, during model year i
        if i < 5:
            bal += fcf[i] + interest[i] + eq.get(i + 1, 0.0) + dt.get(i + 1, 0.0)
            debt += dt.get(i + 1, 0.0)
            cash.append(bal - debt)
            owed.append(debt)

    def equity_later(t):   # equity arriving after year-end t, discounted to t at the cost of equity
        v, disc = 0.0, 1.0
        for j in range(t + 1, 6):
            disc *= 1 + ke[j - 1]
            v += eq.get(j, 0.0) / disc
        return v

    def dcf_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, len(fcf)):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return v + tv / disc + cash[t] + equity_later(t)

    exit_total = d['pe'] * nopat[5]

    def mult_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, 5):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return v + exit_total / disc + cash[t] + equity_later(t)

    dcf_t, mul_t = [dcf_at(t) for t in range(6)], [mult_at(t) for t in range(6)]
    val_t = [(a + b) / 2 for a, b in zip(dcf_t, mul_t)]
    basis, failed = f.get('issue_price'), False
    if not eq:
        px = {}
    elif basis == 'model':
        share = sum(a / val_t[j] for j, a in eq.items()) if all(val_t[j] > 0 for j in eq) else 1.0
        if share < 1:
            total = sh0 / (1 - share)
            px = {j: val_t[j] / total for j in eq}
        else:   # the raise is worth the whole company or more at the model's value: no such price
            failed, px = True, {j: 0.01 for j in eq}
    else:
        px = {j: float(basis) for j in eq}
    new = {j: a / px[j] for j, a in eq.items()}
    sh = sh0 + sum(new.values())
    sh_year = [sh0 + sum(n for j, n in new.items() if j <= i) for i in range(6)]
    eps_op = [nopat[i] / sh_year[i] for i in range(6)]
    dcf, mul = [x / sh for x in dcf_t], [x / sh for x in mul_t]
    val_raw = [x / sh for x in val_t]
    val = [max(x, 0.01) for x in val_raw]
    stub = (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    today = val[0] / stub
    ret = (val[5] / p['price']) ** (1 / p['years_to_horizon']) - 1
    plan = {'shares_m_before': sh0, 'new_shares_m': round(sh - sh0, 2), 'shares_m': round(sh, 2),
            'equity_raise_m': {y0 + j: a for j, a in sorted(eq.items())},
            'issue_price': {y0 + j: round(px[j], 2) for j in sorted(eq)},
            'issue_price_basis': None if not eq else ('model' if basis == 'model' else 'given'),
            'debt_raise_m': {y0 + j: a for j, a in sorted(dt.items())}, 'debt_rate': rd,
            'debt_m': {y0 + t: round(x) for t, x in enumerate(owed)},
            'note': ('money by the year-end it lands (key 0 = the base year-end); values per share on the full share count '
                     'of the plan, EPS on the shares out in each year')}
    if f.get('why'):
        plan['why'] = f['why']
    if failed:
        plan['issue_price_model_failed'] = ('the raise is worth the whole company or more at the model\'s value: no issue '
                                            'price exists; priced at 0.01, which leaves the holders nothing')
    res = {
        'revenue_m': {y0 + i: round(x) for i, x in enumerate(rev)},
        'op_margin': {y0 + 1 + i: m for i, m in enumerate(d['m'])},
        'eps_operating': {y0 + 1 + i: round(x, 2) for i, x in enumerate(eps_op)},
        'eps_with_interest': {y0 + 1 + i: round((nopat[i] + interest[i]) / sh_year[i], 2) for i in range(6)},
        'dcf_today': round(dcf[0] / stub, 2),
        'multiple_today': round(mul[0] / stub, 2),
        'value_today': round(today, 2),
        'year_end_values': {y0 + t: round(val[t], 2) for t in range(1, 6)},
        'annual_return_pct': round(ret * 100, 1),
        'ke0_pct': round(ke[0] * 100, 2),
        'funding': plan,
    }
    if detail:
        return res, {'shares_m': sh, 'nopat': nopat, 'fcf': fcf[:5], 'cash': cash, 'val_raw': val_raw, 'val': val,
                     'today': today, 'ret': ret, 'dcf': dcf, 'mul': mul, 'eps_op': eps_op, 'ke': ke,
                     'fin': [eq.get(t + 1, 0.0) for t in range(5)], 'debt_in': [dt.get(t + 1, 0.0) for t in range(5)],
                     'debt': owed, 'debt_rate': rd}
    return res


BANDS = {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0}   # owner's bands on the base-case yearly return (CLAUDE.md, 05.11.2026)


def band(ret_pct, bands=None):
    b = bands or BANDS
    if ret_pct >= b['buy']:
        return 'buy'
    if ret_pct >= b['hold']:
        return 'hold'
    if ret_pct >= b['lagging']:
        return 'lagging'
    return 'sell'


PE_RULES = ('net_of_cash',)   # calibrated.pe_rule (16.11.2026): a held multiple tied to the price, see _held_pe


def _net_cash_per_share(p, d):
    """Net cash at the base year-end per share of the scenario (negative = net debt per share)."""
    return p['net_cash_m'] / d.get('shares_m', p['shares_m'])


def _held_pe(p, d, price, eps0):
    """The multiple the calibrated lens holds at a given price, by the params' rule (None: no multiple to hold).

    Default: price / first-year operating EPS. calibrated.pe_rule "net_of_cash": (price - net cash per share) / first-year
    operating EPS, because the exit value adds the cash at face on top of a multiple of earnings before interest; the
    default counts the cash twice for a cash-rich company and too little for an indebted one. Either rule is recomputed
    at any trial price, so the buy-test rerun and a reprice hold the multiple of that price."""
    if not eps0 > 0:
        return None
    if ((p.get('calibrated') or {}).get('pe_rule')) == 'net_of_cash':
        return (price - _net_cash_per_share(p, d)) / eps0
    return price / eps0


def _calibrated_inputs(p, d):
    cal = p.get('calibrated') or {}
    first = run_scenario(p, d)
    eps0 = first['eps_operating'][min(first['eps_operating'])]
    pe_today = p['price'] / eps0 if eps0 > 0 else None
    pe = cal['pe'] if 'pe' in cal else _held_pe(p, d, p['price'], eps0)
    if pe is not None and pe <= 0:
        pe = None   # net_of_cash at a price under the net cash per share: no positive multiple to hold
    m = cal.get('m') or [max(d['m'][0], x) for x in d['m']]
    return cal, eps0, pe_today, pe, m


def calibrated_lens(p, d):
    """The base case with today's forward P/E held and the margin floored at the first forecast year (see the docstring)."""
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, d)
    out = {'pe_today': round(pe_today, 1) if pe_today else None, 'pe_used': round(pe, 1) if pe else None,
           'pe_overridden': 'pe' in cal, 'margin_used': [round(x, 4) for x in m], 'margin_overridden': bool(cal.get('m')),
           'why': cal.get('why', '')}
    if cal.get('pe_rule'):   # new keys only when the rule is set: a lens without it is key for key what it was
        out.update(pe_rule=cal['pe_rule'], pe_held_exact=round(pe, 3) if pe else None,
                   net_cash_per_share=round(_net_cash_per_share(p, d), 2))
    if pe is None:
        out['note'] = ('the price is not above the net cash per share: no positive multiple to hold (calibrated.pe_rule '
                       'net_of_cash)' if cal.get('pe_rule') and eps0 > 0 else
                       'no positive first-year EPS: the calibrated lens needs "calibrated.pe"')
        return out
    r = run_scenario(p, dict(d, pe=pe, m=m))
    out.update(value_today=r['value_today'], year_end_values=r['year_end_values'], annual_return_pct=r['annual_return_pct'])
    if pe_today and pe_today > 40 and 'pe' not in cal and not cal.get('pe_rule'):
        out['note'] = 'today\'s multiple is above 40: holding it for five years is not credible; set "calibrated.pe"'
    elif cal.get('pe_rule') and pe > 40:
        out['note'] = 'the held multiple is above 40: holding it for five years is not credible; set "calibrated.pe"'
    return out


# ---- reporting (15.11.2026): reads the model above, changes none of its numbers ----

BUY_TEST = {'bull': 17.0, 'bear': -10.0}   # with the 'buy' band for the base in both lenses (owner's sleeve rule, 05.11.2026)
MARGIN_OF_SAFETY_PCT = 13.0   # the one fixed margin-of-safety return (research/technical/FUNDAMENTAL_HANDOFF_he.md)
NOISE_NOTE = ('the 1-2 year value is the noisiest number here: over one or two years the price follows the multiple and '
              'the news far more than the value; the 5-year value rests on the business path')


def _price_at(end_value, hurdle_pct, years, factor=None):
    """The one formula for every entry price: year-end value at the horizon / (1 + hurdle) ^ years.

    factor: 1 + return written exactly as buy_price / index_parity_price write it, so those rungs equal those keys."""
    return round(end_value / (factor or 1 + hurdle_pct / 100) ** years, 2)


def _end_values(p, out, cal):
    last = p['base_year'] + 5
    ends = {k: out[k]['year_end_values'][last] for k in out}
    ends['base_calibrated'] = cal['year_end_values'][last] if 'year_end_values' in cal else None
    return ends


def _repriced(p, base, hurdle_pct):
    """Calibrated entry price when the multiple held is the one of that price (what a rerun at that price shows).

    The end value is linear in the held multiple, so two runs give the line and one more checks it.
    None: no such price (holding the price's own multiple, the lens is above or below the hurdle at every price)."""
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, base)
    if 'pe' in cal or not pe_today:
        return None

    def end(price):   # the held multiple of that price (linear in the price under either rule)
        return run_scenario(p, dict(base, pe=_held_pe(p, base, price, eps0), m=m), detail=True)[1]['val_raw'][5]

    return _solve_rerun(end, p['price'], (1 + hurdle_pct / 100) ** p['years_to_horizon'])


def _solve_rerun(end, price, k):
    """The price x with end(x) = k * x, for an end value linear in the trial price (two runs give the line, one more
    checks it). None: no such price."""
    p1, p2 = price, price / 2
    slope = (end(p1) - end(p2)) / (p1 - p2)
    if k - slope <= 1e-9:
        return None
    x = (end(p1) - slope * p1) / (k - slope)
    if x <= 0 or abs(end(x) - k * x) > 0.005 * k * x:
        return None
    return round(x, 2)


def price_ladder(p, out, cal, bands=None):
    b = bands or BANDS
    years, ends = p['years_to_horizon'], _end_values(p, out, cal)
    idx = round((p['rf'] + p['erp']) * 100, 2)   # the label; the rung price uses the unrounded return
    mos = round(p['buy_return'] * 100, 2)
    spec = [('buy_test', f'buy test: base return {b["buy"]:g}% a year', b['buy'], None),
            ('margin_of_safety', f'entry with a margin of safety: buy_return {mos:g}% a year', mos, 'buy_price'),
            ('margin_of_safety_13', f'margin of safety: fixed {MARGIN_OF_SAFETY_PCT:g}% a year for every company',
             MARGIN_OF_SAFETY_PCT, None),
            ('index', f'index return (rf + erp) {idx:g}% a year', idx, 'index_parity_price'),
            ('hold_floor', f'hold floor: {b["hold"]:g}% a year', b['hold'], None),
            ('sell_line', f'sell line: {b["lagging"]:g}% a year', b['lagging'], None)]
    rungs = {}
    for key, label, h, same in sorted(spec, key=lambda s: -s[2]):
        f = {'margin_of_safety': 1 + p['buy_return'], 'index': 1 + p['rf'] + p['erp']}.get(key)
        rung = {'label': label, 'return_pct': h, 'ours': _price_at(ends['base'], h, years, f),
                'calibrated': _price_at(ends['base_calibrated'], h, years, f) if ends['base_calibrated'] is not None else None}
        if same:
            rung['same_as'] = same
        if key == 'margin_of_safety':   # the label is kept as saved results hold it; this is what the rung is
            rung['meaning'] = f'company hurdle (buy_return): {mos:g}% a year'
            rung['note'] = 'not the fixed margin of safety: that is the margin_of_safety_13 rung'
        if key == 'margin_of_safety_13':   # 16.11.2026: the label is kept as saved results hold it
            rung['meaning'] = f'the base case alone at {MARGIN_OF_SAFETY_PCT:g}% a year'
            rung['note'] = ('not the margin-of-safety price: that is buy_test.margin_of_safety_price (all four buy-test legs '
                            f'with the base at {MARGIN_OF_SAFETY_PCT:g}%), never above the buy-test price')
        rungs[key] = rung
    return {'method': 'price today = year-end value at the horizon / (1 + return) ^ years_to_horizon; one return per rung',
            'horizon_year': p['base_year'] + 5, 'years_to_horizon': years, 'price': p['price'],
            'end_value': {'ours': ends['base'], 'calibrated': ends['base_calibrated']},
            'rungs': rungs,
            'calibrated_note': ('the calibrated rungs hold the multiple of today\'s price (or calibrated.pe) to the horizon; '
                                'after a price move a rerun holds the new multiple (see buy_test.rerun)')}


def buy_test(p, out, cal, base, fund, bands=None, rerun=None):
    """rerun: a function hurdle % -> the calibrated price a rerun confirms (the worlds pass their weighted one);
    None = _repriced on p and base."""
    b = bands or BANDS
    last = p['base_year'] + 5
    years, ends = p['years_to_horizon'], _end_values(p, out, cal)
    spec = [('base_ours', 'base', out.get('base', {}).get('annual_return_pct'), b['buy']),
            ('base_calibrated', 'base_calibrated', cal.get('annual_return_pct'), b['buy']),
            ('bull', 'bull', out.get('bull', {}).get('annual_return_pct'), BUY_TEST['bull']),
            ('bear', 'bear', out.get('bear', {}).get('annual_return_pct'), BUY_TEST['bear'])]
    legs = {}
    for key, end_key, ret, thr in spec:
        end = ends.get(end_key)
        legs[key] = {'return_pct': ret, 'threshold_pct': thr, 'pass': None if ret is None else ret >= thr,
                     'max_price': None if end is None else _price_at(end, thr, years)}
        if last in fund['scenarios'].get(end_key, {}).get('equity_wiped_year_ends', []):
            legs[key].update(max_price=None, equity_wiped_at_horizon=True)   # no price passes a wiped scenario
    res = {'rule': (f'base >= {b["buy"]:g}% a year in both lenses, bull >= {BUY_TEST["bull"]:g}%, bear >= '
                    f'{BUY_TEST["bear"]:g}% (yearly returns from today\'s price to the horizon year-end)'),
           'price': p['price'], 'legs': legs}
    res['complete'] = all(v['pass'] is not None for v in legs.values())
    res['pass'] = res['complete'] and all(v['pass'] for v in legs.values())
    priced = {k: v['max_price'] for k, v in legs.items() if v['max_price'] is not None}
    wiped = [k for k, v in legs.items() if v.get('equity_wiped_at_horizon')]
    if wiped:
        res.update(max_price_passing=None, binding_leg=wiped[0],
                   no_price_passes=f'the {wiped[0]} case wipes the equity at the horizon: no price passes that leg')
    elif priced:
        res['binding_leg'] = min(priced, key=priced.get)
        res['max_price_passing'] = priced[res['binding_leg']]
        res['max_price_vs_price_pct'] = round((res['max_price_passing'] / p['price'] - 1) * 100, 1)
        if res['pass'] and p['price'] > res['max_price_passing']:
            res['on_the_line'] = True   # the legs compare returns rounded to 0.1 point, the price is the exact one
    rerun_fn = rerun
    can_rerun = (not wiped and legs['base_calibrated']['max_price'] is not None
                 and 'pe' not in (p.get('calibrated') or {}))
    if can_rerun:
        # the calibrated leg holds today's multiple; at another price a rerun holds that price's multiple
        rp = rerun_fn(b['buy']) if rerun_fn else _repriced(p, base, b['buy'])
        rerun = dict(priced, base_calibrated=rp) if rp is not None else {k: v for k, v in priced.items() if k != 'base_calibrated'}
        lead = min(rerun, key=rerun.get)
        res['rerun'] = {'calibrated_max_price': rp, 'max_price_passing': rerun[lead], 'binding_leg': lead,
                        'lower_than_stated': rerun[lead] < res['max_price_passing'] * 0.99,
                        'note': ('the calibrated leg with the multiple of its own price held, which is what a rerun at that '
                                 'price shows; calibrated_max_price null = holding the price\'s own multiple the lens never '
                                 'crosses the hurdle, so the leg is left out')}
    if not res['complete']:
        res['note'] = 'a leg has no value (no calibrated lens or a missing scenario): the test cannot pass'
    res.update(_margin_of_safety(p, legs, ends, years, wiped, res, rerun_fn if can_rerun else None,
                                 base if can_rerun and not rerun_fn else None))
    return res


def _margin_of_safety(p, legs, ends, years, wiped, res, rerun_fn, base):
    """The margin-of-safety price (16.11.2026): the buy test's four legs with the base raised to 13% a year in both
    lenses (bull >= 17% and bear >= -10% unchanged), the highest price at which all four pass, and the rerun price when
    the model warns, exactly as the buy-test price at 12%. So it is never above the buy-test price. New keys only."""
    h = MARGIN_OF_SAFETY_PCT
    out = {'margin_of_safety_rule': (f'the buy test with the base at {h:g}% a year in both lenses (bull >= {BUY_TEST["bull"]:g}%, '
                                     f'bear >= {BUY_TEST["bear"]:g}%): the highest price at which all four legs pass; the '
                                     'rerun price when a rerun at that price holds a lower multiple'),
           'margin_of_safety_price': None, 'margin_of_safety_binding_leg': None, 'margin_of_safety_stated_price': None}
    if wiped:
        out.update(margin_of_safety_binding_leg=wiped[0],
                   margin_of_safety_note=f'the {wiped[0]} case wipes the equity at the horizon: no price passes that leg')
        return out
    ends_of = {'base_ours': 'base', 'base_calibrated': 'base_calibrated'}
    priced = {}
    for k, leg in legs.items():
        if k in ends_of:
            end = ends.get(ends_of[k])
            mp = None if end is None else _price_at(end, h, years)
        else:
            mp = leg['max_price']
        if mp is not None:
            priced[k] = mp
    if not priced:
        return out
    lead = min(priced, key=priced.get)
    out.update(margin_of_safety_stated_price=priced[lead], margin_of_safety_price=priced[lead], margin_of_safety_binding_leg=lead)
    if rerun_fn or base is not None:
        rp = rerun_fn(h) if rerun_fn else _repriced(p, base, h)
        rr = dict(priced, base_calibrated=rp) if rp is not None else {k: v for k, v in priced.items() if k != 'base_calibrated'}
        if rr:
            lead_r = min(rr, key=rr.get)
            lower = rr[lead_r] < priced[lead] * 0.99
            out['margin_of_safety_rerun'] = {'calibrated_max_price': rp, 'max_price_passing': rr[lead_r], 'binding_leg': lead_r,
                                             'lower_than_stated': lower, 'used': lower}
            if lower:
                out.update(margin_of_safety_price=rr[lead_r], margin_of_safety_binding_leg=lead_r)
    # never above the buy-test price (the one the record publishes: the rerun price when the model warns at 12%)
    rr12 = res.get('rerun') or {}
    bt = rr12['max_price_passing'] if rr12.get('lower_than_stated') else res.get('max_price_passing')
    if bt is not None and out['margin_of_safety_price'] > bt:
        out.update(margin_of_safety_price=bt, margin_of_safety_capped_at_buy_test=True)
    return out


def ranges(p, out, cal):
    y0, price = p['base_year'], p['price']

    def row(t, years):
        r = {'year': y0 + t, 'years_from_today': round(years, 2)}
        vals = {k: out[k]['year_end_values'][y0 + t] for k in out}
        if 'year_end_values' in cal:
            vals['base_calibrated'] = cal['year_end_values'][y0 + t]
        r.update(vals)
        if t == 5:
            r['return_pct'] = {k: (out[k] if k in out else cal)['annual_return_pct'] for k in vals}
        elif years >= 0.25:   # under a quarter a yearly rate says nothing
            r['return_pct'] = {k: round(((v / price) ** (1 / years) - 1) * 100, 1) for k, v in vals.items()}
        return r

    today = {k: out[k]['value_today'] for k in out}
    if 'value_today' in cal:
        today['base_calibrated'] = cal['value_today']
    return {'today': today, 'two_year': row(2, p['years_to_horizon'] - 3), 'five_year': row(5, p['years_to_horizon']),
            'note': NOISE_NOTE}


def calibrated_diagnostics(p, base, out, cal_out):
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, base)
    y0 = p['base_year']
    changed = [y0 + 1 + i for i, (a, b) in enumerate(zip(m, base['m'])) if abs(a - b) > 1e-12]
    res = {'margin_changed': bool(changed), 'margin_years_changed': changed,
           'margin_floor_binds': bool(changed) and not cal.get('m'),
           'differs_from_base_only_by_multiple': not changed,
           'pe_held': round(pe, 2) if pe else None, 'pe_base_exit': base['pe'],
           'pe_held_to_base_exit': round(pe / base['pe'], 2) if pe and base['pe'] else None,
           'first_year_eps_operating': eps0,
           'first_year_eps_to_price_pct': round(eps0 / p['price'] * 100, 2)}
    reasons = []
    if res['pe_held_to_base_exit'] is not None and pe / base['pe'] > 1.5:
        reasons.append(f'the held multiple {pe:.1f} is {pe / base["pe"]:.2f}x the base exit multiple {base["pe"]:g} (above 1.5x)')
    if 'pe' not in cal and 0 < eps0 < 0.02 * p['price']:   # no positive EPS: no multiple to hold (the lens says so in its note)
        reasons.append(f'first-year operating EPS {eps0:g} is {eps0 / p["price"] * 100:.1f}% of the price (under 2%): '
                       'today\'s multiple may be inflated by depressed earnings')
    res['inflated_multiple_warning'] = bool(reasons)
    res['warning_reasons'] = reasons
    if 'value_today' in cal_out:
        ours = out['base']
        only_pe, d_pe = run_scenario(p, dict(base, pe=pe), detail=True)
        _, d_ours = run_scenario(p, base, detail=True)
        _, d_cal = run_scenario(p, dict(base, pe=pe, m=m), detail=True)
        res['gap_to_base'] = {
            'value_today': round(cal_out['value_today'] - ours['value_today'], 2),
            'value_today_from_multiple': round(d_pe['today'] - d_ours['today'], 2),
            'value_today_from_margin': round(d_cal['today'] - d_pe['today'], 2),
            'return_pts': round(cal_out['annual_return_pct'] - ours['annual_return_pct'], 1),
            'return_pts_from_multiple': round((d_pe['ret'] - d_ours['ret']) * 100, 1),
            'return_pts_from_margin': round((d_cal['ret'] - d_pe['ret']) * 100, 1)}
    if pe is None:
        res['reading'] = 'no positive first-year EPS: there is no multiple to hold and no calibrated lens; set "calibrated.pe"'
    elif reasons:
        res['reading'] = 'the held multiple looks inflated: the calibrated lens is not a second opinion here; set "calibrated.pe"'
    elif not changed:
        res['reading'] = 'the margin floor does not bind: the two lenses differ only by the exit multiple'
    else:
        res['reading'] = 'the lenses differ by the exit multiple and by the margin'
    return res


def funding(p, scenarios):
    y0, res = p['base_year'], {}
    for k, d in scenarios.items():
        r0, det = run_scenario(p, d, detail=True)
        cash, nopat = det['cash'], det['nopat']
        i = min(range(len(cash)), key=lambda j: cash[j])
        low = cash[i]
        profit = nopat[max(i - 1, 0)]   # the low-point year's after-tax operating profit (base year-end: the first forecast year)
        net_debt = max(-low, 0.0)
        outside = max(min(cash[0], 0.0) - low, 0.0)   # borrowing beyond today's balance sheet
        plan = r0.get('funding')
        if plan:   # a funding plan: its equity is in the path; the debt it names is not outside money it leaves open
            outside_before = outside
            outside = max(outside - det['debt'][i], 0.0)
        reasons = []
        if net_debt > 0 and (profit <= 0 or net_debt > 3 * profit):
            reasons.append(f'net debt {net_debt:,.0f}M at the low point ({y0 + i}) is above 3x that year\'s after-tax operating '
                           f'profit ({profit:,.0f}M)')
        if outside > 0.5:
            size = f' ({outside / profit:.1f}x that year\'s after-tax operating profit)' if profit > 0 else ''
            if plan:
                reasons.append(f'the path needs {outside:,.0f}M of outside money by {y0 + i}{size} beyond the funding plan: the '
                               f'model carries it as debt at {plan["debt_rate"]:g} and does not check that it can be raised')
            else:
                reasons.append(f'the path needs {outside:,.0f}M of outside money by {y0 + i}{size} that is not in shares or net '
                               'cash: the model carries it as debt at debt_rate and does not check that it can be raised')
        row = {'net_cash_m': {y0 + j: round(c) for j, c in enumerate(cash)},
               'fcf_m': {y0 + 1 + j: round(x) for j, x in enumerate(det['fcf'])},
               'low_point_m': round(low), 'low_point_year': y0 + i,
               'low_year_nopat_m': round(profit), 'low_year_nopat_year': y0 + max(i, 1),
               'net_debt_to_nopat_at_low': round(net_debt / profit, 2) if net_debt > 0 and profit > 0 else None,
               'outside_money_m': round(outside),
               'outside_money_to_nopat': round(outside / profit, 2) if outside > 0.5 and profit > 0 else None,
               'shares_m': det['shares_m'],
               'flag': bool(reasons), 'reasons': reasons}
        if plan:
            row.update(funding_plan=plan, outside_money_before_named_debt_m=round(outside_before))
        if det['val_raw'][0] <= 0:
            row['equity_wiped'] = True   # value today is zero or less: say 'equity wiped', never a negative price
        wiped = [y0 + t for t in range(1, 6) if det['val_raw'][t] <= 0]
        if wiped:
            row['equity_wiped_year_ends'] = wiped
        res[k] = row
    return {'note': ('the model\'s own path: net cash at face at the base year-end, plus each year\'s free cash flow and the '
                     'after-tax interest on the balance; shares are fixed in each scenario'),
            'scenarios': res}


SENSITIVITY_STEPS = (
    ('operating margin', '1 point every year', 'm', 0.01),
    ('revenue growth', '1 point every year', 'g', 0.01),
    ('exit P/E', '2 turns', 'pe', 2.0),
    ('beta', '0.1', 'beta', 0.1),
    ('free-cash-flow conversion', '5 points every year', 'conv', 0.05),
)


def sensitivity(p, base):
    r0, d0 = run_scenario(p, base, detail=True)
    floored = d0['val_raw'][0] <= 0

    def side(key, step):
        if key == 'beta':
            r, det = run_scenario(dict(p, beta=p['beta'] + step), base, detail=True)
        elif key == 'pe':
            r, det = run_scenario(p, dict(base, pe=base['pe'] + step), detail=True)
        else:
            r, det = run_scenario(p, dict(base, **{key: [x + step for x in base[key]]}), detail=True)
        dv = det['today'] - d0['today']
        return {'value_today': r['value_today'], 'd_value': round(dv, 2),
                'd_value_pct': None if floored else round(dv / d0['today'] * 100, 1),
                'annual_return_pct': r['annual_return_pct'], 'd_return_pts': round((det['ret'] - d0['ret']) * 100, 2)}

    rows = []
    for name, change, key, step in SENSITIVITY_STEPS:
        down, up = side(key, -step), side(key, step)
        rows.append({'input': name, 'change': '+/- ' + change, 'down': down, 'up': up,
                     'abs_effect_value': max(abs(down['d_value']), abs(up['d_value']))})
    rows.sort(key=lambda r: -r['abs_effect_value'])
    return {'note': ('one input changed at a time on the base case, the others held: this is the model\'s own arithmetic '
                     '(what moves the value most), not a forecast and not a probability'),
            'base': {'value_today': r0['value_today'], 'annual_return_pct': r0['annual_return_pct']},
            'rows': rows, 'moves_most': rows[0]['input']}


def reverse_reading(p, base, res):
    """What the price needs: on the base margin path (the existing keys) and at a margin held flat at the first year."""
    y0, price = p['base_year'], p['price']
    tol = max(0.02, 0.005 * price)
    flat = dict(base, m=[base['m'][0]] * len(base['m']))
    d = _reverse_growth(p, flat)
    r = run_scenario(p, d)
    as_modelled = _reverse_growth(p, base)
    return {
        'base_growth_pct': [round(g * 100, 1) for g in base['g']],
        'base_end_revenue_m': run_scenario(p, base)['revenue_m'][y0 + 5],
        'base_margin_path': {'growth_pct': res['market_implied_growth_pct'],
                             'end_revenue_m': res['market_implied_end_revenue_m'],
                             'solved': abs(run_scenario(p, as_modelled)['value_today'] - price) <= tol},
        'flat_margin': {'margin_held': base['m'][0], 'growth_pct': [round(g * 100, 1) for g in d['g']],
                        'end_revenue_m': r['revenue_m'][y0 + 5], 'solved': abs(r['value_today'] - price) <= tol,
                        'value_today_at_base_growth': run_scenario(p, flat)['value_today']},
        'note': ('the first (guided) year is held and the later years are scaled until the value today equals the price; '
                 'solved false = no scaling between -1x and 4x reaches the price. A reading of the price, not a forecast'),
    }


# ---- reporting, second set (engine upgrade 15.11.2026): new keys only; a wiped scenario is value 0 and return null ----

def _lens_scenarios(p, cal_out):
    """The scenarios plus the calibrated base (as 'base_calibrated') when that lens has a value."""
    sc = dict(p['scenarios'])
    if 'value_today' in cal_out:
        _, _, _, pe, m = _calibrated_inputs(p, p['scenarios']['base'])
        sc['base_calibrated'] = dict(p['scenarios']['base'], pe=pe, m=m)
    return sc


def _pct(end, price, years):
    return round(((end / price) ** (1 / years) - 1) * 100, 1) + 0.0 if end > 0 else None   # + 0.0: never -0.0


def _value_at(points, when):
    """(value, flags) on a dated path: log-linear between the points, as forecast_tracker.value_at does.

    After the last point the last segment's rate is carried on (flag beyond_days). A segment that touches a wiped
    year-end (value 0) gives 0 (flag wiped)."""
    if when <= points[0][0]:
        return points[0][1], ({'wiped': True} if points[0][1] <= 0 else {})
    beyond = when > points[-1][0]
    pairs = [points[-2:]] if beyond else [pq for pq in zip(points, points[1:]) if pq[0][0] <= when <= pq[1][0]][:1]
    (d0, v0), (d1, v1) = pairs[0]
    flags = {'beyond_days': (when - d1).days} if beyond else {}
    if v0 <= 0 or v1 <= 0:
        return 0.0, dict(flags, wiped=True)
    w = (when - d0).days / max((d1 - d0).days, 1)
    return math.exp(math.log(v0) + w * (math.log(v1) - math.log(v0))), flags


def dated_values(p, out, cal_out, inputs):
    """An ISO date on every year-end value, and the values at exactly as_of + 1, 2 and 5 years."""
    as_of, end0 = _date(inputs.get('as_of')), _date(inputs.get('base_year_end'))
    if as_of is None or end0 is None:
        return {'available': False, 'note': 'no ISO as_of in the params: the values cannot be dated'}
    y0, price, days = p['base_year'], p['price'], p['days_to_base_year_end']
    ke0 = out['base']['ke0_pct'] / 100
    paths, year_ends = {}, {y0 + t: {'date': _shift_years(end0, t).isoformat()} for t in range(1, 6)}
    today = {'date': as_of.isoformat()}
    for k, d in _lens_scenarios(p, cal_out).items():
        r = cal_out if k == 'base_calibrated' else out[k]
        raw = run_scenario(p, d, detail=True)[1]['val_raw']
        v_today = r['value_today'] if raw[0] > 0 else 0.0
        today[k] = v_today
        pts = [(as_of, v_today)]
        if end0 > as_of:   # the base year-end, carried from today at the first year's cost of equity (as the tracker does)
            pts.append((end0, v_today * (1 + (r.get('ke0_pct', ke0 * 100) / 100)) ** (days / 365)))
        for t in range(1, 6):
            v = r['year_end_values'][y0 + t] if raw[t] > 0 else 0.0
            year_ends[y0 + t][k] = v
            if _shift_years(end0, t) > as_of:   # a year-end already behind as_of is not on the path from today
                pts.append((_shift_years(end0, t), v))
        paths[k] = pts
    at = {}
    for name, n in (('one_year', 1), ('two_year', 2), ('five_year', 5)):
        when = _shift_years(as_of, n)
        row, rets, wiped = {'date': when.isoformat(), 'years_from_today': n}, {}, []
        for k, pts in paths.items():
            v, flags = _value_at(pts, when)
            row[k] = round(v, 2)
            rets[k] = _pct(v, price, n)
            if flags.get('wiped'):
                wiped.append(k)
            if 'beyond_days' in flags:
                row['beyond_last_year_end_days'] = flags['beyond_days']
        row['return_pct'] = rets
        if wiped:
            row['wiped'] = wiped
        at[name] = row
    return {'available': True, 'as_of': as_of.isoformat(), 'base_year_end': end0.isoformat(),
            'base_year_end_source': inputs.get('base_year_end_source'), 'price': price,
            'today': today, 'year_ends': year_ends, 'at': at,
            'method': ('log-linear (geometric) interpolation between today, the base year-end and the dated year-end values, '
                       'the same rule as forecast_tracker.value_at; return_pct is the yearly rate from today\'s price to the '
                       'value on that date'),
            'note': ('conditional values on each path at a calendar date, not price forecasts; a wiped scenario is 0 with a '
                     'null return; beyond_last_year_end_days = the date is after the last modelled year-end and the last '
                     'year\'s rate was carried on. ' + NOISE_NOTE)}


PE_GAP_RANGE = (0.8, 1.25)   # exit P/E against the P/E the DCF implies: outside this the two methods tell different stories


def method_gap(p, out, cal_out, bands=None):
    """What each of the two methods gives alone, per scenario. The published value stays their plain average."""
    price, years, days = p['price'], p['years_to_horizon'], p['days_to_base_year_end']
    rows, warns = {}, []
    for k, d in _lens_scenarios(p, cal_out).items():
        det = run_scenario(p, d, detail=True)[1]
        stub = (1 + det['ke'][0]) ** (days / 365)

        def alone(series):
            end, ret = series[5], _pct(series[5], price, years)
            row = {'value_today': round(series[0] / stub, 2) if series[0] > 0 else 0.0,
                   'end_value': round(end, 2) if end > 0 else 0.0, 'return_pct': ret,
                   'band': None if ret is None else band(ret, bands)}
            if end <= 0:
                row['wiped'] = True
            return row

        dcf, mul = alone(det['dcf']), alone(det['mul'])
        avg_ret = _pct(det['val_raw'][5], price, years)
        nopat_next = det['eps_op'][5] * det['shares_m']
        implied = (det['dcf'][5] * det['shares_m'] - det['cash'][5]) / nopat_next if nopat_next > 0 else None
        ratio = d['pe'] / implied if implied and implied > 0 else None
        row = {'dcf': dcf, 'exit_multiple': mul,
               'averaged': {'value_today': round(det['today'], 2) if det['val_raw'][0] > 0 else 0.0,
                            'end_value': round(det['val_raw'][5], 2) if det['val_raw'][5] > 0 else 0.0,
                            'return_pct': avg_ret, 'band': None if avg_ret is None else band(avg_ret, bands)},
               'exit_pe': round(d['pe'], 2), 'dcf_implied_exit_pe': None if implied is None else round(implied, 1),
               'exit_pe_to_dcf_implied': None if ratio is None else round(ratio, 2),
               'pe_gap_wide': None if ratio is None else not PE_GAP_RANGE[0] <= ratio <= PE_GAP_RANGE[1],
               'bands_differ': dcf['band'] != mul['band']}
        if row['bands_differ']:
            def say(m):
                return 'equity wiped' if m['return_pct'] is None else f'{m["return_pct"]:g}% a year ({m["band"]})'
            pe = ('' if implied is None else f'; the exit P/E {round(d["pe"], 1):g} against {implied:.1f} implied by the DCF'
                  + ('' if ratio is None else f' ({ratio:.2f}x)'))
            row['warning'] = (f'{k}: the two methods land in different bands: DCF alone {say(dcf)}, exit multiple alone '
                              f'{say(mul)}, averaged {say(row["averaged"])}{pe}. Say which side is believed and why')
            warns.append(row['warning'])
        rows[k] = row
    return {'note': ('each method alone, from the same inputs: return = that method\'s value at the horizon year-end against '
                     'today\'s price; dcf_implied_exit_pe = the DCF\'s value of the business at the horizon (cash taken out) '
                     'over the next year\'s after-tax operating profit, to read against the exit P/E. The published value is '
                     'the plain average of the two; nothing here changes it'),
            'pe_gap_range': list(PE_GAP_RANGE), 'scenarios': rows, 'warnings': warns}


def sensitivity_terminal(p, base, sens):
    """The one-at-a-time table for the terminal inputs that sensitivity() does not cover."""
    r0, d0 = run_scenario(p, base, detail=True)
    floored = d0['val_raw'][0] <= 0
    spread = p['beta_mature'] * p['erp']   # mature cost of equity less the risk-free rate

    def side(q=None, tg=None):
        q = q or p
        g = q['rf'] if tg is None else tg
        if q['beta_mature'] * q['erp'] + q['rf'] - g <= 1e-9 or q['roic_terminal'] <= g:
            return None   # no terminal value at this input: the discount rate or the ROIC is at or below the growth
        r, det = run_scenario(q, base, detail=True, tg=tg)
        dv = det['today'] - d0['today']
        return {'value_today': r['value_today'], 'd_value': round(dv, 2),
                'd_value_pct': None if floored else round(dv / d0['today'] * 100, 1),
                'annual_return_pct': r['annual_return_pct'], 'd_return_pts': round((det['ret'] - d0['ret']) * 100, 2)}

    rf = p['rf']
    rows = [{'input': 'mature beta', 'change': '+/- 0.1 (beta_mature)',
             'down': side(dict(p, beta_mature=p['beta_mature'] - 0.1)), 'up': side(dict(p, beta_mature=p['beta_mature'] + 0.1))},
            {'input': 'terminal ROIC', 'change': '+/- 5 points (roic_terminal)',
             'down': side(dict(p, roic_terminal=p['roic_terminal'] - 0.05)), 'up': side(dict(p, roic_terminal=p['roic_terminal'] + 0.05))},
            {'input': 'terminal growth', 'change': '+/- 0.5 point (the model uses the risk-free rate)',
             'down': side(tg=rf - 0.005), 'up': side(tg=rf + 0.005), 'at_3pct': side(tg=0.03)}]
    for row in rows:
        row['abs_effect_value'] = max([abs(x['d_value']) for x in (row['down'], row['up']) if x] or [0.0])
    rows.sort(key=lambda r: -r['abs_effect_value'])
    both = [(r['input'], r['abs_effect_value']) for r in sens['rows'] + rows]
    top = max(both, key=lambda x: x[1])
    return {'note': ('the terminal inputs, one changed at a time on the base case: model arithmetic, not a forecast; a null '
                     'side means the input leaves no terminal value (discount rate or ROIC at or below the growth); '
                     'at_3pct = terminal growth of 3% instead of the risk-free rate'),
            'base': {'value_today': r0['value_today'], 'annual_return_pct': r0['annual_return_pct'],
                     'beta_mature': p['beta_mature'], 'roic_terminal': p['roic_terminal'], 'terminal_growth': rf,
                     'mature_cost_of_equity_less_growth': round(spread, 4)},
            'rows': rows, 'moves_most': rows[0]['input'],
            'moves_most_overall': {'input': top[0], 'abs_effect_value': top[1]}}


def _paid_out(p, det, rates):
    """The year-5 value per share if the cash above today's net cash (or above zero, for net debt) were paid out at each
    year-end and reinvested at `rates` (one yearly rate per model year). Returns (value, the amounts paid by year index)."""
    tax, cash, fcf = p['tax'], det['cash'], det['fcf']
    floor = max(cash[0], 0.0)   # today's net cash stays in the company; debt is repaid first, as in the model
    bal, paid = cash[0], []
    if 'fin' in det:   # a funding plan: the same path as _run_funded, with the payout taken from the cash
        rd, debt = det['debt_rate'], det['debt'][0]
        gross = cash[0] + debt
        for t in range(5):
            rate = p['cash_yield'] if gross >= 0 else rd
            gross += fcf[t] + (gross * rate - debt * rd) * (1 - tax) + det['fin'][t] + det['debt_in'][t]
            debt += det['debt_in'][t]
            out = max(gross - debt - floor, 0.0)
            gross -= out
            paid.append(out)
        bal = gross - debt
    for t in range(5 if 'fin' not in det else 0):
        rate = p['cash_yield'] if bal >= 0 else p.get('debt_rate', p['cash_yield'])
        bal += fcf[t] + bal * rate * (1 - tax)
        out = max(bal - floor, 0.0)
        bal -= out
        paid.append(out)
    grown = 0.0
    for t, x in enumerate(paid):   # paid at the year-end of model year t + 1, held to the year-end of model year 5
        f = 1.0
        for i in range(t + 1, 5):
            f *= 1 + rates[i]
        grown += x * f
    return det['val_raw'][5] + (bal - cash[5] + grown) / det['shares_m'], paid


def return_with_distributions(p, out, cal_out, bands=None):
    """The 5-year return with the accumulated free cash paid out and reinvested at the cost of equity: a second line."""
    price, years, y0 = p['price'], p['years_to_horizon'], p['base_year']
    index = p['rf'] + p['erp']
    rows, crosses = {}, []
    for k, d in _lens_scenarios(p, cal_out).items():
        det = run_scenario(p, d, detail=True)[1]
        head = (cal_out if k == 'base_calibrated' else out[k])['annual_return_pct']
        if det['val_raw'][5] <= 0:
            rows[k] = {'wiped': True, 'end_value': 0.0, 'return_pct': None, 'end_value_with_distributions': 0.0,
                       'return_with_distributions_pct': None, 'return_with_distributions_at_index_pct': None}
            continue
        end_ke, paid = _paid_out(p, det, det['ke'])
        end_ix, _ = _paid_out(p, det, [index] * 5)
        ret = _pct(end_ke, price, years)
        row = {'end_value': round(det['val_raw'][5], 2), 'return_pct': head,
               'end_value_with_distributions': round(end_ke, 2), 'return_with_distributions_pct': ret,
               'd_return_pts': round(((end_ke / price) ** (1 / years) - (det['val_raw'][5] / price) ** (1 / years)) * 100, 1),
               'return_with_distributions_at_index_pct': _pct(end_ix, price, years),
               'paid_out_m': {y0 + 1 + t: round(x) for t, x in enumerate(paid)},
               'accumulated_cash_share_of_end_value_pct': round((det['cash'][5] - max(det['cash'][0], 0.0)) / det['shares_m']
                                                                / det['val_raw'][5] * 100, 1),
               'band': band(head, bands), 'band_with_distributions': band(ret, bands)}
        if row['band'] != row['band_with_distributions']:
            crosses.append(k)
        rows[k] = row
    return {'note': ('a second line, never the headline: the model leaves each year\'s free cash in the company at the cash '
                     'yield, while the index it is compared with reinvests its distributions. Here the cash above today\'s net '
                     'cash (above zero for a company in net debt, which repays first) is paid out at each year-end and '
                     'reinvested at the cost of equity to the horizon; return_with_distributions_at_index_pct reinvests it '
                     'at the index return instead. Changing the headline needs a preregistered test'),
            'reinvestment_rate_pct': [round(x * 100, 2) for x in run_scenario(p, p['scenarios']['base'], detail=True)[1]['ke'][:5]],
            'index_return_pct': round(index * 100, 2), 'scenarios': rows, 'band_changes': crosses}


def collect_warnings(p, res):
    w = []
    lens = res['two_lens']
    if lens.get('agree') is False:
        w.append(f'lenses disagree (ours {lens["ours"]["band"]}, calibrated {lens["calibrated"]["band"]}): hold, or stage '
                 'the trade around the next report')
    if lens['calibrated'].get('note'):
        w.append('calibrated lens: ' + lens['calibrated']['note'])
    w += ['calibrated lens: ' + x for x in res['calibrated_diagnostics']['warning_reasons']]
    for k, f in res['funding']['scenarios'].items():
        if f.get('equity_wiped'):
            w.append(f'{k}: equity wiped (value today is zero or less; the scenario needs a restructuring the model does not show)')
        elif f.get('equity_wiped_year_ends'):
            w.append(f'{k}: equity wiped at the year-ends {f["equity_wiped_year_ends"]}')
        w += [f'{k} funding: {x}' for x in f['reasons']]
    if res['buy_test'].get('on_the_line'):
        w.append(f'buy test: the pass rests on rounding (the price is above the highest passing price '
                 f'{res["buy_test"]["max_price_passing"]:,.2f}, within 0.05 point of return): read it as on the line')
    rr = res['buy_test'].get('rerun')
    if rr and rr['lower_than_stated']:
        w.append(f'buy test: at the stated highest passing price {res["buy_test"]["max_price_passing"]:,.2f} a rerun would hold a '
                 f'lower multiple; with the multiple of its own price held the highest passing price is '
                 f'{rr["max_price_passing"]:,.2f} (binding leg: {rr["binding_leg"]})')
    for key, label in (('base_margin_path', 'on the base margin path'), ('flat_margin', 'at a flat margin')):
        if not res['reverse_reading'][key]['solved']:
            why = ' (the first-year margin is not positive)' if key == 'flat_margin' and p['scenarios']['base']['m'][0] <= 0 else ''
            w.append(f'reverse reading {label}: no growth path between -1x and 4x of the base reaches the price{why}')
    return w


def add_reporting(p, res, inputs=None):
    """New keys only: every key that existed before keeps its value."""
    if inputs is None:
        inputs = _dates(p)[0]
        inputs['warnings'] = []
    out, base, bands = res['scenarios'], p['scenarios']['base'], p.get('bands')
    cal = res['two_lens']['calibrated']
    res['price_ladder'] = price_ladder(p, out, cal, bands)
    fund = funding(p, p['scenarios'])
    res['buy_test'] = buy_test(p, out, cal, base, fund, bands)
    res['ranges'] = ranges(p, out, cal)
    res['calibrated_diagnostics'] = calibrated_diagnostics(p, base, out, cal)
    res['funding'] = fund
    res['sensitivity'] = sensitivity(p, base)
    res['reverse_reading'] = reverse_reading(p, base, res)
    res['warnings'] = inputs['warnings'] + collect_warnings(p, res)
    # second set (engine upgrade 15.11.2026)
    res['inputs'] = inputs
    res['dated_values'] = dated_values(p, out, cal, inputs)
    res['method_gap'] = method_gap(p, out, cal, bands)
    res['sensitivity_terminal'] = sensitivity_terminal(p, base, res['sensitivity'])
    res['return_with_distributions'] = return_with_distributions(p, out, cal, bands)
    return res


# ---- worlds (engine upgrade P3b, 15.11.2026): weighted values over named variant files; new key "worlds" only ----

WORLD_SAME = ('price', 'as_of', 'base_year', 'days_to_base_year_end', 'years_to_horizon')   # must equal the main file's
WORLD_NOTE = ('rf', 'erp', 'buy_return', 'bands')   # may differ: the weighted ladder and bands use the main file's
LENS_KEYS = ('bear', 'base', 'bull', 'base_calibrated')


def _sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_worlds(spec, base_dir=None):
    """[(entry, params, path)] of a worlds spec ({"worlds": [...]} or the list). File paths are relative to base_dir
    (None = as given). Raises ParamsError with every problem: names, weights that sum to 1, readable files."""
    entries = spec.get('worlds') if isinstance(spec, dict) else spec
    if not isinstance(entries, list) or not entries:
        raise ParamsError(['worlds must be a non-empty list of {"name", "file", "weight"}'])
    problems, names, out, total = [], set(), [], 0.0
    for i, e in enumerate(entries):
        tag = f'world {i + 1}'
        if not isinstance(e, dict):
            problems.append(f'{tag} must be an object {{name, file, weight}}')
            continue
        name = e.get('name')
        if not (isinstance(name, str) and name.strip()):
            problems.append(f'{tag}: name must be a non-empty text')
        else:
            tag = f'world {name!r}'
            if name in names:
                problems.append(f'{tag}: the name is used twice')
            names.add(name)
        w = e.get('weight')
        if not (_num(w) and 0 <= w <= 1):
            problems.append(f'{tag}: weight must be a number from 0 to 1 (got {w!r})')
        else:
            total += w
        fn = e.get('file')
        if not (isinstance(fn, str) and fn):
            problems.append(f'{tag}: file must be the path of a params file (relative to the file that names the worlds)')
            continue
        path = fn if os.path.isabs(fn) or base_dir is None else os.path.join(base_dir, fn)
        try:
            with open(path) as fh:
                wp = json.load(fh)
        except (OSError, ValueError) as ex:
            problems.append(f'{tag}: cannot read {fn} ({ex})')
            continue
        out.append((e, wp, path))
    if not problems and abs(total - 1) > 1e-6:
        problems.append(f'the world weights sum to {total:.6g}, not 1')
    if problems:
        raise ParamsError(problems)
    return out


def _lens_values(res):
    """(today, year_ends, dated 'at' rows or None) of one run, in the convention of the new keys (wiped = 0)."""
    dv = res['dated_values']
    if dv.get('available'):
        today = {k: v for k, v in dv['today'].items() if k in LENS_KEYS}
        ye = {y: {k: v for k, v in row.items() if k in LENS_KEYS} for y, row in dv['year_ends'].items()}
        return today, ye, dv['at']
    fund, today, ye = res['funding']['scenarios'], {}, {}
    for k, r in res['scenarios'].items():
        today[k] = 0.0 if fund[k].get('equity_wiped') else r['value_today']
        for y, v in r['year_end_values'].items():
            ye.setdefault(y, {})[k] = 0.0 if y in fund[k].get('equity_wiped_year_ends', []) else v
    cal = res['two_lens']['calibrated']
    if 'value_today' in cal:
        today['base_calibrated'] = cal['value_today']
        for y, v in cal['year_end_values'].items():
            ye[y]['base_calibrated'] = v
    return today, ye, None


def _calibrated_end_fn(p):
    """The calibrated base's raw year-5 value per share as a function of the trial price (the multiple of that price
    held), or a constant when calibrated.pe fixes it. (function, moves with the price) or (None, False): no lens."""
    base = p['scenarios']['base']
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, base)
    if pe is None:
        return None, False
    if 'pe' in cal or not pe_today:
        const = run_scenario(p, dict(base, pe=pe, m=m), detail=True)[1]['val_raw'][5]
        return (lambda x: const), False
    return (lambda x: run_scenario(p, dict(base, pe=_held_pe(p, base, x, eps0), m=m), detail=True)[1]['val_raw'][5]), True


def worlds_report(p, raw, spec, base_dir=None, source=None):
    """The "worlds" key. p: the main params as run (dates derived); raw: the main params as given (with "repriced"
    when --price / --as-of were used, so every world is repriced the same way)."""
    loaded = load_worlds(spec, base_dir)
    rp = raw.get('repriced') if isinstance(raw.get('repriced'), dict) else None
    problems, notes, worlds = [], [], []
    for e, wp, path in loaded:
        tag = f'world {e["name"]!r}'
        try:
            if rp:
                wp = reprice(wp, raw['price'], rp.get('as_of_requested'))
            q, _ = prepare(wp)
        except ParamsError as ex:
            problems += [f'{tag} ({e["file"]}): {x}' for x in ex.problems]
            continue
        if raw.get('ticker') and wp.get('ticker') and wp['ticker'] != raw['ticker']:
            problems.append(f'{tag}: ticker {wp["ticker"]} is not the main file\'s {raw["ticker"]}')
        for key in WORLD_SAME:
            if q.get(key) != p.get(key):
                problems.append(f'{tag}: {key} {q.get(key)!r} differs from the main file\'s {p.get(key)!r} (the worlds '
                                'must share the price and the dates to be weighted)')
        diff = [key for key in WORLD_NOTE if q.get(key) != p.get(key)]
        if diff:
            notes.append(f'{tag}: {", ".join(diff)} differ from the main file; the weighted ladder and bands use the main file\'s')
        worlds.append((e, wp, q, path))
    if problems:
        raise ParamsError(problems)
    price, years, last, bands = p['price'], p['years_to_horizon'], p['base_year'] + 5, p.get('bands')
    rows, vals = [], []
    for e, wp, q, path in worlds:
        wres = _run_single(wp)
        today, ye, at = _lens_values(wres)
        vals.append((e['weight'], today, ye, at, wres))
        bt, lad = wres['buy_test'], wres['price_ladder']['rungs']
        rows.append({
            'name': e['name'], 'file': e['file'], 'weight': e['weight'], 'sha256': _sha256(path),
            'same_inputs_as_main': wp == raw,
            **{k: e[k] for k in e if k not in ('name', 'file', 'weight')},
            'value_today': today, 'year_end_values': ye,
            'annual_return_pct': {k: _pct(ye[last][k], price, years) for k in ye[last]},
            'at': None if at is None else {n: {k: v for k, v in r.items() if k in LENS_KEYS + ('date', 'return_pct')}
                                           for n, r in at.items()},
            'weighted_value_today': wres['weighted_value_today'],
            'base_band': wres['two_lens']['ours']['band'], 'two_lens_agree': wres['two_lens'].get('agree'),
            'buy_test': {'pass': bt['pass'], 'max_price_passing': bt.get('max_price_passing'),
                         'binding_leg': bt.get('binding_leg'),
                         'rerun_max_price_passing': (bt.get('rerun') or {}).get('max_price_passing'),
                         'rerun_used': bool((bt.get('rerun') or {}).get('lower_than_stated')),
                         'margin_of_safety_price': bt.get('margin_of_safety_price'),
                         'margin_of_safety_binding_leg': bt.get('margin_of_safety_binding_leg')},
            'margin_of_safety_13': {'ours': lad['margin_of_safety_13']['ours'],
                                    'calibrated': lad['margin_of_safety_13']['calibrated']}})
    keys = [k for k in LENS_KEYS if all(k in v[1] for v in vals)]

    def wsum(get):
        return sum(w * get(t, y, a) for w, t, y, a, _ in vals)

    w_today = {k: round(wsum(lambda t, y, a: t[k]), 2) for k in keys}
    w_ye = {yr: {k: round(wsum(lambda t, y, a: y[yr][k]), 2) for k in keys} for yr in vals[0][2]}
    w_ret = {k: _pct(w_ye[last][k], price, years) for k in keys}
    w_at = None
    if all(v[3] is not None for v in vals):
        w_at = {}
        for name, row in vals[0][3].items():
            r = {'date': row['date'], 'years_from_today': row['years_from_today']}
            r.update({k: round(wsum(lambda t, y, a: a[name][k]), 2) for k in keys})
            r['return_pct'] = {k: _pct(r[k], price, row['years_from_today']) for k in keys}
            w_at[name] = r
    # the weighted ladder and buy test: the main file's functions on the weighted horizon values
    out_w = {k: {'year_end_values': {last: w_ye[last][k]}, 'annual_return_pct': w_ret[k]}
             for k in keys if k != 'base_calibrated'}
    cal_w = ({'year_end_values': {last: w_ye[last]['base_calibrated']}, 'annual_return_pct': w_ret['base_calibrated']}
             if 'base_calibrated' in keys else {})
    fund_w = {'scenarios': {k: {'equity_wiped_year_ends': [last] if w_ye[last][k] <= 0 else []} for k in keys}}
    ends = [_calibrated_end_fn(q) for _, _, q, _ in worlds]
    moves = 'base_calibrated' in keys and any(mv for _, mv in ends)

    def rerun(h):
        def end(x):
            return sum(e['weight'] * fn(x) for (e, _, _, _), (fn, _) in zip(worlds, ends))
        return _solve_rerun(end, price, (1 + h / 100) ** years)

    p_w = dict(p, calibrated=None if moves else {'pe': 'fixed in every world'})
    bt_w = buy_test(p_w, out_w, cal_w, None, fund_w, bands, rerun=rerun)
    if not moves and 'base_calibrated' in keys:
        bt_w['note_rerun'] = 'every world fixes calibrated.pe: there is no rerun price'
    weighted = {
        'value_today': w_today, 'year_end_values': w_ye, 'annual_return_pct': w_ret, 'at': w_at,
        'base_return_pct': w_ret.get('base'),
        'probability_weighted_value_today': round(sum(w * r['weighted_value_today'] for w, _, _, _, r in vals), 2),
        'two_lens': {'ours': None if w_ret.get('base') is None else band(w_ret['base'], bands),
                     'calibrated': None if w_ret.get('base_calibrated') is None else band(w_ret['base_calibrated'], bands)},
        'price_ladder': price_ladder(p, out_w, cal_w, bands) if 'base' in keys else None,
        'buy_test': bt_w}
    lens = weighted['two_lens']
    lens['agree'] = None if None in (lens['ours'], lens['calibrated']) else lens['ours'] == lens['calibrated']
    return {'source': source, 'price': price, 'as_of': p.get('as_of'),
            'weights': {e['name']: e['weight'] for e, _, _, _ in worlds},
            'worlds': rows, 'weighted': weighted, 'warnings': notes,
            'note': ('weighted value = sum of weight x value of each world, per scenario and date (wiped = 0); the weighted '
                     '5-year return is the weighted horizon year-end value against the price; the ladder and buy test are '
                     'the model\'s on those weighted values, and the buy test\'s rerun price holds each world\'s own multiple '
                     'at the trial price. The weights are judgement, not engine odds. The other keys describe the main file')}


def _fmt(x, width=10, dec=2):
    return f'{"n/a":>{width}}' if x is None else f'{x + 0.0:>{width},.{dec}f}'


def summary(p, res):
    """A short plain table of the result."""
    rg, lad, bt = res['ranges'], res['price_ladder'], res['buy_test']
    cols = [k for k in ('bear', 'base', 'bull', 'base_calibrated') if k in rg['today']]
    wiped = res['funding']['scenarios']

    def cell(k, v, today=False, year=None):
        f = wiped.get(k, {})
        if (today and f.get('equity_wiped')) or (year and year in f.get('equity_wiped_year_ends', [])):
            return f'{"wiped":>12}'
        return _fmt(v, 12)

    inp, dv, rwd = res['inputs'], res['dated_values'], res['return_with_distributions']['scenarios']
    L = [f'{p.get("ticker", "?")}  as of {p.get("as_of", "?")}  price {p["price"]:,.2f}   '
         '(conditional values on each path, not price forecasts)']
    if inp.get('base_year_end'):
        how = 'derived from base_year_end' if inp['dates_derived'] else 'from as_of + days_to_base_year_end, as typed in the file'
        L.append(f'Dates: base year-end {inp["base_year_end"]}, {inp["days_to_base_year_end"]:g} days from as_of; '
                 f'{inp["years_to_horizon"]:g} years to the horizon year-end {inp["horizon_date"]} ({how})')
    if inp.get('repriced'):
        L.append('Repriced: ' + inp['repriced']['note'])
    L += ['',
         f'{"":<24}' + ''.join(f'{("base (cal.)" if k == "base_calibrated" else k):>12}' for k in cols),
         f'{"value today":<24}' + ''.join(cell(k, rg['today'][k], today=True) for k in cols)]
    for key in ('two_year', 'five_year'):
        r = rg[key]
        L.append(f'{"end " + str(r["year"]) + " (" + format(r["years_from_today"], ".1f") + "y)":<24}'
                 + ''.join(cell(k, r[k], year=r['year']) for k in cols))
        if 'return_pct' in r:
            L.append(f'{"  return % a year":<24}' + ''.join(_fmt(r['return_pct'][k], 12, 1) for k in cols))
    L.append(f'{"  with distributions":<24}' + ''.join(_fmt(rwd.get(k, {}).get('return_with_distributions_pct'), 12, 1) for k in cols)
             + '   (second line, not the headline)')
    if dv.get('available'):
        for key, name in (('one_year', 'in 1 year'), ('two_year', 'in 2 years'), ('five_year', 'in 5 years')):
            r = dv['at'][key]
            L.append(f'{name + " (" + r["date"] + ")":<24}'
                     + ''.join((f'{"wiped":>12}' if k in r.get('wiped', []) else _fmt(r.get(k), 12)) for k in cols)
                     + (f'   ({r["beyond_last_year_end_days"]} days after the last year-end: carried on)'
                        if 'beyond_last_year_end_days' in r else ''))
        L.append(f'{"  return % a year":<24}' + ''.join(_fmt(dv['at']['five_year']['return_pct'].get(k), 12, 1) for k in cols))
    else:
        L.append('Values at exactly 1, 2 and 5 years: not available (' + dv['note'] + ')')
    L += [f'{"index % a year":<24}{res["index_return_pct"]:>12,.1f}   weighted return {res["weighted_return_pct"]:.1f}% a year '
          '(subjective probabilities)', 'Note: ' + rg['note'] + '.',
          'With distributions: the 5-year return if the free cash the model accumulates were paid out each year and '
          'reinvested at the cost of equity.', '',
          f'Price ladder: price today at which the base return to end {lad["horizon_year"]} equals each hurdle',
          f'{"  hurdle":<58}{"ours":>10}{"calibrated":>12}']
    for rung in lad['rungs'].values():
        L.append(f'  {rung.get("meaning", rung["label"]):<56}{_fmt(rung["ours"])}{_fmt(rung["calibrated"], 12)}')
    L += ['',
          f'Buy test: {"PASS" if bt["pass"] else "FAIL"}   ({bt["rule"]})',
          f'{"  leg":<20}{"return %":>10}{"threshold %":>13}{"pass":>7}{"max price":>12}']
    for k, leg in bt['legs'].items():
        L.append(f'  {k:<18}{_fmt(leg["return_pct"], 10, 1)}{leg["threshold_pct"]:>13,.1f}'
                 f'{("n/a" if leg["pass"] is None else "yes" if leg["pass"] else "no"):>7}{_fmt(leg["max_price"], 12)}')
    if bt.get('no_price_passes'):
        L.append('  ' + bt['no_price_passes'])
    elif 'max_price_passing' in bt:
        L.append(f'  highest price at which every leg passes: {bt["max_price_passing"]:,.2f} ({bt["max_price_vs_price_pct"]:+.1f}% '
                 f'vs today), binding leg: {bt["binding_leg"]}')
    if bt.get('rerun', {}).get('lower_than_stated'):
        L.append(f'  on a rerun at that price (its own multiple held): {bt["rerun"]["max_price_passing"]:,.2f}, binding leg: '
                 f'{bt["rerun"]["binding_leg"]}')
    if bt.get('margin_of_safety_price') is not None:
        L.append(f'  margin-of-safety price (the same four legs, base at {MARGIN_OF_SAFETY_PCT:g}% in both lenses): '
                 f'{bt["margin_of_safety_price"]:,.2f}, binding leg: {bt["margin_of_safety_binding_leg"]}'
                 + (' (rerun price)' if (bt.get('margin_of_safety_rerun') or {}).get('used') else ''))
    mg = res['method_gap']
    L += ['', 'Method gap: what each method gives alone (the value is the plain average of the two)',
          f'{"  scenario":<18}{"DCF today":>10}{"ret %":>7}{"band":>9}{"multiple today":>16}{"ret %":>7}{"band":>9}'
          f'{"exit P/E":>10}{"DCF implies":>13}']
    for k, row in mg['scenarios'].items():
        a, b = row['dcf'], row['exit_multiple']
        L.append(f'  {("base (cal.)" if k == "base_calibrated" else k):<16}{_fmt(a["value_today"])}{_fmt(a["return_pct"], 7, 1)}'
                 f'{(a["band"] or "wiped"):>9}{_fmt(b["value_today"], 16)}{_fmt(b["return_pct"], 7, 1)}{(b["band"] or "wiped"):>9}'
                 f'{_fmt(row["exit_pe"], 10, 1)}{_fmt(row["dcf_implied_exit_pe"], 13, 1)}')
    L += ['  ! ' + x for x in mg['warnings']]
    L += ['', 'What moves the base value most (one input at a time; model arithmetic, not a forecast)']
    for i, r in enumerate(res['sensitivity']['rows'][:3], 1):
        dn, up = r['down'], r['up']
        pct = '' if dn['d_value_pct'] is None else f' ({dn["d_value_pct"]:+.1f}% / {up["d_value_pct"]:+.1f}%)'
        L.append(f'  {i}. {r["input"]} {r["change"]}: value today {dn["d_value"]:+,.2f} / {up["d_value"]:+,.2f}{pct}, '
                 f'5-year return {dn["d_return_pts"]:+.2f} / {up["d_return_pts"]:+.2f} points')
    L.append('  terminal inputs (not in the list above):')
    for r in res['sensitivity_terminal']['rows']:
        dn, up = r['down'], r['up']

        def one(x, key, f):
            return 'n/a' if x is None or x[key] is None else format(x[key], f)
        extra = '' if 'at_3pct' not in r or r['at_3pct'] is None else f'; at 3% growth {r["at_3pct"]["d_value"]:+,.2f}'
        L.append(f'  - {r["input"]} {r["change"]}: value today {one(dn, "d_value", "+,.2f")} / {one(up, "d_value", "+,.2f")}'
                 f' ({one(dn, "d_value_pct", "+.1f")}% / {one(up, "d_value_pct", "+.1f")}%){extra}')
    rv, cd = res['reverse_reading'], res['calibrated_diagnostics']
    L += ['', f'Price needs growth % (first year held): {rv["base_margin_path"]["growth_pct"]} on the base margin path; '
          f'{rv["flat_margin"]["growth_pct"]} at a flat {rv["flat_margin"]["margin_held"] * 100:g}% margin; base '
          f'{rv["base_growth_pct"]}',
          f'Calibrated lens: multiple held {_fmt(cd["pe_held"], 0).strip()} vs base exit {cd["pe_base_exit"]:g} '
          f'({_fmt(cd["pe_held_to_base_exit"], 0).strip()}{"" if cd["pe_held_to_base_exit"] is None else "x"}); {cd["reading"]}',
          'Lowest net cash ($M): ' + '; '.join(f'{k} {f["low_point_m"]:,} in {f["low_point_year"]}'
                                              for k, f in res['funding']['scenarios'].items()),
          '']
    for k, r in res['scenarios'].items():
        fp = r.get('funding')
        if fp:
            eq = ', '.join(f'{a:,.0f}M at {fp["issue_price"][y]:,.2f} (year-end {y})' for y, a in fp['equity_raise_m'].items())
            dt = ', '.join(f'{a:,.0f}M (year-end {y})' for y, a in fp['debt_raise_m'].items())
            L.append(f'Funding plan, {k}: equity {eq or "none"}; shares {fp["shares_m_before"]:,.1f}M -> {fp["shares_m"]:,.1f}M; '
                     f'debt {dt or "none"}; debt rate {fp["debt_rate"]:g}')
    if 'worlds' in res:
        L += summary_worlds(res['worlds']) + ['']
    L += ['Warnings:' if res['warnings'] else 'Warnings: none']
    L += ['  - ' + x for x in res['warnings']]
    return '\n'.join(L)


def summary_worlds(wd):
    """The worlds block of --summary."""
    W = wd['weighted']
    cols = [k for k in LENS_KEYS if k in W['value_today']]
    head = ''.join(f'{("base (cal.)" if k == "base_calibrated" else k):>12}' for k in cols)
    L = [f'Worlds ({wd["source"] or "given"}): weighted values (sum of weight x value; weights are judgement)',
         f'{"  world":<36}{"weight":>7}' + head + '   base % a year, cal. %']
    for r in wd['worlds']:
        L.append(f'  {r["name"]:<34}{r["weight"]:>7.2f}' + ''.join(_fmt(r['value_today'].get(k), 12) for k in cols)
                 + f'   {_fmt(r["annual_return_pct"].get("base"), 0, 1).strip()} / '
                   f'{_fmt(r["annual_return_pct"].get("base_calibrated"), 0, 1).strip()}')
    L.append(f'  {"weighted, today":<34}{"":>7}' + ''.join(_fmt(W['value_today'][k], 12) for k in cols))
    last = max(W['year_end_values'])
    L.append(f'  {"weighted, end " + str(last):<34}{"":>7}' + ''.join(_fmt(W['year_end_values'][last][k], 12) for k in cols))
    L.append(f'  {"  return % a year":<34}{"":>7}' + ''.join(_fmt(W['annual_return_pct'][k], 12, 1) for k in cols))
    if W.get('at'):
        for key, name in (('one_year', 'in 1 year'), ('five_year', 'in 5 years')):
            r = W['at'][key]
            L.append(f'  {"weighted, " + name + " (" + r["date"] + ")":<34}{"":>7}' + ''.join(_fmt(r[k], 12) for k in cols))
    bt, lad = W['buy_test'], W['price_ladder']
    L.append(f'  weighted lenses: ours {W["two_lens"]["ours"]}, calibrated {W["two_lens"]["calibrated"]}; probability-weighted '
             f'value today {W["probability_weighted_value_today"]:,.2f}')
    if lad:
        L.append(f'  weighted 13% price of the base alone {_fmt(lad["rungs"]["margin_of_safety_13"]["ours"], 0).strip()} (calibrated '
                 f'{_fmt(lad["rungs"]["margin_of_safety_13"]["calibrated"], 0).strip()}); buy test '
                 f'{"PASS" if bt["pass"] else "FAIL"}, highest passing price {_fmt(bt.get("max_price_passing"), 0).strip()} '
                 f'(binding: {bt.get("binding_leg")})'
                 + (f'; on a rerun {bt["rerun"]["max_price_passing"]:,.2f}' if bt.get('rerun', {}).get('lower_than_stated') else '')
                 + f'; margin-of-safety price {_fmt(bt.get("margin_of_safety_price"), 0).strip()}')
    L += ['  ! ' + x for x in wd['warnings']]
    return L



def _reverse_growth(p, base):
    lo, hi = -1.0, 4.0
    for _ in range(80):
        f = (lo + hi) / 2
        d = dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])
        if run_scenario(p, d)['value_today'] < p['price']:
            lo = f
        else:
            hi = f
    return dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])


def run(p, worlds=None, base_dir=None, source=None):
    """The full result. The params are checked first (ParamsError) and the dates derived when base_year_end is given.

    worlds: a worlds spec ({"worlds": [...]}); None = the params' own "worlds" key, if any. base_dir: the folder the
    world files are relative to (None = as given). Without worlds the result is _run_single's, unchanged."""
    res = _run_single(p)
    spec = worlds if worlds is not None else p.get('worlds')
    if spec is not None:
        q, _ = prepare(p)
        res['worlds'] = worlds_report(q, p, spec, base_dir, source or ('params' if worlds is None else None))
    return res


def _run_single(p):
    """The result of one params file (every key but "worlds")."""
    p, inputs = prepare(p)
    out = {k: run_scenario(p, d) for k, d in p['scenarios'].items()}
    pr = p['probabilities']
    last = str(p['base_year'] + 5)
    w_today = sum(pr[k] * out[k]['value_today'] for k in out)
    w_end = sum(pr[k] * out[k]['year_end_values'][int(last)] for k in out)
    base = p['scenarios']['base']
    res = {
        'scenarios': out,
        'weighted_value_today': round(w_today, 2),
        'weighted_end_value': round(w_end, 2),
        'weighted_return_pct': round(((w_end / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 1),
        'index_return_pct': round((p['rf'] + p['erp']) * 100, 1),
        'buy_price': round(out['base']['year_end_values'][int(last)] / (1 + p['buy_return']) ** p['years_to_horizon'], 2),
        'index_parity_price': round(out['base']['year_end_values'][int(last)] / (1 + p['rf'] + p['erp']) ** p['years_to_horizon'], 2),
    }
    # reverse DCF: hold the first (guided) year, scale later growth until value today = price
    d = _reverse_growth(p, base)
    res['market_implied_growth_pct'] = [round(g * 100, 1) for g in d['g']]
    res['market_implied_end_revenue_m'] = run_scenario(p, d)['revenue_m'][p['base_year'] + 5]
    sens = {}
    for b in (p['beta'] - 0.3, p['beta'] + 0.3):
        sens[f'beta {b:.2f}'] = run_scenario(dict(p, beta=b), base)['value_today']
    for pe in (base['pe'] - 5, base['pe'] + 5):
        r = run_scenario(p, dict(base, pe=pe))
        sens[f'exit P/E {pe:g}'] = [r['value_today'], r['annual_return_pct']]
    res['sensitivity_base'] = sens
    cal = calibrated_lens(p, base)
    bands = p.get('bands')
    ours_ret = out['base']['annual_return_pct']
    lens = {'ours': {'value_today': out['base']['value_today'], 'annual_return_pct': ours_ret, 'band': band(ours_ret, bands),
                     'pe_exit': base['pe']}, 'calibrated': cal}
    if 'annual_return_pct' in cal:
        cal['band'] = band(cal['annual_return_pct'], bands)
        lens['agree'] = cal['band'] == lens['ours']['band']
        lens['rule'] = ('both lenses in the same band: act on it' if lens['agree'] else
                        'lenses disagree: hold, or stage the trade around the next report')
    res['two_lens'] = lens
    return add_reporting(p, res, inputs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('params')
    ap.add_argument('--out')
    ap.add_argument('--summary', action='store_true', help='print a short plain table instead of the JSON')
    ap.add_argument('--price', type=float, help='reprice: run the saved params at this price')
    ap.add_argument('--as-of', dest='as_of', help='reprice: run at this date (YYYY-MM-DD); the dates are recomputed only '
                    'when the params hold base_year_end')
    ap.add_argument('--params-out', help='write the repriced params (with a "repriced" record) to this file')
    ap.add_argument('--worlds', help='a worlds file {"worlds": [{"name", "file", "weight"}]} (files relative to it): adds '
                    'the "worlds" key with the weighted values; the other keys describe the params file')
    ap.add_argument('--calibrated-rule', dest='calibrated_rule',
                    help='a calibrated-rule sidecar {"pe_rule": "net_of_cash", "why": ...} (e.g. calibrated_rule.json beside '
                         'the params): the second lens holds a multiple tied to the price; a fixed calibrated.pe of the '
                         'params is dropped for this run (the params file is not edited)')
    a = ap.parse_args()
    p = json.load(open(a.params))
    if a.calibrated_rule:
        try:
            p = apply_calibrated_rule(p, json.load(open(a.calibrated_rule)))
        except ParamsError as e:
            print(f'lean_model: {a.calibrated_rule}: ' + '; '.join(e.problems), file=sys.stderr)
            sys.exit(2)
    spec, wdir, src = None, None, None
    if a.worlds:
        spec, wdir, src = json.load(open(a.worlds)), os.path.dirname(os.path.abspath(a.worlds)), a.worlds
    elif p.get('worlds') is not None:
        wdir, src = os.path.dirname(os.path.abspath(a.params)), a.params
    try:
        if a.price is not None or a.as_of is not None:
            p = reprice(p, a.price, a.as_of)
        res = run(p, worlds=spec, base_dir=wdir, source=src)
    except ParamsError as e:
        print(f'lean_model: {a.params} cannot be run ({len(e.problems)} problem(s)):', file=sys.stderr)
        for x in e.problems:
            print('  - ' + x, file=sys.stderr)
        sys.exit(2)
    if a.params_out:
        open(a.params_out, 'w').write(json.dumps(p, indent=1))
    txt = json.dumps(res, indent=1)
    if a.out:
        open(a.out, 'w').write(txt)
    print(summary(p, res) if a.summary else txt)


if __name__ == '__main__':
    main()
