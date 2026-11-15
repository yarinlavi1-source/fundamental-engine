"""FINISH lane, 13.11.2026: apply the red-team findings to the sell ladder (imports model13 and pe unchanged; ESTIMATE)."""
import sys, io, contextlib, json, os
# research copy: the path lines replace the run folder's absolute paths; the result goes to model13.OUT_DIR
M = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, M)
with contextlib.redirect_stdout(io.StringIO()):
    import model13 as m
import pe
T, DIV, CL31, KE = 5.13, 12.3, 4.4, 0.1096
P = 634.68; P_EPS27 = 33.97; PE_T = P / P_EPS27
def r(v31, p, t=T, div=DIV): return (((v31 + div) / p) ** (1 / t) - 1) * 100
def px(v31, x, t=T, div=DIV): return (v31 + div) / (1 + x / 100) ** t
b = pe.run('b', m.pe_corr('base'), beta=1.434, pe_exit=20.35)
f27m = b['path'][2027]['op_margin']
cal = pe.run('c', m.pe_corr('base'), beta=1.434, pe_exit=PE_T, margin_floor=f27m)
e = lambda run, y: run['path'][y]['eps']
years = sorted(b['path'].keys())
TH = [12.0, 9.14, 6.0, 3.0]
lines = {}
v_fun = 866.0
v_cal = PE_T * e(cal, 2032) - CL31                       # finding 1: multiple frozen at today's 18.68x
v_dw = PE_T * e(b, 2033) / (1 + KE) - CL31               # finding 3b: rule's evidenced-cost line (P/E lane, exit normalised)
v_dw_raw = PE_T * e(b, 2032) - CL31                      # finding 3a (peak-D&A exit year, shown only)
for k, v in dict(fundamental=v_fun, calibrated_frozen=v_cal, depwave_evidenced=v_dw, depwave_peak_year=v_dw_raw).items():
    lines[k] = {'v31': round(v, 1), 'ret_today': round(r(v, P), 2), **{str(x): round(px(v, x)) for x in TH}}
def both_below(a, c, x): return max(lines[a][str(x)], lines[c][str(x)])
def both_add(a, c): return min(lines[a]['12.0'], lines[c]['12.0'])
rule = dict(add=both_add('fundamental', 'calibrated_frozen'), parity=both_below('fundamental', 'calibrated_frozen', 9.14),
            trim=both_below('fundamental', 'calibrated_frozen', 6.0), sell=both_below('fundamental', 'calibrated_frozen', 3.0))
alt = dict(add=both_add('fundamental', 'depwave_evidenced'), parity=both_below('fundamental', 'depwave_evidenced', 9.14),
           trim=both_below('fundamental', 'depwave_evidenced', 6.0), sell=both_below('fundamental', 'depwave_evidenced', 3.0))
# finding 2: dated lines at end-2027 (horizon end-2032, 5.0 y)
dps = [b['path'][y]['dps'] for y in range(2028, 2033)]; d5 = sum(dps)
v32c = PE_T * e(cal, 2033) - CL31 * (1 + KE)
v32f = v_fun * v_fun / 783.0
dated = dict(calibrated_sell=round(px(v32c, 3, 5.0, d5)), calibrated_trim=round(px(v32c, 6, 5.0, d5)),
             fundamental_sell_approx=round(px(v32f, 3, 5.0, d5)), fundamental_trim_approx=round(px(v32f, 6, 5.0, d5)),
             fundamental_add_approx=round(px(v32f, 12, 5.0, d5)))
if 2034 in b['path']:
    v32d = PE_T * e(b, 2034) / (1 + KE) - CL31 * (1 + KE)
    dated.update(depwave_sell=round(px(v32d, 3, 5.0, d5)), depwave_trim=round(px(v32d, 6, 5.0, d5)))
else:
    g = e(b, 2033) / e(b, 2032) - 1
    dated['depwave_sell_scaled'] = round(lines['depwave_evidenced']['3.0'] * dated['calibrated_sell'] / lines['calibrated_frozen']['3.0'])
    dated['depwave_trim_scaled'] = round(lines['depwave_evidenced']['6.0'] * dated['calibrated_trim'] / lines['calibrated_frozen']['6.0'])
upward = dict(cal_at_20_35x_sell=988, cal_consensus_scaled_sell=984, capex_fade_rule_depwave_sell=972)  # red-team V2/V3/V6
out = dict(price=P, pe_held=round(PE_T, 2), eps32_cal=round(e(cal, 2032), 2), eps32_pe_lane=round(e(b, 2032), 2),
           eps33_pe_lane=round(e(b, 2033), 2), path_years=[years[0], years[-1]], lines=lines, rule_both=rule,
           claude_alt_both=alt, dated_end2027=dated, upward_alternatives=upward,
           post_split=dict(rule={k: v / 2 for k, v in rule.items()}, alt={k: v / 2 for k, v in alt.items()},
                           dated_end2027={k: v / 2 for k, v in dated.items()}))
print(json.dumps(out, indent=1))
json.dump(out, open(os.path.join(m.OUT_DIR, 'finish_result.json'), 'w'), indent=1)
