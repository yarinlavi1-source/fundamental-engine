"""Company price path per scenario: the owner's yearly price for years 1-5 (bear/base/bull, דובי/בסיסי/שורי), built
from this company only.

Contract (inputs['company_path']):
    {'metric': 'eps', 'multiple': 'forward_pe',
     'scenarios': {'bear'|'base'|'bull': {'multiple_path': [m_today, m_y1, m_y2, m_y3, m_y4, m_y5],
                                         'justification': 'justified_high'|'peak'|'unproven', 'reason_he': str,
                                         'source_tier': one of TIERS, 'competitors': [ticker, ...] (tier competitor only),
                                         'evidence_ids': [...]}},
     'own_history': {'low', 'median', 'high', 'years', 'evidence_ids'} (optional),
     'peers': [{'ticker', 'forward_pe', 'growth', 'evidence_ids'}] (optional)}

Price at the end of year t (t = 0 today, 1..5 the anniversaries of as_of) = multiple_path[t] x the EPS of fiscal year t+1
of the engine's own executed forecast for that scenario, where fiscal year 0 is the one in progress at as_of. At each
anniversary the same fiscal year is in progress one year later, so "next fiscal year" keeps one meaning on every row.

EPS is derived from the executed valuation's own period rows (valuation.operating), nothing is assumed:
    net income = EBIT after SBC - interest - modelled taxes + investment income after tax - noncontrolling distributions
    EPS        = sum of the fiscal year's net income / its time-weighted average shares,
                 period shares = (opening + closing) / 2 (the engine dates no issuance inside a period).
SBC is charged as an expense (cash-equivalent policy), so the EPS is after SBC and no award is counted twice; the shares
are the engine's point-in-time count plus every share the forecast issues. Taxes are the engine's modelled taxes: a
fiscal year that uses carried-forward losses is flagged (book EPS would carry a deferred tax charge). A residual-income
case reads its rows' net_income and shares directly. A fiscal year split by the fifth anniversary is summed back.

Rules: a multiple needs evidence ids and a source tier; a scenario without inputs is absent (never defaulted); a 'peak'
multiple above the company's own-history median is flagged with its stated reason (unchecked when no own history is
given); zero or negative EPS, a fiscal year beyond the forecast, or a scenario the engine withheld for unfunded cash
gives no price for that row, with the reason. The yearly return is the price change from today's quote, distributions
excluded. Values are conditional estimates, not market-price predictions; nothing here changes any other value.
"""
from datetime import date
import math

from .valuation import years

SCEN = ('bear', 'base', 'bull')
LABEL_HE = {'bear': 'דובי', 'base': 'בסיסי', 'bull': 'שורי'}
TIERS = ('guidance', 'contracted', 'consensus', 'own_history', 'competitor', 'market', 'analyst')
TIER_HE = {'guidance': 'הנחיית ההנהלה', 'contracted': 'ביקוש חוזי', 'consensus': 'קונצנזוס אנליסטים', 'own_history': 'ההיסטוריה של החברה',
           'competitor': 'מתחרים ישירים', 'market': 'מחיר שוק', 'analyst': 'הערכת אנליסט'}
JUSTIFICATIONS = ('justified_high', 'peak', 'unproven')
JUST_HE = {'justified_high': 'מכפיל גבוה מוצדק', 'peak': 'מכפיל של שיא', 'unproven': 'עוד לא הוכח'}
YEARS = 5
TITLE_HE = 'מחיר צפוי לפי שנה (רווח למניה × מכפיל)'
TOP_KEYS = {'metric', 'multiple', 'scenarios', 'own_history', 'peers'}
SCEN_KEYS = {'multiple_path', 'justification', 'reason_he', 'source_tier', 'competitors', 'evidence_ids'}
HIST_KEYS = {'low', 'median', 'high', 'years', 'evidence_ids'}
PEER_KEYS = {'ticker', 'forward_pe', 'growth', 'evidence_ids'}
WHY_HE = {'nonpositive_eps': 'אין מחיר: רווח למניה אפסי או שלילי', 'beyond_forecast': 'אין מחיר: השנה מעבר לתחזית',
          'funding_blocked': 'אין מחיר: התרחיש תלוי במימון שאין', 'no_eps': 'אין מחיר: אין רווח למניה בתחזית'}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _evidence(ev, where, known):
    if not isinstance(ev, list) or not ev or not all(isinstance(e, str) and e for e in ev):
        raise ValueError(f'company_path {where}: evidence_ids required (a non-empty list of ids)')
    bad = [e for e in ev if known is not None and e not in known]
    if bad:
        raise ValueError(f'company_path {where}: unknown evidence ids {bad}')
    return list(ev)


def _keys(d, allowed, where):
    if not isinstance(d, dict):
        raise ValueError(f'company_path {where}: must be an object')
    extra = sorted(set(d) - allowed)
    if extra:
        raise ValueError(f'company_path {where}: unknown keys {extra} ({", ".join(sorted(allowed))})')


def validate(cp, known=None):
    """The normalized company_path input, or ValueError naming the first defect. known: the FactBook's source and
    qualitative ids (None skips the id check)."""
    _keys(cp, TOP_KEYS, 'input')
    if cp.get('metric') != 'eps' or cp.get('multiple') != 'forward_pe':
        raise ValueError("company_path: metric must be 'eps' and multiple 'forward_pe'")
    sc = cp.get('scenarios')
    if not isinstance(sc, dict) or not sc or set(sc) - set(SCEN):
        raise ValueError('company_path: scenarios must be an object with bear, base and/or bull')
    out = {'metric': 'eps', 'multiple': 'forward_pe', 'scenarios': {}}
    for s in SCEN:
        if s not in sc:
            continue
        x = sc[s]
        _keys(x, SCEN_KEYS, s)
        path = x.get('multiple_path')
        if not isinstance(path, list) or len(path) != YEARS + 1 or any(_num(m) is None or m <= 0 for m in path):
            raise ValueError(f'company_path {s}: multiple_path needs {YEARS + 1} positive numbers (today and years 1-{YEARS})')
        if x.get('justification') not in JUSTIFICATIONS:
            raise ValueError(f'company_path {s}: justification must be one of {", ".join(JUSTIFICATIONS)}')
        if not isinstance(x.get('reason_he'), str) or not x['reason_he'].strip():
            raise ValueError(f'company_path {s}: reason_he (why this multiple) is required')
        tier = x.get('source_tier')
        if tier not in TIERS:
            raise ValueError(f'company_path {s}: source_tier required, one of {", ".join(TIERS)}')
        comp = x.get('competitors')
        if tier == 'competitor':
            if not isinstance(comp, list) or not comp or not all(isinstance(c, str) and c.strip() for c in comp):
                raise ValueError(f'company_path {s}: tier competitor needs the named competitors (tickers)')
        elif comp is not None:
            raise ValueError(f'company_path {s}: competitors belong to tier competitor only')
        out['scenarios'][s] = {'multiple_path': [float(m) for m in path], 'justification': x['justification'],
                               'reason_he': x['reason_he'].strip(), 'source_tier': tier,
                               **({'competitors': list(comp)} if tier == 'competitor' else {}),
                               'evidence_ids': _evidence(x.get('evidence_ids'), s, known)}
    if cp.get('own_history') is not None:
        h = cp['own_history']
        _keys(h, HIST_KEYS, 'own_history')
        lo, md, hi = (_num(h.get(k)) for k in ('low', 'median', 'high'))
        if None in (lo, md, hi) or not 0 < lo <= md <= hi:
            raise ValueError('company_path own_history: 0 < low <= median <= high required')
        if h.get('years') is None:
            raise ValueError('company_path own_history: years (the window the range covers) required')
        out['own_history'] = {'low': lo, 'median': md, 'high': hi, 'years': h['years'],
                              'evidence_ids': _evidence(h.get('evidence_ids'), 'own_history', known)}
    if cp.get('peers') is not None:
        if not isinstance(cp['peers'], list):
            raise ValueError('company_path peers: must be a list')
        out['peers'] = []
        for i, p in enumerate(cp['peers']):
            _keys(p, PEER_KEYS, f'peers[{i}]')
            if not isinstance(p.get('ticker'), str) or not p['ticker'].strip() or _num(p.get('forward_pe')) is None \
                    or p['forward_pe'] <= 0 or _num(p.get('growth')) is None:
                raise ValueError(f'company_path peers[{i}]: ticker, a positive forward_pe and a growth number required')
            out['peers'].append({'ticker': p['ticker'], 'forward_pe': float(p['forward_pe']), 'growth': float(p['growth']),
                                 'evidence_ids': _evidence(p.get('evidence_ids'), f'peers[{i}]', known)})
    return out


def fiscal_years(rows, interim=(), opening_nol=0.):
    """Fiscal-year EPS from one scenario's executed period rows (see the module docstring for the derivation)."""
    out, cur, used, nol = [], [], False, opening_nol or 0.
    for r in rows:
        cur.append(r)
        left = r.get('nol_remaining')
        if left is not None:
            used, nol = used or left < nol - 1e-6, left
        if r['end'] in interim:
            continue
        dur = [years(p['start'], p['end']) for p in cur]
        span = sum(dur)
        if all('net_income' in p for p in cur):
            ni = sum(p['net_income'] for p in cur)
        else:
            ni = sum(p['ebit'] - p['interest'] - p['cash_taxes'] + p.get('investment_income_after_tax', 0.)
                     - p.get('noncontrolling_distributions', 0.) for p in cur)
        sh = sum((p.get('shares_open', p['shares']) + p['shares']) / 2 * d for p, d in zip(cur, dur)) / span
        rev = sum(p['revenue'] for p in cur) if all('revenue' in p for p in cur) else None
        ebit = sum(p['ebit'] for p in cur) if all('ebit' in p for p in cur) else None
        out.append({'fiscal_year_end': r['end'], 'start': cur[0]['start'], 'years': span, 'partial': span < .99,
                    'revenue': rev, 'operating_margin': ebit / rev if rev and ebit is not None else None, 'net_income': ni,
                    'weighted_shares': sh, 'eps': ni / sh if sh > 0 else None, 'uses_tax_losses': used})
        cur, used = [], False
    return out


def _anniversary(as_of, t):
    d = date.fromisoformat(as_of)
    try:
        return date(d.year + t, d.month, d.day).isoformat()
    except ValueError:                        # 29 February
        return date(d.year + t, d.month, 28).isoformat()


def _fmt(v):
    return f'{v:,.2f}' if abs(v) < 100 else f'{v:,.0f}'


def _big(v):
    a = abs(v)
    return f'{v / 1e9:,.2f} מיליארד' if a >= 1e9 else (f'{v / 1e6:,.1f} מיליון' if a >= 1e6 else f'{v:,.0f}')


def build(cp, val, known=None):
    """casefile['company_path'] from the validated input and the executed valuation (pipeline.valuate output)."""
    spec = validate(cp, known)
    res, case = (val or {}).get('result') or {}, (val or {}).get('case') or {}
    if not res.get('scenarios') or res.get('method') not in ('operating', 'residual_income'):
        return {'status': 'not_available', 'reason': 'no executed operating or residual-income valuation to read EPS from',
                'title_he': TITLE_HE}
    interim = set((case.get('generated_from') or {}).get('interim_boundaries') or ())
    as_of, quote = case['as_of'], _num((case.get('quote') or {}).get('price'))
    executed = {s['name']: s for s in res['scenarios']}
    out = {'status': 'computed', 'metric': 'eps', 'multiple': 'forward_pe', 'title_he': TITLE_HE, 'as_of': as_of,
           'currency': case.get('currency'), 'quote': quote, 'valuation_sha256': res.get('input_sha256'),
           'eps_basis': 'net income = EBIT after SBC - interest - modelled taxes + investment income after tax - noncontrolling '
                        'distributions; EPS = fiscal-year net income / time-weighted (opening + closing) / 2 shares; fiscal year 0 '
                        'is the one in progress at as_of; year t uses fiscal year t+1',
           'scenarios': {}, 'absent': [s for s in SCEN if s not in spec['scenarios']], 'flags': [], 'fiscal_years': {},
           **{k: spec[k] for k in ('own_history', 'peers') if k in spec},
           'meaning': 'Conditional yearly prices: the scenario forward multiple x the engine forecast EPS of the next fiscal '
                      'year. Not a market-price prediction; the return is price change from today\'s quote, distributions excluded.'}
    hist = spec.get('own_history')
    for s, x in spec['scenarios'].items():
        ex = executed.get(s)
        if ex is None:
            raise ValueError(f'company_path {s}: the executed valuation has no {s} scenario')
        fy = fiscal_years(ex.get('forecast') or [], interim, _num((case.get('opening') or {}).get('tax_loss_carryforward')))
        out['fiscal_years'][s] = fy
        blocked = ex.get('status') == 'funding_blocked'
        rows = []
        for t, m in enumerate(x['multiple_path']):
            f = fy[t + 1] if t + 1 < len(fy) else None
            when = as_of if t == 0 else _anniversary(as_of, t)
            r = {'year': t, 'date': when, 'multiple': m, 'fiscal_year_end': f and f['fiscal_year_end'],
                 'eps': f and f['eps'], 'revenue': f and f['revenue'], 'operating_margin': f and f['operating_margin'],
                 'net_income': f and f['net_income'], 'weighted_shares': f and f['weighted_shares'], 'price': None,
                 'annual_return': None, 'status': 'no_price'}
            why = ('funding_blocked' if blocked else 'beyond_forecast' if f is None else 'no_eps' if f['eps'] is None
                   else 'nonpositive_eps' if f['eps'] <= 0 else None)
            label = f"{LABEL_HE[s]}, {'היום' if t == 0 else f'שנה {t}'}"
            if why:
                r['why'] = why
                r['reason_he'] = f'{label}: {WHY_HE[why]}' + (f" (רווח למניה {_fmt(f['eps'])} בשנת הכספים שמסתיימת ב־{f['fiscal_year_end']})"
                                                             if why == 'nonpositive_eps' else '') + '.'
            else:
                r['price'] = m * f['eps']
                r['status'] = 'priced'
                if t and quote and quote > 0:
                    r['annual_return'] = (r['price'] / quote) ** (1 / t) - 1
                drv = (f"הכנסות {_big(f['revenue'])}, שולי רווח תפעולי {f['operating_margin']:.0%}, " if f['revenue'] else '')
                r['reason_he'] = (f"{label}: רווח למניה {_fmt(f['eps'])} בשנת הכספים שמסתיימת ב־{f['fiscal_year_end']} ({drv}"
                                  f"{_fmt(f['weighted_shares'] / 1e6)} מיליון מניות) × מכפיל {m:.1f} ({JUST_HE[x['justification']]}; "
                                  f"מקור: {TIER_HE[x['source_tier']]}" + (f": {', '.join(x['competitors'])}" if x.get('competitors') else '')
                                  + f") = {_fmt(r['price'])}. {x['reason_he']}")
                if f['uses_tax_losses']:
                    r['note'] = 'modelled taxes use carried-forward losses this fiscal year; book EPS would carry a deferred tax charge'
            r['evidence_ids'] = list(x['evidence_ids'])
            rows.append(r)
        out['scenarios'][s] = {'label_he': LABEL_HE[s], **x, 'rows': rows}
        if x['justification'] == 'peak':
            if hist is None:
                out['flags'].append({'id': 'peak_unchecked', 'scenario': s, 'severity': 'medium',
                                     'text_he': f'תרחיש {LABEL_HE[s]}: מכפיל של שיא בלי טווח מכפילים היסטורי של החברה לבדוק מולו.'})
            else:
                over = [(t, m) for t, m in enumerate(x['multiple_path']) if m > hist['median'] + 1e-9]
                if over:
                    out['flags'].append({'id': 'peak_above_own_median', 'scenario': s, 'severity': 'high', 'years': [t for t, _ in over],
                                         'max_multiple': max(m for _, m in over), 'own_median': hist['median'],
                                         'explanation_he': x['reason_he'],
                                         'text_he': f"תרחיש {LABEL_HE[s]}: מכפיל של שיא עד {max(m for _, m in over):.1f}, מעל החציון של החברה "
                                                    f"עצמה ({hist['median']:.1f}, {hist['years']}); ההסבר שניתן: {x['reason_he'].rstrip('.')}."})
    return out


def table_he(cp, money):
    """Rows of the owner's yearly price table (שנה | דובי | בסיסי | שורי) and its notes; money(v) formats a price."""
    if (cp or {}).get('status') != 'computed':
        return [], []
    sc = cp['scenarios']
    lines = []
    for t in range(YEARS + 1):
        cells = []
        when = None
        for s in SCEN:
            if s not in sc:
                cells.append('—')
                continue
            r = sc[s]['rows'][t]
            when = when or r['date']
            if r['status'] != 'priced':
                cells.append(WHY_HE[r['why']])
            elif t and r['annual_return'] is not None:
                cells.append(f"{money(r['price'])} ({r['annual_return']:+.0%} בשנה)")
            else:
                cells.append(money(r['price']))
        head = 'היום' if t == 0 else f"שנה {t} ({when[:7].replace('-', '/')})"
        lines.append(f"| {head} | {' | '.join(cells)} |")
    notes = ['מחיר צפוי לפי שנה = הרווח למניה של שנת הכספים הבאה (תחזית המנוע לאותו תרחיש, אחרי הוצאות שכר במניות) × מכפיל רווח '
             'שנומק לכל תרחיש מהנתונים של החברה; בסוגריים: שינוי מחיר שנתי מהמחיר של היום, בלי דיבידנדים. הערכה מותנית, לא הבטחה.']
    for s in cp.get('absent') or []:
        notes.append(f'תרחיש {LABEL_HE[s]}: לא הוזן מכפיל מנומק, ולכן אין לו מחיר (לא ממלאים ברירת מחדל).')
    notes += [f['text_he'] for f in cp.get('flags') or []]
    return lines, notes
