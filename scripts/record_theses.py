"""Freeze theses and forecasts for a set of casefiles into the append-only, hash-chained forecast ledger.

    python scripts/record_theses.py runs/ledger/forecasts.jsonl CASEFILE [CASEFILE ...]

Each casefile becomes a dated thesis (monitor.thesis_from_casefile: assumptions with falsifiers, bear/base/bull forecasts per
fiscal year, material triggers, review date) written at the time it is made (monitor.freeze refuses backdating). Later,
monitor.score compares the frozen forecasts with reported results; nothing is adjusted after the fact. Drafts are recorded
too, labelled with their review status, so the record shows what the engine said before the outcome was known.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import monitor  # noqa: E402


def main(argv=None):
    argv = argv or sys.argv[1:]
    ledger, paths = argv[0], argv[1:]
    os.makedirs(os.path.dirname(ledger) or '.', exist_ok=True)
    out = []
    for p in paths:
        with open(p, encoding='utf-8') as f:
            cf = json.load(f)
        th = monitor.thesis_from_casefile(cf)
        r = monitor.freeze(ledger, th)
        out.append({'ticker': th['ticker'], 'thesis_id': th['thesis_id'], 'verdict': th['verdict'], 'review_status': th['review_status'],
                    'written': len(r['written']), 'skipped': len(r['skipped']), 'rejected': len(r['rejected']),
                    'ledger_ok': (r.get('ledger') or {}).get('ok')})
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
