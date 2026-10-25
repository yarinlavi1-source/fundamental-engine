"""Bear / base / bull business forecast, emitted as a driver spec (driver_version 1).

Why: a valuation is only as good as the business forecast behind it. This module turns the FactBook,
the financial analysis (analysis.py), the business model card (model_card.py) and the validated
opening balance (dataqa.py) into explicit paths for growth, GAAP operating margin (after SBC),
reinvestment, working capital, SBC, tax, funding and the discount rate. Every number becomes an
assumption row with a kind, a rationale, a falsifier and sources, and the paths are written as a
drivers.build_case spec that valuation.value_company executes and valuation_audit reviews.

Growth costs capital (growth capex from incremental sales-to-capital, converging to the terminal g / ROIC rule; a shrinking
business releases capital in the explicit years too), funding is explicit (investments first, SBC settled in shares, assumed
debt within capacity, then assumed equity; new shares are issued at min(quote, the scenario's own value), labelled and
flagged, never silent), SBC is a cash-equivalent cost with point-in-time shares (no double counting with dilution), the
terminal margin equals the last explicit year and depreciation converges to maintenance capex with the EBITDA margin held
(non-cash amortization rolls off unless recurring acquisitions replace it). The cost of equity never depends on the quote:
the table rate is read at market-average leverage, unlevered with the expected return on debt and relevered against the
model's own equity value. Cyclicals start from the cycle (mid-cycle trend revenue, through-the-cycle margin = sum of EBIT /
sum of revenue), an unobserved cyclical peak is never extrapolated silently, declining businesses may keep declining, an
unassessed moat gets at most a labelled, continuous proxy for base and bull (never the bear) with its value effect shown,
and the fifth anniversary of as_of is always an explicit valuation date.

The constants below are MODEL ASSUMPTIONS: chosen once from base rates and documented here, cited in
the ledger through the 'engine:forecast_rules' source, and never tuned toward a return target or
the owner's 14% aspiration. Generated assumptions stay 'unreviewed' until an independent reviewer
marks them reviewed (inputs['reviews']); only then can the valuation become eligible for synthesis.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import math
import re
from . import base_rates, facts
from .drivers import build_case, RATIO_KEYS, ABSOLUTE_KEYS, reinvest as _reinvest, capex_for_path as _capex_for_path, value_paused as _value_paused, RELEASE_FLOOR
from .finance import number
from .history import history_shape
from .model_card import cyclical_evidence
from .valuation import value_company, years, ROUTES, TERMINAL_GROWTH_MAX
from .valuation_tools import implied_cost_of_equity

FORECAST_VERSION = 1
SCEN = ('bear', 'base', 'bull')
BUILT = ('fcff_annual_path', 'normalized_cycle_fcff', 'scenario_survival_fcff')
NOT_BUILT = ('residual_income', 'nav', 'rnpv', 'sotp', 'not_reliably_valuable')
RF_DEFAULT = .0425       # US 10-year Treasury approximation; replace with a dated quote via inputs['risk_free']
ERP_DEFAULT = .045       # mature-market equity risk premium (implied-ERP studies centre around 4-5%)
# Terminal (perpetual) growth = the risk-free rate: the convention of the ERP it is paired with. The implied ERP the engine is
# fed (Damodaran's: ERP_DEFAULT's order of magnitude, the dated runs/macro input, engine_backtest.IMPLIED_ERP) is solved from
# the index level with cash flows growing at the Treasury yield forever after year five (his ERPOct26.xlsx: terminal earnings
# = year-5 earnings x 1.0529 at a 5.29% T-bond), and he sets stable growth at (never above) the risk-free rate. The former
# min(risk-free, 3%) paired that premium with a lower perpetual growth: re-solving his own 1 Oct 2026 index model at g = 3%
# gives an ERP of 2.82%, not 4.20%, so whenever the risk-free rate rose above 3% (2023-2026) every company was discounted at
# a premium solved for one growth and grown at another, and the walk-forward's median value/price drifted with the rate
# (approximately 0.71 - 17 x max(0, rf - 3%): a ten-point fit of yearly medians against the June 10-year yield, correlation
# about -0.8; approximate, not a calibrated relation). Growth is not free: the terminal reinvestment is g / ROIC with ROIC
# above g, and the terminal WACC stays at least 3 points above g. Bull = base (perpetual outgrowth of the economy is not a
# bull case); the bear is TERMINAL_BEAR_GAP lower; a declining business keeps its own rule (DECLINE_FLOOR); an analyst
# override still wins. The convention stops at the valuation contract's bound valuation.TERMINAL_GROWTH_MAX (8%): a rate above
# it (a high-rate reporting currency) keeps growth at 8%, below the rate, and is flagged 'terminal_growth_capped', never a
# failed valuation.
TERMINAL_BEAR_GAP = .005   # bear perpetual growth half a point below base (never below zero unless the risk-free rate is)
TERMINAL_HIGH_RF = .06     # terminal growth used above this (the risk-free rate, or an override) is flagged (inflation or a rate spike)
MARGIN_FLOOR = .02       # terminal margin must be positive; a binding floor is flagged, not hidden
HORIZON = ((.25, 15), (.15, 12), (-9, 10))   # growth anchor above x -> explicit fiscal years (extended to 20 if still far from terminal)

# Stage parameters (model assumptions, not estimates of a specific company):
# half_life  years for EXCESS growth (growth - terminal growth) to halve. Koller et al. (McKinsey) report
#            >20% growers slowing to ~8% within five years and ~5% within ten; that path implies a half-life
#            of ~2.4 years for excess growth. Slower decay is allowed for mature, small excess growth.
# g_spread   minimum first-year growth gap between base and bull/bear (stage uncertainty floor).
# m_spread   GAAP operating-margin gap between scenarios at maturity.
# conv       years over which the margin moves from today's level to its mature target.
# months     minimum operating cash in months of cash costs (capped at today's cash: the company already
#            operates with its balance).  sbc_fade  share of today's SBC/revenue that falls away at scale.
# catch_up   share of the gap to the type-typical mature margin a below-typical company closes in base.
# ke         business-risk add-on to the cost of equity (NOT price beta) for the explicit years; the terminal rate drops it
#            (TERMINAL_KE_DROPPED: the forecast is mature by its terminal date).
# debt_spread spread over risk-free for ASSUMED new financing.
STAGE = {
    'early':      dict(half_life=2.0, g_spread=.10, m_spread=.10, conv=7, months=6, sbc_fade=.4, catch_up=.6, ke=.03, debt_spread=.07),
    'growth':     dict(half_life=2.5, g_spread=.06, m_spread=.07, conv=6, months=4, sbc_fade=.3, catch_up=.7, ke=.015, debt_spread=.05),
    'maturing':   dict(half_life=3.0, g_spread=.04, m_spread=.05, conv=5, months=3, sbc_fade=.2, catch_up=.6, ke=.005, debt_spread=.03),
    'mature':     dict(half_life=3.0, g_spread=.03, m_spread=.04, conv=4, months=2, sbc_fade=.1, catch_up=.4, ke=0., debt_spread=.02),
    'decline':    dict(half_life=2.0, g_spread=.04, m_spread=.05, conv=4, months=3, sbc_fade=0., catch_up=.2, ke=.01, debt_spread=.04),
    'turnaround': dict(half_life=2.0, g_spread=.06, m_spread=.08, conv=5, months=6, sbc_fade=.1, catch_up=.5, ke=.025, debt_spread=.05),
}
# Business-model parameters: incremental sales-to-capital range (revenue added per unit of net capital),
# type-typical mature GAAP operating margin after SBC, a ceiling few companies of the type sustain, and the
# valuation.ROUTES archetype. Rough engine ranges for orders of magnitude, NOT a peer study: replace with
# a peer set through inputs['overrides'] when one is evidenced.
TYPE = {
    'subscription_software':            ((1.5, 5.0), .25, .40, 'software'),
    'platform_marketplace':             ((1.5, 5.0), .25, .40, 'platform'),
    'semiconductor':                    ((0.8, 2.0), .25, .40, 'nonfinancial'),
    'hardware_manufacturing':           ((1.2, 3.0), .10, .20, 'industrial'),
    'capital_intensive_infrastructure': ((0.3, 1.0), .30, .45, 'infrastructure'),
    'consumer_brand':                   ((1.5, 3.5), .15, .25, 'consumer'),
    'industrial':                       ((1.0, 2.5), .12, .20, 'industrial'),
    'commodity':                        ((0.4, 1.2), .15, .30, 'commodity'),
    # order of magnitude from US industry operating-margin tables (drugs and devices about 20-25%, hospitals and labs about
    # 10-12%, IT services about 10-12%, wholesale distribution about 3-6%): documented assumptions, not peer studies
    'healthcare_products':              ((0.8, 2.5), .22, .40, 'nonfinancial'),
    'healthcare_services':              ((1.0, 3.0), .11, .18, 'nonfinancial'),
    'it_services':                      ((2.0, 5.0), .12, .20, 'nonfinancial'),
    'distribution':                     ((3.0, 8.0), .05, .10, 'industrial'),
}
DEFAULT_TYPE = ((1.0, 3.0), .15, .25, 'nonfinancial')
NON_OPERATING_TYPES = {'financials': 'residual_income', 'reit': 'nav', 'pre_revenue_biotech': 'rnpv', 'conglomerate': 'sotp'}
# 'cyclical' is charged only when cyclicality is EVIDENCED (model_card.cyclical_evidence): the reported history shows a cycle,
# an analyst confirmed one (card inputs.cyclical value true, forecast inputs.cyclical treatment 'cyclical', a method override to
# normalized_cycle_fcff) or the type is cyclical by economics (semiconductor, commodity). An industry word alone (travel,
# machinery, construction, ...) had charged it -- MKT ('industrial machinery', no cycle in its history, not normalized) +0.5pp,
# about 9% of value at Ke -- though the word measures nothing about the company's own cash flows; it now stays a visible
# question (route cyclical_evidence, components.cyclical_evidence, warning cyclical_unevidenced) until inputs.cyclical settles
# it. Evidence only gates the charge (a card that does not flag cyclicality is never charged). Not a calibration: the 0.5pp is
# unchanged and no constant was tuned to the walk-forward.
KE_FACTORS = (  # (condition, add-on, reason) evaluated on the card and the executed funding result
    ('cyclical', .005, 'cyclical revenue and margins'),
    ('capital_intensity_high', .005, 'high capital intensity (operating leverage on fixed assets)'),
    ('needs_external_funding', .01, 'base plan depends on external funding (executed funding result)'),   # explicit years only (TERMINAL_KE_DROPPED)
)
# The ERP is the premium of the value-weighted market over the risk-free rate, so the value-weighted average cost of equity of
# the market's own companies must equal rf + ERP. The add-ons above only add (no stage, factor or dimension subtracts): on the
# 481 largest US operating companies valued on 2026-10-20 they averaged 0.93pp with calibration's market values (1.2pp with
# the listing feed's), so every company was discounted about 1pp above the market's own definition. This row removes the
# measured average: an average-risk company is discounted at rf + ERP, one with no add-on below it. calibration.market_implied
# reports the weighted mean of the net adjustment after each sweep (it should stay near zero).
MARKET_NEUTRAL_ADJUSTMENT = -.01
# Terminal cost of equity = the MATURE table. The forecast itself makes the business mature by its terminal date (excess growth
# faded to the terminal rate, the margin at its mature target, reinvestment on the g / ROIC rule out of its own cash flow), yet
# the stage add-on (early +3pp, growth +1.5pp, maturing +0.5pp, turnaround +2.5pp) and the one-off funding add-on were also
# charged on the perpetuity after it: the risk of a phase the model has already ended, discounted forever (on the diagnosis's
# synthetic 31% grower about a third of its value, 21.10.2026). Damodaran: risk should move toward the mature level as growth
# fades -- a stable-growth firm's beta moves toward one (Investment Valuation, 3rd ed., ch. 12, closure in valuation), and his
# young-growth model (fcffsimpleginzu.xlsx) moves the cost of capital in years 6-10 to the after-year-10 rate of a mature firm
# (risk-free + mature ERP by default). So the terminal Ke drops exactly these rows (TERMINAL_KE_DROPPED: the stages whose forecast
# fades to maturity, and the funding need of the explicit plan, met by the terminal date) and keeps every structural one
# (cyclicality, capital intensity, regulated utility, weak card dimensions, market-neutral), clamped like the table and relevered
# at the same D/E (Harris-Pringle); the terminal WACC is built from it and floored at terminal growth + 3 points as before. The
# terminal ROIC rules (Ku + 2pp, the persistence blend, the strong-moat floor Ku + 5pp) and the franchise-durability excess are
# measured against the same mature table: one hurdle for the terminal state. Unchanged: the explicit years and the discount of
# the terminal equity value back to today keep the full table rate (the growth-phase risk is real while it lasts); a declining
# business keeps its add-on (its terminal still shrinks, DECLINE_FLOOR: it is not mature); an analyst cost-of-equity override
# is used for both (its components are not the table's). The same terminal rate in every scenario: the bear is never discounted
# below the mature table (no scenario-specific discount rate); it still differs by outcomes. Not a calibration: no constant was
# tuned to the walk-forward, and the mature stage (ke 0) is unchanged.
TERMINAL_MATURE_STAGES = ('early', 'growth', 'maturing', 'turnaround')
TERMINAL_KE_DROPPED = tuple('stage:' + s for s in TERMINAL_MATURE_STAGES) + ('needs_external_funding',)
# Excess returns on capital fade slowly and only part of the way (Mauboussin, Callahan & Majd, The Base Rate Book, Credit
# Suisse 2016, from HOLT data since 1950): over ten years the CFROI spread between the top and bottom quintiles shrinks from 18
# to 9 points (half of the excess left) and the operating-margin spread from 0.21 to 0.15 (71% left); Damodaran: "excess returns
# seem to last far longer than high growth rates do". Without a moat assessment the terminal ROIC keeps that share of the
# demonstrated excess over Ku (base 50%, bull 71%; never below the former Ku+2pp / Ku+4pp); the bear still tests a moat that
# fails (Ku+1pp) and an analyst 'weak' moat keeps the full fade. Demonstrated = the lower of the latest and the 5-year average
# ROIC, capped at 40% (a single strong year earns no persistence).
# On a scale-up path the 5-year average is taken over the post-breakeven years only (_post_breakeven_roic): when the ROIC
# window's own fiscal years show revenue up every year and operating margin from losses to the window high (history.history_shape
# 'scale_up', the reading the model card already uses so start-up losses are not averaged into margins), the years up to the
# window's last loss year are the ramp, not the business the terminal describes. The diagnosis profile (2026-10-21: ROIC -79%,
# -28%, 18%, 42%, 124%) averaged 15.6% and faded to Ku+2.9pp against 124% latest. At least SCALE_UP_ROIC_MIN_YEARS such years are
# needed (one profitable year is not a record: the 5-year average stays). Not incremental ROIC: on a window that starts in a loss
# year it credits the removal of start-up losses as a return on new capital. Unchanged: the lower of latest and average, the 40%
# cap, the bear (Ku+1pp), the weak-moat fade, the durability lift (it needs PERSISTENCE_MIN_YEARS of stable ROIC: a window with
# a loss year varies by at least 50%, the floor end of the stability ramp) and the moat proxy's whole-window average (a stricter
# test of a moat, not the base-rate share of a demonstrated excess). No constant tuned to the walk-forward.
SCALE_UP_ROIC_MIN_YEARS = 2
# The kept share is not flat across the quintiles: the Base Rate Book's own data and the McKinsey ROIC transition matrices
# (Koller et al.) both show the firms whose realized ROIC is both HIGH and STABLE over a long record stay high far longer than
# the average fader -- a wide-moat compounder keeps much more of its excess than a company with one good year. So the base/bull
# persistence is lifted from the floor toward a ceiling by a continuous, evidence-only durability weight (_franchise_durability):
# how far realized ROIC clears the hurdle, how stable it has been over at least PERSISTENCE_MIN_YEARS, and whether margins are a
# durable franchise's. The lift is withheld from cyclicals (their return is the cycle's, assessed through _cycle_margin, not a
# persistence premium) and from a weak moat; the bear is never lifted; and no analyst input is needed, so an automatic run earns
# it from the realized record alone. A move in one input never flips the value. NOTE (docs/ENGINE_CONSERVATISM_2026-10-21.md): an
# A/B on the 491-company walk-forward measured this lever's own effect at under 1% of value -- it corrects a principled
# inconsistency in the flat 50%, it is NOT the cure for the engine's conservatism on growth names, which is mostly structural
# (discount rate and growth fade) and largely appropriate. That gap is surfaced, not inflated, via decision.price_implied_growth.
EXCESS_RETURN_PERSISTENCE = {'base': .50, 'bull': .71}          # floor: a thin or unstable ROIC record keeps only this
EXCESS_RETURN_PERSISTENCE_CEIL = {'base': .75, 'bull': .85}     # ceiling: a high, stable ROIC over a long record
PERSISTENCE_EXCESS_RAMP = (.05, .20)        # demonstrated ROIC excess over the hurdle: +5pp -> floor, +20pp -> ceiling
PERSISTENCE_STABILITY_RAMP = (.50, .20)     # coefficient of variation of the ROIC history: 50% -> floor, 20% -> ceiling
PERSISTENCE_MIN_YEARS = 5                    # fewer years of ROIC than this earns no lift (a short record is not durability)
# inherently cyclical business models never earn the premium: across a boom window their trailing ROIC looks high and stable
# though it is the cycle's, not a franchise's, and the 491-company walk-forward found their value/price signal flat-to-inverse
# (semiconductors Spearman -0.28). They are valued around the cycle through the mid-cycle margin instead.
CYCLICAL_LIFT_EXCLUDED_TYPES = ('semiconductor', 'commodity')
# Financial leverage is NOT a table add-on. The table rate (risk-free + ERP + business-risk add-ons) is the cost of equity of
# a company of that business risk at MARKET-AVERAGE leverage: the ERP is measured on levered equities, so treating it as an
# unlevered rate and relevering on top double counts leverage. Ku unlevers it at MARKET_DE with the EXPECTED return on debt
# (never the promised coupon: a higher coupon on the same debt is a cash cost, not a lower equity risk), then Harris-Pringle
# relevers at the company's own D/E taken from the model's own base equity value (fixed point, never the quote):
#   Ku = (table + Kd_e x MARKET_DE) / (1 + MARKET_DE);  Ke = max(table, Ku + (Ku - Kd_e) x D/E)
#   WACC = E/V x Ke + D/V x (Kd_e - Kd_promised x tax)  (= Ku - D/V x Kd_promised x tax when Ke is not floored)
# A company with less than market leverage keeps the table rate (conservative: it is not credited with a lower rate).
MARKET_DE = .25           # market-average debt / equity behind the ERP (US non-financials, order of magnitude; a model assumption)
DEBT_EXPECTED_PREMIUM = .01   # expected (not promised) return on corporate debt over risk-free: about a BBB debt beta x ERP
DEBT_RATE_RANGE = (.005, .06)  # interest / debt below 0.5% or above risk-free + 6pp is flagged as a data issue (it sets interest and the
                               # tax shield); a coupon below today's risk-free rate is normal for debt issued when rates were lower
REGULATED_ASSET_RISK = .5     # regulated utilities: asset risk about half the market's (allowed returns, pass-through costs): ERP x 0.5
REGULATED_WORDS = ('utilities', 'utility')
W_MAX = .6                # debt weight cap when the modelled equity is tiny (distress): flagged, not hidden
DEBT_CAPACITY = 3.0       # assumed new borrowing only up to 3x EBITDA before SBC (the usual high-leverage line); beyond -> equity
EQUITY_FEE = .03          # gross spread on an assumed follow-on equity raise
# Revenue-scale growth ceilings for the trailing anchor (revenue below x -> anchor at most y). Orders of magnitude from
# revenue-growth base rates by company size (sustained growth this fast is rare at that scale; cf. Mauboussin & Callahan,
# The Base Rate Book, 2016), NOT a calibrated study. Guidance or consensus replaces the anchor when present; a backlog
# (RPO) of at least one year of revenue lifts the ceiling one band.
SCALE_CAPS = ((1e8, 1.0), (1e9, .6), (1e10, .4), (5e10, .25), (float('inf'), .15))
# bounds on the long-run (trend) growth of a cyclical's revenue: -3% .. +5% nominal (the upper bound was written as the former
# 3% terminal cap + 2 points; kept as its own number so the terminal-growth convention does not move the cycle fit)
CYCLE_TREND = (-.03, .05)
CYCLE_CONV = {'bear': 2, 'base': 3, 'bull': 4}     # years for a cyclical to revert from today's level to its cycle path
CYCLE_FIT_MIN_YEARS = 6   # fewer years than about one full cycle (3-5 years) plus a leg: the fitted slope is one up-leg, not a trend
DECLINE_FLOOR = -.05      # lowest perpetual growth for a structurally declining business
DEBT_INCOMPLETE_RATE = .25     # interest / debt above this (no investment-grade or bank rate) means debt lines are missing
KNOWN_INPUTS = ('overrides', 'reviews', 'risk_free', 'equity_risk_premium', 'horizon_years', 'financing', 'nol', 'cyclical',
                'operating_funding', 'nonrecurring', 'debt_like_claims', 'non_operating_assets', 'capital_events')
# inputs['nonrecurring'] = [{kind, period_end, revenue, operating_income, reason, evidence_ids}]: a one-off inside reported
# figures (the effect it had, removed from the history the paths start from: TTM, fiscal years, year to date). Only kinds
# that do not recur by nature; restructuring, SBC or 'adjusted' costs are not one-offs (never erase recurring costs).
# BioCryst 2026-10-20: a $258m European licence sale in Q4 2025 revenue and a $698m acquired-R&D charge in Q1 2026 had
# started the forecast at -33% margin and a falling revenue base.
# inputs['debt_like_claims'] = [{kind, amount, reason, evidence_ids}]: claims ahead of the shares that the debt lines miss,
# added to opening debt (they carry interest like debt). BioCryst's royalty financing obligation ($427m at 30.6.2026, paid
# from ORLADEYO royalties with its non-cash interest in interest expense) was outside the bridge and made 20% look like the
# rate on its $395m term loan.
# inputs['non_operating_assets'] = [{kind, fair_value, haircut, book_value_in_bridge, reason, evidence_ids}]: a stake the
# balance sheet carries far below its value (equity method or at cost; Microsoft's ~25% of OpenAI at book while OpenAI's
# March 2026 round priced it at $852bn). Added to opening investments at fair_value x (1 - haircut) less what the bridge
# already holds for it (book_value_in_bridge; when the filing does not split it out, the whole line that contains it, which
# understates the addition). The haircut is the analyst's: illiquidity, preference terms, tax on a sale. Investments enter the
# value at face, so the addition moves the value one for one; the reviewer accepts it like a debt reclassification.
NON_OPERATING_KINDS = ('private_stake', 'equity_method_stake')
# inputs['capital_events'] = [{kind, date, cash, shares, reason, evidence_ids}]: a share issue or buyback completed after the
# balance-sheet date and by as_of (SK hynix's July 2026 US listing: 17.79m new shares for W39.9tn, two weeks after its June
# balance sheet). The cash moves the opening cash (in for an issue, out for a buyback); the shares move the opening count only
# when the point-in-time count is dated before the event (a later count already holds them). Listed as bridge events for review.
CAPITAL_EVENT_KINDS = ('share_issue', 'share_buyback')
DEBT_LIKE_KINDS = ('royalty_financing', 'litigation', 'pension_deficit', 'deferred_consideration', 'preferred_or_minority',
                   'finance_leases')     # finance-lease payments are not in operating costs (amortisation and interest)
NONRECURRING_KINDS = ('acquired_iprd', 'impairment', 'gain_or_loss_on_sale', 'litigation_settlement', 'one_time_license_revenue',
                      'divestiture_in_revenue')
# inputs['operating_funding'] = {amount, reason, evidence_ids}: borrowings that fund an operating asset (a lending book) and
# whose interest is already inside operating costs (MercadoLibre books fintech funding costs in cost of revenue). Kept in
# the bridge they would be paid twice, in the margin and as debt; they leave the bridge and reinvestment must be measured
# net of them (sales_to_capital override), which the engine asks for when missing.
CYCLE_TREATMENTS = ('structural', 'cyclical')   # inputs['cyclical']['treatment'] for an industry-cyclical company at a margin peak
# Moat proxy (no analyst moat assessment): a continuous weight on the strong-moat rules for base and bull (never the bear, which
# tests the moat failing), from evidence ramps so a 1pp move in one input never flips the value: multi-year average ROIC
# (15% -> 25%), measurable incremental ROIC (10% -> 20%) and pricing power (analyst strong = 1; computed: gross margin level
# 45% -> 75% and stability, range 8pp -> 3pp). Blocked by any analyst moat row (even 'unknown'), an analyst 'weak' on
# competition / substitutes / technology_ai, or unassessed FactBook evidence on moat, substitutes or technology_ai.
PROXY_RAMPS = {'roic_avg': (.15, .25), 'incremental_roic': (.10, .20), 'gross_margin': (.45, .75), 'gross_margin_range': (.08, .03),
               # fallback when no cost of revenue is reported (networks, many service companies): operating margin level and range
               'operating_margin': (.20, .35), 'operating_margin_range': (.08, .03)}
PROXY_BLOCK_DIMS = ('competition', 'substitutes', 'technology_ai')
PROXY_EVIDENCE_TOPICS = ('moat', 'substitutes', 'technology_ai')
FINANCING_MODES = ('assumed', 'assumed_debt', 'none')   # 'assumed_debt' kept as the legacy name of the default
WEAK_DIMENSIONS = ('customer_concentration', 'technology_ai', 'governance', 'competition')
ECON_LEVERS = ('growth_anchor', 'growth_half_life', 'terminal_growth', 'mature_margin', 'sales_to_capital',
               'terminal_roic', 'cost_of_equity', 'tax_rate', 'payout_fraction', 'minimum_cash_months')
TERMINAL_KEYS = ('growth', 'wacc', 'roic', 'operating_margin', 'tax_rate', 'other_claims', 'net_runoff_obligation')
KIND_MAP = {'filing_derived': ('filing', 'provider_normalized'), 'vendor_normalized': ('provider_data', 'provider_normalized'),
            'market_feed': ('market_data', 'market_feed'), 'consensus': ('analyst_estimates', 'provider_normalized'),
            'document': ('document', None), 'synthetic': ('synthetic', None), 'secondary': ('secondary_source', 'search_excerpt')}
# issuer documents (and legacy 'document' sources whose dataset is a filing or release) keep their primary kind; how they
# were read is carried in retrieval, so a 10-Q seen through search excerpts counts as primary but not read directly
ISSUER_DATASETS = {'filing': 'filing', 'issuer_release': 'issuer_release', 'earnings_release': 'earnings_release'}
RETRIEVALS = ('primary_document', 'provider_normalized', 'search_excerpt', 'market_feed')
# kinds graded as a primary document somewhere (valuation, supervisor, facts): a rate typed into the inputs never claims one
PRIMARY_CLAIMS = frozenset({'filing', 'earnings_release', 'issuer_release', 'regulator', 'official_statistics'} | facts.REPORTED_KINDS)
RULES = 'engine:forecast_rules'


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _r(x, k=6):
    return round(float(x), k)


def _sha(obj):
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _fy(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _metric(an, name):
    m = (an.get('metrics') or {}).get(name)
    return _num(m.get('value') if isinstance(m, dict) else m)


def _pct(x):
    return 'n/a' if x is None else f'{x:.1%}'


# ---------------------------------------------------------------- inputs

def _check_overrides(fb, overrides):
    known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
    out = []
    for i, o in enumerate(overrides):
        if not isinstance(o, dict):
            raise ValueError(f'override {i}: must be an object')
        extra = sorted(set(o) - {'path', 'scenario', 'value', 'reason', 'evidence_ids'})
        if extra:
            raise ValueError(f'override {i}: unknown keys {extra} (path, scenario, value, reason, evidence_ids)')
        path = o.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError(f'override {i}: path required')
        if not isinstance(o.get('reason'), str) or not o['reason'].strip():
            raise ValueError(f'override {path}: a written reason is required')
        ev = o.get('evidence_ids')
        if not isinstance(ev, list) or not ev:
            raise ValueError(f'override {path}: evidence_ids required (FactBook source or qualitative ids)')
        bad = [e for e in ev if e not in known]
        if bad:
            raise ValueError(f'override {path}: unknown evidence ids {bad}')
        if o.get('scenario', 'all') not in SCEN + ('all',):
            raise ValueError(f'override {path}: scenario must be bear, base, bull or all')
        raw = path == 'growth' or path in RATIO_KEYS or path in ABSOLUTE_KEYS or (
            path.startswith('terminal.') and path.split('.', 1)[1] in TERMINAL_KEYS)
        if path not in ECON_LEVERS and not raw:
            raise ValueError(f'override {path}: unknown path; use {", ".join(ECON_LEVERS)}, a driver key or terminal.<key>')
        v = o.get('value')
        vals = v if isinstance(v, list) else [v]
        if not vals or any(_num(x) is None for x in vals):
            raise ValueError(f'override {path}: value must be a finite number or a list of numbers')
        if isinstance(v, list) and path in ECON_LEVERS:
            raise ValueError(f'override {path}: economic levers take one number')
        out.append({'id': f'override_{i + 1}', 'path': path, 'scenario': o.get('scenario', 'all'), 'value': deepcopy(v),
                    'reason': o['reason'], 'evidence_ids': list(ev), 'raw': raw and path not in ECON_LEVERS})
    return out


def _check_reviews(reviews):
    out = {}
    for aid, r in (reviews or {}).items():
        if not isinstance(r, dict) or r.get('status') not in ('reviewed', 'unreviewed'):
            raise ValueError(f'review {aid}: status must be reviewed or unreviewed')
        if r['status'] == 'reviewed' and (not isinstance(r.get('reviewer'), str) or not r['reviewer'].strip()):
            raise ValueError(f'review {aid}: a reviewed assumption needs a named reviewer')
        out[aid] = r
    return out


def _rate_input(fb, spec, default, label):
    """inputs['risk_free'] / ['equity_risk_premium'] -> (value, source dict or None, note)."""
    if not spec:
        return default, None, f'{label} default {default:.2%} (model assumption)'
    if not isinstance(spec, dict):
        raise ValueError(f'{label}: must be an object with value, as_of and source_id or title')
    extra = sorted(set(spec) - {'value', 'as_of', 'source_id', 'title', 'url', 'kind', 'retrieval'})
    if extra:
        raise ValueError(f'{label}: unknown keys {extra}')
    v = number(spec.get('value'), label, -.05, .3)
    when = spec.get('as_of') or fb['as_of']
    if _d(when) > _d(fb['as_of']):
        raise ValueError(f'{label} dated after the FactBook as_of (look-ahead)')
    sid = spec.get('source_id')
    if sid and sid in fb['sources']:
        return v, {'fb': sid}, f'{label} {v:.2%} from {sid} ({when})'
    if not spec.get('title'):
        raise ValueError(f'{label}: supply source_id (FactBook source) or title/url for the dated quote')
    kind = spec.get('kind')
    if kind is not None and str(kind).strip().lower() in PRIMARY_CLAIMS:
        raise ValueError(f"{label}: kind {kind!r} claims a primary document, but a value typed into the inputs is analyst input; "
                         'register the document in the FactBook and cite its source_id')
    retrieval = spec.get('retrieval', 'provider_normalized')
    if retrieval not in RETRIEVALS:
        raise ValueError(f'{label}: retrieval must be one of {list(RETRIEVALS)}')
    sid = 'input:' + label
    src = {'id': sid, 'title': spec['title'], 'url': spec.get('url') or f'input://{label}', 'kind': 'analyst_input',
           'published_at': when, 'available_at': when, 'retrieval': retrieval}
    if kind is not None:
        src['stated_kind'] = str(kind)
    if not spec.get('as_of'):
        src['published_at_estimated'] = True
    return v, {'case': src}, f'{label} {v:.2%} ({spec["title"]}, {when}; analyst input)'


# ---------------------------------------------------------------- routing and history

def _ramp(x, lo, hi):
    """0 at lo, 1 at hi, linear between (hi < lo reverses the direction); None -> 0."""
    if x is None:
        return 0.
    return _clamp((x - lo) / (hi - lo), 0., 1.)


def _margin_franchise(h):
    """0..1 pricing-power weight from the margin record alone: a high, stable gross margin (or operating margin where no cost
    of revenue is reported, as for a payment network). The same test the moat proxy uses, isolated so a cyclical whose margins
    swing cannot look like a franchise even when its boom-window ROIC does."""
    gms, oms = h.get('gross_margins') or [], h.get('operating_margins') or []
    if len(gms) >= 3 and gms[-1] - gms[0] > -.05:
        return min(_ramp(gms[-1], *PROXY_RAMPS['gross_margin']), _ramp(max(gms) - min(gms), *PROXY_RAMPS['gross_margin_range'])), \
            f'gross margin {_pct(gms[-1])}, range {_pct(max(gms) - min(gms))} over {len(gms)} years'
    if len(oms) >= 3 and oms[-1] - oms[0] > -.05:
        return min(_ramp(oms[-1], *PROXY_RAMPS['operating_margin']), _ramp(max(oms) - min(oms), *PROXY_RAMPS['operating_margin_range'])), \
            f'operating margin {_pct(oms[-1])}, range {_pct(max(oms) - min(oms))} over {len(oms)} years (no cost of revenue reported)'
    return 0., 'margin stability not evidenced (fewer than three margins, or falling)'


def _franchise_durability(h, ku, route):
    """0..1 evidence that today's excess return on capital will persist. A durable franchise shows ALL of: a realized ROIC that
    clears the hurdle by a wide margin, a stable ROIC record over a long enough window, and a high, stable margin (pricing
    power). The weakest of the three decides (as in the moat proxy), so a cyclical whose margins swing earns little even when
    its boom-window ROIC looks high and stable. Withheld entirely from the inherently cyclical business models and from any
    company the card/analyst already flags cyclical -- their return is the cycle's, assessed through the mid-cycle margin, not a
    persistence premium. This is the Base Rate Book / McKinsey persistence signal, computable with no analyst input."""
    if route['cyclical'] or route['cycle_path'] or route['business_model'] in CYCLICAL_LIFT_EXCLUDED_TYPES:
        return 0., {'durability': 0., 'basis': f"cyclical business ({route['business_model']}): the cycle's average return, no persistence premium"}
    ser = [x for x in (h.get('roic_series') or []) if x is not None]
    dem = _demonstrated_roic(h)
    if len(ser) < PERSISTENCE_MIN_YEARS or dem is None:
        return 0., {'durability': 0., 'years': len(ser), 'basis': f'ROIC record under {PERSISTENCE_MIN_YEARS} years'}
    mean = sum(ser) / len(ser)
    if mean <= 0:
        return 0., {'durability': 0., 'years': len(ser), 'basis': 'average realized ROIC not positive'}
    cv = (sum((x - mean) ** 2 for x in ser) / len(ser)) ** .5 / mean
    excess = _ramp(dem - ku, *PERSISTENCE_EXCESS_RAMP)
    stability = _ramp(cv, *PERSISTENCE_STABILITY_RAMP)
    margin, m_basis = _margin_franchise(h)
    dur = round(min(excess, stability, margin), 4)
    return dur, {'durability': dur, 'years': len(ser), 'roic_cv': round(cv, 3), 'demonstrated_excess': round(dem - ku, 4),
                 'excess_weight': round(excess, 3), 'stability_weight': round(stability, 3), 'margin_weight': round(margin, 3),
                 'basis': f'demonstrated ROIC {_pct(dem)} vs hurdle {_pct(ku)} (excess {excess:.2f}); ROIC variation {cv:.0%} '
                          f'over {len(ser)} years (stability {stability:.2f}); {m_basis} (margin {margin:.2f})'}


def _demonstrated_roic(h):
    """Demonstrated ROIC: the lower of the latest and the multi-year average -- on a scale-up path the post-breakeven average
    (_history, _post_breakeven_roic) -- or None when neither is known. The 40% cap is applied where it is used."""
    shown = [x for x in (h.get('roic'), h.get('roic_avg_demonstrated', h.get('roic_avg'))) if x is not None]
    return min(shown) if shown else None


def _post_breakeven_roic(h, window):
    """ROIC of the post-breakeven years when the ROIC window's own fiscal years are a scale-up path, else None.
    window: the forensics ROIC entries the 5-year average spans ({'fiscal_year', 'roic'}, oldest first). They are matched to the
    history rows by fiscal-period label (no match, no reading: the exemption needs evidence); the shape is read over the
    contiguous fiscal years from the window's first to its last (history.history_shape, as the card and the growth start do);
    post-breakeven = the window years after its last loss year (operating margin below zero). At least SCALE_UP_ROIC_MIN_YEARS."""
    at = {r.get('fiscal_period'): i for i, (_, r) in enumerate(h['rows']) if r.get('fiscal_period') is not None}
    idx = [at.get(x.get('fiscal_year')) for x in window]
    if len(window) <= SCALE_UP_ROIC_MIN_YEARS or None in idx or any(b <= a for a, b in zip(idx, idx[1:])):
        return None
    lo, hi = idx[0], idx[-1] + 1
    if history_shape(h['revenue'][lo:hi], h['margins'][lo:hi]) != 'scale_up':
        return None
    last_loss = max(i for i in range(lo, hi) if h['margins'][i] is not None and h['margins'][i] < 0)
    post = [_num(x['roic']) for x, i in zip(window, idx) if i > last_loss]
    return post if len(post) >= SCALE_UP_ROIC_MIN_YEARS else None


def _moat_proxy(card, h, fb, normalize):
    """Weight (0..1) of the strong-moat rules for base and bull when no analyst assessed the moat, with its evidence."""
    dims = card.get('dimensions') or {}
    blocked = []
    cycle_roic = [x for x in (h.get('roic_series_all') or [])[-10:]]
    through_cycle = normalize and len(cycle_roic) >= CYCLE_FIT_MIN_YEARS
    if normalize and not through_cycle:
        blocked.append('cyclical normalization (returns measured over the cycle, not a franchise)')
    weak = [d for d in PROXY_BLOCK_DIMS if (dims.get(d) or {}).get('assessment') == 'weak' and (dims.get(d) or {}).get('source') == 'analyst']
    if weak:
        blocked.append('analyst rates ' + ', '.join(weak) + ' weak')
    topics = sorted({q.get('topic') for q in fb.get('qualitative', []) if q.get('topic') in PROXY_EVIDENCE_TOPICS
                     and (dims.get(q.get('topic')) or {}).get('source') != 'analyst'})
    if topics:
        blocked.append('the FactBook holds unassessed evidence on ' + ', '.join(topics) + ' (an analyst must weigh it)')
    pp = dims.get('pricing_power') or {}
    gms = h.get('gross_margins') or []
    if pp.get('source') == 'analyst':
        w_pp, pp_note = (1. if pp.get('assessment') == 'strong' else 0.), f"analyst pricing power {pp.get('assessment')}"
    elif len(gms) >= 3 and gms[-1] - gms[0] > -.05:
        w_pp = min(_ramp(gms[-1], *PROXY_RAMPS['gross_margin']), _ramp(max(gms) - min(gms), *PROXY_RAMPS['gross_margin_range']))
        pp_note = f'gross margin {_pct(gms[-1])}, range {_pct(max(gms) - min(gms))} over {len(gms)} years'
    elif len(gms) < 3 and len(h.get('operating_margins') or []) >= 3 and h['operating_margins'][-1] - h['operating_margins'][0] > -.05:
        # no cost of revenue reported (Mastercard, Visa): a high, stable operating margin is the pricing-power evidence instead
        oms = h['operating_margins']
        w_pp = min(_ramp(oms[-1], *PROXY_RAMPS['operating_margin']), _ramp(max(oms) - min(oms), *PROXY_RAMPS['operating_margin_range']))
        pp_note = (f'no gross margin reported; operating margin {_pct(oms[-1])}, range {_pct(max(oms) - min(oms))} over {len(oms)} years '
                   '(fallback evidence of pricing power)')
    else:
        w_pp, pp_note = 0., 'pricing power not evidenced (fewer than three gross margins, or falling)'
    roic_avg = h.get('roic_avg')
    # a cyclical's average return is the cycle's; a franchise still earns its excess return at the trough, so with at least
    # CYCLE_FIT_MIN_YEARS years the LOWEST yearly ROIC of the last ten replaces the 5-year average (ASML: 17% at its trough)
    roic_used = min(cycle_roic) if through_cycle else roic_avg
    parts = {'pricing_power': w_pp, 'roic_avg': _ramp(roic_used, *PROXY_RAMPS['roic_avg']),
             'incremental_roic': _ramp(h.get('inc_roic'), *PROXY_RAMPS['incremental_roic'])}
    w = 0. if blocked else round(min(parts.values()), 4)
    note = (f"{pp_note}; " + (f"trough ROIC {_pct(roic_used)} over {len(cycle_roic)} years (cyclical: the cycle's low, not its average)"
                              if through_cycle else f"average ROIC {_pct(roic_avg)} over {len(h.get('roic_series') or [])} years")
            + f"; incremental ROIC {_pct(h.get('inc_roic'))}" + ('' if h.get('inc_roic') is not None else ' (not measurable: no proxy)'))
    return {'weight': w, 'parts': parts, 'blocked': blocked, 'evidence': note}


def _route(card, h, warn, fb=None, cyc_input=None):
    val = card.get('valuation') or {}
    bm = (card.get('business_model') or {}).get('primary')
    method = val.get('method') or 'fcff_annual_path'
    stage = (card.get('stage') or {}).get('value')
    if stage not in STAGE:
        g, m = h['g_anchor'], h['m0']
        stage = ('early' if m is not None and m < 0 and g > .2 else 'growth' if g > .15 else 'maturing' if g > .07
                 else 'decline' if g < 0 else 'mature')
        warn('stage_inferred', f'model card stage missing or unknown; inferred {stage} from growth {_pct(g)} and margin {_pct(m)}')
    cyc_card = card.get('cyclical') or {}
    cyclical = bool(cyc_card.get('value')) or method == 'normalized_cycle_fcff'        # cyclical economics (Ke add-on)
    normalize = method == 'normalized_cycle_fcff' or bool(cyc_card.get('normalize'))   # history shows a cycle: normalize it
    # an industry-cyclical company at a margin peak the history has not shown to be a cycle (the card's own position 'peak', no
    # completed cycle in the data, no analyst decision on the card): the peak is never extrapolated silently. Unless an analyst
    # says structural (inputs['cyclical']) it is valued around the cycle (mid-cycle revenue and margin, the conservative
    # reading), with an unresolved material issue until a named reviewer accepts the treatment.
    peak = (bool(cyc_card.get('value')) and cyc_card.get('position') == 'peak' and not cyc_card.get('data_shows_cycle')
            and cyc_card.get('basis') != 'analyst' and not cyc_card.get('override'))
    treatment = (cyc_input or {}).get('treatment') if peak else None
    forced = peak and not normalize and treatment != 'structural'
    # an evidenced analyst decision also governs a cycle the history shows: Powell 2026-10 (oil-and-gas bust 2015-2022, now
    # two-thirds utilities, commercial and data centers) had its 'structural' input silently ignored and revenue reverted
    # 8% to the old trend. The decision stays visible and needs a reviewer (cycle_treatment_override).
    override = None
    if not peak and (cyc_input or {}).get('treatment') in CYCLE_TREATMENTS:
        want = cyc_input['treatment'] == 'cyclical'
        if want != normalize:
            override, normalize = cyc_input['treatment'], want
            if not want and method == 'normalized_cycle_fcff':
                method = 'fcff_annual_path'
            warn('cycle_treatment_override', f"the model card {'normalized' if not want else 'did not normalize'} the cycle; an analyst "
                                             f"judged it {cyc_input['treatment']} ({cyc_input['reason']}): valued "
                                             + ('without' if not want else 'with') + ' cycle normalization until a reviewer accepts it', 'medium')
    if forced:
        normalize = True
    elif peak and treatment == 'structural':
        normalize = False
    # the card's reading of a history that is not cycle-shaped (a scale-up, a loss-making grower, pre-commercial noise) stands
    # until an analyst confirms a cycle (card inputs.cyclical, or treatment 'cyclical' here)
    confirmed = bool((cyc_card.get('override') or {}).get('value')) or (cyc_input or {}).get('treatment') == 'cyclical'
    if normalize and stage in ('early', 'growth'):
        warn('cyclical_stage_normalized', f'card stage {stage} for a cyclical business: growth measured into a cyclical peak is not a '
                                          'growth stage; the forecast uses mature-stage parameters around the cycle', 'medium')
        stage = 'mature'
    if bm not in TYPE:
        warn('business_model_default', f'business model {bm!r} has no forecast type table; generic ranges used', 'low')
    typ = TYPE.get(bm, DEFAULT_TYPE)
    archetype = val.get('archetype') or typ[3]
    dims = card.get('dimensions') or {}
    mrow = dims.get('moat') or {}
    moat, basis, proxy = mrow.get('assessment', 'unknown'), 'card', None
    if mrow.get('source') == 'analyst':
        # an analyst row is final, including 'unknown': a computed proxy never overrides an analyst's judgement
        basis = f"analyst assessment ({moat})" + (f": {mrow['note']}" if mrow.get('note') else '')
        moat = moat if moat in ('strong', 'adequate', 'weak') else 'unknown'
    elif moat not in ('strong', 'adequate', 'weak'):
        moat = 'unknown'
        proxy = _moat_proxy(card, h, fb or {}, normalize)
        if proxy['weight'] > 0:
            basis = (f"computed proxy, weight {proxy['weight']:.2f} on the strong-moat rules for base and bull (the bear keeps the "
                     f"unassessed-moat rules): {proxy['evidence']}")
        else:
            basis = ('no analyst moat assessment; no computed proxy (' + ('; '.join(proxy['blocked']) if proxy['blocked'] else
                                                                           f"evidence too weak: {proxy['evidence']}") + ')')
    weights = {s: (1. if moat == 'strong' else (proxy['weight'] if proxy and s != 'bear' else 0.)) for s in SCEN}
    # the rate charge needs evidence of a cycle (KE_FACTORS 'cyclical'); a word-only flag stays a visible question
    evidence = cyclical_evidence(card, (cyc_input or {}).get('treatment'))
    if cyclical and not evidence['evidenced'] and evidence['question']:
        warn('cyclical_unevidenced', evidence['question'], 'low')
    return {'method': method, 'business_model': bm, 'stage': stage, 'type': typ, 'archetype': archetype, 'cyclical': cyclical, 'normalize': normalize,
            'cyclical_evidence': evidence,
            'cycle_path': normalize, 'cycle_peak': peak, 'cycle_treatment': treatment if peak else override, 'cycle_forced': forced,
            'cycle_override': override, 'cycle_history': None if confirmed else cyc_card.get('history'),
            'capital_intensity': (card.get('capital_intensity') or {}).get('value'),
            'funding': (card.get('funding') or {}).get('value'), 'moat': moat, 'moat_basis': basis, 'moat_proxy': proxy,
            'moat_weights': weights, 'dimensions': dims}


def _latest_src(fb, *metrics):
    ids = []
    for m in metrics:
        s = facts.series(fb, m, 'annual') or facts.series(fb, m, 'instant')
        if s:
            ids.append(s[-1][2]['source_id'])
    return sorted(set(ids))


def _ytd(fb, metric, last_end, upto=None):
    """Fiscal year to date after the last fiscal year end (standalone quarters, contiguous, or a YTD fact), with the same
    months a year earlier when reported: {'value', 'end', 'prior', 'basis', 'source_ids'} or None. upto: end at that date."""
    a_end = _d(last_end)
    lim = _d(upto) + timedelta(days=7) if upto else None
    q = [x for x in facts.series(fb, metric, 'quarter') if lim is None or _d(x[0]) <= lim]
    post = [x for x in q if 7 < (_d(x[0]) - a_end).days <= 366]     # the fiscal year after the last reported one only
    steps = [(_d(b[0]) - _d(a)).days for a, b in zip([last_end] + [x[0] for x in post], post)]
    if post and all(60 <= x <= 120 for x in steps) and (upto is None or abs((_d(post[-1][0]) - _d(upto)).days) <= 7):
        prior = [next((y for y in q if abs((_d(x[0]) - _d(y[0])).days - 364) <= 10), None) for x in post]
        return {'value': sum(x[1] for x in post), 'end': post[-1][0], 'prior': sum(y[1] for y in prior) if all(prior) else None,
                'basis': f'{len(post)} reported quarter(s) after {last_end}', 'source_ids': sorted({x[2]['source_id'] for x in post})}
    ytd = [f for f in facts.candidates(fb, metric) if f['period'].startswith('YTD@')]
    cur = sorted((f for f in ytd if 7 < (_d(f['period_end']) - a_end).days <= 366 and (lim is None or _d(f['period_end']) <= lim)),
                 key=lambda f: f['period_end'])
    if cur and (upto is None or abs((_d(cur[-1]['period_end']) - _d(upto)).days) <= 7):
        now = cur[-1]
        prev = [f for f in ytd if abs((_d(now['period_end']) - _d(f['period_end'])).days - 364) <= 10]
        return {'value': now['value'], 'end': now['period_end'], 'prior': prev[0]['value'] if prev else None,
                'basis': f'year-to-date fact to {now["period_end"]}', 'source_ids': [now['source_id']]}
    return None


ACQ_UPLIFT = .05        # pro forma prior-period revenue above the reported figure by more than this: bought revenue in the comparison
ACQ_JUMP = .10          # without pro forma: year-to-date growth this far above the pre-deal growth is read as bought


def _acquired(fb, an, h, warn):
    """Acquired revenue in the year-to-date comparison is a level shift, not growth to extrapolate (Dick's Sporting Goods
    2026: +58% year to date with Foot Locker, 2.7% organic; the fade had started the next year at 25%). With the filing's
    pro forma revenue for the same months a year earlier (ASC 805), organic growth = year to date (or its pro forma) / pro
    forma prior - 1. Without it, an acquisition the analysis flags in the current or last fiscal year whose year-to-date
    growth is more than ACQ_JUMP above the pre-deal like-for-like growth uses the median pre-deal growth, flagged high.
    -> {'basis', 'organic', 'timing', ...} or None."""
    ytd = h['ytd']
    if not ytd or not ytd.get('prior') or ytd['prior'] <= 0:
        return None
    span = (_d(ytd['end']) - _d(h['last_end'])).days
    pfs = facts.candidates(fb, 'revenue_pro_forma')
    at = lambda end, days: [f for f in pfs if abs((_d(f['period_end']) - end).days) <= 10 and f.get('period_start')
                            and abs((_d(f['period_end']) - _d(f['period_start'])).days - days) <= 20]
    top = lambda fs: max(fs, key=lambda f: f['value'], default=None)
    prior = top(at(_d(ytd['end']) - timedelta(days=364), span))
    rows = an.get('acquisition_years') or []
    timing = ('current_fiscal_year' if any(a.get('current_year_to_date') for a in rows) else
              'last_fiscal_year' if any(a.get('period_end') == h['last_end'] for a in rows) else 'unknown')
    if prior and prior['value'] / ytd['prior'] - 1 > ACQ_UPLIFT:
        cur, fy = top(at(_d(ytd['end']), span)), top(at(_d(h['last_end']), 365))
        organic = (cur['value'] if cur else ytd['value']) / prior['value'] - 1
        return {'basis': 'pro_forma', 'organic': organic, 'timing': timing, 'uplift': prior['value'] / ytd['prior'] - 1,
                'pro_forma_prior': prior['value'], 'pro_forma_current': cur and cur['value'], 'pro_forma_last_fy': fy and fy['value'],
                'acquired_rate': (prior['value'] - ytd['prior']) / (span / 365.25), 'concept': prior.get('concept'),
                'source_ids': sorted({f['source_id'] for f in (prior, cur, fy) if f}),
                'statement': (f"pro forma revenue for the same months a year earlier {prior['value']:,.0f} against {ytd['prior']:,.0f} "
                              f"reported ({_pct(prior['value'] / ytd['prior'] - 1)} bought): organic growth {_pct(organic)} against "
                              f"{_pct(ytd['value'] / ytd['prior'] - 1)} reported")}
    gs = [(x['period_end'], x['growth']) for x in (((an.get('metrics') or {}).get('revenue_growth_series') or {}).get('value') or [])]
    if timing == 'unknown' or h.get('g_recent') is None:
        return None
    pre = sorted(g for e, g in [x for x in gs if timing == 'current_fiscal_year' or _d(x[0]) < _d(h['last_end']) - timedelta(days=20)][-3:])
    if not pre:
        return None
    g_pre = pre[len(pre) // 2]
    if h['g_recent'] - g_pre <= ACQ_JUMP:
        return None
    warn('acquired_growth_unverified', f"year-to-date growth {_pct(h['g_recent'])} includes an acquisition ({timing.replace('_', ' ')}) and the "
                                       f"saved filings carry no pro forma revenue: the forecast fades from the pre-deal like-for-like growth "
                                       f"{_pct(g_pre)} (median of the last {len(pre)} years); read the acquisition note for organic growth", 'high')
    return {'basis': 'pre_deal_growth', 'organic': g_pre, 'timing': timing, 'pre_deal_years': len(pre),
            'statement': (f"year-to-date growth {_pct(h['g_recent'])} with an acquisition in the {timing.replace('_', ' ')}; no pro forma "
                          f"revenue saved: organic growth taken as the pre-deal median {_pct(g_pre)}")}


def _scale_cap(rev, backlog):
    i = next(k for k, (lim, _) in enumerate(SCALE_CAPS) if rev < lim)
    lifted = backlog is not None and backlog >= rev and i > 0
    return SCALE_CAPS[i - 1 if lifted else i][1], lifted


def _comparable_from(h, an):
    """(first index of the latest comparable run of fiscal years, first index of revenue at today's scale). A gap between
    fiscal years (fresh-start reorganization, fiscal-year change: ends more than 380 days apart) starts a new run; a
    material acquisition step (analysis acquisition year with a growth step) starts today's revenue scale the year after.
    Chesapeake's pre-bankruptcy impairments and the Southwestern merger had put Expand Energy's mid-cycle margin at the 2%
    floor and its mid-cycle revenue far below today's scale."""
    ends = h['ends']
    run = max([i for i in range(1, len(ends)) if (_d(ends[i]) - _d(ends[i - 1])).days > 380] or [0])
    bought = {a['period_end'] for a in an.get('acquisition_years') or [] if a.get('growth_step_in_table')}
    scale = max([run] + [i + 1 for i, e in enumerate(ends) if e in bought and i >= run])
    return run, min(scale, len(ends))


def _cycle_stats(h):
    """Mid-cycle revenue: the log-linear trend of the window, the long-run (out-of-sample) rate bounded, and the historical
    peak / trough relative to that trend. Only revenue at today's scale (_comparable_from); None with fewer than three such
    years. With at least CYCLE_FIT_MIN_YEARS years the trend level at the latest fiscal year is the fitted value of the
    regression itself (in sample, measured); only the growth AFTER it is bounded. With fewer years one up-leg cannot be told
    from a trend, so the window mean is placed at its mid date and moved at the bounded rate (conservative). Bounding the
    in-sample slope too had put ASML's mid-cycle revenue 25% below its 2025 level although nine years of 18% trend growth
    left 2025 below the fitted trend (2026-10)."""
    k = h.get('scale_from', 0)
    ends, rev = h['ends'][k:][-10:], h['revenue'][k:][-10:]
    if len(rev) < 3:
        return None
    x = [years(ends[0], e) for e in ends]
    y = [math.log(r) for r in rev]
    xm, ym = sum(x) / len(x), sum(y) / len(y)
    sxx = sum((a - xm) ** 2 for a in x)
    b = sum((a - xm) * (c - ym) for a, c in zip(x, y)) / sxx if sxx > 0 else 0.
    slope = math.exp(b) - 1
    g, g_basis, lower = _clamp(slope, *CYCLE_TREND), 'bounded', None
    mid = (_d(ends[0]) + timedelta(days=round(xm * 365.25))).isoformat()
    mean = sum(rev) / len(rev)
    fitted = len(rev) >= CYCLE_FIT_MIN_YEARS
    if fitted and sxx > 0:
        # a secular grower in a cyclical industry: growth above the long-run bound is used only as far as the data support it
        # (the slope less two standard errors, about the lower end of a 95% interval), within the revenue-scale base rate
        se = math.sqrt(sum((c - ym - b * (a - xm)) ** 2 for a, c in zip(x, y)) / (len(x) - 2) / sxx)
        lower = math.exp(b - 2 * se) - 1
        cap = (h.get('g_scale_cap') or {}).get('cap')
        if lower > CYCLE_TREND[1]:
            g, g_basis = min(lower, cap) if cap is not None else lower, 'measured_lower_bound'
    trend = ((lambda e: math.exp(ym + b * (years(ends[0], e) - xm))) if fitted else (lambda e: mean * (1 + g) ** years(mid, e)))
    ratio = [r / trend(e) for e, r in zip(ends, rev)]
    return {'mean_revenue': mean, 'mid_date': mid, 'slope': slope, 'slope_lower_2se': lower, 'trend_growth': g,
            'trend_growth_basis': g_basis, 'years': len(rev), 'trend_end': ends[-1], 'trend_level': trend(ends[-1]),
            'trend_basis': 'fitted' if fitted else 'window_mean', 'peak_ratio': max(ratio), 'trough_ratio': min(ratio),
            'window': [ends[0], ends[-1]]}


def _history(fb, an, warn, nonrecurring=None):
    """Evidence the paths start from; analysis.py metrics first, FactBook accessors as fallback. nonrecurring: evidenced
    one-offs removed from the fiscal years, TTM and year to date they fall in (inputs['nonrecurring'])."""
    ann = an.get('annual') or {}
    if not ann:
        t = facts.annual_table(fb, ['revenue', 'operating_income', 'sbc', 'd_and_a', 'capex', 'cfo', 'buybacks',
                                    'dividends_paid', 'acquisitions', 'interest_expense', 'pretax_income', 'income_tax'])
        ann = {e: dict(r, dividends=r.get('dividends_paid')) for e, r in t.items()}
        warn('analysis_annual_missing', 'analysis annual table absent; history read from the FactBook directly', 'low')
    rows = [(e, ann[e]) for e in sorted(ann) if (_num(ann[e].get('revenue')) or 0) > 0 and _d(e) <= _d(fb['as_of'])]
    if not rows:
        return None
    nr = [dict(it, revenue=_num(it.get('revenue')) or 0., operating_income=_num(it.get('operating_income')) or 0.)
          for it in nonrecurring or []]
    inside = lambda it, end, days=366: 0 <= (_d(end) - _d(it['period_end'])).days < days
    if nr:
        rows = [(e, dict(r)) for e, r in rows]
        for it in nr:
            for e, r in rows:
                if inside(it, e):
                    r['revenue'] = r['revenue'] - it['revenue']
                    if _num(r.get('operating_income')) is not None:
                        r['operating_income'] = r['operating_income'] - it['operating_income']
            warn('nonrecurring_removed', f"{it['kind']} at {it['period_end']} removed from the history (revenue {it['revenue']:,.0f}, "
                                         f"operating income {it['operating_income']:,.0f}): {it['reason']}", 'medium')
    ends, rev = [e for e, _ in rows], [r['revenue'] for _, r in rows]
    g = lambda r, k: _num(r.get(k))
    growth = [b / a - 1 for a, b in zip(rev, rev[1:])]
    margins = [g(r, 'operating_income') / r['revenue'] if g(r, 'operating_income') is not None else None for _, r in rows]
    h = {'rows': rows, 'ends': ends, 'revenue': rev, 'growth': growth, 'margins': margins,
         'last_end': ends[-1], 'last_revenue': rev[-1], 'n_years': len(rows)}
    ttm = an.get('ttm') or {}
    t_rev, t_end, t_method = _num(ttm.get('revenue')), ttm.get('period_end'), ttm.get('method')
    if t_rev is None:
        t = facts.ttm(fb, 'revenue')
        t_rev, t_end, t_method = (t['value'], t['period_end'], t['method']) if t else (rev[-1], ends[-1], 'latest_fiscal_year')
    t_oi = _num(ttm.get('operating_income'))
    for it in nr:
        if t_end and t_method != 'latest_fiscal_year' and inside(it, t_end):
            t_rev -= it['revenue']
            t_oi = t_oi - it['operating_income'] if t_oi is not None else None
    if nr and t_end and t_method == 'latest_fiscal_year' and t_end == ends[-1]:
        t_rev = rev[-1]                                   # the fiscal year itself, already adjusted above
    h['ttm'] = {'revenue': t_rev, 'period_end': t_end, 'method': t_method,
                'operating_income': t_oi, 'sbc': _num(ttm.get('sbc'))}
    h['ytd'] = _ytd(fb, 'revenue', ends[-1])
    if h['ytd'] and nr:
        y = h['ytd'] = dict(h['ytd'])
        span = (_d(y['end']) - _d(ends[-1])).days
        for it in nr:
            if inside(it, y['end'], span):
                y['value'] -= it['revenue']
            if y.get('prior') is not None and 0 <= (_d(y['end']) - timedelta(days=364) - _d(it['period_end'])).days < span:
                y['prior'] -= it['revenue']
    use_ttm = h['ttm']['operating_income'] is not None and t_end != ends[-1] and bool(t_rev)
    oi = h['ttm']['operating_income'] if use_ttm else g(rows[-1][1], 'operating_income')
    base_rev = t_rev if use_ttm else rev[-1]
    h['m0'] = oi / base_rev if oi is not None and base_rev else _metric(an, 'operating_margin')
    h['m0_basis'] = f'TTM operating_income / TTM revenue to {t_end}' if use_ttm else f'operating_income / revenue, fiscal year ending {ends[-1]}'
    best = [m for m in margins[-3:] if m is not None]
    h['m_best3'] = _metric(an, 'operating_margin_best_3y')
    if h['m_best3'] is None and best:
        h['m_best3'] = max(best)
    h['m_all'] = [m for m in margins if m is not None]
    # growth anchors
    g_last = _metric(an, 'revenue_growth_last')
    g_last = growth[-1] if g_last is None and growth else g_last
    cagr = lambda n: (rev[-1] / rev[-1 - n]) ** (1 / n) - 1 if len(rev) > n and rev[-1 - n] > 0 else None
    c3 = _metric(an, 'revenue_cagr_3y'); c3 = cagr(3) if c3 is None else c3
    c5 = _metric(an, 'revenue_cagr_5y'); c5 = cagr(5) if c5 is None else c5
    h.update(g_last=g_last, cagr_3y=c3, cagr_5y=c5,
             cagr_5y_ex_acq=((((an.get('metrics') or {}).get('revenue_cagr_5y') or {}).get('detail') or {}).get('ex_acquisition_years')))
    g_recent, yrs, recent_how = None, years(ends[-1], t_end) if t_end else 0, None
    ytd = h['ytd']
    if ytd and ytd['prior'] and ytd['prior'] > 0 and years(ends[-1], ytd['end']) >= .2:
        g_recent, recent_how = ytd['value'] / ytd['prior'] - 1, 'year-to-date revenue vs the same months a year earlier'
    elif t_method in ('quarters', 'annual_plus_ytd') and yrs >= .45 and t_rev and rev[-1] > 0:
        g_recent, recent_how = (t_rev / rev[-1]) ** (1 / yrs) - 1, 'annualized TTM-vs-last-FY growth'
    h['g_recent'] = g_recent
    h['acq'] = _acquired(fb, an, h, warn)
    tiny = len(rev) > 3 and rev[-4] < .1 * rev[-1]       # a 3-year CAGR from a tiny base says nothing about the next year
    trail = _mean([g_last, None if tiny else c3])
    if trail is None:
        trail = 0.
        warn('growth_history_missing', 'fewer than two fiscal years of revenue; growth anchor set to 0% (model)', 'high')
    raw = trail if g_recent is None else .5 * g_recent + .5 * trail
    h['g_trailing_formula'] = (('mean(revenue_growth_last, revenue_cagr_3y)' if not tiny else 'revenue_growth_last (3-year base below 10% of '
                                'today\'s revenue)') if g_recent is None else
                               f'0.5 x {recent_how} + 0.5 x mean(revenue_growth_last, revenue_cagr_3y)')
    scale = t_rev if t_rev else rev[-1]
    bl = facts.series(fb, 'rpo', 'instant')
    h['backlog'] = bl[-1][1] if bl else None
    cap, lifted = _scale_cap(scale, h['backlog'])
    h['g_scale_cap'] = {'cap': cap, 'revenue': scale, 'backlog': h['backlog'], 'backlog_lifted': lifted, 'raw_anchor': raw}
    if raw > cap:
        warn('growth_scale_capped', f'trailing growth {_pct(raw)} is above the base-rate ceiling {_pct(cap)} for revenue of {scale:,.0f}'
                                    + (' (lifted one band by a backlog of ' + f'{h["backlog"]:,.0f})' if lifted else '') +
                                    '; anchor capped (guidance or consensus replaces it when present)', 'medium')
        h['g_trailing_formula'] += f', capped at the revenue-scale base rate {_pct(cap)}'
    h['g_trailing'] = min(raw, cap)
    h['g_cycle'] = c5 if c5 is not None else (c3 if c3 is not None else trail)
    vol = _metric(an, 'growth_volatility')
    if vol is None and len(growth) >= 3:
        mu = sum(growth) / len(growth)
        vol = (sum((x - mu) ** 2 for x in growth) / (len(growth) - 1)) ** .5
    h['growth_volatility'] = vol
    last = rows[-1][1]
    # SBC share of revenue over ONE period: TTM SBC with TTM revenue, else the SBC TTM with revenue over the same twelve
    # months, else the last fiscal year (never fiscal-year SBC over TTM revenue)
    h['sbc_pct'], h['sbc_basis'] = None, None
    if use_ttm and h['ttm']['sbc'] is not None:
        h['sbc_pct'], h['sbc_basis'] = h['ttm']['sbc'] / t_rev, f'TTM SBC / TTM revenue to {t_end}'
    else:
        ts = facts.ttm(fb, 'sbc')
        if ts and ts.get('method') != 'latest_fiscal_year' and _d(ts['period_end']) > _d(ends[-1]):
            ry = _ytd(fb, 'revenue', ends[-1], upto=ts['period_end'])
            if ry and ry['prior'] is not None and rev[-1] + ry['value'] - ry['prior'] > 0:
                h['sbc_pct'] = ts['value'] / (rev[-1] + ry['value'] - ry['prior'])
                h['sbc_basis'] = f"TTM SBC / revenue over the same twelve months to {ts['period_end']}"
        if h['sbc_pct'] is None and g(last, 'sbc') is not None:
            h['sbc_pct'], h['sbc_basis'] = g(last, 'sbc') / rev[-1], f'SBC / revenue, fiscal year ending {ends[-1]}'
    if h['sbc_pct'] is None:
        h['sbc_pct'], h['sbc_basis'] = _metric(an, 'sbc_pct_revenue'), 'analysis sbc_pct_revenue'
    if h['sbc_pct'] is None:
        h['sbc_pct'] = 0.
        warn('sbc_missing', 'SBC not reported in the history; SBC set to 0% of revenue (check the cash-flow statement)', 'medium')
    da = g(last, 'd_and_a')
    h['da_pct'] = da / rev[-1] if da is not None else None
    cap = [abs(g(r, 'capex')) / r['revenue'] for _, r in rows[-3:] if g(r, 'capex') is not None]
    h['capex_pct'] = _mean(cap) if cap else _metric(an, 'capex_pct_revenue')
    if h['da_pct'] is None:
        h['da_pct'] = h['capex_pct'] or .03
        warn('da_missing', f'D&A missing; depreciation set to {h["da_pct"]:.1%} of revenue (capex proxy or 3% default)', 'medium')
    if h['capex_pct'] is None:
        h['capex_pct'] = h['da_pct']
        warn('capex_missing', 'capex missing; maintenance capex set equal to depreciation', 'medium')
    h['acq_pct'] = _metric(an, 'acquisition_spend_pct_revenue_3y')
    h['s2c'] = _metric(an, 'sales_to_capital_incremental_3y')
    ic = [(e, _num(r.get('invested_capital')), r['revenue']) for e, r in rows if _num(r.get('invested_capital')) is not None]
    # revenue grew while invested capital did not: capital-light history (S2C undefined, not unknown)
    h['capital_light'] = len(ic) >= 2 and ic[-1][2] > ic[max(0, len(ic) - 4)][2] and ic[-1][1] <= ic[max(0, len(ic) - 4)][1]
    # 5-year incremental sales-to-capital: a longer window for when a 3-year revenue plateau distorts the 3-year ratio
    h['s2c_5y'] = ((ic[-1][2] - ic[-6][2]) / (ic[-1][1] - ic[-6][1])
                   if len(ic) >= 6 and ic[-1][1] - ic[-6][1] > 0 and ic[-1][2] > ic[-6][2] else None)
    h['roic'] = _metric(an, 'roic')
    h['inc_roic'] = _metric(an, 'incremental_roic_3y')
    ent = [x for x in (((an.get('forensics') or {}).get('roic') or {}).get('series') or [])
           if isinstance(x, dict) and _num(x.get('roic')) is not None]
    ser = [_num(x['roic']) for x in ent]
    h['roic_series_all'] = ser[-10:]
    ser = ser[-5:]
    h['roic_series'], h['roic_avg'] = ser, (_mean(ser) if ser else None)
    # the demonstrated average (terminal persistence rule, durability excess): on a scale-up path the post-breakeven years only
    h['roic_avg_demonstrated'], h['roic_avg_basis'] = h['roic_avg'], (f'mean of the last {len(ser)} yearly ROIC values' if ser else None)
    win = ent[-5:]
    post = _post_breakeven_roic(h, win) if ser else None
    if post:
        h['roic_avg_demonstrated'] = _mean(post)
        h['roic_avg_basis'] = (f"scale-up path ({win[0]['fiscal_year']}-{win[-1]['fiscal_year']}: revenue up every year, operating "
                               'margin from losses to the window high): '
                               f"mean of the {len(post)} post-breakeven years ({', '.join(_pct(x) for x in post)}) = "
                               f"{_pct(h['roic_avg_demonstrated'])}, not the {len(ser)}-year mean {_pct(h['roic_avg'])} that averages "
                               'the start-up losses')
        warn('roic_average_post_breakeven', 'demonstrated ROIC average for the terminal persistence rule: ' + h['roic_avg_basis'], 'low')
    gt = facts.annual_table(fb, ['revenue', 'gross_profit', 'cost_of_revenue'])
    gms = []
    for e in sorted(gt)[-5:]:
        r = gt[e]
        gp = r.get('gross_profit') if r.get('gross_profit') is not None else (r['revenue'] - r['cost_of_revenue'] if r.get('revenue') and r.get('cost_of_revenue') is not None else None)
        if gp is not None and r.get('revenue'):
            gms.append(gp / r['revenue'])
    h['gross_margins'] = gms
    ot = facts.annual_table(fb, ['revenue', 'operating_income'])
    h['operating_margins'] = [ot[e]['operating_income'] / ot[e]['revenue'] for e in sorted(ot)[-5:]
                              if ot[e].get('revenue') and ot[e].get('operating_income') is not None]
    h['nd_ebitda'] = _metric(an, 'net_debt_to_ebitda')
    h['run_from'], h['scale_from'] = _comparable_from(h, an)
    if h['scale_from']:
        warn('history_window_cut', f"cycle statistics use fiscal years from {ends[h['run_from']]} (margins) and "
                                   f"{ends[h['scale_from']] if h['scale_from'] < len(ends) else 'none'} (revenue scale): earlier years "
                                   'are before a reorganization gap or a material acquisition and are not comparable', 'medium')
    h['cycle'] = _cycle_stats(h) if len(rev) >= 3 else None
    dist, fcf = 0., 0.
    for _, r in rows[-3:]:
        f = g(r, 'fcf') if g(r, 'fcf') is not None else (g(r, 'cfo') - abs(g(r, 'capex') or 0) if g(r, 'cfo') is not None else None)
        if f is None:
            continue
        fcf += f
        dist += abs(g(r, 'buybacks') or 0) + abs(g(r, 'dividends') or g(r, 'dividends_paid') or 0)
    h['payout_history'] = dist / fcf if fcf > 0 else None
    # tax: income_tax / pretax over the last three years with positive pretax income
    tx = [(g(r, 'income_tax'), g(r, 'pretax_income')) for _, r in rows[-3:]]
    if all(a is None for a, _ in tx):
        tt = facts.annual_table(fb, ['income_tax', 'pretax_income'])
        tx = [(r.get('income_tax'), r.get('pretax_income')) for e, r in list(tt.items())[-3:]]
    tx = [(a, b) for a, b in tx if a is not None and b is not None and b > 0]
    h['tax_effective'] = sum(a for a, _ in tx) / sum(b for _, b in tx) if tx else None
    h['interest_expense'] = g(last, 'interest_expense')
    if h['interest_expense'] is None:
        s = facts.series(fb, 'interest_expense')
        h['interest_expense'] = abs(s[-1][1]) if s else None
    # operating working capital excluding cash, debt and deferred revenue (deferred revenue has its own ledger)
    wct = facts.annual_table(fb, ['revenue', 'receivables', 'inventory', 'payables'])
    wrow = [(e, r) for e, r in wct.items() if 'revenue' in r and ('receivables' in r or 'payables' in r)]
    wc = lambda r: (r.get('receivables') or 0) + (r.get('inventory') or 0) - (r.get('payables') or 0)
    h['wc_ratio'], h['wc_basis'] = None, None
    if len(wrow) >= 2:
        (e0, r0), (e1, r1) = wrow[max(0, len(wrow) - 4)], wrow[-1]
        dr = r1['revenue'] - r0['revenue']
        if dr > .02 * r1['revenue']:
            h['wc_ratio'], h['wc_basis'] = (wc(r1) - wc(r0)) / dr, f'change in (receivables + inventory - payables) / change in revenue, {e0}..{e1}'
    if h['wc_ratio'] is None and wrow:
        h['wc_ratio'], h['wc_basis'] = wc(wrow[-1][1]) / wrow[-1][1]['revenue'], f'level (receivables + inventory - payables) / revenue at {wrow[-1][0]}'
    h['wc_sources'] = _latest_src(fb, 'receivables', 'payables', 'inventory')
    return h


def _calendar(fb, h, n_full):
    """Fiscal-year boundaries after as_of, plus the fifth anniversary of as_of as its own valuation date (it splits the
    fiscal year containing it unless it falls on a boundary); the horizon always reaches one full fiscal year past it."""
    as_of, last = _d(fb['as_of']), _d(h['last_end'])
    fye = str(fb['company'].get('fiscal_year_end') or '')
    try:
        month, day = int(fye[:2]), int(fye[3:5])
        date(2000, month, day)
    except ValueError:
        month, day = last.month, last.day
    y = last.year
    while _fy(y, month, day) <= as_of:
        y += 1
    fifth = _fy(as_of.year + 5, as_of.month, as_of.day)
    j = next(i for i in range(60) if _fy(y + i, month, day) >= fifth)
    need = j if _fy(y + j, month, day) == fifth else j + 1
    extended = n_full < need
    n_full = max(n_full, need)
    b = [_fy(y + i, month, day) for i in range(n_full + 1)]
    interim = [] if fifth in b else [fifth.isoformat()]
    report = [fb['as_of']] + sorted([x.isoformat() for x in b[:j + 1]] + interim)
    return {'boundaries': [x.isoformat() for x in b], 'periods': sorted([x.isoformat() for x in b] + interim), 'interim': interim,
            'fifth_anniversary': fifth.isoformat(), 'n_full': n_full, 'extended_to_fifth': extended,
            'report_dates': report, 'stub_years': years(fb['as_of'], b[0].isoformat()),
            'fiscal_years_since_last_report': max(1, round(years(h['last_end'], b[0].isoformat()))),
            'fiscal_year_end': f'{month:02d}-{day:02d}',
            'report_rule': 'as_of, the fifth anniversary of as_of (an explicit valuation date: it splits its fiscal year, which '
                           'keeps one annual revenue rate) and every fiscal-year boundary through the first one on or after it '
                           '(nominal fiscal year-ends, so a 52/53-week calendar can differ by a few days)'}


def _consensus(fb, boundaries, last_end):
    """Revenue consensus per nominal fiscal boundary (period_end within 20 days); look-ahead rows dropped."""
    out, dropped = {}, 0
    for e in fb.get('estimates') or []:
        if e.get('metric') != 'revenue' or _num(e.get('value')) is None:
            continue
        if _d(e.get('as_of') or fb['as_of']) > _d(fb['as_of']):
            dropped += 1
            continue
        if _d(e['period_end']) <= _d(last_end):
            continue
        for b in boundaries:
            if abs((_d(e['period_end']) - _d(b)).days) <= 20:
                cur = out.get(b)
                if cur is None or (e.get('as_of') or '') > (cur.get('as_of') or ''):
                    out[b] = e
    return out, dropped


# ---------------------------------------------------------------- scenario construction

def _fade(start, terminal, half_life, n):
    """Excess growth over terminal decays with a half-life: g_t = g_T + (g_0 - g_T) * 0.5^(t / half_life), t = 1..n."""
    return [terminal + (start - terminal) * .5 ** (t / half_life) for t in range(1, n + 1)]


def _cost_of_equity(route, rf, erp, inputs_notes, external, regulated=False):
    """Table cost of equity: what a company of this business risk costs at MARKET-AVERAGE leverage (the ERP is measured on
    levered equities). _solve_capital unlevers it at MARKET_DE and relevers it at the company's own leverage."""
    st = STAGE[route['stage']]
    adj = [{'factor': 'stage:' + route['stage'], 'value': st['ke'], 'reason': f"business-risk add-on for the {route['stage']} stage"}]
    if regulated:
        adj.append({'factor': 'regulated_utility', 'value': -(1 - REGULATED_ASSET_RISK) * erp,
                    'reason': f'regulated utility: allowed returns and pass-through costs, asset risk about {REGULATED_ASSET_RISK:.0%} of the '
                              "market's (ERP x 0.5); the leverage it carries is priced by the relevering, not by this row"})
    ev = route.get('cyclical_evidence') or {'evidenced': route['cyclical'], 'why': None}
    cond = {'cyclical': route['cyclical'] and ev['evidenced'], 'capital_intensity_high': route['capital_intensity'] == 'high',
            'needs_external_funding': bool(external)}
    for key, v, why in KE_FACTORS:
        if cond[key]:
            adj.append({'factor': key, 'value': v, 'reason': why + (f": {ev['why']}" if key == 'cyclical' and ev.get('why') else '')})
    for d in WEAK_DIMENSIONS:   # at most 4 x 0.25pp = 1pp
        if (route['dimensions'].get(d) or {}).get('assessment') == 'weak':
            adj.append({'factor': 'weak_' + d, 'value': .0025, 'reason': f'model card rates {d} weak'})
    adj.append({'factor': 'market_neutral', 'value': MARKET_NEUTRAL_ADJUSTMENT,
                'reason': "the market's value-weighted average add-on, removed so the average company is discounted at risk-free + ERP "
                          '(the definition of the ERP); measured on the 2026-10-20 large-cap sweep'})
    table = _clamp(rf + erp + sum(a['value'] for a in adj), rf + .02, .20)
    # the mature table for the terminal (TERMINAL_KE_DROPPED): never above the explicit table, never below its own clamp
    kept = [a for a in adj if a['factor'] not in TERMINAL_KE_DROPPED]
    dropped = [a for a in adj if a['factor'] in TERMINAL_KE_DROPPED]
    terminal = {'table_rate': min(table, _clamp(rf + erp + sum(a['value'] for a in kept), rf + .02, .20)),
                'dropped': dropped, 'kept': [a['factor'] for a in kept],
                'reason': 'the forecast is mature by its terminal date (growth faded, mature margin): the perpetuity is discounted at '
                          'the mature table without the stage and one-off funding add-ons; structural rows stay, relevered at the same D/E'}
    # a flagged business shows why the cyclical row is or is not charged (a word-only flag: the open question, no row)
    cyc_ev = {'cyclical_evidence': {k: ev.get(k) for k in ('evidenced', 'basis', 'why', 'question')}} if route['cyclical'] else {}
    return table, {'risk_free': rf, 'equity_risk_premium': erp, 'adjustments': adj, 'notes': inputs_notes, 'terminal': terminal, **cyc_ev,
                   'formula': 'table = risk-free + equity risk premium + business-risk adjustments (stage, regulated utility, evidenced cyclicality, '
                              'capital intensity, funding need of the executed base plan, weak card dimensions): a documented table, NOT price '
                              f'beta, read as the cost of equity at market-average leverage (D/E {MARKET_DE:.2f}). Ku = (table + Kd_e x '
                              f'{MARKET_DE:.2f}) / (1 + {MARKET_DE:.2f}) with Kd_e = risk-free + {DEBT_EXPECTED_PREMIUM:.0%} (expected, not promised, '
                              'return on debt); Ke = max(table, Ku + (Ku - Kd_e) x D/E) and terminal WACC = E/V x Ke + D/V x (Kd_e - Kd_promised x '
                              "tax) (Harris-Pringle) with D/E and D/V from the model's own base equity value (fixed point), never the quote; same "
                              'Ke in every scenario so scenarios differ by business outcomes only. The terminal (components.terminal) uses the '
                              'mature table: the same rows without the stage and one-off funding add-ons (TERMINAL_KE_DROPPED), unlevered and '
                              'relevered the same way; the explicit years and the roll-back of the terminal value keep Ke'}


def _ke_parts(parts, table, lev):
    """Final components: the table rows plus the leverage relevering as its own row (Ke = sum of the rows)."""
    adj = list(parts['adjustments'])
    if lev['ke'] - table > 1e-12:
        adj.append({'factor': 'leverage_relevered', 'value': lev['ke'] - table,
                    'reason': f"financial leverage above the market average: Ku + (Ku - Kd_e) x D/E with D/E {lev['d_to_e']:.1%} from the "
                              "model's own equity value"})
    flat = {'adjustment_' + a['factor'].replace(':', '_'): a['value'] for a in adj}   # numeric keys for risk.py's priced-via lookup
    # the terminal's own rate: the mature table (parts['terminal']) unlevered at MARKET_DE and relevered at the same D/E
    term = dict(parts['terminal'], asset_unlevered=lev['unlevered_terminal'], cost_of_equity=lev['ke_terminal'],
                leverage_relevered=max(0., lev['ke_terminal'] - parts['terminal']['table_rate']),
                formula='terminal table = risk-free + ERP + the structural rows only (dropped: ' +
                        (', '.join(f"{a['factor']} {a['value']:+.2%}" for a in parts['terminal']['dropped']) or 'none') +
                        '); terminal Ke = max(terminal table, Ku_T + (Ku_T - Kd_e) x D/E) with the same D/E; terminal WACC = E/V x terminal '
                        'Ke + D/V x (Kd_e - Kd_promised x tax), floored at terminal growth + 3 points')
    # 'unlevered' stays the rate BEFORE the leverage row (consumers show Ke - unlevered as the leverage effect); the asset
    # (Harris-Pringle) rate behind the relevering is 'asset_unlevered'
    return {**{k: v for k, v in parts.items() if k != 'adjustments'}, **flat, 'adjustments': adj, 'table_rate': table,
            'unlevered': table, 'asset_unlevered': lev['unlevered'], 'leverage': lev, 'terminal': term}


def _solve_capital(spec, P, table, table_t, kd_e, kd_p, net_debt, ov_ke, cap_at_quote=True):
    """One fixed point on the model's own values, never the quote:
    1. the debt weight from the base scenario's equity value today;
    2. the issue price of every new share (assumed equity raises and SBC settled in shares) = min(quote, the scenario's own value
       per share right after the issue): no new investor pays more than the scenario says a share is worth (a bear does not raise
       at a price it cannot support), and when the quote is below that value existing holders bear the extra dilution
       (cap_at_quote=False prices at the scenario's own value only: the 'value at a second issue price' disclosure).
    The explicit years are discounted at Ke from the table; the terminal WACC is built from the terminal Ke, the mature table
    (table_t, TERMINAL_KE_DROPPED) unlevered and relevered the same way at the same D/E."""
    shares, quote, B = spec['opening']['shares'], spec['quote']['price'], spec['boundaries']
    ku = (table + kd_e * MARKET_DE) / (1 + MARKET_DE)
    ku_t = (table_t + kd_e * MARKET_DE) / (1 + MARKET_DE)
    issued = {sc['name']: [i for i in range(len(B)) if sc['equity_proceeds'][i] > 0 or sc['sbc_share_settlement'][i] > 0]
              for sc in spec['scenarios']}
    for sc in spec['scenarios']:
        p0 = sc.get('equity_issue_price', quote)
        sc['equity_issue_price'] = [float(x) for x in p0] if isinstance(p0, list) else [float(p0)] * len(B)

    def apply(w):
        de = w / (1 - w)
        ke = max(table, ku + (ku - kd_e) * de)
        ke_t = max(table_t, ku_t + (ku_t - kd_e) * de)      # the terminal: the mature table relevered at the same D/E
        for sc in spec['scenarios']:
            s = sc['name']
            k_e, k_t = ov_ke.get(s, ke), ov_ke.get(s, ke_t)   # an analyst override is the rate of both
            wacc = (1 - w) * k_t + w * (kd_e - kd_p * P[s]['tax'])
            P[s].update(ke=k_e, ke_terminal=k_t, wacc=max(wacc, P[s]['g_terminal'] + .03))
            sc['cost_of_equity'] = _r(k_e)
            sc['terminal']['cost_of_equity'] = _r(k_t)
            sc['terminal']['wacc'] = _r(P[s]['wacc'])
            sc['terminal']['debt_weight'] = float(w)    # a change in Ke moves this WACC by (1 - w) x the change (valuation_tools)
        return ke, ke_t, de
    w, e, it = 0., None, 0
    status = 'no_net_debt' if net_debt <= 0 else 'converged'
    ke, ke_t, de = apply(0.)
    floor = quote * 1e-6
    hist = {}                                  # (scenario, period) -> recent plain iterates of p = V(p), for Aitken extrapolation
    for it in range(1, 151):
        res, err = _run(spec)
        if err:
            status = 'invalid: ' + err
            break
        changed = False
        for sc, rs in zip(spec['scenarios'], res['scenarios']):
            if not issued[sc['name']] or rs['status'] == 'funding_blocked':
                continue
            bv = {x['date']: x['value_per_share'] for x in rs.get('boundary_values') or []}
            for i in issued[sc['name']]:
                v = bv.get(B[i], quote)
                new = max(floor, min(quote, v) if cap_at_quote else v)
                old = sc['equity_issue_price'][i]
                if abs(new - old) <= max(1e-9 * max(new, old), 2e-9):  # 9 significant digits (prices are stored to 1e-9)
                    continue
                h = hist.setdefault((sc['name'], i), [old])
                h.append(new)
                if len(h) >= 3:                                        # geometric convergence: jump to the Aitken limit
                    d1, d2 = h[-2] - h[-3], h[-1] - h[-2]
                    r = d2 / d1 if d1 else 0.
                    if 0 < r < .98:
                        new = h[-1] + d2 * r / (1 - r)
                        new = max(floor, min(quote, new) if cap_at_quote else new)
                        hist[(sc['name'], i)] = [new]
                sc['equity_issue_price'][i], changed = _r(new, 9), True
        if net_debt > 0:
            v = res['annual_values'][0]['base']
            if v is None or v <= 0:
                status, w2 = 'equity_not_positive', W_MAX
            else:
                e = v * shares
                w2 = min(W_MAX, net_debt / (net_debt + e))
                status = 'capped' if w2 >= W_MAX - 1e-12 else 'converged'
            if abs(w2 - w) > 1e-9:
                w, changed = w2, True
                ke, ke_t, de = apply(w)
        if not changed:
            break
    else:
        status = 'not_converged'
    prices = {sc['name']: [{'period_end': B[i], 'price': sc['equity_issue_price'][i], 'quote_binding': sc['equity_issue_price'][i] >= quote * (1 - 1e-9)}
                           for i in issued[sc['name']]] for sc in spec['scenarios']}
    return {'ke': ke, 'unlevered': ku, 'table': table, 'ke_terminal': ke_t, 'unlevered_terminal': ku_t, 'table_terminal': table_t,
            'kd': kd_e, 'kd_expected': kd_e, 'kd_promised': kd_p, 'market_debt_to_equity': MARKET_DE,
            'net_debt': net_debt, 'model_equity': e, 'debt_weight': w, 'd_to_e': de, 'iterations': it, 'status': status,
            'issue_prices': prices,
            'basis': 'net debt = debt - marketable investments - cash above the first-period minimum; equity = base value today x '
                     'point-in-time shares (the model\'s own value, not the quote); new shares issued at min(quote, scenario value after the issue)'}


def _cycle_margin(h):
    """Through-the-cycle operating margin = sum(EBIT) / sum(revenue) over the window used for the revenue normalization (last
    ten fiscal years): the cycle's own average EBIT relative to its average revenue. The simple mean of yearly margins drops the
    revenue-margin covariance (operating leverage) and understates mid-cycle EBIT. Trough and peak are yearly margins."""
    rows = [(r['revenue'], _num(r.get('operating_income'))) for _, r in h['rows'][h.get('run_from', 0):][-10:]]
    rows = [(r, e) for r, e in rows if e is not None and r > 0]
    if not rows:
        return None
    ms = [e / r for r, e in rows]
    return {'mid': sum(e for _, e in rows) / sum(r for r, _ in rows), 'trough': min(ms), 'peak': max(ms), 'years': len(rows),
            'simple_mean': sum(ms) / len(ms)}


def _margin_targets(route, h, warn):
    st, (_, typ, ceil, _) = STAGE[route['stage']], route['type']
    m0, best, sp = h['m0'], h['m_best3'], STAGE[route['stage']]['m_spread']
    cm = _cycle_margin(h)
    if route['cycle_path'] and cm:
        if cm['years'] < 5:
            warn('cycle_not_observed', f"only {cm['years']} fiscal years of margins: mid-cycle margin may not span a full cycle", 'high')
        mid, trough, peak = cm['mid'], cm['trough'], cm['peak']
        # the type ceiling caps assumed expansion, never a margin the company has already earned (ASML's 36% peak against the
        # 20% industrial ceiling had collapsed its bull to the base)
        t = {'base': mid, 'bear': mid - max(sp, .5 * (mid - trough)), 'bull': min(max(ceil, peak), mid + max(sp, .5 * (peak - mid)))}
        why = (f"mid-cycle = sum(operating income) / sum(revenue) over {cm['years']} fiscal years {_pct(mid)} (the cycle's own average "
               f"EBIT; the simple mean of yearly margins, {_pct(cm['simple_mean'])}, would drop the operating leverage); around the cycle, "
               f'not at its extremes: bear = mid - max(stage spread, half the distance to the trough {_pct(trough)}), bull = mid + '
               f'max(stage spread, half the distance to the peak {_pct(peak)}), capped at the type ceiling {_pct(ceil)} or the '
               'evidenced peak, whichever is higher')
    elif route['method'] == 'scenario_survival_fcff':
        base = max(typ * .8, min(m0 if m0 is not None else 0, ceil))
        lows = [m for m in h['m_all'][-5:]]
        floor = max(typ * .35, min(lows) - sp) if lows else typ * .35
        t = {'base': base, 'bear': min(base - sp, floor), 'bull': min(ceil, max(typ, base + .5 * sp))}
        why = (f'survival model: base reaches 80% of the type-typical mature margin {_pct(typ)} (or holds today\'s); bear = base - stage '
               f"spread, never below the company's own 5-year low less the spread ({_pct(min(lows) - sp) if lows else 'n/a'}) or 35% of "
               'the typical margin, whichever is higher; bull the typical margin')
    elif m0 is None:
        t = {'base': typ, 'bear': typ - sp, 'bull': min(ceil, typ + .75 * sp)}
        why = f'no current margin; type-typical mature margin {_pct(typ)}'
        warn('margin_missing', 'current operating margin unavailable; mature margin set to the type-typical level', 'high')
    elif m0 >= typ:
        moat, wts = route['moat'], route['moat_weights']
        strong = max(m0, min(ceil, (m0 + (best if best is not None else m0)) / 2))
        k = .5 if moat == 'weak' else .25
        other = m0 - k * (m0 - typ)
        mix = lambda w: other + w * (strong - other)
        base = mix(wts['base'])
        if moat == 'strong':
            why = (f'strong moat: current margin {_pct(m0)} held, recovering half-way to the best of 3 years {_pct(best)} only up to the '
                   f'type ceiling {_pct(ceil)} (the ceiling caps assumed expansion; it never cuts an evidenced margin)')
        elif wts['base'] > 0:
            why = (f"moat proxy weight {wts['base']:.2f}: base between the unassessed-moat rule ({k:.0%} of the excess of {_pct(m0)} over the "
                   f"type-typical {_pct(typ)} competed away = {_pct(other)}) and the strong-moat rule ({_pct(strong)}); the bear keeps the "
                   'unassessed rule (the moat may fail)')
        else:
            why = f'{moat} moat: {k:.0%} of the excess of current margin {_pct(m0)} over the type-typical {_pct(typ)} competed away'
        b_bear = mix(wts['bear'])
        t = {'base': base, 'bear': b_bear - sp, 'bull': max(base, min(max(ceil, best or 0), mix(wts['bull']) + .75 * sp))}
    else:
        base = m0 + st['catch_up'] * (typ - m0)
        if best is not None:
            base = max(base, min(best, typ))
        why = f'below type-typical: base closes {st["catch_up"]:.0%} of the gap from {_pct(m0)} to {_pct(typ)} (not below the best of 3 years {_pct(best)})'
        hist = [m for m in (h.get('m_all') or [])[-10:] if m is not None]
        own = max(m0, sorted(hist)[len(hist) // 2]) if hist else None
        # structurally thin = five profitable years inside a 5-point band (McKesson, Cigna, CVS); a company whose margins swing
        # through losses while it scales its costs (Pinterest: -4% to +13%) is not thin, it keeps the catch-up
        thin = len(hist) >= 5 and min(hist[-5:]) > 0 and max(hist[-5:]) - min(hist[-5:]) <= .05
        if route['stage'] in ('maturing', 'mature', 'decline') and thin and max(hist + [m0]) < typ and base > own:
            # an established company that never earned the type-typical margin in ten years is structurally thin-margin
            # (distributors, managed care, retailers): converging to the typical margin invented 3-8 points of margin for
            # McKesson and Cigna (2026-10). The base holds its own median margin of those years (today's if higher), not its
            # best year (CVS, Tyson: the best year is the bull case); an analyst mature_margin override with evidence lifts it
            why += (f'; held at its own median margin of the last {len(hist)} years {_pct(own)} (best {_pct(max(hist))}): an '
                    'established company that never reached the typical margin is not assumed to (analyst override with evidence lifts it)')
            base = own
        t = {'base': base, 'bear': base - sp, 'bull': min(ceil, base + .75 * sp)}
    if route['cycle_peak'] and cm and not route['cycle_path']:
        # analyst says structural: the bear still tests a return to the cycle's own average margin
        if t['bear'] > cm['mid']:
            why += f"; bear capped at the through-the-cycle margin {_pct(cm['mid'])} (cyclical industry at a margin peak)"
        t['bear'] = min(t['bear'], cm['mid'])
    for s in SCEN:
        if t[s] < MARGIN_FLOOR:
            warn('margin_floor', f'{s} mature margin {_pct(t[s])} raised to the {MARGIN_FLOOR:.0%} floor so a terminal value exists; '
                                 'a business that never earns a margin has no going-concern value (see scenario_survival / risk)', 'high')
            t[s] = MARGIN_FLOOR
    t['bull'] = max(t['bull'], t['base'])
    t['bear'] = min(t['bear'], t['base'])
    return t, why


def _revenue_guidance(fb):
    """Latest dated company revenue guidance per fiscal-year label ('FY2026'), issued on or before as_of."""
    out = {}
    for g in fb.get('guidance', []):
        if g.get('metric') != 'revenue' or not str(g.get('period', '')).startswith('FY') or len(str(g['period'])) != 6:
            continue
        if g.get('issued_at') and _d(g['issued_at']) > _d(fb['as_of']):
            continue
        lo, hi = _num(g.get('low')), _num(g.get('high'))
        if lo is None or hi is None or hi < lo:
            continue
        prev = out.get(g['period'])
        if prev is None or (g.get('issued_at') or '') > (prev.get('issued_at') or ''):
            out[g['period']] = {'value': (lo + hi) / 2, 'low': lo, 'high': hi, 'issued_at': g.get('issued_at'),
                                'source_ids': g.get('source_ids', [])}
    return out


def _current_year(route, h, cal, cons):
    """Current fiscal year revenue from the best dated evidence: consensus, company guidance, reported year to date plus the
    remaining months at the run rate; None when only the trailing anchor is available."""
    k, b1 = cal['fiscal_years_since_last_report'], cal['boundaries'][0]
    gd = h.get('guidance', {}).get(f'FY{b1[:4]}')
    ytd = h['ytd'] if h['ytd'] and k == 1 and _d(h['ytd']['end']) < _d(b1) else None
    if b1 in cons:
        r, kind = cons[b1]['value'], 'analyst'
        basis = f"consensus revenue for the fiscal year ending {b1} ({cons[b1]['source_id']}, {cons[b1].get('as_of')})"
    elif gd:
        r, kind = gd['value'], 'guidance'
        basis = (f"company guidance midpoint for the fiscal year ending {b1} ({gd['low']:,.0f}-{gd['high']:,.0f}, issued "
                 f"{gd.get('issued_at')}; management guidance, not a reported fact)")
    elif ytd:
        rem = years(ytd['end'], b1)
        acq = h.get('acq')
        if ytd['prior'] and 0 < ytd['prior'] < h['last_revenue']:
            left, how = (h['last_revenue'] - ytd['prior']) * ytd['value'] / ytd['prior'], 'the same months a year earlier x year-to-date growth'
            g1, rest = 1 + (acq or {}).get('organic', 0), h['last_revenue'] - ytd['prior']
            if acq and acq.get('pro_forma_last_fy') and acq['pro_forma_last_fy'] > acq['pro_forma_prior']:
                left = (acq['pro_forma_last_fy'] - acq['pro_forma_prior']) * g1
                how = 'the same months a year earlier on the pro forma basis (acquired business included) x (1 + organic growth)'
            elif acq and acq['timing'] == 'current_fiscal_year' and acq.get('acquired_rate'):
                left = rest * g1 + acq['acquired_rate'] * rem
                how = ('the same months a year earlier x (1 + organic growth) + the acquired business at its pre-deal pro forma '
                       'run rate')
            elif acq and acq['timing'] != 'current_fiscal_year':
                left = rest * g1
                how = ('the same months a year earlier x (1 + organic growth): the acquired business is in the prior year from its '
                       'closing; months before the closing are not added (conservative)')
        else:
            left, how = ytd['value'] / years(h['last_end'], ytd['end']) * rem, 'the annualized year-to-date run rate'
        r, kind = ytd['value'] + left, 'model'      # reported year to date plus a modelled remainder
        basis = (f"reported year to date {ytd['value']:,.0f} ({ytd['basis']}) + the remaining {rem:.2f} years to {b1} at {how} "
                 f"({left:,.0f})")
    else:
        return None
    out = {'r_fy': r, 'g_cur': (r / h['last_revenue']) ** (1 / k) - 1, 'basis': basis, 'kind': kind, 'ytd': ytd}
    if ytd and r - ytd['value'] > 0 and years(ytd['end'], b1) > .02:
        out['stub_rate'] = (r - ytd['value']) / years(ytd['end'], b1)
        out['stub_basis'] = f"(current-year revenue - reported year to date) / years from {ytd['end']} to {b1}"
    return out


def _params(route, h, cal, cons, rf, ku, ku_t, ov_ke, debt0, ov, warn):
    """ku: the table cost of equity of the explicit years; ku_t: the terminal's (the mature table, TERMINAL_KE_DROPPED), the
    hurdle the terminal ROIC rules and the franchise durability are measured against; ov_ke: analyst cost-of-equity overrides
    by scenario (the rate of the explicit years and of the terminal alike)."""
    st = STAGE[route['stage']]
    s2c_range = route['type'][0]
    cyc = route['cycle_path'] and h.get('cycle')
    if route['stage'] == 'decline':
        gT_base = _clamp(.5 * h['g_trailing'], DECLINE_FLOOR, 0.)
        gT = {'base': gT_base, 'bear': max(DECLINE_FLOOR, gT_base - .01), 'bull': min(gT_base + .02, rf)}
    else:
        # the implied ERP's own convention (TERMINAL_BEAR_GAP comment), within the valuation contract's bound; bear never above base
        gT_base = min(rf, TERMINAL_GROWTH_MAX)
        gT = {'base': gT_base, 'bull': gT_base, 'bear': min(gT_base, max(0., gT_base - TERMINAL_BEAR_GAP))}
    k = cal['fiscal_years_since_last_report']
    b1 = cal['boundaries'][0]
    n_full = len(cal['boundaries']) - 1
    gen = dict(gT)
    for s in SCEN:
        gT[s] = ov(s, 'terminal_growth', gT[s])
    # flags read the growth actually used (after overrides): an evidenced override replaces the rate-based number and its flag
    capped = [s for s in SCEN if route['stage'] != 'decline' and rf > TERMINAL_GROWTH_MAX and gT[s] == gen[s]]
    if capped:
        warn('terminal_growth_capped', f'risk-free {rf:.2%} is above the valuation contract\'s {TERMINAL_GROWTH_MAX:.0%} bound on '
                                       f'nominal perpetual growth: terminal growth held at {", ".join(f"{s} {_pct(gT[s])}" for s in capped)}, '
                                       'below the rate, so the ERP\'s growth convention no longer holds there and the terminal value is lower '
                                       'than it would give. Check the rate and its currency; value the cash flows in a currency with a '
                                       f'lower rate, or override terminal_growth with evidence (at most {TERMINAL_GROWTH_MAX:.0%})', 'high')
    high = {s: gT[s] for s in SCEN if gT[s] > TERMINAL_HIGH_RF and s not in capped}
    if high:
        warn('terminal_growth_high', f'terminal growth {", ".join(f"{s} {_pct(g)}" for s, g in high.items())} above '
                                     f'{TERMINAL_HIGH_RF:.0%} (risk-free {rf:.2%}): a nominal perpetual growth this high only holds with '
                                     'matching inflation in the reporting currency forever (check the rate, its currency and the '
                                     'cash-flow currency; an evidenced terminal_growth override replaces it)', 'medium')
    hl = ov('base', 'growth_half_life', st['half_life'])
    cur = _current_year(route, h, cal, cons)
    level, e0, dev0 = None, 0., 1.
    if cyc:
        trend_at = lambda d: cyc['trend_level'] * (1 + cyc['trend_growth']) ** years(cyc['trend_end'], d)
        trend_b1 = trend_at(b1)
    if cur:
        r_fy, g_cur, cur_basis, cur_kind = cur['r_fy'], cur['g_cur'], cur['basis'], cur['kind']
        level = r_fy
        dev0 = r_fy / trend_b1 if cyc else 1.
    elif cyc:
        newer = h['ttm']['period_end'] and _d(h['ttm']['period_end']) > _d(h['last_end']) and h['ttm']['revenue']
        level, l_end = (h['ttm']['revenue'], h['ttm']['period_end']) if newer else (h['last_revenue'], h['last_end'])
        e0 = years(l_end, b1)
        w0 = min(1., e0 / CYCLE_CONV['base'])
        # today's distance from the trend (a ratio) closes geometrically; holding today's level flat while the trend grows
        # would add a catch-up jump on top of the trend growth for a fast-trending cyclical
        dev0 = level / trend_at(l_end)
        r_fy = trend_b1 * dev0 ** (1 - w0)
        g_cur, cur_kind = (r_fy / h['last_revenue']) ** (1 / k) - 1, 'model'
        cur_basis = (f"cyclical: today's level {level:,.0f} (to {l_end}, {dev0:.2f} x its trend) closing {w0:.0%} of that distance (in log terms) to the mid-cycle trend {trend_b1:,.0f} "
                     f"by {b1} (reversion over {CYCLE_CONV['base']} years; never the peak grown at a trough-to-peak growth rate)")
    else:
        g_cur, cur_kind = _clamp(h['g_trailing'], -.5, 2.), 'model'
        newer = h['ttm']['method'] in ('quarters', 'annual_plus_ytd') and h['ttm']['period_end'] and _d(h['ttm']['period_end']) > _d(h['last_end'])
        if newer:
            r_fy = h['ttm']['revenue'] * (1 + g_cur) ** years(h['ttm']['period_end'], b1)
            cur_basis = f"TTM revenue to {h['ttm']['period_end']} grown at the trailing anchor to {b1}"
        else:
            r_fy = h['last_revenue'] * (1 + g_cur) ** years(h['last_end'], b1)
            cur_basis = f"last reported fiscal-year revenue ({h['last_end']}) grown at the trailing anchor to {b1}"
    if cur and cur.get('stub_rate'):
        stub_rate, stub_basis = cur['stub_rate'], cur['stub_basis']
    else:
        stub_rate = r_fy * (1 + g_cur) ** (.5 - cal['stub_years'] / 2)
        stub_basis = 'current-year revenue x (1+g)^(0.5 - stub/2) (late-year run rate)'
    # consensus growth for the first two full years
    cg, lows, highs = [], [], []
    prev = r_fy
    for b in cal['boundaries'][1:3]:
        if b not in cons:
            break
        e = cons[b]
        cg.append(e['value'] / prev - 1)
        lows.append(_num(e.get('low')) / prev - 1 if _num(e.get('low')) else None)
        highs.append(_num(e.get('high')) / prev - 1 if _num(e.get('high')) else None)
        prev = e['value']
    half = (highs[0] - lows[0]) / 2 if cg and lows[0] is not None and highs[0] is not None else None
    spread = min(.15, max(st['g_spread'], h['growth_volatility'] or 0, half or 0))
    cyc_meta, weights = None, {}
    if cyc:
        g_long = {'base': ov('base', 'growth_anchor', cyc['trend_growth'])}
        g_long['bear'] = ov('bear', 'growth_anchor', g_long['base'] - .01)
        g_long['bull'] = ov('bull', 'growth_anchor', g_long['base'] + .01)
        factor = {'base': 1., 'bear': min(1., cyc['trough_ratio']) ** .5, 'bull': max(1., cyc['peak_ratio']) ** .5}
        rates = {}
        for s in SCEN:
            hs = hl * {'base': 1., 'bear': .8, 'bull': 1.3}[s]
            gt, trend, r = _fade(g_long[s], gT[s], hs, n_full), trend_b1, [r_fy]
            weights[s] = [min(1., e0 / CYCLE_CONV['base'])]
            for t in range(1, n_full + 1):
                trend *= 1 + gt[t - 1]
                w = min(1., (e0 + t) / CYCLE_CONV[s])
                weights[s].append(w)
                r.append(trend * dev0 ** (1 - w) * factor[s] ** w)
            rates[s] = r
        rates['bear'] = [min(x, y * (1 - 1e-3)) if i else x for i, (x, y) in enumerate(zip(rates['bear'], rates['base']))]
        rates['bull'] = [max(x, y * (1 + 1e-3)) if i else x for i, (x, y) in enumerate(zip(rates['bull'], rates['base']))]
        paths = {s: [b / a - 1 for a, b in zip(rates[s], rates[s][1:])] for s in SCEN}
        g_kind = 'model'
        cyc_meta = dict(cyc, trend_at_current_fy=trend_b1, level=level, distance_from_trend=dev0, elapsed_at_current_fy=e0, long_run_growth=g_long,
                        level_factor=factor, conv=CYCLE_CONV)
    else:
        # bought revenue is a level shift in the current year, not growth to repeat: the fade starts from organic growth. A
        # cyclical business's change this year is the cycle (prices and volumes), not a trend: it starts from its long-run
        # like-for-like growth (Expand Energy 2026: +25% on gas prices had been faded as if it were demand). Not when the card
        # reads the history as a scale-up, a loss-making grower or pre-commercial noise (cycle_history) AND the CAGR's own fiscal
        # years have that shape (history_shape over them): the industry word is a flag there and that CAGR is the ramp from a small
        # base, not a like-for-like cycle average. The card's label covers its whole window: Tesla's 2010-2011 pre-commercial years
        # label all 15 years, while its 2018-2023 CAGR (the 2024 walk-forward formation) is commercial and keeps the rule
        g0 = h['acq']['organic'] if h.get('acq') else g_cur
        lr, n = next(((x, n) for x, n in ((h.get('cagr_5y_ex_acq'), 5), (h.get('cagr_5y'), 5), (h.get('cagr_3y'), 3)) if x is not None),
                     (None, 0))
        ramp = (bool(route.get('cycle_history')) and lr is not None
                and history_shape(h['revenue'][-n - 1:], h['margins'][-n - 1:]) is not None)
        if route['cyclical'] and not ramp and not h.get('acq') and not cg and lr is not None and abs(g0 - lr) > .02:
            warn('cyclical_growth_start', f"cyclical business: next-year growth fades from its long-run like-for-like growth {_pct(lr)} "
                                          f"(5-year CAGR without acquisition steps, else 3-year), not this year's {_pct(g0)} (the cycle, not a trend)", 'medium')
            g0 = h['cyclical_start'] = lr
        start = _fade(g0, gT['base'], hl, 1)[0]
        cap = (h.get('g_scale_cap') or {}).get('cap')
        if not cg and cap is not None and start > cap:
            # the revenue-scale base rate bounds the first forecast year too, not only the trailing anchor: fading a year-to-date
            # surge (NVIDIA fiscal 2027: +96%) started a $400bn+ business at +73% a year without evidence for it
            warn('growth_start_capped', f'next-year growth faded from the current year ({_pct(g_cur)}) would be {_pct(start)}, above the '
                                        f'revenue-scale base rate {_pct(cap)}; capped (consensus or company guidance replaces it, an analyst '
                                        'override with evidence can lift it)', 'medium')
            start = cap
        anchor = ov('base', 'growth_anchor', cg[0] if cg else start)
        if cg and anchor == cg[0]:
            base, g_kind = cg + _fade(cg[-1], gT['base'], hl, n_full - len(cg)), 'analyst'
        else:
            base, g_kind = [anchor] + _fade(anchor, gT['base'], hl, n_full - 1), 'model'
        b0 = base[0]
        bear0 = min(b0 - spread, b0 / 2) if b0 > 0 else b0 - spread
        if lows and lows[0] is not None:
            bear0 = min(bear0, lows[0])
        bull0 = b0 + spread
        if highs and highs[0] is not None:
            bull0 = min(max(bull0, highs[0]), b0 + 2 * spread)
        bear0, bull0 = ov('bear', 'growth_anchor', bear0), ov('bull', 'growth_anchor', bull0)
        hb, hu = ov('bear', 'growth_half_life', .8 * st['half_life']), ov('bull', 'growth_half_life', 1.3 * st['half_life'])
        bear = [bear0] + _fade(bear0, gT['bear'], hb, n_full - 1)
        bull = [bull0] + _fade(bull0, gT['bull'], hu, n_full - 1)
        bear = [min(x, y - .005) for x, y in zip(bear, base)]
        bull = [max(x, y + .005) for x, y in zip(bull, base)]
        paths = {'bear': bear, 'base': base, 'bull': bull}
    margins, m_why = _margin_targets(route, h, warn)
    (lo, hi) = s2c_range
    s2c_h = h['s2c']
    if (s2c_h is None or s2c_h <= 0) and h['capital_light']:
        s2c = hi
        warn('sales_to_capital_capped', f'revenue grew while invested capital did not (capital-light history); type ceiling {hi:.2f} used so growth still costs capital', 'low')
    elif s2c_h is None or s2c_h <= 0:
        s2c = (lo + hi) / 2
        warn('sales_to_capital_default', f'incremental sales-to-capital not meaningful in the history ({s2c_h}); type midpoint {s2c:.2f} used', 'medium')
    elif s2c_h > hi:
        s2c = hi
        warn('sales_to_capital_capped', f'historical incremental sales-to-capital {s2c_h:.2f} above the type range; capped at {hi:.2f} so growth still costs capital', 'low')
    elif s2c_h < lo and (h.get('s2c_5y') or 0) > s2c_h:
        # a 3-year plateau with capital still rising (Tesla 2023-25: 0.62) would charge every new dollar of revenue the
        # stagnation's capital; the 5-year ratio spans the build-out and its revenue
        s2c = min(max(h['s2c_5y'], .5 * lo), hi)
        warn('sales_to_capital_long_window', f'3-year incremental sales-to-capital {s2c_h:.2f} is below the type range ({lo}-{hi}) '
                                             f"while the 5-year ratio is {h['s2c_5y']:.2f}: the 3-year plateau is read as noise; "
                                             f'used {s2c:.2f}', 'medium')
    elif s2c_h < lo:
        s2c = max(s2c_h, .5 * lo)
        warn('sales_to_capital_low', f'historical incremental sales-to-capital {s2c_h:.2f} below the type range ({lo}-{hi}); the business is more capital-hungry than the type; used {s2c:.2f}', 'medium')
    else:
        s2c = s2c_h
    s2c_s = {'base': ov('base', 'sales_to_capital', s2c), 'bear': ov('bear', 'sales_to_capital', .8 * s2c),
             'bull': ov('bull', 'sales_to_capital', 1.2 * s2c)}
    tax_eff = h['tax_effective']
    tax = _clamp(tax_eff, .15, .25) if tax_eff is not None else .21
    if tax_eff is not None and tax != tax_eff:
        warn('tax_normalized', f'effective tax {tax_eff:.1%} outside 15-25%; normalized to {tax:.1%} (override with evidence)', 'low')
    r_raw = h['interest_expense'] / debt0 if h['interest_expense'] and debt0 > 0 else None
    r0 = _clamp(r_raw, .01, .15) if r_raw is not None else rf + st['debt_spread']
    if r_raw is not None and not DEBT_RATE_RANGE[0] <= r_raw <= rf + DEBT_RATE_RANGE[1]:
        warn('debt_rate_unusual', f'interest expense / debt {r_raw:.2%} is outside {DEBT_RATE_RANGE[0]:.1%}..risk-free + {DEBT_RATE_RANGE[1]:.0%}: '
                                  'check the debt and interest data (it sets interest and the tax shield; the cost of equity uses the '
                                  'expected return on debt, never the coupon)', 'medium')
    # a non-cyclical franchise earns a persistence lift from its realized record (durable ROIC and durable margins); a cyclical
    # keeps the floor (its return is the cycle's, assessed through the mid-cycle margin); computed once, the same across scenarios
    dur, dur_meta = _franchise_durability(h, ku_t, route)
    persist = {s: EXCESS_RETURN_PERSISTENCE[s] + (EXCESS_RETURN_PERSISTENCE_CEIL[s] - EXCESS_RETURN_PERSISTENCE[s]) * dur
               for s in EXCESS_RETURN_PERSISTENCE}
    out = {}
    for s in SCEN:
        k_u = ov(s, 'cost_of_equity', ku)
        k_t = ov_ke.get(s, ku_t)      # an analyst override is the terminal's rate too
        tx = ov(s, 'tax_rate', tax)
        w0 = max(k_t, gT[s] + .03)    # mature table cost of capital: the hurdle terminal excess returns fade toward (leverage-free)
        roic_s = min(max(h['roic'] or 0, w0 + .05), .40)
        strong = {'base': roic_s, 'bear': max(w0 + .02, .7 * roic_s), 'bull': min(.45, 1.2 * roic_s)}[s]
        other = {'base': w0 + .02, 'bear': w0 + .01, 'bull': w0 + .04}[s]
        dem = _demonstrated_roic(h)       # lower of latest and average (post-breakeven average on a scale-up path)
        if s in EXCESS_RETURN_PERSISTENCE and route['moat'] != 'weak' and dem is not None and dem > w0:
            other = max(other, w0 + persist[s] * (min(dem, .40) - w0))
        wt = route['moat_weights'][s]
        roic = other + wt * (strong - other)        # the moat proxy weight blends the two rules (bear: unassessed rules)
        roic = ov(s, 'terminal_roic', roic)
        if roic < gT[s]:
            raise ValueError(f'{s}: terminal ROIC below terminal growth')
        mm = ov(s, 'mature_margin', margins[s])
        out[s] = {'growth': [_clamp(x, -.5, 2.) for x in paths[s]], 'g_terminal': gT[s], 'mature_margin': mm,
                  'margin_final': mm != margins[s],      # an analyst override is the final EBIT margin (no D&A roll-off added)
                  's2c': s2c_s[s], 's2c_terminal': roic / max(mm * (1 - tx), 1e-6), 'ke': k_u, 'ke_terminal': k_t, 'tax': tx, 'wacc': w0,
                  'hurdle': w0,
                  'roic': roic, 'cycle_weights': weights.get(s), 'roic_persistence': persist.get(s),
                  'conv': CYCLE_CONV[s] if cyc else (max(2, round(.75 * st['conv'])) if s == 'bear' else st['conv'])}
    meta = {'r_fy': r_fy, 'g_cur': g_cur, 'cur_basis': cur_basis, 'cur_kind': cur_kind, 'ytd': cur['ytd'] if cur else None,
            'stub_rate': stub_rate, 'stub_basis': stub_basis, 'consensus_growth': cg,
            'consensus_low': lows, 'consensus_high': highs, 'growth_kind': g_kind, 'spread': spread, 'half_life': hl,
            'margin_why': m_why, 's2c_history': s2c_h, 's2c_used': s2c, 's2c_range': s2c_range, 'tax_effective': tax_eff,
            'tax_used': tax, 'interest_existing': r0, 'g_terminal': gT, 'cycle': cyc_meta,
            'roic_durability': dur_meta, 'roic_persistence': persist}
    return out, meta


def _paths(P, meta, h, route, cal, open_bal, inv_income):
    """Fiscal-year paths. Depreciation converges to maintenance capex and the capital needed per unit of new revenue converges
    to the terminal g / ROIC rule by the last explicit year, so the last explicit year and the terminal value describe the same
    steady state. Growth capex closes the reinvestment identity per period on the bases drivers.build_case uses:
    growth capex + change in working capital - change in deferred revenue = change in revenue x capital per unit of revenue.
    A declining business (decline stage or negative terminal growth) releases capital in the explicit years too (maintenance
    capex below depreciation), converging to the terminal g / ROIC release, so the terminal is not a step up in cash flow.
    D&A is non-cash: as it converges to maintenance capex the EBITDA margin is held and EBIT rises, except for the part of the
    roll-off that recurring acquisition spend (3-year history) replaces, which stays a cash cost."""
    st = STAGE[route['stage']]
    B = cal['boundaries']
    n = len(B)
    m0 = h['m0'] if h['m0'] is not None else P['base']['mature_margin']
    sbc0, dep0, cap = h['sbc_pct'], h['da_pct'], h['capex_pct']
    maint = min(cap, dep0)
    wc = _clamp(h['wc_ratio'] if h['wc_ratio'] is not None else .05, -.3, .5)
    dr = _clamp(open_bal['deferred_revenue'] / meta['stub_rate'], 0, 1.5) if meta['stub_rate'] else 0.
    prepaid = min(1., 2 * dr) if dr > 0 else 0.
    dur = [cal['stub_years']] + [years(a, b) for a, b in zip(B, B[1:])]
    conv_w = [t / (n - 1) if n > 1 else 1. for t in range(n)]          # 0 today -> 1 in the last explicit year
    dep = [dep0 + (maint - dep0) * w for w in conv_w]
    roll = dep0 - maint
    acq_share = _clamp((h['acq_pct'] or 0) / roll, 0., 1.) if roll > 1e-9 else 0.
    out, ident = {}, {}
    for s in SCEN:
        p = P[s]
        g = p['growth']
        rates = [meta['stub_rate']]
        prev = meta['r_fy']
        for x in g:
            prev *= 1 + x
            rates.append(prev)
        mt, conv = p['mature_margin'], p['conv']
        if p.get('cycle_weights'):
            margin = [m0 + (mt - m0) * w for w in p['cycle_weights']]
        else:
            margin = [m0] + [mt + (m0 - mt) * max(0., 1 - t / conv) for t in range(1, n)]
        sbc = [sbc0] + [sbc0 * (1 - st['sbc_fade'] * min(1., t / st['conv'])) for t in range(1, n)]
        # EBIT path: the margin target (measured with today's D&A load) plus the non-cash D&A roll-off not replaced by acquisitions
        if not p.get('margin_final'):
            margin = [m + (dep0 - d) * (1 - acq_share) for m, d in zip(margin, dep)]
        ccr = [1 - m - x - d for m, x, d in zip(margin, sbc, dep)]
        if any(c < 0 or c > 2 for c in ccr):
            raise ValueError('cash-cost ratio outside driver bounds (operating losses above 100% of revenue or margins above 100%)')
        p['s2c_terminal'] = p['roic'] / max(margin[-1] * (1 - p['tax']), 1e-6)     # terminal rule on the final EBIT margin
        capint = [1 / p['s2c'] + (1 / p['s2c_terminal'] - 1 / p['s2c']) * w for w in conv_w]
        fy_rate = [meta['r_fy']] + rates[1:]
        release = route['stage'] == 'decline' or p['g_terminal'] < 0
        gc, mcx, need = _reinvest(fy_rate, rates, dur, capint, wc, dr, maint, release,
                                  d_rev0=meta['r_fy'] - meta['r_fy'] / (1 + meta['g_cur']))
        if release and p['g_terminal'] < 0 and n > 1:
            # the terminal never releases capital faster than the last explicit year can (capex is not negative): terminal ROIC
            # is raised until g / ROIC equals the last year's executed release per unit of NOPAT
            t = n - 1
            d_rev = fy_rate[t] - fy_rate[t - 1]
            net = (mcx[t] + gc[t] - dep[t]) * rates[t] * dur[t] + wc * d_rev - dr * (rates[t] - rates[t - 1])
            nopat = margin[t] * (1 - p['tax']) * rates[t] * dur[t]
            if nopat > 0 and net < 0 and g[-1] < 0 and g[-1] * nopat / net > p['roic']:
                # at most 100% (the valuation's bound): a near-zero last-year release had asked BioCryst's bear for 167%
                p['roic'], p['release_bounded'] = min(1., g[-1] * nopat / net), True
                p['s2c_terminal'] = p['roic'] / max(margin[-1] * (1 - p['tax']), 1e-6)
        months = P[s].get('cash_months', st['months'])
        min_cash = [max(0., min(months / 12 * c * r, open_bal['cash'])) for c, r in zip(ccr, rates)]
        out[s] = {'growth': [_r(x) for x in g], 'rates': rates, 'operating_margin': [_r(x) for x in margin],
                  'sbc_pct': [_r(x) for x in sbc], 'depreciation_pct': [_r(x) for x in dep], 'cash_cost_ratio': [_r(x) for x in ccr],
                  'maintenance_capex_pct': [_r(x) for x in mcx], 'growth_capex_pct': [_r(x) for x in gc],
                  'tax_rate': [_r(p['tax'])] * n, 'interest_rate': [_r(meta['interest_existing'])] * n,
                  'deferred_revenue_ratio': [_r(dr)] * n, 'working_capital_ratio': [_r(wc)] * n, 'prepaid_share': [_r(prepaid)] * n,
                  'nol_usage_limit': [.8] * n, 'payout_fraction': [0.] * n,
                  'amortization': [0.] * n, 'minimum_cash': [float(round(x)) for x in min_cash],
                  'investment_income_after_tax': [float(round(inv_income * (1 - p['tax'])))] * n,
                  'debt_draw': [0.] * n, 'debt_repayment': [0.] * n, 'fixed_cash_costs': [0.] * n}
        ident[s] = {'net_reinvestment_target': need, 'capital_per_revenue': capint, 'release': release}
    return out, {'wc': wc, 'dr': dr, 'prepaid': prepaid, 'maint': maint, 'dep': dep0, 'dep_path': dep, 'm0': m0, 'sbc0': sbc0,
                 'identity': ident, 'acq_share': acq_share, 'roll': roll}


# ---------------------------------------------------------------- execution helpers

def _run(spec):
    try:
        return value_company(build_case(spec)), None
    except ValueError as e:
        return None, str(e)


def _scen(spec, name):
    return next(s for s in spec['scenarios'] if s['name'] == name)


def _fund(spec, policy, allow, r_new, r0, debt0, locked, inv0):
    """Explicit funding, never silent, cheapest source first, per period with a cash shortfall:

    1. marketable investments are liquidated (their after-tax income falls with the balance);
    2. the period's cash-equivalent SBC charge is settled in new shares (dilution at the quote, never debt);
    3. ASSUMED new debt, only up to DEBT_CAPACITY x EBITDA before SBC (none while EBITDA is not positive);
    4. an ASSUMED equity raise at the quote (EQUITY_FEE fees) for the rest, so dilution is priced into per-share values.
    Distributions start once free cash flow (before new external money and SBC settlement) stays positive; a later
    shortfall pauses the prior distribution instead. A scenario that keeps a gap, or still needs external money in its
    last explicit year (the terminal value would assume a self-funding business the explicit path never reaches), is
    reverted to unfunded: value_company then withholds its values."""
    n = len(spec['boundaries'])
    lock = lambda nm, k: k in locked.get(nm, ())
    info = {sc['name']: {'draws': [0.] * n, 'equity': [0.] * n, 'liquidation': [0.] * n, 'settled': [0.] * n, 'retained': []}
            for sc in spec['scenarios']}
    inc0 = {sc['name']: list(sc['investment_income_after_tax']) for sc in spec['scenarios']}
    for sc in spec['scenarios']:
        if not lock(sc['name'], 'payout_fraction'):
            sc['payout_fraction'] = [0.] * n
        sc.setdefault('equity_issue_price', spec['quote']['price'])
        sc.setdefault('equity_fee_rate', EQUITY_FEE)

    def loop(step):
        for _ in range(4 * n + 20):
            res, err = _run(spec)
            if err:
                return err
            changed = False
            for sc, rs in zip(spec['scenarios'], res['scenarios']):
                j = next((i for i, row in enumerate(rs['forecast']) if row['funding_gap'] > 1e-6), None)
                if j is not None and step(sc, sc['name'], j, rs['forecast'][j]['funding_gap'], rs['forecast']):
                    changed = True
            if not changed:
                return None
        return None

    def fund(sc, nm, j, gap, rows):
        I, rem, changed = info[nm], gap * 1.0001 + 1, False
        left = inv0 - sum(I['liquidation'])
        if left > 1 and not lock(nm, 'investment_liquidation'):
            x = min(left, rem)
            I['liquidation'][j] += x
            rem, changed = rem - x, True
            bal, inc = inv0, []
            for i in range(n):
                bal -= I['liquidation'][i]
                inc.append(float(round(inc0[nm][i] * max(0., bal) / inv0)))
            sc['investment_liquidation'] = list(I['liquidation'])
            if not lock(nm, 'investment_income_after_tax'):
                sc['investment_income_after_tax'] = inc
        room = (sc['sbc_pct'][j] * rows[j]['revenue'] - I['settled'][j]) * (1 - 1e-9)
        if rem > 0 and room > 1 and not lock(nm, 'sbc_share_settlement'):
            x = min(room, rem)
            I['settled'][j] += x
            rem, changed = rem - x, True
            sc['sbc_share_settlement'] = list(I['settled'])
        if rem <= 0 or not allow:
            return changed
        dur = years(rows[j]['start'], rows[j]['end'])
        head = DEBT_CAPACITY * max(0., rows[j]['ebitda_before_sbc'] / dur) - rows[j]['debt']
        if head > 1 and not lock(nm, 'debt_draw'):
            x = float(math.ceil(min(head, rem * 1.1)))
            I['draws'][j] += x
            rem, changed = rem - x, True
            cum, rates = 0., []
            for d in I['draws']:
                cum += d
                rates.append(_r((debt0 * r0 + cum * r_new) / (debt0 + cum)) if debt0 + cum > 0 else _r(r0))
            sc['debt_draw'], sc['financing_status'] = list(I['draws']), 'assumed'
            if not lock(nm, 'interest_rate'):
                sc['interest_rate'] = rates
        if rem > 0 and not lock(nm, 'equity_proceeds'):
            I['equity'][j] += float(math.ceil(rem * 1.1 / (1 - sc['equity_fee_rate'])))
            sc['equity_proceeds'], sc['financing_status'], changed = list(I['equity']), 'assumed', True
        return changed

    def retain(sc, nm, j, gap, rows):
        if j == 0 or sc['payout_fraction'][j - 1] <= 0 or lock(nm, 'payout_fraction'):
            return False
        sc['payout_fraction'][j - 1] = 0.
        info[nm]['retained'].append(spec['boundaries'][j - 1])
        return True

    err = loop(fund)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        f = [row['fcfe_before_new_equity'] - d - x for row, d, x in zip(rs['forecast'], I['draws'], I['settled'])]
        k = next(i for i in range(n + 1) if all(x > 0 for x in f[i:]))
        I['payout_starts'] = spec['boundaries'][k] if k < n else None
        if not lock(nm, 'payout_fraction'):
            sc['payout_fraction'] = [0.] * k + [policy[nm]] * (n - k)
    err = loop(retain)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    reverted = False
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        ext = sum(I['draws']) + sum(I['equity'])
        if ext and (gap or I['draws'][-1] > 0 or I['equity'][-1] > 0):
            I['reverted'] = (f'assumed external financing of {ext:,.0f} did not converge (a shortfall remains or the plan still needs new '
                             'money in its last explicit year, so the terminal value would assume a self-funding business the explicit '
                             'path never reaches); values are withheld instead of assuming free capital')
            sc['debt_draw'], sc['equity_proceeds'], sc['financing_status'] = [0.] * n, [0.] * n, 'none'
            if not lock(nm, 'interest_rate'):
                sc['interest_rate'] = [_r(r0)] * n
            if not lock(nm, 'payout_fraction'):
                sc['payout_fraction'] = [0.] * n
            I.update(draws=[0.] * n, equity=[0.] * n, retained=[], payout_starts=None)
            reverted = True
    if reverted:
        res, err = _run(spec)
        if err:
            return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        debt, eq = sum(I['draws']), sum(I['equity'])
        B = spec['boundaries']
        I.update(total_assumed=debt + eq, debt_assumed=debt, equity_raised=eq, investments_liquidated=sum(I['liquidation']),
                 sbc_settled_in_shares=sum(I['settled']),
                 new_financing_shares=sum(r['new_financing_shares'] for r in rs['forecast']),
                 sbc_settlement_shares=sum(r.get('sbc_settlement_shares', 0) for r in rs['forecast']),
                 status='unfunded' if gap else ('assumed_external_financing' if debt + eq else 'self_funded'),
                 draws=[{'period_end': b, 'amount': x} for b, x in zip(B, I['draws']) if x],
                 equity=[{'period_end': b, 'amount': x} for b, x in zip(B, I['equity']) if x],
                 liquidation=[{'period_end': b, 'amount': x} for b, x in zip(B, I['liquidation']) if x],
                 settled=[{'period_end': b, 'amount': x} for b, x in zip(B, I['settled']) if x],
                 first_gap=next((row['end'] for row in rs['forecast'] if row['funding_gap'] > 1e-6), None))
        sc.setdefault('financing_status', 'none')
    return {'info': info, 'result': res}


# ---------------------------------------------------------------- ledger, sources, underwriting

def _case_sources(fb, ids, extra):
    """FactBook sources -> case sources. Kind: KIND_MAP; issuer documents (and legacy 'document' with a filing/release
    dataset) take their dataset, primary_document its bundle kind (regulator, official_statistics). Retrieval: the source's
    own when it is a valuation RETRIEVAL (any kind), else the kind's default. published_at: the document date when the
    FactBook has one, else the first availability of its facts flagged published_at_estimated."""
    cutoff = fb['as_of']
    avail = {}
    for f in fb['facts']:
        if facts.usable(fb, f):
            avail[f['source_id']] = max(avail.get(f['source_id'], '0000-00-00'), f['available_at'][:10])
    for e in fb.get('estimates') or []:
        if (e.get('as_of') or cutoff) <= cutoff:
            avail[e['source_id']] = max(avail.get(e['source_id'], '0000-00-00'), (e.get('as_of') or cutoff)[:10])
    price = (fb.get('market') or {}).get('price') or {}
    if price.get('source_id'):
        avail[price['source_id']] = max(avail.get(price['source_id'], '0000-00-00'), price['as_of'][:10])
    out = []
    for sid in sorted(ids):
        if sid in extra:
            out.append(extra[sid])
            continue
        s = fb['sources'][sid]
        kind, retrieval = KIND_MAP.get(s['kind'], (s['kind'], None))
        if s['kind'] == 'issuer_document' or (s['kind'] == 'document' and s.get('dataset') in ISSUER_DATASETS):
            kind, retrieval = ISSUER_DATASETS.get(s.get('dataset'), 'document'), 'search_excerpt'
        elif s['kind'] == 'primary_document':
            kind, retrieval = s['dataset'], 'search_excerpt'      # regulator / official_statistics: the bundle kind
        if s.get('retrieval') in RETRIEVALS:
            retrieval = s['retrieval']                              # how the content was actually read, for every kind
        when = avail.get(sid, cutoff)
        pub = str(s['published_at'])[:10] if s.get('published_at') else None
        row = {'id': sid, 'title': f"{s['provider']} {s['dataset']}" + (f" ({s['note']})" if s.get('note') else ''),
               'url': s.get('url') or f"connector://{s['provider']}/{s['dataset']}", 'kind': kind,
               'published_at': pub or when, 'available_at': max(when, pub) if pub else when}
        if not pub:
            row['published_at_estimated'] = True                    # no document date: the first availability of its facts
        if retrieval:
            row['retrieval'] = retrieval
        out.append(row)
    return out


def build(fb, an, card, qa, inputs=None):
    """FactBook + analysis + model card + dataqa -> bear/base/bull driver spec with an assumption ledger.

    Returns {'status': built|method_not_supported_by_forecast|insufficient_data|invalid_spec, 'spec', 'spec_sha256',
    'method', 'assumptions', 'anchors', 'paths', 'cost_of_equity', 'funding', 'overrides', 'preview', 'warnings', 'notes',
    'reason'}. Unknown top-level input keys are ignored with a warning and a note (never silently); unknown keys inside a
    known input are rejected. The funding add-on to the cost of equity comes from the executed base funding result: when
    it disagrees with the card's funding label the forecast is rebuilt once with the executed answer."""
    if inputs is not None and not isinstance(inputs, dict):
        raise ValueError('forecast inputs must be an object')
    inputs = deepcopy(inputs or {})
    ignored = sorted(set(inputs) - set(KNOWN_INPUTS))
    if inputs.get('financing', 'assumed') not in FINANCING_MODES:
        raise ValueError(f"financing must be one of {', '.join(FINANCING_MODES)}")
    if inputs.get('horizon_years') is not None and (isinstance(inputs['horizon_years'], bool) or not isinstance(inputs['horizon_years'], int)
                                                    or not 1 <= inputs['horizon_years'] <= 30):
        raise ValueError('horizon_years must be an integer from 1 to 30')
    cy = inputs.get('cyclical')
    if cy is not None:
        if not isinstance(cy, dict) or sorted(set(cy) - {'treatment', 'reason', 'evidence_ids'}):
            raise ValueError('cyclical: an object with treatment, reason and evidence_ids')
        if cy.get('treatment') not in CYCLE_TREATMENTS:
            raise ValueError(f"cyclical.treatment must be one of {', '.join(CYCLE_TREATMENTS)}")
        if not isinstance(cy.get('reason'), str) or not cy['reason'].strip():
            raise ValueError('cyclical: a written reason is required')
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        ev = cy.get('evidence_ids')
        if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
            raise ValueError('cyclical: evidence_ids must be FactBook source or qualitative ids')
    nr = inputs.get('nonrecurring')
    if nr is not None:
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        if not isinstance(nr, list):
            raise ValueError('nonrecurring: a list of items')
        for i, it in enumerate(nr):
            if not isinstance(it, dict) or sorted(set(it) - {'kind', 'period_end', 'revenue', 'operating_income', 'reason', 'evidence_ids'}):
                raise ValueError(f'nonrecurring {i}: kind, period_end, revenue, operating_income, reason, evidence_ids')
            if it.get('kind') not in NONRECURRING_KINDS:
                raise ValueError(f"nonrecurring {i}: kind must be one of {', '.join(NONRECURRING_KINDS)} (recurring costs are never removed)")
            try:
                _d(it.get('period_end'))
            except (TypeError, ValueError):
                raise ValueError(f'nonrecurring {i}: period_end must be a date') from None
            if any(it.get(k) is not None and _num(it[k]) is None for k in ('revenue', 'operating_income')) or \
                    not any(_num(it.get(k)) for k in ('revenue', 'operating_income')):
                raise ValueError(f'nonrecurring {i}: revenue and/or operating_income effect as numbers')
            if not isinstance(it.get('reason'), str) or not it['reason'].strip():
                raise ValueError(f'nonrecurring {i}: a written reason is required')
            ev = it.get('evidence_ids')
            if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
                raise ValueError(f'nonrecurring {i}: evidence_ids must be FactBook source or qualitative ids')
    dl = inputs.get('debt_like_claims')
    if dl is not None:
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        if not isinstance(dl, list):
            raise ValueError('debt_like_claims: a list of items')
        for i, it in enumerate(dl):
            if not isinstance(it, dict) or sorted(set(it) - {'kind', 'amount', 'reason', 'evidence_ids'}):
                raise ValueError(f'debt_like_claims {i}: kind, amount, reason, evidence_ids')
            if it.get('kind') not in DEBT_LIKE_KINDS:
                raise ValueError(f"debt_like_claims {i}: kind must be one of {', '.join(DEBT_LIKE_KINDS)}")
            if _num(it.get('amount')) is None or it['amount'] <= 0:
                raise ValueError(f'debt_like_claims {i}: amount must be a positive number')
            if not isinstance(it.get('reason'), str) or not it['reason'].strip():
                raise ValueError(f'debt_like_claims {i}: a written reason is required')
            ev = it.get('evidence_ids')
            if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
                raise ValueError(f'debt_like_claims {i}: evidence_ids must be FactBook source or qualitative ids')
    na = inputs.get('non_operating_assets')
    if na is not None:
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        if not isinstance(na, list):
            raise ValueError('non_operating_assets: a list of items')
        for i, it in enumerate(na):
            if not isinstance(it, dict) or sorted(set(it) - {'kind', 'fair_value', 'haircut', 'book_value_in_bridge', 'reason', 'evidence_ids'}):
                raise ValueError(f'non_operating_assets {i}: kind, fair_value, haircut, book_value_in_bridge, reason, evidence_ids')
            if it.get('kind') not in NON_OPERATING_KINDS:
                raise ValueError(f"non_operating_assets {i}: kind must be one of {', '.join(NON_OPERATING_KINDS)}")
            if _num(it.get('fair_value')) is None or it['fair_value'] <= 0:
                raise ValueError(f'non_operating_assets {i}: fair_value must be a positive number')
            if _num(it.get('haircut')) is None or not 0 <= it['haircut'] < 1:
                raise ValueError(f'non_operating_assets {i}: haircut must be at least 0 and below 1 (state it; 0 is a choice too)')
            if _num(it.get('book_value_in_bridge')) is None or it['book_value_in_bridge'] < 0:
                raise ValueError(f'non_operating_assets {i}: book_value_in_bridge must be a number >= 0 (what the investments already hold)')
            if not isinstance(it.get('reason'), str) or not it['reason'].strip():
                raise ValueError(f'non_operating_assets {i}: a written reason is required')
            ev = it.get('evidence_ids')
            if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
                raise ValueError(f'non_operating_assets {i}: evidence_ids must be FactBook source or qualitative ids')
    ce = inputs.get('capital_events')
    if ce is not None:
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        if not isinstance(ce, list):
            raise ValueError('capital_events: a list of items')
        for i, it in enumerate(ce):
            if not isinstance(it, dict) or sorted(set(it) - {'kind', 'date', 'cash', 'shares', 'reason', 'evidence_ids'}):
                raise ValueError(f'capital_events {i}: kind, date, cash, shares, reason, evidence_ids')
            if it.get('kind') not in CAPITAL_EVENT_KINDS:
                raise ValueError(f"capital_events {i}: kind must be one of {', '.join(CAPITAL_EVENT_KINDS)}")
            try:
                when = _d(it.get('date'))
            except (TypeError, ValueError):
                raise ValueError(f'capital_events {i}: date must be a date') from None
            if when > _d(fb['as_of']):
                raise ValueError(f'capital_events {i}: dated after as_of (look-ahead)')
            if _num(it.get('cash')) is None or it['cash'] <= 0 or _num(it.get('shares')) is None or it['shares'] <= 0:
                raise ValueError(f'capital_events {i}: cash and shares must be positive numbers (the kind gives the direction)')
            if not isinstance(it.get('reason'), str) or not it['reason'].strip():
                raise ValueError(f'capital_events {i}: a written reason is required')
            ev = it.get('evidence_ids')
            if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
                raise ValueError(f'capital_events {i}: evidence_ids must be FactBook source or qualitative ids')
    of = inputs.get('operating_funding')
    if of is not None:
        if not isinstance(of, dict) or sorted(set(of) - {'amount', 'reason', 'evidence_ids'}):
            raise ValueError('operating_funding: an object with amount, reason and evidence_ids')
        if _num(of.get('amount')) is None or of['amount'] <= 0:
            raise ValueError('operating_funding.amount must be a positive number')
        if not isinstance(of.get('reason'), str) or not of['reason'].strip():
            raise ValueError('operating_funding: a written reason is required')
        known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
        ev = of.get('evidence_ids')
        if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
            raise ValueError('operating_funding: evidence_ids must be FactBook source or qualitative ids')
    clean = {k: v for k, v in inputs.items() if k in KNOWN_INPUTS}
    out = _build(fb, an, card, qa, clean, None)
    if out.get('status') == 'built' and out['_external'] != out['_external_assumed']:
        again = _build(fb, an, card, qa, clean, out['_external'])
        if again.get('status') == 'built' and again['_external'] != again['_external_assumed']:
            again['warnings'].append({'code': 'funding_addon_unstable', 'severity': 'medium',
                                      'message': 'the external-funding add-on flips the funding result; the second build is kept'})
        out = again
    out['notes'] = [f'input {k!r} is not a forecast input ({", ".join(KNOWN_INPUTS)}): ignored, nothing applied' for k in ignored]
    for k in ignored:
        out['warnings'].append({'code': 'input_ignored', 'severity': 'medium', 'message': out['notes'][ignored.index(k)]})
    out.pop('_external', None)
    out.pop('_external_assumed', None)
    return out


def _build(fb, an, card, qa, inputs, external, no_proxy=False):
    an, card, qa = an or {}, card or {}, qa or {}
    warnings = []

    def warn(code, message, severity='medium'):
        warnings.append({'code': code, 'severity': severity, 'message': message})

    overrides = _check_overrides(fb, inputs.get('overrides') or [])
    reviews = _check_reviews(inputs.get('reviews'))
    rf_spec, ccy = inputs.get('risk_free'), fb['company'].get('currency', 'USD')
    local = (fb.get('market') or {}).get('risk_free') or {}
    if not rf_spec and local.get('currency') == ccy and local.get('source_id') in fb['sources']:
        rf_spec = {k: local[k] for k in ('value', 'as_of', 'source_id')}       # the reporting currency's own long-term yield
    rf, rf_src, rf_note = _rate_input(fb, rf_spec, RF_DEFAULT, 'risk_free')
    if ccy != 'USD' and not rf_spec:
        warn('risk_free_currency', f'cash flows in {ccy} discounted with the USD risk-free default {RF_DEFAULT:.2%}: a currency with '
                                   'lower rates (EUR, DKK, JPY, TWD) is undervalued and one with higher rates overvalued by about '
                                   'the rate gap; supply the local long-term government yield', 'high')
    erp, erp_src, erp_note = _rate_input(fb, inputs.get('equity_risk_premium'), ERP_DEFAULT, 'equity_risk_premium')

    def empty(status, reason, method=None):
        return {'forecast_version': FORECAST_VERSION, 'status': status, 'reason': reason, 'method': method, 'spec': None,
                'spec_sha256': None, 'assumptions': [], 'anchors': {}, 'paths': {}, 'cost_of_equity': None, 'funding': {},
                'overrides': [], 'preview': None, 'warnings': warnings}

    method = (card.get('valuation') or {}).get('method') or 'fcff_annual_path'
    bm = (card.get('business_model') or {}).get('primary')
    if method == 'residual_income' and bm == 'financials':
        from .forecast_financials import build as build_residual_income
        return build_residual_income(fb, an, card, qa, inputs, (rf, erp, rf_src, erp_src, rf_note, erp_note), reviews, warn, warnings,
                                     empty)
    if method in NOT_BUILT or method not in BUILT:
        return empty('method_not_supported_by_forecast', f'valuation method {method!r} needs a dedicated model (residual income, '
                     'NAV, rNPV, SOTP) or no reliable valuation; the operating driver forecast does not apply', method)
    if bm in NON_OPERATING_TYPES:
        return empty('method_not_supported_by_forecast', f'business model {bm} routes to {NON_OPERATING_TYPES[bm]}, not an operating forecast', method)
    h = _history(fb, an, warn, inputs.get('nonrecurring'))
    if h is None:
        return empty('insufficient_data', 'no fiscal year with positive revenue available at as_of', method)
    h['guidance'] = _revenue_guidance(fb)
    h['g_anchor'] = h['g_trailing']
    route = _route(card, h, warn, fb, inputs.get('cyclical'))
    method = route['method']                  # an analyst's structural reading of a card-normalized cycle switches the method
    proxy_w = route['moat_weights']['base'] if route['moat_proxy'] else 0.
    if no_proxy and route['moat_proxy']:
        route['moat_weights'] = {x: 0. for x in SCEN}
    if ROUTES.get(route['archetype']) != 'operating':
        return empty('method_not_supported_by_forecast', f"archetype {route['archetype']} is not an operating route", method)
    res = qa.get('resolved') or {}
    val = lambda k: _num((res.get(k) or {}).get('value'))
    missing = [k for k in ('shares_outstanding', 'cash', 'debt') if val(k) is None]
    price = (fb.get('market') or {}).get('price') or {}
    if missing:
        return empty('insufficient_data', 'dataqa did not resolve opening ' + ', '.join(missing) + '; no silent opening balance', method)
    if _num(price.get('value')) is None or price.get('currency', fb['company']['currency']) != fb['company']['currency']:
        return empty('insufficient_data', 'no quote in the company currency at as_of', method)
    if price.get('unresolved'):
        return empty('insufficient_data', f"the quote is not per ordinary share ({price['unresolved']})", method)
    as_of = fb['as_of']
    material = []
    open_bal = {'cash': val('cash'), 'sti': val('short_term_investments') or 0.,
                'lti': (val('long_term_investments') or 0.) + (val('equity_securities_fv') or 0.),
                'debt': val('debt'), 'shares': val('shares_outstanding'), 'deferred_revenue': val('deferred_revenue') or 0.}
    claims_added = sum(float(it['amount']) for it in inputs.get('debt_like_claims') or [])
    if claims_added:
        open_bal['debt'] = (open_bal['debt'] or 0.) + claims_added
        warn('debt_like_claims_added', f"{claims_added:,.0f} of debt-like claims added to opening debt: "
             + '; '.join(f"{it['kind']} {float(it['amount']):,.0f} ({it['reason']})" for it in inputs['debt_like_claims']), 'medium')
    bridge = []
    stmt_date = min(_d((res.get('cash') or {}).get('as_of') or as_of), _d((res.get('debt') or {}).get('as_of') or as_of))
    sh_date = (res.get('shares_outstanding') or {}).get('as_of')
    for it in inputs.get('capital_events') or []:
        sign = 1. if it['kind'] == 'share_issue' else -1.
        if _d(it['date']) <= stmt_date:
            warn('capital_event_before_statement', f"{it['kind']} on {it['date']} is not after the balance sheet ({stmt_date}): "
                                                   'already in the reported cash; ignored', 'low')
            continue
        open_bal['cash'] = (open_bal['cash'] or 0.) + sign * float(it['cash'])
        count = not sh_date or _d(sh_date) < _d(it['date'])
        if count:
            open_bal['shares'] = (open_bal['shares'] or 0.) + sign * float(it['shares'])
        bridge.append({'kind': it['kind'], 'date': it['date'], 'cash': sign * float(it['cash']), 'shares': sign * float(it['shares']),
                       'shares_applied': count, 'evidence_ids': it['evidence_ids']})
        warn('capital_event_applied', f"{it['kind']} on {it['date']}: cash {sign * float(it['cash']):+,.0f}"
             + (f", shares {sign * float(it['shares']):+,.0f}" if count else ' (shares already in the point-in-time count)')
             + f" after the {stmt_date} balance sheet ({it['reason']})", 'medium')
    assets_added = 0.
    for it in inputs.get('non_operating_assets') or []:
        add_i = float(it['fair_value']) * (1 - float(it['haircut'])) - float(it['book_value_in_bridge'])
        if add_i <= 0:
            warn('non_operating_asset_not_added', f"{it['kind']}: fair value {float(it['fair_value']):,.0f} after a {float(it['haircut']):.0%} "
                                                  f"haircut does not exceed the {float(it['book_value_in_bridge']):,.0f} already in investments", 'low')
            continue
        assets_added += add_i
    if assets_added:
        open_bal['lti'] += assets_added
        warn('non_operating_assets_added', f"{assets_added:,.0f} of non-operating stakes added to opening investments at fair value less "
             'haircut less book value already held: ' + '; '.join(
                 f"{it['kind']} {float(it['fair_value']):,.0f} x (1 - {float(it['haircut']):.0%}) - {float(it['book_value_in_bridge']):,.0f} "
                 f"({it['reason']})" for it in inputs['non_operating_assets']), 'medium')
    op_fund = 0.
    if inputs.get('operating_funding') and open_bal['debt']:
        op_fund = min(float(inputs['operating_funding']['amount']), open_bal['debt'])
        open_bal['debt'] -= op_fund
        warn('operating_funding', f"{op_fund:,.0f} of debt treated as operating funding (its interest is in operating costs) and "
                                  f"left out of the bridge: {inputs['operating_funding']['reason']}", 'medium')
        if not any(o['path'] == 'sales_to_capital' for o in inputs.get('overrides') or []):
            warn('operating_funding_capital', 'historical sales-to-capital counts this funding as invested capital: measure '
                                              'reinvestment net of it (sales_to_capital override) or growth is charged twice', 'high')
    if qa.get('status') == 'blocked':
        warn('data_blocked', 'dataqa status blocked (' + ', '.join(qa.get('blocking') or []) + '): no price conclusion until resolved', 'high')
    for f in qa.get('limited') or []:
        warn('data_limited', f'dataqa field {f} is limited (single source or stale)', 'low')
    external_assumed = external if external is not None else (route['funding'] == 'needs_external' or route['method'] == 'scenario_survival_fcff')

    # ---- calendar and horizon
    cyc_h = route['cycle_path'] and h.get('cycle')
    g_probe = h['cycle']['trend_growth'] if cyc_h else h['g_trailing']
    n_full = int(inputs.get('horizon_years') or next(n for lim, n in HORIZON if g_probe > lim))
    cal = _calendar(fb, h, n_full)
    cons, dropped = _consensus(fb, cal['boundaries'], h['last_end'])
    if dropped:
        warn('lookahead_estimates_ignored', f'{dropped} estimate rows dated after as_of were ignored', 'medium')
    if cons and not inputs.get('horizon_years') and not cyc_h:
        b2 = cal['boundaries'][1]
        g_probe = cons[b2]['value'] / cons.get(cal['boundaries'][0], {'value': h['last_revenue']})['value'] - 1 if b2 in cons else g_probe
        n2 = next(n for lim, n in HORIZON if g_probe > lim)
        if n2 != n_full:
            cal = _calendar(fb, h, n2)
            cons, _ = _consensus(fb, cal['boundaries'], h['last_end'])
    if cal['extended_to_fifth']:
        warn('horizon_extended_to_fifth_year', f"explicit horizon extended to {cal['n_full']} fiscal years so the forecast covers the fifth "
                                               f"anniversary of as_of ({cal['fifth_anniversary']}) and one full fiscal year after it", 'low')
    n_full = cal['n_full']
    st = STAGE[route['stage']]
    label = ' '.join(str(fb['company'].get(k) or '') for k in ('sector', 'industry')).lower()
    regulated = 'regulated' in label and any(w in label for w in REGULATED_WORDS)
    ku, ke_parts = _cost_of_equity(route, rf, erp, [rf_note, erp_note], external_assumed, regulated)   # ku: the explicit table rate
    ku_t = ke_parts['terminal']['table_rate']                                                           # the terminal's (mature) table rate

    # ---- overrides
    applied = []
    econ = {}
    for o in overrides:
        if o['raw']:
            continue
        for s in (SCEN if o['scenario'] == 'all' else (o['scenario'],)):
            econ[(s, o['path'])] = o

    def ov(s, lever, default):
        o = econ.get((s, lever))
        if o is None:
            return default
        applied.append({'id': o['id'], 'path': lever, 'scenario': s, 'original': default, 'value': o['value'],
                        'reason': o['reason'], 'evidence_ids': o['evidence_ids']})
        return float(o['value'])
    ov_ke = {s: float(econ[(s, 'cost_of_equity')]['value']) for s in SCEN if (s, 'cost_of_equity') in econ}

    try:
        P, meta = _params(route, h, cal, cons, rf, ku, ku_t, ov_ke, open_bal['debt'], ov, warn)
        for s in SCEN:
            P[s]['cash_months'] = ov(s, 'minimum_cash_months', st['months'])
        inv_income = (open_bal['sti'] + open_bal['lti']) * rf
        paths, fixed = _paths(P, meta, h, route, cal, open_bal, inv_income)
    except ValueError as e:
        return empty('method_not_supported_by_forecast', str(e), method)
    # extend the horizon if any scenario ends far from its terminal growth (valuation_audit flags >10pp cliffs)
    gap_end = max(abs(paths[s]['growth'][-1] - P[s]['g_terminal']) for s in SCEN)
    if gap_end > .03 and n_full < 20 and not inputs.get('horizon_years'):
        out = _build(fb, an, card, qa, dict(inputs, horizon_years=min(20, n_full + 5)), external, no_proxy)
        out['warnings'].append({'code': 'horizon_extended', 'severity': 'low',
                                'message': f'explicit horizon extended to {n_full + 5} fiscal years so growth fades before the terminal value'})
        return out

    # ---- value effect of judgement switches the evidence does not settle (shown next to the switch, never hidden)
    alt_moat = alt_struct = None
    if route['moat_proxy'] and proxy_w > 0 and not no_proxy:
        a = _build(fb, an, card, qa, inputs, external, no_proxy=True)
        alt_moat = a['preview']['today'] if a.get('status') == 'built' and a.get('preview') else None
    if route['cycle_peak'] and not inputs.get('cyclical'):
        c2 = deepcopy(card)
        c2.setdefault('cyclical', {})['normalize'] = False
        if (c2.get('valuation') or {}).get('method') == 'normalized_cycle_fcff':
            c2['valuation']['method'] = 'fcff_annual_path'
        a = _build(fb, an, c2, qa, dict(inputs, cyclical={'treatment': 'structural', 'reason': 'value-effect probe', 'evidence_ids': []}),
                   external, no_proxy)
        alt_struct = a['preview']['today'] if a.get('status') == 'built' and a.get('preview') else None

    # ---- spec skeleton
    tax_srcs = _latest_src(fb, 'income_tax', 'pretax_income')
    rev_srcs = _latest_src(fb, 'revenue')
    cons_srcs = sorted({e['source_id'] for e in cons.values()})
    fields = qa.get('fields') or {}

    def qsrc(field, keys, *fallback):
        """Sources dataqa used for a field / resolved value; FactBook latest facts when dataqa does not say."""
        ids = set((fields.get(field) or {}).get('sources') or [])
        for k in keys:
            r = res.get(k) or {}
            ids |= set(r.get('source_ids') or []) | ({r['source_id']} if r.get('source_id') else set())
        return sorted(i for i in ids if i in fb['sources']) or _latest_src(fb, *fallback)
    bal_srcs = (qsrc('cash_and_liquid_investments', ('cash', 'short_term_investments', 'long_term_investments', 'equity_securities_fv'),
                     'cash', 'short_term_investments')
                + qsrc('debt', ('debt',), 'long_term_debt_tagged', 'long_term_debt', 'current_portion_ltd'))
    share_srcs = qsrc('shares_outstanding', ('shares_outstanding',), 'shares_outstanding_vendor', 'shares_outstanding_cover', 'shares_outstanding')
    extra = {RULES: {'id': RULES, 'title': 'fundamental_engine.forecast documented model rules (stage fade half-lives, type ranges, '
                     'business-risk table); base-rate references in docs/RESEARCH_BASIS.md', 'url': 'engine://fundamental_engine/forecast/rules',
                     'kind': 'model_rules', 'published_at': as_of, 'available_at': as_of}}
    for src in (rf_src, erp_src):
        if src and 'case' in src:
            extra[src['case']['id']] = src['case']
    rf_ids = [rf_src['fb'] if rf_src and 'fb' in rf_src else (rf_src['case']['id'] if rf_src else RULES)]
    erp_ids = [erp_src['fb'] if erp_src and 'fb' in erp_src else (erp_src['case']['id'] if erp_src else RULES)]
    rows = []

    def add(aid, topic, kind, values, rationale, falsifier, source_ids, provenance=None):
        ids = sorted(set(source_ids) & (set(fb['sources']) | set(extra))) or [RULES]
        row = {'id': aid, 'topic': topic, 'kind': kind,
               'provenance': provenance or {'reported': 'reported', 'analyst': 'vendor_estimate', 'guidance': 'management_guidance'}.get(kind, 'model_assumption'),
               'values': values, 'rationale': rationale, 'falsifier': falsifier, 'source_ids': ids}
        at = next((i for i, r in enumerate(rows) if r['id'] == aid), None)
        if at is None:
            rows.append(row)
        else:
            rows[at] = row

    B = cal['boundaries']
    fy = lambda b: f'FY ending {b}'
    cyc = meta['cycle']
    # opening
    sh_res, cash_res, debt_res = res['shares_outstanding'], res['cash'], res['debt']
    stmt = min(_d(cash_res.get('as_of') or as_of), _d(debt_res.get('as_of') or as_of)).isoformat()
    add('open_balance', 'opening balance', 'reported',
        {'cash': open_bal['cash'], 'short_term_investments': open_bal['sti'], 'long_term_investments': open_bal['lti'],
         'equity_securities_fv_included': val('equity_securities_fv') or 0.,
         'debt': open_bal['debt'], 'deferred_revenue': open_bal['deferred_revenue'], 'statement_as_of': stmt,
         'operating_funding_excluded': op_fund, 'debt_like_claims_added': claims_added, 'non_operating_assets_added': assets_added,
         'capital_events_after_statement': bridge},
        f"dataqa-resolved balance at {stmt}: cash {open_bal['cash']:,.0f}, investments {open_bal['sti'] + open_bal['lti']:,.0f} "
        f"(of which listed equity stakes at fair value {val('equity_securities_fv') or 0.:,.0f}; "
        + (f"plus {assets_added:,.0f} of evidenced private stakes at fair value less haircut and book value) " if assets_added else
           'private stakes at book value only) ') +
        f"(kept as assets earning risk-free after tax; liquidated before any external financing is assumed), debt {open_bal['debt']:,.0f} "
        f"({debt_res.get('method', 'dataqa')}; operating leases treated in cash costs"
        + (f"; {op_fund:,.0f} of operating funding excluded, its cost is in operating costs" if op_fund else '')
        + (f"; includes {claims_added:,.0f} of debt-like claims from inputs" if claims_added else '') + "), deferred revenue "
        "{:,.0f} in its own ledger.".format(open_bal['deferred_revenue']),
        'A primary balance sheet at or after the statement date shows cash, investments or debt differing by more than 2% of market value.',
        bal_srcs)
    add('shares_point', 'point-in-time shares', 'reported', {'shares': open_bal['shares'], 'as_of': sh_res.get('as_of'), 'basis': sh_res.get('basis')},
        f"Point-in-time common shares {open_bal['shares']:,.0f} at {sh_res.get('as_of')} (basis {sh_res.get('basis')}); weighted-average "
        'EPS shares are not used for ownership.', 'The latest filing cover count differs by more than 1%.', share_srcs)
    conv_f = fields.get('convertibles') or {}
    converts = []
    for x in conv_f.get('instruments') or []:
        cs_, cl_ = _num(x.get('conversion_shares')), _num(x.get('accreted_claim')) or _num(x.get('principal'))
        if cs_ and cs_ > 0 and cl_ is not None:
            converts.append({'id': str(x.get('id')), 'claim': cl_, 'conversion_shares': cs_, 'conversion_price': _num(x.get('conversion_price'))})
    # debt is carried net of issuance costs while the claim is the principal (Pinterest: 981m carried, 1bn principal): within 5%
    # the opening debt is lifted to the principal (the claim at maturity), stated; beyond that the definitions differ
    claims = sum(c['claim'] for c in converts)
    if converts and open_bal['debt'] + 1 < claims <= open_bal['debt'] * 1.05 + 1:
        warn('debt_at_convertible_principal', f"opening debt {open_bal['debt']:,.0f} (carrying value) lifted to the convertible principal "
                                              f'{claims:,.0f}: unamortized issuance costs are not a reduction of the claim', 'low')
        open_bal['debt'] = claims
    if converts and claims > open_bal['debt'] + 1:
        warn('convertibles_outside_debt', 'convertible claims exceed the resolved debt: if-converted treatment skipped; reconcile the debt '
                                          'definition', 'high')
        material.append({'id': 'convertibles_unapplied', 'status': 'unresolved', 'assumption_ids': ['dilution'],
                         'description': 'convertible notes are not reconciled to the opening debt, so their conversion is not modelled'})
        converts = []
    conv_sh = sum(c['conversion_shares'] for c in converts)
    add('dilution', 'dilution', 'model', {'incremental_dilutive_shares': 0, 'convertibles': converts},
        'No incremental shares added today: future equity awards are charged as a cash-equivalent cost (sbc_pct), so adding unvested '
        'awards would double count; shares are added only where a cash shortfall is closed by settling SBC in shares or by an '
        f"equity raise (see the funding rows). Convertibles/warrants status in dataqa: {conv_f.get('status', 'not reported')}" +
        (f"; {len(converts)} convertible(s) with {conv_sh:,.0f} conversion shares are held as debt and convert at every valuation date "
         'where the scenario value per share exceeds claim / conversion shares (if-converted, valuation._roll_back).' if converts else '.'),
        'Filings disclose in-the-money convertibles, warrants or vested options above 1% of shares that are not modelled here.',
        share_srcs + [RULES])
    add('claims', 'other claims', 'model', {'other_claims': 0, 'net_runoff_obligation': 0},
        'No preferred stock, minority interests or other senior claims are recorded in the FactBook; deferred revenue renews in '
        'a going concern (ledger, not a runoff claim).', 'A filing shows preferred stock, material minority interests or contingent consideration.',
        bal_srcs + [RULES])
    ta_s, tl_s = facts.series(fb, 'total_assets', 'instant'), facts.series(fb, 'total_liabilities', 'instant')
    last_fy = (facts.annual_table(fb, ['revenue', 'cost_of_revenue', 'gross_profit']) or {None: {}})
    last_fy = last_fy[max(last_fy, key=lambda k: k or '')]
    lender = None
    if ta_s and tl_s and ta_s[-1][0] == tl_s[-1][0] and ta_s[-1][1] > 0 and tl_s[-1][1] / ta_s[-1][1] >= .85 \
            and last_fy.get('cost_of_revenue') is None and last_fy.get('gross_profit') is None:
        lender = {'liabilities_to_assets': tl_s[-1][1] / ta_s[-1][1], 'as_of': ta_s[-1][0]}
        add('classification', 'operating classification of a lender-shaped balance sheet', 'model', lender,
            f"Liabilities are {lender['liabilities_to_assets']:.0%} of assets at {lender['as_of']} and no cost of revenue is reported: the "
            'balance sheet looks like a lender\'s (deposits or wholesale funding), whatever the industry label. An operating FCFF forecast '
            'treats that funding as debt and its spread as operating margin; a residual-income model (book equity, ROE, cost of equity) is '
            'the economic route. A reviewer must confirm the operating classification with evidence or reroute.',
            'Filings show the liabilities are customer prepayments or trade payables of an operating business, not funding of a loan book.',
            _latest_src(fb, 'total_assets', 'total_liabilities', 'revenue') + [RULES])
    sh_gap = abs((_d(sh_res.get('as_of') or stmt) - _d(stmt)).days)
    add('bridge', 'statement-to-as_of bridge', 'model', {'statement_as_of': stmt, 'as_of': as_of, 'events': []},
        f'No bridge events applied between the balance-sheet date {stmt} and as_of {as_of}: cash generated, buybacks and financing '
        'in between are NOT reflected' + (f'; share count is dated {sh_res.get("as_of")} ({sh_gap} days from the balance sheet), so cash and '
                                          'shares are from different dates' if sh_gap > 45 else '') + '. A reviewer must bridge or accept this.',
        'The next filing shows net cash or shares changed by more than 2% of market value since the statement date.', bal_srcs + share_srcs)
    if sh_gap > 45:
        warn('opening_dates_differ', f'shares dated {sh_res.get("as_of")} but cash/debt dated {stmt}: per-share cash may be mis-stated until bridged', 'high')
    if (_d(as_of) - _d(stmt)).days > 150:
        warn('stale_balance_sheet', f'balance sheet dated {stmt} is more than 150 days before as_of; valuation_audit will require a newer statement', 'high')
    # revenue and growth
    gd = h.get('guidance', {}).get(f'FY{B[0][:4]}')
    cur_kind = meta['cur_kind']
    cur_srcs = list(rev_srcs)
    if cur_kind == 'guidance':
        cur_srcs += [x for x in gd['source_ids'] if x not in cur_srcs]
    if meta['ytd']:
        cur_srcs += [x for x in meta['ytd']['source_ids'] if x not in cur_srcs]
    add('revenue_current_fy', 'current fiscal year revenue', cur_kind,
        {'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'stub_annual_rate': meta['stub_rate'],
         'year_to_date': meta['ytd'] and {k: meta['ytd'][k] for k in ('value', 'end', 'prior')}},
        f"{fy(B[0])} revenue {meta['r_fy']:,.0f} ({meta['cur_basis']}); growth {_pct(meta['g_cur'])} vs the last report. The stub "
        f"{as_of}..{B[0]} runs at {meta['stub_rate']:,.0f}/yr = {meta['stub_basis']}.",
        f"Reported {fy(B[0])} revenue differs from {meta['r_fy']:,.0f} by more than 3%.", cur_srcs + cons_srcs + [RULES])
    add('growth_fade', 'growth fade rule', 'model',
        {s: {'half_life_years': (meta['half_life'] if s == 'base' else meta['half_life'] * (.8 if s == 'bear' else 1.3)),
             'terminal_growth': P[s]['g_terminal']} for s in SCEN},
        f"Excess growth over terminal growth halves every {meta['half_life']:.1f} years in base ({route['stage']} stage; base rate: "
        '>20% growers slow to ~8% in five years and ~5% in ten, Koller et al.); bear fades 25% faster, bull 30% slower. Terminal growth '
        + (f"for a declining business: half the trailing decline, bounded at {DECLINE_FLOOR:.0%} (base {_pct(P['base']['g_terminal'])}, "
           f"bear {_pct(P['bear']['g_terminal'])}, bull {_pct(P['bull']['g_terminal'])}); the business is not forced to recover."
           if route['stage'] == 'decline' else
           f"= the risk-free rate {rf:.2%}"
           + (f" held at the valuation contract's {TERMINAL_GROWTH_MAX:.0%} bound (flagged terminal_growth_capped)" if rf > TERMINAL_GROWTH_MAX else '')
           + f" in base and bull, {TERMINAL_BEAR_GAP:.1%} lower in the bear (base {_pct(P['base']['g_terminal'])}, "
           f"bear {_pct(P['bear']['g_terminal'])}, bull {_pct(P['bull']['g_terminal'])}): the convention of the implied equity risk "
           "premium it is paired with (solved with perpetual growth at the Treasury yield; Damodaran sets stable growth at, never "
           "above, the risk-free rate), with terminal reinvestment g / ROIC (growth is not free)."),
        'Revenue growth stays more than 5 points above the base path for two consecutive years, or falls below the bear path.',
        ([RULES] if route['stage'] == 'decline' else rf_ids + erp_ids + [RULES]))
    if cyc:
        add('cycle_normalization', 'mid-cycle revenue', 'model',
            {'mean_revenue': cyc['mean_revenue'], 'mid_date': cyc['mid_date'], 'window': cyc['window'], 'trend_slope': cyc['slope'],
             'trend_growth': cyc['long_run_growth'], 'trend_growth_basis': cyc['trend_growth_basis'],
             'slope_lower_2se': cyc['slope_lower_2se'], 'trend_basis': cyc['trend_basis'], 'trend_level': cyc['trend_level'],
             'trend_end': cyc['trend_end'], 'trend_at_current_fy': cyc['trend_at_current_fy'], 'level_factor': cyc['level_factor'],
             'reversion_years': cyc['conv'], 'peak_ratio': cyc['peak_ratio'], 'trough_ratio': cyc['trough_ratio']},
            "Cyclical revenue is anchored to the cycle, not the latest year: "
            + (f"the log-linear trend of {cyc['years']} fiscal years ({cyc['window'][0]}..{cyc['window'][1]}, slope {_pct(cyc['slope'])}) is "
               f"{cyc['trend_level']:,.0f} at {cyc['trend_end']} (fitted in sample), grown after it at the long-run trend "
               + (f"{_pct(cyc['long_run_growth']['base'])}: the slope less two standard errors ({_pct(cyc['slope_lower_2se'])}, what the data "
                  f"support with about 95% confidence) within the revenue-scale base rate, fading to terminal like any growth rate"
                  if cyc['trend_growth_basis'] == 'measured_lower_bound' else
                  f"{_pct(cyc['long_run_growth']['base'])} (the slope bounded to {_pct(CYCLE_TREND[0])}..{_pct(CYCLE_TREND[1])}"
                  + (f"; its lower 2-standard-error bound {_pct(cyc['slope_lower_2se'])} does not support more)" if cyc['slope_lower_2se'] is not None else ')'))
               if cyc['trend_basis'] == 'fitted' else
               f"mean revenue {cyc['mean_revenue']:,.0f} over {cyc['years']} fiscal years ({cyc['window'][0]}..{cyc['window'][1]}; fewer than "
               f"{CYCLE_FIT_MIN_YEARS}, so no fitted trend) placed at {cyc['mid_date']} and grown at the long-run trend "
               f"{_pct(cyc['long_run_growth']['base'])} (log-linear slope {_pct(cyc['slope'])} bounded to {_pct(CYCLE_TREND[0])}..{_pct(CYCLE_TREND[1])})")
            + f" = {cyc['trend_at_current_fy']:,.0f} at {B[0]}. "
            f"Revenue and margin revert together from today's level ({cyc['distance_from_trend']:.2f} x trend; the ratio closes geometrically) over {CYCLE_CONV['base']} years (bear {CYCLE_CONV['bear']}, bull {CYCLE_CONV['bull']}); "
            f"bear settles at trend x {cyc['level_factor']['bear']:.2f} (half-way to the historical trough ratio {cyc['trough_ratio']:.2f} in log terms), "
            f"bull at trend x {cyc['level_factor']['bull']:.2f} (half-way to the peak ratio {cyc['peak_ratio']:.2f}).",
            'Revenue stays more than 15% above the trend path for two years after the reversion period (a structural step-up, not a cycle).',
            rev_srcs + [RULES])
    cmar = _cycle_margin(h)
    if route['cycle_peak']:
        tr = route['cycle_treatment']
        cy_in = inputs.get('cyclical') or {}
        ev = cy_in.get('evidence_ids') or []
        cy_srcs = [e for e in ev if e in fb['sources']] + [x for q in fb.get('qualitative', []) if q['id'] in ev for x in q['source_ids']]
        f2 = lambda v: 'n/a' if not v else ' / '.join('n/a' if v.get(k) is None else f'{v[k]:,.2f}' for k in SCEN)
        add('cycle_position', 'cycle position: cyclical industry at a margin peak', 'analyst' if tr else 'model',
            {'position': 'peak', 'treatment': tr or 'cyclical (default until an analyst decides)', 'latest_margin': h['m0'],
             'through_cycle_margin': cmar and cmar['mid'], 'value_if_structural': alt_struct},
            (f"The industry is cyclical and the latest operating margin {_pct(h['m0'])} sits at the top of the history (through-the-cycle "
             f"{_pct(cmar and cmar['mid'])}; the model card's position: peak). " +
             (f"Analyst treatment '{tr}': {cy_in.get('reason')} (evidence {', '.join(ev)})." if tr else
              'No analyst has shown the margin to be structural, so it is valued around the cycle (mid-cycle revenue and margin), '
              f'never extrapolated from the peak. Value today if the margin were structural instead (bear / base / bull): {f2(alt_struct)}.')
             + (' The bear still tests a return to the through-the-cycle margin.' if tr == 'structural' else '')),
            'Margins hold above the through-the-cycle level through a full industry downturn (structural), or fall to the trough '
            '(the cycle turned).', rev_srcs + _latest_src(fb, 'operating_income') + cy_srcs + [RULES],
            provenance='model_assumption')
    for s in SCEN:
        g = paths[s]['growth']
        why = {'base': (f"Years 1-{len(meta['consensus_growth'])} follow revenue consensus, then fade" if meta['growth_kind'] == 'analyst'
                        else 'Revenue reverts from today\'s level to the mid-cycle trend (cycle_normalization), then grows at the trend fading to terminal'
                        if cyc else (f"Faded from the long-run like-for-like growth {_pct(h['cyclical_start'])} (5-year CAGR without acquisition steps, else "
                                     f"3-year), not the current fiscal year's {_pct(meta['g_cur'])}: a cyclical business's change this "
                                     'year is the cycle, not a trend' if h.get('cyclical_start') is not None else
                                     f"Faded from organic growth {_pct(h['acq']['organic'])}, not the current fiscal year's "
                                     f"{_pct(meta['g_cur'])} that includes acquired revenue ({h['acq']['statement']}); the acquired "
                                     'revenue is a level shift in the current year' if h.get('acq') else
                                     f"Faded from the current fiscal year's growth {_pct(meta['g_cur'])} ("
                        + (f"year-to-date reports, see revenue_current_fy; trailing growth {_pct(h['g_trailing'])} for reference"
                           if meta.get('ytd') else f"trailing growth {_pct(h['g_trailing'])}: {h['g_trailing_formula']}") + ')')),
               'bear': ('Faster reversion to a lower cycle level, lower trend growth' if cyc else
                        f"Realistic disappointment: first-year growth min(base - spread, half of base) with spread {_pct(meta['spread'])} "
                        '(max of stage floor, historical growth volatility and consensus dispersion), faster fade'),
               'bull': ('Slower reversion to a higher cycle level, higher trend growth' if cyc else
                        f"Plausible upside: base + spread {_pct(meta['spread'])} (never above base + 2 spreads), slower fade")}[s]
        add(f'growth_{s}', f'{s} revenue growth', 'analyst' if s == 'base' and meta['growth_kind'] == 'analyst' else 'model',
            {s: g}, f"{why}: {_pct(g[0])} in {fy(B[1])}, {_pct(g[min(4, len(g) - 1)])} in {fy(B[min(5, len(B) - 1)])}, "
            f"{_pct(g[-1])} in {fy(B[-1])}; terminal {_pct(P[s]['g_terminal'])}.",
            f"Reported growth for {fy(B[1])} " + ({'base': f"below {_pct((g[0] + paths['bear']['growth'][0]) / 2)} or above {_pct((g[0] + paths['bull']['growth'][0]) / 2)}",
                                                   'bear': f"above {_pct(paths['base']['growth'][0])} (bear too harsh)",
                                                   'bull': f"below {_pct(paths['base']['growth'][0])} (bull not on track)"}[s]) + '.',
            rev_srcs + (cons_srcs if s == 'base' else []) + [RULES])
    # outside view: how often companies of this size grew as fast as each scenario (a reality check on the inside view, never an input)
    outside = None
    if fb['company'].get('currency', 'USD') == 'USD' and h['last_revenue'] and h['last_revenue'] > 0 and not cyc:
        full = {s: [meta['g_cur']] * cal['fiscal_years_since_last_report'] + list(paths[s]['growth']) for s in SCEN}
        outside = {s: base_rates.outside_view(h['last_revenue'], {n: base_rates.cagr(full[s], n) for n in (5, 10)}) for s in SCEN}
        bh = outside['base']['horizons']
        n_chk = 10 if 10 in bh else 5 if 5 in bh else None
        if n_chk:
            x = bh[n_chk]
            add('outside_view_growth', 'growth against base rates for companies of this size', 'model',
                {s: {n: {k: v[k] for k in ('nominal_cagr', 'real_cagr', 'share_at_least', 'class_median_real')}
                     for n, v in outside[s]['horizons'].items()} for s in SCEN} | {'reference_class': outside['base']['reference_class']},
                f"Base case: revenue {_pct(x['nominal_cagr'])} a year over {n_chk} fiscal years ({_pct(x['real_cagr'])} after "
                f"{base_rates.INFLATION:.2%} expected inflation); {x['share_at_least']:.0%} of companies with sales of "
                f"{outside['base']['reference_class']} (2015 dollars) grew at least that fast in 1950-2015, class median "
                f"{_pct(x['class_median_real'])} ({base_rates.SOURCE}; sales include acquisitions and survivors only). A reality check on "
                'the forecast, not a probability for this company: evidence can justify a place in the tail.',
                'Reported growth over the horizon outside the bear-bull range.', [RULES])
            if x['share_at_least'] < .10:
                warn('growth_above_base_rate', f"base case needs {_pct(x['real_cagr'])} real revenue growth a year for {n_chk} years: only "
                                               f"{x['share_at_least']:.0%} of companies this size did it (base rates, 1950-2015); the "
                                               'evidence for a tail outcome must be in the case', 'medium')
            elif x['share_at_least'] > .60:
                warn('growth_below_base_rate', f"base case grows {_pct(x['real_cagr'])} a year after inflation for {n_chk} years, slower than "
                                               f"{1 - x['share_at_least']:.0%} of companies this size did (class median "
                                               f"{_pct(x['class_median_real'])}); conservative unless the evidence says the business is "
                                               'shrinking or saturated', 'low')
    # margins
    for s in SCEN:
        m = paths[s]['operating_margin']
        add(f'margin_{s}', f'{s} operating margin (after SBC)', 'model', {s: {'path': m, 'mature': P[s]['mature_margin']}},
            f"GAAP operating margin after SBC moves from {_pct(fixed['m0'])} ({h['m0_basis']}) to {_pct(P[s]['mature_margin'])} over "
            f"{P[s]['conv']} years, then holds. {meta['margin_why'] if s == 'base' or route['cycle_path'] else s + ' = base mature margin ' + ('- ' + _pct(st['m_spread']) if s == 'bear' else '+ 0.75 x ' + _pct(st['m_spread']) + ', capped at the type ceiling ' + _pct(route['type'][2]))}. "
            'cash_cost_ratio = 1 - (operating margin + sbc_pct + depreciation_pct), matching drivers.operating: '
            'ebit = revenue - cash costs - SBC - depreciation.',
            {'base': f"Reported operating margin for {fy(B[1])} below {_pct(m[1] - st['m_spread'] / 2)} without a one-off explanation.",
             'bear': f"Reported operating margin stays at or above the base path ({_pct(paths['base']['operating_margin'][1])} in {fy(B[1])}) for two years (bear too harsh).",
             'bull': f"Reported operating margin for {fy(B[1])} below the base path {_pct(paths['base']['operating_margin'][1])} (bull not on track)."}[s],
            _latest_src(fb, 'operating_income', 'revenue') + [RULES])
    k_moat = .5 if route['moat'] == 'weak' else .25
    typ_m = route['type'][1]
    w_mo = route['moat_weights']
    haircut = (1 - w_mo['base']) * k_moat * (fixed['m0'] - typ_m) if route['moat'] != 'strong' and fixed['m0'] > typ_m and not cyc else 0.
    f3 = lambda v: 'n/a' if not v else ' / '.join('n/a' if v.get(k) is None else f'{v[k]:,.2f}' for k in SCEN)
    add('moat_basis', 'competitive advantage behind margins and returns', 'model',
        {'moat': route['moat'], 'basis': route['moat_basis'], 'proxy_weight': w_mo, 'proxy': route['moat_proxy'],
         'margin_competed_away': haircut, 'terminal_roic_base': P['base']['roic'],
         'roic_demonstrated': h['roic'], 'roic_average': h.get('roic_avg'), 'roic_average_demonstrated': h.get('roic_avg_demonstrated'),
         'roic_average_basis': h.get('roic_avg_basis'), 'unlevered_cost_of_capital': P['base']['hurdle'],
         'value_today_without_proxy': alt_moat},
        (f"Moat {route['moat']} ({route['moat_basis']}): " +
         ('current margin held and terminal ROIC max(demonstrated ROIC, Ku+5pp) capped at 40%.' if route['moat'] == 'strong' else
          (f"base and bull blend the strong-moat rules at weight {w_mo['base']:.2f} (base terminal ROIC {_pct(P['base']['roic'])}, "
           f"{haircut:.1%} of revenue competed away); the bear keeps the unassessed rules (terminal ROIC {_pct(P['bear']['roic'])}). "
           f"Value today without the proxy (bear / base / bull): {f3(alt_moat)}. A computed proxy, not an analyst judgement: an evidenced "
           'moat assessment replaces it.' if w_mo['base'] > 0 else
           f"{k_moat:.0%} of today's margin excess over the type-typical {_pct(typ_m)} is competed away ({haircut:.1%} of revenue) and terminal "
           f"ROIC fades to {_pct(P['base']['roic'])} against a demonstrated ROIC {_pct(h['roic'])} ("
           + ('weak moat: excess returns competed down to Ku+2pp' if route['moat'] == 'weak' else
              f"Ku {_pct(P['base']['hurdle'])} + {(P['base'].get('roic_persistence') or EXCESS_RETURN_PERSISTENCE['base']):.0%} of the "
              f"demonstrated excess, at least Ku+2pp: the base-rate share of excess returns left after ten years, lifted for a high, "
              f"stable realized ROIC ({meta.get('roic_durability', {}).get('basis', 'floor')})"
              + (f"; demonstrated average: {h['roic_avg_basis']}" if h.get('roic_avg_demonstrated') != h.get('roic_avg') else '')) +
           '). A quantified model haircut, not evidence: an evidenced analyst moat assessment replaces it.'))),
        f"Returns on capital stay above {_pct(P['base']['roic'] + .05)} and margins above {_pct(P['base']['mature_margin'] + .03)} for three years "
        '(haircut too harsh), or fall below the bear path (too generous).', _latest_src(fb, 'operating_income', 'gross_profit', 'equity') + [RULES])
    add('sbc_path', 'stock-based compensation', 'model', {s: paths[s]['sbc_pct'] for s in SCEN},
        f"SBC {_pct(fixed['sbc0'])} of revenue today ({h['sbc_basis']}), fading by {st['sbc_fade']:.0%} of its level over {st['conv']} years at "
        'scale; charged as a cash-equivalent cost (policy cash_equivalent) with point-in-time shares, so no separate dilution is added '
        'unless a cash shortfall is closed by settling it in shares. Value-neutral to the mix: the GAAP margin path already includes SBC.',
        'SBC rises above today\'s share of revenue for two consecutive years.', _latest_src(fb, 'sbc', 'revenue'))
    add('da_capex', 'depreciation and maintenance capex', 'model',
        {'depreciation_pct': fixed['dep'], 'depreciation_path': [_r(x) for x in fixed['dep_path']], 'maintenance_capex_pct': fixed['maint']},
        f"Depreciation {_pct(fixed['dep'])} of revenue today (latest D&A incl. amortization) converges to maintenance capex "
        f"{_pct(fixed['maint'])} = min(3-year average capex {_pct(h['capex_pct'])}, depreciation) by the last explicit year, so the terminal "
        'year carries no free depreciation tax shield or unfunded replacement. The GAAP margin path is held, so D&A above maintenance capex '
        '(mostly amortization of acquired intangibles) is treated as a recurring economic cost by the end, not released as cash.',
        'Capex or D&A as a share of revenue moves by more than 1.5 points.', _latest_src(fb, 'd_and_a', 'capex', 'revenue'))
    add('reinvestment', 'growth reinvestment', 'model',
        {s: {'sales_to_capital': P[s]['s2c'], 'sales_to_capital_terminal': P[s]['s2c_terminal'], 'growth_capex_pct': paths[s]['growth_capex_pct']}
         for s in SCEN},
        f"Growth costs capital: net reinvestment = change in revenue x capital per unit of new revenue, starting at 1 / sales-to-capital "
        f"(history {meta['s2c_history']}, type range {meta['s2c_range']}, base {P['base']['s2c']:.2f}, bear x0.8, bull x1.2) and converging "
        f"by the last explicit year to the terminal rule g / ROIC (sales-to-capital = ROIC / NOPAT margin, base {P['base']['s2c_terminal']:.2f}). "
        'Per period growth capex + change in working capital - change in deferred revenue equals that need on the bases drivers uses '
        '(stub: the current year\'s need pro rata, no working-capital step; first year: change from the current fiscal year); growth '
        'capex is floored at zero where customer prepayments fund more than the need; a declining business releases capital through '
        f"maintenance capex below depreciation (never below {RELEASE_FLOOR:.0%} of the maintenance level). Acquisition spend 3y: "
        f"{_pct(h['acq_pct'])} of revenue (the share of the D&A roll-off it replaces stays a cash cost: {fixed['acq_share']:.0%}).",
        'Net reinvestment per unit of new revenue over the next two years differs from the base by more than 50%.',
        _latest_src(fb, 'revenue', 'capex', 'equity') + [RULES])
    add('working_capital', 'working capital and deferred revenue', 'model',
        {'working_capital_ratio': fixed['wc'], 'deferred_revenue_ratio': fixed['dr'], 'prepaid_share': fixed['prepaid']},
        f"working_capital_ratio {fixed['wc']:.3f} = {h['wc_basis'] or 'default 5% (not computable)'}; deferred revenue kept at "
        f"{fixed['dr']:.1%} of the annual revenue rate (opening balance / stub rate); prepaid_share {fixed['prepaid']:.2f} = 2 x ratio "
        '(ledger split only, value-neutral).', 'Receivables+inventory-payables or deferred revenue move by more than 5% of revenue against revenue.',
        h['wc_sources'] + _latest_src(fb, 'deferred_revenue_current', 'revenue'))
    add('tax', 'tax rate', 'model', {s: P[s]['tax'] for s in SCEN},
        f"Normalized tax {P['base']['tax']:.1%}: 3-year effective {_pct(meta['tax_effective'])} bounded to 15-25% (21% statutory when "
        'pretax income is not positive); NOL usage capped at 80% of taxable income; opening NOL 0 unless supplied.',
        'Cash taxes exceed 25% or fall below 12% of pretax income for two years.', tax_srcs + [RULES])
    policy = {}
    for s in SCEN:
        dflt = 1. if (h['payout_history'] or 0) >= .5 or route['stage'] in ('mature', 'maturing', 'decline') else .5
        policy[s] = ov(s, 'payout_fraction', dflt)
    mc0 = paths['base']['minimum_cash'][0]
    mc_max = max(paths['base']['minimum_cash'])          # the operating balance held at its peak: never released, never valued
    add('funding_policy', 'minimum cash and payout', 'model',
        {'minimum_cash_months': {s: P[s]['cash_months'] for s in SCEN}, 'payout_fraction': policy, 'payout_history_3y': h['payout_history'],
         'minimum_cash_today': mc0, 'minimum_cash_peak': mc_max, 'minimum_cash_per_share': mc_max / open_bal['shares']},
        f"Minimum operating cash = min({P['base']['cash_months']:.0f} months of cash costs, today's cash), {mc0:,.0f} in the first period "
        f"rising to {mc_max:,.0f} (base) of today's cash {open_bal['cash']:,.0f}: about {mc_max / open_bal['shares']:,.2f} per share is HELD "
        'as operating cash and never released, so it adds no value (a judgement: the company already operates with this balance). Cash '
        f"above it and the investments are valued at face. Excess cash is distributed at {policy['base']:.0%} (buybacks/dividends; history: "
        f"distributions = {_pct(h['payout_history'])} of FCF over 3 years) only once free cash flow stays positive; cash retained above the "
        'minimum is still worth face (valuation._roll_back), so the payout policy changes funding, not value. A shortfall is closed by '
        'investments, then SBC settled in shares, then assumed debt within capacity, then an assumed equity raise.',
        'The company accumulates cash for two years without distributing, or distributes while cash falls below the minimum.',
        _latest_src(fb, 'buybacks', 'cfo', 'cash') + [RULES])
    r_new = rf + st['debt_spread']
    add('debt_interest', 'existing debt', 'model', {'interest_rate_existing': meta['interest_existing'], 'new_money_rate': r_new},
        f"Existing debt {open_bal['debt']:,.0f} held flat (maturities refinanced) at {meta['interest_existing']:.2%} "
        f"({'interest expense / debt' if h['interest_expense'] and open_bal['debt'] else 'risk-free + stage spread'}); any assumed new money at "
        f'{r_new:.2%} (risk-free + {st["debt_spread"]:.0%}), only up to {DEBT_CAPACITY:.0f}x EBITDA before SBC.',
        'Refinancing at a rate more than 2 points above the assumption or a covenant breach.', _latest_src(fb, 'interest_expense') + bal_srcs)
    add('investment_income', 'investment income', 'model', {'annual_after_tax': paths['base']['investment_income_after_tax'][0]},
        f"Marketable investments {open_bal['sti'] + open_bal['lti']:,.0f} earn the risk-free rate {rf:.2%} after tax; principal counted once in the "
        'terminal bridge, or spent first when cash falls short (income falls with the balance).',
        'Investments are liquidated for acquisitions or buybacks.', bal_srcs + rf_ids)
    ke_row = lambda: add('cost_of_equity', 'cost of equity and terminal WACC', 'model', {'cost_of_equity': {s: P[s]['ke'] for s in SCEN}},
                         'placeholder until the capital structure is solved', 'n/a', [RULES])
    ke_row()

    def term_row(s):
        rule = ({'base': 'returns stay above the cost of capital: max(demonstrated ROIC, Ku+5pp), capped at 40%', 'bear': '70% of the base ROIC, '
                 'at least Ku+2pp', 'bull': '120% of the base ROIC, capped at 45%'}[s] if route['moat'] == 'strong' else
                {'base': 'excess returns competed down to Ku+2pp', 'bear': 'Ku+1pp', 'bull': 'Ku+4pp'}[s] if route['moat'] == 'weak' else
                {'base': f"Ku + {(P['base'].get('roic_persistence') or EXCESS_RETURN_PERSISTENCE['base']):.0%} of the demonstrated excess return (at least Ku+2pp)",
                 'bear': 'Ku+1pp',
                 'bull': f"Ku + {(P['bull'].get('roic_persistence') or EXCESS_RETURN_PERSISTENCE['bull']):.0%} of the demonstrated excess return (at least Ku+4pp)"}[s])
        if s in EXCESS_RETURN_PERSISTENCE and route['moat'] != 'strong' and route['moat_weights'][s] == 0 and (meta.get('roic_durability') or {}).get('durability'):
            rule += f" (persistence lifted from the {EXCESS_RETURN_PERSISTENCE[s]:.0%} floor by a durable realized ROIC: {meta['roic_durability']['basis']})"
        if s in EXCESS_RETURN_PERSISTENCE and route['moat'] not in ('strong', 'weak') and h.get('roic_avg_demonstrated') != h.get('roic_avg'):
            rule += (f" (demonstrated average {_pct(h['roic_avg_demonstrated'])} over the post-breakeven years of a scale-up path, not "
                     f"the {len(h['roic_series'])}-year mean {_pct(h['roic_avg'])})")
        if route['moat'] != 'strong' and route['moat_weights'][s] > 0:
            rule += f" blended toward the strong-moat rule at proxy weight {route['moat_weights'][s]:.2f}"
        if P[s].get('release_bounded'):
            rule += ('; for this shrinking business ROIC is raised so the terminal releases capital no faster than the last explicit '
                     'year can (capex cannot be negative)')
        add(f'terminal_{s}', f'{s} terminal value', 'model',
            {s: {'growth': P[s]['g_terminal'], 'roic': P[s]['roic'], 'wacc': P[s]['wacc'], 'operating_margin': paths[s]['operating_margin'][-1]}},
            f"After {B[-1]}: growth {_pct(P[s]['g_terminal'])}, operating margin {_pct(paths[s]['operating_margin'][-1])} = the last explicit "
            f"year (no jump), reinvestment = g / ROIC = {P[s]['g_terminal'] / P[s]['roic']:.1%} of NOPAT with ROIC {_pct(P[s]['roic'])} "
            f"({route['moat']} moat, {rule}; Ku = the terminal's mature-table cost of capital {_pct(P[s]['hurdle'])}); the last explicit year already "
            f"reinvests on that rule (reinvestment), discounted at WACC {_pct(P[s]['wacc'])}.",
            'Mature-stage margin or returns on capital of the company (or its closest peers) stay below these levels.',
            _latest_src(fb, 'operating_income', 'equity') + [RULES])
    for s in SCEN:
        term_row(s)
    if cons:
        add('consensus', 'consensus comparison', 'analyst', {b: {k: cons[b].get(k) for k in ('value', 'low', 'high', 'n_analysts', 'as_of')} for b in sorted(cons)},
            'Revenue consensus (vendor estimate, not a reported fact) for ' + ', '.join(fy(b) for b in sorted(cons)) +
            '; GAAP revenue on both sides. Agreement with consensus is not required; it anchors only the first two base years.',
            'A refreshed consensus differs by more than 5%.', cons_srcs)
        stale = [b for b, e in cons.items() if (_d(as_of) - _d(e.get('as_of') or as_of)).days > 90]
        if stale:
            warn('stale_estimate', 'consensus older than 90 days for ' + ', '.join(stale), 'medium')
    else:
        add('consensus', 'consensus comparison', 'model', {'status': 'unavailable'},
            'The FactBook carries no revenue consensus at as_of (not retrieved or not stored); nothing is fabricated. Growth is anchored on '
            'trailing growth instead.', 'A dated consensus becomes available and differs from the base first-year revenue by more than 10%.',
            rev_srcs + [RULES])
        warn('no_consensus', 'no revenue consensus in the FactBook: growth anchored on trailing growth only', 'medium')
    if h['n_years'] < 3:
        warn('short_history', f"only {h['n_years']} fiscal years of history", 'high')
    if h['ttm']['period_end'] and (_d(as_of) - _d(h['ttm']['period_end'])).days > 200:
        warn('stale_ttm', f"latest revenue period ends {h['ttm']['period_end']} ({h['ttm']['method']}), more than 200 days before as_of", 'medium')
    if (h['acq_pct'] or 0) > .05:
        warn('acquired_growth', f"acquisitions averaged {_pct(h['acq_pct'])} of revenue over 3 years: trailing growth includes bought growth", 'medium')
    # an acquisition closed inside the year to date: the year-to-date growth in the anchor includes acquired revenue
    yacq = _ytd(fb, 'acquisitions', h['last_end'])
    closed = [q for q in fb.get('qualitative', []) if q.get('kind') == 'reported' and re.search(r'\bacqui', q.get('statement', ''), re.I)
              and re.search(r'\b(closed|completed)\b', q.get('statement', ''), re.I)
              and any(_d(h['last_end']) < _d(d) <= _d(as_of) for d in re.findall(r'\b(20\d\d-\d\d-\d\d)\b', q.get('statement', '')))]
    if closed or (yacq and abs(yacq['value']) > .01 * h['last_revenue']):
        warn('anchor_includes_acquired_revenue', 'an acquisition closed in the current fiscal year (' +
             ', '.join([q['id'] for q in closed] + ([f"year-to-date acquisition spend {abs(yacq['value']):,.0f}"] if yacq and abs(yacq['value']) > .01 * h['last_revenue'] else []))
             + '): year-to-date growth, the current-year revenue and the trailing growth anchor include acquired revenue; organic growth is lower',
             'medium')

    # ---- spec: fiscal-year paths expanded onto the period boundaries (the fifth anniversary may split one fiscal year)
    Pb = cal['periods']
    pmap = [next(j for j, b in enumerate(B) if b >= d) for d in Pb]
    starts = [as_of] + Pb[:-1]
    cont = [x in cal['interim'] for x in starts]
    fy_pidx = [Pb.index(b) for b in B]
    ticker = fb['company']['ticker']
    company_id = f"{ticker.lower()}-cik{fb['company']['cik']}" if fb['company'].get('cik') else ticker.lower()
    scen = []
    for s in SCEN:
        p = paths[s]
        thesis = (f"{s.capitalize()}: revenue growth {_pct(p['growth'][0])} in {fy(B[1])} fading to {_pct(P[s]['g_terminal'])}; operating "
                  f"margin after SBC {_pct(p['operating_margin'][0])} -> {_pct(P[s]['mature_margin'])}; growth reinvestment at sales-to-capital "
                  f"{P[s]['s2c']:.2f} converging to {P[s]['s2c_terminal']:.2f}.")
        sc = {'name': s, 'thesis': thesis, 'assumption_ids': [f'growth_{s}', f'margin_{s}', 'cost_of_equity'],
              'period_assumption_ids': ['revenue_current_fy', 'growth_fade', f'growth_{s}', f'margin_{s}', 'sbc_path', 'da_capex',
                                        'reinvestment', 'working_capital', 'tax', 'funding_policy', 'debt_interest', 'investment_income']
                                       + (['cycle_normalization'] if cyc else []),
              'cost_of_equity': _r(P[s]['ke']), 'sbc_policy': 'cash_equivalent',
              'growth': [0. if cont[i] else p['growth'][pmap[i] - 1] for i in range(1, len(Pb))]}
        for k in list(RATIO_KEYS) + list(ABSOLUTE_KEYS):
            sc[k] = [p[k][pmap[i]] for i in range(len(Pb))] if k in p else [0.0] * len(Pb)   # e.g. equity_proceeds: none before funding
        sc['terminal'] = {'assumption_ids': [f'terminal_{s}', 'cost_of_equity', 'claims'], 'growth': _r(P[s]['g_terminal']),
                          'wacc': _r(P[s]['wacc']), 'roic': _r(P[s]['roic']), 'operating_margin': _r(1 - p['cash_cost_ratio'][-1] - p['sbc_pct'][-1] - p['depreciation_pct'][-1]),
                          'tax_rate': _r(P[s]['tax']), 'sbc_in_margin': True, 'net_runoff_obligation': 0, 'other_claims': 0,
                          'working_capital_rationale': 'Working capital and deferred revenue renew with the business in a going concern; their growth '
                                                       'is funded by the g/ROIC reinvestment rule. No runoff claim.'}
        idn = fixed['identity'][s]
        # the engine's own reinvestment rule, per fiscal year (index 0 = the stub), so a growth sensitivity can charge capital
        # for the growth it adds (what_must_be_true); informational for drivers.build_case, which ignores it
        sc['reinvestment_rule'] = {'capital_per_revenue': [_r(x) for x in idn['capital_per_revenue']], 'maintenance_capex_pct': _r(fixed['maint']),
                                   'release': idn['release'], 'stub_revenue_change': _r(meta['r_fy'] - meta['r_fy'] / (1 + meta['g_cur']), 2),
                                   'rule': 'growth capex + change in working capital - change in deferred revenue = change in revenue x '
                                           'capital per unit of revenue (forecast._reinvest)'}
        scen.append(sc)
    spec = {'driver_version': 1, 'strict_drivers': True, 'is_demo': False, 'as_of': as_of, 'company_id': company_id, 'ticker': ticker,
            'currency': fb['company']['currency'], 'unit': 'absolute', 'archetype': route['archetype'], 'method': 'operating',
            'quote': {'price': float(price['value']), 'currency': price.get('currency', fb['company']['currency']), 'as_of': price['as_of'],
                      'source_ids': [price['source_id']]},
            'opening': {'as_of': as_of, 'assumption_ids': ['open_balance', 'shares_point', 'dilution', 'bridge'],
                        'unrestricted_cash': open_bal['cash'], 'debt': open_bal['debt'], 'shares': open_bal['shares'],
                        'deferred_revenue': open_bal['deferred_revenue'], 'tax_loss_carryforward': float(_num(inputs.get('nol')) or 0),
                        'marketable_investments': open_bal['sti'] + open_bal['lti'],
                        **({'convertibles': [{k: c[k] for k in ('id', 'claim', 'conversion_shares')} for c in converts]} if converts else {})},
            'report_dates': cal['report_dates'], 'boundaries': Pb, 'interim_boundaries': list(cal['interim']),
            'stub_annual_revenue_rate': _r(meta['stub_rate'], 2), 'last_full_year_revenue': _r(meta['r_fy'], 2), 'segment_name': 'Revenue',
            'segment_driver': 'subscribers' if route['business_model'] == 'subscription_software' else 'units', 'scenarios': scen}
    if _num(inputs.get('nol')):
        warn('nol_supplied', f"opening tax-loss carryforward {inputs['nol']:,.0f} supplied by inputs", 'low')
    elif fixed['m0'] < 0:
        warn('nol_unknown', 'loss-making company: tax-loss carryforwards are not in the FactBook and are set to 0 (conservative)', 'medium')
    # raw driver overrides (after paths, before funding); lists are per period boundary (spec['boundaries'])
    locked = {}
    for o in overrides:
        if not o['raw']:
            continue
        for s in (SCEN if o['scenario'] == 'all' else (o['scenario'],)):
            sc = _scen(spec, s)
            if o['path'].startswith('terminal.'):
                key = o['path'].split('.', 1)[1]
                orig, sc['terminal'][key] = sc['terminal'].get(key), float(o['value'])
            else:
                orig = sc[o['path']]
                need = len(orig)
                v = o['value'] if isinstance(o['value'], list) else [o['value']] * need
                if len(v) != need:
                    raise ValueError(f"override {o['path']}: needs {need} values")
                sc[o['path']] = [float(x) for x in v]
                locked.setdefault(s, set()).add(o['path'])
                if o['path'] in ('cash_cost_ratio', 'sbc_pct', 'depreciation_pct', 'amortization', 'fixed_cash_costs') and 'terminal.operating_margin' not in [x['path'] for x in overrides]:
                    last_rev = paths[s]['rates'][-1]
                    sc['terminal']['operating_margin'] = _r(1 - sc['cash_cost_ratio'][-1] - sc['sbc_pct'][-1] - sc['depreciation_pct'][-1]
                                                            - (sc['amortization'][-1] + sc['fixed_cash_costs'][-1]) / last_rev)
            applied.append({'id': o['id'], 'path': o['path'], 'scenario': s, 'original': orig, 'value': o['value'],
                            'reason': o['reason'], 'evidence_ids': o['evidence_ids']})
    # growth costs capital: an analyst growth path is charged the engine's own capital per unit of new revenue, unless the
    # analyst also set the capex paths (NVIDIA 2026-10-20: the override replaced growth but kept the reinvestment of the
    # original path, so changing the cap on the discarded path moved the value by about $20 a share)
    for s, keys in locked.items():
        sc = _scen(spec, s)
        if 'growth' in keys and not keys & {'growth_capex_pct', 'maintenance_capex_pct'} and sc.get('reinvestment_rule'):
            sc['growth_capex_pct'], sc['maintenance_capex_pct'] = _capex_for_path(spec, sc, sc['reinvestment_rule'])
            paths[s]['growth_capex_pct'] = list(sc['growth_capex_pct'])
            for r in rows:          # the recorded reinvestment assumption must show what the spec executes
                if r['id'] == 'reinvestment' and isinstance(r['values'].get(s), dict):
                    r['values'][s]['growth_capex_pct'] = list(sc['growth_capex_pct'])
            warn('growth_capex_recomputed', f'{s}: growth capex recomputed for the analyst growth path (capital per unit of new revenue '
                                            'unchanged)', 'low')
    by_id = {}
    for a in applied:
        by_id.setdefault(a['id'], []).append(a)
    for oid, items in sorted(by_id.items()):
        ev = items[0]['evidence_ids']
        srcs = [e for e in ev if e in fb['sources']] + [s for q in fb.get('qualitative', []) if q['id'] in ev for s in q['source_ids']]
        add(oid, 'analyst override: ' + items[0]['path'], 'analyst', {i['scenario']: {'original': i['original'], 'override': i['value']} for i in items},
            f"Analyst override of {items[0]['path']}: {items[0]['reason']} (evidence {', '.join(ev)}); original engine value recorded.",
            'The cited evidence is contradicted by a later filing or the override reason no longer holds.', srcs, provenance='model_assumption')
        for i in items:
            sc = _scen(spec, i['scenario'])
            sc['assumption_ids'].append(oid)
            sc['period_assumption_ids'].append(oid)

    # ---- assumptions, sources, underwriting (attached before funding runs the spec, refreshed after)
    if qa.get('status') == 'blocked':
        material.append({'id': 'data_blocked', 'status': 'unresolved', 'description': 'dataqa blocked critical fields: ' + ', '.join(qa.get('blocking') or [])})
    rv_ok = lambda aid: reviews.get(aid, {}).get('status') == 'reviewed'
    if route['cycle_peak']:
        if route['cycle_treatment']:
            warn('cyclical_peak_treatment', f"cyclical industry at a margin peak: analyst treatment '{route['cycle_treatment']}' (cycle_position)", 'low')
        else:
            warn('cyclical_peak_unconfirmed', f"cyclical industry with the latest margin {_pct(h['m0'])} at a peak and no completed cycle in the "
                                              f"history (through-the-cycle {_pct(cmar and cmar['mid'])}): valued around the cycle"
                                              + (' although the model card did not normalize it' if route['cycle_forced'] else '') +
                                              "; an analyst decides structural or cyclical (inputs['cyclical']) and a named reviewer accepts it",
                 'high' if route['cycle_forced'] else 'medium')
        material.append({'id': 'cyclical_peak_unconfirmed', 'status': 'resolved' if rv_ok('cycle_position') else 'unresolved',
                         'description': 'cyclical industry at a margin peak: structural or cyclical not established by an analyst with evidence',
                         'assumption_ids': ['cycle_position']})
    # interest expense far above any plausible rate on the debt found: debt lines are missing (T-Mobile 2026-10: long-term
    # debt under issuer tags, $6.1bn of short-term borrowings read against about $3.5bn of yearly interest, +74% 'upside')
    ie, d0 = h.get('interest_expense'), open_bal['debt'] or 0.
    if ie and ie > 0 and (ie / d0 if d0 > 0 else float('inf')) > DEBT_INCOMPLETE_RATE and ie > .005 * h['last_revenue']:
        warn('debt_incomplete', f"interest expense {ie:,.0f} is {('%.0f%%' % (100 * ie / d0)) if d0 > 0 else 'charged with no debt found'}"
                                f"{' of the debt found ' + format(d0, ',.0f') if d0 > 0 else ''}: debt lines are probably missing "
                                "(issuer tags, leases or financing obligations); supply the debt (capital structure or debt_like_claims)", 'high')
        material.append({'id': 'debt_incomplete', 'status': 'resolved' if rv_ok('open_balance') else 'unresolved',
                         'description': 'interest expense implies debt far above the debt read from the filings',
                         'assumption_ids': ['open_balance']})
    if route.get('cycle_override'):
        material.append({'id': 'cycle_treatment_override', 'status': 'resolved' if rv_ok('cycle_position') else 'unresolved',
                         'description': f"analyst treated a cycle the model card read from the history as {route['cycle_override']}",
                         'assumption_ids': ['cycle_position']})
    if route['moat_proxy'] and route['moat_weights']['base'] > 0:
        warn('moat_computed_proxy', f"moat unassessed: strong-moat rules blended at weight {route['moat_weights']['base']:.2f} for base and bull "
                                    f"from the computed record; value today without the proxy {f3(alt_moat)} (bear keeps the unassessed rules)", 'medium')
        material.append({'id': 'moat_proxy', 'status': 'resolved' if rv_ok('moat_basis') else 'unresolved',
                         'description': 'margins and returns rest on a computed moat proxy, not an analyst assessment', 'assumption_ids': ['moat_basis']})
    # a lender-shaped balance sheet (liabilities >= 85% of assets, no cost of revenue) is not an operating business whatever its label:
    # deposits and funding are operating liabilities, so an FCFF / EV bridge misprices it
    if lender:
        warn('possible_lender', f"liabilities are {lender['liabilities_to_assets']:.0%} of assets with no cost of revenue: a lender-shaped "
                                'balance sheet, which an operating FCFF forecast misprices (deposits and funding are operating liabilities)', 'high')
        material.append({'id': 'possible_lender', 'status': 'resolved' if rv_ok('classification') else 'unresolved', 'assumption_ids': ['classification'],
                         'description': 'lender-shaped balance sheet valued with an operating FCFF model: route to residual income '
                                        'or confirm the operating classification with evidence'})
    if sh_gap > 45:
        material.append({'id': 'opening_dates_differ', 'status': 'resolved' if reviews.get('bridge', {}).get('status') == 'reviewed' else 'unresolved',
                         'description': f'shares dated {sh_res.get("as_of")}, balance sheet {stmt}: bridge needed', 'assumption_ids': ['bridge']})

    def finalize():
        known = {r['id'] for r in rows}
        case_assumptions = []
        for r in rows:
            rv = reviews.get(r['id'], {})
            a = {'id': r['id'], 'kind': r['kind'], 'as_of': as_of, 'review_status': 'reviewed' if rv.get('status') == 'reviewed' else 'unreviewed',
                 'source_ids': r['source_ids'], 'rationale': r['rationale'], 'falsifier': r['falsifier']}
            if rv.get('status') == 'reviewed':
                a['reviewer'], a['review_note'] = rv['reviewer'], rv.get('note', '')
            r['review_status'] = a['review_status']
            case_assumptions.append(a)
        used = {x for r in rows for x in r['source_ids']} | {price['source_id']}
        spec['sources'] = _case_sources(fb, used, extra)
        spec['is_demo'] = any(fb['sources'][x]['kind'] == 'synthetic' for x in used if x in fb['sources'])
        spec['assumptions'] = case_assumptions
        fin = lambda s: [x for x in (f'financing_{s}', f'internal_funding_{s}') if x in known]
        spec['underwriting'] = {
            'opening_reconciliation': {
                'assumption_ids': ['open_balance', 'shares_point'], 'statement_as_of': stmt, 'share_basis': 'point_in_time_common_outstanding',
                'cash_and_equivalents': open_bal['cash'], 'restricted_cash': val('restricted_cash') or 0., 'short_term_investments': open_bal['sti'],
                'long_term_investments': open_bal['lti'], 'investment_haircut': 0, 'debt': open_bal['debt'],
                'common_shares_outstanding': open_bal['shares'], 'incremental_dilutive_shares': 0,
                'dilution_review': {'assumption_ids': ['dilution']}, 'claims_review': {'assumption_ids': ['claims']},
                'bridge_events': bridge, 'bridge_review': {'assumption_ids': ['bridge']},
                # evidenced analyst reclassifications of the validated debt (not events after the statement): review.py
                # compares the opening with validated debt plus these, and asks the reviewer to accept them
                'debt_adjustments': [a for a in ({'kind': 'operating_funding_excluded', 'amount': -op_fund},
                                                 {'kind': 'debt_like_claims_added', 'amount': claims_added}) if a['amount']],
                'investment_adjustments': [{'kind': 'non_operating_assets_added', 'amount': assets_added}] if assets_added else []},
            'scenario_reviews': {s: {'demand_and_capacity': {'assumption_ids': [f'growth_{s}', 'revenue_current_fy', 'growth_fade']
                                                             + (['cycle_normalization'] if cyc else [])},
                                     'funding_and_claims': {'assumption_ids': ['funding_policy', 'debt_interest', 'open_balance', 'claims'] + fin(s)},
                                     'competitive_response': {'assumption_ids': [f'margin_{s}', 'reinvestment', 'moat_basis']},
                                     'maturity': {'assumption_ids': [f'terminal_{s}', 'sbc_path', 'cost_of_equity']}} for s in SCEN},
            'consensus_review': {'status': 'unavailable', 'assumption_ids': ['consensus'], 'comparisons': []},
            'material_issues': deepcopy(material)}
        if B[1] in cons:
            e = cons[B[1]]
            spec['underwriting']['consensus_review'] = {
                'status': 'compared', 'assumption_ids': ['consensus'],
                'comparisons': [{'id': f'revenue_{B[1]}', 'scenario': 'base', 'metric': 'revenue', 'period_start': B[0], 'period_end': B[1],
                                 'as_of': e.get('as_of') or as_of, 'available_at': e.get('as_of') or as_of, 'currency': fb['company']['currency'],
                                 'unit': 'absolute', 'external_value': float(e['value']), 'external_basis': 'gaap_revenue',
                                 'model_basis': 'gaap_revenue', 'assumption_ids': ['consensus']}]}

    finalize()
    # ---- funding: investments, SBC settled in shares, assumed debt within capacity, assumed equity; then the payout schedule
    allow = inputs.get('financing', 'assumed') != 'none'
    inv0 = open_bal['sti'] + open_bal['lti']
    fund = _fund(spec, policy, allow, r_new, meta['interest_existing'], open_bal['debt'], locked, inv0)
    if 'error' in fund:
        return dict(empty('invalid_spec', fund['error'], method), spec=spec, assumptions=rows)
    frows = {r['name']: r['forecast'] for r in fund['result']['scenarios']}
    for s in SCEN:
        f = fund['info'][s]
        internal = (f"investments liquidated {f['investments_liquidated']:,.0f}" if f['investments_liquidated'] else '') + \
                   ((', ' if f['investments_liquidated'] else '') + f"SBC settled in {f['sbc_settlement_shares']:,.0f} new shares "
                    f"({f['sbc_settled_in_shares']:,.0f} of the cash-equivalent charge)" if f['sbc_settled_in_shares'] else '')
        if f['status'] == 'assumed_external_financing':
            first = min([d['period_end'] for d in f['draws'] + f['equity']])
            own = frows[s][-1]['original_ownership']
            add(f'financing_{s}', f'{s} assumed financing', 'model', {s: {'debt': f['draws'], 'equity': f['equity'], 'investments': f['liquidation'],
                                                                          'sbc_settled': f['settled'], 'new_shares': f['new_financing_shares']}},
                f"Cash would fall below the minimum operating balance from {first}" + (f" even after internal sources ({internal})" if internal else '') +
                f": external financing of {f['total_assumed']:,.0f} is ASSUMED, not committed: debt {f['debt_assumed']:,.0f} at {r_new:.2%} "
                f"(only within {DEBT_CAPACITY:.0f}x EBITDA before SBC) and equity {f['equity_raised']:,.0f} at the quote "
                f"{spec['quote']['price']:,.2f} less {EQUITY_FEE:.0%} fees ({f['new_financing_shares']:,.0f} new shares; today's holders keep "
                f"{own:.1%} of the company by {spec['boundaries'][-1]}). The dilution is priced into per-share values; a raise at a lower price "
                'dilutes more.',
                f"No committed financing (facility, offering or partner prepayment) exists before {first}, or terms are worse "
                f"(debt above {r_new:.2%}, equity below {spec['quote']['price']:,.2f}).", _latest_src(fb, 'cash', 'cfo') + [RULES])
            _scen(spec, s)['period_assumption_ids'].append(f'financing_{s}')
            warn('financing_assumed', f"{s}: {f['total_assumed']:,.0f} of external financing assumed (not committed) from {first}"
                                      f" (debt {f['debt_assumed']:,.0f}, equity {f['equity_raised']:,.0f})", 'high')
            material.append({'id': f'assumed_financing_{s}', 'description': f'{s} plan depends on assumed (uncommitted) financing',
                             'status': 'resolved' if reviews.get(f'financing_{s}', {}).get('status') == 'reviewed' else 'unresolved',
                             'assumption_ids': [f'financing_{s}']})
        elif f['status'] == 'unfunded':
            why = f.get('reverted') or 'external financing assumptions are disabled (inputs financing=none)'
            warn('funding_blocked', f"{s}: cash falls below the minimum from {f['first_gap']}; {why}; value_company withholds this scenario's values", 'high')
        if internal and f['status'] != 'assumed_external_financing':
            first = min([d['period_end'] for d in f['liquidation'] + f['settled']])
            add(f'internal_funding_{s}', f'{s} internal funding', 'model', {s: {'investments': f['liquidation'], 'sbc_settled': f['settled']}},
                f"Cash would fall below the minimum operating balance from {first}; closed with internal sources, no external financing: "
                f"{internal}. Settling SBC in shares makes the dilution explicit instead of borrowing to pay a non-cash charge.",
                'The investments are restricted, pledged or illiquid, or the company borrows or raises equity to fund operations.',
                bal_srcs + [RULES])
            _scen(spec, s)['period_assumption_ids'].append(f'internal_funding_{s}')
            warn('internal_funding', f"{s}: shortfall closed internally ({internal})", 'low')
        if f['retained']:
            warn('payout_paused', f"{s}: distributions paused before {', '.join(f['retained'])} to keep the minimum cash balance", 'low')
    if route['method'] == 'scenario_survival_fcff':
        warn('survival_model', 'scenario survival model: values depend on funding and on reaching profitability; lower confidence', 'high')
    unknown_reviews = sorted(set(reviews) - {r['id'] for r in rows} - {f'{x}_{s}' for x in ('financing', 'internal_funding') for s in SCEN})
    if unknown_reviews:
        warn('unknown_review_ids', 'reviews supplied for ids not in this forecast: ' + ', '.join(unknown_reviews), 'low')
    finalize()
    base_external = fund['info']['base']['status'] in ('assumed_external_financing', 'unfunded')

    # ---- capital structure: debt weight and issue prices from the model's own values (fixed point), Ke relevered, terminal WACC
    excess0 = max(0., open_bal['cash'] - _scen(spec, 'base')['minimum_cash'][0])
    # an evidenced private stake is an asset, not liquidity: it does not offset debt in the leverage that sets the cost of capital
    net_debt = max(0., open_bal['debt'] - (inv0 - assets_added) - excess0)
    kd_e, kd_p = rf + DEBT_EXPECTED_PREMIUM, meta['interest_existing']
    lev = _solve_capital(spec, P, ku, ku_t, kd_e, kd_p, net_debt, ov_ke)
    if lev['status'] in ('equity_not_positive', 'capped', 'not_converged'):
        warn('leverage_' + lev['status'], f"debt weight {lev['debt_weight']:.0%} ({lev['status']}): the model's own equity value is too small "
                                          'to carry the debt; the cost of equity is relevered at the cap, so the value is set by the cap '
                                          'constant, not by a fixed point', 'high')
        material.append({'id': 'leverage_at_cap', 'status': 'resolved' if reviews.get('cost_of_equity', {}).get('status') == 'reviewed' else 'unresolved',
                         'description': f"leverage relevering at the {W_MAX:.0%} debt-weight cap ({lev['status']}): the equity value is a "
                                        'cap artefact, not a valuation, until the capital structure is reviewed', 'assumption_ids': ['cost_of_equity']})
    # issue prices: who bears the dilution, and whether the per-share value depends on the quote
    dependent = {}
    for s in SCEN:
        f = fund['info'][s]
        ip = lev['issue_prices'][s]
        f['issue_price_rule'] = 'min(quote, scenario value per share right after the issue)'
        f['issue_prices'] = ip
        f['price_dependent'] = any(x['quote_binding'] for x in ip)
        if f['status'] == 'self_funded' and f['sbc_settled_in_shares']:
            f['status'] = 'share_settled'          # self-funded in cash, but part of SBC paid in new shares (dilution at the issue price)
        if f['price_dependent']:
            dependent[s] = ip
    rows_after, _ = _run(spec)
    for s in SCEN:
        f = fund['info'][s]
        rs = next(r for r in rows_after['scenarios'] if r['name'] == s) if rows_after else None
        if rs:
            f['new_financing_shares'] = sum(r['new_financing_shares'] for r in rs['forecast'])
            f['sbc_settlement_shares'] = sum(r.get('sbc_settlement_shares', 0) for r in rs['forecast'])
    second = None
    if dependent.get('base'):
        x = deepcopy(spec)
        alt = _solve_capital(x, deepcopy(P), ku, ku_t, kd_e, kd_p, net_debt, ov_ke, cap_at_quote=False)
        r_alt, _ = _run(x)
        second = r_alt['annual_values'][0]['base'] if r_alt else None
        fund['info']['base']['value_if_issued_at_own_value'] = second
    if dependent:
        warn('value_depends_on_quote', '; '.join(f"{s}: new shares issued at the quote {spec['quote']['price']:,.2f} in "
                                                 f"{', '.join(x['period_end'] for x in ip if x['quote_binding'])} (below the scenario's own value)"
                                                 for s, ip in dependent.items()) +
             ': per-share value falls if the price falls' + (f'; base value if issued at its own value {second:,.2f}' if second else ''), 'medium')
    ke_parts = _ke_parts(ke_parts, ku, lev)
    ke = P['base']['ke']
    floored = lev['ke'] <= ku + 1e-12 and lev['d_to_e'] > 0
    add('cost_of_equity', 'cost of equity and terminal WACC', 'model',
        {'cost_of_equity': {s: P[s]['ke'] for s in SCEN}, 'table_rate': ku, 'unlevered': lev['unlevered'],
         'terminal_cost_of_equity': {s: P[s]['ke_terminal'] for s in SCEN}, 'terminal_table_rate': ku_t,
         'terminal_dropped': [a['factor'] for a in ke_parts['terminal']['dropped']],
         'terminal_wacc': {s: P[s]['wacc'] for s in SCEN}, 'debt_weight': lev['debt_weight'], 'net_debt': net_debt,
         'model_equity': lev['model_equity'], 'kd_expected': kd_e, 'kd_promised': kd_p, 'issue_prices': lev['issue_prices']},
        f"Ke {ke:.2%}: table rate {ku:.2%} (risk-free {rf:.2%} + ERP {erp:.2%} + business-risk add-ons " +
        (', '.join(f"{a['factor']} {a['value']:+.2%}" for a in ke_parts['adjustments'] if a['factor'] != 'leverage_relevered') or 'none') +
        f") is the cost of equity at market-average leverage (D/E {MARKET_DE:.2f}); unlevered Ku {lev['unlevered']:.2%} with the expected "
        f"return on debt {kd_e:.2%} (risk-free + {DEBT_EXPECTED_PREMIUM:.0%}; the coupon {kd_p:.2%} sets interest and the tax shield, never the "
        f"discount rate); Ke = max(table, Ku + (Ku - Kd_e) x D/E {lev['d_to_e']:.1%}) = {ke:.2%}" +
        (' (below-market leverage keeps the table rate: conservative, no credit for a lower rate)' if floored or lev['d_to_e'] == 0 else '') +
        f". Net debt {net_debt:,.0f} (debt - investments - cash above the minimum) against the model's own base equity value "
        f"{lev['model_equity'] or 0:,.0f} gives debt weight {lev['debt_weight']:.1%} ({lev['status']}, {lev['iterations']} iterations): a "
        "fixed point on the model's value, never the quote, so the discount rate does not move with the price" +
        ('' if not dependent else ' (per-share values of ' + ', '.join(dependent) + ' still depend on the quote: new shares are issued at '
         'the quote below their own value)') +
        f". Terminal WACC {P['base']['wacc']:.2%} = E/V x terminal Ke {P['base']['ke_terminal']:.2%} + D/V x (Kd_e - Kd_promised x tax), "
        'floored at terminal growth + 3 points; the terminal Ke is ' +
        ('the analyst override (used for the explicit years and the terminal alike)' if ov_ke else
         f"the mature table {ku_t:.2%}" +
         (' (the table without ' + ', '.join(f"{a['factor']} {a['value']:+.2%}" for a in ke_parts['terminal']['dropped']) +
          ': the forecast is mature by its terminal date, so the perpetuity does not carry the risk of a phase the model has ended)'
          if ke_parts['terminal']['dropped'] else ' (no stage or funding add-on to drop: the same table as the explicit years)') +
         f", relevered at the same D/E; the explicit years and the roll-back of the terminal value keep Ke {ke:.2%}") +
        f". External-funding add-on {'applied' if external_assumed else 'not applied'} from the executed base funding result (explicit years). "
        'Same Ke, and the same terminal rate, in all scenarios.',
        'Long-term risk-free rate moves by more than 1 point, the company changes its leverage materially, or the base plan '
        'starts or stops needing external funding.', rf_ids + erp_ids + [RULES])
    for s in SCEN:
        term_row(s)
    finalize()

    # ---- preview execution (the pipeline executes again; this run proves the spec is valid)
    result, err = _run(spec)
    if err:
        return dict(empty('invalid_spec', err, method), spec=spec, assumptions=rows)
    today = result['annual_values'][0]
    terminal_share = {r['name']: r.get('terminal_share_of_present_value') for r in result['scenarios']}
    for s, v in terminal_share.items():
        if v is not None and v > .8:
            warn('terminal_dominant', f'{s}: {v:.0%} of present value comes from the terminal value', 'medium')
    if all(today.get(k) is not None for k in SCEN) and not today['bear'] <= today['base'] <= today['bull']:
        warn('scenario_values_cross', 'today: bear <= base <= bull does not hold; labels were not re-sorted', 'high')
    preview = {'status': result['status'], 'input_sha256': result['input_sha256'],
               'today': {k: today.get(k) for k in SCEN}, 'quote': today['current_quote'],
               'terminal_share_of_present_value': terminal_share,
               'funding_status': {r['name']: r['status'] for r in result['scenarios']},
               'audit_issue_codes': sorted({i['code'] for i in result['underwriting_audit']['issues']}),
               'eligible_for_research_synthesis': result['underwriting_audit']['eligible_for_research_synthesis'],
               'note': 'Internal consistency run of this spec; pipeline.py executes value_company itself. Conditional values, not price forecasts.'}
    path_out = {s: [{'end': b, 'growth': (None if j == 0 else paths[s]['growth'][j - 1]), 'revenue_rate': _r(paths[s]['rates'][j], 0),
                     'operating_margin': _r(1 - _scen(spec, s)['cash_cost_ratio'][i] - _scen(spec, s)['sbc_pct'][i] - _scen(spec, s)['depreciation_pct'][i]),
                     'sbc_pct': _scen(spec, s)['sbc_pct'][i], 'growth_capex_pct': _scen(spec, s)['growth_capex_pct'][i],
                     'payout_fraction': _scen(spec, s)['payout_fraction'][i], 'debt_draw': _scen(spec, s)['debt_draw'][i],
                     'equity_proceeds': _scen(spec, s)['equity_proceeds'][i]}
                    for j, (b, i) in enumerate(zip(B, fy_pidx))] for s in SCEN}
    anchors = {'last_fiscal_year_end': h['last_end'], 'last_fiscal_year_revenue': h['last_revenue'], 'fiscal_years': h['n_years'],
               'ttm': h['ttm'], 'year_to_date': h['ytd'],
               'revenue_adjusted_for_one_offs': ({**dict(zip(h['ends'], h['revenue'])), 'ttm': h['ttm']['revenue']}
                                                 if inputs.get('nonrecurring') else None),
               'current_fiscal_year': {'end': B[0], 'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'basis': meta['cur_basis'], 'kind': cur_kind},
               'stub': {'start': as_of, 'end': B[0], 'years': cal['stub_years'], 'annual_rate': meta['stub_rate'], 'basis': meta['stub_basis']},
               'growth': {'last': h['g_last'], 'cagr_3y': h['cagr_3y'], 'cagr_5y': h['cagr_5y'], 'recent_annualized': h['g_recent'],
                          'trailing_anchor': h['g_trailing'], 'formula': h['g_trailing_formula'], 'volatility': h['growth_volatility'],
                          'scale_cap': h['g_scale_cap'], 'consensus': {b: e['value'] for b, e in sorted(cons.items())}, 'scenario_spread': meta['spread']},
               'cycle': cyc, 'outside_view': outside,
               'operating_margin': {'current': h['m0'], 'basis': h['m0_basis'], 'best_3y': h['m_best3'], 'history': h['m_all']},
               'sbc_pct': h['sbc_pct'], 'sbc_basis': h['sbc_basis'], 'da_pct': h['da_pct'], 'capex_pct_3y': h['capex_pct'],
               'sales_to_capital': {'history': meta['s2c_history'], 'used': meta['s2c_used'], 'type_range': meta['s2c_range'],
                                    'terminal': {s: P[s]['s2c_terminal'] for s in SCEN}},
               'working_capital_ratio': {'value': fixed['wc'], 'basis': h['wc_basis']}, 'deferred_revenue_ratio': fixed['dr'],
               'tax': {'effective_3y': meta['tax_effective'], 'used': meta['tax_used']}, 'interest_rate_existing': meta['interest_existing'],
               'roic': h['roic'], 'payout_history_3y': h['payout_history'], 'net_debt_to_ebitda': h['nd_ebitda'], 'opening': open_bal,
               'reinvestment_identity': fixed['identity'],
               'route': {k: route[k] for k in ('method', 'business_model', 'stage', 'archetype', 'cyclical', 'cyclical_evidence', 'capital_intensity',
                                               'funding', 'moat', 'moat_basis', 'moat_weights', 'cycle_path', 'cycle_peak', 'cycle_treatment',
                                               'cycle_history')},
               'calendar': {k: cal[k] for k in ('boundaries', 'periods', 'interim', 'fifth_anniversary', 'report_dates', 'fiscal_year_end', 'report_rule')}}
    json.dumps(spec, allow_nan=False)
    return {'forecast_version': FORECAST_VERSION, 'status': 'built', 'reason': None, 'method': method, 'spec': spec,
            'spec_sha256': _sha(spec), 'assumptions': rows, 'anchors': anchors, 'paths': path_out,
            'cost_of_equity': {'bear': P['bear']['ke'], 'base': P['base']['ke'], 'bull': P['bull']['ke'], 'components': ke_parts,
                               'terminal_cost_of_equity': {s: P[s]['ke_terminal'] for s in SCEN},
                               'terminal_wacc': {s: P[s]['wacc'] for s in SCEN}},
            'funding': fund['info'], 'overrides': applied, 'preview': preview, 'warnings': warnings,
            'moat_proxy': {'weights': route['moat_weights'], 'basis': route['moat_basis'], 'value_today_with_proxy': preview['today'],
                           'value_today_without_proxy': alt_moat} if route['moat_proxy'] and route['moat_weights']['base'] > 0 else None,
            '_external': base_external, '_external_assumed': bool(external_assumed)}


# ---------------------------------------------------------------- what must be true

def _bisect(f, lo, hi, target, increasing=True, steps=48):
    vlo, vhi = f(lo), f(hi)
    # an invalid or unfunded extreme narrows the domain toward the valid end instead of ending the search: MercadoLibre's
    # growth (+50% unfunded) and margin (0.1% invalid) searches had both reported 'not reachable' for a price near the base value
    if (vlo is None) != (vhi is None):
        good, bad, vgood = (lo, hi, vlo) if vhi is None else (hi, lo, vhi)
        for _ in range(16):                 # bisection on validity: the valid end walks to the edge of the valid domain
            mid = (good + bad) / 2
            vm = f(mid)
            if vm is None:
                bad = mid
            else:
                good, vgood = mid, vm
        if vhi is None:
            hi, vhi = good, vgood
        else:
            lo, vlo = good, vgood
    if vlo is None or vhi is None:
        return None, 'invalid_search_domain', (vlo, vhi)
    if not (min(vlo, vhi) <= target <= max(vlo, vhi)):
        return None, 'not_bracketed', (vlo, vhi)
    if increasing is None:
        increasing = vhi >= vlo
    for _ in range(steps):
        mid = (lo + hi) / 2
        v = f(mid)
        if v is None:
            return None, 'invalid_search_domain', (vlo, vhi)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, 'solved', (vlo, vhi)


def _value(spec):
    r, err = _run(spec)
    return None if err else r['annual_values'][0]['base']


def what_must_be_true(spec, price):
    """Levers that make the base-case value today equal the price, one at a time, everything else fixed.

    Sensitivities of THIS spec, not market expectations: growth (the first five fiscal-year growth rates shifted together), mature
    margin (cash-cost ratio shift ramped in over five years, terminal margin moved by the same amount),
    capital efficiency (growth capex and terminal reinvestment scaled) and the return the price implies
    (valuation_tools.implied_cost_of_equity)."""
    if not spec:
        return []
    s = deepcopy(spec)
    s['quote'] = dict(s['quote'], price=number(price, 'price', 1e-9))
    base = _scen(s, 'base')
    stub = 1 if s.get('stub_annual_revenue_rate') is not None else 0
    rows = []

    def side(rng):
        vals = [v for v in rng or () if v is not None]
        if len(vals) < 2:
            return ' (the search ran into an invalid or unfunded case)'
        where = 'above' if min(vals) > s['quote']['price'] else 'below'
        return (f': across the searched range the base value stays {where} the price ({min(vals):,.2f} to {max(vals):,.2f}), '
                'so this lever alone does not explain the price')

    def row(lever, label, status, required, anchor, meaning, unit, detail=None):
        rows.append({'lever': lever, 'label': label, 'status': 'solved' if status == 'solved' else 'not_reachable',
                     'required': required, 'anchor': anchor, 'unit': unit, 'meaning': meaning,
                     'detail': detail or {'search_status': status}})

    # growth: the first five fiscal-year steps shifted together (a continuation period of a split year has no step); later
    # years and terminal growth unchanged, so the answer is the five-year growth the price needs, as the label says
    n = len(s['boundaries'])
    starts = [s['as_of']] + s['boundaries'][:-1]
    interim = set(s.get('interim_boundaries') or [])
    steps = [i for i in range(stub, n) if starts[i] not in interim]
    gl = base['growth'] if isinstance(base['growth'], list) else [base['growth']] * (n - stub)
    first5 = [i - stub for i in steps[:5]]
    g_anchor = sum(gl[i] for i in first5) / len(first5)

    rule = base.get('reinvestment_rule')

    def grow(shift):
        x = deepcopy(s)
        sc = _scen(x, 'base')
        sc['growth'] = [v + shift if i in first5 else v for i, v in enumerate(gl)]
        if rule:          # growth costs capital: the engine's own capital per unit of new revenue on the shifted path
            sc['growth_capex_pct'], sc['maintenance_capex_pct'] = _capex_for_path(x, sc, rule)
        return _value_paused(x)
    lo_g = max(-.3, -.85 - min(gl[i] for i in first5))
    hi_g = next((h for h in (.5, .3, .2, .12, .08, .05, .03) if grow(h) is not None), .5)   # above it the path is unfunded
    d, st, rng = _bisect(grow, lo_g, hi_g, s['quote']['price'], None)
    capital_note = ('growth capex recomputed from the engine\'s capital per unit of new revenue (growth is not free)' if rule else
                    'capital needs as a fixed share of revenue')
    if d is not None:
        req = g_anchor + d
        need = base_rates.cagr([gl[i] + d for i in first5], len(first5))
        view = (base_rates.outside_view(s.get('last_full_year_revenue'), {5: need})
                if s.get('currency') == 'USD' and len(first5) == 5 else None)
        five = (view or {}).get('horizons', {}).get(5)
        row('growth', 'average revenue growth, next five fiscal years', 'solved', req, g_anchor,
            f'At {price:,.2f} the base case needs average revenue growth of {req:.1%} a year over the next five fiscal years '
            f'(base {g_anchor:.1%}), with {capital_note}; later years, terminal growth, margins and the discount rate unchanged.'
            + (f" Outside view: {five['share_at_least']:.0%} of companies of this size ({view['reference_class']} in 2015 dollars) grew "
               f"at least {five['real_cagr']:.1%} a year after inflation over five years in 1950-2015 (class median "
               f"{five['class_median_real']:.1%})." if five else ''), 'ratio',
            {'search_status': 'solved', 'growth_shift': d, 'years_shifted': len(first5), 'range_values': rng, 'capital': capital_note,
             'base_rate': view,
             'limitation': 'Shift of the first five fiscal-year growth rates of this spec, everything else fixed; a sensitivity, not market consensus.'})
    else:
        row('growth', 'average revenue growth, next five fiscal years', st, None, g_anchor,
            f'No five-year growth shift between {lo_g:+.0%} and {hi_g:+.0%} reconciles the base case with this price ({capital_note}'
            + ('; faster growth than that is unfunded on these terms' if hi_g < .5 else '') + ')' + side(rng) + '.', 'ratio',
            {'search_status': st, 'range_values': rng, 'capital': capital_note, 'funded_up_to_shift': hi_g})
    # mature margin: cash-cost ratio lowered by delta, ramped in over five full years; terminal margin + delta
    t_m = base['terminal']['operating_margin']
    ccr = base['cash_cost_ratio']
    ramp = [0. if (stub and i == 0) else min(1., sum(1 for j in steps if j <= i) / 5) for i in range(len(ccr))]

    def margin(delta):
        x = deepcopy(s)
        sc = _scen(x, 'base')
        sc['cash_cost_ratio'] = [c - delta * w for c, w in zip(ccr, ramp)]
        sc['terminal']['operating_margin'] = t_m + delta
        if any(c < 0 or c > 2 for c in sc['cash_cost_ratio']) or not 0 < t_m + delta <= 1:
            return None
        return _value(x)
    hi = min(.30, min(c for c, w in zip(ccr, ramp) if w) - 1e-6, 1 - t_m)
    lo = max(-.25, -t_m + .001)
    d, st, rng = _bisect(margin, lo, hi, s['quote']['price'], True)
    row('mature_margin', 'mature operating margin after SBC', st, None if d is None else t_m + d, t_m,
        (f'At {price:,.2f} the base case needs a mature operating margin of {t_m + d:.1%} (base {t_m:.1%}), reached over five years, '
         'with growth and capital needs unchanged.') if d is not None else
        f'No mature margin between {t_m + lo:.1%} and {t_m + hi:.1%} reconciles the base case with this price' + side(rng) + '.', 'ratio',
        {'search_status': st, 'margin_shift': d, 'range_values': rng})
    # capital efficiency: growth capex and terminal reinvestment (g / ROIC) scaled by k
    gc, roic, gt = base['growth_capex_pct'], base['terminal']['roic'], base['terminal']['growth']

    def capital(k):
        x = deepcopy(s)
        sc = _scen(x, 'base')
        sc['growth_capex_pct'] = [min(1., c * k) for c in gc]
        sc['terminal']['roic'] = min(1., roic / k) if k > 0 else 1.
        if sc['terminal']['roic'] < gt:
            return None
        return _value_paused(x)
    k_hi = min(6., roic / gt - 1e-6) if gt > 0 else 6.
    k, st, rng = _bisect(capital, 0., k_hi, s['quote']['price'], False)
    row('capital_efficiency', 'growth investment per unit of new revenue (x base)', st, k, 1.,
        (f'At {price:,.2f} the base case still works if growth needs {k:.2f}x the capital assumed in base (explicit growth capex and '
         f'terminal reinvestment; terminal ROIC {min(1., roic / k) if k else 1:.0%} vs {roic:.0%}).') if k is not None else
        f'No scaling of growth investment between zero and {k_hi:.1f}x (terminal ROIC down to terminal growth) reconciles the base '
        'case with this price' + side(rng) + '.', 'x',
        {'search_status': st, 'range_values': rng})
    # return implied by the price
    case = build_case(s)
    r = implied_cost_of_equity(case)
    ke = base['cost_of_equity']
    if r['status'] == 'solved':
        row('cost_of_equity', 'annual return implied by the price (base cash flows)', 'solved', r['implied_cost_of_equity'], ke,
            f"If the base-case cash flows happen, buying at {price:,.2f} returns about {r['implied_cost_of_equity']:.1%} a year "
            f'(the model requires {ke:.1%}). Not a forecast of the market price.', 'ratio',
            {'search_status': 'solved', 'limitation': r['limitation']})
    else:
        row('cost_of_equity', 'annual return implied by the price (base cash flows)', r['status'], None, ke,
            'The implied return lies outside the searched range (Ke -8 to +12 points)' + side(r.get('range_values')) + '.', 'ratio',
            {'search_status': r['status'], 'limitation': r['limitation']})
    return rows
