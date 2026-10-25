"""Quarterly report review: the fast read a good analyst does on results day,
made deterministic and auditable.

Lessons taken from comparing human earnings-day write-ups with the engine:
- remove one-off gains AND add back one-off charges symmetrically; recurring
  costs stay; no default tax rate on pre-tax items;
- opex growth versus revenue growth, reported and ex-unusual items;
- restricted cash, insurance reserves and customer funds are not spendable liquidity;
- cumulative KPIs ("products ever opened") are not activity evidence;
- price reacts to results versus expectations, so guidance is compared with prior
  guidance and an attributed consensus, never judged on its own;
- a peak-cycle quarter times four is a run rate, not normalized earnings;
- a dual-listed line (ADR) can trade at a premium to the same economic claim;
- banks: price / tangible book only means something against ROTE and cost of equity;
- EPS x exit multiple is a dated sensitivity with an implied annual return, not value.

Business economics (econ layer, WP7): an optional case['economics'] is the econ case subset (econ.review's case without
the company, period_end, as_of and currency it inherits from this case; period 'quarter'; sources merged with
case['sources']; card_type, a model_card type, read here). Its review comes back as `economics`; its flags merge as
econ.<key> beside quarter's own keys; its falsifiers feed `falsifiers` (a falsifier with econ {id, scope, field} reads its
current value from the econ row; the other econ falsifiers are listed as tracked). Its model, when evidenced (econ.select's
rule or an analyst override with evidence ids), also decides the peak exception in _cycle (operating_leverage_or_peak).
Without case['economics'] every output is byte-identical.

Inputs are absolute currency units in one stated currency. Nothing is fetched.
Output is research indication, not a trade instruction or a price forecast.
"""
from datetime import date
from hashlib import sha256
import json
from .finance import number
from . import __version__

LINES = ('revenue', 'gross_profit', 'operating_income', 'adjusted_ebitda', 'net_income',
         'operating_expenses', 'operating_cash_flow', 'capex', 'diluted_shares', 'sbc', 'buybacks')
KPI_BASES = {'active', 'flow', 'balance', 'cumulative'}
SEVERITY = {'high': 0, 'medium': 1, 'info': 2}
# case['economics'] (the econ case subset): the meta it inherits from this case, the period it reads, the checks of the
# econ registry this module owns (run in _cycle, never by econ.review), and the fields a falsifier may read from an econ row
ECON_INHERITED = ('company', 'period_end', 'as_of', 'currency')
ECON_PERIOD = 'quarter'
QUARTER_OWNED_CHECKS = ('peak_margin_run_rate', 'operating_leverage_or_peak')
ECON_FALSIFIER_FIELDS = ('value', 'growth', 'change')
# Peak exception (docs/prereg_econ.md section 1, frozen 21.10.2026). With gross profit and operating expenses that
# reconcile to operating income, OM = GM - opex/revenue exactly, so y/y dOM = dGM - d(opex/revenue) and the leverage share
# -d(opex/revenue) / dOM says how much of the margin rise came from costs growing slower than revenue rather than from
# gross margin (price, mix). peak_margin_run_rate becomes operating_leverage_or_peak (registry severity, medium) only when
# the share is >= rules.leverage_share_min (0.667, an uncalibrated screen), case.economics names a model that is not
# price-cyclical (registry price_cyclical: memory_cycle, gpu_vendor) and no cyclical card type (model_card.CYCLICAL_TYPES):
# operating leverage in a cyclical upswing is the peak mechanism itself, so the identity alone never clears a cyclical.
# Filed Q2-26 checks: PLTR 0.809, RDDT 0.970 -> medium; SKHY (no gross profit or opex; memory_cycle) stays high.
# Stricter than the design text (noted in the WP7 notes): the identity must reconcile in both periods (|operating income -
# (gross profit - opex)| <= rules.identity_tolerance x |operating income|), else opex is not the operating cost base.
# The model that clears the flag must be evidenced as econ.select chooses one (WP4): the rule's own selection on this
# subset (two of card type, text and KPI facts agree, with economics.card_type) names the same models, or economics.model
# is an analyst override {primary, secondary?, reason, evidence_ids} whose ids are sources available at as_of and not a
# consensus, a market feed or an assumption (econ.select refuses those). A bare model id is a reading choice: it reads the
# identities but never lowers a high flag (model_not_evidenced; close-out review of WP7, 25.10.2026).
PEAK_TESTS = (('history_max', 'the history maximum'), ('median_plus_15pp', 'the history median + 15pp'))
MODEL_OVERRIDE_KEYS = ('primary', 'secondary', 'reason', 'evidence_ids')
# Hebrew text of each econ flag merged into quarter's flags (econ.<key>); the numbers stay in economics.flags
ECON_FLAG_HE = {
    'per_unit_vs_units': 'מספר היחידות גדל, אבל ההכנסה ליחידה ירדה ביותר מ-5%: הצמיחה באה מכמות ולא ממחיר.',
    'take_rate_shift': 'החלק שהחברה שומרת מכל דולר של הזמנות זז ביותר מנקודת אחוז: לבדוק אם זה מחיר, תמהיל או שינוי בהגדרת ההכנסה.',
    'thesis_on_minor_line': 'התזה נשענת על שורת הכנסה קטנה שמסבירה רק חלק קטן מהצמיחה.',
    'core_units_flat': 'מספר המשתמשים או היחידות בליבת העסק כמעט לא זז: הצמיחה לא באה מעוד לקוחות.',
    'disclosure_dropped': 'החברה מפסיקה לדווח מדד שהתזה נשענת עליו: אי אפשר יותר לעקוב אחרי תנאי ההפרכה שלו.',
    'segment_unexplained': 'מגזר מהותי בלי הסבר כלכלי מאומת: עוד לא ברור איך הוא מרוויח.',
    'cash_conversion': 'התזרים החופשי נמוך בהרבה מהרווח הנקי: לבדוק הון חוזר והשקעות.',
    'top_n_inconsistent': 'נתוני הלקוחות הגדולים לא מתיישבים עם ההכנסות של אותה תקופה.',
    'lta_unproven': 'לחוזים ארוכי הטווח חסרים פרטים (היקף, מנגנון מחיר, מקדמות, משך): הם עוד לא מקטינים את סיכון המחזור.',
    'customer_prepayment_build': 'המקדמות מלקוחות גדלו: הלקוחות מממנים חלק מהפעילות.',
    'nco_basis': 'החברה מוכרת הלוואות, אבל ניתן רק שיעור המחיקות הכותרתי: הפסדי האשראי בפועל יכולים להיות גבוהים יותר.',
    'capital_lever_insufficient': 'גם החזרה של כל ההון העודף לא מביאה לתשואה על ההון שהמחיר מניח: השאר צריך לבוא מרווח גבוה יותר.',
    'tranche_schedule_mismatch': 'הוצאת המניות מעסקת הרכישה ברבעון לא מתאימה ללוח ההבשלה המוצהר: לא ברור מתי היא תסתיים.',
}


def _num(value, label, minimum=None):
    return None if value is None else number(value, label, minimum)


def _growth(a, b):
    return a / b - 1 if a is not None and b is not None and b > 0 else None


def _margin(a, rev):
    return a / rev if a is not None and rev is not None and rev > 0 else None


def _sources(item, label):
    ids = item.get('source_ids')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise ValueError(f'{label}: source_ids required')
    return ids


def _lines(block, label):
    if block is None:
        return {}
    if not isinstance(block, dict) or set(block) - set(LINES):
        raise ValueError(f'{label}: unknown line; allowed: ' + ', '.join(LINES))
    out = {k: _num(v, f'{label}.{k}') for k, v in block.items()}
    for k in ('revenue', 'diluted_shares', 'capex', 'operating_expenses', 'sbc', 'buybacks'):
        if out.get(k) is not None and out[k] < 0:
            raise ValueError(f'{label}.{k}: expected non-negative (capex/buybacks as positive outflows)')
    return out


def _normalize(cur, one_offs, unusual, flags):
    """Symmetric after-tax normalization of net income. Recurring items are kept.
    Non-recurring unusual opex removed for operating leverage is added back here too,
    so a charge cannot be ignored in one view and kept in the other."""
    one_offs = list(one_offs) + [{**u, 'kind': 'charge', 'pretax': True, 'from_unusual_opex': True}
                                 for u in unusual if not u.get('recurring')]
    removed, kept = [], []
    adjustment = 0.0
    for i, o in enumerate(one_offs):
        label = f'one_offs[{i}]'
        _sources(o, label)
        if o.get('kind') not in {'gain', 'charge'}:
            raise ValueError(f'{label}.kind must be gain or charge')
        amount = number(o.get('amount'), label + '.amount', 0)
        if o.get('pretax', True):
            if o.get('tax_rate') is None:
                raise ValueError(f"{'unusual_opex item ' + repr(o.get('label')) if o.get('from_unusual_opex') else label}: "
                                 'pre-tax item needs an explicit tax_rate (no default)')
            after = amount * (1 - number(o['tax_rate'], label + '.tax_rate', 0, 1))
        else:
            after = amount
        row = {'label': o.get('label', label), 'kind': o['kind'], 'after_tax': after,
               'non_cash': bool(o.get('non_cash'))}
        if o.get('recurring'):
            kept.append({**row, 'why': 'Recurring or repeated items stay in earnings; never erase a recurring cost or income.'})
            continue
        adjustment += -after if o['kind'] == 'gain' else after
        removed.append(row)
    ni = cur.get('net_income')
    if ni is None:
        if removed:
            raise ValueError('one_offs need current.net_income to normalize')
        return None
    normalized = ni + adjustment
    shares = cur.get('diluted_shares')
    out = {'reported_net_income': ni, 'normalized_net_income': normalized, 'adjustment': adjustment,
           'removed': removed, 'kept': kept,
           'reported_eps': ni / shares if shares else None,
           'normalized_eps': normalized / shares if shares else None,
           'note': 'Gains removed and charges added back after tax, symmetrically. Normalization is not cash and not a forecast.'}
    if ni and abs(adjustment) > .25 * abs(ni):
        flags.append(('headline_distorted', 'high',
                      f'כ-{abs(adjustment) / abs(ni):.0%} מהרווח הנקי המדווח הוא אירוע חד-פעמי; השורה התחתונה המדווחת לא מייצגת.'))
    return out


def _leverage(cur, py, unusual, flags):
    rg = _growth(cur.get('revenue'), py.get('revenue'))
    og = _growth(cur.get('operating_expenses'), py.get('operating_expenses'))
    if rg is None or og is None:
        return None
    one_off = sum(number(u.get('amount'), f'unusual_opex[{i}].amount', 0)
                  for i, u in enumerate(unusual) if _sources(u, f'unusual_opex[{i}]') and not u.get('recurring'))
    clean = _growth(cur['operating_expenses'] - one_off, py['operating_expenses'])
    out = {'revenue_growth': rg, 'opex_growth_reported': og, 'opex_growth_ex_unusual': clean,
           'spread_reported': rg - og, 'spread_ex_unusual': rg - clean, 'unusual_removed': one_off}
    oi, poi = cur.get('operating_income'), py.get('operating_income')
    if oi is not None and poi is not None and cur['revenue'] != py['revenue']:
        out['incremental_operating_margin'] = (oi - poi) / (cur['revenue'] - py['revenue'])
    if rg - clean < -.02:
        flags.append(('negative_operating_leverage', 'medium',
                      'ההוצאות צומחות מהר מההכנסות גם אחרי ניקוי סעיפים חריגים; הצמיחה נקנית ביוקר.'))
    elif rg - clean > .05:
        flags.append(('operating_leverage', 'info', 'ההכנסות צומחות מהר מההוצאות (אחרי ניקוי חריגים): מינוף תפעולי.'))
    return out


def _cash(cur, norm, flags):
    ocf, capex = cur.get('operating_cash_flow'), cur.get('capex')
    if ocf is None or capex is None:
        return None
    fcf = ocf - capex
    out = {'operating_cash_flow': ocf, 'capex': capex, 'free_cash_flow': fcf,
           'fcf_margin': _margin(fcf, cur.get('revenue')), 'capex_to_ocf': capex / ocf if ocf > 0 else None,
           'definition': 'operating cash flow minus capex as supplied; check issuer FCF definition separately'}
    base = norm['normalized_net_income'] if norm else cur.get('net_income')
    if base and base > 0:
        out['fcf_to_normalized_net_income'] = fcf / base
        if fcf / base < .6:
            flags.append(('weak_cash_conversion', 'medium', 'התזרים החופשי נמוך בהרבה מהרווח המנורמל ברבעון; לבדוק הון חוזר והשקעות.'))
    if ocf > 0 and capex / ocf > .5:
        flags.append(('investment_phase', 'info',
                      'יותר ממחצית התזרים התפעולי הולך להשקעות; תזרים חופשי נמוך זמנית אינו בהכרח חולשה, אבל הוא תלוי בתשואה על ההשקעה.'))
    return out


def _liquidity(bal, flags):
    if not bal:
        return None
    allowed = {'cash', 'short_term_investments', 'restricted_cash', 'insurance_reserves',
               'customer_funds', 'debt', 'convertible_debt', 'source_ids'}
    if set(bal) - allowed:
        raise ValueError('balance: unknown field')
    _sources(bal, 'balance')
    v = {k: _num(bal.get(k), 'balance.' + k, 0) or 0.0 for k in allowed - {'source_ids'}}
    split = 'convertible_debt' in bal
    spendable = v['cash'] + v['short_term_investments']
    tied = v['restricted_cash'] + v['insurance_reserves'] + v['customer_funds']
    debt = v['debt'] + v['convertible_debt']
    if tied:
        flags.append(('restricted_not_spendable', 'info',
                      'מזומן מוגבל, עתודות ביטוח וכספי לקוחות מוצגים בנפרד: הם מגבים התחייבויות ואינם נזילות פנויה.'))
    return {'spendable_liquidity': spendable, 'restricted_or_held_for_others': tied, 'total_debt': debt,
            'net_cash': spendable - debt, 'convertible_share_of_debt': v['convertible_debt'] / debt if debt and split else None}


def _capital_return(cur, py, market, flags):
    out = {}
    if cur.get('diluted_shares') and py.get('diluted_shares'):
        out['diluted_share_change_yoy'] = cur['diluted_shares'] / py['diluted_shares'] - 1
        if out['diluted_share_change_yoy'] > .03:
            flags.append(('dilution', 'medium', f"מספר המניות המדולל עלה {out['diluted_share_change_yoy']:.1%} בשנה; חלק מהצמיחה לא שייך למחזיק הקיים."))
    if cur.get('buybacks') is not None and cur.get('sbc') is not None:
        out['buybacks_minus_sbc'] = cur['buybacks'] - cur['sbc']
    cap = market.get('market_cap') if market else None
    if cap and cur.get('buybacks') is not None:
        out['quarter_buyback_to_market_cap'] = cur['buybacks'] / cap
        out['annualized_buyback_yield_run_rate'] = 4 * cur['buybacks'] / cap
    return out or None


def _segments(segs, cur, flags):
    rows = []
    total = sum(number(s['profit'], 'segment profit') for s in segs if s.get('profit') is not None and s['profit'] > 0)
    for i, s in enumerate(segs):
        _sources(s, f'segments[{i}]')
        rev, prev = _num(s.get('revenue'), 'segment revenue', 0), _num(s.get('prior_revenue'), 'segment prior revenue', 0)
        p, pp = _num(s.get('profit'), 'segment profit'), _num(s.get('prior_profit'), 'segment prior profit')
        m, pm = _margin(p, rev), _margin(pp, prev)
        row = {'name': s['name'], 'revenue_growth': _growth(rev, prev), 'profit_growth': _growth(p, pp),
               'margin': m, 'margin_change': m - pm if m is not None and pm is not None else None,
               'share_of_positive_profit': p / total if p is not None and p > 0 and total else None,
               'profit_definition': s.get('profit_definition', 'unspecified')}
        if row['margin_change'] is not None and row['margin_change'] < -.03:
            flags.append(('segment_margin_compression', 'medium', f"שולי הרווח של {s['name']} נשחקו ב-{-100 * row['margin_change']:.0f} נקודות אחוז."))
        if row['revenue_growth'] is not None and row['revenue_growth'] < 0:
            flags.append(('segment_shrinking', 'medium', f"ההכנסות של {s['name']} מתכווצות."))
        rows.append(row)
    top = max((r for r in rows if r['share_of_positive_profit']), key=lambda r: r['share_of_positive_profit'], default=None)
    if top and top['share_of_positive_profit'] > .7 and len(rows) > 1:
        flags.append(('profit_concentration', 'medium', f"{top['name']} מייצר יותר מ-70% מהרווח המגזרי; הסיפור הרחב נשען בפועל על מגזר אחד."))
    return rows


def _kpis(kpis, flags):
    rows = []
    for i, k in enumerate(kpis):
        _sources(k, f'kpis[{i}]')
        if k.get('basis') not in KPI_BASES:
            raise ValueError(f'kpis[{i}].basis must be one of ' + ', '.join(sorted(KPI_BASES)))
        v, p = number(k['value'], 'kpi value'), _num(k.get('prior'), 'kpi prior')
        rows.append({'name': k['name'], 'value': v, 'growth': _growth(v, p), 'basis': k['basis']})
        if k['basis'] == 'cumulative':
            flags.append(('cumulative_kpi', 'medium',
                          f"המדד '{k['name']}' מצטבר (כולל מה שכבר נסגר/נפרע); הוא לא מוכיח פעילות נוכחית."))
    return rows


def _guidance(items, flags):
    rows = []
    for i, g in enumerate(items):
        label = f'guidance[{i}]'
        _sources(g, label)
        low, high = number(g['low'], label + '.low'), number(g['high'], label + '.high')
        if high < low:
            raise ValueError(label + ': high below low')
        mid = (low + high) / 2
        row = {'metric': g['metric'], 'period': g.get('period'), 'midpoint': mid}
        if g.get('prior_low') is not None and g.get('prior_high') is not None:
            prior = (number(g['prior_low'], label + '.prior_low') + number(g['prior_high'], label + '.prior_high')) / 2
            row['change_vs_prior_guidance'] = _growth(mid, prior) if prior > 0 else mid - prior
            row['direction'] = 'raised' if mid > prior * 1.005 else ('cut' if mid < prior * .995 else 'maintained')
        if g.get('comparable_actual') is not None:
            row['implied_growth'] = _growth(mid, number(g['comparable_actual'], label + '.comparable_actual'))
        if g.get('consensus') is not None:
            if not g.get('consensus_source_ids'):
                raise ValueError(label + ': consensus needs consensus_source_ids (attributed estimate)')
            c = number(g['consensus'], label + '.consensus')
            row['vs_consensus'] = _growth(mid, c) if c > 0 else mid - c
            if c > 0 and mid < c * .99:
                flags.append(('guide_below_consensus', 'medium',
                              f"תחזית {g['metric']} (אמצע הטווח) נמוכה מציפיות האנליסטים; מסביר ירידה גם אחרי רבעון טוב."))
        if row.get('direction') == 'maintained' and g.get('related_raised_metric'):
            flags.append(('reinvesting_upside', 'info',
                          f"{g['related_raised_metric']} הועלה אבל {g['metric']} נשאר: ההנהלה משקיעה את העודף ולא מעבירה אותו לשורה התחתונה."))
        rows.append(row)
    return rows


def _track(track, flags):
    if not track:
        return None
    surprises = []
    for i, t in enumerate(track):
        _sources(t, f'guidance_track[{i}]')
        if not t.get('same_basis', False):
            raise ValueError(f'guidance_track[{i}]: same_basis must confirm matching scope, accounting and period')
        mid = (number(t['low'], 'low') + number(t['high'], 'high')) / 2
        actual = number(t['actual'], 'actual')
        # A guided growth rate is compared in percentage points, an amount in relative terms.
        surprises.append(actual - mid if t.get('unit') == 'rate' else (_growth(actual, mid) if mid > 0 else None))
    s = [x for x in surprises if x is not None]
    beats = sum(1 for x in s if x > .005)
    out = {'periods': len(track), 'beats': beats, 'mean_surprise_vs_midpoint': sum(s) / len(s) if s else None,
           'pattern': 'conservative' if s and beats == len(s) else ('mixed' if beats else 'misses_or_in_line'),
           'small_sample': len(track) < 4}
    if out['pattern'] == 'conservative':
        flags.append(('conservative_guider', 'info',
                      f"ההנהלה עקפה את התחזית שלה ב-{beats}/{len(s)} תקופות" + (' (מדגם קטן)' if out['small_sample'] else '')
                      + '; התחזית מתנהגת כרצפה, אבל אין להניח עקיפה אוטומטית.'))
    return out


def _peak_exception(cur, py, om, hist, median, econ):
    """The identity-based peak exception (see PEAK_TESTS above): both readings' numbers, the conditions and why it did not
    apply. Only called when the peak fired and case.economics was supplied."""
    rules = econ['rules']
    tol, smin, near = rules['identity_tolerance'], rules['leverage_share_min'], rules['near_threshold']
    tests = []
    for (test, label), thr in zip(PEAK_TESTS, (hist[-1], median + .15)):
        row = {'test': test, 'label': label, 'threshold': thr, 'fired': om > thr, 'above_threshold': om - thr}
        if abs(om - thr) <= near:
            row['near_threshold'] = near
        tests.append(row)
    out = {'rule': 'operating_leverage_or_peak', 'peak_tests': tests, 'leverage_share_min': smin,
           'model': econ['selection'], 'model_evidence': econ['model_evidence'], 'price_cyclical_models': econ['cyclical_models'],
           'card_types': econ['card_types'], 'cyclical_card_types': econ['cyclical_card_types']}
    reasons = []
    need = [f'{blk}.{k}' for blk, lines in (('current', cur), ('prior_year', py))
            for k in ('revenue', 'gross_profit', 'operating_expenses', 'operating_income') if lines.get(k) is None]
    if not need and py['revenue'] <= 0:
        need.append('prior_year.revenue (positive)')
    if need:
        reasons.append('gross_profit_and_opex_missing: needs ' + ', '.join(need))
    else:
        per = {}
        for blk, L in (('current', cur), ('prior_year', py)):
            rev, oi = L['revenue'], L['operating_income']
            residual = oi - (L['gross_profit'] - L['operating_expenses'])
            per[blk] = {'gross_margin': L['gross_profit'] / rev, 'opex_ratio': L['operating_expenses'] / rev,
                        'operating_margin': oi / rev, 'identity_residual': residual,
                        'reconciles': abs(residual) <= tol * abs(oi)}
        c, p = per['current'], per['prior_year']
        d_om, d_gm, d_x = (c[k] - p[k] for k in ('operating_margin', 'gross_margin', 'opex_ratio'))
        out['decomposition'] = {
            **per, 'window': 'y/y (current vs prior_year)', 'delta_operating_margin': d_om, 'delta_gross_margin': d_gm,
            'delta_opex_ratio': d_x, 'residual': d_om - (d_gm - d_x),
            'identity': 'OM = GM - opex/revenue (gross profit - operating expenses = operating income)',
            'reconcile_tolerance': f'|operating income - (gross profit - operating expenses)| <= rules.identity_tolerance ({tol}) '
                                   'x |operating income|, in both periods'}
        if not (c['reconciles'] and p['reconciles']):
            bad = [blk for blk in per if not per[blk]['reconciles']]
            reasons.append('identity_not_reconciled: gross profit - operating expenses differs from operating income in '
                           + ', '.join(bad) + ': the supplied opex is not the operating cost base, so the split does not hold')
        elif d_om <= 0:
            reasons.append('no_margin_expansion: the operating margin did not rise year on year; there is no rise to attribute')
        else:
            share = -d_x / d_om
            out.update(leverage_share=share, derived_from='-delta_opex_ratio / delta_operating_margin (current vs prior_year)')
            if abs(share - smin) <= near:
                out['leverage_share_near_threshold'] = near
            if share < smin:
                reasons.append(f'leverage_share_below_min: {share:.3f} < rules.leverage_share_min {smin}: the rise came mostly '
                               'from gross margin (price or mix)')
    if not econ['models']:
        reasons.append('model_not_known: case.economics names no economic model; the exception needs one that is not '
                       'price-cyclical')
    elif not econ['cyclical_models'] and not econ['model_evidence']['evidenced']:     # a cyclical model keeps it high anyway
        reasons.append('model_not_evidenced: ' + econ['model_evidence']['reason'])
    if econ['cyclical_models']:
        reasons.append('price_cyclical_model: ' + ', '.join(econ['cyclical_models']) + ' (registry price_cyclical): a record '
                       'margin is read as the cycle')
    if econ['cyclical_card_types']:
        reasons.append('cyclical_card_type: ' + ', '.join(econ['cyclical_card_types']) + ' (model_card.CYCLICAL_TYPES)')
    applies = not reasons
    out.update(applies=applies, flag='operating_leverage_or_peak' if applies else 'peak_margin_run_rate',
               severity=econ['exception_severity'] if applies else 'high', reasons=reasons)
    return out


def _exception_he(om, median, hist, exc):
    d = exc['decomposition']
    ref = (f'מעל השיא ההיסטורי של {hist[-1]:.1%}' if exc['peak_tests'][0]['fired']
           else f'מעל החציון ההיסטורי ({median:.1%}) ועוד 15 נקודות ({median + .15:.1%})')
    return (f"שולי רווח תפעולי של {om:.1%}, {ref}. אבל כ-{exc['leverage_share']:.0%} מהעלייה של "
            f"{100 * d['delta_operating_margin']:.1f} נקודות אחוז בשנה באה מהוצאות שגדלו לאט מההכנסות (יחס ההוצאות להכנסות "
            f"{100 * d['delta_opex_ratio']:+.1f} נקודות, שולי הרווח הגולמי {100 * d['delta_gross_margin']:+.1f}). שתי קריאות: "
            'מינוף תפעולי שנשאר כל עוד ההכנסות ממשיכות לגדול, או רווח שיא שיתכווץ אם הצמיחה תיעצר; רבעון כפול 4 עדיין אינו רווח מנורמל.')


def _cycle(cur, history_margins, flags, py=None, econ=None):
    om = _margin(cur.get('operating_income'), cur.get('revenue'))
    if om is None or not history_margins:
        return None
    hist = sorted(number(m, 'history_operating_margins', -10, 1) for m in history_margins)
    median = hist[len(hist) // 2]
    peak = om > max(hist) or om > median + .15
    exc = _peak_exception(cur, py or {}, om, hist, median, econ) if peak and econ is not None else None
    if exc and exc['applies']:
        flags.append((exc['flag'], exc['severity'], _exception_he(om, median, hist, exc)))
    elif peak:
        flags.append(('peak_margin_run_rate', 'high',
                      f'שולי רווח תפעולי של {om:.0%} מול חציון היסטורי של {median:.0%}: רווח רבעוני כפול 4 הוא רווח שיא, לא רווח מנורמל לאורך מחזור.'))
    out = {'operating_margin': om, 'historical_median': median, 'historical_max': hist[-1], 'above_history': peak}
    if exc is not None:
        out['peak_exception'] = exc
    return out


def _market(m, cur, norm, cycle, flags):
    if not m:
        return None
    _sources(m, 'market')
    date.fromisoformat(m['price_as_of'])
    price = number(m['price'], 'market.price', 1e-12)
    out = {'price': price, 'price_as_of': m['price_as_of']}
    shares = _num(m.get('shares_outstanding'), 'market.shares_outstanding', 1)
    if shares:
        out['market_cap'] = price * shares
    eps = norm['normalized_eps'] if norm else None
    if eps:
        out['run_rate_eps'] = 4 * eps
        out['pe_on_run_rate'] = price / (4 * eps) if eps > 0 else None
        out['run_rate_basis'] = 'peak_quarter_x4' if cycle and cycle['above_history'] else 'quarter_x4'
    fwd = []
    for i, f in enumerate(m.get('forward_eps') or []):
        if f.get('origin') not in {'consensus', 'own_model'}:
            raise ValueError(f'forward_eps[{i}].origin must be consensus or own_model')
        _sources(f, f'forward_eps[{i}]')
        e = number(f['value'], 'forward eps')
        row = {'year': f['year'], 'eps': e, 'origin': f['origin'], 'pe': price / e if e > 0 else None}
        if f.get('growth') is not None and e > 0 and number(f['growth'], 'growth') > 0:
            row['peg'] = row['pe'] / (100 * f['growth'])
        fwd.append(row)
    if fwd:
        out['forward'] = fwd
    dual = m.get('other_listing')
    if dual:
        _sources(dual, 'other_listing')
        equivalent = number(dual['price_local'], 'price_local', 1e-12) / number(dual['local_per_quote_currency'], 'fx', 1e-12) \
            * number(dual['local_shares_per_unit'], 'local_shares_per_unit', 1e-12)
        out['other_listing'] = {'equivalent_price': equivalent, 'premium': price / equivalent - 1, 'name': dual.get('name')}
        if abs(price / equivalent - 1) > .1:
            flags.append(('listing_premium', 'high' if price / equivalent - 1 > .2 else 'medium',
                          f"קו המסחר הזה נסחר ב-{price / equivalent - 1:+.0%} מול אותה מניה בבורסת הבית: משלמים מחיר שונה על אותה זכות כלכלית. הפער יכול להיסגר."))
    return out


def _scenarios(m, price, flags):
    """EPS x exit multiple tables are dated sensitivities. Show implied annual returns."""
    rows = []
    for i, s in enumerate(m.get('multiple_scenarios') or []):
        _sources(s, f'multiple_scenarios[{i}]')
        years = (date.fromisoformat(s['target_date']) - date.fromisoformat(m['price_as_of'])).days / 365.25
        if years <= 0:
            raise ValueError('multiple_scenarios target_date must follow price_as_of')
        eps = number(s['eps'], 'scenario eps', 1e-12)
        for pe in s['multiples']:
            val = eps * number(pe, 'multiple', 1e-12)
            row = {'name': s.get('name'), 'target_date': s['target_date'], 'eps': eps, 'multiple': pe, 'value_at_date': val,
                   'implied_annual_return': (val / price) ** (1 / years) - 1}
            if s.get('cost_of_equity') is not None:
                row['discounted_today'] = val / (1 + number(s['cost_of_equity'], 'cost_of_equity', 0, 1)) ** years
            rows.append(row)
    if rows and not any(s.get('multiple_basis_source_ids') for s in m.get('multiple_scenarios') or []):
        flags.append(('unanchored_multiples', 'medium',
                      'המכפילים בתרחישים לא מעוגנים בהיסטוריה/עמיתים מתועדים; זו רגישות, לא שווי.'))
    return rows


def _bank(b, price, flags):
    if not b:
        return None
    _sources(b, 'bank')
    tbv = number(b['tangible_book_per_share'], 'tangible_book_per_share', 1e-12)
    ptbv = price / tbv
    out = {'price_to_tangible_book': ptbv}
    if b.get('rote') is not None and b.get('cost_of_equity') is not None and b.get('growth') is not None:
        rote, ke, g = (number(b[k], 'bank.' + k, -1, 1) for k in ('rote', 'cost_of_equity', 'growth'))
        if ke <= g:
            raise ValueError('bank: cost_of_equity must exceed long-run growth')
        out['justified_price_to_tangible_book'] = (rote - g) / (ke - g)
        out['rote_implied_by_price'] = g + ptbv * (ke - g)
        out['justified_price'] = tbv * out['justified_price_to_tangible_book']
        out['note'] = 'Steady-state (ROTE-g)/(COE-g); a check on what the price requires, not a full bank valuation.'
        if out['rote_implied_by_price'] > rote + .03:
            flags.append(('bank_price_requires_higher_rote', 'high',
                          f"המחיר מגלם תשואה על הון מוחשי של כ-{out['rote_implied_by_price']:.0%} מול {rote:.1%} בפועל: השוק כבר משלם על היעד ולא על המצב."))
    if b.get('capital_ratio') is not None and b.get('target_ratio') is not None and b.get('risk_weighted_assets') is not None:
        excess = (number(b['capital_ratio'], 'capital_ratio', 0, 1) - number(b['target_ratio'], 'target_ratio', 0, 1)) \
            * number(b['risk_weighted_assets'], 'risk_weighted_assets', 0)
        out['excess_capital_vs_target'] = excess
        if b.get('minimum_ratio') is not None:
            out['headroom_to_minimum'] = b['capital_ratio'] - number(b['minimum_ratio'], 'minimum_ratio', 0, 1)
    if b.get('fair_value_marks') is not None and b.get('equity'):
        out['fair_value_marks_to_equity'] = number(b['fair_value_marks'], 'fair_value_marks') / number(b['equity'], 'equity', 1e-12)
        if out['fair_value_marks_to_equity'] > .15:
            flags.append(('equity_from_marks', 'medium',
                          f"כ-{out['fair_value_marks_to_equity']:.0%} מההון העצמי הוא שערוך שווי הוגן ולא מזומן; תלוי בהנחות."))
    return out


def _sensitivities(items):
    rows = []
    for i, s in enumerate(items):
        _sources(s, f'sensitivities[{i}]')
        impact, base = number(s['annual_impact'], 'annual_impact'), _num(s.get('base_annual'), 'base_annual', 1e-12)
        rows.append({'driver': s['driver'], 'annual_impact': impact, 'share_of_base': impact / base if base else None,
                     'offset_growth_needed': -impact / base if base and impact < 0 else None})
    return rows


def _econ_value(ref, econ_rows, label):
    """The current value of a falsifier bound to an econ falsifier row (econ {id, scope, field}) and the link shown with it.
    No value (the KPI not supplied, the field not stated) gives None and the econ status; nothing is filled in."""
    if econ_rows is None:
        raise ValueError(f'{label}.econ: needs case.economics (the econ review the value is read from)')
    if not isinstance(ref, dict) or not isinstance(ref.get('id'), str) or not ref['id'] or set(ref) - {'id', 'scope', 'field'}:
        raise ValueError(f'{label}.econ: {{id, scope, field}} naming an econ falsifier row')
    if ref.get('field') not in ECON_FALSIFIER_FIELDS:
        raise ValueError(f'{label}.econ.field: one of {ECON_FALSIFIER_FIELDS} (the number the threshold applies to)')
    scope = ref.get('scope', 'total')
    hits = [r for r in econ_rows if r['id'] == ref['id'] and r['scope'] == scope]
    link = {'id': ref['id'], 'scope': scope, 'field': ref['field']}
    if not hits:
        known = sorted({r['id'] for r in econ_rows})
        if ref['id'] not in known:
            raise ValueError(f"{label}.econ.id: {ref['id']!r} is not a falsifier of the selected economic model(s) {known}")
        return None, {**link, 'status': 'missing'}
    values = {r.get(ref['field']) for r in hits} - {None}
    if len(values) > 1:
        raise ValueError(f"{label}.econ: {ref['id']} ({scope}) has several {ref['field']} values {sorted(values)}; name one scope")
    row = next((r for r in hits if r.get(ref['field']) is not None), hits[0])
    link.update(status=row['status'], kind=row['kind'], tag=row.get('tag'), window=row.get('window'),
                derived_from=row.get('derived_from'))
    if row.get('from_period'):
        link['unobservable_from'] = row['from_period']
    return row.get(ref['field']), link


def _falsifiers(items, flags, econ_rows=None):
    rows, bound = [], set()
    for i, f in enumerate(items):
        if f.get('direction') not in {'below', 'above'}:
            raise ValueError(f'falsifiers[{i}].direction must be below or above')
        link = None
        if f.get('econ') is not None:
            if f.get('current') is not None:
                raise ValueError(f'falsifiers[{i}]: current and econ both given; the value comes from one place')
            cur, link = _econ_value(f['econ'], econ_rows, f'falsifiers[{i}]')
            bound.add((link['id'], link['scope']))
            if cur is None:
                rows.append({'id': f['id'], 'metric': f['metric'], 'current': None,
                             'threshold': number(f['threshold'], 'falsifier threshold'), 'status': link['status'], 'econ': link})
                continue
            cur = number(cur, 'falsifier current')
        else:
            cur = number(f['current'], 'falsifier current')
        thr = number(f['threshold'], 'falsifier threshold')
        broken = cur < thr if f['direction'] == 'below' else cur > thr
        prior = _num(f.get('prior'), 'falsifier prior')
        moving_toward = prior is not None and ((cur < prior) if f['direction'] == 'below' else (cur > prior))
        status = 'broken' if broken else ('weakening' if moving_toward else 'intact')
        rows.append({'id': f['id'], 'metric': f['metric'], 'current': cur, 'threshold': thr, 'status': status})
        if link:
            rows[-1]['econ'] = link
        if status == 'broken':
            flags.append(('thesis_falsifier_broken', 'high', f"תנאי ההפרכה '{f['metric']}' נחצה: התזה דורשת בחינה מחדש, לא הסבר."))
        elif status == 'weakening':
            flags.append(('thesis_falsifier_weakening', 'medium', f"'{f['metric']}' זז לכיוון סף ההפרכה."))
    for r in econ_rows or []:          # the model's falsifiers no threshold is bound to: tracked every quarter, not judged
        if (r['id'], r['scope']) in bound:
            continue
        rows.append({'id': f"econ.{r['id']}", 'metric': r['id'].replace('_', ' ') + (f" ({r['scope']})" if r['scope'] else ''),
                     'current': r.get('value'), 'threshold': None, 'status': r['status'],
                     'econ': {k: r[k] for k in ('id', 'scope', 'kind', 'tag', 'window', 'growth', 'change', 'from_period',
                                                'derived_from') if r.get(k) is not None}})
    return rows


def _model_evidence(E, built, cards, mids):
    """Is the model named in case.economics evidenced, so that it may lower the peak flag? (MODEL_OVERRIDE_KEYS above.)
    An override (economics.model {primary, secondary?, reason, evidence_ids}, or a select() output carrying one) is
    checked by econ.select_case: a missing reason, unknown or not yet available ids, or ids of a refused kind raise.
    Otherwise econ.select_case's rule on this subset (economics.card_type and its KPI facts) must select the same models."""
    if not mids:
        return {'evidenced': False, 'basis': None, 'reason': 'no model named'}
    m = built.get('model')
    ov = m['override'] if isinstance(m, dict) and isinstance(m.get('override'), dict) else m
    if isinstance(ov, dict) and (ov.get('evidence_ids') is not None or ov.get('reason') is not None):
        sel = E.select_case(built, card_type=cards or None,
                            override={k: ov[k] for k in MODEL_OVERRIDE_KEYS if ov.get(k) is not None})
        o = sel['override']
        out = {'basis': 'analyst_override', 'reason': o['reason'], 'evidence_ids': o['evidence_ids'],
               'evidence_kinds': o.get('evidence_kinds'), 'agrees_with_rule': o['agrees_with_rule'],
               'rule': {'primary': sel['rule']['primary'], 'secondary': sel['rule']['secondary']}, 'checked_by': 'econ.select_case'}
        if [o['primary']] + o['secondary'] == mids:
            return {**out, 'evidenced': True}
        return {**out, 'evidenced': False, 'reason': f"the override selects {', '.join([o['primary']] + o['secondary'])}, "
                                                     f"economics.model names {', '.join(mids)}"}
    sel = E.select_case(built, card_type=cards or None)
    rule = [sel['primary']] + sel['secondary'] if sel['status'] == E.SELECTED else []
    out = {'rule': {'primary': sel['primary'], 'secondary': sel['secondary'], 'reasons': sel['reasons']},
           'checked_by': 'econ.select_case'}
    if rule and rule[0] == mids[0] and set(rule) == set(mids):
        return {**out, 'evidenced': True, 'basis': 'rule'}
    return {**out, 'evidenced': False, 'basis': None,
            'reason': f"{', '.join(mids)} named without evidence; the rule on this subset (card type "
                      f"{', '.join(cards) or 'not given'} and its KPI facts) selects {', '.join(rule) or 'no model (unclassified)'}: "
                      'state economics.model as {primary, reason, evidence_ids} citing an issuer document, or give '
                      'economics.card_type with the model\'s KPI facts'}


def _economics(case):
    """case['economics'] -> the econ review of the subset and what the peak exception needs from it; None when absent."""
    sub = case.get('economics')
    if sub is None:
        return None
    from . import econ as E, model_card as M          # lazily: econ imports this module (quarter._bank)
    if not isinstance(sub, dict):
        raise ValueError('economics: the econ case subset (an object; see econ.review)')
    sub = dict(sub)
    for k in ECON_INHERITED:
        if k in sub and sub[k] != case[k]:
            raise ValueError(f'economics.{k}: {sub[k]!r} differs from the quarter case ({case[k]!r}); the subset inherits it')
    if sub.get('period', ECON_PERIOD) != ECON_PERIOD:
        raise ValueError(f"economics.period: quarter_review reads one quarter ({ECON_PERIOD!r})")
    sources = case.get('sources') or {}
    extra = sub.get('sources') or {}
    if not isinstance(sources, dict) or not isinstance(extra, dict):
        raise ValueError('sources: {id: {kind, available_at}}')
    for sid, s in extra.items():                       # one id, one source: its kind and date must agree (notes may differ)
        if sid in sources and any((a.get(k) if isinstance(a, dict) else a) != (s.get(k) if isinstance(s, dict) else s)
                                  for a in (sources[sid],) for k in ('kind', 'available_at')):
            raise ValueError(f'economics.sources.{sid}: kind or available_at differs from the quarter case sources')
    cards = E._card_types(sub.pop('card_type', None), M)
    built = {**sub, **{k: case[k] for k in ECON_INHERITED}, 'period': ECON_PERIOD,
             'sources': {**extra, **sources}}              # the quarter case's entry is kept for a shared id
    review = E.review(built)
    owned = [r for r in review['checks_not_run'] if r['check'] in QUARTER_OWNED_CHECKS]
    review['checks_not_run'] = [r for r in review['checks_not_run'] if r['check'] not in QUARTER_OWNED_CHECKS]
    review['checks_run_by_quarter'] = [{'check': r['check'], 'model': r['model'], 'where': 'cycle'} for r in owned]
    sel = review['model']
    mids = [m for m in ([sel['primary']] + sel['secondary'] if sel else []) if m != E.UNCLASSIFIED]
    review['quarter_link'] = {
        'inherited': list(ECON_INHERITED) + ['period'], 'sources': 'case.sources merged with economics.sources',
        'card_type': cards, 'flags': 'merged into flags as econ.<key>; the numbers stay here',
        'falsifiers': 'fed to falsifiers: bound by econ {id, scope, field}, else tracked without a threshold'}
    return {'review': review, 'selection': sel, 'models': mids, 'card_types': cards,
            'model_evidence': _model_evidence(E, built, cards, mids),
            'cyclical_models': [m for m in mids if E.model(m)['price_cyclical']],
            'cyclical_card_types': [t for t in cards if t in M.CYCLICAL_TYPES], 'rules': E.rules(),
            'exception_severity': E.registry()['checks']['operating_leverage_or_peak']['severity']}


def _econ_flags(review):
    """The econ review's flags as quarter flags under econ.<key>: they never take one of quarter's own keys."""
    out = []
    for f in review['flags']:
        where = f.get('segment') or (f['scope'] if f.get('scope') not in (None, 'total') else None)
        he = ECON_FLAG_HE.get(f['key'], f"בדיקה כלכלית '{f['key']}': ראו economics.flags.")
        out.append((f"econ.{f['key']}", f['severity'], f'{where}: {he}' if where else he))
    return out


def _currency(case):
    rc, cur = case.get('reporting_currency', case['currency']), case['currency']
    if rc == cur:
        return None
    fx = case.get('fx')
    if not fx:
        raise ValueError('reporting_currency differs from currency: supply fx {rate, quote, as_of, source_ids}')
    _sources(fx, 'fx')
    date.fromisoformat(fx['as_of'])
    return {'reporting_currency': rc, 'presented_in': cur, 'rate': number(fx['rate'], 'fx.rate', 1e-12),
            'quote': fx.get('quote'), 'as_of': fx['as_of'],
            'note': 'Values converted by the caller at one dated rate; USD growth mixes business and FX moves.'}


def _text(case, r):
    """One short Hebrew paragraph in the user's preferred style."""
    parts = [f"{case['company']} ({case['period_end']}):"]
    g = r['growth'].get('revenue_yoy')
    if g is not None:
        parts.append(f"ההכנסות צמחו {g:.0%} בשנה.")
    n = r.get('normalization')
    if n and n['removed']:
        parts.append(f"אחרי ניקוי חד-פעמיים הרווח הנקי הוא {n['normalized_net_income'] / 1e9:,.2f} מיליארד ולא {n['reported_net_income'] / 1e9:,.2f}.")
    lev = r.get('operating_leverage')
    if lev:
        parts.append('ההוצאות צומחות לאט מההכנסות.' if lev['spread_ex_unusual'] > 0 else 'ההוצאות צומחות מהר מההכנסות.')
    for g in r.get('guidance', []):
        if g.get('direction'):
            parts.append(f"תחזית {g['metric']}: {dict(raised='הועלתה', cut='הורדה', maintained='נשארה')[g['direction']]}"
                         + (f", {g['vs_consensus']:+.0%} מול הקונצנזוס" if g.get('vs_consensus') is not None else '') + '.')
            break
    high = [f for f in r['flags'] if f['severity'] == 'high']
    if high:
        parts.append('נקודות מרכזיות: ' + ' '.join(f['he'] for f in high[:2]))
    parts.append('המחיר זז לפי הפער מול הציפיות ולא לפי איכות הדוח בלבד; זו סקירת דוח ולא הערכת שווי או המלצה.')
    return ' '.join(parts)


def quarter_review(case):
    for k in ('company', 'period_end', 'as_of', 'currency'):
        if not case.get(k):
            raise ValueError(f'{k} required')
    if date.fromisoformat(case['period_end']) > date.fromisoformat(case['as_of']):
        raise ValueError('period_end after as_of')
    _sources(case, 'case')
    cur, py, pq = _lines(case.get('current'), 'current'), _lines(case.get('prior_year'), 'prior_year'), _lines(case.get('prior_quarter'), 'prior_quarter')
    if cur.get('revenue') is None:
        raise ValueError('current.revenue required')
    econ = _economics(case)
    raw = []
    growth = {}
    for k in ('revenue', 'gross_profit', 'operating_income', 'adjusted_ebitda', 'operating_cash_flow'):
        growth[k + '_yoy'] = _growth(cur.get(k), py.get(k))
        growth[k + '_qoq'] = _growth(cur.get(k), pq.get(k))
    margins = {k: _margin(cur.get(k), cur['revenue']) for k in ('gross_profit', 'operating_income', 'adjusted_ebitda', 'net_income')}
    margins_prior = {k: _margin(py.get(k), py.get('revenue')) for k in margins}
    norm = _normalize(cur, case.get('one_offs') or [], case.get('unusual_opex') or [], raw)
    eg, og = _growth(cur.get('adjusted_ebitda'), py.get('adjusted_ebitda')), _growth(cur.get('operating_income'), py.get('operating_income'))
    if eg is not None and og is not None and eg - og > .15:
        raw.append(('adjusted_vs_reported_gap', 'medium',
                    f'EBITDA מתואם צמח {eg:.0%} מול רווח תפעולי מדווח {og:.0%}: לבדוק מה מוחרג (SBC, פחת, משפטי) ואם זה חוזר.'))
    cycle = _cycle(cur, case.get('history_operating_margins'), raw, py, econ)
    market = _market(case.get('market'), cur, norm, cycle, raw)
    result = {
        'company': case['company'], 'period_end': case['period_end'], 'as_of': case['as_of'], 'currency': case['currency'],
        'currency_conversion': _currency(case),
        'growth': {k: v for k, v in growth.items() if v is not None},
        'margins': {k: v for k, v in margins.items() if v is not None},
        'margin_change_yoy': {k: margins[k] - margins_prior[k] for k in margins if margins[k] is not None and margins_prior[k] is not None},
        'normalization': norm,
        'operating_leverage': _leverage(cur, py, case.get('unusual_opex') or [], raw),
        'cash': _cash(cur, norm, raw),
        'liquidity': _liquidity(case.get('balance'), raw),
        'cycle': cycle,
        'market': market,
        'capital_return': _capital_return(cur, py, market, raw),
        'segments': _segments(case.get('segments') or [], cur, raw),
        'kpis': _kpis(case.get('kpis') or [], raw),
        'guidance': _guidance(case.get('guidance') or [], raw),
        'guidance_track': _track(case.get('guidance_track') or [], raw),
        'bank': _bank(case.get('bank'), market['price'], raw) if case.get('bank') and market else None,
        'multiple_scenarios': _scenarios(case['market'], market['price'], raw) if market else [],
        'sensitivities': _sensitivities(case.get('sensitivities') or []),
        'falsifiers': _falsifiers(case.get('falsifiers') or [], raw, econ['review']['falsifiers'] if econ else None),
    }
    if econ:
        result['economics'] = econ['review']
    if case.get('bank') and not market:
        raise ValueError('bank checks need market.price')
    for i, c in enumerate(case.get('concentration') or []):
        _sources(c, f'concentration[{i}]')
        share = number(c['share'], 'concentration share', 0, 1)
        if share >= .3:
            raw.append(('customer_concentration', 'medium', f"{c['group']} = {share:.0%} מההכנסות: כוח מיקוח של מעטים מול החברה."))
    if not (case.get('guidance') or []) or not any(g.get('consensus') is not None for g in case.get('guidance') or []):
        raw.append(('no_expectations_data', 'info', 'אין נתון ציפיות מתועד; אי אפשר לקבוע אם התגובה במניה מוצדקת או מוגזמת.'))
    if econ:
        raw += _econ_flags(econ['review'])
    result['flags'] = sorted(({'key': k, 'severity': s, 'he': he} for k, s, he in raw), key=lambda f: SEVERITY[f['severity']])
    result['next_quarter_watch'] = [f['he'] for f in result['flags'] if f['severity'] != 'info'][:5]
    result['text'] = _text(case, result)
    result['input_sha256'] = sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result['engine_version'] = __version__
    result['boundary'] = ('Deterministic quarter review from caller-supplied, sourced figures. Not source verification, '
                          'not a valuation (use value_company), not a price forecast or order.')
    return result
