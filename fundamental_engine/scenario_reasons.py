"""Scenario reasons: behind every bear/base/bull price (דובי/בסיסי/שורי), the condition that has to hold and the business
of that year, read from the executed casefile.

The owner (25.10.2026): "I want bullish, base and bearish. And behind the scenes, of course, every price has a reason: if
something happens, then this price." build(casefile) answers that for each scenario and each dated row of the owner's
yearly table:

- condition: one plain-Hebrew sentence of what has to happen (if_he). Events come ONLY from the analyst
  (inputs.analyst_sheet.scenario_events = {bear|base|bull: [{event_he, evidence_ids}]}, validated against the FactBook by
  analyst_sheet.build and stored in casefile['analyst_sheet']['scenario_events']; what could not be read is listed in
  'scenario_events_rejected' and said here); an event without known evidence ids is kept and marked a hypothesis, in
  Hebrew too. Without events the condition is the model's own mechanics in plain words, never an invented story: on the
  operating route revenue growth (the first full fiscal year with a rate -> the last explicit year, dated, and the
  long-run rate), operating margin after SBC (start -> end, a loss named as a loss), external funding and dilution; on the
  residual-income route (financials) the ROE path, the payout and book value per share; and the discount rates (the
  explicit years' cost of equity and the long-run rate when it differs). A scenario without a value gets no rate clause.
- rows: today and each dated row the owner's table shows (render.yearly_table_rows on
  dc.expected_return.yearly_at_model_cost_of_equity: the dates where all three scenarios have a value, at least three of
  them; else the same rule on val.result.annual_values through the fifth anniversary). Each row has the executed value
  (exactly the number in the casefile), the drivers of the fiscal year that holds that date (operating: revenue, executed
  growth and the path's assumed rate, operating margin after SBC, earnings and FCF per share, diluted shares; residual
  income: ROE, net income, earnings and book value per share, shares; both: cumulative distributions), an implied P/E on
  the next full fiscal year's earnings estimate when it is positive, and why_he: 'אם <condition>, אז ב־<date>: …→ שווי …'.
  When the owner's table shows no yearly table, no dated values are listed and the reasons say so.
- assumption ids (the scenario's own, its period and terminal ids, the underwriting scenario reviews), the analyst's
  validated driver overrides for that scenario (context, never events) and gaps: what the casefile does not hold, said
  instead of filled in (each with a plain-Hebrew noun in 'he').

Nothing is revalued and no value changes: values are copied from the executed valuation, drivers are read from the
executed forecast rows (val.result.scenarios[].forecast) and the forecast paths (fc.paths, fc.anchors, fc.funding,
fc.cost_of_equity); the only arithmetic is per-share division, ratios and sums of executed flows. A valuation the owner's
table does not show (render.value_display: a non-price verdict, blocked data, a failed valuation, demo inputs, a governance
flag, a rejected assumption, a 'hold' material issue) gets no values here either: the reasons say why there is no value. A
price claim withheld only for review reasons (an unreleased forensic flag, an open material issue, an unreviewed draft)
keeps its values under the same banner as the table ('טיוטה — לא אומתה' and the reasons: draft_reason_he).
These reasons explain the model's conditional values; they are not forecasts, probabilities or price targets. The
earnings per share here is the model's estimate, not reported or GAAP earnings.
"""
from datetime import date

SCENARIOS = ('bear', 'base', 'bull')
LABEL_HE = {'bear': 'דובי', 'base': 'בסיסי', 'bull': 'שורי'}
ROUTES_READ = ('operating', 'residual_income')
FUNDING_STATUSES = ('self_funded', 'share_settled', 'assumed_external_financing', 'unfunded')
ROUNDING = ('value and cumulative_distributions: the executed numbers, unrounded; revenue, net income and diluted shares: '
            'executed, unrounded; growth, margins, ROE, payout and rates: 6 decimals; eps, fcf_per_share and '
            'book_value_per_share: 4 decimals; implied P/E: 2 decimals; a rounded -0.0 is written 0.0; Hebrew text: per-share '
            'values to 3 significant figures (render._ps), percentages to one decimal, revenue in billions/millions, the '
            'implied P/E as a whole number (its 2-decimal value rounded half to even)')
DEFINITIONS = {
    'fiscal_year': 'the executed forecast period that holds the row date (a period ending on the date, else the one running '
                   'through it); a valuation date inside a fiscal year (the fifth anniversary) is merged back into its fiscal '
                   'year (monitor._merge_interim; residual-income net income is summed the same way); the first period, which '
                   'starts on the valuation date, is the current fiscal year and is a full year only when it starts on the '
                   'previous fiscal year end (365 days or more, or fc.anchors.last_fiscal_year_end), whatever its length',
    'revenue': 'executed forecast revenue of that fiscal year; for the current fiscal year, only partly inside the forecast, '
               'the forecast anchor fc.anchors.current_fiscal_year (the model full-year revenue)',
    'revenue_growth': 'executed: revenue of that fiscal year / revenue of the previous full fiscal year (or the current-year '
                      'anchor) - 1; for the current fiscal year, the anchor growth over the last reported year',
    'revenue_growth_assumption': 'fc.paths[scenario] growth of that fiscal year: the input annual rate (day counts, such as '
                                 'a 366-day year, make the executed ratio differ slightly)',
    'operating_margin_after_sbc': 'executed EBIT after SBC and D&A / revenue of that fiscal year (for the current fiscal '
                                  'year, fc.paths when held, else the executed partial period); negative is an operating loss',
    'eps': "the model's earnings estimate per share, not reported, GAAP or adjusted earnings; full fiscal years only. "
           'Operating route: (EBIT after SBC - interest - cash taxes) / average model shares ((open + end) / 2), investment '
           'income excluded (as growth.multiples_view), model shares carry no buybacks (excess cash is paid out as '
           'distributions). Residual-income route: model net income (ROE x book) / shares (constant in that model)',
    'fcf_per_share': '(CFO - maintenance capex - growth capex) / average model shares, full fiscal years only; after SBC '
                     'when the scenario charges SBC as a cash-equivalent cost (sbc_policy)',
    'roe': 'residual-income route: model net income / opening book equity per year of the period (current fiscal year: '
           'fc.paths when held)',
    'book_value_per_share': 'residual-income route: model book equity / shares at the row date',
    'diluted_shares': 'model shares at the row date (opening shares for today, period-end shares otherwise)',
    'cumulative_distributions': 'distributions per share (dividends / buybacks) paid by the row date, from the same yearly row',
    'implied_pe': 'row value / eps of the next full fiscal year (the first full-year period starting on or after the row '
                  'date; the partial current fiscal year is never it); null with the reason when that year is missing or its '
                  'eps is not positive',
}
GAP_HE = {'no_values': 'שווי לתרחיש הזה בהערכה שבוצעה',
          'no_forecast_rows': 'שורות התחזית שבוצעו לתרחיש',
          'no_forecast_paths': 'מסלולי התחזית לפי שנה (הצמיחה והרווחיות נקראו משורות ההערכה)',
          'current_year_revenue': 'הכנסות של כל השנה הנוכחית',
          'growth': 'צמיחה של שנת כספים',
          'next_year_eps': 'אומדן רווח של שנת הכספים המלאה הבאה (למכפיל)',
          'cost_of_equity': 'שיעור היוון לתרחיש',
          'funding': 'פירוט מימון לתרחיש (מניות חדשות ופערי מימון נקראו משורות ההערכה)',
          'five_year_date': 'תאריך יום השנה החמישי (כל הערכים המתוארכים מוצגים)',
          'yearly_rows': 'טבלה שנתית (נקראו הערכים המתוארכים של ההערכה)',
          'no_yearly_table': 'טבלה שנתית בטבלת השווי (אין שלושה מועדים שבהם לכל התרחישים יש שווי)',
          'events_unvalidated': 'בדיקת האירועים מול מאגר הנתונים המלא (נבדקו רק מול הראיות שבתיק)',
          'route_drivers': 'פירוק של מודל ההערכה הזה לגורמים עסקיים',
          'condition_not_distinct': 'מה שמבדיל בין התרחיש הזה לתרחיש אחר עם שווי אחר (התנאי זהה)',
          'events_rejected': 'אירועים שהאנליסט סיפק ולא נקראו'}
REJECT_HE = {'not_object': 'האירועים לא נמסרו כאובייקט של תרחישים',
             'unknown_scenario': 'שם תרחיש לא מוכר: "{scenario}" (צריך bear, base או bull)',
             'not_list': 'האירועים של התרחיש לא נמסרו כרשימה',
             'event_not_object': 'אירוע {n} אינו אובייקט עם event_he',
             'no_event_he': 'לאירוע {n} אין טקסט ב־event_he',
             'bad_evidence_ids': 'ל־evidence_ids של אירוע {n} יש סוג לא תקין (צריך רשימה או מזהה אחד כטקסט)',
             'evidence_ids_items_dropped': 'באירוע {n} הושמטו מזהי ראיות שאינם טקסט',
             'unreadable': 'אירוע {n} לא נקרא',
             'unknown_keys': 'באירוע {n} יש מפתחות לא מוכרים (רק event_he, evidence_ids, hypothesis)'}
EPS_BASIS_HE = {'operating': 'רווח תפעולי למניה = רווח תפעולי אחרי תגמול במניות, ריבית ומס, חלקי מספר המניות במודל (בלי הכנסות '
                             'מהשקעות ובלי רכישות עצמיות). זה לא רווח GAAP, לא הרווח המתואם שהחברה מפרסמת ולא הרווח למניה של '
                             'טבלת המחיר השנתית (שם נכללות גם הכנסות מהשקעות אחרי מס, על ממוצע מניות משוקלל), ולכן המכפיל כאן '
                             'לא בהכרח דומה למכפיל רווח שמצוטט בשוק.',
                'residual_income': 'אומדן רווח למניה = הרווח הנקי במודל (התשואה על ההון כפול ההון העצמי) חלקי מספר המניות. זה '
                                   'לא רווח מדווח, ולכן המכפיל כאן לא בהכרח דומה למכפיל רווח שמצוטט בשוק.'}

EPS_HE = {False: 'רווח תפעולי למניה', True: 'אומדן רווח למניה'}            # by route: operating / residual income
EPS_NEXT_HE = {False: 'הרווח התפעולי למניה', True: 'אומדן הרווח למניה'}


def _n(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r(v, d):
    if not _n(v):
        return None
    x = round(v, d)
    return 0.0 if x == 0 else x                  # never -0.0


def _day(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _years(a, b):
    da, db = _day(a), _day(b)
    return (db - da).days / 365.25 if da and db else None


def _ym(d):
    return str(d)[:7].replace('-', '/')


def _pct(x, d=1):
    return f'{x * 100:.{d}f}%'


def _cur(cur):
    return '$' if cur == 'USD' else (f' {cur}' if cur else '')


def _big(v, cur):
    a = abs(v)
    s = f'{v / 1e9:,.1f} מיליארד' if a >= 1e9 else f'{v / 1e6:,.0f} מיליון' if a >= 1e6 else f'{v:,.0f}'
    return s + _cur(cur)


def _money(v, cur):
    from .render import _money as money
    return money(v, cur)


def _gap(gaps, code, detail, he=None):
    g = {'code': code, 'detail': detail, 'he': he or GAP_HE.get(code, code)}
    if g not in gaps:
        gaps.append(g)


def _check(cf):
    """A pipeline casefile or nothing: a malformed one is invalid input (ValueError), not an operator failure."""
    if not isinstance(cf, dict):
        raise ValueError('scenario_reasons needs a pipeline casefile (a JSON object)')
    for k in ('dc', 'val', 'fc', 'analyst_sheet', 'company'):
        if cf.get(k) is not None and not isinstance(cf[k], dict):
            raise ValueError(f'scenario_reasons needs a pipeline casefile: {k} must be an object')
    for parent, keys in (('dc', ('verdict', 'valuation_view', 'expected_return', 'price', 'company')),
                         ('val', ('result', 'case'))):
        for k in keys:
            v = (cf.get(parent) or {}).get(k)
            if v is not None and not isinstance(v, dict):
                raise ValueError(f'scenario_reasons needs a pipeline casefile: {parent}.{k} must be an object')
    y = ((cf.get('dc') or {}).get('expected_return') or {}).get('yearly_at_model_cost_of_equity')
    if y is not None and (not isinstance(y, dict) or not isinstance(y.get('rows') or [], list)):
        raise ValueError('scenario_reasons needs a pipeline casefile: yearly_at_model_cost_of_equity.rows must be a list')
    res = (cf.get('val') or {}).get('result') or {}
    for k in ('scenarios', 'annual_values'):
        if res.get(k) is not None and not isinstance(res[k], list):
            raise ValueError(f'scenario_reasons needs a pipeline casefile: val.result.{k} must be a list')


# ------------------------------------------------------------------ reading the casefile

def _dated(rows):
    from .render import yearly_table_rows
    return [r for _, r in yearly_table_rows({'yearly_at_model_cost_of_equity': {'rows': rows}})]


def _yearly(cf, gaps):
    """The dated values: (source name, every dated row [{date, bear, base, bull, distributions}], the rows the owner's
    yearly table shows (render.yearly_table_rows: all three scenarios valued, at least three dates; else none))."""
    dc = cf.get('dc') or {}
    y = (dc.get('expected_return') or {}).get('yearly_at_model_cost_of_equity') or {}
    if y.get('rows'):
        rows = sorted((r for r in y['rows'] if isinstance(r, dict) and r.get('date')), key=lambda r: str(r['date']))
        return 'dc.expected_return.yearly_at_model_cost_of_equity', rows, _dated(rows)
    res = ((cf.get('val') or {}).get('result') or {})
    av = [r for r in res.get('annual_values') or [] if isinstance(r, dict) and r.get('date')]
    if not av:
        return None, [], []
    _gap(gaps, 'yearly_rows', 'dc.expected_return.yearly_at_model_cost_of_equity absent: dated values read from '
                              'val.result.annual_values')
    five = ((dc.get('valuation_view') or {}).get('five_year') or {}).get('date')
    if not five:
        _gap(gaps, 'five_year_date', 'dc.valuation_view.five_year.date absent: every dated value of the executed path is listed')
    paid = {sc.get('name'): {t.get('date'): t.get('cumulative_dividend_per_share') for t in sc.get('timeline') or []
                             if isinstance(t, dict)}
            for sc in res.get('scenarios') or [] if isinstance(sc, dict)}
    rows = sorted(({'date': r['date'], **{s: r.get(s) for s in SCENARIOS},
                    'distributions': {s: (paid.get(s) or {}).get(r['date']) for s in SCENARIOS}}
                   for r in av if not five or r['date'] <= five), key=lambda r: str(r['date']))
    return 'val.result.annual_values', rows, _dated(rows)


def _interim(cf, five):
    """Valuation dates that split a fiscal year (the fifth anniversary): from the forecast calendar or the driver spec; when
    the casefile holds neither, the fifth-anniversary date when a forecast period ends on it."""
    fc = cf.get('fc') or {}
    lists = [((fc.get('anchors') or {}).get('calendar') or {}).get('interim'), (fc.get('spec') or {}).get('interim_boundaries'),
             (((cf.get('val') or {}).get('case') or {}).get('generated_from') or {}).get('interim_boundaries')]
    known = [x for x in lists if isinstance(x, list)]
    if known:
        return set(d for x in known for d in x)
    return {five} if five else set()


def _fiscal_years(rows, interim, today, last_fy_end):
    """Executed forecast periods with interim splits merged back into fiscal years; the merged year keeps the opening share
    count and opening book of its first part, and residual-income flows are summed like monitor's flow keys. The first
    period, starting on the valuation date, is the current fiscal year: a full year only when it starts on the previous
    fiscal year end (365 days or more, or the last reported fiscal year end); every other period is a full year when it
    lasts 0.9 to 1.01 years."""
    from .monitor import _merge_interim
    rows = [r for r in rows or [] if isinstance(r, dict)]
    out = []
    for p in _merge_interim(rows, interim):
        p = dict(p)
        if p.get('merged_interim'):
            first = next((r for r in rows if r.get('start') == p.get('start') and r.get('end') == p['merged_interim']), {})
            if 'shares_open' in first:
                p['shares_open'] = first['shares_open']
            if 'book_open' in first:
                p['book_open'] = first['book_open']
            for k in ('net_income', 'residual_income'):
                if _n(first.get(k)) and _n(p.get(k)):
                    p[k] = first[k] + p[k]
        da, db = _day(p.get('start')), _day(p.get('end'))
        if today and str(p.get('start')) == str(today) and da and db:
            p['_current'] = True
            p['_full'] = (db - da).days >= 365 or str(p.get('start')) == str(last_fy_end)
        else:
            y = _years(p.get('start'), p.get('end'))
            p['_full'] = y is not None and .9 <= y <= 1.01
        out.append(p)
    return out


def _per_share(p, flow):
    so, se = p.get('shares_open'), p.get('shares')
    if not p.get('_full') or not _n(flow) or not _n(so) or not _n(se) or so + se <= 0:
        return None
    return flow / ((so + se) / 2)


def _eps(p, method='operating'):
    if not p or not p.get('_full'):
        return None
    if method == 'residual_income':
        ni, sh = p.get('net_income'), p.get('shares')
        return ni / sh if _n(ni) and _n(sh) and sh > 0 else None
    if not all(_n(p.get(k)) for k in ('ebit', 'interest', 'cash_taxes')):
        return None
    return _per_share(p, p['ebit'] - p['interest'] - p['cash_taxes'])


def _fcf_ps(p):
    if not all(_n(p.get(k)) for k in ('cfo', 'maintenance_capex', 'growth_capex')):
        return None
    return _per_share(p, p['cfo'] - p['maintenance_capex'] - p['growth_capex'])


def _period_for(years, d):
    """The fiscal year that holds date d: the one ending on d, else the one running through it."""
    return (next((p for p in years if p.get('end') == d), None)
            or next((p for p in years if str(p.get('start')) <= d < str(p.get('end'))), None))


def _shares_at(raw):
    """Model shares on each forecast date (not a merged fiscal year): the opening count on the first start (the period-end
    count when the model holds shares constant, as valuation._roll_back), the period-end count on every end."""
    out = {}
    if raw:
        first = raw[0].get('shares_open', raw[0].get('shares'))
        if _n(first):
            out[raw[0].get('start')] = first
    for p in raw:
        if _n(p.get('shares')):
            out[p.get('end')] = p['shares']
    return out


def _book_at(raw):
    """Residual-income route: model book equity on each forecast date."""
    out = {}
    if raw and _n(raw[0].get('book_open')):
        out[raw[0].get('start')] = raw[0]['book_open']
    for p in raw:
        if _n(p.get('book_equity')):
            out[p.get('end')] = p['book_equity']
    return out


def _next_full(years, d):
    """The next full fiscal year after date d: the first full-year period starting on or after d (the partial current
    fiscal year is never full, so it is skipped)."""
    return next((p for p in years if p.get('_full') and str(p.get('start')) >= d), None)


def _roe_of(p):
    ni, bo, y = p.get('net_income'), p.get('book_open'), _years(p.get('start'), p.get('end'))
    return ni / bo / y if _n(ni) and _n(bo) and bo > 0 and y else None


# ------------------------------------------------------------------ the condition

def _paths(fc, s):
    return [p for p in ((fc.get('paths') or {}).get(s) or []) if isinstance(p, dict)]


def _scenario_spec(cf, s):
    for sc in ((cf.get('fc') or {}).get('spec') or {}).get('scenarios') or []:
        if isinstance(sc, dict) and sc.get('name') == s:
            return sc
    for sc in ((cf.get('val') or {}).get('case') or {}).get('scenarios') or []:
        if isinstance(sc, dict) and sc.get('name') == s:
            return sc
    return {}


def _first_last(d, key, rows, f, source):
    rows = [p for p in rows if _n(f(p))]
    if rows:
        d[key + '_first'] = {'value': _r(f(rows[0]), 6), 'fiscal_year_end': rows[0].get('end'), 'source': source}
        d[key + '_last'] = {'value': _r(f(rows[-1]), 6), 'fiscal_year_end': rows[-1].get('end'), 'source': source}
    return bool(rows)


def _rates(cf, s, d, gaps):
    fc = cf.get('fc') or {}
    coe = fc.get('cost_of_equity') if isinstance(fc.get('cost_of_equity'), dict) else {}
    spec = _scenario_spec(cf, s)
    if _n(coe.get(s)):
        d['cost_of_equity'] = {'value': _r(coe[s], 6), 'source': 'fc.cost_of_equity'}
    elif _n(spec.get('cost_of_equity')):
        d['cost_of_equity'] = {'value': _r(spec['cost_of_equity'], 6), 'source': 'valuation case scenario'}
    else:
        _gap(gaps, 'cost_of_equity', f'{s}: no cost of equity in fc.cost_of_equity or the valuation case')
    tk = coe.get('terminal_cost_of_equity') if isinstance(coe.get('terminal_cost_of_equity'), dict) else {}
    tw = coe.get('terminal_wacc') if isinstance(coe.get('terminal_wacc'), dict) else {}
    if _n(tk.get(s)):
        d['terminal_cost_of_equity'] = {'value': _r(tk[s], 6), 'source': 'fc.cost_of_equity.terminal_cost_of_equity'}
    if _n(tw.get(s)):
        d['terminal_wacc'] = {'value': _r(tw[s], 6), 'source': 'fc.cost_of_equity.terminal_wacc'}
    elif _n((spec.get('terminal') or {}).get('wacc')):
        d['terminal_wacc'] = {'value': _r(spec['terminal']['wacc'], 6), 'source': 'scenario terminal'}


def _drivers(cf, s, years, raw, gaps, opening_shares, five_date):
    """Operating route: the mechanical drivers of the scenario as the casefile holds them, each with its source."""
    fc = cf.get('fc') or {}
    paths = _paths(fc, s)
    d = {}
    if not _first_last(d, 'growth', paths, lambda p: p.get('growth'), 'fc.paths'):
        full = [p for p in years if p.get('_full') and _n(p.get('revenue'))]
        pairs = [(a, b) for a, b in zip(full, full[1:]) if a.get('end') == b.get('start') and a['revenue'] > 0]
        if pairs:
            for k, (a, b) in (('growth_first', pairs[0]), ('growth_last', pairs[-1])):
                d[k] = {'value': _r(b['revenue'] / a['revenue'] - 1, 6), 'fiscal_year_end': b.get('end'),
                        'source': 'executed forecast rows'}
    if not _first_last(d, 'operating_margin', paths, lambda p: p.get('operating_margin'), 'fc.paths'):
        _first_last(d, 'operating_margin', years,
                    lambda p: p['ebit'] / p['revenue'] if _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0 else None,
                    'executed forecast rows')
    term = (_scenario_spec(cf, s).get('terminal') or {})
    if _n(term.get('growth')):
        d['terminal_growth'] = {'value': _r(term['growth'], 6), 'source': 'scenario terminal'}
    _rates(cf, s, d, gaps)
    f = (fc.get('funding') or {}).get(s) if isinstance(fc.get('funding'), dict) else None
    if isinstance(f, dict) and f.get('status'):
        d['funding'] = {'status': f.get('status'), 'equity_raised': f.get('equity_raised'), 'debt_assumed': f.get('debt_assumed'),
                        'new_financing_shares': f.get('new_financing_shares'), 'sbc_settlement_shares': f.get('sbc_settlement_shares'),
                        'first_gap': f.get('first_gap'), 'source': 'fc.funding',
                        **({'stub_draw_immaterial': True} if f.get('stub_draw_immaterial') else {})}
    elif years:
        new = sum(p.get('new_financing_shares') or 0 for p in years if _n(p.get('new_financing_shares')))
        gap = next((p.get('end') for p in years if _n(p.get('funding_gap')) and p['funding_gap'] > 1e-6), None)
        d['funding'] = {'status': None, 'new_financing_shares': new, 'first_gap': gap, 'source': 'executed forecast rows'}
        _gap(gaps, 'funding', f'{s}: fc.funding absent: new shares and funding gaps read from the executed rows')
    five_shares = _shares_at(raw).get(five_date)
    if _n(opening_shares) and _n(five_shares) and opening_shares > 0:
        d['shares'] = {'today': opening_shares, 'fifth_year': five_shares, 'change': _r(five_shares / opening_shares - 1, 6),
                       'source': 'executed forecast rows'}
    return d


def _drivers_ri(cf, s, years, raw, gaps, opening_shares, five_date):
    """Residual-income route (financials): ROE and payout paths, the long-run ROE and growth, book value per share today
    and at the fifth anniversary, the regulatory-capital check; shares are constant in that model."""
    fc = cf.get('fc') or {}
    paths = _paths(fc, s)
    d = {}
    if not _first_last(d, 'roe', paths, lambda p: p.get('roe'), 'fc.paths'):
        _first_last(d, 'roe', years, _roe_of, 'executed forecast rows')
    _first_last(d, 'payout', paths, lambda p: p.get('payout_fraction'), 'fc.paths')
    term = (_scenario_spec(cf, s).get('terminal') or {})
    if _n(term.get('roe')):
        d['terminal_roe'] = {'value': _r(term['roe'], 6), 'source': 'scenario terminal'}
    if _n(term.get('growth')):
        d['terminal_growth'] = {'value': _r(term['growth'], 6), 'source': 'scenario terminal'}
    _rates(cf, s, d, gaps)
    book, shares = _book_at(raw), _shares_at(raw)
    today = raw[0].get('start') if raw else None
    if all(_n(x) and x > 0 for x in (book.get(today), shares.get(today), book.get(five_date), shares.get(five_date))):
        d['book_value_per_share'] = {'today': _r(book[today] / shares[today], 4), 'fifth_year': _r(book[five_date] / shares[five_date], 4),
                                     'fifth_year_date': five_date, 'source': 'executed forecast rows'}
    short = next((p.get('end') for p in raw if _n(p.get('capital_shortfall')) and p['capital_shortfall'] > 0), None)
    d['funding'] = {'status': None, 'new_financing_shares': 0, 'first_capital_shortfall': short,
                    'source': 'executed forecast rows (residual income: constant shares, no recapitalization modeled)'}
    return d


def _growth_he(d):
    """Revenue growth: the first fiscal year with a rate (named) and the last explicit year (dated)."""
    g0, g1 = d.get('growth_first'), d.get('growth_last')
    if not g0:
        return None
    v0 = g0['value']
    out = (f"ההכנסות {'יצמחו' if v0 >= 0 else 'יקטנו'} בכ־{_pct(abs(v0))} בשנה שמסתיימת ב־{_ym(g0['fiscal_year_end'])}")
    if g1 and g1.get('fiscal_year_end') != g0.get('fiscal_year_end'):
        v1, when = g1['value'], _ym(g1['fiscal_year_end'])
        if abs(v1 - v0) < .0005:
            out += f" וימשיכו בקצב הזה עד {when}"
        elif abs(v1) < .0005:
            out += f", ועד {when} יתייצבו"
        elif v1 < 0:
            out += f", ועד {when} יקטנו בכ־{_pct(-v1)} בשנה"
        elif v0 < 0:
            out += f", ואחר כך יחזרו לצמוח, לכ־{_pct(v1)} בשנה עד {when}"
        else:
            out += f", והצמיחה {'תאט' if v1 < v0 else 'תאיץ'} לכ־{_pct(v1)} בשנה עד {when}"
    return out


def _margin_he(d, long):
    """Operating margin after SBC, start -> end (dated); a negative margin is an operating loss."""
    m0, m1 = d.get('operating_margin_first'), d.get('operating_margin_last')
    if not m0:
        return None
    tag = ' (אחרי תגמול במניות)' if long else ''
    a = m0['value']
    if not m1 or m1.get('fiscal_year_end') == m0.get('fiscal_year_end') or abs(m1['value'] - a) < .0005:
        return (f'הרווח התפעולי{tag} יישאר כ־{_pct(a)} מההכנסות' if a >= 0 else
                f'ההפסד התפעולי{tag} יישאר כ־{_pct(-a)} מההכנסות')
    b, when = m1['value'], _ym(m1['fiscal_year_end'])
    if a >= 0 and b >= 0:
        return f"הרווח התפעולי{tag} {'יעלה' if b > a else 'ירד'} מכ־{_pct(a)} מההכנסות לכ־{_pct(b)} עד {when}"
    if a < 0 and b < 0:
        return f"ההפסד התפעולי{tag} {'יקטן' if b > a else 'יגדל'} מכ־{_pct(-a)} מההכנסות לכ־{_pct(-b)} עד {when}"
    if a < 0:
        return f'העסק יעבור מהפסד תפעולי{tag} של כ־{_pct(-a)} מההכנסות לרווח תפעולי של כ־{_pct(b)} עד {when}'
    return f'העסק יעבור מרווח תפעולי{tag} של כ־{_pct(a)} מההכנסות להפסד תפעולי של כ־{_pct(-b)} עד {when}'


def _roe_he(d):
    r0, r1 = d.get('roe_first'), d.get('roe_last')
    if not r0:
        return None
    a = r0['value']
    if not r1 or r1.get('fiscal_year_end') == r0.get('fiscal_year_end') or abs(r1['value'] - a) < .0005:
        return (f"התשואה על ההון העצמי תישאר כ־{_pct(a)} בשנה" + (f" עד {_ym(r1['fiscal_year_end'])}" if r1 else ''))
    b = r1['value']
    return (f"התשואה על ההון העצמי {'תעלה' if b > a else 'תרד'} מכ־{_pct(a)} בשנה שמסתיימת ב־{_ym(r0['fiscal_year_end'])} "
            f"לכ־{_pct(b)} עד {_ym(r1['fiscal_year_end'])}")


def _payout_he(d):
    p0, p1 = d.get('payout_first'), d.get('payout_last')
    if not p0:
        return None
    if not p1 or p1.get('fiscal_year_end') == p0.get('fiscal_year_end') or abs(p1['value'] - p0['value']) < .005:
        return f"כ־{_pct(p0['value'])} מהרווח יחולקו לבעלי המניות"
    verb = 'יעלה' if p1['value'] > p0['value'] else 'ירד'
    return f"החלק מהרווח שמחולק לבעלי המניות {verb} מכ־{_pct(p0['value'])} לכ־{_pct(p1['value'])}"


def _book_he(d, cur):
    b = d.get('book_value_per_share')
    if not b:
        return None
    a, z = b['today'], b['fifth_year']
    if abs(z / a - 1) < .005:
        return f"ההון העצמי למניה יישאר כ־{_money(a, cur)}"
    return (f"ההון העצמי למניה {'יגדל' if z > a else 'יקטן'} מכ־{_money(a, cur)} לכ־{_money(z, cur)} עד "
            f"{_ym(b['fifth_year_date'])}")


def _funding_he(d, cur, opening_shares, method):
    f = d.get('funding') or {}
    if method == 'residual_income':
        if f.get('first_capital_shortfall'):
            return (f"אבל ההון ירד מתחת לדרישת ההון הרגולטורי מ־{_ym(f['first_capital_shortfall'])}, והמודל אינו מניח "
                    'גיוס הון, ולכן לתרחיש הזה אין שווי')
        return 'בלי גיוס הון (מספר המניות קבוע במודל)' if f else None
    st = f.get('status')
    sh = d.get('shares') or {}
    new = f.get('new_financing_shares') or 0
    dil = (f", כ־{_pct(new / opening_shares)} מניות חדשות" if _n(new) and new > 0 and _n(opening_shares) and opening_shares > 0
           else '')
    if st == 'unfunded':
        return 'אבל חסר מימון והמודל אינו מניח מימון שלא הובטח, ולכן לתרחיש הזה אין שווי'
    if st == 'assumed_external_financing' and f.get('stub_draw_immaterial'):
        return 'בלי מימון חיצוני מהותי (משיכת חוב קטנה עד סוף השנה הנוכחית, מכוסה בתזרים החופשי שהחברה דיווחה מאז המאזן האחרון)'
    if st == 'assumed_external_financing':
        parts = []
        if _n(f.get('equity_raised')) and f['equity_raised'] > 0:
            parts.append(f"גיוס הון של כ־{_big(f['equity_raised'], cur)}{dil}")
        if _n(f.get('debt_assumed')) and f['debt_assumed'] > 0:
            parts.append(f"חוב חדש של כ־{_big(f['debt_assumed'], cur)}")
        return 'עם מימון חיצוני שהמודל מניח ועוד לא הובטח (' + ' ו'.join(parts or ['לא פורט']) + ')'
    out = 'בלי מימון חיצוני' if st in ('self_funded', 'share_settled') else None
    if st is None and f:
        out = (f"עם כ־{_pct(new / opening_shares)} מניות חדשות למימון" if dil else
               'בלי מניות חדשות למימון') + (f" (פער מימון מ־{_ym(f['first_gap'])})" if f.get('first_gap') else '')
    if _n(sh.get('change')) and sh['change'] > .005:
        out = (out + ', ' if out else '') + f"ומספר המניות יגדל בכ־{_pct(sh['change'])} בחמש השנים"
    elif st == 'share_settled' and _n(f.get('sbc_settlement_shares')) and f['sbc_settlement_shares'] > 0:
        out = (out + ', ' if out else '') + 'וחלק מתגמול העובדים ישולם במניות חדשות'
    return out


def _rate_he(d):
    """The discount rates: the explicit years' cost of equity and, when it differs, the long-run rate of the terminal value."""
    ke = (d.get('cost_of_equity') or {}).get('value')
    if not _n(ke):
        return ''
    tk, tw = (d.get('terminal_cost_of_equity') or {}).get('value'), (d.get('terminal_wacc') or {}).get('value')
    if _n(tk) and abs(tk - ke) >= .0005:
        return f'השווי מחושב בשיעור היוון של כ־{_pct(ke)} בשנה בשנים המפורשות וכ־{_pct(tk)} לטווח הארוך'
    if not _n(tk) and _n(tw) and abs(tw - ke) >= .0005:
        return (f'השווי מחושב בשיעור היוון של כ־{_pct(ke)} בשנה בשנים המפורשות, ולטווח הארוך בשיעור ההיוון של כל החברה '
                f'(כולל החוב), כ־{_pct(tw)}')
    return f'השווי מחושב בשיעור היוון של כ־{_pct(ke)} בשנה'


def _event_he(e, owner):
    """One event in Hebrew; a hypothesis says so (the owner's table note without raw evidence ids)."""
    text = e['event_he'].rstrip('.')
    if not e['hypothesis']:
        return text
    return text + ' (השערה: ' + ('אין ראיה מתועדת בתיק' if owner else e['hypothesis_he']) + ')'


def _condition(d, events, cur, opening_shares, method, has_value):
    if method not in ROUTES_READ:
        long_parts, short_parts = [], []
    elif method == 'residual_income':
        roe = _roe_he(d)
        t, g = d.get('terminal_roe'), d.get('terminal_growth')
        tail = (f" (ומשם תשואה על ההון של כ־{_pct(t['value'])} וצמיחה של כ־{_pct(g['value'])} בשנה לטווח הארוך)"
                if t and g else '')
        long_parts = [x for x in ((roe + tail) if roe else None, _payout_he(d), _book_he(d, cur)) if x]
        short_parts = [x for x in (roe, _payout_he(d)) if x]
    else:
        gr = _growth_he(d)
        t = d.get('terminal_growth')
        long_parts = [x for x in ((gr + (f" (ומשם כ־{_pct(t['value'])} בשנה לטווח הארוך)" if t else '')) if gr else None,
                                  _margin_he(d, True)) if x]
        short_parts = [x for x in (gr, _margin_he(d, False)) if x]
    body = ', ו'.join(long_parts)
    short = ', ו'.join(short_parts)
    f = _funding_he(d, cur, opening_shares, method) if method in ROUTES_READ else None
    if f:
        body = (body + '; ' if body else '') + f
    st = (d.get('funding') or {}).get('status')
    if method == 'operating' and st == 'assumed_external_financing' and not (d.get('funding') or {}).get('stub_draw_immaterial'):
        short += ('; ' if short else '') + 'ובמימון חיצוני שעוד לא הובטח'
    elif (method == 'operating' and st == 'unfunded') or (method == 'residual_income' and (d.get('funding') or {}).get('first_capital_shortfall')):
        short += ('; ' if short else '') + ('אבל חסר מימון' if method == 'operating' else 'אבל חסר הון רגולטורי')
    rate = _rate_he(d) if has_value else ''
    rate = f'; {rate}' if rate else ''
    if events:
        ev = '; '.join(_event_he(e, False) for e in events)
        if_he = f'אם {ev} — ובמודל: {body}{rate}.' if body else f'אם {ev}{rate}.'
        return {'kind': 'analyst_events', 'if_he': if_he, 'short_he': '; '.join(_event_he(e, True) for e in events)}
    kind = 'mechanical' if method in ROUTES_READ else 'route_not_read'
    if not body:
        return {'kind': kind, 'if_he': None, 'short_he': None}
    return {'kind': kind, 'if_he': f'אם {body}{rate}.', 'short_he': short or None}


def _reject_he(x):
    n = x.get('index')
    return REJECT_HE.get(x.get('code'), 'אירוע שלא נקרא').format(scenario=x.get('scenario'), n=(n + 1) if _n(n) else '')


def _events(cf, s, gaps):
    """Analyst events for scenario s, as analyst_sheet.build validated them against the FactBook (what it could not read is
    a gap here); an event without validation marks is checked against the casefile's own evidence (the FactBook is not in
    the casefile)."""
    from .analyst_sheet import evidence_id_list
    sheet = cf.get('analyst_sheet') if isinstance(cf.get('analyst_sheet'), dict) else {}
    for x in sheet.get('scenario_events_rejected') or []:
        if isinstance(x, dict) and x.get('scenario') == s:
            _gap(gaps, 'events_rejected', f"{s}[{x.get('index')}]: {x.get('reason')}", _reject_he(x))
    allev = sheet.get('scenario_events') if isinstance(sheet.get('scenario_events'), dict) else {}
    raw = allev.get(s) if isinstance(allev.get(s), list) else []
    out, index = [], None
    for i, e in enumerate(raw):
        if not isinstance(e, dict) or not isinstance(e.get('event_he'), str) or not e['event_he'].strip():
            _gap(gaps, 'events_rejected', f'{s}[{i}]: not an event with event_he', _reject_he(
                {'code': 'no_event_he' if isinstance(e, dict) else 'event_not_object', 'index': i}))
            continue
        ids, problem = evidence_id_list(e.get('evidence_ids'))
        if ids is None:
            _gap(gaps, 'events_rejected', f'{s}[{i}]: {problem}', _reject_he({'code': 'bad_evidence_ids', 'index': i}))
            continue
        if isinstance(e.get('hypothesis'), bool) and isinstance(e.get('unknown_ids'), list):
            unknown, hyp = [str(u) for u in e['unknown_ids']], e['hypothesis']
        else:
            if index is None:
                index = _evidence_index(cf)
                _gap(gaps, 'events_unvalidated', 'scenario events carry no FactBook validation: checked against the evidence '
                                                 'ids the casefile holds')
            unknown = [u for u in ids if u not in index]
            hyp = not ids or bool(unknown)
        why = None
        if hyp:
            why = ('אין לו ראיה מתועדת' if not ids else
                   f'הראיה {unknown[0]} לא נמצאה בתיק' if len(unknown) == 1 else
                   'הראיות ' + ', '.join(unknown) + ' לא נמצאו בתיק')
        out.append({'event_he': e['event_he'].strip(), 'evidence_ids': ids, 'unknown_ids': unknown, 'hypothesis': hyp,
                    'hypothesis_he': why})
    return out


def _evidence_index(cf):
    """Evidence ids the casefile itself holds (a fallback when the FactBook is not at hand)."""
    from .analyst_sheet import evidence_id_list

    def ids_of(v):
        got = evidence_id_list(v)[0]
        return set(got or [])
    ids = set()
    case = ((cf.get('val') or {}).get('case') or {})
    ids |= {s.get('id') for s in case.get('sources') or [] if isinstance(s, dict)}
    for a in list((cf.get('fc') or {}).get('assumptions') or []) + list(case.get('assumptions') or []):
        if isinstance(a, dict):
            ids |= ids_of(a.get('source_ids'))
    card = cf.get('card') if isinstance(cf.get('card'), dict) else {}
    for u in card.get('understanding') or []:
        ids |= ids_of((u or {}).get('evidence_ids')) if isinstance(u, dict) else set()
    for dim in (card.get('dimensions') or {}).values() if isinstance(card.get('dimensions'), dict) else []:
        ids |= ids_of((dim or {}).get('evidence_ids')) if isinstance(dim, dict) else set()
    for r in (cf.get('rk') or {}).get('register') or [] if isinstance(cf.get('rk'), dict) else []:
        ids |= ids_of(r.get('evidence_ids')) if isinstance(r, dict) else set()
    return {str(i) for i in ids if i}


def _assumptions(cf, s):
    spec = _scenario_spec(cf, s)
    case_sc = next((x for x in ((cf.get('val') or {}).get('case') or {}).get('scenarios') or []
                    if isinstance(x, dict) and x.get('name') == s), {})
    ids = list(dict.fromkeys(list(spec.get('assumption_ids') or []) + list(case_sc.get('assumption_ids') or [])))
    reviews = ((((cf.get('val') or {}).get('case') or {}).get('underwriting') or {}).get('scenario_reviews') or {}).get(s) or {}
    status = {}
    for a in list((cf.get('fc') or {}).get('assumptions') or []) + list(((cf.get('val') or {}).get('case') or {}).get('assumptions') or []):
        if isinstance(a, dict) and a.get('id') and a['id'] not in status:
            status[a['id']] = a.get('review_status')
    allids = ids + list(spec.get('period_assumption_ids') or []) + list((spec.get('terminal') or {}).get('assumption_ids') or [])
    allids += [i for r in reviews.values() if isinstance(r, dict) for i in r.get('assumption_ids') or []]
    allids = list(dict.fromkeys(allids))
    return {'scenario': ids, 'periods': list(spec.get('period_assumption_ids') or []),
            'terminal': list((spec.get('terminal') or {}).get('assumption_ids') or []),
            'scenario_reviews': {k: list((v or {}).get('assumption_ids') or []) for k, v in sorted(reviews.items())},
            'review_status': {i: status.get(i) for i in allids if i in status}}


def _overrides(cf, s):
    return [{k: o.get(k) for k in ('id', 'path', 'value', 'reason', 'evidence_ids')}
            for o in (cf.get('fc') or {}).get('overrides') or [] if isinstance(o, dict) and o.get('scenario') in (s, None, 'all')]


# ------------------------------------------------------------------ the rows

def _row_operating(cf, s, p, path, drv, src, gaps, years, anchor):
    if p['_full']:
        drv['revenue'], src['revenue'] = p.get('revenue'), 'executed'
        prev = next((x for x in years if x.get('end') == p.get('start')), None)
        if prev and prev['_full'] and _n(prev.get('revenue')):
            base, how = prev['revenue'], 'executed'
        elif anchor.get('end') == p.get('start') and _n(anchor.get('revenue')):
            base, how = anchor['revenue'], 'executed over fc.anchors.current_fiscal_year'
        else:
            base, how = None, None
        if _n(base) and base > 0 and _n(p.get('revenue')):
            drv['revenue_growth'], src['revenue_growth'] = _r(p['revenue'] / base - 1, 6), how
        else:
            _gap(gaps, 'growth', f"{s}: no growth for the fiscal year ending {p.get('end')} (no previous full fiscal year or "
                                 'current-year anchor)', f"צמיחה לשנה שמסתיימת ב־{_ym(p.get('end'))}")
        if path and _n(path.get('growth')):
            drv['revenue_growth_assumption'], src['revenue_growth_assumption'] = _r(path['growth'], 6), 'fc.paths'
        if _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0:
            drv['operating_margin_after_sbc'], src['operating_margin_after_sbc'] = _r(p['ebit'] / p['revenue'], 6), 'executed'
        drv['eps'], drv['fcf_per_share'] = _r(_eps(p), 4), _r(_fcf_ps(p), 4)
        return
    # the current fiscal year: only its remainder is inside the forecast
    if anchor.get('end') == p.get('end') and _n(anchor.get('revenue')):
        drv['revenue'], src['revenue'] = anchor['revenue'], 'fc.anchors.current_fiscal_year'
        if _n(anchor.get('growth')):
            drv['revenue_growth'], src['revenue_growth'] = _r(anchor['growth'], 6), 'fc.anchors.current_fiscal_year'
    else:
        _gap(gaps, 'current_year_revenue', f"{s}: the fiscal year ending {p.get('end')} is only partly in the forecast and "
                                           'the casefile holds no full-year anchor (fc.anchors.current_fiscal_year)',
             f"הכנסות של כל השנה הנוכחית (עד {_ym(p.get('end'))})")
    if path and _n(path.get('operating_margin')):
        drv['operating_margin_after_sbc'], src['operating_margin_after_sbc'] = _r(path['operating_margin'], 6), 'fc.paths'
    elif _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0:
        drv['operating_margin_after_sbc'] = _r(p['ebit'] / p['revenue'], 6)
        src['operating_margin_after_sbc'] = 'executed partial period'


def _row_ri(p, path, drv, src):
    if p['_full']:
        drv['net_income'], src['net_income'] = p.get('net_income'), 'executed'
        if _n(_roe_of(p)):
            drv['roe'], src['roe'] = _r(_roe_of(p), 6), 'executed'
        drv['eps'] = _r(_eps(p, 'residual_income'), 4)
    elif path and _n(path.get('roe')):
        drv['roe'], src['roe'] = _r(path['roe'], 6), 'fc.paths'
    elif _n(_roe_of(p)):
        drv['roe'], src['roe'] = _r(_roe_of(p), 6), 'executed partial period'


def _row(cf, s, r, today, years, paths, cur, gaps, shares_at, book_at, method):
    d = str(r['date'])
    value = r.get(s)
    dist = (r.get('distributions') or {}).get(s) if isinstance(r.get('distributions'), dict) else None
    p = _period_for(years, d)
    ri = method == 'residual_income'
    keys = (('roe', 'net_income', 'eps', 'book_value_per_share', 'diluted_shares') if ri else
            ('revenue', 'revenue_growth', 'revenue_growth_assumption', 'operating_margin_after_sbc', 'eps', 'fcf_per_share',
             'diluted_shares'))
    fy, drv, src = None, dict.fromkeys(keys), {}
    anchor = ((cf.get('fc') or {}).get('anchors') or {}).get('current_fiscal_year') or {}
    if p:
        fy = {'start': p.get('start'), 'end': p.get('end'), 'full_year': p['_full']}
        path = next((x for x in paths if x.get('end') == p.get('end')), None)
        if ri:
            _row_ri(p, path, drv, src)
        elif method == 'operating':
            _row_operating(cf, s, p, path, drv, src, gaps, years, anchor)
    drv['diluted_shares'] = shares_at.get(d)
    if ri and _n(book_at.get(d)) and _n(shares_at.get(d)) and shares_at[d] > 0:
        drv['book_value_per_share'], src['book_value_per_share'] = _r(book_at[d] / shares_at[d], 4), 'executed'
    nxt = _next_full(years, d)
    raw_eps = _eps(nxt, method) if nxt else None
    pe = {'value': None, 'eps_next_fiscal_year': _r(raw_eps, 4), 'next_fiscal_year_start': (nxt or {}).get('start'),
          'next_fiscal_year_end': (nxt or {}).get('end'), 'reason': None}
    lacks = 'net income or shares' if ri else 'EBIT, interest, cash taxes or shares'
    if method not in ROUTES_READ:
        pe['reason'] = f'the {method} route holds no earnings per share'
    elif not nxt:
        pe['reason'] = 'no full fiscal year after this date in the executed forecast'
        _gap(gaps, 'next_year_eps', f'{s}: no full fiscal year after {d} in the executed forecast (implied P/E not computed)',
             f'שנת כספים מלאה אחרי {_ym(d)} (למכפיל)')
    elif raw_eps is None:
        pe['reason'] = f'the next fiscal year lacks {lacks}'
        _gap(gaps, 'next_year_eps', f"{s}: the fiscal year ending {nxt.get('end')} lacks {lacks}",
             f"אומדן רווח לשנה שמסתיימת ב־{_ym(nxt.get('end'))} (למכפיל)")
    elif raw_eps <= 0:
        pe['reason'] = 'next fiscal year earnings are zero or negative: a P/E is not meaningful'
    else:
        pe['value'] = _r(value / raw_eps, 2)
    when = 'היום' if d == today else f'ב־{_ym(d)}'
    parts = []
    if ri:
        if _n(drv['roe']):
            parts.append(f"תשואה על ההון כ־{_pct(drv['roe'])}")
    else:
        if _n(drv.get('revenue')):
            g = drv['revenue_growth']
            parts.append(f"הכנסות כ־{_big(drv['revenue'], cur)}" + (f" ({'+' if g >= 0 else '−'}{_pct(abs(g))})" if _n(g) else ''))
        m = drv.get('operating_margin_after_sbc')
        if _n(m):
            parts.append(f'רווח תפעולי {_pct(m)}' if m >= 0 else f'הפסד תפעולי {_pct(-m)}')
    if _n(drv['eps']):
        parts.append(f"{EPS_HE[ri]} כ־{_money(drv['eps'], cur)}")
    if ri and _n(drv['book_value_per_share']):
        parts.append(f"הון עצמי למניה כ־{_money(drv['book_value_per_share'], cur)}")
    period = ('' if not fy else
              ('סוף השנה הנוכחית' if fy['end'] == d else f"השנה הנוכחית עד {_ym(fy['end'])}") if not fy['full_year'] else
              f"שנת הכספים עד {_ym(fy['end'])}" if fy['end'] != d else '')
    text = ', '.join(parts)
    if _n(value):
        text += (' → ' if text else '') + f'שווי כ־{_money(value, cur)}'
        if _n(pe['value']):
            text += f" (פי {pe['value']:.0f} על {EPS_NEXT_HE[ri]} של השנה הבאה)"
    if _n(dist) and _n(value) and dist > .005 * abs(value):
        text += f'; עד אז יחולקו כ־{_money(dist, cur)} למניה'
    return {'date': d, 'when_he': when + (f' ({period})' if period else ''), 'value': value, 'cumulative_distributions': dist,
            'fiscal_year': fy, 'drivers': drv, 'driver_sources': dict(sorted(src.items())), 'implied_pe': pe, 'then_he': text}


PRICE_ROW_KEYS = ('year', 'date', 'price', 'annual_return', 'status', 'why', 'multiple', 'eps', 'revenue', 'operating_margin',
                  'weighted_shares', 'fiscal_year_end', 'events', 'eps_drivers', 'eps_driver_gaps', 'evidence_ids',
                  'multiple_evidence_ids', 'reason_he')


def _price_rows(cf, s):
    """The company's own yearly prices for scenario s (casefile company_path: next-fiscal-year EPS x an evidenced multiple),
    years 1-5 as the owner's table shows them, each with its stored reason: the EPS drivers (revenue, margin, shares and the
    levers with their source tiers and evidence), the multiple and why (justification, tier, named competitors), the events and
    the evidence ids. None when the casefile has no computed company path."""
    cp = cf.get('company_path') if isinstance(cf.get('company_path'), dict) else {}
    if cp.get('status') != 'computed':
        return None
    x = (cp.get('scenarios') or {}).get(s)
    if not isinstance(x, dict):
        return []
    return [{**{k: r.get(k) for k in PRICE_ROW_KEYS}, 'justification': x.get('justification'), 'source_tier': x.get('source_tier'),
             'competitors': list(x.get('competitors') or []), 'multiple_reason_he': x.get('reason_he')}
            for r in x.get('rows') or [] if isinstance(r, dict) and r.get('year')]


def _scenario(cf, s, ctx):
    gaps = []
    method = ctx['method']
    sc = {'name': s, 'label_he': LABEL_HE[s], 'status': 'not_available', 'condition': None, 'note_he': None, 'rows': [],
          'assumption_ids': _assumptions(cf, s), 'analyst_overrides': _overrides(cf, s), 'gaps': gaps,
          'thesis': _scenario_spec(cf, s).get('thesis'), 'why_no_values_he': None, 'price_rows': None}
    if not ctx['shown']:
        sc['why_no_values_he'] = ctx['why_none']
        return sc
    sc['price_rows'] = _price_rows(cf, s)
    events = _events(cf, s, gaps)
    raw = ctx['raw'][s]
    years = ctx['years'][s]
    if not raw:
        _gap(gaps, 'no_forecast_rows', f'{s}: val.result.scenarios[{s}].forecast is absent')
    if method not in ROUTES_READ:
        _gap(gaps, 'route_drivers', f'{s}: the {method} route is not broken into business drivers here; its values are the '
                                    'executed snapshots')
    elif not _paths(cf.get('fc') or {}, s):
        _gap(gaps, 'no_forecast_paths', f'fc.paths[{s}] absent: drivers read from the executed forecast rows')
    shares_at = _shares_at(raw)
    opening = shares_at.get(ctx['today'])
    has_value = any(_n(r.get(s)) for r in ctx['all_rows'])
    build_d = _drivers_ri if method == 'residual_income' else _drivers if method == 'operating' else None
    d = build_d(cf, s, years, raw, gaps, opening, ctx['last_date']) if build_d else {}
    if method not in ROUTES_READ:
        _rates(cf, s, d, gaps)
    cond = _condition(d, events, ctx['cur'], opening, method, has_value)
    sc['condition'] = {'kind': cond['kind'], 'if_he': cond['if_he'], 'events': events, 'drivers': d}
    if cond['short_he']:
        sc['note_he'] = f"{LABEL_HE[s]} = אם {cond['short_he']}."
    if not has_value:
        st = (ctx['res'].get(s) or {}).get('status')
        _gap(gaps, 'no_values', f'{s}: the executed valuation holds no value for this scenario' + (f' (status {st})' if st else ''))
        sc['why_no_values_he'] = ('לתרחיש הזה אין שווי בהערכה שבוצעה' + (' (חסר מימון שהמודל לא מניח)' if st == 'funding_blocked' else ''))
        return sc
    if not ctx['rows']:
        _gap(gaps, 'no_yearly_table', f'{s}: the owner\'s table shows no yearly table (fewer than three dates where every '
                                      'scenario has a value): no dated values are listed')
    paths = _paths(cf.get('fc') or {}, s)
    book_at = _book_at(raw)
    for r in ctx['rows']:
        row = _row(cf, s, r, ctx['today'], years, paths, ctx['cur'], gaps, shares_at, book_at, method)
        row['why_he'] = f"אם {cond['short_he']}, אז {row['when_he']}: {row['then_he']}." if cond['short_he'] else \
            f"{row['when_he']}: {row['then_he']}."
        sc['rows'].append(row)
    sc['status'] = 'computed' if not gaps and sc['rows'] else 'partial'
    return sc


def _distinct(scen, all_rows):
    """Never the same condition for different values: two valued scenarios with one condition text get a gap each."""
    first = {s: next((r.get(s) for r in all_rows if _n(r.get(s))), None) for s in SCENARIOS}
    for x in scen:
        cx, vx = (x.get('condition') or {}).get('if_he'), first.get(x['name'])
        same = [y['name'] for y in scen if y is not x and cx and (y.get('condition') or {}).get('if_he') == cx
                and _n(vx) and _n(first.get(y['name'])) and first[y['name']] != vx]
        if same:
            _gap(x['gaps'], 'condition_not_distinct', f"{x['name']}: the same condition as {', '.join(same)} for a different value")
            if x['status'] == 'computed':
                x['status'] = 'partial'


def build(casefile):
    """-> the reasons behind every scenario price of an executed casefile (module docstring). Reads, never revalues."""
    from .render import value_display
    cf = {} if casefile is None else casefile
    _check(cf)
    dc = cf.get('dc') or {}
    company = dc.get('company') or cf.get('company') or {}
    cur = (dc.get('price') or {}).get('currency') or company.get('currency') or ''
    disp = value_display(cf)
    gaps = []
    src, all_rows, rows = _yearly(cf, gaps)
    res = ((cf.get('val') or {}).get('result') or {})
    res_sc = {x.get('name'): x for x in res.get('scenarios') or [] if isinstance(x, dict)}
    method = (((cf.get('val') or {}).get('case') or {}).get('method') or (cf.get('fc') or {}).get('method') or 'operating')
    five = ((dc.get('valuation_view') or {}).get('five_year') or {}).get('date') or (all_rows[-1]['date'] if all_rows else None)
    interim = _interim(cf, five)
    today = all_rows[0]['date'] if all_rows else None
    last_fy = ((cf.get('fc') or {}).get('anchors') or {}).get('last_fiscal_year_end')
    raw_by = {s: [p for p in (res_sc.get(s) or {}).get('forecast') or [] if isinstance(p, dict)] for s in SCENARIOS}
    years_by = {s: _fiscal_years(raw_by[s], interim, today, last_fy) for s in SCENARIOS}
    shown = bool(disp['show']) and bool(all_rows)
    why_none = None if shown else (disp['reason_he'] or 'אין בתיק ערכים מתוארכים של ההערכה')
    if shown and not rows:
        _gap(gaps, 'no_yearly_table', "the owner's table shows no yearly table (fewer than three dates where every scenario "
                                      'has a value): no dated values are listed, only the conditions')
    sheet = cf.get('analyst_sheet') if isinstance(cf.get('analyst_sheet'), dict) else {}
    if shown:
        for x in sheet.get('scenario_events_rejected') or []:
            if isinstance(x, dict) and x.get('scenario') not in SCENARIOS:
                _gap(gaps, 'events_rejected', f"{x.get('scenario')}: {x.get('reason')}", _reject_he(x))
    ctx = {'shown': shown, 'why_none': why_none, 'raw': raw_by, 'years': years_by, 'today': today,
           'last_date': all_rows[-1]['date'] if all_rows else None,
           'all_rows': all_rows, 'rows': rows, 'cur': cur, 'method': method, 'res': res_sc}
    scen = [_scenario(cf, s, ctx) for s in SCENARIOS]
    _distinct(scen, all_rows)
    status = ('not_available' if not shown else
              'computed' if not gaps and all(s['status'] == 'computed' for s in scen) else 'partial')
    v = dc.get('verdict') or {}
    yr = (dc.get('expected_return') or {}).get('yearly_at_model_cost_of_equity') or {}
    return {'reasons_version': 1, 'status': status,
            'company': {'ticker': company.get('ticker'), 'name': company.get('name')},
            'as_of': cf.get('as_of') or dc.get('as_of'), 'currency': cur, 'method': method,
            'values_shown': shown, 'yearly_table': bool(shown and rows), 'draft': bool(v.get('price_claim_withheld')) if shown else None,
            'draft_reason_he': disp.get('reason_he') if shown and disp.get('draft') else None,
            'why_no_values_he': why_none,
            'source': {'values': src if shown else None, 'rate': yr.get('rate') or 'model cost of equity',
                       'model_cost_of_equity': yr.get('model_cost_of_equity',
                                                      (dc.get('valuation_view') or {}).get('model_cost_of_equity'))},
            'scenarios': scen, 'gaps': gaps, 'definitions': DEFINITIONS, 'rounding': ROUNDING,
            'meaning': "the reasons explain the model's conditional values: what has to happen for each scenario price and the "
                       'business of that year; not forecasts, probabilities or price targets; values are read from the executed '
                       'valuation, never recomputed; earnings per share is the model estimate, not reported earnings'}


def _hyp_he(events):
    hyp = [e for e in events if e.get('hypothesis')]
    n, m = len(hyp), len(events)
    if not n:
        return None
    if m == 1:
        return 'האירוע בתנאי הוא השערה בלי ראיה מתועדת בתיק.'
    if n == 1:
        return f'אחד מ־{m} האירועים בתנאי הוא השערה בלי ראיה מתועדת בתיק.'
    if n == m:
        return f'כל {m} האירועים בתנאי הם השערות בלי ראיה מתועדת בתיק.'
    return f'{n} מתוך {m} האירועים בתנאי הם השערות בלי ראיה מתועדת בתיק.'


LEVER_LABEL_HE = {'growth_anchor': 'צמיחת ההכנסות בשנים הקרובות', 'growth_half_life': 'קצב האטת הצמיחה',
                  'mature_margin': 'הרווחיות התפעולית בבגרות', 'sbc': 'תגמול במניות', 'sales_to_capital': 'השקעה לכל דולר מכירות',
                  'tax_rate': 'שיעור המס'}


def _price_line(p, cur):
    """One company-path price behind the scenes: the price as the owner's table shows it, then its whole stored reason."""
    when = f"שנה {p['year']} ({str(p.get('date'))[:7].replace('-', '/')})"
    if p.get('status') != 'priced' or not _n(p.get('price')):
        return f"- {when}: {p.get('reason_he') or 'אין מחיר'}"
    return f"- {when}: **{_money(p['price'], cur)}** — {p.get('reason_he')}"


def _drivers_line(rows):
    """The levers behind the scenario's EPS (the same on every row of the scenario) and every evidence id, once."""
    from .company_path import TIER_HE
    line, p = '', next((r for r in rows if r.get('eps_drivers')), rows[0] if rows else {})
    drv = [d for d in p.get('eps_drivers') or [] if isinstance(d, dict)]
    if drv:
        line += '- מה קובע את הרווח למניה בכל השנים: ' + '; '.join(
            LEVER_LABEL_HE.get(d['lever'], d['lever'])
            + (f" {d['value']:.1%}" if _n(d.get('value')) and abs(d['value']) < 1.5 else '')
            + f" ({TIER_HE.get(d.get('source_tier'), 'ברירת מחדל של המנוע')}"
            + (f": {', '.join(d['evidence_ids'])}" if d.get('evidence_ids') else '')
            + (', פער: כלל של המנוע ולא נתון של החברה' if d.get('default_class') else '') + ')' for d in drv) + '.'
    ids = list(dict.fromkeys(i for r in rows for i in r.get('evidence_ids') or []))
    if ids:
        line += (' ' if line else '- ') + f"ראיות (מכפיל, אירועים ונתוני הרווח): {', '.join(ids)}."
    return line


def render_he(reasons):
    """out/scenarios_he.md: one short section per scenario (its condition and one line per dated price), plus what is
    missing; behind the scenes, under the owner's table."""
    r = reasons or {}
    c = r.get('company') or {}
    title = c.get('name') or c.get('ticker') or 'החברה'
    out = [f"**{title}{' (' + c['ticker'] + ')' if c.get('ticker') and c.get('name') else ''} — מה צריך לקרות כדי שכל מחיר "
           f"יתממש, {r.get('as_of') or ''}**", '']
    if r.get('values_shown') and r.get('draft_reason_he'):
        from .render import DRAFT_BANNER_HE           # the owner's table carries the same banner (render.value_display 'draft')
        out += [f"> **{DRAFT_BANNER_HE}.** הסיבה: {r['draft_reason_he']}. המחירים כאן הם החישוב של המנוע, לא מסקנה, ואין בהם "
                'המלצה לקנות או למכור.', '']
    if r.get('status') == 'failed':
        return '\n'.join(out + ['לא ניתן היה להפיק את הסיבות לתרחישים: ' + str(r.get('error') or '') + '.', ''])
    if not r.get('values_shown'):
        return '\n'.join(out + [f"אין שווי להציג: {r.get('why_no_values_he') or 'הערכת השווי לא הושלמה'}. "
                                'לכן אין כאן מחירים לפי תרחיש.', ''])
    meaning = ('שווי מותנה: מה המניה שווה אם התנאי של התרחיש מתקיים. זו לא תחזית, לא הסתברות ולא יעד מחיר; הערכים נקראו '
               'מההערכה שבוצעה ולא חושבו מחדש.')
    if not r.get('yearly_table'):
        intro = ('בטבלת השווי אין טבלה שנתית (אין שלושה מועדים שבהם לכל התרחישים יש שווי), ולכן כאן רק התנאי של כל תרחיש, '
                 'בלי מחירים לפי שנה. התנאים מסבירים את השווי המותנה של המודל; הם לא תחזית, לא הסתברות ולא יעד מחיר.')
    elif (r.get('source') or {}).get('values') == 'val.result.annual_values':
        intro = 'כל מחיר כאן (הערכים המתוארכים של ההערכה; בתיק אין טבלה שנתית) הוא ' + meaning
    else:
        intro = 'כל מחיר בטבלה השנתית הוא ' + meaning
    priced = any(s.get('price_rows') for s in r.get('scenarios') or [])
    if priced:
        intro = ('כל מחיר בטבלה השנתית "מחיר המניה בכל שנה" = הרווח למניה של שנת הכספים הבאה × מכפיל מנומק, אם התנאי של התרחיש '
                 'מתקיים: מחיר מותנה, לא תחזית, לא הסתברות ולא יעד מחיר. מתחת לכל מחיר: הרווח למניה ומה שקובע אותו, המכפיל ולמה, '
                 'מקור כל נתון ומזהי הראיות. אחריהם, בכל תרחיש, השווי במודל תזרים המזומנים (שממנו השווי ההוגן); הערכים נקראו '
                 'מההערכה שבוצעה ולא חושבו מחדש.')
    out.append(intro + (' זו טיוטה שעוד לא נבדקה.' if r.get('draft') else ''))
    if any(_n((row.get('drivers') or {}).get('eps')) for s in r.get('scenarios') or [] for row in s.get('rows') or []):
        out.append(EPS_BASIS_HE.get(r.get('method'), EPS_BASIS_HE['operating']))
    top_rej = [g['he'] for g in r.get('gaps') or [] if g.get('code') == 'events_rejected']
    if top_rej:
        out.append('אירועים שהאנליסט סיפק ולא נקראו: ' + '; '.join(top_rej) + '.')
    for s in r.get('scenarios') or []:
        out += ['', f"### {s.get('label_he')}", '']
        cond = s.get('condition') or {}
        gaps = s.get('gaps') or []
        rejected = [g['he'] for g in gaps if g.get('code') == 'events_rejected']
        if cond.get('if_he'):
            label = ('**התנאי (אירועים מהאנליסט):** ' if cond.get('kind') == 'analyst_events' else
                     '**התנאי (מנגנון המודל; אירועים שהאנליסט סיפק לא נקראו, ראו למטה):** ' if rejected else
                     '**התנאי (מנגנון המודל, בלי אירועים מתועדים מהאנליסט):** ')
            out.append(label + cond['if_he'])
        if s.get('why_no_values_he'):
            out.append(s['why_no_values_he'] + '.')
        if s.get('price_rows'):
            out += ['', '**מחיר המניה בכל שנה (רווח למניה × מכפיל):**']
            out += [_price_line(p, r.get('currency')) for p in s['price_rows']]
            dl = _drivers_line(s['price_rows'])
            if dl:
                out.append(dl)
            if s.get('rows'):
                out += ['', '**שווי במודל תזרים המזומנים:**']
        for row in s.get('rows') or []:
            out.append(f"- {row['when_he'].replace('ב־', '', 1)}: {row['then_he']}")
        hyp = _hyp_he(cond.get('events') or [])
        if hyp:
            out.append('- ' + hyp)
        if rejected:
            out.append('- אירועים שהאנליסט סיפק ולא נקראו: ' + '; '.join(rejected) + '.')
        miss = list(dict.fromkeys(g.get('he') or GAP_HE.get(g['code'], g['code']) for g in gaps
                                  if g.get('code') not in ('events_rejected', 'no_yearly_table')
                                  and not (g.get('code') == 'no_values' and s.get('why_no_values_he'))))
        if miss:
            out.append('- חסר בתיק: ' + '; '.join(miss) + '.')
    return '\n'.join(out + [''])
