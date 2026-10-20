"""Learning-loop report across research rounds.

    python scripts/learning_report.py ROUND_DIR [ROUND_DIR ...] [--ledger runs/ledger/forecasts.jsonl]
                                      [--log docs/learning_log.json] [--out runs/learning]

Reads each round's per-company inputs.json and out/casefile.json (fundamental_engine/learning.py) and writes report.json and
report_he.md to --out (git-ignored runs/ by default). Candidate default changes are suggestions; nothing is changed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import learning  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('rounds', nargs='+')
    ap.add_argument('--ledger')
    ap.add_argument('--log', default=os.path.join(os.path.dirname(__file__), '..', 'docs', 'learning_log.json'))
    ap.add_argument('--out', default='runs/learning')
    a = ap.parse_args(argv)
    log = json.load(open(a.log, encoding='utf-8')) if a.log and os.path.exists(a.log) else None
    rep = learning.report(a.rounds, a.ledger, log)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=str)
    text = learning.render_he(rep)
    with open(os.path.join(a.out, 'report_he.md'), 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
