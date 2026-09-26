"""Evidence-aware research. Rules audit analyst inputs; they do not prove causality."""
from datetime import date
from .finance import number

CAUSES = {'fx', 'divestiture', 'acquisition', 'seasonality', 'comparison', 'delivery_delay',
          'recognition_timing', 'strike', 'restructuring', 'customer_loss', 'competition',
          'technology_disruption', 'demand', 'legal', 'asset_sale', 'other'}
STAGES = ('idea', 'prototype', 'pilot', 'paying_customers', 'repeat_orders', 'scaling', 'proven_unit_economics')
METRICS = {'revenue', 'ebit', 'net_income', 'cfo', 'capex'}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: nonempty text required')
    return value


class Evidence:
    def __init__(self, data):
        self.cutoff = date.fromisoformat(data['as_of'])
        self.sources = {s['id']: s for s in data['sources']}

    def eligible(self, item, date_key='reviewed_at'):
        refs = item.get('source_ids', [])
        if not refs or any(r not in self.sources for r in refs):
            raise ValueError('Missing or unknown evidence references')
        reviewed = date.fromisoformat(item[date_key])
        return reviewed <= self.cutoff and all(date.fromisoformat(self.sources[r]['available_at']) <= self.cutoff for r in refs)

    def origins(self, refs):
        # Unspecified origin is UNKNOWN, not an independent corroborating source.
        return sorted({self.sources[r]['origin_id'] for r in refs if self.sources[r].get('origin_id')})


def analyze_events(data, periods):
    ev = Evidence(data)
    events, excluded, bridges = [], [], {}
    seen, impact_seen = set(), set()
    reported = {(p['end'], m): p.get(m) for p in periods for m in METRICS}
    for e in data.get('events', []):
        eid = text(e['id'], 'event.id')
        if eid in seen:
            raise ValueError('Duplicate event id')
        seen.add(eid)
        text(e['description'], 'event.description')
        if e['cause'] not in CAUSES or e['cash_effect'] not in {'lost', 'delayed', 'noncash', 'unknown'}:
            raise ValueError('Unknown event cause/cash effect')
        if e['recurrence'] not in {'isolated', 'repeated', 'unknown'}:
            raise ValueError('Invalid event recurrence')
        if date.fromisoformat(e['observed_at']) > date.fromisoformat(e['reviewed_at']):
            raise ValueError('Event review precedes observation')
        if not ev.eligible(e):
            excluded.append(eid)
            continue
        evidence = []
        for item in e.get('evidence', []):
            if item['role'] not in {'supports_temporary', 'supports_structural', 'recovery', 'attribution'}:
                raise ValueError('Unknown event evidence role')
            if item['status'] not in {'supported', 'unverified', 'contradicted'}:
                raise ValueError('Unknown evidence review status')
            text(item['claim'], 'evidence.claim')
            if ev.eligible(item):
                evidence.append(item)
        roles = {r['role'] for r in evidence if r['status'] == 'supported'}
        temporary = 'supports_temporary' in roles
        structural = 'supports_structural' in roles or e['recurrence'] == 'repeated'
        if structural and temporary:
            status = 'mixed'
        elif structural:
            status = 'structural_concern'
        elif temporary and 'recovery' in roles and e.get('recovery_requirements') and e.get('falsifiers'):
            status = 'temporary_supported'
        elif temporary:
            status = 'recovery_unproven'
        else:
            status = 'insufficient_evidence'
        rows = []
        for impact in e.get('impacts', []):
            mid = text(impact['id'], 'impact.id')
            if mid in impact_seen:
                raise ValueError('Impact IDs must be globally unique to prevent double counting')
            impact_seen.add(mid)
            metric, end = impact['metric'], impact['period_end']
            date.fromisoformat(end)
            if metric not in METRICS:
                raise ValueError('Unknown impact metric')
            effect = number(impact['reported_effect'], 'reported_effect') if impact.get('reported_effect') is not None else None
            if impact['classification'] not in {'booked_exceptional', 'comparability', 'counterfactual', 'unknown'}:
                raise ValueError('Unknown impact classification')
            if impact['measurement'] not in {'disclosed', 'analyst_estimate', 'unknown'}:
                raise ValueError('Unknown impact measurement')
            impact_ok = ev.eligible(impact)
            reason = None
            if not impact_ok:
                # Do not expose future effect values in historical output.
                continue
            if effect is None or impact['measurement'] != 'disclosed':
                reason = 'No disclosed, quantified effect'
            elif impact['classification'] != 'booked_exceptional':
                reason = 'Comparison or counterfactual belongs in scenarios, not historical add-backs'
            elif metric in {'revenue', 'capex'}:
                reason = 'Revenue/capex adjustments require a separate comparable-scope model'
            elif e['recurrence'] != 'isolated' or structural:
                reason = 'Repeated, uncertain or structurally relevant item is not removed'
            elif 'attribution' not in roles or impact.get('review_status') != 'supported':
                reason = 'Booked effect and attribution have not been reviewed as supported'
            elif reported.get((end, metric)) is None:
                reason = 'Missing eligible reported metric for exact period'
            adjustment = -effect if reason is None else 0
            row = {**impact, 'adjustment': adjustment, 'accepted': reason is None, 'reason': reason}
            rows.append(row)
            key = (end, metric)
            if key not in bridges:
                bridges[key] = {'period_end': end, 'metric': metric, 'reported': reported.get(key),
                                'adjustments': [], 'normalized': reported.get(key)}
            bridges[key]['adjustments'].append({'event_id': eid, 'impact_id': mid, 'amount': adjustment, 'accepted': reason is None})
            if reason is None:
                bridges[key]['normalized'] += adjustment
        refs = sorted({r for i in evidence for r in i['source_ids']} | set(e['source_ids']))
        events.append({'id': eid, 'description': e['description'], 'cause': e['cause'], 'status': status,
                       'management_claim': e.get('management_claim'), 'evidence': evidence,
                       'origin_groups': ev.origins(refs), 'recurrence': e['recurrence'],
                       'cash_effect': e['cash_effect'], 'impacts': rows,
                       'recovery_requirements': e.get('recovery_requirements', []),
                       'falsifiers': e.get('falsifiers', []), 'source_ids': refs})
    return {'events': events, 'normalization': list(bridges.values()), 'excluded_event_ids': excluded,
            'note': 'Classification audits analyst-reviewed evidence; normalization never changes the original financials or forecast automatically.'}


def evaluate_driver(node, evidence, depth=0):
    if depth > 12:
        raise ValueError('Revenue driver tree exceeds depth limit')
    text(node['label'], 'driver label')
    text(node['unit'], 'driver unit')
    if node['op'] == 'input':
        available = evidence.eligible(node)
        value = number(node['value'], 'driver value', 0) if node.get('value') is not None and available else None
        return {**{k:node[k] for k in ('label','unit','op')}, 'value': value,
                'source_ids': node['source_ids'] if available else [], 'status': 'available' if value is not None else 'missing'}
    if node['op'] not in {'sum', 'product'} or not 2 <= len(node.get('children', [])) <= 30:
        raise ValueError('Driver requires sum/product and 2..30 children')
    children = [evaluate_driver(c, evidence, depth+1) for c in node['children']]
    if node['op'] == 'sum' and any(c['unit'] != node['unit'] for c in children):
        raise ValueError('Cannot sum driver values with different units')
    value = None
    if all(c['value'] is not None for c in children):
        value = 0 if node['op'] == 'sum' else 1
        for c in children:
            value = value+c['value'] if node['op'] == 'sum' else value*c['value']
        number(value, 'driver result')
    return {'label': node['label'], 'unit': node['unit'], 'op': node['op'], 'value': value, 'children': children}


def analyze_business(data):
    ev = Evidence(data)
    b = data.get('business')
    if not b or not ev.eligible(b):
        return {'status': 'missing', 'drivers': None, 'claims': [], 'opportunities': []}
    claims = []
    for c in b.get('claims', []):
        if c['kind'] not in {'customer', 'supplier', 'moat', 'competition', 'segment', 'geography', 'capital_allocation', 'value_chain'}:
            raise ValueError('Unknown business claim kind')
        if c['status'] not in {'supported', 'unverified', 'contradicted'}:
            raise ValueError('Unknown business claim status')
        if ev.eligible(c):
            claims.append(c)
    opportunities = []
    for o in b.get('opportunities', []):
        if o['claimed_stage'] not in STAGES:
            raise ValueError('Unknown commercialization stage')
        if not ev.eligible(o):
            continue
        supports = []
        for s in o.get('stage_evidence', []):
            if s['stage'] not in STAGES:
                raise ValueError('Unknown evidence stage')
            if ev.eligible(s) and s['status'] == 'supported':
                supports.append(s)
        supported = max((STAGES.index(s['stage']) for s in supports), default=-1)
        milestones = []
        for m in o.get('milestones', []):
            date.fromisoformat(m['due_at'])
            for key in ('success_criterion','failure_criterion'):
                text(m[key], key)
            if m.get('cost') is not None:
                number(m['cost'], 'milestone cost', 0)
            milestones.append(m)
        opportunities.append({'name': o['name'], 'claimed_stage': o['claimed_stage'],
            'highest_evidenced_stage': STAGES[supported] if supported >= 0 else None,
            'status': 'stage_supported' if supported >= STAGES.index(o['claimed_stage']) else 'stage_unproven',
            'stage_evidence': supports, 'milestones': milestones,
            'shareholder_risks': o.get('shareholder_risks', []), 'source_ids': o['source_ids']})
    return {'status': 'structured_analyst_research', 'description': b['description'],
            'drivers': evaluate_driver(b['revenue_driver'], ev) if b.get('revenue_driver') else None,
            'claims': claims, 'opportunities': opportunities, 'source_ids': b['source_ids']}


def investigation(report):
    questions = []
    def add(priority, question, why):
        questions.append({'priority': priority, 'question': question, 'why': why})
    for e in report['event_analysis']['events']:
        if e['status'] != 'temporary_supported':
            add(1, f"מה יבדיל התאוששות מנזק מתמשך באירוע: {e['description']}?", e['status'])
    if any(p.get('status') != 'funded' for p in report.get('owner_valuations', [])) or (report['funding'] and report['funding']['first_gap_period']):
        add(1, 'מה מקור המימון, מחיר ההנפקה והחלופה אם הגיוס נכשל?', 'סיכון לנזילות ולחלקה של המניה הקיימת')
    if not report['valuations'] and not report.get('owner_valuations'):
        add(2, 'איזה טווח הנחות ושווי נתמך בנתונים, ומה מחיר המניה במועד המחקר?', 'לא ניתן להסיק הזדמנות עסקית מהירידה בתוצאות בלבד')
    if report['business_analysis']['status'] == 'missing':
        add(2, 'מי הלקוח, מה מניע את ההכנסות ומה מונע ממנו לעבור למתחרה?', 'חסר מודל עסקי מבוסס מקורות')
    for o in report['business_analysis']['opportunities']:
        if o['status'] != 'stage_supported':
            add(2, f"מה הראיה לשלב המסחרי של {o['name']}?", 'הצהרת שלב אינה ראיית אימוץ')
    if not report['quote']:
        add(3, 'מהו מחיר המניה ומספר המניות בדילול במועד ההערכה?', 'עסק טוב יכול להיות יקר')
    return sorted(questions, key=lambda q:q['priority'])[:5]


def market_context(data, report):
    """Descriptive event-window comparison, not a causal or statistical event study."""
    m = data.get('market_context')
    if not m:
        return {'status':'not_established','reason':'No matched event-window market data'}
    ev = Evidence(data)
    if not ev.eligible(m):
        return {'status':'not_established','reason':'Market context unavailable at cutoff'}
    start, end = date.fromisoformat(m['start']), date.fromisoformat(m['end'])
    if start >= end or end > ev.cutoff:
        raise ValueError('Invalid market event window')
    if m['return_basis'] != 'total_return':
        raise ValueError('Matched total returns required, including distributions and corporate actions')
    stock = number(m['stock_return'],'stock_return',-1)
    market = number(m['benchmark_return'],'benchmark_return',-1)
    beta = number(m['beta'],'beta',0,5)
    text(m['benchmark'],'benchmark')
    text(m['beta_method'],'beta_method')
    if not isinstance(m['confounders'],list) or not isinstance(m['confounders_reviewed'],bool):
        raise ValueError('Explicit confounder review and list required')
    residual = stock-beta*market
    quote = report['quote']
    consistent_price = quote and quote['as_of']==m['end'] and (ev.cutoff-end).days<=7
    values = [v['value_per_share'] for v in report['valuations']]
    supported_temporary = bool(report['event_analysis']['events']) and all(e['status']=='temporary_supported' for e in report['event_analysis']['events'])
    qualifies = (consistent_price and values and min(values)>quote['price'] and stock<0 and residual<0
                 and supported_temporary and m['confounders_reviewed'] and not m['confounders']
                 and m.get('liquidity_reviewed') is True and not report['owner_valuations']
                 and not (report['funding'] and report['funding']['first_gap_period']))
    return {'status':'possible_overreaction_requires_review' if qualifies else 'not_established',
            'start':m['start'],'end':m['end'],'stock_total_return':stock,'benchmark_total_return':market,
            'benchmark':m['benchmark'],'beta':beta,'beta_method':m['beta_method'],
            'descriptive_residual':residual,'confounders':m['confounders'],
            'source_ids':m['source_ids'],
            'note':'Descriptive beta-adjusted return only. No statistical significance, causal attribution, consensus forecast or proven mispricing. Legacy DCF scenarios exclude financing dilution.'}
