"""13.11.2026 'after the checks' sensitivities on notes13/params.json (base unchanged; stable-valuation rule).
Financing realism, 2028 revenue level, VIE note financing. Writes checks/params_*.json, checks/result_*.json, checks/checks.json."""
import json, copy, subprocess, os, sys, tempfile
# research copy: the path lines below replace the run folder's absolute paths (nothing else in this file changed);
# the base params are this folder's lean_params.json (byte-identical to the run's notes13/params.json) and the output
# goes to NBIS_RERUN_OUT (default: a new temp folder), never into this folder
N = os.path.dirname(os.path.abspath(__file__)) + '/'
ROOT = os.environ.get('FE_ROOT') or os.path.abspath(os.path.join(N, '..', '..', '..', '..'))
C = (os.environ.get('NBIS_RERUN_OUT') or tempfile.mkdtemp(prefix='nbis_checks13_')).rstrip('/') + '/'
LM = [sys.executable, os.path.join(ROOT, 'scripts', 'lean_model.py')]
P = json.load(open(N + 'lean_params.json'))
def run(p, name):
    fp, fo = C + 'params_' + name + '.json', C + 'result_' + name + '.json'
    json.dump(p, open(fp, 'w'), indent=1)
    subprocess.run(LM + [fp, '--out', fo], check=True, capture_output=True)
    r = json.load(open(fo)); b = r['scenarios']['base']; c = r['two_lens']['calibrated']
    return {'base_today': b['value_today'], 'base_2028': b['year_end_values']['2028'], 'base_2031': b['year_end_values']['2031'],
            'ours_pct': b['annual_return_pct'], 'ours_band': r['two_lens']['ours']['band'], 'cal_today': c['value_today'],
            'cal_2031': c['year_end_values']['2031'], 'cal_pct': c['annual_return_pct'], 'cal_band': c['band'],
            'bull_today': r['scenarios']['bull']['value_today'], 'bull_pct': r['scenarios']['bull']['annual_return_pct'],
            'buy_price_ours': r['buy_price'], 'buy_price_cal': round(c['year_end_values']['2031'] / 1.12 ** P['years_to_horizon'], 2)}
def equity(p, usd_m, px):
    q = copy.deepcopy(p); n = usd_m / px
    q['net_cash_m'] += usd_m; q['shares_m'] = round(q['shares_m'] + n, 1)
    for k in ('bull', 'bear'):
        q['scenarios'][k]['shares_m'] = round(q['scenarios'][k]['shares_m'] + n, 1)
    return q
def rev28(p, level):
    q = copy.deepcopy(p); g = q['scenarios']['base']['g']; r27 = P['rev0_m'] * (1 + g[0])
    g[1] = round(level / r27 - 1, 4); return q
out = {'new_base': run(P, 'base_rerun')}
for r in (0.085, 0.10, 0.12):
    out[f'debt {r:.1%}'] = run(dict(copy.deepcopy(P), debt_rate=r), f'debt_{int(r*1000)}')
out['debt 8.5% + $6B equity at $95 (financing-realistic)'] = run(equity(dict(copy.deepcopy(P), debt_rate=0.085), 6000, 95), 'fin_6b_95')
out['debt 8.5% + $8B equity at $80'] = run(equity(dict(copy.deepcopy(P), debt_rate=0.085), 8000, 80), 'fin_8b_80')
out['debt 8.5% + $8B equity at $60'] = run(equity(dict(copy.deepcopy(P), debt_rate=0.085), 8000, 60), 'fin_8b_60')
out['2028 revenue 19,000 (later rates and charges kept)'] = run(rev28(P, 19000), 'rev28_19000')
out['2028 revenue 17,500 (no growth after Dec-2027)'] = run(rev28(P, 17500), 'rev28_17500')
out['2028 19,000 + financing-realistic'] = run(equity(dict(rev28(P, 19000), debt_rate=0.085), 6000, 95), 'rev28_19000_fin')
out['VIE: intended 662.2 advanced, kept at zero'] = run(dict(copy.deepcopy(P), net_cash_m=P['net_cash_m'] - 662.2), 'vie_662')
out['VIE: Q4 advance 230.6 at face'] = run(dict(copy.deepcopy(P), net_cash_m=P['net_cash_m'] + 230.6), 'vie_230_face')
out['VIE: all notes at face 348.6+230.6'] = run(dict(copy.deepcopy(P), net_cash_m=P['net_cash_m'] + 579.2), 'vie_579_face')
json.dump(out, open(C + 'checks.json', 'w'), indent=1)
for k, v in out.items():
    print(f"{k:55s} base {v['base_today']:7.2f} 2031 {v['base_2031']:7.2f} ours {v['ours_pct']:5.1f}% {v['ours_band']:8s} | cal {v['cal_today']:7.2f} {v['cal_pct']:5.1f}% {v['cal_band']:8s} | 12%: {v['buy_price_ours']} / {v['buy_price_cal']}")
