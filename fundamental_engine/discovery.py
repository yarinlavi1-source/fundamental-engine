"""Emerging-opportunity discovery. Research prioritization, never an entry filter.

Future constraints are explicit hypotheses. Profitability, valuation availability
and company age do not determine discovery eligibility. The analyst supplies and
reviews economic mechanisms; this calculator does not predict the next winner.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from .finance import number

ROLES={'technical','pilot','design_win','paid_adoption','production','repeat',
       'scaling','unit_economics','value_capture','demand','supply','substitute','funding'}
NODE_TYPES={'driver','constraint','solution','beneficiary','substitute'}

def day(s):return date.fromisoformat(s)
def txt(v,k):
    if not isinstance(v,str) or not v.strip():raise ValueError(k+': nonempty text required')
    return v

def _unique(rows,label):
    out={}
    for row in rows:
        k=txt(row['id'],label+'.id')
        if k in out:raise ValueError('Duplicate '+label+' id')
        out[k]=row
    return out


def scan(case):
    c=deepcopy(case)
    if c.get('discovery_version')!=1:raise ValueError('discovery_version must be 1')
    json.dumps(c,allow_nan=False)
    cutoff=day(c['as_of'])
    for k in ('company_id','ticker'):txt(c[k],k)
    sources=_unique(c['sources'],'source')
    for src in sources.values():
        for k in ('url','title','kind','origin_id'):txt(src[k],'source.'+k)
        if day(src['available_at'])<day(src['published_at']):raise ValueError('Source precedes publication')
        if src['kind']=='synthetic' and not c.get('is_demo'):raise ValueError('Synthetic sources require is_demo')
    def refs(ids):
        if not ids or any(s not in sources for s in ids):raise ValueError('Missing/unknown source reference')
        return all(day(sources[s]['available_at'])<=cutoff for s in ids)
    nodes=_unique(c['nodes'],'node')
    if len(nodes)>100:raise ValueError('Graph too large; split themes')
    for n in nodes.values():
        txt(n['description'],'node.description')
        if n['kind'] not in NODE_TYPES:raise ValueError('Unknown node kind')
    edges=_unique(c['edges'],'edge');eligible_edges={};excluded=[]
    for e in edges.values():
        if e['from'] not in nodes or e['to'] not in nodes or e['from']==e['to']:raise ValueError('Invalid causal edge endpoints')
        for k in ('mechanism','condition','falsifier'):txt(e[k],'edge.'+k)
        if e['kind'] not in {'observed','hypothesis'}:raise ValueError('Unknown causal edge kind')
        if not isinstance(e['reviewed'],bool):raise ValueError('reviewed must be boolean')
        number(e['lag_months'],'lag_months',0,120)
        valid=refs(e['source_ids']) and day(e['as_of'])<=cutoff
        if valid:eligible_edges[e['id']]=e
        else:excluded.append(e['id'])
    obs=_unique(c['observations'],'observation');active={};stale=[]
    freshness=c.get('freshness_days',365)
    if isinstance(freshness,bool) or not isinstance(freshness,int) or not 1<=freshness<=1825:raise ValueError('freshness_days must be 1..1825')
    for o in obs.values():
        if o['role'] not in ROLES or o['direction'] not in {'supports','opposes'}:raise ValueError('Unknown evidence role/direction')
        if o['kind'] not in {'reported','guidance','opinion'}:raise ValueError('Unknown observation kind')
        for k in ('statement','location'):txt(o[k],k)
        if not isinstance(o['reviewed'],bool):raise ValueError('reviewed must be boolean')
        if day(o['reviewed_at'])<day(o['observed_at']):raise ValueError('Review precedes observation')
        known=refs(o['source_ids'])
        if not known or day(o['observed_at'])>cutoff or day(o['reviewed_at'])>cutoff:excluded.append(o['id']);continue
        if (cutoff-day(o['observed_at'])).days>freshness:stale.append(o['id']);continue
        active[o['id']]=o
    opportunities=[]
    for op in _unique(c['opportunities'],'opportunity').values():
        for k in ('problem','payer','product','capture_mechanism','substitutes','supply_response','why_now','falsifier'):txt(op[k],k)
        path=op['path']
        if not path or len(path)>20 or any(e not in edges for e in path):raise ValueError('Invalid opportunity path')
        pe=[edges[e] for e in path]
        if nodes[pe[0]['from']]['kind']!='driver' or nodes[pe[-1]['to']]['kind']!='beneficiary':raise ValueError('Path must connect driver to beneficiary')
        if any(a['to']!=b['from'] for a,b in zip(pe,pe[1:])):raise ValueError('Causal path is disconnected')
        vertices=[pe[0]['from']]+[e['to'] for e in pe]
        if len(set(vertices))!=len(vertices):raise ValueError('Opportunity path must not loop back')
        if any(x not in obs for x in op['observation_ids']):raise ValueError('Unknown opportunity observation')
        good=[active[x] for x in op['observation_ids'] if x in active and active[x]['reviewed'] and active[x]['kind']=='reported']
        support={o['role'] for o in good if o['direction']=='supports'}
        opposing={o['role'] for o in good if o['direction']=='opposes'}
        stage='hypothesis'
        for role,label in [('technical','technical_evidence'),('pilot','pilot'),('design_win','design_win'),('paid_adoption','paying_customers'),('production','production'),('repeat','repeat_adoption'),('scaling','scaling')]:
            if role in support:stage=label
        if {'repeat','unit_economics'}<=support and support & {'production','scaling'}:stage='demonstrated_unit_economics'
        capture='supported' if 'value_capture' in support and 'value_capture' not in opposing else 'contested' if 'value_capture' in opposing else 'unproven'
        path_available=all(e in eligible_edges for e in path)
        hypotheses=[e['id'] for e in pe if e['kind']=='hypothesis' or not e['reviewed']]
        lane='explore'
        if support & {'technical','pilot','design_win','demand'}:lane='monitor_progress'
        if support & {'paid_adoption','production','repeat','scaling'} and capture=='supported':lane='underwrite_early_growth'
        if opposing:lane='resolve_counterevidence'
        if not path_available:lane='repair_evidence_path'
        pressures=[]
        for p in op.get('pressure_cases',[]):
            if p['case'] not in {'low','base','high'}:raise ValueError('Pressure case must be low/base/high')
            if p['kind'] not in {'observed','scenario'}:raise ValueError('Pressure kind required')
            txt(p['unit'],'pressure unit');txt(p['scope'],'pressure scope');txt(p['rationale'],'pressure rationale')
            if p['kind']=='observed' and day(p['date'])>cutoff:raise ValueError('Future capacity gap cannot be an observed fact')
            demand=number(p['demand'],'demand',0);supply=number(p['supply'],'supply',0)
            if not refs(p['source_ids']):continue
            pressures.append({**p,'shortfall':max(0,demand-supply),'demand_to_supply':demand/supply if supply else None,'status':'conditional_shortfall' if p['kind']=='scenario' and demand>supply else 'observed_shortfall' if demand>supply else 'no_shortfall_in_case'})
        milestones=op['milestones']
        for m in milestones:
            for k in ('test','success','failure','action_if_success','action_if_failure'):txt(m[k],k)
            day(m['due_at'])
        if not milestones:raise ValueError('Opportunity requires a falsifiable milestone')
        # Distinct origin counts describe provenance breadth, never probability/quality.
        origins=sorted({sources[s]['origin_id'] for o in good for s in o['source_ids']})
        questions=[]
        if capture!='supported':questions.append('Identify who pays and why this company retains economic profit instead of customers or competing suppliers.')
        if not support & {'paid_adoption','production','repeat'}:questions.append('Find paid conversion or production adoption; distinguish design wins/pilots from realized orders.')
        if hypotheses:questions.append('Test the weakest causal link and the timing/supply response; future bottlenecks remain scenarios.')
        if opposing:questions.append('Resolve counterevidence before promoting the thesis; retain both supporting and opposing observations.')
        if 'funding' not in support:questions.append('Model runway and funding to the NEXT milestone; retain the opportunity even if financing remains conditional.')
        opportunities.append({'id':op['id'],'research_lane':lane,'evidenced_stage':stage,'value_capture':capture,
          'supported_roles':sorted(support),'opposing_roles':sorted(opposing),'path_available':path_available,
          'hypothesis_edge_ids':hypotheses,'causal_path':pe,'pressure_cases':pressures,
          'distinct_source_origins':origins,'supporting_observation_ids':[o['id'] for o in good if o['direction']=='supports'],
          'opposing_observation_ids':[o['id'] for o in good if o['direction']=='opposes'],
          'next_questions':questions,'milestones':milestones,
          'investment_status':'valuation_and_funding_underwriting_required',
          'note':'Research priority only; not a buy signal. Missing profitability or conventional valuation does not reject discovery.'})
    trends=[]
    for series in c.get('trajectories',[]):
        txt(series['metric'],'metric');txt(series['definition'],'definition')
        if series['better'] not in {'higher','lower'}:raise ValueError('Unknown improvement direction')
        points=[]
        seen=set()
        for p in series['points']:
            number(p['value'],'trajectory value');number(p['period_days'],'period_days',1,370)
            key=p['date']
            if key in seen:raise ValueError('Duplicate date in metric trajectory')
            seen.add(key)
            if not refs(p['source_ids']) or day(p['date'])>cutoff:continue
            if p['unit']!=series['unit'] or p['scope']!=series['scope']:raise ValueError('Trajectory unit/scope mismatch')
            points.append(p)
        points.sort(key=lambda p:p['date'])
        comparable=len(points)>1 and max(p['period_days'] for p in points)/min(p['period_days'] for p in points)<=1.05
        deltas=[b['value']-a['value'] for a,b in zip(points,points[1:])] if comparable else []
        sign=1 if series['better']=='higher' else -1
        trajectory='insufficient_comparable_data'
        if deltas:trajectory='improving' if all(d*sign>0 for d in deltas) else 'deteriorating' if all(d*sign<0 for d in deltas) else 'mixed_or_flat'
        trends.append({'metric':series['metric'],'definition':series['definition'],'status':trajectory,'changes':deltas,'points':points,
                       'note':'Descriptive change, not causation or forecast; compare seasonally matched periods.'})
    result={'version':'0.5.0','company_id':c['company_id'],'ticker':c['ticker'],'as_of':c['as_of'],'is_demo':bool(c.get('is_demo')),
      'sources':[v for v in sources.values() if day(v['available_at'])<=cutoff],
      'opportunities':opportunities,'trajectories':trends,'excluded_ids':excluded,'stale_observation_ids':stale,
      'input_sha256':sha256(json.dumps(c,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
      'policy':{'profitability_required_for_discovery':False,'positive_fcf_required_for_discovery':False,'low_pe_required_for_discovery':False,
                'discovery_is_entry_permission':False,'buy_orders_supported':False},
      'limitations':['Analyst-supplied mechanisms and review labels require substantive source inspection.',
                     'Bottleneck scenarios and source counts are not calibrated success probabilities.',
                     'No autonomous future invention, ticker picking, monitoring schedule or trade execution.']}
    json.dumps(result,allow_nan=False)
    return result


def compare_discovery(before,after):
    a,b=scan(before),scan(after)
    if a['company_id']!=b['company_id'] or a['ticker']!=b['ticker']:raise ValueError('Cannot compare different securities')
    if day(a['as_of'])>day(b['as_of']):raise ValueError('Revision dates out of order')
    aa={o['id']:o for o in a['opportunities']};bb={o['id']:o for o in b['opportunities']}
    return {'company_id':a['company_id'],'before':a['as_of'],'after':b['as_of'],
            'changes':[{'id':k,'before':{f:aa[k][f] for f in ('research_lane','evidenced_stage','value_capture')} if k in aa else None,
                        'after':{f:bb[k][f] for f in ('research_lane','evidenced_stage','value_capture')} if k in bb else None}
                       for k in sorted(set(aa)|set(bb)) if aa.get(k)!=bb.get(k)],
            'trajectories_before':a['trajectories'],'trajectories_after':b['trajectories'],
            'note':'Revision comparison, not prediction accuracy; late evidence must not be backdated.'}


def frontier_plan(request):
    """Theme-first agenda, before a security is selected. No autonomous retrieval."""
    theme=txt(request['theme'],'theme');cutoff=day(request['as_of']).isoformat()
    return {'theme':theme,'as_of':cutoff,'packets':['frontier_discovery','frontier_research','potential','annual_valuation'],
      'tasks':[
       {'step':'demand','question':'What workload grows, who pays, and what measurable unit should be forecast?','source':'customer budgets, usage disclosures, original industry data'},
       {'step':'constraint','question':'What resource can limit this workload at 0–12, 12–36 and 36–60 months? Specify geography, units and scenario.','source':'technical standards, engineering research, lead times and capacity data'},
       {'step':'response','question':'What efficiency, substitution, capacity addition or insourcing can remove the constraint?','source':'competing architectures, supplier plans and customer alternatives'},
       {'step':'capture','question':'Which listed firms control a scarce qualified solution, and can they retain profit?','source':'product economics, customer confirmations, contracts and competition'},
       {'step':'progress','question':'What evidence moved from pilot/design win to paid deployment, repeat use or improving contribution?','source':'dated comparable operational metrics'},
       {'step':'underwrite','question':'What financing and per-share outcome follow under failure, delay and scale? What does the quote already require?','source':'filings and explicit annual valuation scenarios'}],
      'policy':'Keep early hypotheses on the research radar; profitability is not an admission requirement. None of these steps is an automatic entry signal.'}
