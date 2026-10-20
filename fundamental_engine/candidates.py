"""Candidate cards and the research queue: discovery output a person can act on, built from screen.scan.

A card answers, per company: why it surfaced, in which lane, what could make it exceptional, what is supported by
data and what is still a hypothesis, what would falsify the thesis, what the price already assumes, the main risks,
what data is missing, and whether it deserves the full (expensive) analysis. It adds no new scores: every statement
is read from the scan's dimensions, lens results and data flags, so the card cannot disagree with the screen.

The exploratory path admits a company that fails a numeric lens when written business evidence justifies a look
(an analyst or connector supplies it with dated sources); the numeric failures stay on the card.
The queue is a research priority, never a buy list.
"""
from .screen import LENSES, LENS_RULES, LENS_CHECKS, _fmt

LANES_HE = {
    'quality_compounder': 'צמיחה איכותית',
    'early_commercial_growth': 'צמיחה מוקדמת עם הוכחה מסחרית',
    'mispriced_turning': 'נקודת מפנה',
    'quality_fair_price': 'איכות במחיר סביר',
    'structural_tailwind': 'שינוי מבני או צוואר בקבוק',
    'recovery_mispriced': 'התאוששות או תמחור שגוי',
    'exploratory': 'ראיה עסקית (מסלול חקר)',
    'signal': 'אות חיצוני (קניות בעלי עניין / ערוץ גילוי)',
}
# what is NOT in screening data and must come from filings or research before a full conclusion, per lane
NOT_IN_SCREEN = {
    'quality_compounder': ['organic vs acquired growth split', 'customer concentration', 'reinvestment runway'],
    'early_commercial_growth': ['paid adoption, cohorts and retention', 'unit economics by product', 'runway to the next milestone'],
    'mispriced_turning': ['segment or driver behind the improvement', 'whether the improvement persists beyond one year'],
    'quality_fair_price': ["the market's concern behind the moderate price", 'moat evidence beyond margins'],
    'structural_tailwind': ['who captures the value (pricing power, supply response)', 'backlog or contracted demand'],
    'recovery_mispriced': ['cause of the setback (cycle, customer, pricing, product, competition)', 'what peers report since'],
    'exploratory': ['numbers that confirm the business evidence'],
    'signal': ['what the insiders or the channel may know: the next filing, guidance, contracts'],
}
HYPOTHESES = {
    'quality_compounder': 'growth of this quality continues for several more years without a margin give-back',
    'early_commercial_growth': 'commercial traction converts into self-funded, profitable scale before more dilution',
    'mispriced_turning': 'the improvement is durable and not a one-year rebound',
    'quality_fair_price': 'the business stays as good as its record while the price assumes less',
    'structural_tailwind': 'the tailwind lasts and this company keeps a share of the value',
    'recovery_mispriced': 'the setback is temporary and the earlier economics return',
    'exploratory': 'the supplied business evidence is material and not yet in the numbers',
    'signal': 'the signal reflects information about the business that the price does not yet carry',
}


# Walk-forward evidence per lane (docs/BACKTEST_DISCOVERY_2026-10-20.md: 2012-2025, July and October formations, equal-weight,
# survivorship-biased upward). 'negative' = underperformed the S&P 500 and the equal-weight universe in both variants (t about -2
# in one): research priority lowered, lane kept and re-tested. Data, not tuning: update only from a new out-of-sample test.
LANE_EVIDENCE = {'quality_compounder': 'mixed', 'quality_fair_price': 'neutral', 'early_commercial_growth': 'positive_noisy',
                 'mispriced_turning': 'neutral', 'recovery_mispriced': 'negative', 'structural_tailwind': 'negative',
                 'exploratory': 'untested', 'signal': 'untested'}


def _falsifier(lane, m):
    g, om, gm = m.get('revenue_growth') or m.get('growth_level'), m.get('operating_margin'), m.get('gross_margin')
    return {
        'quality_compounder': f"revenue growth below {_fmt(max(.06, (g or .12) / 2))} for a year, or operating margin down more than 3 pts "
                              f"from {_fmt(om)}",
        'early_commercial_growth': f"gross margin below 40% (now {_fmt(gm)}), losses widening in dollars, or share count up more than 10% a year",
        'mispriced_turning': 'the margin or cash-flow improvement reverses within the next two reported periods',
        'quality_fair_price': f"revenue shrinking two years running, or operating margin falling below {_fmt(max(.05, (om or .2) - .05))}",
        'structural_tailwind': 'growth falling to the sector median, or gross margin down 3 pts or more (supply response or substitutes)',
        'recovery_mispriced': 'revenue declining again next year or margins still falling: a structural decline, not a setback',
        'exploratory': 'the evidence is not confirmed by the next filing',
        'signal': 'the next two quarterly reports show no improvement in the business the signal pointed to',
    }[lane]


def _signals(dim, sign):
    return [v['reason'] for v in (dim.get('inputs') or {}).values() if v.get('signal') == sign and v.get('reason')]


def card(scan, symbol, lane_hits=None, exploratory=None, shortlisted=False):
    """One card from the scan. lane_hits: [{'lens','rank','why'}]; exploratory: {'reason', 'evidence': [...]} or None."""
    d = scan['dimensions'][symbol]
    m = d['metrics']
    lanes = list(lane_hits or [])
    if exploratory:
        lanes.append({'lens': 'exploratory', 'rank': None, 'why': exploratory['reason']})
    main = lanes[0]['lens'] if lanes else None
    pi = d['priced_in']
    priced = (f"the price implies about {_fmt(pi.get('implied_growth'))} a year on {pi.get('multiple_basis')} versus "
              f"{_fmt(pi.get('reference_growth'))} {pi.get('reference_basis')}" if pi.get('status') == 'solved' else
              'not assessed: ' + '; '.join(pi.get('reasons', [])[:1]))
    exceptional = None
    if pi.get('status') == 'solved' and pi.get('reference_growth') is not None and pi.get('implied_growth') is not None:
        gap = pi['reference_growth'] - pi['implied_growth']
        exceptional = (f"if the demonstrated {_fmt(pi['reference_growth'])} growth persists while the price assumes {_fmt(pi['implied_growth'])}, "
                       f"the gap ({_fmt(gap, 'pts')}) is the opportunity; it is only real if the economics hold")
    risks = [f['message'] for f in d['risk']['flags']]
    risks += [f"data: {f['message']}" for f in (d['profile'].get('data_flags') or [])]
    cy = d['profile'].get('cyclical')
    if cy and cy.get('value'):
        risks.append(f"cyclical ({cy.get('word') or cy.get('type') or cy.get('basis')}); peak check: {cy.get('peak_check') or 'none'}")
    du = d['data_uncertainty']
    missing = list(du.get('missing') or [])
    for ln in [x['lens'] for x in lanes]:
        missing += [x for x in NOT_IN_SCREEN.get(ln, []) if x not in missing]
    if d['profile'].get('data_grade') == 'sec_frames':
        missing.append('trailing twelve months (screen uses the latest calendar-year frame)')
    supported = _signals(d['business_quality'], 1) + _signals(d['growth_quality'], 1) + _signals(d['financial_strength'], 1)
    weak = _signals(d['business_quality'], -1) + _signals(d['growth_quality'], -1) + _signals(d['financial_strength'], -1)
    held = scan['excluded'].get(symbol) or ''
    if du['level'] == 'weak':
        decision, why = 'no', 'data not reliable enough to rank: ' + '; '.join(du['reasons'][:2])
    elif held.startswith('not supported'):
        decision, why = 'no', 'outside the operating model (' + held[len('not supported: '):].split(':')[0] + '); needs a sector-specific method'
    elif held.startswith('distress'):
        decision, why = 'maybe', held + '; only with a funding and survival view first'
    elif exploratory:
        decision, why = 'yes', 'business evidence supplied for a company the numeric lenses did not admit; the numbers must confirm it'
    elif shortlisted:
        decision, why = 'yes', f"leads its lane ({LANES_HE.get(main, main)}) after diversity across lanes"
    elif 'priced_for_perfection' in sum((c.get('tags', []) for c in (scan['lenses'].get(main) or []) if c['symbol'] == symbol), []):
        decision, why = 'maybe', 'passes the lane but the price already assumes very high growth'
    else:
        decision, why = 'maybe', 'passes a lane but ranks below the shortlist; analyse when capacity allows'
    return {'symbol': symbol, 'name': d['profile'].get('name'), 'sector': d['profile'].get('sector'), 'industry': d['profile'].get('industry'),
            'market_cap': m.get('market_cap'), 'lanes': [{**x, 'label_he': LANES_HE.get(x['lens'], x['lens'])} for x in lanes],
            'why_found': lanes[0]['why'] if lanes else None, 'exceptional_if': exceptional,
            'supported': supported[:6], 'weak_points': weak[:4],
            'hypotheses': [HYPOTHESES[x['lens']] for x in lanes if x['lens'] in HYPOTHESES],
            'falsifiers': [_falsifier(x['lens'], m) for x in lanes],
            'priced_in': priced, 'risks': risks[:6], 'missing_data': missing[:8],
            'research_question': LENS_CHECKS.get(main) if main in LENS_CHECKS else 'confirm the supplied evidence in filings',
            'evidence': (exploratory or {}).get('evidence', []),
            'levels': {k: d[k]['level'] for k in ('business_quality', 'growth_quality', 'financial_strength', 'valuation', 'priced_in', 'risk',
                                                  'data_uncertainty')},
            'full_analysis': {'decision': decision, 'reason': why},
            'meaning': 'research priority, not a recommendation; supported items are screening-grade data, hypotheses are untested'}


def cards(scan, exploratory=None, include='lenses', signals=None, max_signal_cards=10):
    """Cards for every lens candidate (include='lenses') or only the shortlist (include='shortlist'), plus the exploratory
    path: exploratory = {symbol: {'reason': text, 'evidence': [{'source', 'date', 'statement'}]}} (written evidence required)."""
    hits = {}
    for ln in LENSES:
        for c in scan['lenses'][ln]:
            hits.setdefault(c['symbol'], []).append({'lens': ln, 'rank': c['rank'], 'why': c['why']})
    short = {s['symbol']: (i, s['selected_by']) for i, s in enumerate(scan['shortlist'])}
    for s, (_, ln) in short.items():          # the lane that selected a shortlisted name goes first on its card
        hits[s].sort(key=lambda x: x['lens'] != ln)
    syms = sorted(short) if include == 'shortlist' else sorted(hits)
    out = {s: card(scan, s, hits.get(s), shortlisted=s in short) for s in syms}
    for s, (i, _) in short.items():
        if s in out:
            out[s]['shortlist_position'] = i + 1
    notes = []
    for sym, ev in (exploratory or {}).items():
        if not isinstance(ev, dict) or not str(ev.get('reason') or '').strip() or not ev.get('evidence'):
            raise ValueError(f'exploratory.{sym}: a written reason and at least one dated evidence item are required')
        for i, e in enumerate(ev['evidence']):
            if not isinstance(e, dict) or not all(str(e.get(k) or '').strip() for k in ('source', 'date', 'statement')):
                raise ValueError(f'exploratory.{sym}.evidence[{i}]: source, date and statement are required')
        if sym not in scan['dimensions']:
            notes.append(f'{sym}: not in the scanned universe (outside coverage or no data); collect it directly to analyse')
            continue
        out[sym] = card(scan, sym, hits.get(sym), exploratory=ev, shortlisted=sym in short)
        if not hits.get(sym):
            out[sym]['numeric_failures'] = scan['excluded'].get(sym) or 'not recorded by the scan'
    # external discovery signals (insider purchase clusters, channel evidence): {symbol: [evidence]}. Attached to lane cards
    # as support; strong ones outside every lane open a 'signal' card for research (decision maybe: a signal is attention or
    # conviction, not evidence about the economics)
    # red flags and negative signals (leads.card_signals: a late filing, non-reliance, an impairment) attach as questions, never
    # as support, and never open a card
    added = 0
    for sym, all_evs in (signals or {}).items():
        bad = [e for e in all_evs if e.get('kind') == 'red_flag' or e.get('direction') == 'negative']
        evs = [e for e in all_evs if e not in bad]
        if bad and sym in out:
            out[sym].setdefault('red_flags', []).extend(bad)
        if not evs:
            continue
        if sym in out:
            out[sym].setdefault('signals', []).extend(evs)
            continue
        if sym not in scan['dimensions'] or added >= max_signal_cards:
            continue
        held = scan['excluded'].get(sym) or ''
        if held.startswith(('not supported', 'distress', 'unreliable', 'coverage gap')):
            continue
        c = card(scan, sym, [{'lens': 'signal', 'rank': None, 'why': evs[0]['statement']}])
        c['signals'] = list(evs)
        if bad:
            c['red_flags'] = list(bad)
        c['numeric_failures'] = held or None
        c['full_analysis'] = {'decision': 'maybe', 'reason': 'external signal without a numeric lane: research the business first'}
        out[sym] = c
        added += 1
    for c in out.values():
        if c.get('signals') and c['full_analysis']['decision'] == 'yes':
            c['full_analysis']['reason'] += '; supported by an external signal (' + c['signals'][0].get('kind', 'evidence') + ')'
    return {'cards': list(out.values()), 'notes': notes}


def research_queue(card_set, max_full=6):
    """Cards marked for full analysis, round-robin across lanes (no lane crowds out the others), at most max_full."""
    yes = [c for c in card_set['cards'] if c['full_analysis']['decision'] == 'yes'
           or (c.get('signals') and c['full_analysis']['decision'] == 'maybe' and c['lanes'] and c['lanes'][0]['lens'] != 'signal')]
    by_lane = {}
    for c in yes:
        by_lane.setdefault(c['lanes'][0]['lens'] if c['lanes'] else 'other', []).append(c)
    for lst in by_lane.values():
        # two independent sources (a numeric lane and an external signal) first, then the shortlist order
        lst.sort(key=lambda c: (0 if c.get('signals') else 1, c.get('shortlist_position') or 999, c['lanes'][0]['rank'] or 99, c['symbol']))
    queue, taken = [], set()
    # lanes with negative walk-forward evidence come after the others are exhausted
    first = [ln for ln in by_lane if LANE_EVIDENCE.get(ln) != 'negative']
    last = [ln for ln in by_lane if LANE_EVIDENCE.get(ln) == 'negative']
    for group in (first, last):
        _fill(queue, taken, {ln: by_lane[ln] for ln in group}, max_full)
    return queue


def _fill(queue, taken, by_lane, max_full):
    while len(queue) < max_full and any(by_lane.values()):
        for ln in list(by_lane):
            while by_lane[ln] and by_lane[ln][0]['symbol'] in taken:
                by_lane[ln].pop(0)
            if by_lane[ln] and len(queue) < max_full:
                c = by_lane[ln].pop(0)
                queue.append({'symbol': c['symbol'], 'lane': c['lanes'][0]['lens'] if c['lanes'] else None,
                              'reason': c['full_analysis']['reason'],
                              'lane_evidence': LANE_EVIDENCE.get(c['lanes'][0]['lens'] if c['lanes'] else None, 'untested')})
                taken.add(c['symbol'])


def render_he(card_set, limit=None):
    """Short Hebrew summary per card (markdown)."""
    lines = []
    for c in card_set['cards'][:limit]:
        lanes = ', '.join(x['label_he'] for x in c['lanes'])
        lines += [f"### {c['symbol']} — {c['name']} ({lanes})",
                  f"- למה התגלתה: {c['why_found']}",
                  f"- מה יכול להפוך אותה לחריגה: {c['exceptional_if'] or 'לא ניתן לחשב מנתוני הסינון'}",
                  f"- נתמך בנתונים: {'; '.join(c['supported'][:3]) or 'אין אות חזק'}",
                  f"- השערה: {'; '.join(c['hypotheses'])}",
                  f"- מה יפריך: {'; '.join(c['falsifiers'])}",
                  f"- מה מגולם במחיר: {c['priced_in']}",
                  f"- סיכונים: {'; '.join(c['risks'][:3]) or 'לא סומנו בסינון'}",
                  f"- נתונים חסרים: {'; '.join(c['missing_data'][:4])}",
                  f"- ניתוח מלא: {c['full_analysis']['decision']} — {c['full_analysis']['reason']}", '']
    return '\n'.join(lines)
