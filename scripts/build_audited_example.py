"""Build a fully populated fictional underwriting contract; no real price target."""
import json
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def example():
    c = json.loads((ROOT/'examples/valuation/infrastructure.json').read_text())
    ref = {'assumption_ids': ['demo']}
    c['opening']['marketable_investments'] = 100_000_000
    c['underwriting'] = {
        'opening_reconciliation': {
            **ref, 'statement_as_of': c['as_of'], 'share_basis': 'point_in_time_common_outstanding',
            'cash_and_equivalents': 1_000_000_000, 'restricted_cash': 12_000_000,
            'short_term_investments': 20_000_000, 'long_term_investments': 80_000_000,
            'investment_haircut': 0, 'debt': 300_000_000,
            'common_shares_outstanding': 98_000_000, 'incremental_dilutive_shares': 2_000_000,
            'dilution_review': deepcopy(ref), 'claims_review': deepcopy(ref), 'bridge_events': []},
        'scenario_reviews': {s['name']: {k: deepcopy(ref) for k in (
            'demand_and_capacity', 'funding_and_claims', 'competitive_response', 'maturity',
            'transition_exception', 'replacement_exception')} for s in c['scenarios']},
        'consensus_review': {**ref, 'status': 'not_applicable'}, 'material_issues': []}
    # Every reference deliberately points to the synthetic assumption. In a real
    # dossier each rationale/falsifier must substantiate its own economic claim.
    return c

if __name__ == '__main__':
    p = ROOT/'examples/valuation/audited_infrastructure.json'
    p.write_text(json.dumps(example(), indent=2, ensure_ascii=False)+'\n')
    print(p.relative_to(ROOT))
