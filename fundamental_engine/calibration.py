"""Engine-consistent market return: value the index with the same engine, so a stock's edge is measured like for like.

The default index alternative (risk-free + equity risk premium) is built differently from a stock's implied return (the
engine's own forecast and fade rules). If the engine is systematically conservative, every stock looks worse than that
index and the system becomes a rejection machine; if it is generous, everything looks attractive. Valuing a broad
cap-weighted sample of the largest companies with the same engine gives the market's implied return on the same basis:
a shared model bias then cancels in the comparison (stock IRR - engine market IRR), which is what "beat the S&P 500" needs.

The result is an input to decision.inputs.index ({value, basis, source, low, high}) and to allocation; the absolute
assumption stays visible next to it. Coverage (the share of the sample's market value with a computed return) is reported:
low coverage means the estimate leans on the companies the engine could value.

pick() chooses the calibration file a single-company run uses by default (cli deep/pipeline when no --index is given):
the newest computed one dated on or before the run (never a later one: no look-ahead) and at most MAX_AGE_DAYS older.
Without one, the decision keeps the generic risk-free + ERP assumption and says so (decision.COMPARATORS).
"""
import json
from datetime import date
from pathlib import Path
from statistics import median

# A sweep values each company on its latest 10-Q/10-K and the day's prices; one reporting quarter later most of its inputs
# are a filing behind, so an older file is not used by default (pass it explicitly with --index to override).
MAX_AGE_DAYS = 92


def market_implied(summaries, caps, as_of, assumption=.0875, clip=(-.10, .40), band=.01):
    """summaries: pipeline.summary rows (symbol, base_irr, bear_irr, bull_irr); caps: {symbol: market value}."""
    total = sum(caps.get(s['symbol']) or 0 for s in summaries)
    pts = [(s['symbol'], min(max(s['base_irr'], clip[0]), clip[1]), caps.get(s['symbol']) or 0, s)
           for s in summaries if s.get('base_irr') is not None and caps.get(s['symbol'])]
    if not pts or not total:
        return {'status': 'not_computed', 'reason': 'no implied returns with market values'}
    w = sum(c for _, _, c, _ in pts)
    cw = sum(r * c for _, r, c, _ in pts) / w
    cw_bear = sum(min(max(s.get('bear_irr') if s.get('bear_irr') is not None else r, clip[0]), clip[1]) * c for _, r, c, s in pts) / w
    kp = [(s.get('cost_of_equity'), c) for _, _, c, s in pts if isinstance(s.get('cost_of_equity'), (int, float))]
    ke_m = sum(k * c for k, c in kp) / sum(c for _, c in kp) if kp else None
    ra = [(s.get('risk_adjustment'), c) for _, _, c, s in pts if isinstance(s.get('risk_adjustment'), (int, float))]
    ra_m = sum(a * c for a, c in ra) / sum(c for _, c in ra) if ra else None
    rs = sorted(r for _, r, _, _ in pts)
    q = lambda f: rs[min(len(rs) - 1, int(f * len(rs)))]
    out = {'status': 'computed', 'as_of': as_of, 'n_sample': len(summaries), 'n_with_return': len(pts), 'coverage_by_value': w / total,
           'engine_market_return': cw, 'engine_market_bear': cw_bear, 'median': median(rs),
           'percentiles': {'p10': q(.1), 'p25': q(.25), 'p50': q(.5), 'p75': q(.75), 'p90': q(.9)},
           'share_above_assumption': sum(1 for r in rs if r > assumption) / len(rs), 'assumption': assumption,
           'gap_to_assumption': cw - assumption, 'clip': clip,
           # the engine's own cost of equity for the same market: the gap is how much stricter it is than the market's pricing
           'engine_market_cost_of_equity': ke_m, 'rate_shift': (ke_m - cw) if ke_m is not None else None,
           # value-weighted net risk adjustment over risk-free + ERP: near zero when the add-ons are market-neutral (the ERP's
           # definition); forecast.MARKET_NEUTRAL_ADJUSTMENT is re-measured from it
           'mean_risk_adjustment': ra_m}
    out['index_input'] = {'value': cw, 'low': cw - band, 'high': cw + band,
                          'basis': (f"engine-consistent market return: cap-weighted base-case implied return of {len(pts)} of the {len(summaries)} "
                                    f"largest US operating companies valued by the same engine on {as_of} ({w / total:.0%} of their value)"),
                          'source': 'fundamental_engine.calibration.market_implied over the large-cap sweep casefiles'}
    if ke_m is not None and ke_m > cw:
        out['index_input'].update(rate_shift=ke_m - cw, market_cost_of_equity=ke_m)
    out['reading'] = ('the engine is on the same footing as the index assumption' if abs(cw - assumption) <= .01 else
                      f"the engine implies {cw:.1%} for the market vs the {assumption:.2%} assumption: comparing stocks with the assumption "
                      f"would {'reject' if cw < assumption else 'favour'} almost everything; compare with the engine-consistent value")
    return out


def pick(as_of, paths, max_age_days=MAX_AGE_DAYS):
    """Newest usable calibration among paths for a run dated as_of: status computed with an index_input, dated on or before
    as_of and at most max_age_days older. Returns {path, as_of, age_days, index_input} or None. Unreadable files are skipped."""
    try:
        run = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None
    best = None
    for path in paths:
        try:
            cal = json.loads(Path(path).read_text(encoding='utf-8'))
            d = date.fromisoformat(str(cal.get('as_of'))[:10])
        except (OSError, ValueError, AttributeError):
            continue
        ii = cal.get('index_input')
        age = (run - d).days
        if cal.get('status') != 'computed' or not isinstance(ii, dict) or not 0 <= age <= max_age_days:
            continue
        if best is None or d.isoformat() > best['as_of']:
            best = {'path': str(path), 'as_of': d.isoformat(), 'age_days': age, 'index_input': ii}
    return best
