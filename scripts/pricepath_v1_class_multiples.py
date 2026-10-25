"""Post-run diagnostic of the spec-1 expected price path lane (docs/PRICE_PATH_2026-10.md; docs/prereg_price_path.md section 9).
Read-only on the saved lane rows (scripts/backtest_pricepath.py output, run 21.10.2026), no fundamental_engine imports,
deterministic. Not part of the preregistered evaluation; it changes nothing in spec 1.

What the market paid, point in time: median raw trailing P/E (price x cover shares / TTM net income) of the lane's ok rows
at each 30-June formation, by run-rate growth bucket and by stage, 2016-2025 (today's survivors). Diagnostic for the v2
preregistration (a contemporaneous cross-section is point in time by construction). Usage: scripts/pricepath_v1_class_multiples.py <rows.jsonl>"""
import json, sys, statistics
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]
ok = [r for r in rows if r.get('status') == 'ok']
def pe(r):
    a = r['anchors']; return r['price'] * a['shares'] / a['net_income']
GB = ((0, '<0%'), (.05, '0-5%'), (.10, '5-10%'), (.20, '10-20%'), (.30, '20-30%'), (9, '>30%'))
def gb(g):
    for lim, lab in GB:
        if g < lim: return lab
MB = ((.05, 'm<5%'), (.10, '5-10%'), (.20, '10-20%'), (9, '>20%'))
def mb(m):
    for lim, lab in MB:
        if m < lim: return lab
years = sorted({int(r['formation'][:4]) for r in ok})
sane = [r for r in ok if 1 <= pe(r) <= 500]
print(f'ok rows {len(ok)}, with raw P/E in [1, 500] {len(sane)}')
print('median raw trailing P/E by formation year (all): ' + ', '.join(f"{y}: {statistics.median(pe(r) for r in sane if int(r['formation'][:4]) == y):.1f}x (n {sum(1 for r in sane if int(r['formation'][:4]) == y)})" for y in years))
print('\nmedian raw P/E by run-rate growth bucket x year (n):')
print('bucket  ' + ' '.join(f'{y:>10d}' for y in years) + '   all')
for _, lab in GB:
    cells = []
    for y in years:
        s = [pe(r) for r in sane if int(r['formation'][:4]) == y and gb(r['anchors']['g_run']) == lab]
        cells.append(f'{statistics.median(s):5.1f}({len(s):3d})' if len(s) >= 5 else '      -   ')
    s = [pe(r) for r in sane if gb(r['anchors']['g_run']) == lab]
    print(f'{lab:7s} ' + ' '.join(cells) + f'  {statistics.median(s):5.1f}({len(s)})')
print('\nmedian raw P/E by TTM net-margin bucket x year:')
print('bucket  ' + ' '.join(f'{y:>10d}' for y in years))
for _, lab in MB:
    cells = []
    for y in years:
        s = [pe(r) for r in sane if int(r['formation'][:4]) == y and mb(r['anchors']['m_ttm']) == lab]
        cells.append(f'{statistics.median(s):5.1f}({len(s):3d})' if len(s) >= 5 else '      -   ')
    print(f'{lab:7s} ' + ' '.join(cells))
print('\nmedian raw P/E by growth bucket x margin bucket (all years), n:')
for _, g in GB:
    print(f'{g:7s} ' + '  '.join(f"{m}: {statistics.median([pe(r) for r in sane if gb(r['anchors']['g_run']) == g and mb(r['anchors']['m_ttm']) == m] or [float('nan')]):5.1f}x({sum(1 for r in sane if gb(r['anchors']['g_run']) == g and mb(r['anchors']['m_ttm']) == m):4d})" for _, m in MB))
print('\nmedian raw P/E by stage x year:')
for st in ('early', 'growth', 'maturing', 'mature', 'decline', 'turnaround'):
    cells = []
    for y in years:
        s = [pe(r) for r in sane if int(r['formation'][:4]) == y and r['stage'] == st]
        cells.append(f'{statistics.median(s):5.1f}({len(s):3d})' if len(s) >= 5 else '      -   ')
    print(f'{st:10s} ' + ' '.join(cells))
print('\nthe lane\'s justified multiple today, median by growth bucket (all years): ' + ', '.join(
    f"{lab}: {statistics.median(r['justified_today'] for r in sane if gb(r['anchors']['g_run']) == lab):.1f}x" for _, lab in GB))
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))]
print('\nraw P/E p25 / median / p75 by run-rate growth bucket (all years) -- candidate bear / base / bull multiple targets:')
for _, lab in GB:
    s = [pe(r) for r in sane if gb(r['anchors']['g_run']) == lab]
    print(f'  {lab:7s} n {len(s):4d}: {pct(s, .25):5.1f}x / {pct(s, .5):5.1f}x / {pct(s, .75):5.1f}x')
print('same by formation year, 5-10% bucket: ' + ', '.join(f"{y}: {pct([pe(r) for r in sane if int(r['formation'][:4]) == y and gb(r['anchors']['g_run']) == '5-10%'], .25):.0f}/{pct([pe(r) for r in sane if int(r['formation'][:4]) == y and gb(r['anchors']['g_run']) == '5-10%'], .5):.0f}/{pct([pe(r) for r in sane if int(r['formation'][:4]) == y and gb(r['anchors']['g_run']) == '5-10%'], .75):.0f}" for y in years))
