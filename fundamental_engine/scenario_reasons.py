"""Scenario reasons: behind every bear/base/bull price (דובי/בסיסי/שורי), the condition that has to hold and the business
of that year, read from the executed casefile.

The owner (25.10.2026): "I want bullish, base and bearish. And behind the scenes, of course, every price has a reason: if
something happens, then this price." build(casefile) answers that for each scenario and each dated row of the owner's
yearly table:

- condition: one plain-Hebrew sentence of what has to happen (if_he). Events come ONLY from the analyst
  (inputs.analyst_sheet.scenario_events = {bear|base|bull: [{event_he, evidence_ids}]}, validated against the FactBook by
  analyst_sheet.build and stored in casefile['analyst_sheet']['scenario_events']); an event without known evidence ids is
  kept and marked a hypothesis, in Hebrew too. Without events the condition is the model's own mechanics in plain words
  (revenue growth first year -> last explicit year and the long-run rate, operating margin after SBC start -> end,
  external funding and dilution, the cost of equity), never an invented story.
- rows: today and each dated row the owner's table shows (dc.expected_return.yearly_at_model_cost_of_equity; else
  val.result.annual_values through the fifth anniversary), each with the executed value (exactly the number in the
  casefile), the drivers of the fiscal year that holds that date (revenue, growth, operating margin after SBC, earnings and
  FCF per share, diluted shares, cumulative distributions), an implied P/E on the next full fiscal year's earnings when they
  are positive, and why_he: 'אם <condition>, אז ב־<date>: הכנסות …, רווח תפעולי …% → שווי …'.
- assumption ids (the scenario's own, its period and terminal ids, the underwriting scenario reviews), the analyst's
  validated driver overrides for that scenario (context, never events) and gaps: what the casefile does not hold, said
  instead of filled in.

Nothing is revalued and no value changes: values are copied from the executed valuation, drivers are read from the
executed forecast rows (val.result.scenarios[].forecast) and the forecast paths (fc.paths, fc.anchors, fc.funding,
fc.cost_of_equity); the only arithmetic is per-share division, ratios and sums of executed flows. A valuation the owner's
table does not show (render.value_display: a non-price verdict, demo inputs, an unreleased forensic or governance flag, a
rejected assumption, a 'hold' material issue) gets no values here either: the reasons say why there is no value.
These reasons explain the model's conditional values; they are not forecasts, probabilities or price targets.
"""
from datetime import date

SCENARIOS = ('bear', 'base', 'bull')
LABEL_HE = {'bear': 'דובי', 'base': 'בסיסי', 'bull': 'שורי'}
FUNDING_STATUSES = ('self_funded', 'share_settled', 'assumed_external_financing', 'unfunded')
ROUNDING = ('value and cumulative_distributions: the executed numbers, unrounded; revenue and diluted shares: executed, '
            'unrounded; growth and margins: 6 decimals; eps and fcf_per_share: 4 decimals; implied P/E: 2 decimals; Hebrew '
            'text: per-share values to 3 significant figures (render._ps), percentages to one decimal, revenue in '
            'billions/millions')
DEFINITIONS = {
    'fiscal_year': 'the executed forecast period that holds the row date (a period ending on the date, else the one running '
                   'through it); a valuation date inside a fiscal year (the fifth anniversary) is merged back into its fiscal '
                   'year (monitor._merge_interim); a first partial period is the current fiscal year',
    'revenue': 'executed forecast revenue of that fiscal year; for the current fiscal year, only partly inside the forecast, '
               'the forecast anchor fc.anchors.current_fiscal_year (the model full-year revenue)',
    'revenue_growth': 'fc.paths[scenario] growth of that fiscal year; without forecast paths, executed revenue over the '
                      'previous full fiscal year (or the current-year anchor) minus 1',
    'operating_margin_after_sbc': 'executed EBIT after SBC and D&A / revenue of that fiscal year (for the current fiscal '
                                  'year, fc.paths when held, else the executed partial period)',
    'eps': '(EBIT after SBC - interest - cash taxes) / average model shares ((open + end) / 2), full fiscal years only: '
           'an operating-earnings proxy (investment income excluded, as growth.multiples_view); model shares carry no '
           'buybacks (excess cash is paid out as distributions)',
    'fcf_per_share': '(CFO - maintenance capex - growth capex) / average model shares, full fiscal years only; after SBC '
                     'when the scenario charges SBC as a cash-equivalent cost (sbc_policy)',
    'diluted_shares': 'model shares at the row date (opening shares for today, period-end shares otherwise)',
    'cumulative_distributions': 'distributions per share (dividends / buybacks) paid by the row date, from the same yearly row',
    'implied_pe': 'row value / eps of the next full fiscal year (the first full-year period starting on or after the row '
                  'date; a partial period after the date is skipped); null with the reason when that year is missing or its '
                  'eps is not positive',
}
GAP_HE = {'no_values': 'אין ערכים לתרחיש הזה בהערכה שבוצעה',
          'no_forecast_rows': 'אין בתיק שורות תחזית שבוצעו לתרחיש',
          'no_forecast_paths': 'אין בתיק את מסלולי התחזית (צמיחה ורווחיות לפי שנה); הנתונים נקראו משורות ההערכה',
          'current_year_revenue': 'אין בתיק הכנסות של כל השנה הנוכחית',
          'growth': 'אין בתיק צמיחה לשנה זו',
          'next_year_eps': 'אין בתיק שנת כספים מלאה אחרי התאריך',
          'cost_of_equity': 'אין בתיק שיעור היוון לתרחיש',
          'funding': 'אין בתיק פירוט מימון לתרחיש',
          'five_year_date': 'אין בתיק תאריך של יום השנה החמישי',
          'yearly_rows': 'אין בתיק טבלה שנתית; נקראו הערכים המתוארכים של ההערכה',
          'events_unvalidated': 'האירועים נבדקו רק מול הראיות שבתיק (בלי מאגר הנתונים המלא)'}


def _n(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r(v, d):
    return round(v, d) if _n(v) else None


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


def _gap(gaps, code, detail):
    g = {'code': code, 'detail': detail}
    if g not in gaps:
        gaps.append(g)


# ------------------------------------------------------------------ reading the casefile

def _yearly(cf, gaps):
    """The dated rows the owner's table shows: (source name, rows [{date, bear, base, bull, distributions}])."""
    dc = cf.get('dc') or {}
    y = (dc.get('expected_return') or {}).get('yearly_at_model_cost_of_equity') or {}
    if y.get('rows'):
        return 'dc.expected_return.yearly_at_model_cost_of_equity', y['rows']
    res = ((cf.get('val') or {}).get('result') or {})
    av = res.get('annual_values') or []
    if not av:
        return None, []
    _gap(gaps, 'yearly_rows', 'dc.expected_return.yearly_at_model_cost_of_equity absent: dated values read from '
                              'val.result.annual_values')
    five = ((dc.get('valuation_view') or {}).get('five_year') or {}).get('date')
    if not five:
        _gap(gaps, 'five_year_date', 'dc.valuation_view.five_year.date absent: every dated value of the executed path is listed')
    paid = {sc.get('name'): {t.get('date'): t.get('cumulative_dividend_per_share') for t in sc.get('timeline') or []}
            for sc in res.get('scenarios') or [] if isinstance(sc, dict)}
    rows = [{'date': r['date'], **{s: r.get(s) for s in SCENARIOS},
             'distributions': {s: (paid.get(s) or {}).get(r['date']) for s in SCENARIOS}}
            for r in av if r.get('date') and (not five or r['date'] <= five)]
    return 'val.result.annual_values', rows


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


def _fiscal_years(rows, interim):
    """Executed forecast periods with interim splits merged back into fiscal years; the merged year keeps the opening share
    count of its first part."""
    from .monitor import _merge_interim
    opening = {r.get('start'): r.get('shares_open') for r in rows or []}
    out = []
    for p in _merge_interim(rows, interim):
        p = dict(p)
        if p.get('merged_interim'):
            p['shares_open'] = opening.get(p.get('start'), p.get('shares_open'))
        y = _years(p.get('start'), p.get('end'))
        p['_full'] = y is not None and .9 <= y <= 1.01
        out.append(p)
    return out


def _per_share(p, flow):
    so, se = p.get('shares_open'), p.get('shares')
    if not p.get('_full') or not _n(flow) or not _n(so) or not _n(se) or so + se <= 0:
        return None
    return flow / ((so + se) / 2)


def _eps(p):
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
    """Model shares on each forecast date (not a merged fiscal year): the opening count on the first start, the period-end
    count on every end."""
    out = {}
    if raw and _n(raw[0].get('shares_open')):
        out[raw[0].get('start')] = raw[0]['shares_open']
    for p in raw:
        if _n(p.get('shares')):
            out[p.get('end')] = p['shares']
    return out


def _next_full(years, d):
    return next((p for p in years if p.get('_full') and str(p.get('start')) >= d), None)


# ------------------------------------------------------------------ the condition

def _paths(fc, s):
    return [p for p in ((fc.get('paths') or {}).get(s) or []) if isinstance(p, dict)]


def _scenario_spec(cf, s):
    for sc in ((cf.get('fc') or {}).get('spec') or {}).get('scenarios') or []:
        if sc.get('name') == s:
            return sc
    for sc in ((cf.get('val') or {}).get('case') or {}).get('scenarios') or []:
        if sc.get('name') == s:
            return sc
    return {}


def _drivers(cf, s, years, gaps, opening_shares, five_shares):
    """The mechanical drivers of the scenario as the casefile holds them, each with its source."""
    fc = cf.get('fc') or {}
    paths = _paths(fc, s)
    d = {}
    grow = [p for p in paths if _n(p.get('growth'))]
    if grow:
        d['growth_first'] = {'value': _r(grow[0]['growth'], 6), 'fiscal_year_end': grow[0].get('end'), 'source': 'fc.paths'}
        d['growth_last'] = {'value': _r(grow[-1]['growth'], 6), 'fiscal_year_end': grow[-1].get('end'), 'source': 'fc.paths'}
    else:
        full = [p for p in years if p.get('_full') and _n(p.get('revenue'))]
        pairs = [(a, b) for a, b in zip(full, full[1:]) if a.get('end') == b.get('start') and a['revenue'] > 0]
        if pairs:
            a, b = pairs[0]
            d['growth_first'] = {'value': _r(b['revenue'] / a['revenue'] - 1, 6), 'fiscal_year_end': b.get('end'),
                                 'source': 'executed forecast rows'}
            a, b = pairs[-1]
            d['growth_last'] = {'value': _r(b['revenue'] / a['revenue'] - 1, 6), 'fiscal_year_end': b.get('end'),
                                'source': 'executed forecast rows'}
    marg = [p for p in paths if _n(p.get('operating_margin'))]
    if marg:
        d['operating_margin_first'] = {'value': _r(marg[0]['operating_margin'], 6), 'fiscal_year_end': marg[0].get('end'),
                                       'source': 'fc.paths'}
        d['operating_margin_last'] = {'value': _r(marg[-1]['operating_margin'], 6), 'fiscal_year_end': marg[-1].get('end'),
                                      'source': 'fc.paths'}
    else:
        rows = [p for p in years if _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0]
        if rows:
            d['operating_margin_first'] = {'value': _r(rows[0]['ebit'] / rows[0]['revenue'], 6),
                                           'fiscal_year_end': rows[0].get('end'), 'source': 'executed forecast rows'}
            d['operating_margin_last'] = {'value': _r(rows[-1]['ebit'] / rows[-1]['revenue'], 6),
                                          'fiscal_year_end': rows[-1].get('end'), 'source': 'executed forecast rows'}
    term = (_scenario_spec(cf, s).get('terminal') or {})
    if _n(term.get('growth')):
        d['terminal_growth'] = {'value': _r(term['growth'], 6), 'source': 'scenario terminal'}
    ke = (fc.get('cost_of_equity') or {}).get(s)
    if _n(ke):
        d['cost_of_equity'] = {'value': _r(ke, 6), 'source': 'fc.cost_of_equity'}
    elif _n(_scenario_spec(cf, s).get('cost_of_equity')):
        d['cost_of_equity'] = {'value': _r(_scenario_spec(cf, s)['cost_of_equity'], 6), 'source': 'valuation case scenario'}
    else:
        _gap(gaps, 'cost_of_equity', f'{s}: no cost of equity in fc.cost_of_equity or the valuation case')
    f = (fc.get('funding') or {}).get(s)
    if isinstance(f, dict) and f.get('status'):
        d['funding'] = {'status': f.get('status'), 'equity_raised': f.get('equity_raised'), 'debt_assumed': f.get('debt_assumed'),
                        'new_financing_shares': f.get('new_financing_shares'), 'sbc_settlement_shares': f.get('sbc_settlement_shares'),
                        'first_gap': f.get('first_gap'), 'source': 'fc.funding'}
    elif years:
        new = sum(p.get('new_financing_shares') or 0 for p in years if _n(p.get('new_financing_shares')))
        gap = next((p.get('end') for p in years if _n(p.get('funding_gap')) and p['funding_gap'] > 1e-6), None)
        d['funding'] = {'status': None, 'new_financing_shares': new, 'first_gap': gap, 'source': 'executed forecast rows'}
        _gap(gaps, 'funding', f'{s}: fc.funding absent: new shares and funding gaps read from the executed rows')
    if _n(opening_shares) and _n(five_shares) and opening_shares > 0:
        d['shares'] = {'today': opening_shares, 'fifth_year': five_shares, 'change': _r(five_shares / opening_shares - 1, 6),
                       'source': 'executed forecast rows'}
    return d


def _brief_he(d):
    """The short condition (table note and each row's 'if'): growth first -> last and margin start -> end, no dates; an
    assumed or missing financing is named, without numbers."""
    g0, g1, m0, m1 = (d.get(k) for k in ('growth_first', 'growth_last', 'operating_margin_first', 'operating_margin_last'))
    out = []
    if g0:
        v0 = g0['value']
        s = f"ההכנסות {'יצמחו' if v0 >= 0 else 'יקטנו'} בכ־{_pct(abs(v0))} בשנה הראשונה"
        if g1 and g1.get('fiscal_year_end') != g0.get('fiscal_year_end') and abs(g1['value'] - v0) >= .0005:
            v1 = g1['value']
            s += (f" ובסוף יקטנו בכ־{_pct(-v1)} בשנה" if v1 < 0 else f" והצמיחה {'תאט' if v1 < v0 else 'תאיץ'} לכ־{_pct(v1)}")
        out.append(s)
    if m0:
        if m1 and m1.get('fiscal_year_end') != m0.get('fiscal_year_end') and abs(m1['value'] - m0['value']) >= .0005:
            out.append(f"הרווח התפעולי {'יעלה' if m1['value'] > m0['value'] else 'ירד'} מכ־{_pct(m0['value'])} לכ־"
                       f"{_pct(m1['value'])} מההכנסות")
        else:
            out.append(f"הרווח התפעולי יישאר כ־{_pct(m0['value'])} מההכנסות")
    s = ', ו'.join(out)
    st = (d.get('funding') or {}).get('status')
    if st == 'assumed_external_financing':
        s += ('; ' if s else '') + 'ובמימון חיצוני שעוד לא הובטח'
    elif st == 'unfunded':
        s += ('; ' if s else '') + 'אבל חסר מימון'
    return s or None


def _growth_he(d):
    g0, g1 = d.get('growth_first'), d.get('growth_last')
    if not g0:
        return None
    v0 = g0['value']
    out = (f"ההכנסות {'יצמחו' if v0 >= 0 else 'יקטנו'} בכ־{_pct(abs(v0))} בשנה שמסתיימת ב־{_ym(g0['fiscal_year_end'])}")
    if g1 and g1.get('fiscal_year_end') != g0.get('fiscal_year_end'):
        v1 = g1['value']
        if abs(v1 - v0) < .0005:
            out += f" וימשיכו בקצב הזה עד {_ym(g1['fiscal_year_end'])}"
        elif v1 < 0:
            out += f", ועד {_ym(g1['fiscal_year_end'])} יקטנו בכ־{_pct(-v1)} בשנה"
        else:
            out += f", והצמיחה {'תאט' if v1 < v0 else 'תאיץ'} לכ־{_pct(v1)} בשנה עד {_ym(g1['fiscal_year_end'])}"
    t = d.get('terminal_growth')
    if t:
        out += f" (ומשם כ־{_pct(t['value'])} בשנה לטווח הארוך)"
    return out


def _margin_he(d):
    m0, m1 = d.get('operating_margin_first'), d.get('operating_margin_last')
    if not m0:
        return None
    if not m1 or m1.get('fiscal_year_end') == m0.get('fiscal_year_end') or abs(m1['value'] - m0['value']) < .0005:
        return f"הרווח התפעולי (אחרי תגמול במניות) יישאר כ־{_pct(m0['value'])} מההכנסות"
    verb = 'יעלה' if m1['value'] > m0['value'] else 'ירד'
    return (f"הרווח התפעולי (אחרי תגמול במניות) {verb} מכ־{_pct(m0['value'])} מההכנסות לכ־{_pct(m1['value'])} "
            f"עד {_ym(m1['fiscal_year_end'])}")


def _funding_he(d, cur, opening_shares):
    f = d.get('funding') or {}
    st = f.get('status')
    sh = d.get('shares') or {}
    new = f.get('new_financing_shares') or 0
    dil = (f", כ־{_pct(new / opening_shares)} מניות חדשות" if _n(new) and new > 0 and _n(opening_shares) and opening_shares > 0
           else '')
    if st == 'unfunded':
        return 'אבל חסר מימון והמודל אינו מניח מימון שלא הובטח, ולכן לתרחיש הזה אין שווי'
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


def _condition(d, events, cur, opening_shares):
    g, m, f = _growth_he(d), _margin_he(d), _funding_he(d, cur, opening_shares)
    mech = [x for x in (g, m) if x]
    body = ', ו'.join(mech)
    if f:
        body = (body + '; ' if body else '') + f
    ke = (d.get('cost_of_equity') or {}).get('value')
    rate = f'; השווי מחושב בשיעור היוון של כ־{_pct(ke)} בשנה' if _n(ke) else ''
    short = _brief_he(d)
    if events:
        ev = '; '.join(e['event_he'].rstrip('.') + (' (השערה: ' + e['hypothesis_he'] + ')' if e['hypothesis'] else '')
                       for e in events)
        if_he = f'אם {ev} — ובמודל: {body}{rate}.' if body else f'אם {ev}{rate}.'
        return {'kind': 'analyst_events', 'if_he': if_he, 'short_he': ev}
    if not body:
        return {'kind': 'mechanical', 'if_he': None, 'short_he': None}
    return {'kind': 'mechanical', 'if_he': f'אם {body}{rate}.', 'short_he': short}


def _events(cf, s, gaps):
    """Analyst events for scenario s, as analyst_sheet.build validated them against the FactBook; an event without
    validation marks is checked against the casefile's own evidence (the FactBook is not in the casefile)."""
    raw = (((cf.get('analyst_sheet') or {}).get('scenario_events') or {}).get(s)) or []
    out, index = [], None
    for e in raw:
        if not isinstance(e, dict) or not str(e.get('event_he') or '').strip():
            continue
        ids = [str(i) for i in e.get('evidence_ids') or []]
        if isinstance(e.get('hypothesis'), bool) and isinstance(e.get('unknown_ids'), list):
            unknown, hyp = [str(i) for i in e['unknown_ids']], e['hypothesis']
        else:
            if index is None:
                index = _evidence_index(cf)
                _gap(gaps, 'events_unvalidated', 'scenario events carry no FactBook validation: checked against the evidence '
                                                 'ids the casefile holds')
            unknown = [i for i in ids if i not in index]
            hyp = not ids or bool(unknown)
        why = None
        if hyp:
            why = ('אין לו ראיה מתועדת' if not ids else 'הראיה ' + ', '.join(unknown) + ' לא נמצאה בתיק')
        out.append({'event_he': str(e['event_he']).strip(), 'evidence_ids': ids, 'unknown_ids': unknown, 'hypothesis': hyp,
                    'hypothesis_he': why})
    return out


def _evidence_index(cf):
    """Evidence ids the casefile itself holds (a fallback when the FactBook is not at hand)."""
    ids = set()
    case = ((cf.get('val') or {}).get('case') or {})
    ids |= {s.get('id') for s in case.get('sources') or [] if isinstance(s, dict)}
    for a in list((cf.get('fc') or {}).get('assumptions') or []) + list(case.get('assumptions') or []):
        if isinstance(a, dict):
            ids |= set(a.get('source_ids') or [])
    card = cf.get('card') or {}
    for u in card.get('understanding') or []:
        ids |= set((u or {}).get('evidence_ids') or [])
    for dim in (card.get('dimensions') or {}).values():
        ids |= set((dim or {}).get('evidence_ids') or []) if isinstance(dim, dict) else set()
    for r in (cf.get('rk') or {}).get('register') or []:
        ids |= set((r or {}).get('evidence_ids') or [])
    return {str(i) for i in ids if i}


def _assumptions(cf, s):
    spec = _scenario_spec(cf, s)
    case_sc = next((x for x in ((cf.get('val') or {}).get('case') or {}).get('scenarios') or [] if x.get('name') == s), {})
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

def _row(cf, s, r, i, years, paths, cur, gaps, shares_at):
    d = str(r['date'])
    value = r.get(s)
    dist = (r.get('distributions') or {}).get(s)
    p = _period_for(years, d)
    fy, drv, src = None, {'revenue': None, 'revenue_growth': None, 'operating_margin_after_sbc': None, 'eps': None,
                          'fcf_per_share': None, 'diluted_shares': None}, {}
    anchor = ((cf.get('fc') or {}).get('anchors') or {}).get('current_fiscal_year') or {}
    if p:
        fy = {'start': p.get('start'), 'end': p.get('end'), 'full_year': p['_full']}
        path = next((x for x in paths if x.get('end') == p.get('end')), None)
        if p['_full']:
            drv['revenue'], src['revenue'] = p.get('revenue'), 'executed'
            if path and _n(path.get('growth')):
                drv['revenue_growth'], src['revenue_growth'] = _r(path['growth'], 6), 'fc.paths'
            else:
                prev = next((x for x in years if x.get('end') == p.get('start')), None)
                base = (prev.get('revenue') if prev and prev['_full'] else
                        anchor.get('revenue') if anchor.get('end') == p.get('start') else None)
                if _n(base) and base > 0 and _n(p.get('revenue')):
                    drv['revenue_growth'] = _r(p['revenue'] / base - 1, 6)
                    src['revenue_growth'] = 'executed' if prev and prev['_full'] else 'executed over fc.anchors.current_fiscal_year'
                else:
                    _gap(gaps, 'growth', f"{s}: no growth for the fiscal year ending {p.get('end')} (no forecast path and no "
                                         'previous full fiscal year)')
            if _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0:
                drv['operating_margin_after_sbc'], src['operating_margin_after_sbc'] = _r(p['ebit'] / p['revenue'], 6), 'executed'
            drv['eps'], drv['fcf_per_share'] = _r(_eps(p), 4), _r(_fcf_ps(p), 4)
        else:
            # the current fiscal year: only its remainder is inside the forecast
            if anchor.get('end') == p.get('end') and _n(anchor.get('revenue')):
                drv['revenue'], src['revenue'] = anchor['revenue'], 'fc.anchors.current_fiscal_year'
                if _n(anchor.get('growth')):
                    drv['revenue_growth'], src['revenue_growth'] = _r(anchor['growth'], 6), 'fc.anchors.current_fiscal_year'
            else:
                _gap(gaps, 'current_year_revenue', f"{s}: the fiscal year ending {p.get('end')} is only partly in the forecast and "
                                                   'the casefile holds no full-year anchor (fc.anchors.current_fiscal_year)')
            if path and _n(path.get('operating_margin')):
                drv['operating_margin_after_sbc'], src['operating_margin_after_sbc'] = _r(path['operating_margin'], 6), 'fc.paths'
            elif _n(p.get('ebit')) and _n(p.get('revenue')) and p['revenue'] > 0:
                drv['operating_margin_after_sbc'] = _r(p['ebit'] / p['revenue'], 6)
                src['operating_margin_after_sbc'] = 'executed partial period'
    drv['diluted_shares'] = shares_at.get(d)
    nxt = _next_full(years, d)
    raw_eps = _eps(nxt) if nxt else None
    pe = {'value': None, 'eps_next_fiscal_year': _r(raw_eps, 4), 'next_fiscal_year_start': (nxt or {}).get('start'),
          'next_fiscal_year_end': (nxt or {}).get('end'), 'reason': None}
    if not nxt:
        pe['reason'] = 'no full fiscal year after this date in the executed forecast'
        _gap(gaps, 'next_year_eps', f'{s}: no full fiscal year after {d} in the executed forecast (implied P/E not computed)')
    elif raw_eps is None:
        pe['reason'] = 'the next fiscal year lacks EBIT, interest, cash taxes or shares'
        _gap(gaps, 'next_year_eps', f"{s}: the fiscal year ending {nxt.get('end')} lacks EBIT, interest, cash taxes or shares")
    elif raw_eps <= 0:
        pe['reason'] = 'next fiscal year earnings are zero or negative: a P/E is not meaningful'
    else:
        pe['value'] = _r(value / raw_eps, 2)
    when = 'היום' if i == 0 else f'ב־{_ym(d)}'
    parts = []
    if _n(drv['revenue']):
        g = drv['revenue_growth']
        parts.append(f"הכנסות כ־{_big(drv['revenue'], cur)}" + (f" ({'+' if g >= 0 else '−'}{_pct(abs(g))})" if _n(g) else ''))
    if _n(drv['operating_margin_after_sbc']):
        parts.append(f"רווח תפעולי {_pct(drv['operating_margin_after_sbc'])}")
    if _n(drv['eps']):
        parts.append(f"רווח למניה כ־{_money(drv['eps'], cur)}")
    period = ('' if not fy else
              ('סוף השנה הנוכחית' if fy['end'] == d else f"השנה הנוכחית עד {_ym(fy['end'])}") if not fy['full_year'] else
              f"שנת הכספים עד {_ym(fy['end'])}" if fy['end'] != d else '')
    text = ', '.join(parts)
    if _n(value):
        text += (' → ' if text else '') + f'שווי כ־{_money(value, cur)}'
        if _n(pe['value']):
            text += f" (פי {pe['value']:.0f} על הרווח למניה של השנה הבאה)"
    if _n(dist) and _n(value) and dist > .005 * abs(value):
        text += f'; עד אז חולקו כ־{_money(dist, cur)} למניה'
    return {'date': d, 'when_he': when + (f' ({period})' if period else ''), 'value': value, 'cumulative_distributions': dist,
            'fiscal_year': fy, 'drivers': drv, 'driver_sources': dict(sorted(src.items())), 'implied_pe': pe, 'then_he': text}


def _scenario(cf, s, rows, years_by, raw_by, res_sc, cur, shown, why_none, today):
    gaps = []
    sc = {'name': s, 'label_he': LABEL_HE[s], 'status': 'not_available', 'condition': None, 'note_he': None, 'rows': [],
          'assumption_ids': _assumptions(cf, s), 'analyst_overrides': _overrides(cf, s), 'gaps': gaps,
          'thesis': _scenario_spec(cf, s).get('thesis'), 'why_no_values_he': None}
    if not shown:
        sc['why_no_values_he'] = why_none
        return sc
    events = _events(cf, s, gaps)
    years = years_by.get(s) or []
    if not years:
        _gap(gaps, 'no_forecast_rows', f'{s}: val.result.scenarios[{s}].forecast is absent')
    if not (cf.get('fc') or {}).get('paths'):
        _gap(gaps, 'no_forecast_paths', 'fc.paths absent: growth and margins read from the executed forecast rows')
    shares_at = _shares_at(raw_by.get(s) or [])
    opening = shares_at.get(today)
    d = _drivers(cf, s, years, gaps, opening, shares_at.get(rows[-1]['date']) if rows else None)
    cond = _condition(d, events, cur, opening)
    sc['condition'] = {'kind': cond['kind'], 'if_he': cond['if_he'], 'events': events, 'drivers': d}
    if cond['short_he']:
        sc['note_he'] = f"{LABEL_HE[s]} = אם {cond['short_he']}."
    paths = _paths(cf.get('fc') or {}, s)
    mine = [r for r in rows if _n(r.get(s))]
    if not mine:
        st = (res_sc or {}).get('status')
        _gap(gaps, 'no_values', f'{s}: the executed valuation holds no value for this scenario' + (f' (status {st})' if st else ''))
        sc['why_no_values_he'] = ('לתרחיש הזה אין שווי בהערכה שבוצעה' + (' (חסר מימון שהמודל לא מניח)' if st == 'funding_blocked' else ''))
        return sc
    for i, r in enumerate(rows):
        if not _n(r.get(s)):
            continue
        row = _row(cf, s, r, i, years, paths, cur, gaps, shares_at)
        row['why_he'] = f"אם {cond['short_he']}, אז {row['when_he']}: {row['then_he']}." if cond['short_he'] else \
            f"{row['when_he']}: {row['then_he']}."
        sc['rows'].append(row)
    sc['status'] = 'computed' if not gaps else 'partial'
    return sc


def build(casefile):
    """-> the reasons behind every scenario price of an executed casefile (module docstring). Reads, never revalues."""
    from .render import value_display
    cf = casefile or {}
    dc = cf.get('dc') or {}
    company = dc.get('company') or cf.get('company') or {}
    cur = (dc.get('price') or {}).get('currency') or company.get('currency') or ''
    disp = value_display(cf)
    gaps = []
    src, rows = _yearly(cf, gaps)
    rows = sorted((r for r in rows if isinstance(r, dict) and r.get('date')), key=lambda r: str(r['date']))
    res = ((cf.get('val') or {}).get('result') or {})
    res_sc = {x.get('name'): x for x in res.get('scenarios') or [] if isinstance(x, dict)}
    five = ((dc.get('valuation_view') or {}).get('five_year') or {}).get('date') or (rows[-1]['date'] if rows else None)
    interim = _interim(cf, five)
    raw_by = {s: [p for p in (res_sc.get(s) or {}).get('forecast') or [] if isinstance(p, dict)] for s in SCENARIOS}
    years_by = {s: _fiscal_years(raw_by[s], interim) for s in SCENARIOS}
    shown = bool(disp['show']) and bool(rows)
    why_none = None if shown else (disp['reason_he'] or 'אין בתיק ערכים מתוארכים של ההערכה')
    today = rows[0]['date'] if rows else None
    scen = [_scenario(cf, s, rows, years_by, raw_by, res_sc.get(s), cur, shown, why_none, today) for s in SCENARIOS]
    have = [s for s in scen if s['rows']]
    status = ('not_available' if not have else
              'computed' if len(have) == len(SCENARIOS) and not gaps and all(s['status'] == 'computed' for s in scen) else 'partial')
    v = dc.get('verdict') or {}
    yr = (dc.get('expected_return') or {}).get('yearly_at_model_cost_of_equity') or {}
    return {'reasons_version': 1, 'status': status,
            'company': {'ticker': company.get('ticker'), 'name': company.get('name')},
            'as_of': cf.get('as_of') or dc.get('as_of'), 'currency': cur,
            'values_shown': shown, 'draft': bool(v.get('price_claim_withheld')) if shown else None,
            'why_no_values_he': why_none,
            'source': {'values': src if shown else None, 'rate': yr.get('rate') or 'model cost of equity',
                       'model_cost_of_equity': yr.get('model_cost_of_equity',
                                                      (dc.get('valuation_view') or {}).get('model_cost_of_equity'))},
            'scenarios': scen, 'gaps': gaps, 'definitions': DEFINITIONS, 'rounding': ROUNDING,
            'meaning': "the reasons explain the model's conditional values: what has to happen for each scenario price and the "
                       'business of that year; not forecasts, probabilities or price targets; values are read from the executed '
                       'valuation, never recomputed'}


def render_he(reasons):
    """out/scenarios_he.md: one short section per scenario (its condition and one line per dated price), plus what is
    missing; behind the scenes, under the owner's table."""
    r = reasons or {}
    c = r.get('company') or {}
    title = c.get('name') or c.get('ticker') or 'החברה'
    out = [f"**{title}{' (' + c['ticker'] + ')' if c.get('ticker') and c.get('name') else ''} — מה צריך לקרות כדי שכל מחיר "
           f"יתממש, {r.get('as_of') or ''}**", '']
    if r.get('status') == 'failed':
        return '\n'.join(out + ['לא ניתן היה להפיק את הסיבות לתרחישים: ' + str(r.get('error') or '') + '.', ''])
    if not r.get('values_shown'):
        return '\n'.join(out + [f"אין שווי להציג: {r.get('why_no_values_he') or 'הערכת השווי לא הושלמה'}. "
                                'לכן אין כאן מחירים לפי תרחיש.', ''])
    out.append('כל מחיר בטבלה השנתית הוא שווי מותנה: מה המניה שווה אם התנאי של התרחיש מתקיים. זו לא תחזית, לא הסתברות ולא '
               'יעד מחיר; הערכים נקראו מההערכה שבוצעה ולא חושבו מחדש.' + (' זו טיוטה שעוד לא נבדקה.' if r.get('draft') else ''))
    for s in r.get('scenarios') or []:
        out += ['', f"### {s.get('label_he')}", '']
        cond = s.get('condition') or {}
        if cond.get('if_he'):
            out.append(('**התנאי (אירועים מהאנליסט):** ' if cond.get('kind') == 'analyst_events' else
                        '**התנאי (מנגנון המודל, בלי אירועים מתועדים מהאנליסט):** ') + cond['if_he'])
        if s.get('why_no_values_he'):
            out.append(s['why_no_values_he'] + '.')
        for row in s.get('rows') or []:
            out.append(f"- {row['when_he'].replace('ב־', '', 1)}: {row['then_he']}")
        hyp = [e for e in cond.get('events') or [] if e.get('hypothesis')]
        if hyp:
            out.append(f'- {len(hyp)} מהאירועים בתנאי הם השערה בלי ראיה מתועדת בתיק.')
        miss = list(dict.fromkeys(GAP_HE.get(g['code'], g['code']) for g in s.get('gaps') or []))
        if miss:
            out.append('- חסר בתיק: ' + '; '.join(miss) + '.')
    return '\n'.join(out + [''])
