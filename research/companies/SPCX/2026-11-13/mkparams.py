"""Writes params.json for model.py (SPCX, 13.11.2026). Every input has its source or 'judgement' in notes."""
import json, sys
sys.path.insert(0, '.')
from model import vint_dep

hist = {
 'conn': {2021: 1.5, 2022: 2.0, 2023: 2.455, 2024: 3.498, 2025: 4.178, 2026: 5.79},
 'space': {2019: 1.0, 2020: 1.0, 2021: 1.1, 2022: 1.2, 2023: 1.497, 2024: 2.032, 2025: 3.832, 2026: 5.43},
 'si': {2021: 0.2, 2022: 0.3, 2023: 0.463, 2024: 5.633, 2025: 12.727, 2026: 57.0},
}
targets = {'conn': 3.85, 'space': 0.70, 'si': 7.9}
lives = {'conn': 4, 'space': 12}
scale = {}
for k in ('conn', 'space'):
    scale[k] = round(targets[k] / vint_dep(hist[k], lives[k], 2026), 3)
srv = {v: c * 0.75 for v, c in hist['si'].items()}
bld = {v: c * 0.25 for v, c in hist['si'].items()}
scale['si'] = round(targets['si'] / (vint_dep(srv, 5, 2026) + vint_dep(bld, 20, 2026)), 3)

p = {
 'ticker': 'SPCX', 'as_of': '2026-11-13', 'price': 140.66,
 'rf': 0.0494, 'erp': 0.042, 'beta': 1.9, 'beta_mature': 1.2,
 'shares_m': 13191.0, 'net_cash_end2026': 39.5, 'investment_assets': 2.2,
 'net_ppe_end2026': 106.6, 'net_ppe_split': {'conn': 22.0, 'space': 12.6, 'si': 72.0},
 'cash_yield': 0.038, 'debt_rate': 0.06, 'tax': [0.08, 0.12, 0.16, 0.21, 0.21, 0.21],
 'roic_floor': 0.04, 'roic_cap': 0.25, 'days_to_base_year_end': 48, 'years_to_horizon': 5.13,
 'server_life': 5, 'building_life': 20, 'conn_life': 4, 'space_life': 12, 'si_server_share': 0.75,
 'seg_beta': {'conn': 1.1, 'space': 1.0, 'si': 2.2},
 'hist_capex': hist, 'legacy_da_scale': scale, 'x_ads_g': 0.03,
 'base_2026': {'subs_end_m': 15.2, 'ent_gov_rev': 7.35, 'space_rev': 4.75, 'cursor_rev': 1.9, 'x_ads_rev': 1.85,
               'fy2026_rev_est': 44.6},
 'one_off_cash': {'2027': 8.5},
 'probabilities': {'bear': 0.30, 'base': 0.50, 'bull': 0.20,
                   'label': 'subjective (Claude judgement, 13.11.2026), not engine odds'},
 'calibrated': {'pe': 35, 'why': "today's forward P/E on our own 2027 operating EPS is >100x and not credible to hold; 35 = the market's multiple on the FY2027 consensus EPS ($4.04, AV n=34, P/E 34.8) and inside the IPO-to-date FY27 P/E range 26.8-49.9x (median 36.2x). Margin floored at the 2027 group EBIT margin."},
 'scenarios': {
  'base': {
   'conn': {'subs_end_m': [21.0, 26.5, 31.5, 36.0, 40.0, 43.5], 'arpu': [67, 65, 63, 62, 61, 60],
            'hw_factor': [1.15, 1.14, 1.13, 1.12, 1.11, 1.10], 'ent_gov_g': [0.40, 0.30, 0.22, 0.18, 0.15, 0.12],
            'ebitda_m': [0.62] * 6, 'capex_pct': [0.27, 0.25, 0.23, 0.22, 0.21, 0.20]},
   'space': {'g': [0.12, 0.12, 0.12, 0.12, 0.10, 0.10], 'core_m': [0.72] * 6, 'rd': [4.6, 4.4, 4.0, 3.5, 3.0, 2.5],
             'capex': [6.0, 6.0, 5.5, 5.0, 5.0, 5.0]},
   'si': {'capex': [85, 75, 65, 65, 70, 75], 'yield': [0.60, 0.55, 0.52, 0.50, 0.48, 0.48], 'cursor_rev_2027': 7.0,
          'cursor_g': [0, 0.35, 0.25, 0.20, 0.15, 0.12], 'ebitda_m': [0.52] * 6},
   'pe': 28, 'g_2033': 0.08},
  'bear': {
   'conn': {'subs_end_m': [19.5, 23.0, 26.0, 28.5, 30.5, 32.0], 'arpu': [62, 56, 52, 50, 48, 47],
            'hw_factor': [1.15, 1.14, 1.13, 1.12, 1.11, 1.10], 'ent_gov_g': [0.25, 0.15, 0.10, 0.08, 0.06, 0.05],
            'ebitda_m': [0.58, 0.55, 0.52, 0.50, 0.50, 0.50], 'capex_pct': [0.30, 0.29, 0.28, 0.27, 0.26, 0.25]},
   'space': {'g': [0.0, 0.03, 0.03, 0.03, 0.03, 0.03], 'core_m': [0.65] * 6, 'rd': [5.2, 5.2, 4.0, 3.0, 2.5, 2.0],
             'capex': [6.5, 6.5, 6.5, 6.0, 6.0, 6.0]},
   'si': {'capex': [75, 12, 10, 10, 12, 15], 'yield': [0.50, 0.40, 0.36, 0.34, 0.33, 0.33], 'cursor_rev_2027': 5.5,
          'cursor_g': [0, 0.15, 0.10, 0.08, 0.06, 0.05], 'ebitda_m': [0.48, 0.42, 0.42, 0.44, 0.45, 0.45]},
   'pe': 18, 'g_2033': 0.03},
  'bull': {
   'conn': {'subs_end_m': [22.5, 29.5, 36.5, 43.0, 49.0, 54.0], 'arpu': [69, 68, 67, 66, 66, 66],
            'hw_factor': [1.15, 1.14, 1.13, 1.12, 1.11, 1.10], 'ent_gov_g': [0.50, 0.38, 0.30, 0.25, 0.20, 0.15],
            'ebitda_m': [0.65] * 6, 'capex_pct': [0.24, 0.21, 0.19, 0.18, 0.17, 0.16]},
   'space': {'g': [0.20, 0.20, 0.18, 0.15, 0.12, 0.10], 'core_m': [0.75] * 6, 'rd': [4.4, 3.8, 3.0, 2.4, 2.0, 1.8],
             'capex': [6.0, 6.0, 5.5, 5.0, 5.0, 5.0]},
   'si': {'capex': [95, 90, 85, 85, 90, 95], 'yield': [0.68, 0.64, 0.60, 0.58, 0.56, 0.55], 'cursor_rev_2027': 8.5,
          'cursor_g': [0, 0.45, 0.35, 0.25, 0.20, 0.15], 'ebitda_m': [0.56] * 6},
   'pe': 35, 'g_2033': 0.10},
 },
}
json.dump(p, open('params.json', 'w'), indent=1)
print(scale)
