"""Learning loop: what research rounds teach the engine.

Each research round leaves, per company, the analyst's evidenced inputs (inputs.json) and the executed casefile
(out/casefile.json). Read together across rounds they show where the engine is systematically wrong:

- corrections: every applied override with the engine's own value next to the analyst's, grouped by lever and business
  type, with the direction (the analyst raised or lowered it). The same lever moved the same way for the same kind of
  business in several companies is a candidate engine default change; one company never is (that is judgement).
- special inputs: one-offs removed, debt-like claims added, operating funding, cycle treatments overridden: each use marks a
  pattern the engine did not see by itself.
- warnings: medium and high warnings by code and how many companies raised them.
- engine fixes: the hand-written log of engine bugs found in each round (docs/learning_log.json), so the record shows how
  many fixes each round needed; fewer per company over time is the measure that the engine is learning.
- forecasts: the frozen theses in the forecast ledger (monitor.score), scored only once results are reported.

Candidates are suggestions for the developer, never applied automatically: a default changed because a handful of
companies needed it would fit the engine to its own sample (keep benchmark claims honest)."""
import json
import os
from collections import Counter, defaultdict

CANDIDATE_MIN_COMPANIES = 3     # a lever moved the same way for at least this many companies of one type: candidate default
SPECIAL_INPUTS = ('nonrecurring', 'debt_like_claims', 'operating_funding', 'cyclical')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def company_record(folder, round_name=None):
    """One researched company: {round, ticker, business_model, stage, verdict, price, base, corrections, special, warnings}.
    None when the folder has no executed casefile."""
    cpath = os.path.join(folder, 'out', 'casefile.json')
    if not os.path.exists(cpath):
        return None
    cf = _load(cpath)
    ipath = os.path.join(folder, 'inputs.json')
    inputs = _load(ipath) if os.path.exists(ipath) else {}
    fc_in = inputs.get('forecast') or {}
    fc = cf.get('fc') or {}
    vv = (cf.get('dc') or {}).get('valuation_view') or {}
    card = cf.get('card') or {}
    corr = []
    for o in fc.get('overrides') or []:
        orig, val = o.get('original'), o.get('value')
        num = isinstance(orig, (int, float)) and isinstance(val, (int, float)) and not isinstance(orig, bool)
        corr.append({'path': o['path'], 'scenario': o.get('scenario'), 'original': orig, 'value': val,
                     'direction': ('raised' if val > orig else 'lowered' if val < orig else 'kept') if num else 'set',
                     'relative': (val / orig - 1) if num and orig else None, 'reason': o.get('reason'),
                     'evidence_ids': o.get('evidence_ids') or []})
    special = []
    for k in SPECIAL_INPUTS:
        v = fc_in.get(k)
        if not v:
            continue
        for it in (v if isinstance(v, list) else [v]):
            special.append({'input': k, 'kind': it.get('kind') or it.get('treatment'), 'amount': it.get('amount'),
                            'revenue': it.get('revenue'), 'operating_income': it.get('operating_income'), 'reason': it.get('reason')})
    sheet = inputs.get('analyst_sheet') or {}
    return {'round': round_name or os.path.basename(os.path.dirname(os.path.abspath(folder))),
            'ticker': (cf.get('company') or {}).get('ticker') or os.path.basename(os.path.abspath(folder)),
            'business_model': (card.get('business_model') or {}).get('primary'), 'stage': (card.get('stage') or {}).get('value'),
            'verdict': ((cf.get('dc') or {}).get('verdict') or {}).get('code'), 'price': vv.get('price'), 'base': vv.get('base'),
            'bear': vv.get('bear'), 'bull': vv.get('bull'), 'corrections': corr, 'special': special,
            'warnings': [{'code': w['code'], 'severity': w['severity']} for w in fc.get('warnings') or []],
            'errors': cf.get('errors') or [], 'sheet_questions_read': sorted(sheet)}


def collect(round_dirs):
    """Company records from research-round folders (each holding one folder per company)."""
    out = []
    for rd in round_dirs:
        name = os.path.basename(os.path.abspath(rd))
        for sub in sorted(os.listdir(rd)):
            p = os.path.join(rd, sub)
            if os.path.isdir(p):
                r = company_record(p, name)
                if r:
                    out.append(r)
    return out


def corrections(records):
    """Overrides grouped by (lever, scenario): companies, directions and median relative move; and by (lever, business type)
    in the base scenario, where candidates come from."""
    by_lever, by_type = defaultdict(list), defaultdict(list)
    for r in records:
        for c in r['corrections']:
            row = {'ticker': r['ticker'], 'round': r['round'], 'business_model': r['business_model'], **c}
            by_lever[(c['path'], c['scenario'])].append(row)
            if c['scenario'] in ('base', 'all'):
                by_type[(c['path'], r['business_model'])].append(row)

    def summary(rows):
        rel = sorted(x['relative'] for x in rows if x['relative'] is not None)
        return {'companies': sorted({x['ticker'] for x in rows}), 'directions': dict(Counter(x['direction'] for x in rows)),
                'median_relative': rel[len(rel) // 2] if rel else None,
                'examples': [{k: x[k] for k in ('ticker', 'round', 'original', 'value')} for x in rows[:5]]}
    levers = [{'path': p, 'scenario': s, **summary(rows)} for (p, s), rows in sorted(by_lever.items())]
    types = [{'path': p, 'business_model': t, **summary(rows)} for (p, t), rows in sorted(by_type.items(), key=lambda kv: str(kv[0]))]
    return {'by_lever': levers, 'by_type': types}


def candidates(records, min_companies=CANDIDATE_MIN_COMPANIES):
    """Base-case levers moved the same way for at least min_companies companies of one business type: suggestions only."""
    out = []
    for row in corrections(records)['by_type']:
        for d, n in row['directions'].items():
            if d in ('raised', 'lowered') and n >= min_companies:
                out.append({'path': row['path'], 'business_model': row['business_model'], 'direction': d, 'companies': row['companies'],
                            'median_relative': row['median_relative'],
                            'note': 'candidate engine default change: check the evidence of each company before changing the rule'})
    return out


def special_inputs(records):
    c = Counter((s['input'], s['kind']) for r in records for s in r['special'])
    who = defaultdict(set)
    for r in records:
        for s in r['special']:
            who[(s['input'], s['kind'])].add(r['ticker'])
    return [{'input': i, 'kind': k, 'uses': n, 'companies': sorted(who[(i, k)])} for (i, k), n in c.most_common()]


def warnings(records, severities=('medium', 'high')):
    c, who = Counter(), defaultdict(set)
    for r in records:
        for w in r['warnings']:
            if w['severity'] in severities:
                c[w['code']] += 1
                who[w['code']].add(r['ticker'])
    return [{'code': k, 'count': n, 'companies': sorted(who[k])} for k, n in c.most_common()]


def engine_fixes(log):
    """The engine-fix log ({rounds: [{round, date, companies, fixes: [{ticker, kind, finding, fix}]}]}): fixes per round and
    per company researched."""
    out = []
    for rd in (log or {}).get('rounds') or []:
        n_co = len(rd.get('companies') or [])
        fixes = rd.get('fixes') or []
        out.append({'round': rd.get('round'), 'date': rd.get('date'), 'companies': n_co, 'fixes': len(fixes),
                    'fixes_per_company': (len(fixes) / n_co) if n_co else None,
                    'by_kind': dict(Counter(f.get('kind') for f in fixes))})
    return out


def ledger_status(ledger_path):
    """Frozen theses and whether any forecast can be scored yet (monitor.score on the ledger alone)."""
    if not ledger_path or not os.path.exists(ledger_path):
        return {'status': 'no_ledger'}
    from . import monitor
    rows = []
    with open(ledger_path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    theses = [r for r in rows if r.get('type') == 'thesis']
    try:
        sc = monitor.score(rows)
        scored = len(sc.get('items') or [])
        pending = len(sc.get('not_scored') or [])
    except (ValueError, KeyError, TypeError) as e:
        scored, pending = None, f'score failed: {e}'
    return {'status': 'ok', 'theses': len(theses), 'entities': sorted({t.get('entity') for t in theses}),
            'forecasts': sum(1 for r in rows if r.get('type') == 'forecast'), 'scored': scored, 'not_yet_scorable': pending}


def report(round_dirs, ledger_path=None, log=None):
    recs = collect(round_dirs)
    return {'learning_version': 1, 'rounds': sorted({r['round'] for r in recs}), 'companies': len(recs),
            'records': [{k: r[k] for k in ('round', 'ticker', 'business_model', 'stage', 'verdict', 'price', 'base', 'bear', 'bull')}
                        for r in recs],
            'corrections': corrections(recs), 'candidates': candidates(recs), 'special_inputs': special_inputs(recs),
            'warnings': warnings(recs), 'engine_fixes': engine_fixes(log), 'ledger': ledger_status(ledger_path),
            'errors': [{'ticker': r['ticker'], 'errors': r['errors']} for r in recs if r['errors']]}


def render_he(rep):
    """Short Hebrew summary of a learning report."""
    L = [f"# לולאת למידה – {', '.join(rep['rounds']) or 'אין סבבים'}", '',
         f"חברות שנחקרו: {rep['companies']}."]
    for f in rep['engine_fixes']:
        per = f"{f['fixes_per_company']:.1f}" if f['fixes_per_company'] is not None else '—'
        L.append(f"- סבב {f['round']}: {f['fixes']} תיקוני מנוע על {f['companies']} חברות ({per} לחברה): {f['by_kind']}")
    if rep['corrections']['by_lever']:
        L += ['', '## איפה האנליסט תיקן את המנוע']
        for x in rep['corrections']['by_lever']:
            med = f", חציון שינוי {x['median_relative']:+.0%}" if x['median_relative'] is not None else ''
            L.append(f"- {x['path']} ({x['scenario']}): {', '.join(x['companies'])} – {x['directions']}{med}")
    if rep['special_inputs']:
        L += ['', '## דפוסים שהמנוע לא זיהה לבד']
        L += [f"- {x['input']}/{x['kind']}: {x['uses']} שימושים ({', '.join(x['companies'])})" for x in rep['special_inputs']]
    L += ['', '## מועמדים לשינוי ברירת מחדל (הצעה בלבד)']
    L += [f"- {c['path']} ל־{c['business_model']}: {c['direction']} ב־{len(c['companies'])} חברות" for c in rep['candidates']] or \
         ['- אין עדיין: אף מנוף לא תוקן באותו כיוון ב־3 חברות מאותו סוג.']
    lg = rep['ledger']
    if lg.get('status') == 'ok':
        L += ['', f"## יומן תחזיות: {lg['theses']} תזות קפואות, {lg['forecasts']} תחזיות; נמדדו עד כה: {lg['scored']}"]
    return '\n'.join(L)
