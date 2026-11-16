"""Portfolio health: the owner's portfolio rules computed from the saved valuations (engine upgrade P9, 15.11.2026).

Usage: python scripts/portfolio_health.py --positions runs/portfolio_<D>/positions.json [--rules runs/portfolio_rules.json]
                                          [--as-of D] [--out runs/portfolio_<D>/health_auto]
Reads the READ-ONLY positions copy (as written from the broker's positions), the private rules file and the saved
valuations in research/; reprices each holding with lean_model at the position's price and date (a custom model's saved
returns are read at that price), and writes portfolio_health.json and portfolio_health_he.md under runs/ only (private).
When the output folder holds a holdings_update.json (python -m fundamental_engine holdings-update ... --out the same
folder), its filing-check status is shown per holding. Rule results, never orders. See fundamental_engine/portfolio_health.py.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fundamental_engine import portfolio_health as PH  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--positions', required=True, help='READ-ONLY positions copy {as_of, source, rows, cash?, ...}')
    ap.add_argument('--rules', default=os.path.join(ROOT, PH.RULES_PATH), help='private rules file (default runs/portfolio_rules.json)')
    ap.add_argument('--as-of', help='the date the positions\' prices stand for (default: the positions\' as_of)')
    ap.add_argument('--out', help='output folder under runs/ (default runs/portfolio_<as_of>/health_auto)')
    a = ap.parse_args(argv)
    with open(a.positions, encoding='utf-8') as f:
        positions = json.load(f)
    if not os.path.isfile(a.rules):
        print(f'portfolio_health: no rules file at {a.rules}: the defaults of CLAUDE.md are used and no holding has a role',
              file=sys.stderr)
    rules = PH.load_rules(a.rules)
    rules['_source'] = os.path.relpath(os.path.abspath(a.rules), ROOT)
    when = a.as_of or positions.get('as_of')
    out = a.out or os.path.join(ROOT, 'runs', f'portfolio_{when}', 'health_auto')
    hu_path = os.path.join(out, 'holdings_update.json')
    hu = json.load(open(hu_path, encoding='utf-8')) if os.path.isfile(hu_path) else None
    doc = PH.health(positions, rules, ROOT, when, holdings_update=hu)
    paths = PH.write(doc, out, ROOT)
    print(json.dumps({'as_of': doc['as_of'], 'lagging_core_reading': doc['lagging_core_reading']['value'],
                      'rules_drafts': len(doc['rules_drafts']), 'rules_problems': doc['rules_problems'],
                      'holdings': len(doc['rows']), 'gaps': {r['ticker']: r['gaps'] for r in doc['rows'] if r['gaps']},
                      **paths}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
