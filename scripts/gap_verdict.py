"""Mechanical pass/fail of gap_backtest_results.json against docs/prereg_expectation_gap.md.
No judgement calls: each criterion is the literal threshold written before the data was seen.

usage: python scripts/gap_verdict.py out_dir/gap_backtest_results.json
"""
import json, sys

r = json.load(open(sys.argv[1]))
V = {}

h = sorted(r['H1']['holdout'], key=lambda x: x['q'])
real = [x['realized'] for x in h]
mono = all(b >= a for a, b in zip(real, real[1:]))
spread = real[-1] - real[0]
V['H1'] = {'monotonic': mono, 'top_minus_bottom': spread, 'threshold': 0.15, 'pass': bool(mono and spread >= 0.15)}

H2 = {}
ok = True
for w in ('design', 'holdout'):
    x = r['H2'][w]
    ex = x['excess_vs_universe_per_yr']
    H2[w] = {'excess_per_yr': ex, 'pass_excess': ex is not None and ex >= 0.02}
    ok &= H2[w]['pass_excess']
hx = r['H2']['holdout']
H2['holdout']['t_stat'] = hx['t_stat']; H2['holdout']['pass_t'] = hx['t_stat'] is not None and hx['t_stat'] >= 2
dd_gap = hx['top_quintile']['max_dd'] - hx['universe']['max_dd']
H2['holdout']['dd_top_minus_universe'] = dd_gap; H2['holdout']['pass_dd'] = dd_gap >= -0.10
ok &= H2['holdout']['pass_t'] and H2['holdout']['pass_dd']
H2['pass'] = bool(ok)
V['H2'] = H2

x = r['H3']['holdout']
a, b = x['momentum_near_high'], x['plus_pbeat_top2']
add = (b['cagr'] - a['cagr']) if a and b else None
V['H3'] = {'momentum_cagr': a and a['cagr'], 'plus_pbeat_cagr': b and b['cagr'], 'added_per_yr': add,
           'halved_a': x['halved12_a'], 'halved_b': x['halved12_b'], 'months_a': a and a['months'], 'months_b': b and b['months'],
           'note': 'months_b < months_a means the filtered sleeve was empty in some months; CAGR is over its own live months',
           'pass': bool(add is not None and add >= 0.02 and x['halved12_b'] is not None and x['halved12_b'] <= x['halved12_a'])}
V['signal_useful_per_prereg'] = V['H1']['pass'] and V['H2']['pass'] and V['H3']['pass']
print(json.dumps(V, indent=1, default=float))
