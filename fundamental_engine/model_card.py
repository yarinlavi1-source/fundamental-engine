"""Model card: how the company makes money, decided before any forecast or valuation.

Why: a mature software franchise, an AI-infrastructure builder, a bank, a cyclical and a pre-revenue
biotech need different questions, drivers and valuation methods (docs/ARCHITECTURE.md, stage 3a).
Business understanding comes first; the forecast (forecast.py) and the valuation route
(valuation.ROUTES archetype) are derived from this card, never the other way round.

What the card does, deterministically and with stated reasons:
- business model: sector/industry text + financial signature + analyst override (reason mandatory,
  kept next to the computed value); hybrids get secondary types; 'unclassified' is a legitimate
  answer when neither text nor numbers support a listed type.
- stage: rules on scale, growth, profitability and profit history. Fixes known defects of
  profile.classify: a large company with one loss year is not 'young growth' (scale and a record of
  profits make it mature-with-a-loss or a turnaround); sub-annual data never relabels the stage;
  a missing or stale latest year is stated, not silently replaced by an older year.
- cyclicality, capital intensity, funding (cash runway), playbooks, driver tree with data status,
  valuation method table (type x stage) with cross-checks and limitations, an understanding
  checklist, qualitative dimensions (unknown is legitimate; no blended score) and completeness.

A promising grower is not rejected for lacking profit: it is routed to a funding-and-dilution
scenario model and asked for paid adoption, milestone progress and an explicit failure scenario.
A bank or insurer is never routed to FCFF; an unsupported sector is never relabelled to force a
generic model. Pure function of (fb, an, qa, inputs); reads facts only through facts.py accessors.
"""
from datetime import date
from statistics import mean, pstdev
import re
from . import facts as F
from .history import cagr, acquisition_years, organic_cagr, txt
from .valuation import ROUTES
from . import profile

CARD_VERSION = 1
TYPES = ('subscription_software', 'platform_marketplace', 'semiconductor', 'hardware_manufacturing',
         'capital_intensive_infrastructure', 'consumer_brand', 'industrial', 'financials', 'pre_revenue_biotech',
         'conglomerate', 'reit', 'commodity')
UNCLASSIFIED = 'unclassified'
STAGES = ('early', 'growth', 'maturing', 'mature', 'decline', 'turnaround')
METHODS = ('fcff_annual_path', 'normalized_cycle_fcff', 'scenario_survival_fcff', 'residual_income', 'nav', 'rnpv',
           'sotp', 'not_reliably_valuable')
OPERATING_METHODS = {'fcff_annual_path', 'normalized_cycle_fcff', 'scenario_survival_fcff'}
METHOD_ROUTE = {**{m: 'operating' for m in OPERATING_METHODS}, 'residual_income': 'residual_income', 'nav': 'nav',
                'rnpv': 'rnpv', 'sotp': 'sotp'}
METHOD_ARCHETYPE = {'residual_income': 'financials', 'nav': 'reit', 'rnpv': 'biotech', 'sotp': 'conglomerate'}
ARCHETYPE = {'subscription_software': 'software', 'platform_marketplace': 'platform', 'semiconductor': 'industrial',
             'hardware_manufacturing': 'industrial', 'capital_intensive_infrastructure': 'infrastructure',
             'consumer_brand': 'consumer', 'industrial': 'industrial', 'financials': 'financials',
             'pre_revenue_biotech': 'biotech', 'conglomerate': 'conglomerate', 'reit': 'reit', 'commodity': 'commodity',
             UNCLASSIFIED: 'nonfinancial'}
PLAYBOOKS = {'subscription_software': ['software'], 'platform_marketplace': ['platform'], 'semiconductor': ['industrial'],
             'hardware_manufacturing': ['industrial'], 'capital_intensive_infrastructure': ['infrastructure'],
             'consumer_brand': ['consumer'], 'industrial': ['industrial'], 'financials': ['financials'],
             'pre_revenue_biotech': ['biotech'], 'conglomerate': ['conglomerate'], 'reit': ['infrastructure'],
             'commodity': ['industrial'], UNCLASSIFIED: []}
LEVELS = ('strong', 'adequate', 'weak', 'unknown')
DIMENSIONS = ('moat', 'switching_costs', 'pricing_power', 'retention', 'customer_concentration', 'market_size',
              'competition', 'substitutes', 'management', 'capital_allocation', 'governance', 'regulation',
              'technology_ai', 'geopolitics')
QUESTIONS = (
    ('why_customers_pay', 'Why do customers pay, and what problem does the product solve that they value?', True),
    ('why_they_dont_switch', 'Why do customers not switch to a cheaper, newer or in-house alternative?', True),
    ('what_proves_thesis_wrong', 'What observable evidence would prove the thesis wrong, and by when?', True),
    ('who_funds_growth', 'Who funds growth (operating cash, debt, new shares) and at what dilution to current holders?', True),
    ('key_competitors_substitutes', 'Who are the key competitors and substitutes, and what do they do better?', False),
    ('customer_concentration', 'How concentrated are customers, and what happens if the largest one leaves?', False),
    ('management_incentives', 'How is management paid, and does it reward per-share value or size?', False),
    ('what_must_happen_to_succeed', 'What must happen (milestones, adoption, margins) for the thesis to succeed?', True),
    ('what_could_cause_failure', 'What could cause failure or permanent loss of capital (explicit failure scenario)?', True),
)

# Reporting-currency thresholds (USD). Applied only when the FactBook currency is USD; otherwise scale is
# not used and the stage reasons say so (no silent FX conversion).
PRE_REVENUE = 1e7            # below this there is no commercial revenue base
PRE_REVENUE_BIOTECH = 1e8    # biotech/pharma below this (or R&D >= revenue) is pre-commercial
SMALL_EARLY = 5e8            # loss-making and smaller than this: still early
ESTABLISHED = 1e9            # scale at which a record of profits outweighs one loss year
VERY_LARGE = 2e10            # very large, very profitable: 15-30% growth usually fades (profile.large_mature)
STALE_ANNUAL_DAYS = 465      # fiscal year end + ~15 months: a newer 10-K should exist
RUNWAY_YEARS = 3.0           # cash runway below this needs external funding

# Text rules: first match per field; the industry decides and the sector is only a fallback. Order =
# specificity; '(?<![a-z])' prefix boundary avoids 'oil' in 'soil'; text and keywords are compared after
# clean_text (dashes -> spaces, whitespace collapsed: 'Auto - Manufacturers' == 'auto manufacturers').
# Fee-based payment networks/exchanges are platforms; balance-sheet lenders and insurers are financials
# (conservative: 'credit services' lists both, so it maps to financials; an analyst override can clear it).
TEXT_RULES = (
    (('industrial conglomerate', 'conglomerate', 'multi-sector holding', 'diversified holding'), 'conglomerate'),
    (('mortgage reit', 'reit mortgage', 'mortgage real estate'), 'financials'),
    (('transaction & payment', 'payment processing', 'financial exchanges', 'exchanges & data', 'stock exchanges',
      'financial data'), 'platform_marketplace'),
    (('real estate services', 'real estate brokerage'), 'industrial'),
    (('bank', 'thrift', 'savings institution', 'mortgage finance', 'consumer finance', 'credit services', 'insurance',
      'reinsurance', 'capital markets', 'investment banking', 'brokerage', 'asset management', 'custody',
      'specialty finance', 'specialized finance', 'diversified financial', 'financial services', 'financials'), 'financials'),
    (('reit', 'real estate investment trust', 'equity real estate', 'real estate operating', 'real estate development',
      'real estate holding', 'diversified real estate', 'real estate'), 'reit'),
    (('biotechnology', 'biotech', 'pharmaceutical', 'drug manufacturer'), 'pre_revenue_biotech'),
    (('semiconductor',), 'semiconductor'),
    (('application software', 'systems software', 'software'), 'subscription_software'),
    (('interactive media', 'internet content', 'internet & direct marketing', 'internet retail', 'marketplace',
      'online travel'), 'platform_marketplace'),
    (('data center', 'internet services & infrastructure', 'utilities', 'utility', 'independent power',
      'renewable electricity', 'telecom', 'wireless', 'cable', 'tower', 'storage & transportation', 'midstream',
      'pipeline', 'airport', 'toll road', 'railroad'), 'capital_intensive_infrastructure'),
    (('oil & gas', 'oil and gas', 'integrated oil', 'exploration & production', 'refining', 'coal', 'metals & mining',
      'mining', 'gold', 'silver', 'copper', 'steel', 'aluminum', 'uranium', 'commodity chemicals', 'fertilizer',
      'agricultural products', 'paper & forest', 'forest products', 'construction materials'), 'commodity'),
    (('technology hardware', 'computer hardware', 'electronic equipment', 'electronic components',
      'communications equipment', 'consumer electronics', 'automobile manufacturer', 'auto manufacturer', 'automobiles',
      'health care equipment', 'medical device', 'medical instrument', 'scientific & technical instruments',
      'storage & peripherals'), 'hardware_manufacturing'),
    (('household products', 'personal products', 'personal care', 'beverage', 'food', 'apparel', 'footwear',
      'textile', 'luxury', 'restaurant', 'retail', 'tobacco', 'leisure', 'hotel', 'casino', 'consumer staples',
      'consumer discretionary', 'consumer defensive', 'consumer cyclical', 'distiller', 'brewer', 'soft drink',
      'confectioner', 'household durables', 'home furnishing'), 'consumer_brand'),
    (('industrials', 'industrial', 'machinery', 'aerospace', 'defense', 'building products', 'construction',
      'engineering', 'electrical equipment', 'trading companies', 'airline', 'marine', 'trucking', 'freight',
      'logistics', 'commercial services', 'professional services', 'environmental services', 'auto parts',
      'chemical', 'packaging', 'life sciences tools', 'waste'), 'industrial'),
)
# Labels that mix platforms with operators: (keywords, default type, platform type, what the label mixes).
# The platform type is primary only when the signature is a platform's (gross margin >= 70%, capex <= 8% of
# revenue); the other type is kept as a secondary type either way.
MIXED_RULES = (
    (('broadline retail',), 'consumer_brand', 'platform_marketplace', 'department stores and online marketplaces'),
    (('hotels, resorts & cruise', 'hotels resorts & cruise', 'travel services'), 'consumer_brand', 'platform_marketplace',
     'hotel and cruise operators and online-travel platforms'),
)
CYCLICAL_WORDS = ('semiconductor', 'oil', 'mining', 'metals', 'steel', 'chemical', 'automobile', 'auto ',
                  'airline', 'homebuild', 'residential construction', 'machinery', 'paper', 'forest', 'marine',
                  'shipping', 'trucking', 'freight', 'building products', 'construction', 'hotel', 'casino', 'leisure',
                  'coal', 'aluminum', 'copper', 'gold', 'silver', 'travel', 'memory', 'commodity', 'fertilizer',
                  'refining', 'household durables')
CYCLICAL_TYPES = {'commodity', 'semiconductor'}

# Driver templates: (driver, why it decides revenue/profit, metric, evidence keys). A key is found as an
# analysis metric, a FactBook fact (any metric name, PIT-filtered) or a qualitative topic.
DRIVERS = {
    'subscription_software': [
        ('customers', 'revenue = paying customers x revenue per customer; growth needs new paying customers, not free users or logos',
         'paying customers / seats', ('customers', 'paying_customers', 'customer_count', 'subscribers', 'seats', 'paid_users')),
        ('revenue_per_customer', 'pricing, seat expansion and product attach decide what each customer is worth',
         'ARR or revenue per customer', ('arr', 'arr_per_customer', 'revenue_per_customer', 'arpu', 'arpa')),
        ('net_revenue_retention', 'expansion minus churn sets how much growth arrives without new customers',
         'net / gross revenue retention', ('net_revenue_retention', 'nrr', 'gross_revenue_retention', 'grr',
                                           'dollar_based_net_retention')),
        ('gross_margin', 'hosting, support and AI inference costs decide what each revenue dollar leaves',
         'gross profit / revenue', ('gross_margin',)),
        ('sales_marketing_efficiency', 'new revenue bought per dollar of sales and marketing sets the cost of growth',
         'new revenue / S&M spend (magic-number proxy)', ('sm',)),
    ],
    'platform_marketplace': [
        ('gmv_or_users', 'activity on the platform is the volume base the take rate applies to',
         'GMV / payment volume / active users', ('gmv', 'gross_bookings', 'gross_merchandise_value', 'payment_volume',
                                                 'tpv', 'active_users', 'mau', 'dau', 'transactions')),
        ('take_rate', 'share of activity the platform keeps; competition and regulation cap it',
         'revenue / GMV or revenue per user', ('take_rate', 'arpu', 'revenue_per_user', 'monetization')),
        ('contribution_margin', 'what each transaction leaves after payments, incentives and fulfilment',
         'contribution margin (gross margin as proxy)', ('contribution_margin', 'gross_margin')),
        ('marketing_efficiency', 'paid acquisition versus organic network effects', 'S&M / revenue', ('sm',)),
    ],
    'semiconductor': [
        ('units_asp', 'revenue = units shipped x average selling price; mix and pricing move with the cycle',
         'units and ASP', ('units', 'shipments', 'asp', 'average_selling_price', 'wafer_shipments')),
        ('design_wins', 'design wins and backlog decide revenue one to three years out',
         'design wins / backlog / RPO', ('design_wins', 'backlog', 'remaining_performance_obligations', 'bookings')),
        ('gross_margin', 'pricing power, fab utilization and mix; swings with the cycle', 'gross profit / revenue',
         ('gross_margin',)),
        ('capex_or_foundry', 'own fabs need heavy capex; fabless designs depend on foundry capacity and pricing',
         'capex / revenue, foundry dependence', ('capex_pct_revenue', 'foundry')),
        ('inventory', 'own and channel inventory are the early cycle signal', 'inventory / revenue', ('inventory',)),
    ],
    'capital_intensive_infrastructure': [
        ('installed_capacity', 'revenue can only come from energized, operating capacity (MW, GPUs, racks, network)',
         'operating capacity', ('installed_capacity', 'operating_capacity', 'capacity_mw', 'active_power_mw', 'gpus', 'racks')),
        ('utilization', 'idle capacity earns nothing but still carries depreciation and interest', 'utilization rate',
         ('utilization', 'occupancy', 'load_factor')),
        ('price_per_unit', 'contracted price per unit of capacity decides revenue per asset', 'realized price per unit',
         ('price_per_unit', 'revenue_per_mw', 'price_per_gpu_hour', 'tariff', 'contracted_price')),
        ('capex_per_unit', 'cost to build each unit sets the return on capital and the funding need',
         'capex per unit of capacity', ('capex_per_unit', 'capex_per_mw', 'build_cost')),
        ('funding', 'capacity is built before revenue arrives; committed financing and dilution decide what holders keep',
         'committed financing and cash runway', ('committed_financing', 'financing', 'funding', 'debt_facility',
                                                 'capital_structure')),
        ('contracted_backlog', 'take-or-pay contracts and RPO show how much capacity is pre-sold, and to whom',
         'RPO / contracted revenue', ('remaining_performance_obligations', 'rpo', 'contracted_backlog', 'backlog')),
    ],
    'consumer_brand': [
        ('volume', 'units sold / comparable sales show whether demand for the brand grows', 'volume / comparable sales',
         ('volume', 'units', 'comparable_sales', 'same_store_sales', 'organic_volume', 'store_count')),
        ('price_mix', 'realized price and mix test pricing power without losing volume', 'price/mix contribution',
         ('price_mix', 'pricing', 'average_price')),
        ('gross_margin', 'input costs versus pricing power', 'gross profit / revenue', ('gross_margin',)),
        ('marketing', 'brand support spend needed to hold volume and price', 'advertising / marketing spend',
         ('advertising_expense', 'advertising', 'marketing', 'sm')),
    ],
    'industrial': [
        ('orders_backlog', 'orders and backlog lead revenue; book-to-bill shows the direction',
         'orders / backlog / book-to-bill', ('orders', 'backlog', 'book_to_bill', 'remaining_performance_obligations')),
        ('price', 'price realization versus input-cost inflation', 'price realization', ('price_realization', 'pricing')),
        ('utilization', 'fixed-cost absorption: margins move with plant utilization', 'capacity utilization',
         ('utilization', 'capacity_utilization')),
        ('operating_margin', 'mix, price and absorption combine here', 'operating income / revenue', ('operating_margin',)),
        ('working_capital', 'inventory and receivables absorb cash as volume grows', 'inventory and receivables / revenue',
         ('inventory', 'receivables')),
    ],
    'financials': [
        ('loans_deposits', 'balance-sheet growth: loans funded by deposits (or premiums and float for insurers)',
         'loans, deposits / premiums, float', ('loans', 'net_loans', 'deposits', 'total_deposits', 'premiums', 'float')),
        ('net_interest_margin', 'spread earned on the balance sheet (combined ratio for insurers)',
         'net interest margin / combined ratio', ('net_interest_margin', 'nim', 'net_interest_income', 'combined_ratio')),
        ('credit_losses', 'underwriting quality shows up as provisions and charge-offs through the cycle',
         'provisions / net charge-offs / loss ratio', ('provision_for_credit_losses', 'net_charge_offs', 'credit_losses',
                                                        'loss_ratio')),
        ('capital', 'regulatory capital limits growth and distributions', 'CET1 / regulatory capital ratio',
         ('cet1_ratio', 'tier1_ratio', 'regulatory_capital', 'solvency_ratio')),
    ],
    'pre_revenue_biotech': [
        ('pipeline_assets', 'value sits in specific programs: indication, phase, endpoint, competing standard of care',
         'programs by phase', ('pipeline', 'pipeline_assets', 'clinical_programs', 'trial')),
        ('probability_of_success', 'phase-based success rates turn programs into expected value',
         'probability of success by phase', ('probability_of_success', 'clinical_phase', 'phase')),
        ('cash_runway', 'cash must last through the next readout; otherwise dilution decides the outcome',
         '(cash + short-term investments) / annual burn', ('cash',)),
        ('market_size', 'treated population x share x net price decides peak sales', 'addressable patients / peak sales',
         ('addressable_patients', 'market_size', 'tam', 'peak_sales')),
    ],
    'commodity': [
        ('volume', 'production volume', 'production / shipments', ('production', 'volume', 'shipments', 'units')),
        ('realized_price_vs_cost_curve', 'realized price relative to the industry cost curve decides through-cycle margins',
         'realized price vs cost-curve position', ('realized_price', 'cost_curve', 'commodity_price')),
        ('unit_cost', 'cost position decides survival at the bottom of the cycle', 'cash cost per unit / AISC',
         ('unit_cost', 'cash_cost', 'aisc', 'cost_per_unit')),
        ('reserves', 'reserve life limits how long the asset base produces', 'reserves / reserve life',
         ('reserves', 'reserve_life', 'resources')),
        ('capex', 'sustaining versus growth capex', 'capex / revenue', ('capex_pct_revenue',)),
    ],
    'hardware_manufacturing': [
        ('units_asp', 'revenue = units x ASP; product cycles move both', 'units and ASP',
         ('units', 'shipments', 'asp', 'average_selling_price')),
        ('gross_margin', 'component costs, pricing and mix', 'gross profit / revenue', ('gross_margin',)),
        ('inventory_channel', 'own and channel inventory signal demand shifts early', 'inventory / revenue',
         ('inventory', 'channel_inventory')),
        ('recurring_attach', 'services and subscriptions on the installed base stabilize revenue',
         'services revenue / installed base', ('services_revenue', 'installed_base', 'subscription_revenue')),
    ],
    'conglomerate': [
        ('segment_economics', 'each engine has its own customers, margins and capital needs',
         'segment revenue and operating income', ('segments', 'segment_revenue', 'segment_operating_income')),
        ('holding_costs', 'corporate costs and tax leakage reduce the sum of the parts', 'corporate / holding costs',
         ('corporate_costs', 'holding_costs')),
        ('capital_allocation', 'moving capital between engines decides compounding', 'ROIC by segment / group ROIC',
         ('roic',)),
        ('minority_claims', 'minorities and intercompany claims are not owned by the listed equity',
         'noncontrolling interests', ('noncontrolling_interest', 'minority_interest')),
    ],
    'reit': [
        ('occupancy', 'occupied space earns rent; vacancy costs carry', 'occupancy / leased %', ('occupancy', 'leased_percentage')),
        ('rent_noi', 'net operating income per property is the cash base of NAV', 'NOI / same-property NOI',
         ('noi', 'net_operating_income', 'same_property_noi')),
        ('cap_rates', 'cap rates translate NOI into asset values', 'cap rate / appraised value',
         ('cap_rate', 'property_values', 'appraised_value')),
        ('leverage_funding', 'debt maturities and LTV decide refinancing risk', 'LTV / debt maturities',
         ('loan_to_value', 'ltv', 'debt_maturities', 'capital_structure')),
    ],
    UNCLASSIFIED: [
        ('revenue_growth', 'business-model drivers unknown: revenue-level growth is the only driver', 'revenue growth',
         ('revenue_cagr_3y', 'revenue_growth_last')),
        ('operating_margin', 'revenue-level profitability', 'operating income / revenue', ('operating_margin',)),
        ('reinvestment', 'capex and acquisitions needed for growth', 'capex + acquisitions / revenue',
         ('capex_pct_revenue', 'acquisition_spend_pct_revenue_3y')),
        ('funding', 'who pays for the reinvestment', 'cash and FCF', ('cash',)),
    ],
}
SEGMENT_KEYS = ('segments', 'segment_revenue', 'segment_operating_income')
STAGE_TO_PROFILE = {'growth': 'high_growth', 'maturing': 'mature_growth', 'mature': 'mature_stable', 'decline': 'decline',
                    'turnaround': 'decline'}


# ---------------------------------------------------------------- small helpers

def _f(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float('inf'), float('-inf')):
        return None
    return float(x)


def _div(a, b):
    return a / b if a is not None and b is not None and b > 0 else None


def _pct(x):
    return 'n/a' if x is None else f'{x:.1%}'


def _money(x, cur):
    if x is None:
        return 'n/a'
    return f'{x / 1e9:,.2f}bn {cur}' if abs(x) >= 1e9 else f'{x / 1e6:,.1f}m {cur}'


def _num(value, formula, period_end=None):
    return {'value': _f(value), 'formula': formula, 'period_end': period_end}


def _metric(an, name):
    m = (an.get('metrics') or {}).get(name)
    return _f(m.get('value') if isinstance(m, dict) else m)


def _row_val(row, key):
    return _f((row or {}).get(key))


def _fcf(row):
    v = _row_val(row, 'fcf')
    if v is None and _row_val(row, 'cfo') is not None and _row_val(row, 'capex') is not None:
        v = _row_val(row, 'cfo') - abs(_row_val(row, 'capex'))
    return v


def _annual_fact(fb, metric, end):
    return next((f for e, _, f in F.series(fb, metric, 'annual') if e == end), None)


def _instant_fact(fb, metric, end=None):
    s = F.series(fb, metric, 'instant')
    if end is None:
        return s[-1][2] if s else None
    return next((f for e, _, f in s if e == end), None)


def _src(*fs):
    return sorted({f['source_id'] for f in fs if f})


def _norm(s):
    return re.sub(r'[^a-z0-9]+', '_', str(s).lower()).strip('_')


def _qualitative(fb):
    """Qualitative items usable at as_of (an item dated later is ignored, even if stored)."""
    cut = date.fromisoformat(fb['as_of'])
    return [q for q in fb.get('qualitative') or [] if q.get('as_of') and date.fromisoformat(q['as_of'][:10]) <= cut]


def _evidence(fb, an, keys):
    """Where a driver's data exists: analysis metric, PIT-usable FactBook fact, qualitative topic or capital structure."""
    found = []
    quals = {}
    for q in _qualitative(fb):
        quals.setdefault(_norm(q.get('topic', '')), []).append(q['id'])
    for k in keys:
        if k == 'capital_structure':
            if fb.get('capital_structure'):
                found.append({'kind': 'capital_structure', 'key': k})
            continue
        if _metric(an, k) is not None:
            found.append({'kind': 'analysis_metric', 'key': k})
        elif F.candidates(fb, k):
            found.append({'kind': 'fact', 'key': k, 'source_ids': _src(*F.candidates(fb, k)[:1])})
        if quals.get(_norm(k)):
            found.append({'kind': 'qualitative', 'key': k, 'ids': quals[_norm(k)]})
    return found


def _valid_ids(fb):
    """Source ids usable as evidence at as_of (a source published later is not) and PIT-usable qualitative ids."""
    cut = date.fromisoformat(fb['as_of'])
    ok = {sid for sid, src in (fb.get('sources') or {}).items()
          if not (src or {}).get('published_at') or date.fromisoformat(str(src['published_at'])[:10]) <= cut}
    return ok | {q['id'] for q in _qualitative(fb)}


# ---------------------------------------------------------------- data extraction

def _years(an):
    ann = an.get('annual') or {}
    return {e: ann[e] or {} for e in sorted(ann)}


def _signature(fb, rows, end):
    """Financial signature of the latest fiscal year with revenue; every ratio states its formula."""
    r = rows.get(end) or {}
    rev = _row_val(r, 'revenue')
    if end is None:          # no fiscal year with revenue: no signature (never read an arbitrary latest instant)
        return {k: _num(None, f, None) for k, f in (('gross_margin', 'gross_profit / revenue'), ('capex_pct_revenue', 'capex / revenue'),
                ('deferred_revenue_pct_revenue', 'deferred revenue / revenue'), ('rd_pct_revenue', 'R&D expense / revenue'),
                ('inventory_pct_revenue', 'inventory / revenue'), ('capex_to_da', 'capex / D&A'),
                ('liabilities_to_assets', 'total liabilities / total assets'))} | {'has_cost_of_revenue': False}
    capex = _row_val(r, 'capex')
    capex = abs(capex) if capex is not None else None
    rd = _annual_fact(fb, 'rd', end)
    inv = _instant_fact(fb, 'inventory', end)
    ta, tl = _instant_fact(fb, 'total_assets', end), _instant_fact(fb, 'total_liabilities', end)
    cor = _annual_fact(fb, 'cost_of_revenue', end)
    return {
        'gross_margin': _num(_div(_row_val(r, 'gross_profit'), rev), 'gross_profit / revenue', end),
        'capex_pct_revenue': _num(_div(capex, rev), 'capex / revenue', end),
        'deferred_revenue_pct_revenue': _num(_div(_row_val(r, 'deferred_revenue'), rev),
                                             'deferred revenue (current + noncurrent) / revenue', end),
        'rd_pct_revenue': _num(_div(rd['value'] if rd else None, rev), 'R&D expense / revenue', end),
        'inventory_pct_revenue': _num(_div(inv['value'] if inv else None, rev), 'inventory / revenue', end),
        'capex_to_da': _num(_div(capex, _row_val(r, 'd_and_a')), 'capex / depreciation and amortization', end),
        'liabilities_to_assets': _num(_div(tl['value'] if tl else None, ta['value'] if ta else None),
                                      'total liabilities / total assets', end),
        'has_cost_of_revenue': cor is not None or _row_val(r, 'gross_profit') is not None,
    }


def clean_text(s):
    """Lower case, dashes as spaces, whitespace collapsed (vendor 'Medical - Devices' == GICS 'Medical Devices')."""
    return re.sub(r'\s+', ' ', re.sub(r'[-—–]', ' ', str(s or '').lower())).strip()


def _hit(kws, low):
    return next((k for k in kws if re.search(r'(?<![a-z])' + re.escape(clean_text(k)), low)), None)


def _text_matches(company):
    out = []
    for field in ('industry', 'sector'):
        raw = company.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        low = clean_text(raw)
        for kws, t, alt, mixes in MIXED_RULES:
            hit = _hit(kws, low)
            if hit:
                out.append({'field': field, 'text': raw, 'keyword': hit, 'type': t, 'alt': alt, 'mixes': mixes})
                break
        else:
            for kws, t in TEXT_RULES:
                hit = _hit(kws, low)
                if hit:
                    out.append({'field': field, 'text': raw, 'keyword': hit, 'type': t})
                    break
    return out


def text_type(company):
    """The text match that decides the type: the industry match, else the sector match (fallback), else None."""
    m = _text_matches(company or {})
    return next((t for t in m if t['field'] == 'industry'), m[0] if m else None)


def cyclical_word(company):
    """First CYCLICAL_WORDS entry in the industry/sector text, or None (an industry flag, not evidence of a cycle)."""
    text = ' ' + clean_text(' '.join(str((company or {}).get(k) or '') for k in ('industry', 'sector'))) + ' '
    w = next((w for w in CYCLICAL_WORDS if w in text), None)
    return w.strip() if w else None


# ---------------------------------------------------------------- business model

def _business_model(fb, rows, end, sig, inputs):
    company = fb.get('company') or {}
    text = _text_matches(company)
    used = next((t for t in text if t['field'] == 'industry'), text[0] if text else None)   # sector only as a fallback
    sv = {k: (v['value'] if isinstance(v, dict) else v) for k, v in sig.items()}
    reasons, secondary = [], []
    rev = _row_val(rows.get(end), 'revenue') if end else None
    gm, dr, cx, inv = sv['gross_margin'], sv['deferred_revenue_pct_revenue'], sv['capex_pct_revenue'], sv['inventory_pct_revenue']
    for t in text:
        reasons.append(f"{t['field']} '{t['text']}' matches '{t['keyword']}' -> {t['type']}"
                       + ('' if t is used else ' (not used: the industry label is more specific)'))
    text_types = [used['type']] if used else []
    if used and used.get('alt'):
        plat = gm is not None and cx is not None and gm >= .70 and cx <= .08
        text_types = [used['alt'], used['type']] if plat else [used['type'], used['alt']]
        reasons.append(f"'{used['text']}' mixes {used['mixes']}: gross margin {_pct(gm)}, capex {_pct(cx)} of revenue -> "
                       f"{text_types[0]} (rule: platform when gross margin >= 70% and capex <= 8%); {text_types[1]} kept as secondary")
    text_financials = 'financials' in text_types
    pharma_text = 'pre_revenue_biotech' in text_types
    if pharma_text:
        pre_commercial = rev is None or rev < PRE_REVENUE_BIOTECH or (sv['rd_pct_revenue'] or 0) >= 1
        if not pre_commercial:
            text_types = [t for t in text_types if t != 'pre_revenue_biotech']
            reasons.append(f'biotech/pharma label but commercial revenue ({_money(rev, company.get("currency", ""))}, R&D '
                           f'{_pct(sv["rd_pct_revenue"])} of revenue): not pre-revenue; no listed type fits a commercial '
                           'drug company, so the label is not forced; an analyst override with a reason is expected')
    sig_types = []
    if gm is not None and dr is not None and cx is not None and gm >= .65 and dr >= .15 and cx <= .08 and (inv or 0) <= .05:
        sig_types.append('subscription_software')
        reasons.append(f'financial signature {end}: gross margin {_pct(gm)}, deferred revenue {_pct(dr)} of revenue, '
                       f'capex {_pct(cx)} of revenue -> subscription economics (rule: gm >= 65%, deferred >= 15%, capex <= 8%)')
    if cx is not None and cx >= .25:
        sig_types.append('capital_intensive_infrastructure')
        reasons.append(f'financial signature {end}: capex {_pct(cx)} of revenue -> capital-intensive economics (rule: capex >= 25%)')
    lender_sig = sv['liabilities_to_assets'] is not None and sv['liabilities_to_assets'] >= .85 and not sv['has_cost_of_revenue']
    possible_financial = not text and lender_sig
    if possible_financial:
        reasons.append(f"no sector text; liabilities are {_pct(sv['liabilities_to_assets'])} of assets with no cost of revenue: "
                       'looks like a balance-sheet lender or insurer; confirm before choosing any model')
    primary = text_types[0] if text_types else None
    evidence = 'text' if primary else 'none'
    operating_text = primary in (None, 'subscription_software', 'platform_marketplace')
    if operating_text and 'capital_intensive_infrastructure' in sig_types:
        if primary:
            secondary.append(primary)
            reasons.append(f'capex intensity overrides the {primary} label as primary (economics before labels); '
                           'the label is kept as a secondary type')
        primary, evidence = 'capital_intensive_infrastructure', 'text+financials' if primary else 'financials'
    elif primary is None and sig_types:
        primary, evidence = sig_types[0], 'financials'
    elif primary in sig_types:
        evidence = 'text+financials'
    secondary += text_types[1:]
    if primary not in ('financials', 'reit', 'pre_revenue_biotech', 'conglomerate'):
        secondary += [t for t in sig_types if t != primary]
    if primary is None:
        primary = UNCLASSIFIED
        if not possible_financial:
            reasons.append('neither sector/industry text nor the financial signature supports a listed type: unclassified; '
                           'drivers fall back to revenue level until an analyst sets the business model with a reason')
    secondary = [t for t in dict.fromkeys(secondary) if t != primary]
    computed = {'primary': primary, 'secondary': secondary, 'evidence': evidence, 'text_financials': text_financials}
    override = None
    ov = inputs.get('business_model')
    if ov is not None:
        if not isinstance(ov, dict):
            raise ValueError('business_model override must be an object with primary and reason')
        txt(ov.get('reason'), 'business_model.reason')
        if ov.get('primary') not in TYPES:
            raise ValueError('business_model.primary must be one of ' + ', '.join(TYPES))
        clear = text_financials and ov['primary'] != 'financials'
        if clear and lender_sig:
            clear = False
            reasons.append(f"override kept the bank/insurer flag: liabilities are {_pct(sv['liabilities_to_assets'])} of assets with "
                           'no cost of revenue (a lender/insurer balance sheet); correct the FactBook or supply segment data')
        elif clear:
            text_financials = False
            reasons.append("analyst override clears the bank/insurer text flag (the balance sheet is not lender-like: liabilities "
                           f"{_pct(sv['liabilities_to_assets'])} of assets); the computed flag is kept in 'computed'")
        sec = ov.get('secondary')
        if sec is None:
            sec = [t for t in [primary] + secondary if t not in (ov['primary'], UNCLASSIFIED) and not (clear and t == 'financials')]
        elif not isinstance(sec, list) or any(t not in TYPES for t in sec):
            raise ValueError('business_model.secondary must list known types')
        override = {'primary': ov['primary'], 'secondary': list(sec), 'reason': ov['reason'],
                    'evidence_ids': list(ov.get('evidence_ids') or [])}
        reasons.append(f"analyst override: {ov['primary']} ({ov['reason']}); computed {primary} kept in 'computed'")
        primary, secondary = ov['primary'], [t for t in dict.fromkeys(sec) if t != ov['primary']]
        evidence = 'override' if evidence == 'none' else evidence + '+override'
    return {'primary': primary, 'secondary': secondary, 'reasons': reasons, 'override': override, 'computed': computed,
            'evidence': evidence, 'text_matches': text, 'signature': {k: v for k, v in sig.items() if isinstance(v, dict)},
            'flags': {'text_financials': text_financials, 'possible_financial': possible_financial, 'pharma_text': pharma_text}}


# ---------------------------------------------------------------- stage

def _growth(fb, an, rows, ends):
    """3-year revenue CAGR (analysis metric first), organic when goodwill shows a large purchase (history.py rules)."""
    g3 = _metric(an, 'revenue_cagr_3y')
    formula = 'analysis.revenue_cagr_3y'
    window = ends[-4:]
    if g3 is None and len(window) >= 2:
        a, b = window[0], window[-1]
        g3 = cagr(_row_val(rows[a], 'revenue'), _row_val(rows[b], 'revenue'),
                  (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25)
        formula = f'(revenue {b} / revenue {a})^(1/years) - 1' + ('' if len(window) == 4 else ' (shorter than 3 years)')
    g_last = _metric(an, 'revenue_growth_last')
    if g_last is None and len(ends) >= 2:
        g_last = _div(_row_val(rows[ends[-1]], 'revenue'), _row_val(rows[ends[-2]], 'revenue'))
        g_last = g_last - 1 if g_last is not None else None
    hist = []
    for e in window:
        ta = _instant_fact(fb, 'total_assets', e)
        hist.append({'fiscal_year': e, 'period_end': e, 'revenue': _row_val(rows[e], 'revenue') or 0,
                     'goodwill': _row_val(rows[e], 'goodwill'), 'total_assets': ta['value'] if ta else None})
    bought = acquisition_years(hist) if len(hist) >= 2 else []
    organic = organic_cagr(hist) if bought else None
    return g3, formula, g_last, bought, organic


def _profile_cross_check(rows, ends, as_of, primary):
    """profile.classify on the same years, reported side by side (its labels have known defects; not used to decide)."""
    hist = []
    for e in ends:
        r = rows[e]
        h = {'fiscal_year': e, 'period_end': e, 'revenue': _row_val(r, 'revenue'), 'source_ids': ['analysis.annual']}
        for k, src in (('operating_income', 'operating_income'), ('net_income', 'net_income'), ('operating_cash_flow', 'cfo'),
                       ('goodwill', 'goodwill'), ('equity', 'equity'), ('cash', 'cash')):
            if _row_val(r, src) is not None:
                h[k] = _row_val(r, src)
        if _row_val(r, 'capex') is not None:
            h['capex'] = abs(_row_val(r, 'capex'))
        if _row_val(r, 'dividends') is not None:
            h['dividends_paid'] = abs(_row_val(r, 'dividends'))
        hist.append(h)
    arche = {'commodity': 'commodity', 'consumer_brand': 'consumer', 'industrial': 'industrial',
             'capital_intensive_infrastructure': 'infrastructure', 'semiconductor': 'industrial'}.get(primary, 'nonfinancial')
    try:
        p = profile.classify({'archetype': arche, 'as_of': as_of, 'history': hist[-15:]})
        return {'source': 'profile.classify (Damodaran life cycle / Lynch)', 'lifecycle': p['stage'], 'lynch': p['lynch']}
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        return {'source': 'profile.classify', 'status': 'unavailable', 'reason': str(e)}


def _stage(fb, an, rows, inputs, cur, cyc_text):
    as_of = date.fromisoformat(fb['as_of'])
    ends = [e for e in rows if _row_val(rows[e], 'revenue') is not None]
    reasons, data = [], {}
    usd = cur == 'USD'
    if not usd:
        reasons.append(f'size thresholds are defined in USD; reporting currency is {cur}, so scale was not used')
    if not ends:
        reasons.append('no fiscal year with revenue in the analysis (pre-revenue or missing data): treated as early, the most '
                       'conservative label (no operating valuation), until reported revenue is available')
        return _stage_out('early', reasons, data, inputs, 'none')
    last = ends[-1]
    all_ends = list(rows)
    if all_ends[-1] != last:
        reasons.append(f'latest fiscal year {all_ends[-1]} has no revenue; the stage uses {last} and says so')
    age = (as_of - date.fromisoformat(last)).days
    confidence = 'normal'
    if age > STALE_ANNUAL_DAYS:
        confidence = 'low'
        reasons.append(f'latest fiscal year with revenue ends {last}, {age} days before as_of {fb["as_of"]}: a newer annual '
                       'report is probably missing; the stage is provisional and was not silently carried forward')
    rev = _row_val(rows[last], 'revenue')
    om_years = [(e, _div(_row_val(rows[e], 'operating_income'), _row_val(rows[e], 'revenue'))) for e in ends]
    om_years = [(e, m) for e, m in om_years if m is not None]
    om = om_years[-1][1] if om_years else None
    if om_years and om_years[-1][0] != last:
        reasons.append(f'operating income missing for {last}; profitability judged from {om_years[-1][0]} (older year)')
    if not om_years:
        reasons.append('operating income unavailable: profitability unknown')
    g3, gformula, g_last, bought, organic = _growth(fb, an, rows, ends)
    g, gsrc = (g3, gformula) if g3 is not None else (g_last, 'revenue / prior-year revenue - 1 (no 3-year window)')
    if bought and organic is not None:
        reasons.append(f'large acquisition detected ({", ".join(bought)}: goodwill rose by more than 10% of total assets); '
                       f'growth measured organically {_pct(organic)} instead of {_pct(g)}')
        g, gsrc = organic, 'organic CAGR excluding acquisition years (history.organic_cagr)'
    acq = _metric(an, 'acquisition_spend_pct_revenue_3y')
    if acq is not None and acq >= .10 and not bought:
        reasons.append(f'acquisition spend {_pct(acq)} of revenue over 3 years: part of the growth may be bought')
    prior = [m for _, m in om_years[:-1]]
    profitable_prior = sum(m > .02 for m in prior)
    losses = 0
    for _, m in reversed(om_years):
        if m >= 0:
            break
        losses += 1
    fcf = _fcf(rows[last])
    large = usd and rev >= ESTABLISHED
    # A record of profits (most prior years, at least two; or one at established scale) means a loss year is a
    # setback of an established business, never an early-stage company (profile.classify defect).
    established = profitable_prior >= 1 and (profitable_prior >= max(2, (len(prior) + 1) // 2) or large)
    data.update({'latest_fiscal_year_end': last, 'data_age_days': age,
                 'revenue': _num(rev, 'reported revenue, latest fiscal year', last),
                 'revenue_growth_used': _num(g, gsrc, last),
                 'revenue_growth_last': _num(g_last, 'revenue / prior-year revenue - 1', last),
                 'operating_margin': _num(om, 'operating income / revenue', om_years[-1][0] if om_years else None),
                 'fcf': _num(fcf, 'CFO - capex', last),
                 'profitable_prior_years': profitable_prior, 'prior_years_with_margin': len(prior),
                 'consecutive_loss_years': losses, 'established_profit_record': established})
    gtxt = f'revenue growth {_pct(g)}'
    if rev < (PRE_REVENUE if usd else 0) or rev <= 0 or (om is not None and om <= -1 and (g is None or g > .5) and not large):
        value = 'early'
        reasons.append(f'pre-commercial or first revenue (revenue {_money(rev, cur)}, operating margin {_pct(om)})')
    elif om is not None and om < 0 and established:
        scale = f'{_money(rev, cur)} revenue' + (' (established scale)' if large else '')
        if losses >= 2 and g is not None and g >= .15:
            value = 'growth'
            reasons.append(f'{losses} loss years after {profitable_prior} profitable year(s) while growing ({gtxt}) at {scale}: '
                           'an investment phase or a margin reset; growth with losses, not early-stage')
        elif losses >= 2 or (g_last is not None and g_last <= -.05):
            value = 'turnaround'
            reasons.append(f'{losses} loss year(s) after {profitable_prior} profitable year(s) at {scale}'
                           + (f' with revenue down {_pct(-g_last)}' if g_last is not None and g_last <= -.05 else '')
                           + ': profitability must be restored; never treated as an early-stage company')
        else:
            value = _profitable_stage(g, rev, om_years[-2][1] if len(om_years) > 1 else None, usd, reasons, gtxt, om)
            reasons.append(f'one loss year ({om_years[-1][0]}, operating margin {_pct(om)}) after {profitable_prior} profitable '
                           f'year(s) at {scale}: treated as {value} with a loss year, not as early-stage; '
                           'check whether the loss is one-off (charges, impairments) or a margin reset')
    elif om is not None and om < 0:
        if g is not None and g >= .15:
            small = usd and rev < SMALL_EARLY
            value = 'early' if small or (om <= -.25 and not large) else 'growth'
            reasons.append(f'loss-making (operating margin {_pct(om)}) while growing ({gtxt}) without an established record of '
                           f'profits: {value}' + (' (small or deep losses)' if value == 'early' else ''))
        elif g is not None and g < -.02:
            value = 'decline'
            reasons.append(f'loss-making and shrinking ({gtxt}) without a record of profits')
        else:
            value = 'early' if usd and rev < SMALL_EARLY else 'turnaround'
            reasons.append(f'loss-making with slow or unknown growth ({gtxt}): ' + (
                'still small, early' if value == 'early' else 'large, so profitability must be fixed rather than grown into'))
    else:
        value = _profitable_stage(g, rev, om_years[-2][1] if len(om_years) > 1 else None, usd, reasons, gtxt, om)
    if len(ends) < 3:
        confidence = 'low'
        reasons.append(f'only {len(ends)} fiscal year(s) of revenue: stage is provisional')
    if cyc_text:
        reasons.append('cyclical economics: growth over this window may reflect the cycle rather than the life-cycle stage')
    ttm = an.get('ttm') or {}
    t_end, t_rev, t_oi = ttm.get('period_end'), _f(ttm.get('revenue')), _f(ttm.get('operating_income'))
    if t_end and t_end > last and t_rev:
        t_om = _div(t_oi, t_rev)
        data['ttm_watch'] = {'period_end': t_end, 'operating_margin': _num(t_om, 'TTM operating income / TTM revenue', t_end),
                             'revenue': _num(t_rev, 'TTM revenue (' + str(ttm.get('method')) + ')', t_end)}
        if t_om is not None and om is not None and ((t_om >= 0) != (om >= 0) or abs(t_om - om) > .10):
            reasons.append(f'TTM to {t_end} shows operating margin {_pct(t_om)} versus {_pct(om)} in {last}: noted as a watch item; '
                           'the stage is set on fiscal years and is not relabelled by sub-annual data until a fiscal year confirms it')
    return _stage_out(value, reasons, data, inputs, confidence)


def _profitable_stage(g, rev, om_prev, usd, reasons, gtxt, om):
    if g is None:
        reasons.append('growth unknown (one fiscal year): treated as mature, provisional')
        return 'mature'
    if g >= .30:
        reasons.append(f'{gtxt} with positive operating margin: growth')
        return 'growth'
    if g >= .15:
        if usd and rev >= VERY_LARGE and om is not None and om >= .15:
            reasons.append(f'{gtxt}, but very large ({rev / 1e9:,.0f}bn) and highly profitable: growth on this base usually fades; maturing')
            return 'maturing'
        reasons.append(f'{gtxt} with positive operating margin: growth')
        return 'growth'
    if g >= .07:
        reasons.append(f'{gtxt} with established profitability: maturing')
        return 'maturing'
    if g >= -.02:
        reasons.append(f'{gtxt}: mature')
        return 'mature'
    if om_prev is not None and om is not None and om > om_prev:
        reasons.append(f'{gtxt} but margins improving: mature (managed shrinkage), watch for decline')
        return 'mature'
    reasons.append(f'{gtxt} with margins not improving: decline')
    return 'decline'


def _stage_out(value, reasons, data, inputs, confidence):
    ov, override = inputs.get('stage'), None
    computed = value
    if ov is not None:
        if not isinstance(ov, dict) or ov.get('value') not in STAGES:
            raise ValueError('stage override needs value in ' + ', '.join(STAGES) + ' and a reason')
        txt(ov.get('reason'), 'stage.reason')
        override = {'value': ov['value'], 'reason': ov['reason'], 'evidence_ids': list(ov.get('evidence_ids') or [])}
        value = ov['value']
    need = []
    if value in ('early', 'growth'):
        need = ['paid adoption: revenue from paying customers (not pilots, free users or partner logos)',
                'progress against stated milestones, dated', 'an explicit failure scenario',
                'funding path and dilution to current holders']
    return {'value': value, 'reasons': reasons, 'computed': computed, 'override': override, 'confidence': confidence,
            'data': data, 'evidence_needed': need}


# ---------------------------------------------------------------- cyclical, capital intensity, funding

def _cyclical(fb, rows, primary, inputs):
    """Industry words/type are a flag; only a cycle in the reported history or an analyst's confirmation (inputs.cyclical,
    reason mandatory) routes the valuation to normalized mid-cycle margins ('normalize')."""
    reasons = []
    word = cyclical_word(fb.get('company'))
    flag = primary in CYCLICAL_TYPES or bool(word)
    if flag:
        reasons.append(f"industry economics are cyclical ({'type ' + primary if primary in CYCLICAL_TYPES else repr(word)}); "
                       'five years of data may not span a full cycle')
    ends = [e for e in rows if _row_val(rows[e], 'revenue')]
    oms = [_div(_row_val(rows[e], 'operating_income'), _row_val(rows[e], 'revenue')) for e in ends]
    pairs = [(e, m) for e, m in zip(ends, oms) if m is not None]
    ms = [m for _, m in pairs]
    vol = pstdev(ms) if len(ms) >= 3 else None
    revs = [_row_val(rows[e], 'revenue') for e in ends]
    drop_recovered = any(revs[i] < .9 * revs[i - 1] and revs[-1] > revs[i] for i in range(1, len(revs) - 1))
    reversals = sum(1 for i in range(1, len(ms) - 1)
                    if abs(ms[i] - ms[i - 1]) >= .03 and abs(ms[i + 1] - ms[i]) >= .03 and (ms[i] - ms[i - 1]) * (ms[i + 1] - ms[i]) < 0)
    data_cyc = (drop_recovered and (vol or 0) >= .03) or reversals >= 2
    if data_cyc:
        reasons.append(f'reported history swings: operating-margin standard deviation {_pct(vol)}, {reversals} margin reversal(s) of '
                       f'>= 3 points' + (', revenue fell >= 10% and recovered' if drop_recovered else ''))
    elif ms:
        reasons.append(f'reported history shows no cycle pattern (margin standard deviation {_pct(vol)}, {reversals} reversal(s))')
    value, normalize, basis, override = flag or data_cyc, data_cyc, 'data' if data_cyc else ('industry_flag' if flag else None), None
    ov = inputs.get('cyclical')
    if ov is not None:
        if not isinstance(ov, dict) or not isinstance(ov.get('value'), bool):
            raise ValueError('cyclical override needs a boolean value and a reason')
        txt(ov.get('reason'), 'cyclical.reason')
        override = {'value': ov['value'], 'reason': ov['reason'], 'evidence_ids': list(ov.get('evidence_ids') or [])}
        value = normalize = ov['value']
        basis = 'analyst'
        reasons.append(f"analyst {'confirms' if ov['value'] else 'rejects'} a cycle ({ov['reason']})")
    elif flag and not data_cyc:
        reasons.append('industry flag only: the valuation is not normalized to a historical mean unless the reported history '
                       'shows a cycle or an analyst confirms one (inputs.cyclical with a reason)')
    out = {'value': value, 'normalize': normalize, 'basis': basis, 'industry_flag': flag, 'data_shows_cycle': data_cyc,
           'computed': flag or data_cyc, 'override': override, 'reasons': reasons,
           'margin_volatility': _num(vol, 'population standard deviation of annual operating margins', ends[-1] if ends else None)}
    if (out['computed'] or value) and len(ms) >= 3:
        avg = mean(ms)
        out['normalized_operating_margin'] = _num(avg, f'mean annual operating margin over {len(ms)} years ({pairs[0][0]}..{pairs[-1][0]})',
                                                  pairs[-1][0])
        last = ms[-1]
        out['position'] = ('peak' if last > avg + .05 or (avg > 0 and last > 1.5 * avg) else
                           'trough' if last < avg - .05 else 'mid')
        if out['position'] != 'mid':
            reasons.append(f"latest margin {_pct(last)} vs average {_pct(avg)}: possible cycle {out['position']}; do not extrapolate it")
    return out


def _capital_intensity(rows, sig):
    ends = [e for e in rows if _row_val(rows[e], 'revenue')]
    cx = sig['capex_pct_revenue']['value']
    cda = sig['capex_to_da']['value']
    window = ends[-3:]
    avg = [_div(abs(_row_val(rows[e], 'capex')), _row_val(rows[e], 'revenue')) for e in window if _row_val(rows[e], 'capex') is not None]
    avg3 = mean(avg) if avg else None
    data = {'capex_pct_revenue': sig['capex_pct_revenue'], 'capex_to_da': sig['capex_to_da'],
            'capex_pct_revenue_avg': _num(avg3, f'mean of capex / revenue over {len(avg)} year(s)', window[-1] if window else None)}
    if cx is None:
        return {'value': 'unknown', 'reasons': ['capex or revenue unavailable'], 'data': data}
    if cx >= .15 or (cx >= .10 and (cda or 0) >= 2):
        v = 'high'
    elif cx < .05:
        v = 'low'
    else:
        v = 'medium'
    reasons = [f'capex {_pct(cx)} of revenue, capex/D&A {"n/a" if cda is None else f"{cda:.2f}x"} (rules: high >= 15%, or >= 10% '
               'with capex/D&A >= 2; low < 5%)']
    if avg3 is not None and abs(avg3 - cx) >= .05:
        reasons.append(f'capex intensity changed: {len(avg)}-year average {_pct(avg3)} versus latest {_pct(cx)}')
    if cda is not None and cda >= 2:
        reasons.append('capex far above depreciation: the asset base is being built out (growth capex), not just maintained')
    return {'value': v, 'reasons': reasons, 'data': data}


def _liquidity(fb, qa):
    """Cash + short-term investments: dataqa's resolved values first (validated), else the latest PIT instant."""
    res = (qa or {}).get('resolved') or {}
    out, ids = {}, set()
    for k in ('cash', 'short_term_investments'):
        r = res.get(k)
        if isinstance(r, dict) and _f(r.get('value')) is not None:
            out[k] = (_f(r['value']), r.get('as_of'), 'dataqa.resolved' + (f" ({r['status']})" if r.get('status') else ''))
            ids.update(r.get('source_ids') or [])
        else:
            f = F.latest_instant(fb, k)
            if f:
                out[k] = (f['value'], f['period_end'], 'facts.latest_instant')
                ids.add(f['source_id'])
    if 'cash' not in out:
        return None, None, sorted(ids)
    return out['cash'][0] + (out.get('short_term_investments') or (0,))[0], out, sorted(ids)


def _funding(fb, an, qa, rows, cur):
    ends = [e for e in rows if _row_val(rows[e], 'revenue') is not None or _fcf(rows[e]) is not None]
    last = ends[-1] if ends else None
    fcf, basis = (_fcf(rows[last]), f'fiscal year {last}') if last else (None, None)
    ttm = an.get('ttm') or {}
    if ttm.get('period_end') and last and ttm['period_end'] > last and _fcf(ttm) is not None:
        fcf, basis = _fcf(ttm), f"TTM to {ttm['period_end']} ({ttm.get('method')})"
    liq, parts, liq_ids = _liquidity(fb, qa)
    share_cagr = _metric(an, 'share_count_cagr_3y')
    sbc = _metric(an, 'sbc_pct_revenue')
    data = {'fcf': _num(fcf, 'CFO - capex, ' + str(basis), last),
            'liquidity': _num(liq, 'cash + short-term investments (' + ', '.join(f'{k} from {v[2]} as of {v[1]}' for k, v in (parts or {}).items()) + ')',
                              (parts or {}).get('cash', (None, None))[1]),
            'share_count_cagr_3y': _num(share_cagr, 'analysis.share_count_cagr_3y'),
            'evidence_ids': sorted(set(_src(*([_annual_fact(fb, 'cfo', last), _annual_fact(fb, 'capex', last)] if last else []))) | set(liq_ids))}
    reasons = []
    liq_date = (parts or {}).get('cash', (None, None))[1]
    if liq_date and (date.fromisoformat(fb['as_of']) - date.fromisoformat(str(liq_date)[:10])).days > STALE_ANNUAL_DAYS:
        reasons.append(f'liquidity is dated {liq_date}, more than 15 months before as_of: the runway is stale and was not updated')
    if fcf is None or liq is None:
        value = 'unknown'
        reasons.append('free cash flow or cash/short-term investments unavailable: funding cannot be judged')
    elif fcf > 0:
        bs_end = parts['cash'][1]          # debt due within a year at the same balance-sheet date, never an older one
        cur_debt = sum(f['value'] for f in (_instant_fact(fb, 'current_portion_ltd', bs_end),
                                            _instant_fact(fb, 'short_term_borrowings', bs_end)) if f)
        div = abs(_row_val(rows[last], 'dividends') or 0)
        need = cur_debt + div
        if liq + fcf >= need:
            value = 'self_funded'
            data['basis'] = 'operating_cash_flow'
            reasons.append(f'FCF {_money(fcf, cur)} ({basis}) is positive and liquidity {_money(liq, cur)} plus FCF covers '
                           f'current debt tagged at {bs_end} and dividends ({_money(need, cur)})')
        else:
            value = 'needs_external'
            reasons.append(f'FCF positive but liquidity plus FCF ({_money(liq + fcf, cur)}) does not cover near-term debt and dividends '
                           f'({_money(need, cur)}): refinancing needed')
    else:
        runway = liq / -fcf if fcf < 0 else None
        data['runway_years'] = _num(runway, '(cash + short-term investments) / annual burn (-FCF)', last)
        if runway is not None and runway < RUNWAY_YEARS:
            value = 'needs_external'
            reasons.append(f'FCF {_money(fcf, cur)} ({basis}); liquidity {_money(liq, cur)} covers about {runway:.1f} years of burn '
                           f'(< {RUNWAY_YEARS:.0f}): growth depends on new capital; dilution or debt must be modelled')
        else:
            value = 'self_funded'
            data['basis'] = 'existing_liquidity'
            reasons.append(f'cash-burning (FCF {_money(fcf, cur)}) but existing liquidity covers about '
                           f'{"many" if runway is None else f"{runway:.1f}"} years of burn: funded for now, not by operations')
    if share_cagr is not None and share_cagr > .05:
        reasons.append(f'share count grew {_pct(share_cagr)} a year over 3 years: a history of equity funding or heavy SBC dilution')
    if sbc is not None and sbc >= .10:
        reasons.append(f'SBC {_pct(sbc)} of revenue: part of the cost base is paid in shares (FCF before SBC overstates owner cash)')
    return {'value': value, 'reasons': reasons, 'data': data}


# ---------------------------------------------------------------- playbooks, drivers

def _playbooks(bm, cap):
    out = []
    for t in [bm['primary']] + bm['secondary']:
        out += PLAYBOOKS.get(t, [])
    if bm['flags']['pharma_text']:
        out.append('biotech')
    if cap['value'] == 'high':
        out.append('infrastructure')
    return list(dict.fromkeys(out))


def _driver_tree(fb, an, bm):
    tree = []
    for i, t in enumerate([bm['primary']] + bm['secondary']):
        for driver, why, metric, keys in DRIVERS.get(t, [])[: None if i == 0 else 3]:
            if any(d['driver'] == driver for d in tree):
                continue
            ev = _evidence(fb, an, keys)
            tree.append({'driver': driver, 'why': why, 'metric': metric, 'data_status': 'available' if ev else 'missing',
                         'for_type': t, 'evidence': ev})
    return tree


# ---------------------------------------------------------------- valuation method

def _valuation(fb, an, qa, bm, stage, cyc, fund, rows, inputs, cur):
    primary, flags = bm['primary'], bm['flags']
    sd = stage['data']
    om = (sd.get('operating_margin') or {}).get('value')
    rev = (sd.get('revenue') or {}).get('value')
    ends = [e for e in rows if _row_val(rows[e], 'revenue') is not None]
    has = lambda keys: bool(_evidence(fb, an, keys))
    balance_sheet = flags['text_financials'] or primary == 'financials'
    cross, lim, req = [], [], []
    conf = 'normal'

    def pick(method, arche, why, intended=None):
        return {'method': method, 'archetype': arche, 'why': why, 'intended_method': intended}

    if primary == 'financials':
        if _instant_fact(fb, 'equity') is None and not any(_row_val(rows[e], 'equity') for e in rows):
            v = pick('not_reliably_valuable', 'financials', 'bank/insurer: residual income needs book equity, which is missing', 'residual_income')
        else:
            v = pick('residual_income', 'financials', 'balance-sheet business: deposits and float are operating funding, not debt, so '
                     'FCFF and an EV bridge mis-state value; equity is valued from book value and ROE versus cost of equity')
            cross += ['price_to_tangible_book_vs_roe', 'dividend_and_excess_capital']
            lim += ['regulatory capital must be reconciled to book equity', 'valuation.residual_income holds the share count constant']
            req += ['ROE path by scenario with credit-loss cycle', 'required capital ratio and risk-weighted assets']
    elif balance_sheet:
        if has(SEGMENT_KEYS):
            v = pick('sotp', 'conglomerate', 'hybrid with a balance-sheet (bank/insurer) part: value the lending/insurance part on '
                     'residual income and the fee business separately; never one consolidated FCFF')
        else:
            v = pick('not_reliably_valuable', 'conglomerate', 'hybrid with a balance-sheet (bank/insurer) part but no segment data: '
                     'a consolidated FCFF would treat deposits/float as free funding', 'sotp')
    elif primary == 'reit':
        if has(('noi', 'net_operating_income', 'same_property_noi')):
            v = pick('nav', 'reit', 'asset-heavy holding: value = property NOI / cap rate less debt and claims (NAV)')
            cross += ['affo_yield', 'implied_cap_rate']
            lim += ['future NAV snapshots must reconcile acquisitions, development and financing']
        else:
            v = pick('not_reliably_valuable', 'reit', 'NAV needs property-level NOI and cap rates, which the FactBook does not hold', 'nav')
    elif primary == 'pre_revenue_biotech':
        if has(('pipeline', 'pipeline_assets', 'clinical_programs', 'trial')):
            v = pick('rnpv', 'biotech', 'pre-revenue drug developer: value = risk-adjusted NPV of each program less funding needs')
            lim += ['probabilities are phase base rates, not clinical predictions', 'funding and dilution through readouts must be explicit']
            req += ['program-level cash flows with phase probabilities', 'cash runway through the next readout']
        else:
            v = pick('not_reliably_valuable', 'biotech', 'pre-revenue biotech without program-level pipeline data: an rNPV would be invented',
                     'rnpv')
    elif primary == 'conglomerate':
        if has(SEGMENT_KEYS):
            v = pick('sotp', 'conglomerate', 'several economic engines: value each segment with its own method, then subtract holding costs')
            lim += ['intercompany claims and minorities must not be double counted']
        else:
            v = pick('not_reliably_valuable', 'conglomerate', 'conglomerate without segment data: a single consolidated model would blur the engines',
                     'sotp')
    elif flags['possible_financial']:
        v = pick('not_reliably_valuable', 'financials', 'balance sheet looks like a lender/insurer but there is no sector text; '
                 'confirm the business model before choosing any model', 'residual_income')
    else:
        arche = ARCHETYPE[primary]
        ever_profitable = any((_div(_row_val(rows[e], 'operating_income'), _row_val(rows[e], 'revenue')) or -1) > 0 for e in ends)
        if not rev or (cur == 'USD' and rev < PRE_REVENUE):
            v = pick('not_reliably_valuable', arche, 'no commercial revenue base: scenarios would be pure assumption; track paid adoption '
                     'and milestones until revenue exists', 'scenario_survival_fcff')
        elif len(ends) < 2 or om is None or not any(_row_val(rows[e], 'cfo') is not None for e in ends):
            v = pick('not_reliably_valuable', arche, 'an operating model needs at least two fiscal years of revenue, operating income and '
                     'operating cash flow; they are missing', 'fcff_annual_path')
        elif fund['value'] == 'needs_external' or stage['value'] == 'early' or (
                om < 0 and not sd.get('established_profit_record') and not (cyc['normalize'] and ever_profitable)):
            v = pick('scenario_survival_fcff', arche, 'loss-making or funding-dependent: value depends on reaching target economics and on '
                     'surviving the funding path; operating route with explicit financing, dilution and a failure case. Lack of profit is '
                     'not a rejection: promising-but-funding-conditional is a legitimate outcome')
            conf = 'lower'
            req += ['funding modelled in every scenario (source, price, dilution)', 'bear case includes failure / distressed financing',
                    'paid adoption and milestone evidence for the base case']
            cross += ['reverse_dcf (valuation_tools.reverse_price)', 'what_must_be_true (forecast.what_must_be_true)']
            lim += ['terminal value usually dominates; small changes in target margin move value a lot']
        elif cyc['normalize']:
            v = pick('normalized_cycle_fcff', arche, 'cyclical (' + ('analyst-confirmed' if cyc['basis'] == 'analyst' else 'reported history '
                     'shows a cycle') + '): value on a mid-cycle margin, not the latest year; peak margins are not permanent')
            cross += ['reverse_dcf (valuation_tools.reverse_price)', 'value at trough margins']
            nm = (cyc.get('normalized_operating_margin') or {}).get('value')
            req += [f'mid-cycle operating margin (history average {_pct(nm)})' if nm is not None else 'mid-cycle operating margin']
            if len([e for e in ends if _row_val(rows[e], 'operating_income') is not None]) < 5:
                lim += ['history shorter than a full cycle: the mid-cycle margin is an assumption, not an observation']
        elif stage['value'] == 'growth':
            v = pick('fcff_annual_path', arche, 'profitable grower: explicit annual path with growth fading to maturity, reinvestment '
                     'linked to growth, bear/base/bull and what must be true at the current price')
            req += ['explicit growth fade to a mature rate', 'reinvestment (sales-to-capital) linked to growth', 'bear/base/bull scenarios']
            cross += ['what_must_be_true (forecast.what_must_be_true)', 'reverse_dcf (valuation_tools.reverse_price)', 'fcf_yield_after_sbc']
            lim += ['high terminal-value share; the fade assumption dominates value']
        elif stage['value'] == 'decline':
            v = pick('fcff_annual_path', arche, 'declining business: explicit shrinking path; no growth perpetuity')
            cross += ['run_off_or_liquidation_value', 'fcf_yield_after_sbc']
            lim += ['terminal growth must be at or below zero unless a stabilization is evidenced']
        elif stage['value'] == 'turnaround':
            v = pick('fcff_annual_path', arche, 'turnaround with a record of profits: recovery path in the base case, no recovery in the bear '
                     'case, and an explicit failure scenario')
            req += ['evidence of recovery (margins, orders) for the base case', 'failure scenario with balance-sheet consequences']
            cross += ['reverse_dcf (valuation_tools.reverse_price)', 'normalized earnings at prior margins']
        else:
            v = pick('fcff_annual_path', arche, f'{stage["value"]} profitable non-financial business: annual FCFF path with moderate growth fading '
                     'toward GDP-like rates')
            cross += ['reverse_dcf (valuation_tools.reverse_price)', 'fcf_yield_after_sbc', 'earnings_yield']
            req += ['bear/base/bull scenarios', 'reinvestment linked to growth (no growth without investment)']
            lim += ['peak margins must not be treated as permanent without a moat explanation']
        if cyc['value'] and not cyc['normalize'] and v['method'] != 'not_reliably_valuable':
            nm = (cyc.get('normalized_operating_margin') or {}).get('value')
            lim.append('cyclical industry, but the reported history shows no cycle: not normalized to a historical mean'
                       + (f"; latest margin {_pct(om)} vs {_pct(nm)} average looks like a possible peak: confirm structural vs "
                          'cyclical (inputs.cyclical) before extrapolating it' if cyc.get('position') == 'peak' else ''))
        if om is not None and om < 0 and v['method'] != 'scenario_survival_fcff':
            lim.append('latest fiscal year is a loss: start the path from normal margins only with evidence that the loss is one-off')
        if bm['secondary'] and v['method'] != 'not_reliably_valuable':
            if has(SEGMENT_KEYS):
                cross.append('sotp_by_segment')
            else:
                lim.append('hybrid business (' + ', '.join(bm['secondary']) + ') without segment data: a consolidated model may blur the engines')
    if v['method'] == 'not_reliably_valuable':
        lim.append('cannot be valued reliably yet: ' + v['why'])
        conf = 'none'
    if (qa or {}).get('status') in ('blocked', 'limited'):
        lim.append(f"data validation status is {qa['status']}: a price conclusion is limited or withheld downstream")
    if v['archetype'] not in ROUTES:
        raise ValueError('internal: archetype not in valuation.ROUTES')
    computed = {'method': v['method'], 'archetype': v['archetype'], 'intended_method': v['intended_method']}
    out = {'method': v['method'], 'archetype': v['archetype'], 'route': METHOD_ROUTE.get(v['method'], ROUTES[v['archetype']]),
           'why': v['why'], 'cross_checks': list(dict.fromkeys(cross)), 'limitations': list(dict.fromkeys(lim)),
           'requirements': req, 'confidence': conf, 'intended_method': v['intended_method'],
           'default_for_type': True, 'override_reason': None, 'computed': computed}
    ov = inputs.get('method_override')
    if ov is not None:
        if not isinstance(ov, dict):
            raise ValueError('method_override must be an object with method and reason')
        txt(ov.get('reason'), 'method_override.reason')
        if ov.get('method') not in METHODS:
            raise ValueError('method_override.method must be one of ' + ', '.join(METHODS))
        if ov.get('archetype') is not None and ov['archetype'] not in ROUTES:
            raise ValueError('method_override.archetype must be a valuation.ROUTES key')
        if ov['method'] in OPERATING_METHODS and balance_sheet:
            out['override_rejected'] = {'method': ov['method'], 'reason': ov['reason'],
                                        'why_rejected': 'sector text or business model identifies a bank/insurer: an FCFF route treats '
                                                        'deposits/float as free funding. Correct the company sector/industry in the '
                                                        'FactBook if the label is wrong; the card does not relabel to force a generic model'}
        else:
            m = ov['method']
            if m == 'not_reliably_valuable':
                arche = out['archetype']
            elif ov.get('archetype'):
                arche = ov['archetype']
            elif m in METHOD_ARCHETYPE:
                arche = METHOD_ARCHETYPE[m]
            else:
                arche = ARCHETYPE.get(primary, 'nonfinancial')
                arche = arche if ROUTES[arche] == 'operating' else 'nonfinancial'
            if m != 'not_reliably_valuable' and ROUTES[arche] != METHOD_ROUTE[m]:
                raise ValueError(f'method_override: archetype {arche} routes to {ROUTES[arche]}, not {METHOD_ROUTE[m]}')
            out.update({'method': m, 'archetype': arche, 'route': METHOD_ROUTE.get(m, ROUTES[arche]), 'default_for_type': False,
                        'override_reason': ov['reason'], 'why': f"analyst override: {ov['reason']} (computed: {computed['method']} - {v['why']})"})
            if m != 'not_reliably_valuable':
                out['intended_method'] = None
    return out


# ---------------------------------------------------------------- understanding, dimensions, completeness

def _understanding(fb, inputs, fund, cur):
    valid = _valid_ids(fb)
    given = inputs.get('understanding') or {}
    if not isinstance(given, dict):
        raise ValueError('understanding must map question ids to {answer, evidence_ids}')
    unknown = set(given) - {q[0] for q in QUESTIONS}
    if unknown:
        raise ValueError('unknown understanding question ids: ' + ', '.join(sorted(unknown)))
    out = []
    for qid, question, critical in QUESTIONS:
        g = given.get(qid) or {}
        answer = g.get('answer') if isinstance(g.get('answer'), str) and g['answer'].strip() else None
        ids = list(g.get('evidence_ids') or [])
        bad = [i for i in ids if i not in valid]
        row = {'id': qid, 'question': question, 'critical': critical, 'answer': answer, 'evidence_ids': ids,
               'source': 'analyst' if answer else None}
        if answer and ids and not bad:
            row['status'] = 'evidenced'
        elif answer:
            row['status'] = 'asserted'
            row['note'] = ('evidence ids not usable at as_of (unknown, or published after as_of): ' + ', '.join(bad)) if bad else 'no evidence ids'
        else:
            row['status'] = 'open'
        if qid == 'who_funds_growth' and not answer:
            d = fund['data']
            sc = d['share_count_cagr_3y']['value']
            if fund['value'] == 'self_funded' and d.get('basis') == 'operating_cash_flow' and sc is not None and d['evidence_ids']:
                row.update({'status': 'evidenced', 'source': 'computed', 'evidence_ids': d['evidence_ids'],
                            'answer': f"operating cash flow funds growth (FCF {_money(d['fcf']['value'], cur)}); share count "
                                      f"changed {sc:+.1%} a year over 3 years"})
            else:
                row['computed_hint'] = '; '.join(fund['reasons']) or 'funding unknown'
                if fund['value'] == 'needs_external':
                    row['note'] = 'external capital is required: who provides it and at what dilution must be answered with evidence'
        out.append(row)
    return out


def _computed_dimensions(fb, an, rows, bm):
    out = {}
    ends = [e for e in rows if _row_val(rows[e], 'revenue')][-5:]
    ev = lambda *ms: _src(*[_annual_fact(fb, m, e) for m in ms for e in ends])
    gms = [_div(_row_val(rows[e], 'gross_profit'), _row_val(rows[e], 'revenue')) for e in ends]
    gms = [g for g in gms if g is not None]
    if bm['primary'] != 'financials' and len(gms) >= 3:
        rng, trend, last = max(gms) - min(gms), gms[-1] - gms[0], gms[-1]
        if trend <= -.05:
            a = 'weak'
        elif rng <= .05 and last >= .6:
            a = 'strong'
        elif rng <= .05 and last >= .35:
            a = 'adequate'
        else:
            a = 'unknown'
        out['pricing_power'] = {'assessment': a, 'evidence_ids': ev('revenue', 'gross_profit'), 'source': 'computed',
                                'note': f'proxy: gross margin {_pct(last)}, range {_pct(rng)} and change {trend:+.1%} over {len(gms)} years '
                                        '(stable high margins are consistent with pricing power; a proxy, not proof)'}
    roic, inc = _metric(an, 'roic'), _metric(an, 'incremental_roic_3y')
    sc, bb, acq = _metric(an, 'share_count_cagr_3y'), _metric(an, 'buybacks_pct_fcf_3y'), _metric(an, 'acquisition_spend_pct_revenue_3y')
    if roic is not None and bm['primary'] != 'financials':
        notes = [f'ROIC {_pct(roic)}', f'incremental ROIC 3y {_pct(inc)}', f'share count {_pct(sc)}/yr',
                 f'buybacks {_pct(bb)} of FCF (3y)', f'acquisitions {_pct(acq)} of revenue (3y)']
        if roic < .06 and (acq or 0) >= .10 or (sc is not None and sc > .05 and roic < .10):
            a = 'weak'
        elif roic >= .15 and (inc is None or inc >= .10) and (sc is None or sc <= 0):
            a = 'strong'
            if bb is not None and bb > 1.1:
                a = 'adequate'
                notes.append('buybacks exceeded FCF by more than 10%: partly debt-funded')
        else:
            a = 'adequate'
        if bb is not None and 1.0 < bb <= 1.1:
            notes.append('buybacks slightly exceeded FCF')
        out['capital_allocation'] = {'assessment': a, 'evidence_ids': ev('operating_income', 'cfo', 'buybacks'), 'source': 'computed',
                                     'note': 'proxy from the record: ' + ', '.join(notes) + ' (rules: weak if ROIC < 6% with heavy '
                                             'acquisitions or >5%/yr dilution with ROIC < 10%; strong if ROIC >= 15%, incremental '
                                             '>= 10% and no net dilution)'}
    return out


def _dimensions(fb, an, rows, bm, inputs):
    given = inputs.get('assessments') or {}
    if not isinstance(given, dict):
        raise ValueError('assessments must map dimension names to {assessment, evidence_ids, note}')
    unknown = set(given) - set(DIMENSIONS)
    if unknown:
        raise ValueError('unknown assessment dimensions: ' + ', '.join(sorted(unknown)))
    valid = _valid_ids(fb)
    computed = _computed_dimensions(fb, an, rows, bm)
    roic = _metric(an, 'roic')
    out = {}
    for d in DIMENSIONS:
        g = given.get(d)
        if g is not None:
            if not isinstance(g, dict) or g.get('assessment') not in LEVELS:
                raise ValueError(f'assessments.{d}.assessment must be one of ' + ', '.join(LEVELS))
            ids = list(g.get('evidence_ids') or [])
            bad = [i for i in ids if i not in valid]
            row = {'assessment': g['assessment'], 'evidence_ids': ids, 'note': g.get('note'), 'source': 'analyst',
                   'evidence_status': 'evidenced' if ids and not bad else 'asserted'}
            if bad:
                row['unknown_evidence_ids'] = bad
            if d in computed:
                row['computed'] = computed[d]
        elif d in computed:
            row = computed[d]
        else:
            note = 'not assessed: no analyst assessment and no data proxy (unknown is a legitimate value)'
            if d == 'moat' and roic is not None and roic >= .15:
                note += f'; ROIC {_pct(roic)} is consistent with an advantage if persistent, but its source must be named'
            if d == 'customer_concentration':
                note = 'unknown unless supplied from filings (largest-customer disclosure)'
            row = {'assessment': 'unknown', 'evidence_ids': [], 'note': note, 'source': None}
        out[d] = row
    return out


def _completeness(bm, stage, tree, val, und):
    crit = [q for q in und if q['critical']]
    open_c = [q['id'] for q in crit if q['status'] == 'open']
    not_ev = [q for q in crit if q['status'] != 'evidenced']
    bm_evidence = bm['evidence'] != 'none'
    status = 'complete' if not not_ev else ('insufficient' if open_c and not bm_evidence else 'partial')
    missing = [f"understanding:{q['id']} ({q['status']})" for q in not_ev]
    if not bm_evidence:
        missing.append('business_model: no sector text, financial signature or override supports a type')
    missing += [f"driver:{d['driver']}" for d in tree if d['data_status'] == 'missing' and d['for_type'] == bm['primary']]
    if stage['confidence'] == 'low':
        missing.append('stage: provisional (stale or short history)')
    if val['method'] == 'not_reliably_valuable':
        missing.append('valuation: ' + val['why'])
    return {'status': status, 'missing': missing,
            'rule': 'complete when every critical question is evidenced; insufficient when a critical question is open and the '
                    'business model has no evidence; otherwise partial'}


# ---------------------------------------------------------------- entry point

def build(fb, an, qa, inputs=None):
    """Model card for one company at fb['as_of'] (docs/MODULE_CONTRACTS.md, model_card.build)."""
    inputs = inputs or {}
    if not isinstance(inputs, dict):
        raise ValueError('inputs must be an object')
    an = an or {}
    company = fb.get('company') or {}
    cur = company.get('currency') or 'USD'
    notes = []
    extra = set(inputs) - {'business_model', 'stage', 'assessments', 'understanding', 'method_override', 'cyclical'}
    if extra:
        notes.append('ignored unknown input keys: ' + ', '.join(sorted(extra)))
    rows = _years(an)
    ends = [e for e in rows if _row_val(rows[e], 'revenue') is not None]
    last = ends[-1] if ends else None
    sig = _signature(fb, rows, last)
    bm = _business_model(fb, rows, last, sig, inputs)
    cyc = _cyclical(fb, rows, bm['primary'], inputs)
    stage = _stage(fb, an, rows, inputs, cur, cyc['value'])
    stage['cross_check'] = _profile_cross_check(rows, ends, fb['as_of'], bm['primary']) if ends else None
    cross = stage['cross_check'] or {}
    if cross.get('lifecycle') in ('start_up', 'young_growth') and stage['computed'] in ('mature', 'maturing', 'turnaround', 'decline'):
        stage['reasons'].append(f"profile.classify says {cross['lifecycle']} (it ignores scale and profit history); "
                                f"the card keeps {stage['computed']}")
    cap = _capital_intensity(rows, sig)
    fund = _funding(fb, an, qa, rows, cur)
    tree = _driver_tree(fb, an, bm)
    prim = [d for d in tree if d['for_type'] == bm['primary']]
    avail = sum(d['data_status'] == 'available' for d in prim)
    coverage = {'available': avail, 'total': len(prim),
                'forecast_basis': 'driver_level' if prim and avail * 2 > len(prim) else 'revenue_level'}
    if coverage['forecast_basis'] == 'revenue_level':
        notes.append(f"business-model drivers mostly missing ({avail} of {len(prim)} available): the forecast relies on revenue-level "
                     'drivers (growth, margins, reinvestment) and must say so')
    val = _valuation(fb, an, qa, bm, stage, cyc, fund, rows, inputs, cur)
    und = _understanding(fb, inputs, fund, cur)
    dims = _dimensions(fb, an, rows, bm, inputs)
    srev = (stage['data'].get('revenue') or {}).get('value')
    pkey = ('start_up' if stage['value'] == 'early' and (not srev or (cur == 'USD' and srev < PRE_REVENUE))
            else STAGE_TO_PROFILE.get(stage['value'], 'young_growth'))
    play = profile.PLAYBOOK[pkey]
    packets = list(play['packets'])
    if stage['value'] == 'turnaround':
        packets += ['revenue_decline', 'funding', 'forensic']
    if cyc['value']:
        packets.append('forensic')
    if stage['value'] in ('early', 'growth') and fund['value'] != 'self_funded':
        packets += ['frontier_research', 'funding']
    focus = {'profile_stage': pkey, 'key_metrics': list(play['metrics']), 'traps': list(play['traps']),
             'packets': list(dict.fromkeys(packets))}
    if not ends:
        notes.append('analysis has no fiscal year with revenue: the card is mostly unknowns')
    if (qa or {}).get('status') and qa['status'] != 'ok':
        notes.append(f"data validation status {qa['status']}: blocking {qa.get('blocking') or []}, limited {qa.get('limited') or []}")
    return {'card_version': CARD_VERSION, 'ticker': company.get('ticker'), 'as_of': fb['as_of'], 'currency': cur,
            'business_model': bm, 'stage': stage, 'cyclical': cyc, 'capital_intensity': cap, 'funding': fund,
            'playbooks': _playbooks(bm, cap), 'research_focus': focus, 'driver_tree': tree, 'driver_coverage': coverage,
            'valuation': val, 'understanding': und, 'dimensions': dims,
            'completeness': _completeness(bm, stage, tree, val, und), 'notes': notes,
            'meaning': 'How the company makes money and therefore which drivers, questions and valuation method apply. '
                       'Rules are deterministic and coarse; every label carries its reasons and can be overridden only with a '
                       'written reason, kept next to the computed value. Not a quality score and not a verdict.'}
