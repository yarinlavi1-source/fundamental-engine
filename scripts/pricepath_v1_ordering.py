"""Post-run diagnostic of the spec-1 expected price path lane (docs/PRICE_PATH_2026-10.md; docs/prereg_price_path.md section 9).
Read-only on the saved lane rows (scripts/backtest_pricepath.py output, run 21.10.2026), no fundamental_engine imports,
deterministic. Not part of the preregistered evaluation; it changes nothing in spec 1.

Scenario-ordering audit of the saved lane rows (spec 1): counts of rows where the bear's multiple / justified multiple /
growth / price is not below the base's, or the bull's not above, in at least one of the five years; the mechanism is the
scenario half-life rule (HL_bear = 0.8 HL) applied to a NEGATIVE excess growth or a NEGATIVE premium D_0, which then closes
FASTER in the bear. Read-only. Usage: scripts/pricepath_v1_ordering.py <rows.jsonl>"""
import json, sys, statistics
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]
ok = [r for r in rows if r.get('status') == 'ok']
n = len(ok)
def anyyear(r, f):
    return any(f(r['scenarios']['bear']['years'][i], r['scenarios']['base']['years'][i], r['scenarios']['bull']['years'][i]) for i in range(5))
c = {
    'bear multiple >= base multiple': sum(1 for r in ok if anyyear(r, lambda b, m, u: b['multiple'] >= m['multiple'])),
    'bear justified > base justified': sum(1 for r in ok if anyyear(r, lambda b, m, u: b['justified_multiple'] > m['justified_multiple'])),
    'bear growth > base growth': sum(1 for r in ok if anyyear(r, lambda b, m, u: b['growth'] > m['growth'])),
    'bear price >= base price': sum(1 for r in ok if anyyear(r, lambda b, m, u: b['price'] >= m['price'])),
    'bull price <= base price': sum(1 for r in ok if anyyear(r, lambda b, m, u: u['price'] <= m['price'])),
    'bull multiple <= base multiple': sum(1 for r in ok if anyyear(r, lambda b, m, u: u['multiple'] <= m['multiple'])),
    'bear premium_left > base premium_left (negative D_0 closing faster)': sum(1 for r in ok if anyyear(r, lambda b, m, u: b['premium_left'] > m['premium_left'])),
}
neg = sum(1 for r in ok if r['premium_today'] < 0)
negx = sum(1 for r in ok if r['anchors']['g_run'] < r['scenarios']['base']['years'][0]['growth'] - 1e-9 or False)
print(f'ok rows {n}; rows with negative premium D_0 {neg} ({neg/n:.1%})')
for k, v in c.items():
    print(f'{k}: {v} ({v/n:.1%})')
# year-5 size of the inversion where it happens
inv = [r for r in ok if r['scenarios']['bear']['years'][4]['multiple'] >= r['scenarios']['base']['years'][4]['multiple']]
if inv:
    d = [r['scenarios']['bear']['years'][4]['multiple'] - r['scenarios']['base']['years'][4]['multiple'] for r in inv]
    print(f'year-5 bear multiple minus base multiple where >= 0: n {len(inv)}, median {statistics.median(d):+.2f}x, max {max(d):+.2f}x')
pr = [r for r in ok if r['scenarios']['bear']['years'][4]['price'] >= r['scenarios']['base']['years'][4]['price']]
print('year-5 bear price >= base price:', len(pr), sorted({(r['symbol'], r['formation'][:4]) for r in pr})[:12])
# margin floor in the bear by margin level
thin = [r for r in ok if r['anchors']['m_anchor'] < .05]
print(f"rows with m_anchor < 5%: {len(thin)} ({len(thin)/n:.1%}); of which margin_floored_bear: {sum(1 for r in thin if 'margin_floored_bear' in r['flags'])}; "
      f"all margin_floored_bear: {sum(1 for r in ok if 'margin_floored_bear' in r['flags'])}")
# bear year-5 NI relative to normalized NI_0
ratio = [r['scenarios']['bear']['years'][4]['net_income'] / (r['anchors']['revenue'] * r['anchors']['m_anchor']) for r in ok]
print(f'bear year-5 NI / normalized NI_0: p10 {sorted(ratio)[int(.1*n)]:.2f} median {statistics.median(ratio):.2f} p90 {sorted(ratio)[int(.9*n)]:.2f}; '
      f'base: median {statistics.median(r["scenarios"]["base"]["years"][4]["net_income"] / (r["anchors"]["revenue"] * r["anchors"]["m_anchor"]) for r in ok):.2f}')
