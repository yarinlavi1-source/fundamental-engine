"""Rank every analysed company by its edge over the engine-consistent market return.

    python scripts/rank_opportunities.py runs/universe_2026-10-20 --names large500 holdings '' --calibration calibration.json

Reads companies/QUEUE[_name].json for each name ('' = the discovery queue), loads each company's casefile, and writes
companies/opportunities.json / opportunities_he.md: base implied return minus the engine market return, bear-case loss,
quality, confidence, stage, verdict, material disqualifiers and the discovery lane or holding status. A ranking of research
priority on one stated measure, shown with its risk columns; not a recommendation by itself.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import handoff  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--names', nargs='+', default=['large500', 'holdings', ''])
    ap.add_argument('--calibration')
    ap.add_argument('--top', type=int, default=40)
    a = ap.parse_args(argv)
    comp = os.path.join(a.folder, 'companies')
    cal = json.load(open(os.path.join(a.folder, a.calibration))) if a.calibration else None
    mkt = (cal or {}).get('engine_market_return')
    origin, cfs = {}, {}
    for n in a.names:
        qf = os.path.join(comp, f'QUEUE_{n}.json' if n else 'QUEUE.json')
        if not os.path.exists(qf):
            continue
        for e in json.load(open(qf))['queue']:
            s = e.get('symbol')
            cp = os.path.join(comp, s or '', 'out', 'casefile.json')
            if s and os.path.exists(cp):
                origin.setdefault(s, []).append(n or f"discovery:{e.get('lane')}")
                if s not in cfs:
                    with open(cp, encoding='utf-8') as f:
                        cfs[s] = json.load(f)
    cards = json.load(open(os.path.join(a.folder, 'cards.json'))) if os.path.exists(os.path.join(a.folder, 'cards.json')) else None
    rows = handoff.compare(list(cfs.values()), cards)['rows']
    for r in rows:
        r['origin'] = origin.get(r['symbol'])
        b = r['irr'].get('base')
        r['edge_vs_engine_market'] = b - mkt if b is not None and mkt is not None else None
        r['stage'] = ((cfs[r['symbol']].get('card') or {}).get('stage') or {}).get('value')
    ranked = sorted([r for r in rows if r['edge_vs_engine_market'] is not None], key=lambda r: -r['edge_vs_engine_market'])
    out = {'engine_market_return': mkt, 'n': len(rows), 'ranked': ranked,
           'no_return': [r['symbol'] for r in rows if r['edge_vs_engine_market'] is None]}
    with open(os.path.join(comp, 'opportunities.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=str)
    p = lambda x: 'n/a' if x is None else f'{x * 100:.1f}%'
    lines = [f"שוק לפי אותו מנוע: {p(mkt)} לשנה. {len(ranked)} חברות עם תשואה משתמעת, {len(out['no_return'])} בלי.", '',
             '| # | מניה | מקור | שלב | פסק דין | איכות | ביטחון | תשואה בסיס | יתרון על השוק | הפסד בדובי | פסילה מהותית |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for i, r in enumerate(ranked[:a.top], 1):
        lines.append(f"| {i} | {r['symbol']} | {', '.join(r['origin'] or [])} | {r['stage']} | {r['verdict']} | {r['levels'].get('business_quality')} | "
                     f"{r['confidence']} | {p(r['irr'].get('base'))} | {p(r['edge_vs_engine_market'])} | {p(r['loss_to_bear'])} | "
                     f"{'כן' if r['material_disqualifier'] else ''} |")
    with open(os.path.join(comp, 'opportunities_he.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines[:a.top + 4]))


if __name__ == '__main__':
    main()
