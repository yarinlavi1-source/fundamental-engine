"""Engine-consistent market return from a large-cap sweep (calibration.market_implied).

    python scripts/calibrate.py runs/universe_2026-10-20 --name large500 --as-of 2026-10-20

Reads companies/QUEUE_<name>.json, each company's casefile summary and the universe market values (rows.json), and writes
<folder>/calibration.json. Use it with `fundamental_engine deep ... --index calibration.json`.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import calibration, pipeline  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--name', default='large500')
    ap.add_argument('--as-of', required=True)
    a = ap.parse_args(argv)
    comp = os.path.join(a.folder, 'companies')
    q = json.load(open(os.path.join(comp, f'QUEUE_{a.name}.json')))['queue']
    caps = {r['symbol']: r['metrics'].get('market_cap') for r in json.load(open(os.path.join(a.folder, 'rows.json')))}
    sums = []
    for e in q:
        p = os.path.join(comp, e['symbol'] or '', 'out', 'casefile.json')
        if e['symbol'] and os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                sums.append(pipeline.summary(json.load(f)))
    out = calibration.market_implied(sums, caps, a.as_of)
    with open(os.path.join(a.folder, 'calibration.json'), 'w') as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ('index_input',)}, indent=1))


if __name__ == '__main__':
    main()
