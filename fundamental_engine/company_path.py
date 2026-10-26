"""Company price path per scenario: the owner's yearly price for years 1-5 (bear/base/bull, דובי/בסיסי/שורי), built
from this company only.

Contract (inputs['company_path']):
    {'metric': 'eps', 'multiple': 'forward_pe',
     'scenarios': {'bear'|'base'|'bull': {'multiple_path': [m_today, m_y1, m_y2, m_y3, m_y4, m_y5],     (by hand), or
                                         'multiple_from': 'economics', 'roic': x,                  (computed by the engine)
                                         'justification': 'justified_high'|'peak'|'unproven', 'reason_he': str,
                                         'source_tier': one of TIERS, 'competitors': [ticker, ...] (tier competitor only),
                                         'evidence_ids': [...]}},
     'own_history': {'low', 'median', 'high', 'years', 'evidence_ids'} (optional),
     'peers': [{'ticker', 'forward_pe', 'growth', 'evidence_ids'}] (optional)}

Economics mode (multiple_from 'economics', preferred): the analyst supplies only the scenario's incremental ROIC (a decimal,
0 < roic <= ROIC_MAX = 1.5, one above 1 flagged roic_above_100pct; backed by the scenario's evidence_ids and source_tier) and
the engine computes the multiple path from its OWN executed numbers
for that scenario: the fiscal-year EPS path below, the explicit cost of equity Ke, the terminal cost of equity Ke_T and the
terminal growth g_T of the executed valuation case (so the path can never rest on another Ke or EPS path than the one the
engine ran). With E_k the EPS of executed fiscal year k (k = 0 the one in progress; a partial year is annualized for its
growth only), N executed fiscal years and E_N = E_(N-1) x (1 + g_T):
    D_k     = E_k x (1 - g_(k+1) / ROIC)  [= E_k - (E_(k+1) - E_k) / ROIC; g_N = g_T]      distributions of fiscal year k
    TV      = E_N x (1 - g_T / ROIC) / (Ke_T - g_T)                    at the end of the last executed fiscal year
    value_t = sum over the fiscal years still ahead at anniversary t of D_k x (the share of year k still ahead)
              / (1 + Ke)^(years to the midpoint of that part) + TV / (1 + Ke)^(years to the last fiscal year end)
    multiple_path[t] = value_t / E_(t+1)
(the formula of the ticker analysts' path_calc). The formula, Ke, Ke_T, g_T, the ROIC, the engine's own terminal ROIC, the
distributions and value_t are recorded in the scenario's 'economics'; the justification label is the analyst's, unchanged.
A value or EPS that is not positive gives no multiple for that year (the row says why); a ROIC at or below g_T is refused.

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
TITLE_HE = 'מחיר המניה בכל שנה, לפי תרחיש'   # the owner's one yearly table (render.hebrew_table)
TOP_KEYS = {'metric', 'multiple', 'scenarios', 'own_history', 'peers'}
SCEN_KEYS = {'multiple_path', 'multiple_from', 'roic', 'justification', 'reason_he', 'source_tier', 'competitors', 'evidence_ids'}
MULTIPLE_FROM = ('economics', 'hand')
# roic is a decimal (0.35 = 35%). Above 1.5 (150%) the distributions are nearly all of earnings (1 - g/ROIC), so the bound
# costs little and a percent typed as a number above it (35, 4.5) is refused. A ROIC above 1 (100%) reads as 100-150%: it is
# flagged (roic_above_100pct) and recorded in the economics record for the reviewer. Below 1 a typed percent cannot be told
# from a decimal by its size: the evidence, the refusal at or below g_T and the review read it.
ROIC_MAX = 1.5
ROIC_FLAG = 1.
ECON_FORMULA = ('value_t = sum over the executed fiscal years k still ahead at anniversary t of D_k x (share of year k ahead) / '
                '(1 + Ke)^(years to the midpoint of that part) + TV / (1 + Ke)^(years to the last executed fiscal year end); '
                'D_k = E_k x (1 - g_(k+1) / ROIC) = E_k - (E_(k+1) - E_k) / ROIC (a partial year annualized for its growth; '
                'g_N = g_T); TV = E_N x (1 - g_T / ROIC) / (Ke_T - g_T), E_N = E_(N-1) x (1 + g_T); '
                'multiple_t = value_t / E_(t+1); E = the executed fiscal-year EPS of the scenario')
HIST_KEYS = {'low', 'median', 'high', 'years', 'evidence_ids'}
PEER_KEYS = {'ticker', 'forward_pe', 'growth', 'evidence_ids'}
WHY_HE = {'nonpositive_eps': 'אין מחיר: רווח למניה אפסי או שלילי', 'beyond_forecast': 'אין מחיר: השנה מעבר לתחזית',
          'funding_blocked': 'אין מחיר: התרחיש תלוי במימון שאין', 'no_eps': 'אין מחיר: אין רווח למניה בתחזית',
          'nonpositive_value': 'אין מחיר: השווי לפי הכלכלה של התרחיש אינו חיובי',
          'economics_unavailable': 'אין מחיר: אי אפשר לחשב מכפיל מהכלכלה של התרחיש'}


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
        mode = x.get('multiple_from', 'hand')
        if mode not in MULTIPLE_FROM:
            raise ValueError(f"company_path {s}: multiple_from must be 'economics' (the engine computes the path) or 'hand'")
        path = x.get('multiple_path')
        if mode == 'economics':
            if path is not None:
                raise ValueError(f"company_path {s}: multiple_from 'economics' computes multiple_path; do not give one")
            roic = _num(x.get('roic'))
            if roic is None or not 0 < roic <= ROIC_MAX:
                raise ValueError(f"company_path {s}: multiple_from 'economics' needs roic, the scenario's incremental ROIC as a "
                                 f"decimal (0 < roic <= {ROIC_MAX:g}; 0.35 = 35%, so 4.5% is 0.045, never 4.5), backed by its "
                                 'evidence_ids and source_tier')
        else:
            if x.get('roic') is not None:
                raise ValueError(f"company_path {s}: roic belongs to multiple_from 'economics' only")
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
        head = ({'multiple_from': 'economics', 'roic': float(x['roic'])} if mode == 'economics'
                else {'multiple_path': [float(m) for m in path]})
        out['scenarios'][s] = {**head, 'justification': x['justification'],
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


EPS_LEVERS = ('growth_anchor', 'growth_half_life', 'mature_margin', 'sbc', 'sales_to_capital', 'tax_rate')


def _eps_drivers(fc, s):
    """The levers behind the scenario's EPS, read from the executed forecast: for each, its source tier, evidence ids, the
    evidenced value when an override set it, and the rule class when it is still on one (a visible gap, never filled in)."""
    fc = fc or {}
    val = {}
    for o in fc.get('overrides') or []:
        if isinstance(o, dict) and o.get('scenario') in (s, 'all'):
            val[o.get('path')] = o.get('value')
    paths = {'growth_anchor': ('growth', 'growth_anchor'), 'growth_half_life': ('growth_half_life',),
             'mature_margin': ('cash_cost_ratio', 'terminal.operating_margin', 'mature_margin'), 'sbc': ('sbc_pct',),
             'sales_to_capital': ('growth_capex_pct', 'sales_to_capital'), 'tax_rate': ('tax_rate',)}
    out = []
    for r in fc.get('driver_sources') or []:
        if not isinstance(r, dict) or r.get('scenario') not in (s, 'all') or r.get('lever') not in EPS_LEVERS:
            continue
        v = next((val[p] for p in paths.get(r['lever'], ()) if p in val), None)
        out.append({'lever': r['lever'], 'value': v, 'source_tier': r.get('tier'), 'evidence_ids': list(r.get('evidence_ids') or []),
                    'default_class': r.get('default_class')})
    return out


def _events(events, s):
    """The analyst's events behind scenario s (analyst_sheet.scenario_events as validated): an event without known evidence is a
    hypothesis and says so."""
    out = []
    for e in (events or {}).get(s) or []:
        if isinstance(e, dict) and isinstance(e.get('event_he'), str) and e['event_he'].strip():
            ids = [str(i) for i in e.get('evidence_ids') or [] if isinstance(i, str)]
            hyp = bool(e.get('hypothesis')) if isinstance(e.get('hypothesis'), bool) else not ids
            out.append({'event_he': e['event_he'].strip(), 'evidence_ids': ids, 'hypothesis': hyp})
    return out


def _events_he(events):
    return '; '.join(e['event_he'].rstrip('.') + (' (השערה)' if e['hypothesis'] else '') for e in events)


def economics(fy, as_of, ke, ket, gt, roic):
    """The multiple path of one scenario from its own economics (module docstring, economics mode): fy = fiscal_years() of
    the executed scenario, ke / ket / gt = its executed explicit cost of equity, terminal cost of equity and terminal growth,
    roic = the analyst's incremental ROIC. -> the record stored in the scenario's 'economics' (multiple_path: None where a
    year has no positive value or next-year EPS, or beyond the forecast)."""
    rec = {'status': 'computed', 'formula': ECON_FORMULA, 'cost_of_equity': ke, 'terminal_cost_of_equity': ket,
           'terminal_growth': gt, 'roic': roic, 'explicit_fiscal_years': len(fy),
           'last_explicit_fiscal_year_end': fy[-1]['fiscal_year_end'] if fy else None}
    eps = [f.get('eps') for f in fy]
    why = ('the executed forecast has fewer than two fiscal years' if len(fy) < 2 else
           'a fiscal year of the executed forecast has no EPS' if any(_num(e) is None for e in eps) else
           'the terminal cost of equity is not above the terminal growth' if ket <= gt else None)
    if why:
        return {**rec, 'status': 'not_computed', 'reason': why, 'multiple_path': [None] * (YEARS + 1), 'values': []}
    span = [f['years'] for f in fy]
    ends = [years(as_of, f['fiscal_year_end']) for f in fy]
    ann = [e / y if f['partial'] else e for e, y, f in zip(eps, span, fy)]           # a partial year annualized for growth
    nxt = ann[1:] + [ann[-1] * (1 + gt)]
    dist = [e - (y if f['partial'] else 1.) * (b - a) / roic for e, y, f, a, b in zip(eps, span, fy, ann, nxt)]
    tv = nxt[-1] * (1 - gt / roic) / (ket - gt)
    rows, path = [], []
    for t in range(YEARS + 1):
        at = years(as_of, _anniversary(as_of, t)) if t else 0.
        v = 0.
        for k in range(len(fy)):
            if ends[k] <= at + 1e-9:
                continue
            ahead = min(span[k], ends[k] - at)
            v += dist[k] * ahead / span[k] / (1 + ke) ** (ends[k] - at - ahead / 2)
        v += tv / (1 + ke) ** (ends[-1] - at)
        e1 = eps[t + 1] if t + 1 < len(fy) else None
        m = v / e1 if e1 is not None and e1 > 0 and v > 0 else None
        path.append(m)
        rows.append({'year': t, 'date': as_of if t == 0 else _anniversary(as_of, t), 'value': v, 'next_fiscal_year_eps': e1,
                     'multiple': m})
    rec.update(multiple_path=path, values=rows, terminal={'eps': nxt[-1], 'value_at_last_fiscal_year_end': tv},
               fiscal_years=[{'fiscal_year_end': f['fiscal_year_end'], 'years_ahead_at_end': d, 'eps': e,
                              **({'eps_annualized': a} if f['partial'] else {}),
                              'growth_next': (b / a - 1) if a > 0 else None, 'distribution': x}
                             for f, d, e, a, b, x in zip(fy, ends, eps, ann, nxt, dist)])
    return rec


def _economics_for(cs, fy, as_of, roic, s, res_sc):
    """economics() at the executed rates of scenario s of the valuation case (cs); records the engine's own terminal ROIC
    and value today beside it (a reviewer compares them; nothing is changed)."""
    term = cs.get('terminal') or {}
    ke, gt = _num(cs.get('cost_of_equity')), _num(term.get('growth'))
    if ke is None or gt is None:
        raise ValueError(f'company_path {s}: the executed valuation case has no cost of equity or terminal growth for {s}')
    ket = _num(term.get('cost_of_equity'))
    src = 'terminal.cost_of_equity'
    if ket is None:
        ket, src = ke, 'cost_of_equity (the case has no separate terminal cost of equity)'
    if roic <= gt:
        raise ValueError(f'company_path {s}: roic {roic:.4f} must exceed the executed terminal growth {gt:.4f}')
    rec = economics(fy, as_of, ke, ket, gt, roic)
    if roic > ROIC_FLAG:
        rec['roic_check'] = (f'roic {roic:g} is read as a decimal: {roic:.0%} incremental return on capital (1.0 = 100%); a percent '
                             f'typed as a number ({roic:g}%) would be {roic / 100:.4f}. Check it against its evidence')
    eng = _num(term.get('roic', term.get('roe')))
    today = next((r.get('value_per_share') for r in (res_sc or {}).get('timeline') or [] if r.get('date') == as_of), None)
    rec.update(terminal_cost_of_equity_source=src, engine_terminal_roic=eng,
               roic_minus_engine_terminal_roic=(roic - eng) if eng is not None else None, engine_value_today=today)
    return rec


def build(cp, val, known=None, events=None, fc=None):
    """casefile['company_path'] from the validated input and the executed valuation (pipeline.valuate output). events: the
    analyst's validated scenario events (casefile analyst_sheet.scenario_events); fc: the executed forecast (its driver_sources
    and overrides): each priced row stores them as its reason, with their evidence ids and source tiers."""
    spec = validate(cp, known)
    res, case = (val or {}).get('result') or {}, (val or {}).get('case') or {}
    if not res.get('scenarios') or res.get('method') not in ('operating', 'residual_income'):
        return {'status': 'not_available', 'reason': 'no executed operating or residual-income valuation to read EPS from',
                'title_he': TITLE_HE}
    interim = set((case.get('generated_from') or {}).get('interim_boundaries') or ())
    as_of, quote = case['as_of'], _num((case.get('quote') or {}).get('price'))
    executed = {s['name']: s for s in res['scenarios']}
    case_sc = {c.get('name'): c for c in case.get('scenarios') or [] if isinstance(c, dict)}
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
        ev_s, drv_s = _events(events, s), _eps_drivers(fc, s)
        cond = _events_he(ev_s)
        gaps = [d['lever'] for d in drv_s if d['default_class']]
        econ = (_economics_for(case_sc.get(s) or {}, fy, as_of, x['roic'], s, ex) if x.get('multiple_from') == 'economics'
                else None)
        mpath = econ['multiple_path'] if econ else x['multiple_path']
        rows = []
        for t, m in enumerate(mpath):
            f = fy[t + 1] if t + 1 < len(fy) else None
            when = as_of if t == 0 else _anniversary(as_of, t)
            r = {'year': t, 'date': when, 'multiple': m, 'fiscal_year_end': f and f['fiscal_year_end'],
                 'eps': f and f['eps'], 'revenue': f and f['revenue'], 'operating_margin': f and f['operating_margin'],
                 'net_income': f and f['net_income'], 'weighted_shares': f and f['weighted_shares'], 'price': None,
                 'annual_return': None, 'status': 'no_price'}
            why = ('funding_blocked' if blocked else 'beyond_forecast' if f is None else 'no_eps' if f['eps'] is None
                   else 'nonpositive_eps' if f['eps'] <= 0 else None)
            if why is None and m is None:            # economics mode: no positive value for that year, or not computable
                why = 'nonpositive_value' if econ['status'] == 'computed' else 'economics_unavailable'
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
                how = (f"מחושב מהכלכלה של התרחיש: תשואה על ההון {econ['roic']:.1%}, היוון {econ['cost_of_equity']:.1%} ובטווח "
                       f"הארוך {econ['terminal_cost_of_equity']:.1%} עם צמיחה {econ['terminal_growth']:.1%}; " if econ else '')
                r['reason_he'] = (f"{label}: רווח למניה {_fmt(f['eps'])} בשנת הכספים שמסתיימת ב־{f['fiscal_year_end']} ({drv}"
                                  f"{_fmt(f['weighted_shares'] / 1e6)} מיליון מניות) × מכפיל {m:.1f} ({how}{JUST_HE[x['justification']]}; "
                                  f"מקור{' התשואה על ההון' if econ else ''}: {TIER_HE[x['source_tier']]}"
                                  + (f": {', '.join(x['competitors'])}" if x.get('competitors') else '')
                                  + f") = {_fmt(r['price'])}. {x['reason_he'].rstrip('.')}."
                                  + (f" התנאי: {cond}." if cond else ''))
                if f['uses_tax_losses']:
                    r['note'] = 'modelled taxes use carried-forward losses this fiscal year; book EPS would carry a deferred tax charge'
            r['events'], r['eps_drivers'], r['eps_driver_gaps'] = ev_s, drv_s, gaps
            r['multiple_evidence_ids'] = list(x['evidence_ids'])
            r['evidence_ids'] = list(dict.fromkeys(list(x['evidence_ids']) + [i for e in ev_s for i in e['evidence_ids']]
                                                   + [i for d in drv_s for i in d['evidence_ids']]))
            rows.append(r)
        out['scenarios'][s] = {'label_he': LABEL_HE[s], **x, **({'multiple_path': mpath, 'economics': econ} if econ else {}),
                               'rows': rows}
        if econ and econ.get('roic_check'):
            out['flags'].append({'id': 'roic_above_100pct', 'scenario': s, 'severity': 'medium', 'roic': x['roic'],
                                 'text': econ['roic_check'],
                                 'text_he': f"תרחיש {LABEL_HE[s]}: תשואה על ההון {x['roic']:g} נקראת כשבר עשרוני, כלומר {x['roic']:.0%} "
                                            f"(1.0 = 100%); אם הכוונה הייתה {x['roic']:g}% — יש לתקן ל־{x['roic'] / 100:.4f}."})
        if x['justification'] == 'peak':
            if hist is None:
                out['flags'].append({'id': 'peak_unchecked', 'scenario': s, 'severity': 'medium',
                                     'text_he': f'תרחיש {LABEL_HE[s]}: מכפיל של שיא בלי טווח מכפילים היסטורי של החברה לבדוק מולו.'})
            else:
                over = [(t, m) for t, m in enumerate(mpath) if m is not None and m > hist['median'] + 1e-9]
                if over:
                    out['flags'].append({'id': 'peak_above_own_median', 'scenario': s, 'severity': 'high', 'years': [t for t, _ in over],
                                         'max_multiple': max(m for _, m in over), 'own_median': hist['median'],
                                         'explanation_he': x['reason_he'],
                                         'text_he': f"תרחיש {LABEL_HE[s]}: מכפיל של שיא עד {max(m for _, m in over):.1f}, מעל החציון של החברה "
                                                    f"עצמה ({hist['median']:.1f}, {hist['years']}); ההסבר שניתן: {x['reason_he'].rstrip('.')}."})
    return out


def tiers_he(cp):
    """Where each scenario's multiple comes from, in a few words (the full reason stays behind the scenes)."""
    sc = (cp or {}).get('scenarios') or {}
    tiers = {sc[s]['source_tier'] for s in SCEN if s in sc}
    econ = [s for s in SCEN if s in sc and sc[s].get('multiple_from') == 'economics']
    if tiers == {'own_history'} and not econ:
        return 'מההיסטוריה של המכפילים של החברה עצמה'
    return 'מקור לכל מכפיל: ' + '; '.join(
        f"{LABEL_HE[s]} — " + (f"מחושב מהתשואה על ההון ({TIER_HE[sc[s]['source_tier']]})" if s in econ else TIER_HE[sc[s]['source_tier']])
        + (f" ({', '.join(sc[s]['competitors'])})" if sc[s].get('competitors') else '')
        for s in SCEN if s in sc)


def table_he(cp, money):
    """Rows of the owner's yearly price table (שנה | דובי | בסיסי | שורי) for years 1-5 and its notes; money(v) formats a price.
    Today's EPS x multiple (year 0) stays in the casefile: the table's today is the quote and the fair value above it."""
    if (cp or {}).get('status') != 'computed':
        return [], []
    sc = cp['scenarios']
    lines = []
    for t in range(1, YEARS + 1):
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
        lines.append(f"| שנה {t} ({when[:7].replace('-', '/')}) | {' | '.join(cells)} |")
    notes = ['הטבלה השנתית: מחיר המניה בסוף כל שנה אם התרחיש מתממש = הרווח למניה של שנת הכספים הבאה (תחזית המנוע לאותו תרחיש, '
             f'אחרי הוצאות שכר במניות) × מכפיל רווח מנומק ({tiers_he(cp)}; הנימוק לכל מחיר מאחורי הקלעים); בסוגריים: שינוי מחיר '
             'שנתי מהמחיר של היום, בלי דיבידנדים. הערכה מותנית, לא הבטחה.']
    for s in cp.get('absent') or []:
        notes.append(f'תרחיש {LABEL_HE[s]}: לא הוזן מכפיל מנומק, ולכן אין לו מחיר (לא ממלאים ברירת מחדל).')
    notes += [f['text_he'] for f in cp.get('flags') or []]
    return lines, notes
