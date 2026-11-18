"""The self-check of the lean valuation path as code (scripts/lean_check.py is the command; this module holds the logic
and reads no file).

Why: the self-check before delivery lived as prose, and the errors it lists kept returning. research/lessons.jsonl is
the list of past errors; every line names what catches it: a function here (`auto: lean_check.<name>`), a test, or a
manual question. run_checks() runs the model, then every automatic check and every manual question that applies, and
gives each item one status:

  pass            the check holds, or the manual question has a one-line answer
  fail            an automatic check found the error
  open            a question nobody answered yet, or a check that needs an input which is not there
  not_applicable  the check does not apply to this run (no facts supplied, no previous params, not a buy)

Judgement stays with the analyst: the command asks and records. It never edits a params file and never changes a value.
A blocks_buy item that is fail or open while the buy test passes (at the params price, or at a quote more than 3% away)
sets exit_code 1; a "not a buy" result never blocks.

Optional params keys read here (all additive; lean_model ignores them):
  guidance        {year, revenue_low, revenue_high, margin_low, margin_high, source} or a list of such blocks: the
                  company's own guide for a model year (revenue in the params unit, $M; margins as fractions, the same
                  definition as the model's operating margin after SBC). Any of the four numbers may be left out.
  judgement       the locked-rules block of fundamental_engine/locked_rules.py (validate_judgement).
  tags            list of words; `capex_cycle` or `cyclical` turns the digestion-year question on, `asset_heavy` the
                  depreciation questions; `not_capex_cycle` / `not_asset_heavy` turn them off. Without tags a simple
                  reading of the paths decides and says so.
  reconciliation  {revenue, shares, net_cash}: a one-line reason for a gap to the reported figures. A note under
                  params.notes whose key names the input (shares, net_cash, rev0, base_year, ...) counts as well.
  one_off_revenue_m  base-year revenue taken out as one-off ($M): added back before the comparison with the reports.
  price_time_utc  when the price was read ('2026-11-13 21:00 ...'); the first ten characters are the date.

The answers file (--answers) is {"<lesson id>": "one line", ...}, e.g. {"L11": "none: no partnership revenue in 2026"}.
"""
import copy
import datetime
import math

from . import forecast_tracker as FT
from . import locked_rules as LR

CHECK_VERSION = 1
TOLERANCE = 0.03              # revenue, shares and the price: a gap above 3% needs a reason
NET_CASH_TOLERANCE = 0.03     # net cash: a gap above 3% of the market value needs a reason
PRICE_MAX_TRADING_DAYS = 3
STALE_FACT_DAYS = 200         # a reported count older than this before as_of is not compared
DIGESTION_DROP = 0.10         # a digestion year: growth negative; or a dip of 10 points or more that recovers the year
DIGESTION_FLAT = 0.05         # after; or growth of 5% or less right after a year of 15% or more (a fade is not one)
DIGESTION_AFTER = 0.15
FAST_GROWTH = 0.20            # without tags: the digestion question is asked when the first base year grows this fast
HEAVY_CONVERSION = 0.5        # without tags: free cash below half of after-tax operating profit in a base year = asset heavy
MIN_ANSWER = 3                # characters; shorter is not an answer
STATUSES = ('pass', 'fail', 'open', 'not_applicable')
TEXT_KEYS = ('notes', 'review_changes', 'probabilities_note', 'price_time_utc', 'latest_quote', 'ticker', 'judgement',
             'guidance', 'tags', 'reconciliation', 'repriced', 'fcf_build_bn')

# the automatic items: id -> (function name, group, blocks_buy). research/lessons.jsonl names the same functions and
# tests/test_lean_check.py holds the two in step.
AUTO = {
    'inputs': ('check_inputs', 'model', True),
    'lenses_agree': ('check_lenses_agree', 'model', True),
    'calibrated_lens': ('check_calibrated_lens', 'model', True),
    'funding': ('check_funding', 'model', True),
    'equity_wiped': ('check_equity_wiped', 'model', False),
    'method_gap': ('check_method_gap', 'model', False),
    'on_the_line': ('check_on_the_line', 'model', True),
    'rerun_price': ('check_rerun_price', 'model', True),
    'reverse_reading': ('check_reverse_reading', 'model', False),
    'distributions_band': ('check_distributions_band', 'model', False),
    'probabilities_label': ('check_probabilities_label', 'model', False),
    'reconcile_revenue': ('check_reconciliation', 'inputs', True),
    'reconcile_shares': ('check_reconciliation', 'inputs', True),
    'reconcile_net_cash': ('check_reconciliation', 'inputs', True),
    'guidance': ('check_guidance', 'path', True),
    'digestion_year': ('check_digestion_year', 'path', True),
    'price': ('check_price', 'price', True),
    'judgement': ('check_judgement', 'rules', True),
    'market_rates': ('check_market_rates', 'rules', False),
    'prev_inputs': ('check_prev_inputs', 'previous', False),
    'prev_levels': ('check_prev_levels', 'previous', False),
}


def _item(id, status, detail, lessons=None, blocks=None, group=None, **more):
    fn, grp, blk = AUTO.get(id, (None, group or 'manual', bool(blocks)))
    row = {'id': id, 'group': group or grp, 'status': status, 'blocks_buy': blk if blocks is None else bool(blocks),
           'detail': detail, 'lessons': lessons or []}
    row.update(more)
    return row


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _day(x):
    return datetime.date.fromisoformat(str(x)[:10])


def trading_days_between(d0, d1):
    """Weekdays after d0 up to and including d1 (0 when d1 is not after d0). Exchange holidays are not known here, so a
    holiday counts as a trading day: the count can only be too high."""
    d0, d1 = _day(d0), _day(d1)
    n, d = 0, d0
    while d < d1:
        d += datetime.timedelta(days=1)
        n += d.weekday() < 5
    return n


def _answer(answers, key):
    a = (answers or {}).get(key)
    if isinstance(a, dict):
        a = a.get('answer')
    return a.strip() if isinstance(a, str) and len(a.strip()) >= MIN_ANSWER else None


BLOCKING_FORM = '{"answer": text, "confirmed": true, "evidence": [file path or URL, ...]}'


def _blocking_answer(answers, key):
    """(status, text) for a manual question that blocks a buy. A text alone is not enough: the answer must say it is
    confirmed and name its evidence ({"answer", "confirmed": true, "evidence": [...]}), because a written answer such as
    "not yet verified" once passed this check (NVDA, 18.11.2026). confirmed false is a fail; anything else stays open."""
    a = (answers or {}).get(key)
    text = _answer(answers, key)
    if not isinstance(a, dict):
        return 'open', (f'answered without confirmation ({text}); a question that blocks a buy needs {BLOCKING_FORM}'
                        if text else None)
    ev = [e for e in (a.get('evidence') or []) if isinstance(e, str) and e.strip()]
    if a.get('confirmed') is False:
        return 'fail', 'not confirmed: ' + (text or 'no text')
    if a.get('confirmed') is True and ev and text:
        return 'pass', 'confirmed: ' + text + ' (evidence: ' + '; '.join(ev[:4]) + ')'
    return 'open', f'incomplete confirmation ({text or "no text"}); a question that blocks a buy needs {BLOCKING_FORM}'


def _lessons_for(lessons, fn):
    return [x['id'] for x in lessons or [] if str(x.get('enforcement', '')).strip() == f'auto: lean_check.{fn}']


def _stated(params, key, words):
    """Where the params state a reason for a gap in an input: reconciliation.<key>, else a notes key naming the input."""
    rec = params.get('reconciliation')
    if isinstance(rec, dict) and isinstance(rec.get(key), str) and rec[key].strip():
        return f'reconciliation.{key}', rec[key].strip()
    notes = params.get('notes')
    if isinstance(notes, dict):
        for w in words:                                    # the words are in order of how directly they name the input
            for k, v in notes.items():
                if w in str(k).lower() and v not in (None, '', [], {}):
                    return f'notes.{k}', v if isinstance(v, str) else str(v)
    return None, None


def _short(text, n=110):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 3] + '...'


# --------------------------------------------------------------------------------------------- (a) the model's warnings

def check_inputs(params, res):
    w = (res.get('inputs') or {}).get('warnings') or []
    return _item('inputs', 'fail' if w else 'pass', '; '.join(w) if w else 'the input checks raise nothing')


def check_lenses_agree(params, res):
    lens = res['two_lens']
    if 'agree' not in lens:
        return _item('lenses_agree', 'fail', 'no calibrated lens: ' + str(lens['calibrated'].get('note', 'set calibrated.pe')))
    d = f"ours {lens['ours']['band']} ({lens['ours']['annual_return_pct']:g}% a year), calibrated {lens['calibrated']['band']} " \
        f"({lens['calibrated']['annual_return_pct']:g}%)"
    return _item('lenses_agree', 'pass' if lens['agree'] else 'fail',
                 d if lens['agree'] else d + ': hold, or stage the trade around the next report')


def check_calibrated_lens(params, res):
    cd = res['calibrated_diagnostics']
    if cd['inflated_multiple_warning']:
        return _item('calibrated_lens', 'fail', 'not a second opinion: ' + '; '.join(cd['warning_reasons']))
    return _item('calibrated_lens', 'pass', cd['reading'] + (f" (held {cd['pe_held']:g} against the base exit {cd['pe_base_exit']:g})"
                                                              if cd.get('pe_held') else ''))


def check_funding(params, res):
    bad = {k: f['reasons'] for k, f in res['funding']['scenarios'].items() if f.get('reasons')}
    if not bad:
        return _item('funding', 'pass', 'the net-cash path stays funded in every scenario')
    d = '; '.join(f'{k}: ' + ', '.join(v) for k, v in bad.items())
    # the base and bull plans must be funded; an unfunded bear describes the bear and does not block
    return _item('funding', 'fail', d, blocks=any(k in bad for k in ('base', 'bull')))


def check_equity_wiped(params, res):
    w = []
    for k, f in res['funding']['scenarios'].items():
        if f.get('equity_wiped'):
            w.append(f'{k}: wiped today')
        elif f.get('equity_wiped_year_ends'):
            w.append(f"{k}: wiped at the year-ends {f['equity_wiped_year_ends']}")
    return _item('equity_wiped', 'fail' if w else 'pass',
                 ('; '.join(w) + ': report the scenario as wiped (value zero, no return), never as a small price') if w
                 else 'no scenario wipes the equity')


def check_method_gap(params, res):
    rows = res['method_gap']['scenarios']
    base = rows.get('base') or {}
    other = [k for k, r in rows.items() if k != 'base' and r.get('bands_differ')]
    if base.get('bands_differ'):
        return _item('method_gap', 'fail', base.get('warning', 'base: the two methods land in different bands'))
    tail = f' (they differ in: {", ".join(other)})' if other else ''
    return _item('method_gap', 'pass', 'base: DCF and exit multiple land in the same band' + tail)


def check_on_the_line(params, res):
    bt = res['buy_test']
    if bt.get('on_the_line'):
        return _item('on_the_line', 'fail', f"the pass rests on rounding: the price {bt['price']:,.2f} is above the highest "
                                            f"passing price {bt['max_price_passing']:,.2f}")
    return _item('on_the_line', 'pass' if bt.get('pass') else 'not_applicable',
                 'the buy test passes at the exact price' if bt.get('pass') else 'the buy test does not pass')


def check_rerun_price(params, res):
    bt = res['buy_test']
    rr = bt.get('rerun')
    if not rr:
        return _item('rerun_price', 'not_applicable', 'no rerun price (calibrated.pe is set, a leg is wiped or has no value)')
    if not rr['lower_than_stated']:
        return _item('rerun_price', 'pass', f"a rerun confirms the highest passing price {bt['max_price_passing']:,.2f}")
    d = (f"quote {rr['max_price_passing']:,.2f}, not {bt['max_price_passing']:,.2f}: at the stated price a rerun holds a "
         f"lower multiple (binding leg: {rr['binding_leg']})")
    # it blocks only when today's price is above the price a rerun confirms
    return _item('rerun_price', 'fail', d, blocks=params['price'] > rr['max_price_passing'])


def check_reverse_reading(params, res):
    bad = [k for k in ('base_margin_path', 'flat_margin') if not res['reverse_reading'][k]['solved']]
    if bad:
        return _item('reverse_reading', 'fail', 'no growth path reaches the price for: ' + ', '.join(bad)
                     + ': do not quote "the growth the price needs" from it')
    r = res['reverse_reading']
    return _item('reverse_reading', 'pass', f"growth the price needs: {r['base_margin_path']['growth_pct']} on the base margin "
                                            f"path, {r['flat_margin']['growth_pct']} at a flat margin")


def check_distributions_band(params, res):
    rd = res['return_with_distributions']
    if 'base' in rd.get('band_changes', []):
        b = rd['scenarios']['base']
        return _item('distributions_band', 'fail',
                     f"base: {b['return_pct']:g}% a year ({b['band']}) becomes {b['return_with_distributions_pct']:g}% "
                     f"({b['band_with_distributions']}) with distributions reinvested: say so next to the headline")
    return _item('distributions_band', 'pass', 'the base stays in its band with distributions reinvested')


def check_probabilities_label(params, res):
    pr = params.get('probabilities') or {}
    text = ' '.join(str(pr.get(k, '')) for k in pr if k not in ('bear', 'base', 'bull')) + ' ' + str(params.get('probabilities_note', ''))
    ok = any(w in text.lower() for w in ('subjective', 'judgement', 'judgment'))
    return _item('probabilities_label', 'pass' if ok else 'fail',
                 'labelled subjective' if ok else 'no label: add "label": "subjective (...)" inside probabilities')


# ------------------------------------------------------------------------- (b) inputs against SEC companyfacts

CASH_TAGS = ('CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsAndShortTermInvestments')
INVESTMENT_TAGS = (('ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'),
                   ('MarketableSecuritiesNoncurrent', 'LongTermInvestments', 'AvailableForSaleSecuritiesDebtSecuritiesNoncurrent'))
DEBT_TOTAL_TAGS = ('LongTermDebt', 'DebtLongtermAndShorttermCombinedAmount', 'DebtInstrumentCarryingAmount')
DEBT_PART_TAGS = ('LongTermDebtNoncurrent', 'LongTermDebtCurrent')
DEBT_SHORT_TAGS = ('ShortTermBorrowings', 'CommercialPaper')


def _instants(payload, taxonomy, tag, unit):
    """{end: value} of a balance-sheet (instant) tag; the latest filed value of each date stands."""
    out, filed = {}, {}
    node = (((payload.get('facts') or {}).get(taxonomy) or {}).get(tag) or {}).get('units', {}).get(unit) or []
    for f in node:
        if f.get('start') or f.get('val') is None or not f.get('end'):
            continue
        if f.get('filed', '') >= filed.get(f['end'], ''):
            out[f['end']], filed[f['end']] = float(f['val']), f.get('filed', '')
    return out


def reported(payload, params, cutoff=None):
    """What the cached SEC companyfacts report for the three hand-typed inputs. cutoff (ISO date): periods first filed
    after it are left out (a check of a valuation as it stood on that day)."""
    per = FT.periods(payload)
    as_of = _day(params['as_of'])
    base_end = as_of + datetime.timedelta(days=params['days_to_base_year_end'])
    fy_start = FT._shift(base_end, -1) + datetime.timedelta(days=1)
    rev = {k: v for k, v in per['revenue'].items() if cutoff is None or v['first_filed'] <= str(cutoff)}
    out = {'currency': per['currency'], 'base_year_end': base_end.isoformat(), 'revenue': None, 'diluted': None, 'cover': None,
           'net_cash': None}

    def find(start, end):
        return FT._find(rev, start, end)

    full = find(fy_start, base_end)
    if full:
        out['revenue'] = {'basis': 'the base year as reported', 'expected': full['val'], 'period_end': base_end.isoformat(),
                          'first_filed': full['first_filed']}
    else:
        ytd = None
        for (s, e), r in rev.items():
            if abs((_day(s) - fy_start).days) <= FT.DATE_TOL and _day(e) < base_end and FT._span(s, e) in ('Q1', 'H1', '9M'):
                if ytd is None or e > ytd[0]:
                    ytd = (e, r, s)
        if ytd:
            e, r, s = ytd
            prior_ytd = find(FT._shift(_day(s), -1), FT._shift(_day(e), -1))
            prior_fy = find(FT._shift(fy_start, -1), FT._shift(base_end, -1))
            row = {'basis': None, 'expected': None, 'period_end': e, 'period': FT._span(s, e), 'first_filed': r['first_filed'],
                   'year_to_date': r['val']}
            if prior_ytd and prior_fy and prior_ytd['val'] > 0:
                growth = r['val'] / prior_ytd['val']
                row.update(basis=f"year to date ({FT._span(s, e)} to {e}) plus the rest of last year at the year-to-date growth "
                                 f"({(growth - 1) * 100:+.1f}%)",
                           expected=r['val'] + (prior_fy['val'] - prior_ytd['val']) * growth, ytd_growth=growth - 1,
                           trailing_12m=prior_fy['val'] + r['val'] - prior_ytd['val'])
            out['revenue'] = row
    # shares
    dil = {k: v for k, v in per['diluted_shares'].items() if cutoff is None or v['first_filed'] <= str(cutoff)}
    if dil:
        s, e = max(dil, key=lambda k: (k[1], k[0]))                     # the latest end, and of those the shortest period
        out['diluted'] = {'val': dil[(s, e)]['val'], 'end': e, 'period': FT._span(s, e) or 'quarter'}
    cover = _instants(payload, 'dei', 'EntityCommonStockSharesOutstanding', 'shares')
    if cutoff is not None:
        cover = {k: v for k, v in cover.items() if k <= str(cutoff)}
    if cover:
        e = max(cover)
        out['cover'] = {'val': cover[e], 'end': e}
    # net cash: cash and investments less debt at the latest balance-sheet date where both exist
    unit = 'USD' if per['currency'] in (None, 'USD') else per['currency']
    tax = 'us-gaap'

    def at(tag, day):
        return _instants(payload, tax, tag, unit).get(day)
    cash = {}
    for tag in CASH_TAGS:
        for e, v in _instants(payload, tax, tag, unit).items():
            cash.setdefault(e, (tag, v))
    cash = {e: v for e, v in cash.items() if cutoff is None or e <= str(cutoff)}
    for day in sorted(cash, reverse=True)[:2]:
        tag, c = cash[day]
        parts = {tag: c}
        if tag == CASH_TAGS[0]:
            for group in INVESTMENT_TAGS:
                got = [(t, at(t, day)) for t in group if at(t, day) is not None]
                if got:
                    parts[got[0][0]] = got[0][1]
        else:
            got = [(t, at(t, day)) for t in INVESTMENT_TAGS[1] if at(t, day) is not None]
            if got:
                parts[got[0][0]] = got[0][1]
        debt = {}
        total = [(t, at(t, day)) for t in DEBT_TOTAL_TAGS if at(t, day) is not None]
        pieces = [(t, at(t, day)) for t in DEBT_PART_TAGS if at(t, day) is not None]
        if total:
            debt[total[0][0]] = total[0][1]
        elif pieces:
            debt.update(pieces)
        for t in DEBT_SHORT_TAGS:
            if at(t, day) is not None:
                debt[t] = at(t, day)
        if debt:
            out['net_cash'] = {'date': day, 'cash_tags': parts, 'debt_tags': debt,
                               'val': sum(parts.values()) - sum(debt.values())}
            break
    return out


def check_reconciliation(params, res, payload, cutoff=None):
    """Three items: base-year revenue, shares and net cash against the reported figures."""
    if payload is None:
        return [_item(i, 'not_applicable', 'no SEC companyfacts supplied (--facts)') for i in
                ('reconcile_revenue', 'reconcile_shares', 'reconcile_net_cash')]
    rep = reported(payload, params, cutoff)
    as_of = _day(params['as_of'])
    items = []
    same_money = (rep['currency'] or 'USD') == params.get('currency', 'USD')

    # revenue
    r = rep['revenue']
    where, why = _stated(params, 'revenue', ('rev', 'one_off', 'base_year', 'g0', 'basis'))
    if not same_money:
        items.append(_item('reconcile_revenue', 'not_applicable',
                           f"SEC facts are in {rep['currency']}, the model in {params.get('currency', 'USD')}: reconcile by hand"))
    elif not r or r.get('expected') is None:
        items.append(_item('reconcile_revenue', 'open', 'no reported period of the base fiscal year (or no prior-year '
                           'comparison) in the facts: reconcile base-year revenue by hand'
                           + (f" (year to date {r['year_to_date'] / 1e6:,.0f} to {r['period_end']})" if r else '')))
    else:
        rev0 = params['rev0_m'] + (params.get('one_off_revenue_m') or 0)
        gap = rev0 * 1e6 / r['expected'] - 1
        d = (f"rev0 {params['rev0_m']:,.0f}" + (f" + one-off {params['one_off_revenue_m']:,.0f}" if params.get('one_off_revenue_m') else '')
             + f" against {r['expected'] / 1e6:,.0f} ({r['basis']}; filed {r['first_filed']}): {gap * 100:+.1f}%")
        if abs(gap) > 0.5:
            items.append(_item('reconcile_revenue', 'open', d + ': another unit or a remapped revenue line: reconcile by hand'))
        elif abs(gap) <= TOLERANCE:
            items.append(_item('reconcile_revenue', 'pass', d, gap_pct=round(gap * 100, 1)))
        elif where:
            items.append(_item('reconcile_revenue', 'pass', d + f'; reason stated in {where}: "{_short(why)}"',
                               gap_pct=round(gap * 100, 1), reason_in=where))
        else:
            items.append(_item('reconcile_revenue', 'fail', d + ': above 3% with no stated one-off or reason in the notes',
                               gap_pct=round(gap * 100, 1)))

    # shares
    base_sh = (params['scenarios']['base'].get('shares_m') or params['shares_m']) * 1e6
    rows = []
    for name, x in (('latest diluted count', rep['diluted']), ('cover-page count', rep['cover'])):
        if x and (as_of - _day(x['end'])).days <= STALE_FACT_DAYS:
            rows.append((name, x, base_sh / x['val'] - 1))
    where, why = _stated(params, 'shares', ('share', 'dilut', 'adr'))
    if not rows:
        items.append(_item('reconcile_shares', 'open', 'no recent reported share count in the facts: reconcile by hand'))
    else:
        d = f"model {base_sh / 1e6:,.1f}M against " + ', '.join(f"{n} {x['val'] / 1e6:,.1f}M ({x['end']}): {g * 100:+.1f}%"
                                                              for n, x, g in rows)
        worst = max(abs(g) for _, _, g in rows)
        gaps = {n: round(g * 100, 1) for n, _, g in rows}
        if worst <= TOLERANCE:
            items.append(_item('reconcile_shares', 'pass', d, gaps_pct=gaps))
        elif where:
            items.append(_item('reconcile_shares', 'pass', d + f'; reason stated in {where}: "{_short(why)}"', gaps_pct=gaps,
                               reason_in=where))
        else:
            items.append(_item('reconcile_shares', 'fail', d + ': over 3% apart with no reason in the notes', gaps_pct=gaps))

    # net cash
    n = rep['net_cash']
    where, why = _stated(params, 'net_cash', ('net_cash', 'cash', 'debt'))
    if not same_money:
        items.append(_item('reconcile_net_cash', 'not_applicable', f"SEC facts are in {rep['currency']}: reconcile by hand"))
    elif not n:
        items.append(_item('reconcile_net_cash', 'not_applicable', 'no cash tag together with a debt tag in the facts'))
    elif (as_of - _day(n['date'])).days > STALE_FACT_DAYS:
        items.append(_item('reconcile_net_cash', 'open', f"the latest balance sheet in the facts is of {n['date']}, more than "
                           f"{STALE_FACT_DAYS} days before the valuation: reconcile net cash by hand"))
    else:
        market = params['price'] * params['shares_m']
        gap = (params['net_cash_m'] - n['val'] / 1e6) / market
        d = (f"model {params['net_cash_m']:,.0f} (end of the base year) against {n['val'] / 1e6:,.0f} reported at {n['date']} "
             f"(cash and investments {sum(n['cash_tags'].values()) / 1e6:,.0f}, debt {sum(n['debt_tags'].values()) / 1e6:,.0f}): "
             f"the gap is {gap * 100:+.1f}% of the market value")
        if abs(gap) <= NET_CASH_TOLERANCE:
            items.append(_item('reconcile_net_cash', 'pass', d, gap_pct_of_market_value=round(gap * 100, 1)))
        elif where:
            items.append(_item('reconcile_net_cash', 'pass', d + f'; reason stated in {where}: "{_short(why)}"',
                               gap_pct_of_market_value=round(gap * 100, 1), reason_in=where))
        else:
            items.append(_item('reconcile_net_cash', 'fail', d + ': above 3% with no reason in the notes',
                               gap_pct_of_market_value=round(gap * 100, 1)))
    return items


# --------------------------------------------------------------------------------------------------- (c) the guided year

def check_guidance(params, res, answers=None):
    g = params.get('guidance')
    blocks = [g] if isinstance(g, dict) else g if isinstance(g, list) else []
    if not blocks:
        a = _answer(answers, 'L14')
        if a:
            return _item('guidance', 'pass', 'answered (no guidance block in the params): ' + a)
        return _item('guidance', 'open', 'no params.guidance block {year, revenue_low, revenue_high, margin_low, margin_high, '
                                         'source}: is the guided year at the company guide? (answer L14)')
    base = res['scenarios']['base']
    y0, bad, said = params['base_year'], [], []
    for b in blocks:
        y = b.get('year')
        rev = params['rev0_m'] if y == y0 else base['revenue_m'].get(y, base['revenue_m'].get(str(y)))
        mar = base['op_margin'].get(y, base['op_margin'].get(str(y)))
        if not _num(y) or rev is None:
            bad.append(f'guidance year {y!r} is not a model year ({y0}-{y0 + 6})')
            continue
        lo, hi = b.get('revenue_low'), b.get('revenue_high')
        if _num(lo) or _num(hi):
            lo_, hi_ = (lo if _num(lo) else -math.inf), (hi if _num(hi) else math.inf)
            inside = lo_ * (1 - 0.005) <= rev <= hi_ * (1 + 0.005)
            text = f"{y} revenue {rev:,.0f} against the guide {lo if _num(lo) else '-'}-{hi if _num(hi) else '-'}"
            (said if inside else bad).append(text + ('' if inside else ': outside the guide'))
        lo, hi = b.get('margin_low'), b.get('margin_high')
        if (_num(lo) or _num(hi)) and mar is not None:
            lo_, hi_ = (lo if _num(lo) else -math.inf), (hi if _num(hi) else math.inf)
            inside = lo_ - 0.0005 <= mar <= hi_ + 0.0005
            text = f"{y} margin {mar * 100:.1f}% against the guide {'-' if not _num(lo) else f'{lo * 100:g}%'}-{'-' if not _num(hi) else f'{hi * 100:g}%'}"
            (said if inside else bad).append(text + ('' if inside else ': outside the guide'))
        if not (isinstance(b.get('source'), str) and b['source'].strip()):
            bad.append(f'guidance {y}: no source')
    if bad:
        return _item('guidance', 'fail', '; '.join(bad + said))
    if not said:
        return _item('guidance', 'open', 'the guidance block holds no range to compare')
    return _item('guidance', 'pass', '; '.join(said))


def has_digestion_year(g):
    """The first index of a slowdown year in a growth list, else None. A slowdown year is a year of negative growth, a
    dip of 10 points or more that recovers the year after, or growth of 5% or less right after a year of 15% or more.
    A steady fade (38% -> 20% -> 14%) is not one."""
    for i, x in enumerate(g):
        if x < 0:
            return i
        if i and x <= g[i - 1] - DIGESTION_DROP and i + 1 < len(g) and g[i + 1] > x:
            return i
        if i and x <= DIGESTION_FLAT and g[i - 1] >= DIGESTION_AFTER:
            return i
    return None


def _tagged(params, yes, no):
    tags = params.get('tags')
    if not isinstance(tags, list):
        return None
    tags = [str(t).lower() for t in tags]
    if any(t in tags for t in no):
        return False
    return True if any(t in tags for t in yes) else False


def check_digestion_year(params, res, answers=None):
    g = params['scenarios']['base']['g']
    tagged = _tagged(params, ('capex_cycle', 'cyclical'), ('not_capex_cycle',))
    i = has_digestion_year(g)
    y = params['base_year'] + 1
    if tagged is False:
        return _item('digestion_year', 'not_applicable', 'not tagged as a capex-cycle supplier or cyclical')
    if i is not None:
        return _item('digestion_year', 'pass' if tagged else 'not_applicable',
                     f"the base holds a slowdown year: {y + i} growth {g[i] * 100:g}%"
                     + (f" after {g[i - 1] * 100:g}%" if i else ''))
    a = _answer(answers, 'L21')
    if a:
        return _item('digestion_year', 'pass', 'answered: ' + a)
    if tagged is None and g[0] < FAST_GROWTH:
        return _item('digestion_year', 'not_applicable', f'no tags, and the first base year grows {g[0] * 100:g}% (under 20%)')
    why = 'tagged capex cycle' if tagged else f'no tags; the first base year grows {g[0] * 100:g}%'
    return _item('digestion_year', 'open', f"the base growth path {[round(x * 100, 1) for x in g]} has no slowdown year ({why}): "
                                           'if the company sells into a capex cycle, add one or state why not (answer L21)')


# ------------------------------------------------------------------------------------------------------------ (d) price

def check_price(lm, params, res, price=None, today=None):
    as_of = _day(params['as_of'])
    notes, status = [], 'pass'
    stamp = params.get('price_time_utc')
    if isinstance(stamp, str) and len(stamp) >= 10:
        try:
            age = trading_days_between(stamp[:10], as_of)
            notes.append(f"the price is of {stamp[:10]}, {age} trading day(s) before as_of")
            if age > PRICE_MAX_TRADING_DAYS:
                status = 'fail'
        except ValueError:
            notes.append('price_time_utc does not start with a date')
    if today is not None:
        age = trading_days_between(as_of, today)
        notes.append(f"as_of is {age} trading day(s) before {today}")
        if age > PRICE_MAX_TRADING_DAYS and price is None:
            status = 'fail'
            notes.append('older than 3 trading days: reprice (lean_model --price P --as-of D)')
    more = {}
    if price is not None:
        move = price / params['price'] - 1
        notes.append(f"the quote {price:,.2f} is {move * 100:+.1f}% from the params price {params['price']:,.2f}")
        more['move_pct'] = round(move * 100, 1)
        if abs(move) > TOLERANCE:
            status = 'fail'
            again = lm.run(lm.reprice(copy.deepcopy(params), price))
            more['buy_test_pass_at_quote'] = again['buy_test']['pass']
            more['base_return_at_quote_pct'] = again['scenarios']['base']['annual_return_pct']
            notes.append(f"moved more than 3%: rerun at the quote (there the base returns "
                         f"{again['scenarios']['base']['annual_return_pct']:g}% a year and the buy test "
                         f"{'passes' if again['buy_test']['pass'] else 'does not pass'})")
    elif status == 'pass':
        if not notes:
            return _item('price', 'open', 'no quote given (--price) and no price_time_utc in the params: confirm the price '
                                          'before delivery')
        if today is None:
            notes.append('no quote given (--price): the move since the run is not checked')
    return _item('price', status, '; '.join(notes), **more)


# ------------------------------------------------------------------------------------------------ (e) the locked rules

def check_judgement(params, res, market=None, answers=None):
    block = params.get('judgement')
    if block is None:
        a = _answer(answers, 'L17')
        if a:
            return _item('judgement', 'pass', 'answered (no judgement block in the params): ' + a)
        return _item('judgement', 'open', 'no params.judgement block: do beta (2-year weekly, 0.67 x raw + 0.33), the exit P/E '
                                          '(own long-run forward median), the mature beta and the terminal ROIC follow the '
                                          'locked rules, or why not? (answer L17, or add the block)')
    v = LR.validate_judgement(block, params, market)
    if v['ok']:
        return _item('judgement', 'pass', 'the judgement block follows the locked rules or states why'
                     + (' (' + '; '.join(v['warnings']) + ')' if v['warnings'] else ''), rule=v.get('rule'))
    return _item('judgement', 'fail', '; '.join(v['errors']), rule=v.get('rule'))


def check_market_rates(params, res, market=None):
    if market is None:
        return _item('market_rates', 'not_applicable', 'no market file (research/market.json)')
    dev = LR.check_market(params, market)
    if not dev:
        return _item('market_rates', 'pass', f"rf {params['rf']:g} and ERP {params['erp']:g} are the entry in force on {params['as_of']}")
    block = params.get('judgement') if isinstance(params.get('judgement'), dict) else {}
    said = [k for k in ('rf', 'erp') if isinstance(block.get(k), dict) and str(block[k].get('reason_if_different', '')).strip()]
    d = '; '.join(f"{x['key']} {x['used']} against {x['rule']} in force from {x['from']}" for x in dev)
    if all(x['key'] in said for x in dev):
        return _item('market_rates', 'pass', d + ' (reason stated in the judgement block)')
    return _item('market_rates', 'fail', d + ': one rf and one ERP per valuation date')


# ------------------------------------------------------------------------------------- (f) against the previous params

def _flat(x, prefix=''):
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            out.update(_flat(v, f'{prefix}.{k}' if prefix else str(k)))
    else:
        out[prefix] = x
    return out


def model_inputs(params):
    """The inputs the model reads, flattened to {dotted key: value}; text keys are left out. A list is one input."""
    return {k: v for k, v in _flat({k: v for k, v in params.items() if k not in TEXT_KEYS}).items()
            if not k.startswith('probabilities.label')}


def _set(d, dotted, value):
    keys = dotted.split('.')
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    if value is _MISSING:
        d.pop(keys[-1], None)
    else:
        d[keys[-1]] = copy.deepcopy(value)


_MISSING = object()


def _weighted(res):
    return res['weighted_value_today']


def diff_inputs(lm, prev, params):
    """Every changed model input with the effect of that change alone on the previous base value today (and on the
    probability-weighted value). The effects of single changes do not have to add up: the rest is their interaction."""
    a, b = model_inputs(prev), model_inputs(params)
    r0, r1 = lm.run(copy.deepcopy(prev)), lm.run(copy.deepcopy(params))
    v0, v1 = r0['scenarios']['base']['value_today'], r1['scenarios']['base']['value_today']
    rows = []
    for k in sorted(set(a) | set(b)):
        if a.get(k, _MISSING) == b.get(k, _MISSING):
            continue
        row = {'input': k, 'before': a.get(k), 'after': b.get(k), 'base_value_effect': None, 'weighted_value_effect': None}
        trial = copy.deepcopy(prev)
        _set(trial, k, b.get(k, _MISSING))
        try:
            rt = lm.run(trial)
            row['base_value_effect'] = round(rt['scenarios']['base']['value_today'] - v0, 2)
            row['weighted_value_effect'] = round(_weighted(rt) - _weighted(r0), 2)
        except Exception as e:                              # the single change alone is not a valid file (e.g. one odd)
            row['note'] = 'not runnable alone: ' + _short(str(e), 80)
        rows.append(row)
    # dates and odds that only run together
    for name, keys in (('probabilities (all)', [r['input'] for r in rows if r['input'].startswith('probabilities.')]),
                       ('dates (as_of, days_to_base_year_end, years_to_horizon, base_year_end)',
                        [r['input'] for r in rows if r['input'] in ('as_of', 'days_to_base_year_end', 'years_to_horizon', 'base_year_end')])):
        if len(keys) > 1:
            trial = copy.deepcopy(prev)
            for k in keys:
                _set(trial, k, b.get(k, _MISSING))
            try:
                rt = lm.run(trial)
                rows.append({'input': name, 'before': None, 'after': None, 'group_of': keys,
                             'base_value_effect': round(rt['scenarios']['base']['value_today'] - v0, 2),
                             'weighted_value_effect': round(_weighted(rt) - _weighted(r0), 2)})
            except Exception as e:
                rows.append({'input': name, 'group_of': keys, 'base_value_effect': None, 'weighted_value_effect': None,
                             'note': 'not runnable: ' + _short(str(e), 80)})
    single = [r for r in rows if 'group_of' not in r and r['base_value_effect'] is not None]
    return {'base_value_before': v0, 'base_value_after': v1, 'base_value_change': round(v1 - v0, 2),
            'weighted_value_before': _weighted(r0), 'weighted_value_after': _weighted(r1),
            'weighted_value_change': round(_weighted(r1) - _weighted(r0), 2),
            'sum_of_single_effects': round(sum(r['base_value_effect'] for r in single), 2), 'changes': rows}


def level_path(params, res):
    """{fiscal year: (revenue $M, operating profit $M, growth or None)} of the base case, the base year included."""
    b = res['scenarios']['base']
    y0 = params['base_year']
    g = params['scenarios']['base']['g']
    out = {y0: (params['rev0_m'], None, None)}
    for k, rev in b['revenue_m'].items():
        y = int(k)
        m = b['op_margin'].get(k, b['op_margin'].get(str(k)))
        gi = g[y - y0 - 1] if 0 <= y - y0 - 1 < len(g) else None
        out[y] = (rev, None if m is None else rev * m, gi)
    return out


def diff_levels(lm, prev, params):
    """The absolute base revenue and operating-profit path of both files, year by year, and the years where the growth
    rate is the same in both files while the revenue level differs by more than 3% (a rebased base year or an earlier
    growth change hides it)."""
    pa, pb = level_path(prev, lm.run(copy.deepcopy(prev))), level_path(params, lm.run(copy.deepcopy(params)))
    rows, hidden = [], []
    for y in sorted(set(pa) & set(pb)):
        (ra, oa, ga), (rb, ob, gb) = pa[y], pb[y]
        row = {'year': y, 'revenue_before_m': round(ra), 'revenue_after_m': round(rb),
               'revenue_change_pct': round((rb / ra - 1) * 100, 1) if ra else None,
               'operating_profit_before_m': None if oa is None else round(oa),
               'operating_profit_after_m': None if ob is None else round(ob),
               'operating_profit_change_pct': round((ob / oa - 1) * 100, 1) if oa and ob is not None and oa > 0 else None,
               'growth_before': ga, 'growth_after': gb}
        same_growth = ga is not None and gb is not None and abs(ga - gb) < 1e-9
        row['growth_unchanged'] = same_growth
        if same_growth and ra and abs(rb / ra - 1) > TOLERANCE:
            hidden.append(y)
        rows.append(row)
    return {'rebased': prev['base_year'] != params['base_year'], 'base_year_before': prev['base_year'],
            'base_year_after': params['base_year'], 'years': rows, 'hidden_level_change_years': hidden,
            'largest_revenue_change_pct': max((abs(r['revenue_change_pct']) for r in rows if r['revenue_change_pct'] is not None),
                                              default=None),
            'largest_operating_profit_change_pct': max((abs(r['operating_profit_change_pct']) for r in rows
                                                        if r['operating_profit_change_pct'] is not None), default=None)}


def check_prev_inputs(lm, prev, params):
    if prev is None:
        return _item('prev_inputs', 'not_applicable', 'no previous params (--prev)')
    d = diff_inputs(lm, prev, params)
    n = len([r for r in d['changes'] if 'group_of' not in r])
    if not n:
        return _item('prev_inputs', 'pass', 'no model input changed', diff=d)
    top = sorted((r for r in d['changes'] if r.get('base_value_effect') is not None and 'group_of' not in r),
                 key=lambda r: -abs(r['base_value_effect']))[:4]
    text = (f"{n} changed input(s); base value {d['base_value_before']:,.2f} -> {d['base_value_after']:,.2f} "
            f"({d['base_value_change']:+,.2f}), weighted {d['weighted_value_change']:+,.2f}; largest alone: "
            + ', '.join(f"{r['input']} {r['base_value_effect']:+,.2f}" for r in top))
    odds = [r for r in d['changes'] if r['input'].startswith('probabilities') and r.get('weighted_value_effect')]
    if odds:
        text += f"; the odds alone move the weighted value {odds[-1]['weighted_value_effect']:+,.2f}"
    return _item('prev_inputs', 'pass', text + ': state each in the answer', diff=d)


def check_prev_levels(lm, prev, params, answers=None):
    if prev is None:
        return _item('prev_levels', 'not_applicable', 'no previous params (--prev)')
    d = diff_levels(lm, prev, params)
    big = [r for r in d['years'] if (r['revenue_change_pct'] is not None and abs(r['revenue_change_pct']) > TOLERANCE * 100)
           or (r['operating_profit_change_pct'] is not None and abs(r['operating_profit_change_pct']) > TOLERANCE * 100)]
    if not big:
        return _item('prev_levels', 'pass', 'revenue and operating profit within 3% of the previous path in every year', levels=d)
    text = '; '.join(f"{r['year']}: revenue {r['revenue_change_pct']:+.1f}%"
                     + ('' if r['operating_profit_change_pct'] is None else f", operating profit {r['operating_profit_change_pct']:+.1f}%")
                     for r in big)
    if d['hidden_level_change_years'] or d['rebased']:
        why = ('the base year was rebased' if d['rebased'] else 'the growth rate is unchanged') \
            + f" in {d['hidden_level_change_years'] or [r['year'] for r in big]} while the level moved"
        a = _answer(answers, 'L23')
        if a:
            return _item('prev_levels', 'pass', f'{text}; {why}; answered: {a}', levels=d)
        return _item('prev_levels', 'fail', f'{text}; {why}: "growth unchanged" hides a level change; state it (answer L23)', levels=d)
    return _item('prev_levels', 'pass', text + ' (each year\'s growth rate changed with it, so the change is visible)', levels=d)


# --------------------------------------------------------------------------------------------- (g) the manual questions

def manual_applies(lesson, params, res, prev, buy=None):
    """(applies, reason when it does not) for a manual lesson's `ask` field. buy: whether this is a buy (default: the
    buy test at the params price); run_checks also counts a pass at the quote."""
    ask = lesson.get('ask', 'never')
    if ask == 'always':
        return True, None
    if ask == 'buy':
        buy = res['buy_test'].get('pass') if buy is None else buy
        return (True, None) if buy else (False, 'the buy test does not pass')
    if ask == 'prev':
        return (True, None) if prev is not None else (False, 'no previous params (--prev)')
    if ask == 'asset_heavy':
        tagged = _tagged(params, ('asset_heavy', 'capex_cycle_builder'), ('not_asset_heavy',))
        if tagged is None:
            heavy = min(params['scenarios']['base']['conv']) < HEAVY_CONVERSION
            return (True, None) if heavy else (False, 'no tags, and free cash stays above half of operating profit in the base')
        return (True, None) if tagged else (False, 'not tagged asset_heavy')
    return False, 'outside the valuation check (a process rule)'


def manual_questions(lessons, params, res, prev=None, answers=None, buy=None):
    items = []
    for x in lessons or []:
        if str(x.get('enforcement', '')).strip() != 'manual' or x.get('ask', 'never') == 'never':
            continue
        ok, why = manual_applies(x, params, res, prev, buy)
        id = 'ask.' + x['id']
        if not ok:
            items.append(_item(id, 'not_applicable', why, [x['id']], x.get('blocks_buy'), 'manual', question=x['check']))
            continue
        if x.get('blocks_buy'):
            status, text = _blocking_answer(answers, x['id'])
            items.append(_item(id, status, text or x['check'], [x['id']], True, 'manual', question=x['check'],
                               category=x.get('category')))
            continue
        a = _answer(answers, x['id'])
        items.append(_item(id, 'pass' if a else 'open', ('answered: ' + a) if a else x['check'], [x['id']], x.get('blocks_buy'),
                           'manual', question=x['check'], category=x.get('category')))
    return items


# ----------------------------------------------------------------------------------------------------------- the whole

def run_checks(lm, params, prev=None, facts=None, price=None, answers=None, lessons=None, market=None, today=None,
               facts_cutoff=None):
    """lm: the lean_model module (scripts/lean_model.py). Returns the check document; raises lm.ParamsError when the
    params cannot be run at all. Nothing is written and no input is changed."""
    params = copy.deepcopy(params)
    res = lm.run(copy.deepcopy(params))
    checked = lm.prepare(copy.deepcopy(params))[0]            # the dates the model used (derived when base_year_end is given)
    items = [
        check_inputs(checked, res), check_lenses_agree(checked, res), check_calibrated_lens(checked, res),
        check_funding(checked, res), check_equity_wiped(checked, res), check_method_gap(checked, res),
        check_on_the_line(checked, res), check_rerun_price(checked, res), check_reverse_reading(checked, res),
        check_distributions_band(checked, res), check_probabilities_label(checked, res)]
    items += check_reconciliation(checked, res, facts, facts_cutoff)
    items += [check_guidance(checked, res, answers), check_digestion_year(checked, res, answers),
              check_price(lm, params, res, price, today), check_judgement(checked, res, market, answers),
              check_market_rates(checked, res, market), check_prev_inputs(lm, prev, params),
              check_prev_levels(lm, prev, params, answers)]
    for it in items:
        fn = AUTO[it['id']][0]
        it['lessons'] = _lessons_for(lessons, fn)
        it['function'] = fn
    # a quote that moved more than 3% is rerun by check_price: a pass there is a buy at today's price, and it blocks too
    at_quote = next((it.get('buy_test_pass_at_quote') for it in items if it['id'] == 'price'), None)
    buy_here = bool(res['buy_test'].get('pass'))
    buy = buy_here or bool(at_quote)
    items += manual_questions(lessons, checked, res, prev, answers, buy)
    blocking = [it['id'] for it in items if it['blocks_buy'] and it['status'] in ('fail', 'open')]
    counts = {s: sum(it['status'] == s for it in items) for s in STATUSES}
    return {'check_version': CHECK_VERSION, 'ticker': params.get('ticker'), 'as_of': str(params.get('as_of'))[:10],
            'price': params['price'], 'base_value_today': res['scenarios']['base']['value_today'],
            'base_return_pct': res['scenarios']['base']['annual_return_pct'], 'buy_test_pass': buy_here,
            'buy_test_pass_at_quote': at_quote,
            'buy_test_price': res['buy_test'].get('max_price_passing'), 'counts': counts,
            'blocking': blocking if buy else [], 'would_block_a_buy': blocking,
            'exit_code': 1 if buy and blocking else 0,
            'verdict': ('buy test passes at the quote (not at the params price), and blocking items are failed or open: '
                        'not ready to deliver as a buy' if buy and blocking and not buy_here
                        else 'buy test passes, but blocking items are failed or open: not ready to deliver as a buy' if buy and blocking
                        else 'buy test passes and no blocking item is failed or open' if buy
                        else 'not a buy at this price: nothing blocks'),
            'model_warnings': res.get('warnings', []) + res['method_gap'].get('warnings', []),
            'answers_given': sorted(k for k in (answers or {}) if _answer(answers, k)),
            'inputs_supplied': {'previous_params': prev is not None, 'facts': facts is not None, 'price': price,
                                'today': None if today is None else str(today), 'market_file': market is not None},
            'items': items}


MARK = {'pass': 'pass', 'fail': 'FAIL', 'open': 'OPEN', 'not_applicable': 'n/a'}


def render(doc, width=150, show=('fail', 'open', 'pass', 'not_applicable')):
    """The short plain table."""
    head = (f"lean_check {doc['ticker']} {doc['as_of']}: price {doc['price']:,.2f}, base value {doc['base_value_today']:,.2f}, "
            f"base return {doc['base_return_pct']:g}% a year, buy test {'PASSES' if doc['buy_test_pass'] else 'does not pass'}")
    lines = [head, '-' * min(len(head), width)]
    for it in doc['items']:
        if it['status'] not in show:
            continue
        flag = '*' if it['blocks_buy'] else ' '
        lines.append(f"{MARK[it['status']]:<5}{flag} {it['id']:<20} {_short(it['detail'], width - 28)}")
    c = doc['counts']
    lines.append('-' * min(len(head), width))
    lines.append(f"{c['pass']} pass, {c['fail']} fail, {c['open']} open, {c['not_applicable']} not applicable   (* = blocks a buy)")
    lines.append(doc['verdict'] + (': ' + ', '.join(doc['blocking']) if doc['blocking'] else ''))
    return '\n'.join(lines)
