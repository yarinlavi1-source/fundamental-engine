"""Finite investigation loops and original-guidance tracking."""
from datetime import date
from .finance import number
from .research import text


def search_control(case):
    cutoff=date.fromisoformat(case['as_of'])
    budget=case.get('search_budget',{})
    limit=budget.get('max_actions',24)
    per_question=budget.get('max_unproductive_attempts',3)
    for name,value in (('max_actions',limit),('max_unproductive_attempts',per_question)):
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=256:
            raise ValueError(name+' must be an integer from 1 to 256')
    queries=set();unproductive={};actions=0;duplicates=[];seen=set()
    for a in case.get('research_log',[]):
        aid=text(a['id'],'research action id')
        if aid in seen:raise ValueError('Duplicate research action id')
        seen.add(aid)
        if date.fromisoformat(a['completed_at'])>cutoff:
            continue
        text(a['question_id'],'question id');text(a['connector'],'connector')
        query=text(a['query'],'query')
        if a['outcome'] not in {'new_evidence','no_new_evidence','access_blocked'}:
            raise ValueError('Invalid research action outcome')
        actions+=1
        key=(a['question_id'],' '.join(query.lower().split()))
        if key in queries:duplicates.append(aid)
        queries.add(key)
        if a['outcome']!='new_evidence':
            unproductive[a['question_id']]=unproductive.get(a['question_id'],0)+1
    stopped=sorted(q for q,n in unproductive.items() if n>=per_question)
    return {'actions_recorded':actions,'remaining_actions':max(0,limit-actions),
       'budget_exhausted':actions>=limit,'stopped_question_ids':stopped,'duplicate_query_action_ids':duplicates,
       'next_instruction':'Checkpoint unresolved questions and synthesize the limits; do not repeat retrieval.' if actions>=limit else 'Choose the highest material unresolved question outside stopped branches.',
       'note':'Budgets count caller-reported connector actions, not wall time, money or hidden client requests.'}


def promises(case,active_observations):
    result=[]
    for p in case.get('management_promises',[]):
        text(p['id'],'promise.id')
        cutoff=date.fromisoformat(case['as_of']);due=date.fromisoformat(p['due_at'])
        expected=number(p['expected'],'promise expected')
        if p['direction'] not in {'at_least','at_most'}:
            raise ValueError('Promise direction must be at_least/at_most')
        guidance=active_observations.get(p['guidance_observation_id'])
        if not guidance or not guidance['reviewed']:
            continue
        if guidance['kind']!='management_statement':
            raise ValueError('Promise must link to original management statement')
        numeric=guidance.get('numeric')
        if not numeric or expected!=numeric['value']:
            raise ValueError('Promise threshold must match linked original numeric guidance')
        outcomes=[]
        for oid in p.get('outcome_observation_ids',[]):
            o=active_observations.get(oid)
            if not o or not o['reviewed'] or o['kind']!='reported_fact' or not o.get('numeric'):
                continue
            source=next((s for s in case['sources'] if s['id']==o['source_id']),None)
            primary={'filing','earnings_release','regulator','official_statistics'} | ({'synthetic'} if case.get('is_demo') else set())
            if not source or source['kind'] not in primary:
                continue
            n=o['numeric']
            keys=('metric','unit','scope','basis','period_start','period_end')
            if any(n.get(k)!=numeric.get(k) for k in keys):
                continue
            outcomes.append(o)
        values={o['numeric']['value'] for o in outcomes}
        if len(values)>1:
            status='conflicting_outcomes';actual=None
        elif values:
            actual=values.pop()
            met=actual>=expected if p['direction']=='at_least' else actual<=expected
            status='met' if met else 'missed'
        else:
            actual=None;status='due_without_evidence' if due<=cutoff else 'pending'
        result.append({'id':p['id'],'status':status,'expected':expected,'actual':actual,
                       'difference':actual-expected if actual is not None else None,
                       'direction':p['direction'],'definition':numeric,'due_at':p['due_at'],
                       'guidance_observation_id':guidance['id'],'outcome_observation_ids':[o['id'] for o in outcomes]})
    return result
