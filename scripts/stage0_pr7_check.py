"""Stage 0, E8: check PR #7's point-in-time layer (fundamental_engine.pit) against the frozen golden snapshots.

For every golden company the PIT record is built twice: from the full companyfacts file (which contains filings made
after the as-of date) and from the golden point-in-time view (only facts filed on or before as-of - 2 days). If
pit.available() never lets a later filing through, the TTM revenue / latest values seen at the as-of date must be
identical. Also lists where pit.shares_at finds no usable share count (multi-class cover pages), to compare with the
golden manual-extraction list. Read-only; changes nothing in PR #7.

usage: python scripts/stage0_pr7_check.py GOLDEN_DIR OUT_JSON
"""
import datetime as dt, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from fundamental_engine import pit  # noqa: E402

gdir, out = sys.argv[1:3]
golden = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'stage0', 'golden_set.json')))
avail = json.load(open(os.path.join(gdir, 'availability.json')))
res = []
for co in golden['companies']:
    tk, cik, asof = co['tk'], co['cik'], co['asof']
    t = dt.date.fromisoformat(asof)
    full = json.load(open(os.path.join(gdir, 'sec', 'companyfacts', f'CIK{cik:010d}.json')))
    view = json.load(open(os.path.join(gdir, 'pit', f'{tk}_{asof}.json')))
    # rebuild a companyfacts-shaped dict from the golden view (filtered facts only)
    f = {}
    for r in view['facts']:
        row = {k: v for k, v in r.items() if k not in ('tx', 'tag', 'unit')}
        f.setdefault(r['tx'], {}).setdefault(r['tag'], {'units': {}})['units'].setdefault(r['unit'], []).append(row)
    filt = dict(entityName=full.get('entityName'), facts=f)
    a, b = pit.company(full), pit.company(filt)
    row = dict(tk=tk, asof=asof)
    same = True
    for key in ('rev', 'oi', 'ni', 'ocf', 'capex'):
        if key not in a:
            continue
        x, y = pit.ttm_at(a[key], t), pit.ttm_at(b[key], t)
        row[f'ttm_{key}_full'] = x
        row[f'ttm_{key}_golden'] = y
        same &= x == y
        late = [p for p, (v, fd) in a[key].items() if fd > view['cutoff_filed_on_or_before'] and (p, v) in pit.available(a[key], t)]
        if late:
            row[f'{key}_late_facts_used'] = late
            same = False
    sa, sb = pit.shares_at(a, t), pit.shares_at(b, t)
    row['shares_at_full'], row['shares_at_golden'] = sa, sb
    same &= sa == sb
    row['identical'] = same
    row['shares_missing_in_pit'] = sa[1] is None
    row['golden_manual_cover_shares'] = any('cover-page shares' in m for m in avail[tk]['manual_extraction'])
    res.append(row)
json.dump(res, open(out, 'w'), indent=1, default=str)
for r in res:
    print(f"{r['tk']:5} {r['asof']} identical={r['identical']} ttm_rev={r.get('ttm_rev_full')} shares={r['shares_at_full']} "
          f"pit_missing_shares={r['shares_missing_in_pit']} golden_manual={r['golden_manual_cover_shares']}")
print('all identical:', all(r['identical'] for r in res))
