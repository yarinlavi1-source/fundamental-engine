"""Side calculations for SPCX (13.11.2026): peer-multiple value, own IPO-range multiple on our revenue, separate bets,
Tesla merger arithmetic. Inputs from params.json/results.json and the market lane (market.md). $bn, shares in millions."""
import json
p = json.load(open('params.json')); r = json.load(open('results.json'))
sh = p['shares_m']; price = p['price']
b = r['scenarios']['base']['path']
y27 = b['2027']
base = p['scenarios']['base']
conn_ebitda27 = y27['conn_rev'] * base['conn']['ebitda_m'][0]
space_core27 = y27['space_rev'] * base['space']['core_m'][0]
si_rev27 = y27['si_rev']
cash = p['net_cash_end2026'] + p['investment_assets'] - p['one_off_cash']['2027']   # EchoStar cash paid
out = {}
# Report 2: historical-multiple report. No own history (listed 12.06.2026) -> direct peers at today's growth.
#   Connectivity: EV/EBITDA, IRDM 16.6x (GAAP OI+D&A; ~13-14x on company OEBITDA), TMUS 7.8x -> 12/16/20x (growth premium)
#   Space: core EBITDA before Starship R&D x LMT 14.5x (12/14.5/17), Starship R&D as a cost: -3 years of 2027 R&D
#   SI: EV/NTM sales of the closest listed neocloud, NBIS ~4.5x (saved run 12.11: EV ~54bn / 2027 revenue ~12bn); 3/4.5/6x
peer = {}
for lab, (mc, ms, mi) in {'low': (12, 12, 3.0), 'mid': (16, 14.5, 4.5), 'high': (20, 17, 6.0)}.items():
    ev = conn_ebitda27 * mc + space_core27 * ms - 3 * base['space']['rd'][0] + si_rev27 * mi
    peer[lab] = round((ev + cash) * 1000 / sh, 2)
out['peer_multiple_value_per_share'] = peer
out['peer_inputs_2027'] = dict(conn_ebitda=round(conn_ebitda27, 1), space_core_ebitda=round(space_core27, 1),
                               starship_rd=base['space']['rd'][0], si_rev=round(si_rev27, 1), cash_after_echostar=round(cash, 1))
# Own (IPO-to-date) EV/NTM sales range 9.0-17.0x (median 12.2x, market lane) applied to OUR NTM revenue
ntm = 22.0 + 0.75 * y27['rev']           # repair: Q4'26 ~22 (ARR $100bn exit-2026 guide) + 3/4 of base 2027
nc = 51.3 + 2.2                            # net cash at 30.09 as the market lane computed EV (+ BTC/equity stakes)
out['own_range_on_our_ntm'] = {k: round((ntm * m + nc) * 1000 / sh, 2) for k, m in {'p_min_9.0x': 9.0, 'median_12.2x': 12.2, 'max_17.0x': 17.0}.items()}
out['our_ntm_revenue_bn'] = round(ntm, 1)
out['consensus_ntm_revenue_bn'] = 158.2

# Separate bets (NOT in the base). Value if it happens = extra after-tax earnings in 2031 x 20 (capitalised), discounted
# 5.13 years at 12%; probability = Claude's subjective judgement.
def bet(extra_nopat_2031, mult=20, years=5.13, k=0.12):
    return round(extra_nopat_2031 * mult / (1 + k) ** years * 1000 / sh, 2)
c31 = b['2031']['conn_rev']
bets = {
 'starship_full_reuse': dict(extra_nopat_2031=round((c31 * (0.20 - 0.12) + 10 * 0.40) * 0.79, 1), prob=0.40,
     what='Connectivity capex falls to ~12% of revenue (base 20%) and ~$10bn of new heavy-lift/HLS/defence revenue at 40% EBIT by 2031'),
 'direct_to_cell_at_scale': dict(extra_nopat_2031=round(150e6 * 8 * 12 / 1e9 * 0.50 * 0.79, 1), prob=0.30,
     what='150m paying D2C users at ~$8/month, 50% EBIT after D2C satellite capex (spectrum cost already in the base)'),
 'xai_frontier_lab_win': dict(extra_nopat_2031=0.0, prob=0.0,
     what='repair 13.11: folded into the bull (SI pricing holds near today at guided capacity: SI ~$590bn revenue at ~35% EBIT in 2031); not added on top'),
 'mars': dict(extra_nopat_2031=0.0, prob=0.0, what='no cash value inside the horizon; a 1m-person colony is the vesting condition of 1,000m Musk restricted shares'),
 'ai_in_space': dict(extra_nopat_2031=0.0, prob=0.03, what='orbital data centres: no filed milestone; 100 TW in space vests 302m Musk shares; not sizeable'),
}
for k, v in bets.items():
    v['value_per_share_if_it_happens'] = bet(v['extra_nopat_2031'])
    v['expected_per_share'] = round(v['value_per_share_if_it_happens'] * v['prob'], 2)
out['separate_bets'] = bets
out['bets_expected_sum'] = round(sum(v['expected_per_share'] for v in bets.values()), 2)
out['bets_all_succeed_sum'] = round(sum(v['value_per_share_if_it_happens'] for v in bets.values()), 2)

# Tesla merger arithmetic (no terms reported). Market ratio 12.11 close: 397.45 / 139.94 = 2.84 SPCX per TSLA.
tsla_px, spcx_px = 397.45, 139.94
tsla_sh = 1550.0 / tsla_px * 1000          # ~3.9bn TSLA shares (market cap ~$1.55tn, Reuters/Bloomberg)
V_S = r['scenarios']['base']['value_today']
V_T = 34.4                                  # saved TSLA engine base 27.10.2026 (DRAFT, unverified): research/companies/TSLA/2026-10-27
m = {}
for lab, prem in {'at_market_ratio': 0.0, 'spcx_holders_get_15pct_more': -0.15, 'tsla_holders_get_15pct_premium': 0.15}.items():
    ratio = tsla_px / spcx_px * (1 + prem)
    new = tsla_sh * ratio
    per = (sh * V_S + tsla_sh * V_T) / (sh + new)
    m[lab] = dict(ratio_spcx_per_tsla=round(ratio, 3), spcx_holders_share_of_combined=round(sh / (sh + new), 3),
                  intrinsic_per_spcx_share=round(per, 2), vs_standalone=round(per - V_S, 2),
                  market_value_per_spcx_share=round((sh * price + tsla_sh * tsla_px) / (sh + new), 2))
out['tesla_merger'] = m
out['tesla_merger_note'] = ('At the market ratio the deal moves value between holders only as far as the two prices are wrong relative '
                            'to value. On our numbers both trade far above value (SPCX ~5x, TSLA ~12x on a draft base), so an SPCX '
                            'holder swapping into a combined company at the market ratio gives up intrinsic value; a premium for '
                            'SPCX holders reduces that. Musk restricted shares (1,302m) and Tesla CEO awards would need terms.')
json.dump(out, open('side.json', 'w'), indent=1)
print(json.dumps(out, indent=1))
