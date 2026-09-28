"""Reproducible underwriting checks, separate from arithmetic and discovery.

Passing establishes internal consistency of supplied evidence, never its truth.
No rule requires profits, low multiples, agreement with analysts, or a high value.
"""
from datetime import date
from math import isclose
from .finance import number


def audit_valuation(case, results):
    issues, comparisons, transitions = [], [], []
    sources = {s['id']: s for s in case['sources']}
    assumptions = {a['id']: a for a in case['assumptions']}
    cutoff = date.fromisoformat(case['as_of'])

    def add(code, message, path=''):
        issues.append({'code': code, 'path': path, 'message': message})

    def evidence(node, path, primary=False):
        ids = node.get('assumption_ids', []) if isinstance(node, dict) else []
        valid = bool(ids) and all(i in assumptions and assumptions[i]['review_status'] == 'reviewed' for i in ids)
        if primary and valid:
            valid = any(sources[s].get('kind') in ({'filing', 'earnings_release', 'issuer_release', 'synthetic'} if case.get('is_demo') else {'filing', 'earnings_release', 'issuer_release'})
                        for i in ids for s in assumptions[i]['source_ids'])
        if not valid:
            add('evidence_missing', 'Supply reviewed, source-linked assumptions' + (' including a primary source.' if primary else '.'), path)
        return valid

    def equal(actual, expected, path):
        if not isclose(actual, expected, rel_tol=1e-7, abs_tol=.01):
            add('opening_mismatch', f'Expected {expected:g}, supplied {actual:g}. Reconcile definitions and bridge events.', path)

    u = case.get('underwriting', {})
    if not u:
        add('underwriting_missing', 'Supply opening reconciliation, scenario reviews and consensus review.', 'underwriting')
    for a in case['assumptions']:
        if a['review_status'] != 'reviewed':
            add('unreviewed_assumption', 'Material inputs have not been reviewed.', a['id'])
    if (cutoff - date.fromisoformat(case['quote']['as_of'])).days > 7:
        add('stale_quote', 'Refresh the dated comparison quote.', 'quote')
    if any(r['status'] == 'funding_blocked' for r in results):
        add('funding_gap', 'Resolve cash requirements before a going-concern price conclusion.')
    for when in case['report_dates']:
        values = {r['name']: next((p['value_per_share'] for p in r['timeline'] if p['date'] == when), None) for r in results}
        if all(values.get(k) is not None for k in ('bear', 'base', 'bull')) and not values['bear'] <= values['base'] <= values['bull']:
            add('scenario_order', 'Scenario labels cross; resolve labels before publishing price categories.', when)
        if values.get('tail') is not None and values.get('bull') is not None and values['tail'] < values['bull']:
            add('scenario_order', 'Tail outcome is below bull; revise scenario labels.', when)

    if case['method'] == 'operating':
        rec = u.get('opening_reconciliation', {})
        if rec:
            evidence(rec, 'opening_reconciliation', primary=True)
            when = date.fromisoformat(rec['statement_as_of'])
            if when > cutoff:
                raise ValueError('Opening reconciliation is after valuation date')
            if (cutoff - when).days > 150:
                add('stale_statement', 'Opening statement is over 150 days old; use a more recent statement.')
            if rec.get('share_basis') != 'point_in_time_common_outstanding':
                add('share_basis', 'Weighted-average EPS shares cannot stand in for current outstanding shares.')
            fields = ('cash_and_equivalents', 'restricted_cash', 'short_term_investments',
                      'long_term_investments', 'investment_haircut', 'debt',
                      'common_shares_outstanding', 'incremental_dilutive_shares')
            vals = {k: number(rec[k], k, 0) for k in fields}
            investments = vals['short_term_investments'] + vals['long_term_investments'] - vals['investment_haircut']
            if investments < 0:
                raise ValueError('Investment haircut exceeds investments')
            # cash_and_equivalents excludes restricted cash. Never subtract it twice.
            expected = {'unrestricted_cash': vals['cash_and_equivalents'], 'marketable_investments': investments,
                        'debt': vals['debt'], 'shares': vals['common_shares_outstanding'] + vals['incremental_dilutive_shares']}
            events = rec.get('bridge_events', [])
            if when < cutoff:
                evidence(rec.get('bridge_review', {}), 'opening_reconciliation.bridge_review')
            for event in events:
                evidence(event, 'opening_reconciliation.bridge_events')
                if not when < date.fromisoformat(event['date']) <= cutoff:
                    raise ValueError('Bridge event outside statement/valuation interval')
                for k in expected:
                    expected[k] += number(event.get(k + '_delta', 0), k + '_delta')
            for k, v in expected.items():
                equal(number(case['opening'].get(k, 0), k, 0), v, 'opening.' + k)
            evidence(rec.get('dilution_review', {}), 'opening_reconciliation.dilution_review')
            evidence(rec.get('claims_review', {}), 'opening_reconciliation.claims_review')
        else:
            add('opening_reconciliation_missing', 'Reconcile cash, investments, debt, ownership claims and point-in-time diluted shares.')

        reviews = u.get('scenario_reviews', {})
        for s, r in zip(case['scenarios'], results):
            name = s['name']; sr = reviews.get(name, {})
            for key in ('demand_and_capacity', 'funding_and_claims', 'competitive_response', 'maturity'):
                evidence(sr.get(key, {}), name + '.' + key)
            rows = r['forecast']; last = rows[-1]
            dur = (date.fromisoformat(last['end']) - date.fromisoformat(last['start'])).days / 365.25
            margin = last['ebit'] / last['revenue'] if last['revenue'] else 0
            growth = None
            if len(rows) > 1 and rows[-2]['revenue'] > 0:
                prev = rows[-2]
                pdur = (date.fromisoformat(prev['end']) - date.fromisoformat(prev['start'])).days / 365.25
                growth = (last['revenue'] / dur) / (prev['revenue'] / pdur) - 1
            jump = abs(s['terminal']['operating_margin'] - margin)
            cliff = growth is not None and abs(growth - s['terminal']['growth']) > .10
            transition = {'scenario': name, 'terminal_date': last['end'], 'last_annualized_growth': growth,
                          'terminal_growth': s['terminal']['growth'], 'last_operating_margin': margin,
                          'terminal_operating_margin': s['terminal']['operating_margin'],
                          'growth_cliff': cliff, 'margin_jump': jump > .05}
            transitions.append(transition)
            if cliff or jump > .05 or dur < .9:
                evidence(sr.get('transition_exception', {}), name + '.transition_exception')
            if last['maintenance_capex'] == 0 and case['archetype'] in {'industrial', 'infrastructure', 'commodity'}:
                evidence(sr.get('replacement_exception', {}), name + '.replacement_exception')
    else:
        evidence(u.get('specialist_review', {}), 'underwriting.specialist_review', primary=True)

    consensus = u.get('consensus_review', {})
    status = consensus.get('status')
    if status not in {'compared', 'unavailable', 'not_applicable'}:
        add('consensus_review_missing', 'Compare like-for-like estimates, or document unavailable/not-applicable coverage.')
    evidence(consensus, 'consensus_review')
    entries = consensus.get('comparisons', [])
    if status == 'compared' and not entries:
        add('comparisons_missing', 'A compared status requires actual estimate comparisons.')
    for c in entries:
        path = 'consensus.' + c['id']
        evidence(c, path)
        if c['currency'] != case['currency'] or c['unit'] != 'absolute':
            raise ValueError('Consensus currency/unit differs from model')
        if date.fromisoformat(c['available_at']) > cutoff or date.fromisoformat(c['as_of']) > date.fromisoformat(c['available_at']):
            raise ValueError('Consensus unavailable at valuation cutoff')
        if (cutoff - date.fromisoformat(c['as_of'])).days > 90:
            add('stale_estimate', 'Estimate is over 90 days old; refresh or document coverage unavailable.', path)
        result = next((r for r in results if r['name'] == c['scenario']), None)
        if result is None:
            raise ValueError('Consensus comparison has unknown scenario')
        periods = [r for r in result['forecast'] if r.get('start', '') >= c['period_start'] and r.get('end', '') <= c['period_end']]
        if not periods or periods[0]['start'] != c['period_start'] or periods[-1]['end'] != c['period_end']:
            add('estimate_period_mismatch', 'Compare a complete matching forecast interval, not a stub or a different fiscal year.', path)
            continue
        metric = c['metric']
        if metric not in {'revenue', 'ebit', 'earnings_proxy_per_share'}:
            raise ValueError('Unsupported comparable model metric')
        if metric == 'earnings_proxy_per_share':
            model = sum((p['ebit'] - p['interest'] - p['cash_taxes']) / p['shares'] for p in periods)
            if not c.get('basis_reconciliation'):
                add('eps_basis_mismatch', 'Operating earnings proxy is not GAAP/adjusted attributable EPS. Supply explicit reconciliation.', path)
                continue
        else:
            model = sum(p[metric] for p in periods)
        external = number(c['external_value'], 'external_value')
        if c.get('basis_reconciliation'):
            br = c['basis_reconciliation']; evidence(br, path + '.basis_reconciliation')
            external += number(br['adjustment_to_model_basis'], 'adjustment_to_model_basis')
        elif c['external_basis'] != c['model_basis']:
            add('estimate_basis_mismatch', 'GAAP, adjusted and currency/accounting definitions need reconciliation.', path)
            continue
        gap = (model - external) / abs(external) if external else None
        comparisons.append({'id': c['id'], 'metric': metric, 'model': model, 'external_on_model_basis': external,
                            'relative_difference': gap, 'period_end': c['period_end']})
        if (gap is not None and abs(gap) > .20) or (external == 0 and model != 0):
            evidence(c.get('difference_explanation', {}), path + '.difference_explanation')
    for issue in u.get('material_issues', []):
        if issue['status'] == 'resolved':
            evidence(issue, 'material_issues.' + issue['id'])
        elif issue['status'] == 'unresolved':
            add('material_issue_unresolved', issue['description'], issue['id'])
        else:
            raise ValueError('Material issue status must be resolved/unresolved')
    return {'eligible_for_research_synthesis': not issues and not case.get('is_demo', False),
            'status': 'requires_review' if issues else 'internally_reconciled',
            'issues': issues, 'comparisons': comparisons, 'terminal_transitions': transitions,
            'meaning': 'Checks supplied definitions and evidence references, not source truth. Full research_review is still required. Bull is a modeled scenario, never a price ceiling.'}
