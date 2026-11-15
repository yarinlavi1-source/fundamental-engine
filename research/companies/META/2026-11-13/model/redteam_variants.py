"""Red-team variants on the 13.11 META model (imports model13 lanes unchanged; pure arithmetic, ESTIMATE)."""
import sys, io, contextlib, json, copy, os
# research copy: the path lines replace the run folder's absolute paths; the result goes to model13.OUT_DIR
M = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, M)
with contextlib.redirect_stdout(io.StringIO()):
    import model13 as m
import pe
T, DIV, CL31, KE = 5.13, 12.3, 4.4, 0.1096
P_EPS27 = 33.97; PE_T = 634.68 / P_EPS27
def r(v31, p, t=T, div=DIV): return (((v31 + div) / p) ** (1 / t) - 1) * 100
def px(v31, x, t=T, div=DIV): return (v31 + div) / (1 + x / 100) ** t
out = {}
b = pe.run('b', m.pe_corr('base'), beta=1.434, pe_exit=20.35)
f27m = b['path'][2027]['op_margin']
cal = pe.run('c', m.pe_corr('base'), beta=1.434, pe_exit=PE_T, margin_floor=f27m)
e = lambda run, y: run['path'][y]['eps']
# V1 ladder check at two prices: fixed-multiple calibrated vs literal "today's P/E held"
v_cal = PE_T * e(cal, 2032) - CL31; v_fun = 866.0
for p in (700.0, 907.0):
    lit_pe = p / P_EPS27
    out[f'V1_at_{int(p)}'] = dict(fundamental=round(r(v_fun, p), 2), calibrated_fixed_18_68=round(r(v_cal, p), 2),
                                 calibrated_literal_pe=round(lit_pe, 2),
                                 calibrated_literal_ret=round(r(lit_pe * e(cal, 2032) - CL31, p), 2))
# EBITDAR-like margin implied by the floor in 2032 (op margin + D&A + lease increment)
p32 = b['path'][2032]; c32 = cal['path'][2032]; p27 = b['path'][2027]
out['V0_implied_pre_DA_lease_margin'] = dict(y2027=round(p27['op_margin'] + p27['da_pct'] + p27['lease_incr'] / p27['revenue'], 3),
    cal_2032=round(c32['op_margin'] + p32['da_pct'] + p32['lease_incr'] / p32['revenue'], 3),
    fund_2032=round(p32['op_margin'] + p32['da_pct'] + p32['lease_incr'] / p32['revenue'], 3))
# V2 calibrated at the locked 10y median 20.35x (at sell-zone prices the held multiple would exceed 18.68x)
v = 20.35 * e(cal, 2032) - CL31; out['V2_cal_at_20.35x'] = dict(v31=round(v, 1), sell3=round(px(v, 3)), trim6=round(px(v, 6)))
# V3 calibrated with the EPS path scaled to consensus 2027 (multiple is on consensus EPS; path EPS27 = 31.31)
v = PE_T * e(cal, 2032) * P_EPS27 / e(cal, 2027) - CL31; out['V3_cal_consensus_scaled'] = dict(v31=round(v, 1), sell3=round(px(v, 3)), trim6=round(px(v, 6)))
# V4 dep-wave line on the P/E lane itself (the lane whose margin path is D&A+lease only) instead of the 3-lane weighted EPS
v = PE_T * e(b, 2032) - CL31; out['V4_depwave_pe_lane'] = dict(eps32=round(e(b, 2032), 2), v31=round(v, 1), sell3=round(px(v, 3)), trim6=round(px(v, 6)))
# V5 same, exit-year normalised: 2032 is the D&A peak (24.8% of revenue) and EPS then grows +22%/yr; value end-2031 on 2033 EPS discounted 1y
v = PE_T * e(b, 2033) / (1 + KE) - CL31; out['V5_depwave_pe_lane_exit_normalised'] = dict(eps33=round(e(b, 2033), 2), v31=round(v, 1), sell3=round(px(v, 3)), trim6=round(px(v, 6)))
# V6 locked capex rule: commitments in 2027, then fade to own pre-surge share (~24% of revenue 2018-24 median ~22%) by 2030
s6 = m.pe_corr('base'); revs = s6['rev'] + [None]*3
cx = [215, 185, 140]; rv = {2030: 455.2, 2031: 501.9}; rr = 501.9
for i, g in enumerate([0, 0] + s6['g_after']):
    pass
rev_all = [309.8, 359.8, 408.5, 455.2, 501.9]; rr = 501.9
for g in s6['g_after']: rr *= 1 + g; rev_all.append(rr)
s6['capex'] = cx + [round(0.24 * x, 1) for x in rev_all[3:]]
f6 = pe.run('f6', s6, beta=1.434, pe_exit=20.35); c6 = pe.run('c6', s6, beta=1.434, pe_exit=PE_T)
out['V6_capex_fade_rule'] = dict(capex=s6['capex'], eps32=round(e(f6, 2032), 2), da32=round(f6['path'][2032]['da_pct'], 3),
    pe_lane_v_today=round(f6['v_today'], 1), pe_lane_v_today_base=round(b['v_today'], 1),
    depwave_line_v31=round(PE_T * e(c6, 2032) - CL31, 1), depwave_sell3=round(px(PE_T * e(c6, 2032) - CL31, 3)))
# V7 lease term 12y instead of 17y on the dep-wave P/E-lane line
b12 = pe.run('b12', m.pe_corr('base'), beta=1.434, pe_exit=PE_T, lease_term=12.0)
v = PE_T * e(b12, 2032) - CL31; out['V7_depwave_lease12y'] = dict(eps32=round(e(b12, 2032), 2), sell3=round(px(v, 3)))
# V8 drift: the sell line as of end-2027 (horizon end-2032, 5.0 y), calibrated and P/E-lane fundamental
dps = [b['path'][y]['dps'] for y in range(2028, 2033)]; d5 = sum(dps)
v32c = PE_T * e(cal, 2033) - CL31 * (1 + KE); v32f = 866.0 * 866.0 / 783.0
out['V8_sell_line_end2027'] = dict(calibrated=round(px(v32c, 3, 5.0, d5)), fundamental_approx=round(px(v32f, 3, 5.0, d5)),
                                   post_split_calibrated=round(px(v32c, 3, 5.0, d5) / 2, 1), div_5y=round(d5, 1))
print(json.dumps(out, indent=1))
json.dump(out, open(os.path.join(m.OUT_DIR, 'variants_result.json'), 'w'), indent=1)
