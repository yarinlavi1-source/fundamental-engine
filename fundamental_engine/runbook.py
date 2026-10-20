"""One run from discovery to a portfolio proposal, on data already collected.

The engine never calls a connector and never places an order. Data comes in through the collection scripts and the
session's connectors (runs/<universe>/raw, runs/<universe>/companies/<SYMBOL>/raw, the channels folder, a read-only
positions file); this module joins what they produced:

1. discovery: cards and research queue (fundamental_engine universe, with --channels) and the merged leads;
2. research: every company casefile already run (fundamental_engine deep: discovery queue, holdings, large-cap sweep);
3. calibration: the engine-consistent market return (calibration.json) as the index for every edge;
4. evidence: the walk-forward test of the engine (engine_backtest summary): its edge reliability shrinks every edge;
5. portfolio: allocation.construct against the current book (positions, economic-driver exposures), index by default;
6. report: one Hebrew report (what was searched, what the history says, the research list, the proposed book, what is
   missing), plan.json with every number, and the thesis records ready for the forward ledger.

Every proposal is illustrative research output: drafts stay provisional and no position changes without the owner's
explicit approval.
"""
import json
import os

from . import allocation, forecast, handoff, pipeline


def _load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def current_book(positions):
    """IBKR get_account_positions payload ({'positions': [...]}) -> {'weights', 'values', 'total', 'currency'} for stocks."""
    rows = [p for p in (positions or {}).get('positions') or [] if p.get('asset_class', 'STK') == 'STK' and p.get('market_value')]
    total = sum(p['market_value'] for p in rows)
    vals = {}
    for p in rows:
        s = (p.get('contract_description') or p.get('symbol') or '').upper()
        vals[s] = vals.get(s, 0.) + p['market_value']
    return {'weights': {s: v / total for s, v in vals.items()} if total else {}, 'values': vals, 'total': total,
            'currency': next((p.get('currency') for p in rows), None)}


def casefiles(folder, names):
    """{symbol: casefile} and {symbol: [origin]} for every queue name ('' = the discovery queue)."""
    comp = os.path.join(folder, 'companies')
    cfs, origin = {}, {}
    for n in names:
        qf = os.path.join(comp, f'QUEUE_{n}.json' if n else 'QUEUE.json')
        if not os.path.exists(qf):
            continue
        for e in _load(qf)['queue']:
            s = e.get('symbol')
            p = os.path.join(comp, s or '', 'out', 'casefile.json')
            if s and os.path.exists(p):
                origin.setdefault(s, []).append(n or f"discovery:{e.get('lane')}")
                if s not in cfs:
                    cfs[s] = _load(p)
    return cfs, origin


def run(folder, as_of, positions=None, exposures=None, names=('large500', 'holdings', ''), calibration=None, backtest=None,
        options=None, research_top=15):
    """-> {'plan', 'research', 'book', 'calibration', 'backtest', 'discovery', 'notes'} (see the module docstring)."""
    notes = []
    cfs, origin = casefiles(folder, names)
    cards = _load(os.path.join(folder, 'cards.json')) if os.path.exists(os.path.join(folder, 'cards.json')) else None
    rows = handoff.compare(list(cfs.values()), cards)['rows']
    cal = _load(calibration) if calibration and os.path.exists(calibration) else None
    mkt = (cal or {}).get('engine_market_return')
    if mkt is None:
        notes.append('no calibration: each edge uses the index return in its own decision (risk-free + equity premium)')
    cal_rf = (cal or {}).get('risk_free') or forecast.RF_DEFAULT
    for r in rows:
        r['origin'] = origin.get(r['symbol'])
        if mkt is not None:
            r['index_expected_return'] = mkt                  # one like-for-like index for every edge
            if r.get('currency') not in (None, 'USD') and r.get('risk_free') is not None:
                # a TWD or EUR return is compared with the market return in that currency: the USD market return plus the
                # rate gap (interest parity), so a low-rate currency is not penalised for its lower rates
                r['index_expected_return'] = mkt + r['risk_free'] - cal_rf
                r['index_currency_shift'] = r['risk_free'] - cal_rf
    bt = _load(backtest) if backtest and os.path.exists(backtest) else None
    rel = ((bt or {}).get('summary') or {}).get('edge_reliability') or {}
    reliability = min(max(rel.get('median') if rel.get('median') is not None else 1., 0.), 1.)
    if not bt:
        notes.append('no walk-forward test of the engine supplied: edges are taken at face value (reliability 1)')
    book = current_book(positions) if positions else {'weights': {}, 'values': {}, 'total': 0, 'currency': None}
    plan = allocation.construct(rows, book['weights'], exposures, {**(options or {}), 'edge_reliability': reliability})
    edge = lambda r: (r['irr'].get('base') - r['index_expected_return']) if r['irr'].get('base') is not None and r.get('index_expected_return') is not None else None
    research = sorted([r for r in rows if edge(r) is not None and not r.get('material_disqualifier')], key=lambda r: -edge(r))[:research_top]
    for r in research:
        r['edge_vs_market'] = edge(r)
    disc = None
    lp = os.path.join(folder, 'leads.json')
    if os.path.exists(lp):
        d = _load(lp)
        disc = {'channels': d.get('channels'), 'missing': d.get('missing'), 'prelisting': len(d.get('prelisting') or [])}
    return {'as_of': as_of, 'plan': plan, 'research': research, 'rows': rows, 'book': book, 'reliability': reliability,
            'calibration': cal and {k: cal.get(k) for k in ('engine_market_return', 'n_with_return', 'coverage_by_value', 'assumption')},
            'backtest': bt and bt.get('summary'), 'discovery': disc, 'cards': cards and len(cards.get('cards') or []), 'notes': notes}


def report_he(res):
    """The run in simple Hebrew: what was searched, what history says, the research list, the proposed book, what is missing."""
    p = lambda x, d=1: 'לא ידוע' if x is None else f'{x * 100:.{d}f}%'
    plan, bt = res['plan'], res.get('backtest') or {}
    out = [f"# סיכום ריצה — {res['as_of']}", '', '## מה נבדק', '']
    d = res.get('discovery') or {}
    if d:
        ch = {k: v for k, v in (d.get('channels') or {}).items() if not k.endswith('_read')}
        out.append(f"- ערוצי איתור: {', '.join(f'{k} ({v})' for k, v in ch.items())}; חסרים: {', '.join(d.get('missing') or []) or 'אין'}; "
                   f"{d.get('prelisting')} חברות לפני רישום למסחר במעקב.")
    out.append(f"- {res.get('cards') or 0} כרטיסי מועמדים; {len(res['rows'])} חברות עברו הערכת שווי מלאה (טיוטות: אין עדיין בודק בלתי תלוי).")
    if res.get('calibration'):
        c = res['calibration']
        out.append(f"- תשואת השוק לפי אותו מנוע: {p(c['engine_market_return'])} בשנה ({c['n_with_return']} חברות גדולות, "
                   f"{p(c['coverage_by_value'], 0)} מהשווי) — כל יתרון נמדד מולה.")
    out += ['', '## מה הבדיקה ההיסטורית אומרת', '']
    s = bt
    if s:
        top, ew = s.get('engine_top_quintile') or {}, s.get('universe_ew') or {}
        e = (top.get('excess_vs_universe_ew') or {})
        out.append(f"- החמישון העליון של המנוע: {p(top.get('cagr'))} בשנה מול {p(ew.get('cagr'))} למניה הממוצעת באותו יקום "
                   f"(יתרון ממוצע {p(e.get('mean'))}, t={e.get('t') or 0:.1f}) — לא מובהק; המדד: {p((s.get('benchmark') or {}).get('cagr'))}.")
        out.append(f"- חלק היתרון שהתממש בשנה טיפוסית: {res['reliability']:.2f} — ולכן כל יתרון מוכפל בו לפני קביעת גודל פוזיציה.")
        out.append('- המסקנה: המדד הוא ברירת המחדל; דירוג מכני לבדו אינו סיבה לקנות. פוזיציה פעילה צריכה מחקר מעמיק שנבדק.')
    else:
        out.append('- לא סופקה בדיקה היסטורית של המנוע: היתרונות נלקחים כפי שהם (ללא הנחה), ולכן הם אופטימיים.')
    out += ['', '## רשימת מחקר (לא רשימת קנייה)', '', '| מניה | מקור | יתרון על השוק | הפסד בתרחיש רע | ביטחון | סטטוס |', '|---|---|---|---|---|---|']
    for r in res['research']:
        out.append(f"| {r['symbol']} | {', '.join(r.get('origin') or [])} | {p(r['edge_vs_market'])} | {p(r.get('loss_to_bear'), 0)} | "
                   f"{r.get('confidence')} | {'טיוטה' if r.get('price_claim_withheld', True) else 'נבדק'} |")
    out += ['', '## התיק המוצע מול התיק הנוכחי והמדד', '', allocation.render_he(plan), '']
    if res['book']['total']:
        out.append(f"- שווי התיק הנוכחי (מניות): {res['book']['total']:,.0f} {res['book']['currency'] or ''}; קריאה בלבד.")
    out += ['', '## מה חסר', '']
    out += [f'- {n}' for n in res['notes']]
    out += ['- אין בודק בלתי תלוי: כל הערכת שווי היא טיוטה וכל משקל פעיל זמני.',
            '- זו תוצאת מחקר להמחשה. אין כאן פקודה; שום שינוי בתיק לא יבוצע בלי אישור מפורש שלך.']
    return '\n'.join(out)


def write(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    keep = {k: v for k, v in res.items() if k != 'rows'}
    with open(os.path.join(out_dir, 'plan.json'), 'w', encoding='utf-8') as f:
        json.dump(keep, f, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, 'report_he.md'), 'w', encoding='utf-8') as f:
        f.write(report_he(res))
    return os.path.join(out_dir, 'report_he.md')
