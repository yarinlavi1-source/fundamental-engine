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
scenario model and asked for paid adoption, milestone progress and an explicit failure scenario. A profitable,
cash-generating company that needs outside capital (capex above CFO, a refinancing) keeps the annual FCFF path
with the financing flagged (valuation.funding_flag), not the survival model.
A bank or insurer is never routed to FCFF, nor is a lender-shaped balance sheet under a non-financial label
(flags.lender_shaped) unless an analyst business_model states why; an unsupported sector is never relabelled to
force a generic model. Pure function of (fb, an, qa, inputs); reads facts only through facts.py accessors.
"""
from datetime import date
from statistics import mean, pstdev
import re
from . import facts as F
from .history import cagr, acquisition_years, organic_cagr, history_shape, txt
from .valuation import ROUTES
from . import profile

CARD_VERSION = 1
TYPES = ('subscription_software', 'platform_marketplace', 'semiconductor', 'hardware_manufacturing',
         'capital_intensive_infrastructure', 'consumer_brand', 'industrial', 'financials', 'pre_revenue_biotech',
         'conglomerate', 'reit', 'commodity', 'healthcare_products', 'healthcare_services', 'it_services', 'distribution')
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
             'healthcare_products': 'nonfinancial', 'healthcare_services': 'nonfinancial', 'it_services': 'nonfinancial',
             'distribution': 'industrial', UNCLASSIFIED: 'nonfinancial'}
PLAYBOOKS = {'subscription_software': ['software'], 'platform_marketplace': ['platform'], 'semiconductor': ['industrial'],
             'hardware_manufacturing': ['industrial'], 'capital_intensive_infrastructure': ['infrastructure'],
             'consumer_brand': ['consumer'], 'industrial': ['industrial'], 'financials': ['financials'],
             'pre_revenue_biotech': ['biotech'], 'conglomerate': ['conglomerate'], 'reit': ['infrastructure'],
             'commodity': ['industrial'], 'healthcare_products': ['biotech', 'industrial'], 'healthcare_services': ['industrial'],
             'it_services': ['industrial'], 'distribution': ['industrial'], UNCLASSIFIED: []}
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
# Fee-based payment networks, exchanges and insurance brokers are platforms (commission on volume they do not
# carry on their balance sheet); balance-sheet lenders and insurers are financials. Financial conglomerates are
# financials (checked before the generic conglomerate rule). Labels that list fee firms and balance-sheet firms
# together are BALANCE_MIXED_RULES: decided by the balance sheet, financials when it is lender-like or unknown.
FEE_FINANCIAL = ('transaction & payment', 'payment processing', 'financial exchanges', 'exchanges & data', 'stock exchanges',
                 'financial data', 'insurance broker')
TEXT_RULES = (
    (('financial conglomerate',), 'financials'),
    (('industrial conglomerate', 'conglomerate', 'multi-sector holding', 'diversified holding'), 'conglomerate'),
    (('mortgage reit', 'reit mortgage', 'mortgage real estate'), 'financials'),
    (FEE_FINANCIAL, 'platform_marketplace'),
    (('real estate services', 'real estate brokerage'), 'industrial'),
    (('bank', 'thrift', 'savings institution', 'mortgage finance', 'mortgages', 'consumer finance', 'credit services',
      'insurance', 'reinsurance', 'capital markets', 'investment banking', 'brokerage', 'asset management', 'custody',
      'specialty finance', 'specialized finance', 'diversified financial', 'financial services', 'financials'), 'financials'),
    (('reit', 'real estate investment trust', 'equity real estate', 'real estate operating', 'real estate development',
      'real estate holding', 'diversified real estate', 'real estate'), 'reit'),
    (('biotechnology', 'biotech', 'pharmaceutical', 'drug manufacturer'), 'pre_revenue_biotech'),
    (('health care equipment', 'medical device', 'medical instrument', 'medical/dental', 'medical electronics',
      'medical specialities', 'medical specialties', 'diagnostic', 'life sciences tools', 'laboratory analytical',
      'electromedical', 'surgical', 'orthopedic', 'ophthalmic', 'health care supplies'), 'healthcare_products'),
    (('hospital', 'nursing', 'health care services', 'health care facilities', 'medical laboratories', 'home health',
      'health care providers'), 'healthcare_services'),
    (('it services', 'edp services', 'it consulting', 'computer programming', 'data processing', 'integrated systems design',
      'information technology services', 'computer services'), 'it_services'),
    (('wholesale', 'distributors', 'food distribution', 'industrial distribution'), 'distribution'),
    (('semiconductor',), 'semiconductor'),
    (('application software', 'systems software', 'software'), 'subscription_software'),
    (('interactive media', 'internet content', 'internet & direct marketing', 'internet retail', 'marketplace',
      'online travel'), 'platform_marketplace'),
    (('data center', 'internet services & infrastructure', 'utilities', 'utility', 'independent power',
      'renewable electricity', 'telecom', 'wireless', 'cable', 'tower', 'storage & transportation', 'midstream',
      'pipeline', 'pipe lines', 'natural gas distribution', 'airport', 'toll road', 'railroad'), 'capital_intensive_infrastructure'),
    (('oil & gas', 'oil and gas', 'integrated oil', 'exploration & production', 'refining', 'coal', 'metals & mining',
      'mining', 'gold', 'silver', 'copper', 'steel', 'aluminum', 'uranium', 'commodity chemicals', 'fertilizer',
      'agricultural products', 'paper & forest', 'forest products', 'construction materials', 'petroleum', 'oilfield',
      'precious metals', 'paper'), 'commodity'),
    (('technology hardware', 'computer hardware', 'electronic equipment', 'electronic components',
      'communications equipment', 'consumer electronics', 'automobile manufacturer', 'auto manufacturer', 'automobiles',
      'scientific & technical instruments', 'storage & peripherals', 'computer manufacturing', 'electronic computers',
      'computer peripheral', 'computer & office equipment', 'printed circuit', 'electronic connectors', 'electrical products'),
     'hardware_manufacturing'),
    (('household products', 'personal products', 'personal care', 'beverage', 'food', 'apparel', 'footwear',
      'textile', 'luxury', 'restaurant', 'retail', 'tobacco', 'leisure', 'hotel', 'casino', 'consumer staples',
      'consumer discretionary', 'consumer defensive', 'consumer cyclical', 'distiller', 'brewer', 'soft drink',
      'confectioner', 'household durables', 'home furnishing'), 'consumer_brand'),
    (('industrials', 'industrial', 'machinery', 'aerospace', 'defense', 'building products', 'construction',
      'engineering', 'electrical equipment', 'trading companies', 'airline', 'marine', 'trucking', 'freight',
      'logistics', 'commercial services', 'professional services', 'environmental services', 'auto parts',
      'chemical', 'packaging', 'waste'), 'industrial'),
)
# Labels that mix platforms with operators: (keywords, default type, platform type, what the label mixes).
# The platform type is primary only when the signature is a platform's (gross margin >= 70% and capex <= 8% of
# revenue; with no cost-of-revenue line, capex <= 8% and operating margin >= 20%); the other type is kept as a
# secondary type either way.
MIXED_RULES = (
    (('broadline retail',), 'consumer_brand', 'platform_marketplace', 'department stores and online marketplaces'),
    (('hotels, resorts & cruise', 'hotels resorts & cruise', 'travel services'), 'consumer_brand', 'platform_marketplace',
     'hotel and cruise operators and online-travel platforms'),
)
# Labels that list fee businesses with balance-sheet firms: (keywords, default type, fee type, what the label mixes).
# The fee type is used only when the balance sheet is known and not lender-like (liabilities < 85% of assets);
# otherwise financials (conservative). Checked before TEXT_RULES ('Asset Management & Custody Banks' has 'bank').
BALANCE_MIXED_RULES = (
    (('asset management', 'credit services'), 'financials', 'platform_marketplace',
     'fee businesses (asset managers, payment networks) and balance-sheet firms (custody banks, lenders)'),
)
LENDER_LIABILITIES = .85     # liabilities / assets at or above this with no cost of revenue: lender/insurer balance sheet
# A lender's assets are financial (loans, securities): revenue is a small share of assets and capex is small. Under a
# non-financial label the balance sheet decides only when these also hold (an airline or an online-travel agency with thin
# equity and no cost-of-revenue line is not a loan book). Without a cost-of-revenue line, or with lending data
# (LENDING_KEYS), such a balance sheet is a lender's; fee-platform labels (FEE_FINANCIAL: client funds, settlement and
# clearing balances) need the lending data.
LENDER_TURNOVER = .30        # revenue / total assets at or below this
LENDER_CAPEX = .10           # capex / revenue below this (or unknown)
LENDING_KEYS = ('interest_income', 'net_interest_income', 'net_interest_margin', 'loans', 'net_loans', 'loans_receivable',
                'finance_receivables', 'deposits', 'total_deposits', 'customer_deposits')
NON_OPERATING_TYPES = ('financials', 'reit', 'pre_revenue_biotech', 'conglomerate')
CYCLICAL_WORDS = ('semiconductor', 'oil', 'mining', 'metals', 'steel', 'chemical', 'automobile', 'auto ',
                  'airline', 'homebuild', 'residential construction', 'machinery', 'paper', 'forest', 'marine',
                  'shipping', 'trucking', 'freight', 'building products', 'construction', 'hotel', 'casino', 'leisure',
                  'coal', 'aluminum', 'copper', 'gold', 'silver', 'travel', 'memory', 'commodity', 'fertilizer',
                  'refining', 'household durables', 'petroleum')
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
    'healthcare_products': [
        ('products_exclusivity', 'revenue sits in specific products; patent expiry and generic or biosimilar entry decide how long it lasts',
         'revenue by product and loss-of-exclusivity dates', ('product_revenue', 'patent_expiry', 'loss_of_exclusivity', 'exclusivity')),
        ('pipeline_launches', 'late-stage programs and launches replace products that lose exclusivity',
         'late-stage programs, approvals and launches', ('pipeline', 'launches', 'approvals', 'clinical_programs')),
        ('net_price_reimbursement', 'net price after rebates, payer and government negotiation decides the margin',
         'net price / reimbursement', ('net_price', 'pricing', 'reimbursement', 'rebates')),
        ('volume_installed_base', 'patients treated, procedures, placements and consumables pulled through',
         'volume / procedures / installed base', ('procedures', 'units', 'installed_base', 'placements', 'volume')),
        ('rd_productivity', 'research spend versus products approved', 'R&D / revenue', ('rd',)),
    ],
    'healthcare_services': [
        ('volume', 'patients, admissions, tests or studies performed', 'admissions / visits / tests / backlog',
         ('admissions', 'visits', 'tests', 'procedures', 'volume', 'backlog', 'book_to_bill')),
        ('reimbursement_mix', 'payer mix and rates (government versus commercial) decide revenue per unit',
         'revenue per unit and payer mix', ('payer_mix', 'reimbursement', 'revenue_per_unit', 'rate')),
        ('labor_cost', 'staff and wage inflation are the main cost', 'salaries / revenue', ('salaries', 'labor_cost', 'wages', 'sga')),
        ('capital', 'facilities and equipment needed per unit of volume', 'capex / revenue', ('capex_pct_revenue',)),
    ],
    'it_services': [
        ('bookings_backlog', 'bookings and backlog lead revenue', 'bookings / backlog / book-to-bill',
         ('bookings', 'backlog', 'book_to_bill', 'remaining_performance_obligations')),
        ('headcount_utilization', 'revenue = billable staff x utilization x rate; AI automation can shrink billable work',
         'headcount and utilization', ('headcount', 'employees', 'utilization', 'billable')),
        ('rate_vs_wages', 'bill rates versus wage inflation set the margin', 'pricing and wage costs', ('pricing', 'bill_rate', 'wages')),
        ('client_concentration', 'large clients and government contracts', 'largest clients share', ('customer_concentration', 'largest_customer')),
    ],
    'distribution': [
        ('volume_customers', 'units, lines and customers served', 'volume / customers', ('volume', 'units', 'customers')),
        ('gross_margin', 'pricing and supplier rebates', 'gross profit / revenue', ('gross_margin',)),
        ('operating_cost', 'warehouse, freight and selling cost per dollar of sales decide the thin operating margin',
         'SG&A / revenue', ('sga',)),
        ('working_capital', 'inventory and receivables financed against supplier payables', 'inventory, receivables, payables / revenue',
         ('inventory', 'receivables', 'payables')),
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
        if k in SEGMENT_KEYS and ((fb.get('business_map') or {}).get('tables') or {}).get('segment'):
            found.append({'kind': 'business_map', 'key': k, 'source_ids': [fb['business_map'].get('source_id')]})
            continue
        if _metric(an, k) is not None:
            found.append({'kind': 'analysis_metric', 'key': k})
        elif F.candidates(fb, k):
            found.append({'kind': 'fact', 'key': k, 'source_ids': _src(*F.candidates(fb, k)[:1])})
        if quals.get(_norm(k)):
            found.append({'kind': 'qualitative', 'key': k, 'ids': quals[_norm(k)]})
    return found


def _valid_ids(fb):
    """Source ids usable as evidence at as_of and PIT-usable qualitative ids. A dated source counts when published by
    as_of. An undated source with facts counts only when at least one of its facts is usable at as_of (facts.usable), so a
    filing whose facts all become available later is not evidence at as_of; an undated source without facts is kept."""
    cut = date.fromisoformat(fb['as_of'])
    usable, has = set(), set()
    for f in fb.get('facts') or []:
        has.add(f.get('source_id'))
        if f.get('source_id') not in usable and F.usable(fb, f):
            usable.add(f['source_id'])
    day = lambda x: date.fromisoformat(str(x)[:10])
    ok = set()
    for sid, src in (fb.get('sources') or {}).items():
        src = src or {}
        if src.get('published_at'):
            good = day(src['published_at']) <= cut
        else:
            good = sid in usable or sid not in has
        if good:
            ok.add(sid)
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
                ('liabilities_to_assets', 'total liabilities / total assets'), ('revenue_to_assets', 'revenue / total assets'))} | {
            'has_cost_of_revenue': False}
    capex = _row_val(r, 'capex')
    capex = abs(capex) if capex is not None else None
    rd = _annual_fact(fb, 'rd', end)
    inv = _instant_fact(fb, 'inventory', end)
    ta, tl = _instant_fact(fb, 'total_assets', end), _instant_fact(fb, 'total_liabilities', end)
    cor = _annual_fact(fb, 'cost_of_revenue', end)
    gp = _row_val(r, 'gross_profit')
    # filers that tag cost of revenue but no gross-profit line (Alphabet): gross margin from revenue - cost of revenue
    gm = (_num(_div(gp, rev), 'gross_profit / revenue', end) if gp is not None or not (cor and rev) else
          _num(_div(rev - cor['value'], rev), '(revenue - cost_of_revenue) / revenue (no gross-profit line)', end))
    return {
        'gross_margin': gm,
        'capex_pct_revenue': _num(_div(capex, rev), 'capex / revenue', end),
        'deferred_revenue_pct_revenue': _num(_div(_row_val(r, 'deferred_revenue'), rev),
                                             'deferred revenue (current + noncurrent) / revenue', end),
        'rd_pct_revenue': _num(_div(rd['value'] if rd else None, rev), 'R&D expense / revenue', end),
        'inventory_pct_revenue': _num(_div(inv['value'] if inv else None, rev), 'inventory / revenue', end),
        'capex_to_da': _num(_div(capex, _row_val(r, 'd_and_a')), 'capex / depreciation and amortization', end),
        'liabilities_to_assets': _num(_div(tl['value'] if tl else None, ta['value'] if ta else None),
                                      'total liabilities / total assets', end),
        'revenue_to_assets': _num(_div(rev, ta['value'] if ta else None), 'revenue / total assets', end),
        'has_cost_of_revenue': cor is not None or _row_val(r, 'gross_profit') is not None,
    }


def clean_text(s):
    """Lower case, dashes as spaces, whitespace collapsed (vendor 'Medical - Devices' == GICS 'Medical Devices')."""
    return re.sub(r'\s+', ' ', re.sub(r'[-—–]', ' ', str(s or '').lower())).strip()


def _hit(kws, low):
    return next((k for k in kws if re.search(r'(?<![a-z])' + re.escape(clean_text(k)), low)), None)


# Mentions of semiconductor/wafer/lithography per 100,000 words of the annual report above which an industrial or hardware label
# is read as semiconductor equipment. A heuristic, not a calibrated cut: ASML's 2025 20-F has about 260; a machinery maker that
# only names chip shortages as a supply risk has a few. A business_model override with a reason replaces it.
SEMI_TERMS_DENSITY = 40
# Item 1 vocabulary densities (business.BUSINESS_TERMS, per 10,000 words) that decide the type where only a sector or no
# label matched, first match wins. Set on 34 annual reports (2025-2026): payment networks 85-87 vs at most 11 elsewhere;
# marketplaces 35-218 vs at most 27; defense 216-372 vs at most 63; vehicles 56-209 (GD 48); IT and payroll services 77-185
# vs at most 53 (Visa). Reports without a findable Item 1 stay on their label.
# Rechecked 2026-10-25 on the 153 Item 1 sections of the 2026-10-21 universe (vocabulary revised in business.BUSINESS_TERMS):
# marketplaces 35-222 (Zillow, Booking, Coupang, Uber, MercadoLibre, DoorDash) vs at most 27 (Mastercard's 'merchants', decided
# by its payment-network terms first); online travel 44-58 (Expedia, Booking) vs at most 7 (Southwest's 'travelers');
# advertising-funded audiences 43-85 (Alphabet, Reddit, Pinterest) vs at most 10 (Amazon, Fox). Classification cuts set on the
# documents' words, not on any valuation or price outcome.
BUSINESS_TERM_RULES = (('payment_network', 40, 'platform_marketplace'), ('marketplace', 30, 'platform_marketplace'),
                       ('online_travel', 20, 'platform_marketplace'), ('advertising', 25, 'platform_marketplace'),
                       ('defense', 150, 'industrial'), ('vehicles', 50, 'hardware_manufacturing'),
                       ('it_services', 70, 'it_services'))
NON_LABEL_TYPES = ('financials', 'reit', 'pre_revenue_biotech', 'conglomerate')   # balance-sheet routes are never relabelled by words
# Customers prepay a subscription: deferred revenue at or above this share of revenue is the subscription marker of the
# financial signature (_business_model sig_types). Ad-funded and transaction-funded services bill as they deliver: under the
# IT/data-processing labels Meta, Pinterest, Reddit and AppLovin carried 0-3% of revenue against 16-94% for Zoom, Verisign,
# Zscaler, Verisk and Samsara (2016-2026 filings, 2026-10-21 universe).
SUBSCRIPTION_DEFERRED = .15
# SEC codes under which two-sided platforms file next to operators (the SIC system predates them): transportation services
# (Booking and Expedia next to freight brokers), nonstore retailers (online marketplaces next to catalog and mail-order
# retailers) and business services outside computer services (Visa, Mastercard, eBay, Uber, DoorDash, MercadoLibre and Zillow
# under 7389 'Business Services, NEC', Airbnb under 7340, next to consultancies, processors, staffing and equipment lessors).
# For these codes a type that comes only from a label -- the listing sector (Nasdaq files every 7389 filer under 'Consumer
# Discretionary', Visa and Accenture included) or a catch-all such as 'Diversified Commercial Services' -- is a default: when
# the annual report's business section did not decide (none filed by as_of, as in every walk-forward year before the saved
# report, or no Item 1 rule met), the platform signature decides as for MIXED_RULES labels (platform_signature; the label type
# kept as secondary). A loss-making or thin-margin marketplace (Uber, DoorDash) does not show the signature: it keeps the label
# type, and the reason says the business section or a reasoned business_model override decides. Computer services (7370-7379)
# have their own IT/data-processing rule.
INTERMEDIARY_SIC = ((4700, 4799, 'transportation services: online travel agencies next to freight forwarders and brokers'),
                    (5960, 5969, 'nonstore retailers: online marketplaces next to catalog and mail-order retailers'),
                    (7300, 7369, 'business services: online platforms next to agencies, staffing firms and equipment lessors'),
                    (7380, 7399, "business services 'not elsewhere classified': payment networks and marketplaces next to "
                                 'consultancies and processors'))
LABEL_DEFAULT_TYPES = ('consumer_brand', 'industrial')   # what a sector or catch-all services label yields under those codes
# Listing labels that name a bucket, not an industry ('Diversified Commercial Services' covers Airbnb, equipment renters and
# pest control; 'Miscellaneous', 'Other ...', the SEC's 'NEC'): under INTERMEDIARY_SIC they are a default like the sector. A
# specific label ('Integrated Freight & Logistics' for C.H. Robinson) is kept.
CATCH_ALL_LABEL = re.compile(r'(?<![a-z])(?:diversified|miscellaneous|misc|other|nec|not elsewhere|business services|'
                             r'commercial services)(?![a-z])')


def intermediary_sic(sic):
    """The INTERMEDIARY_SIC entry (lo, hi, what) covering an SEC SIC code, or None."""
    return next((r for r in INTERMEDIARY_SIC if isinstance(sic, int) and r[0] <= sic <= r[1]), None)


# SEC codes whose label does not tell the economics (equipment for chip makers or for food plants): the annual report is
# collected so its own words can decide (scripts/collect_companies.py --docs-when-needed)
AMBIGUOUS_SIC = (3559, 3825, 3827, 3829)

# SEC Standard Industrial Classification ranges whose economics live on the balance sheet (same ranges as universe.py):
# banks, credit companies, broker-dealers and insurance carriers (incl. managed care, SIC 6324) are financials; 6798 REIT.
# A SIC code from the filer's own SEC record outranks a listing label ('Medical Specialities' for Cigna)
SIC_TYPES = ((6000, 6199, 'financials'), (6200, 6221, 'financials'), (6300, 6399, 'financials'), (6798, 6798, 'reit'),
             # wholesale trade and health services: the SIC is more precise than listing labels ('Other Pharmaceuticals' for
             # Cardinal Health, 'Medical Specialities' for Quest and LabCorp, 'Automotive Aftermarket' for Genuine Parts)
             (5000, 5199, 'distribution'), (8000, 8099, 'healthcare_services'))
# Used when no industry label matches (before a sector-level fallback): the filer's own SIC code. 3610-3629 is electrical
# transmission and industrial apparatus (Powell's switchgear, SIC 3613), sold to utilities, plants and data centers.
SIC_FALLBACK = ((2833, 2836, 'healthcare_products'), (3826, 3826, 'healthcare_products'), (3840, 3851, 'healthcare_products'),
                (8731, 8734, 'healthcare_services'), (7370, 7379, 'it_services'), (3570, 3579, 'hardware_manufacturing'),
                (3660, 3699, 'hardware_manufacturing'), (3610, 3629, 'industrial'), (1000, 1499, 'commodity'), (2600, 2631, 'commodity'),
                (2900, 2999, 'commodity'), (4600, 4619, 'capital_intensive_infrastructure'),
                (4900, 4949, 'capital_intensive_infrastructure'))


def _text_matches(company):
    out = []
    sic = company.get('sic')
    if isinstance(sic, int):
        t = next((t for lo, hi, t in SIC_TYPES if lo <= sic <= hi), None)
        if t:
            out.append({'field': 'industry', 'text': f"SEC SIC {sic} ({company.get('sic_description') or ''})", 'keyword': f'SIC {sic}',
                        'type': t})
            return out
    fb_sic = next((t for lo, hi, t in SIC_FALLBACK if lo <= sic <= hi), None) if isinstance(sic, int) else None
    for field in ('industry', 'sector'):
        raw = company.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        low = clean_text(raw)
        for kws, t, alt, mixes, sig in [r + ('platform',) for r in MIXED_RULES] + [r + ('balance_sheet',) for r in BALANCE_MIXED_RULES]:
            hit = _hit(kws, low)
            if hit:
                out.append({'field': field, 'text': raw, 'keyword': hit, 'type': t, 'alt': alt, 'mixes': mixes, 'signature': sig})
                break
        else:
            for kws, t in TEXT_RULES:
                hit = _hit(kws, low)
                if hit:
                    out.append({'field': field, 'text': raw, 'keyword': hit, 'type': t})
                    break
    if fb_sic and not any(t['field'] == 'industry' for t in out):
        # no industry label matched: the filer's SIC code ranks ahead of a sector-level fallback
        out.insert(0, {'field': 'industry', 'text': f"SEC SIC {sic} ({company.get('sic_description') or ''})", 'keyword': f'SIC {sic}',
                       'type': fb_sic})
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


def platform_signature(gm, cx, om=None):
    """(True/False/None, rule text): the platform signature for labels that mix platforms with operators. Gross margin
    >= 70% and capex <= 8% of revenue; with no cost-of-revenue line, capex <= 8% and operating margin >= 20%; with capex
    unknown, gross margin >= 70% and operating margin >= 20%. None when the inputs that decide it are missing (undetermined:
    the label's default type is used and the reason says so)."""
    if gm is not None and cx is not None:
        return gm >= .70 and cx <= .08, 'platform when gross margin >= 70% and capex <= 8%'
    if gm is None and cx is not None and om is not None:
        return cx <= .08 and om >= .20, 'no cost-of-revenue line: platform when capex <= 8% and operating margin >= 20%'
    if gm is not None and cx is None and om is not None:
        return gm >= .70 and om >= .20, 'capex unknown: platform when gross margin >= 70% and operating margin >= 20%'
    return None, 'gross margin, capex or operating margin unknown: platform signature undetermined'


# ---------------------------------------------------------------- business model

def _business_model(fb, an, rows, end, sig, inputs):
    company = fb.get('company') or {}
    text = _text_matches(company)
    used = next((t for t in text if t['field'] == 'industry'), text[0] if text else None)   # sector only as a fallback
    sv = {k: (v['value'] if isinstance(v, dict) else v) for k, v in sig.items()}
    reasons, secondary = [], []
    rev = _row_val(rows.get(end), 'revenue') if end else None
    gm, dr, cx, inv = sv['gross_margin'], sv['deferred_revenue_pct_revenue'], sv['capex_pct_revenue'], sv['inventory_pct_revenue']
    om_end = _div(_row_val(rows.get(end), 'operating_income'), rev) if end else None
    for t in text:
        reasons.append(f"{t['field']} '{t['text']}' matches '{t['keyword']}' -> {t['type']}"
                       + ('' if t is used else ' (not used: the industry label is more specific)'))
    text_types = [used['type']] if used else []
    lta = sv['liabilities_to_assets']
    lender_sig = lta is not None and lta >= LENDER_LIABILITIES and not sv['has_cost_of_revenue']
    if used and used.get('signature') == 'balance_sheet':
        if lta is not None and lta < LENDER_LIABILITIES:
            text_types = [used['alt']]
            reasons.append(f"'{used['text']}' lists {used['mixes']}: liabilities {_pct(lta)} of assets (not lender-like) -> "
                           f"{used['alt']} (fee business); financials not kept")
        else:
            text_types = [used['type'], used['alt']]
            reasons.append(f"'{used['text']}' lists {used['mixes']}: liabilities {_pct(lta)} of assets ("
                           + ('lender-like' if lta is not None else 'unknown') + f") -> financials (conservative); {used['alt']} kept "
                           'as secondary; a reasoned business_model override can clear it when the balance sheet is fiduciary')
    elif used and used.get('alt'):
        plat, rule = platform_signature(gm, cx, om_end)
        text_types = [used['alt'], used['type']] if plat else [used['type'], used['alt']]
        reasons.append(f"'{used['text']}' mixes {used['mixes']}: gross margin {_pct(gm)}, capex {_pct(cx)} of revenue, operating margin "
                       f"{_pct(om_end)} -> {text_types[0]} (rule: {rule}" + ('; undetermined, so the default type is used' if plat is None else '')
                       + f'); {text_types[1]} kept as secondary')
    text_financials = 'financials' in text_types
    fin_text = text_financials or any(t['type'] == 'financials' for t in text)   # any label (industry or sector) says financials
    if used and used['type'] == 'conglomerate' and any(t['field'] == 'sector' and t['type'] == 'financials' for t in text):
        text_financials = True
        text_types = text_types + ['financials']
        reasons.append(f"conglomerate in a financial sector ('{company.get('sector')}'): treated as a hybrid with a balance-sheet "
                       '(bank/insurer) part; never one consolidated FCFF')
    pharma_text = 'pre_revenue_biotech' in text_types
    if pharma_text:
        pre_commercial = rev is None or rev < PRE_REVENUE_BIOTECH or (sv['rd_pct_revenue'] or 0) >= 1
        if not pre_commercial:
            text_types = ['healthcare_products' if t == 'pre_revenue_biotech' else t for t in text_types]
            reasons.append(f'biotech/pharma label but commercial revenue ({_money(rev, company.get("currency", ""))}, R&D '
                           f'{_pct(sv["rd_pct_revenue"])} of revenue): not pre-revenue -> healthcare_products (marketed drugs, '
                           'devices or tools: exclusivity, pipeline and reimbursement decide value)')
    bt = (((fb.get('business_map') or {}).get('business_terms') or {}).get('per_10k_words')) or {}
    if text_types and text_types[0] == 'it_services':
        # 'computer programming, data processing' covers IT consultancies, software vendors and ad platforms alike: the
        # annual report's platform vocabulary decides when it is read, else the economics (Alphabet and AppLovin are not
        # consultancies). A high gross margin is subscription software only when customers prepay (SUBSCRIPTION_DEFERRED; not
        # reported keeps the software reading): Meta, Pinterest and Reddit bill advertisers as impressions are delivered and
        # had been read as subscription software, with software drivers (paying seats, net revenue retention)
        words = next((k for k, cut, t in BUSINESS_TERM_RULES if t == 'platform_marketplace' and bt.get(k, 0) >= cut), None)
        high = gm is not None and gm >= .65
        econ = ('platform_marketplace' if words or (high and dr is not None and dr < SUBSCRIPTION_DEFERRED) else
                'subscription_software' if high else
                'platform_marketplace' if gm is not None and gm >= .45 and om_end is not None and om_end >= .20 else None)
        if econ:
            text_types = [econ, 'it_services']
            why = (f"annual report Item 1 with {bt[words]:.0f} {words.replace('_', ' ')} terms per 10,000 words" if words else
                   f'gross margin {_pct(gm)}, deferred revenue {_pct(dr)} of revenue and operating margin {_pct(om_end)}')
            reasons.append(f'IT/data-processing label, {why} -> {econ} (rule: platform vocabulary at its BUSINESS_TERM_RULES cut -> '
                           f'platform; else gross margin >= 65% software when customers prepay (deferred revenue >= '
                           f'{SUBSCRIPTION_DEFERRED:.0%} of revenue, or not reported) and platform when they do not; gross margin '
                           '>= 45% with operating margin >= 20% platform); it_services kept as secondary')
    sector_only = not used or used['field'] != 'industry'
    inter, plat_sig = intermediary_sic(company.get('sic')), False
    # a catch-all industry label under a platform's SEC code ('Diversified Commercial Services' for Airbnb) is as generic as a
    # sector: its business section decides first, as for a sector label (the collector reads the report for these codes)
    generic = sector_only or bool(inter and CATCH_ALL_LABEL.search(clean_text(used['text'])))
    item1 = None
    if generic and bt and (not text_types or text_types[0] not in NON_LABEL_TYPES):
        # generic labels ('Business Services, NEC', a sector): the annual report's own business section decides
        hit = item1 = next(((k, t) for k, cut, t in BUSINESS_TERM_RULES if bt.get(k, 0) >= cut), None)
        if hit and (not text_types or hit[1] != text_types[0]):
            k, t = hit
            reasons.append(f"annual report Item 1: {bt[k]:.0f} {k.replace('_', ' ')} terms per 10,000 words (rule: >= "
                           f"{dict((a, c) for a, c, _ in BUSINESS_TERM_RULES)[k]}) where the label is generic"
                           + (f" ('{used['text']}')" if used else '') + f' -> {t}'
                           + (f'; {text_types[0]} kept as secondary' if text_types else ''))
            text_types = [t] + text_types
    if (inter and generic and not item1 and not (used and used.get('alt'))
            and (not text_types or text_types[0] in LABEL_DEFAULT_TYPES)):
        # a platform's SEC code (INTERMEDIARY_SIC) with a type from a sector or catch-all label only: the platform signature decides
        plat, rule = platform_signature(gm, cx, om_end)
        lead = (f"SEC SIC {company['sic']} ({company.get('sic_description') or 'no description'}) covers {inter[2]}; "
                + (f"the {text_types[0]} type from '{used['text']}' is a label default" if text_types else 'no label type')
                + f': gross margin {_pct(gm)}, capex {_pct(cx)} of revenue, operating margin {_pct(om_end)}')
        if plat:
            reasons.append(f'{lead} -> platform_marketplace (platform signature: {rule})'
                           + (f'; {text_types[0]} kept as secondary' if text_types else ''))
            text_types, plat_sig = ['platform_marketplace'] + text_types, True
        else:
            reasons.append(f"{lead}: no platform signature ({rule}" + ('; undetermined' if plat is None else '') + ') -> '
                           + (f'{text_types[0]} stays on the label' if text_types else 'no type from the signature')
                           + "; the annual report's business section (Item 1) or a business_model override with a reason decides")
    terms = ((fb.get('business_map') or {}).get('industry_terms') or {}).get('per_100k_words') or {}
    if text_types and text_types[0] in ('industrial', 'hardware_manufacturing') and terms.get('semiconductor', 0) >= SEMI_TERMS_DENSITY:
        # a machinery or equipment label whose own annual report is about chips (ASML, Lam Research: SIC 3559 'Special Industry
        # Machinery'): wafer-fab equipment follows the semiconductor capital cycle and its R&D economics
        text_types = ['semiconductor'] + [t for t in text_types if t != 'semiconductor']
        reasons.append(f"annual report: {terms['semiconductor']:.0f} mentions of semiconductor/wafer/lithography per 100,000 words "
                       f"(rule: >= {SEMI_TERMS_DENSITY}) under the '{used['text'] if used else ''}' label -> semiconductor (equipment for chip "
                       f'makers); {text_types[1] if len(text_types) > 1 else "the label"} kept as secondary')
    sig_types = ['platform_marketplace'] if plat_sig else []
    if gm is not None and dr is not None and cx is not None and gm >= .65 and dr >= SUBSCRIPTION_DEFERRED and cx <= .08 and (inv or 0) <= .05:
        sig_types.append('subscription_software')
        reasons.append(f'financial signature {end}: gross margin {_pct(gm)}, deferred revenue {_pct(dr)} of revenue, '
                       f'capex {_pct(cx)} of revenue -> subscription economics (rule: gm >= 65%, deferred >= 15%, capex <= 8%)')
    if cx is not None and cx >= .25:
        sig_types.append('capital_intensive_infrastructure')
        reasons.append(f'financial signature {end}: capex {_pct(cx)} of revenue -> capital-intensive economics (rule: capex >= 25%)')
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
    rta, lending = sv['revenue_to_assets'], _evidence(fb, an, LENDING_KEYS)
    shape = (lta is not None and lta >= LENDER_LIABILITIES and rta is not None and rta <= LENDER_TURNOVER
             and (cx is None or cx < LENDER_CAPEX))
    fee_label = bool(used) and used['keyword'] in FEE_FINANCIAL
    operating_label = bool(text) and primary not in NON_OPERATING_TYPES
    lender_shaped = operating_label and shape and (bool(lending) or (not sv['has_cost_of_revenue'] and not fee_label))
    if lender_shaped:
        reasons.append(f"balance sheet looks like a lender's under the '{used['text']}' label: liabilities {_pct(lta)} of assets, revenue "
                       f"{_pct(rta)} of assets, capex {_pct(cx)} of revenue, "
                       + (f"lending data reported ({', '.join(sorted({e['key'] for e in lending}))})" if lending else 'no cost-of-revenue line')
                       + ' -> financials (economics before labels: deposits and funding are operating liabilities, so FCFF and an EV '
                       f"bridge misprice it); {primary or 'the label'} kept as a secondary type; a business_model override with a reason "
                       'routes it as an operating business when the liabilities are customer prepayments or client funds')
        if primary:
            secondary.append(primary)
        primary, evidence = 'financials', 'text+financials' if primary else 'financials'
    elif operating_label and lender_sig:
        reasons.append(f"liabilities {_pct(lta)} of assets with no cost-of-revenue line, but "
                       + (f"revenue {_pct(rta)} of assets and capex {_pct(cx)} of revenue: an operating asset base, not a loan book"
                          if not shape else f"'{used['text']}' is a fee business whose balance sheet carries client funds, settlement or "
                          'clearing balances, and no lending data is reported')
                       + f"; kept as {primary or 'labelled'}; the forecast's lender check asks a reviewer to confirm the operating classification")
    secondary += text_types[1:]
    if primary not in NON_OPERATING_TYPES:
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
        fid = ov.get('fiduciary_balance_sheet')
        if fid is not None and not isinstance(fid, bool):
            raise ValueError('business_model.fiduciary_balance_sheet must be true or false')
        clear = text_financials and ov['primary'] != 'financials'
        if fid and lender_sig:
            lender_sig = False
            reasons.append(f"analyst states the liabilities ({_pct(sv['liabilities_to_assets'])} of assets) are fiduciary client funds "
                           f"(pass-through, not lending or underwriting funding): lender guard cleared ({ov['reason']})")
        if clear and lender_sig:
            clear = False
            reasons.append(f"override kept the bank/insurer flag: liabilities are {_pct(sv['liabilities_to_assets'])} of assets with "
                           'no cost of revenue (a lender/insurer balance sheet); correct the FactBook, supply segment data, or state '
                           'fiduciary_balance_sheet: true with the reason when the liabilities are client funds')
        elif clear:
            text_financials = False
            reasons.append("analyst override clears the bank/insurer text flag (the balance sheet is not lender-like or is fiduciary: "
                           f"liabilities {_pct(sv['liabilities_to_assets'])} of assets); the computed flag is kept in 'computed'")
        shaped_ov = lender_shaped and ov['primary'] != 'financials'
        if shaped_ov:
            reasons.append(f"analyst override routes the lender-shaped balance sheet as {ov['primary']} ({ov['reason']}): the liabilities are "
                           "taken as operating (customer prepayments or client funds), not funding of a loan book; the computed financials type "
                           "is kept in 'computed'")
        sec = ov.get('secondary')
        if sec is None:
            sec = [t for t in [primary] + secondary
                   if t not in (ov['primary'], UNCLASSIFIED) and not ((clear or shaped_ov) and t == 'financials')]
        elif not isinstance(sec, list) or any(t not in TYPES for t in sec):
            raise ValueError('business_model.secondary must list known types')
        override = {'primary': ov['primary'], 'secondary': list(sec), 'reason': ov['reason'],
                    'evidence_ids': list(ov.get('evidence_ids') or []), 'fiduciary_balance_sheet': bool(fid)}
        reasons.append(f"analyst override: {ov['primary']} ({ov['reason']}); computed {primary} kept in 'computed'")
        primary, secondary = ov['primary'], [t for t in dict.fromkeys(sec) if t != ov['primary']]
        evidence = 'override' if evidence == 'none' else evidence + '+override'
    return {'primary': primary, 'secondary': secondary, 'reasons': reasons, 'override': override, 'computed': computed,
            'evidence': evidence, 'text_matches': text, 'signature': {k: v for k, v in sig.items() if isinstance(v, dict)},
            'flags': {'text_financials': text_financials, 'possible_financial': possible_financial, 'pharma_text': pharma_text,
                      'lender_balance_sheet': lender_sig, 'financial_label': fin_text, 'lender_shaped': lender_shaped}}


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
    arche = {'commodity': 'commodity', 'consumer_brand': 'consumer', 'industrial': 'industrial', 'distribution': 'industrial',
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
            value = _profitable_stage(g, rev, om_years[-2][1] if len(om_years) > 1 else None, usd, reasons, gtxt, om, data)
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
        value = _profitable_stage(g, rev, om_years[-2][1] if len(om_years) > 1 else None, usd, reasons, gtxt, om, data)
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


def _profitable_stage(g, rev, om_prev, usd, reasons, gtxt, om, data=None):
    if g is None:
        reasons.append('growth unknown (one fiscal year): treated as mature, provisional')
        return 'mature'
    if g >= .30:
        reasons.append(f'{gtxt} with positive operating margin: growth')
        return 'growth'
    if g >= .15:
        if usd and rev >= VERY_LARGE and om is not None and om >= .15:
            reasons.append(f'{gtxt}, but very large ({rev / 1e9:,.0f}bn) and highly profitable: growth on this base usually fades; maturing')
            if data is not None:   # a size prior, not this company's evidence: the forecast drops it when growth is evidenced
                data['size_relabel'] = {'prior': 'VERY_LARGE', 'threshold': VERY_LARGE, 'from': 'growth', 'to': 'maturing'}
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

def _margin_position(ms):
    """'peak' | 'trough' | 'mid' for the latest of >= 3 annual operating margins, with the rule that decided it. Peak (a profit
    peak, so only at a positive margin): latest more than 5 points (or 50%) above the window mean, or the latest is the window
    high after a climb of >= 5 points (a plateau at the top of an upswing). A narrowing loss is never a peak."""
    avg, last = mean(ms), ms[-1]
    if last > 0 and (last > avg + .05 or (avg > 0 and last > 1.5 * avg)):
        return 'peak', f'latest margin {_pct(last)} vs average {_pct(avg)}'
    if last > 0 and len(ms) >= 4 and last >= max(ms) - .005 and last - min(ms) >= .05 and last > avg:
        return 'peak', f'latest margin {_pct(last)} is the window high after a climb from {_pct(min(ms))} (average {_pct(avg)})'
    if last < avg - .05:
        return 'trough', f'latest margin {_pct(last)} vs average {_pct(avg)}'
    return 'mid', f'latest margin {_pct(last)} vs average {_pct(avg)}'


def _cyclical(fb, rows, primary, inputs):
    """Industry words/type are a flag. The valuation is normalized to mid-cycle margins ('normalize') when the reported
    history shows a cycle, when a flagged industry's latest margin sits at a possible peak (position 'peak': peak margins
    are not extrapolated), or when an analyst confirms a cycle; an analyst can reject one (inputs.cyclical, reason
    mandatory). A flag at mid or trough margins stays a flag (e.g. a booking platform with stable margins), and so does a
    flag on a history that is not cycle-shaped ('history': a scale-up from losses, a loss-making grower, pre-commercial
    noise) or whose window includes a loss year on normally growing revenue (start-up losses or a one-off charge, not a cycle
    trough): the open question is kept (peak_question) until an analyst settles it."""
    reasons = []
    company = fb.get('company') or {}
    t = text_type(company)
    if t and t.get('signature') == 'platform' and primary == t['alt'] and cyclical_word({'industry': t['text']}):
        word = cyclical_word({'sector': company.get('sector')}) if t['field'] == 'industry' else None
        reasons.append(f"'{t['text']}' is a mixed label resolved to {primary} by its financial signature: the operators' cyclical word "
                       f"('{cyclical_word({'industry': t['text']})}') does not flag the platform")
    else:
        word = cyclical_word(company)
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
    # boom then bust: a fall of 10% or more right after a rise of 15% or more (a refiner from 2022 to 2023, HF Sinclair's
    # history starting in 2020), before any recovery is visible
    boom_bust = any(revs[i] < .9 * revs[i - 1] and revs[i - 1] > 1.15 * revs[i - 2] for i in range(2, len(revs)))
    # a margin reversal is a cycle signal only when the low-margin year also shows a demand dip: revenue falling, or growth
    # more than 5 points below both neighbouring years and under half the company's median growth. Write-downs and fines on
    # steadily growing revenue are one-offs, not a
    # cycle (Microsoft 2012/2015 impairments, Meta 2012 IPO stock pay and 2019 FTC fine had read as a cycle and cut revenue
    # 25% to a 'mid-cycle trend'); a maturing grower's slower years are not dips either. Each low year counts once.
    gr = {ends[i]: revs[i] / revs[i - 1] - 1 for i in range(1, len(revs)) if revs[i - 1]}
    g_med = sorted(gr.values())[len(gr) // 2] if gr else None

    def weak(e):
        g = gr.get(e)
        if g is None:
            return False
        k = ends.index(e)
        nb = [gr[x] for x in (ends[k - 1] if k else None, ends[k + 1] if k + 1 < len(ends) else None) if x in gr]
        return g < 0 or (bool(nb) and g < min(nb) - .05 and g_med is not None and g < .5 * g_med)
    lows, one_offs = set(), set()
    for i in range(1, len(ms) - 1):
        if abs(ms[i] - ms[i - 1]) >= .03 and abs(ms[i + 1] - ms[i]) >= .03 and (ms[i] - ms[i - 1]) * (ms[i + 1] - ms[i]) < 0:
            low = pairs[i][0] if ms[i] < ms[i - 1] else pairs[i + 1][0]
            (lows if weak(low) else one_offs).add(low)
    reversals = len(lows)
    # pre-commercial noise (losses larger than revenue), a loss-maker's investment timing, a scale-up from losses: histories whose
    # margin swings are not a cycle (history_shape)
    history = history_shape(revs, oms)
    pre_commercial, loss_grower, scale_up = (history == x for x in ('pre_commercial', 'loss_grower', 'scale_up'))
    data_cyc = (((drop_recovered or boom_bust) and (vol or 0) >= .03) or reversals >= 2) and not pre_commercial and not loss_grower and not scale_up
    if data_cyc:
        reasons.append(f'reported history swings: operating-margin standard deviation {_pct(vol)}, {reversals} margin reversal(s) of '
                       f'>= 3 points' + (', revenue fell >= 10% and recovered' if drop_recovered else '')
                       + (', revenue fell >= 10% right after rising >= 15% (boom then bust)' if boom_bust else ''))
    elif ms and (pre_commercial or loss_grower or scale_up) and (((drop_recovered or boom_bust) and (vol or 0) >= .03) or reversals >= 2):
        reasons.append('margin swings ignored as a cycle signal: ' + ('losses larger than revenue (pre-commercial noise)' if pre_commercial
                       else 'revenue rose every year while loss-making (investment timing, not a cycle)' if loss_grower
                       else 'revenue rose every year and margins moved from losses to the window high (a scale-up path, not a '
                            'cycle; an analyst can still confirm a cycle with inputs.cyclical)'))
    elif ms:
        reasons.append(f'reported history shows no cycle pattern (margin standard deviation {_pct(vol)}, {reversals} demand-driven '
                       'reversal(s)' + (f"; margin dips on normal revenue growth in {', '.join(sorted(x[:4] for x in one_offs))} read as one-off "
                                       'charges, not a cycle' if one_offs else '') + ')')
    # a cycle position is read off a cycle-shaped window only: the histories data_cyc exempts (pre-commercial noise, a loss-maker's
    # investment timing, a scale-up from losses) are not cycles, so an industry word cannot call their latest margin a 'peak' and
    # normalize it to the window mean (an Uber-like 'travel' history, -22% .. +11% on revenue rising every year, had been valued
    # on its -2% start-up mean: 0.08x price). Nor, without a cycle in the data, can a window with a loss year on normally growing
    # revenue (weak() false: start-up or investment losses, or a one-off charge): that is not a cycle trough, so a mean that
    # includes it is not a mid-cycle margin. A loss year on a demand dip is the usual trough and keeps the peak reading (Micron at
    # the 2016-07 walk-forward formation: 2009 and 2012 memory troughs at -35% and -7.5% under an 18.5% peak), and so does a loss
    # year whose revenue change is unknown (the window's first year): the exemption needs evidence. The word stays a visible
    # analyst question (peak_question); inputs.cyclical settles it either way.
    raw, raw_rule = _margin_position(ms) if len(ms) >= 3 else (None, None)
    startup = [(e, m) for e, m in pairs if m < 0 and e in gr and not weak(e)]
    position, pos_rule = (raw, raw_rule) if history is None and (raw != 'peak' or data_cyc or not startup) else (None, None)
    flag_peak = flag and position == 'peak'
    question = None
    if flag and not data_cyc and raw == 'peak' and position is None:
        question = (f'{raw_rule}: a possible peak in a cyclical industry, but '
                    + {'scale_up': 'the history is a scale-up path (revenue rose every year, margins from losses to the window high)',
                       'pre_commercial': 'the window includes pre-commercial years (losses larger than revenue)'}.get(
                        history, 'the window includes a loss year on normally growing revenue ('
                        + ', '.join(f'{e[:4]}: margin {_pct(m)}, revenue {gr[e]:+.1%}' for e, m in startup)
                        + '), start-up or investment losses or a one-off charge rather than a cycle trough')
                    + ', so its mean is not a mid-cycle margin: not read as a cycle peak and not normalized; cycle or structural is '
                      'an analyst question (inputs.cyclical with a reason and evidence)')
    value, normalize = flag or data_cyc, data_cyc or flag_peak
    basis = 'data' if data_cyc else ('industry_flag+peak' if flag_peak else ('industry_flag' if flag else None))
    override = None
    ov = inputs.get('cyclical')
    if ov is not None:
        if not isinstance(ov, dict) or not isinstance(ov.get('value'), bool):
            raise ValueError('cyclical override needs a boolean value and a reason')
        txt(ov.get('reason'), 'cyclical.reason')
        override = {'value': ov['value'], 'reason': ov['reason'], 'evidence_ids': list(ov.get('evidence_ids') or [])}
        value = normalize = ov['value']
        basis = 'analyst'
        if ov['value'] and not pre_commercial:
            position, pos_rule = raw, raw_rule      # the analyst's cycle: the window is read as one
        question = None                             # settled by the analyst
        reasons.append(f"analyst {'confirms' if ov['value'] else 'rejects'} a cycle ({ov['reason']})")
    elif question:
        reasons.append('industry flag at a possible peak, not a cycle peak: ' + question)
    elif flag_peak and not data_cyc:
        reasons.append(f'cyclical industry at a possible peak ({pos_rule}): normalized to mid-cycle margins rather than extrapolating the '
                       'latest margin; an analyst can reject the cycle with a reason (inputs.cyclical)')
    elif flag and not data_cyc:
        reasons.append('industry flag only: margins are not at a visible peak, so the valuation is not normalized to a historical mean '
                       'unless the reported history shows a cycle or an analyst confirms one (inputs.cyclical with a reason)')
    out = {'value': value, 'normalize': normalize, 'basis': basis, 'industry_flag': flag, 'data_shows_cycle': data_cyc,
           'industry_word': word or None,
           'computed': flag or data_cyc, 'override': override, 'reasons': reasons, 'history': history, 'peak_question': question,
           'margin_volatility': _num(vol, 'population standard deviation of annual operating margins', ends[-1] if ends else None)}
    if (out['computed'] or value) and position:
        out['normalized_operating_margin'] = _num(mean(ms), f'mean annual operating margin over {len(ms)} years ({pairs[0][0]}..{pairs[-1][0]})',
                                                  pairs[-1][0])
        out['position'] = position
        if position != 'mid':
            reasons.append(f'{pos_rule}: possible cycle {position}; do not extrapolate it')
    return out


def cyclical_evidence(card, treatment=None):
    """Is the card's cyclicality EVIDENCED, so that a rate may charge for it (the forecast's KE_FACTORS 'cyclical' row of the cost
    of equity, the decision's PREMIUM 'cyclicality' component)? {'evidenced', 'basis', 'why', 'question'}.
    Evidence (first match): an analyst decision -- the forecast's inputs.cyclical treatment 'cyclical', the card's inputs.cyclical
    (value true confirms, false rejects: settled either way), an analyst method override to normalized_cycle_fcff; the reported
    history shows a cycle (_cyclical's data test: data_shows_cycle); the business type is cyclical by economics (CYCLICAL_TYPES:
    semiconductor, commodity). An industry word alone (CYCLICAL_WORDS: travel, machinery, construction, hotel, ...), also with
    the latest margin at a 'peak' of a history that shows no cycle, says nothing measured about this company's own cash-flow
    risk: it charges no rate and stays a visible question (inputs.cyclical with a reason and evidence decides). The callers charge
    only where they already flagged cyclicality (forecast route 'cyclical', card value true): evidence gates a charge, it never
    adds one. A card with no recorded basis at all (written by hand, or before 25.10.2026) keeps its stated value, labelled 'card'."""
    card = card or {}
    cyc = card.get('cyclical') or {}
    ov = cyc.get('override')
    val = card.get('valuation') or {}
    primary = (card.get('business_model') or {}).get('primary')
    flagged = cyc.get('value') is True

    def out(evidenced, basis, why, question=None):
        return {'evidenced': bool(evidenced), 'basis': basis, 'why': why, 'question': question}
    if treatment == 'cyclical':
        return out(True, 'analyst', "analyst confirms a cycle (forecast inputs.cyclical treatment 'cyclical')")
    if isinstance(ov, dict) and isinstance(ov.get('value'), bool):
        return out(ov['value'], 'analyst', f"analyst {'confirms' if ov['value'] else 'rejects'} a cycle ({ov.get('reason')})")
    if cyc.get('basis') == 'analyst':
        return out(flagged, 'analyst', 'analyst decision on the model card')
    if val.get('method') == 'normalized_cycle_fcff' and val.get('default_for_type') is False:
        return out(True, 'analyst', f"analyst method override to normalized_cycle_fcff ({val.get('override_reason')})")
    if cyc.get('data_shows_cycle') or cyc.get('basis') == 'data':
        return out(True, 'data', 'the reported history shows a cycle (margin swings with a demand dip, or a revenue fall and recovery)')
    if primary in CYCLICAL_TYPES:
        return out(True, 'type', f'business type {primary} is cyclical by economics (CYCLICAL_TYPES)')
    if not {'basis', 'industry_flag', 'data_shows_cycle'} & set(cyc):
        return out(flagged, 'card' if flagged else None, 'cyclical per the model card (no basis recorded)' if flagged else 'not cyclical (model card)')
    if flagged or cyc.get('industry_flag'):
        word = cyc.get('industry_word')
        q = ((f"industry word {word!r}" if word else 'the industry label') + ' flags a possible cycle'
             + (' and the latest margin sits at a possible peak (valued around the cycle until an analyst decides)'
                if cyc.get('basis') == 'industry_flag+peak' else '')
             + ', but the reported history shows no cycle' + (f" (a {cyc['history'].replace('_', '-')} history)" if cyc.get('history') else '')
             + ': cyclical or not is an analyst question (inputs.cyclical with a reason and evidence); no cyclicality charge in the cost '
               'of equity or the required return until then' + (f". {cyc['peak_question']}" if cyc.get('peak_question') else ''))
        return out(False, 'industry_word', 'industry word only: a flag, not evidence of a cycle', q)
    return out(False, None, 'not cyclical (model card)')


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

def _valuation(fb, an, qa, bm, stage, cyc, fund, cap, rows, inputs, cur):
    primary, flags = bm['primary'], bm['flags']
    sd = stage['data']
    om = (sd.get('operating_margin') or {}).get('value')
    rev = (sd.get('revenue') or {}).get('value')
    ends = [e for e in rows if _row_val(rows[e], 'revenue') is not None]
    has = lambda keys: bool(_evidence(fb, an, keys))
    balance_sheet = flags['text_financials'] or primary == 'financials'
    shaped = flags.get('lender_shaped') and primary == 'financials'
    cross, lim, req = [], [], []
    conf, funding_flag = 'normal', None

    def pick(method, arche, why, intended=None):
        return {'method': method, 'archetype': arche, 'why': why, 'intended_method': intended}

    if primary == 'financials':
        lead = 'lender-shaped balance sheet under a non-financial label (business_model reasons): ' if shaped else ''
        if _instant_fact(fb, 'equity') is None and not any(_row_val(rows[e], 'equity') for e in rows):
            v = pick('not_reliably_valuable', 'financials', lead + 'bank/insurer: residual income needs book equity, which is missing',
                     'residual_income')
        else:
            v = pick('residual_income', 'financials', lead + 'balance-sheet business: deposits and float are operating funding, not debt, so '
                     'FCFF and an EV bridge mis-state value; equity is valued from book value and ROE versus cost of equity')
            cross += ['price_to_tangible_book_vs_roe', 'dividend_and_excess_capital']
            lim += ['regulatory capital must be reconciled to book equity', 'valuation.residual_income holds the share count constant']
            req += ['ROE path by scenario with credit-loss cycle', 'required capital ratio and risk-weighted assets']
        if shaped:
            lim.append('the industry label is not financial: the balance sheet decided; a business_model override with a reason routes it as '
                       'an operating business when the liabilities are customer prepayments or client funds')
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
        # a profitable, cash-generating business that needs outside money (capex above operating cash flow, or a refinancing) is a
        # going concern with a financing question, not a survival case: positive operating margin and CFO in the latest two years
        cash_record = len(ends) >= 2 and om is not None and om > 0 and all(
            (_div(_row_val(rows[e], 'operating_income'), _row_val(rows[e], 'revenue')) or 0) > 0 and (_row_val(rows[e], 'cfo') or 0) > 0
            for e in ends[-2:])
        funded_ops = fund['value'] == 'needs_external' and cash_record
        if not rev or (cur == 'USD' and rev < PRE_REVENUE):
            v = pick('not_reliably_valuable', arche, 'no commercial revenue base: scenarios would be pure assumption; track paid adoption '
                     'and milestones until revenue exists', 'scenario_survival_fcff')
        elif len(ends) < 2 or om is None or not any(_row_val(rows[e], 'cfo') is not None for e in ends):
            v = pick('not_reliably_valuable', arche, 'an operating model needs at least two fiscal years of revenue, operating income and '
                     'operating cash flow; they are missing', 'fcff_annual_path')
        elif (fund['value'] == 'needs_external' and not funded_ops) or stage['value'] == 'early' or (
                om < 0 and (not sd.get('established_profit_record') or (stage['value'] == 'growth' and sd.get('consecutive_loss_years', 0) >= 2))
                and not (cyc['normalize'] and ever_profitable)):
            v = pick('scenario_survival_fcff', arche, 'loss-making or funding-dependent: value depends on reaching target economics and on '
                     'surviving the funding path; operating route with explicit financing, dilution and a failure case. Lack of profit is '
                     'not a rejection: promising-but-funding-conditional is a legitimate outcome')
            conf = 'lower'
            req += ['funding modelled in every scenario (source, price, dilution)', 'bear case includes failure / distressed financing',
                    'paid adoption and milestone evidence for the base case']
            cross += ['reverse_dcf (valuation_tools.reverse_price)', 'what_must_be_true (forecast.what_must_be_true)']
            lim += ['terminal value usually dominates; small changes in target margin move value a lot']
        elif cyc['normalize']:
            v = pick('normalized_cycle_fcff', arche, 'cyclical (' + {'analyst': 'analyst-confirmed', 'industry_flag+peak': 'cyclical industry '
                     'with the latest margin at a possible peak'}.get(cyc['basis'], 'reported history shows a cycle')
                     + '): value on a mid-cycle margin, not the latest year; peak margins are not permanent')
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
        if funded_ops and v['method'] in ('fcff_annual_path', 'normalized_cycle_fcff'):
            fcf = (fund['data'].get('fcf') or {}).get('value')
            capex_gap = fcf is not None and fcf < 0
            why = (f"operating margin and operating cash flow positive in {' and '.join(ends[-2:])}, but "
                   + (f"capex exceeds operating cash flow (FCF {_money(fcf, cur)}, capital intensity {cap['value']})" if capex_gap else
                      'near-term debt and dividends exceed liquidity plus FCF (refinancing)')
                   + ': outside funding is a financing and dilution question for a going concern, not a survival case')
            funding_flag = {'basis': 'capex_exceeds_cfo' if capex_gap else 'refinancing', 'capital_intensity': cap['value'],
                            'funding': fund['value'], 'why': why}
            lim.append(why + '; the annual path must carry that financing in every scenario')
            req.append(('financing of the capex plan' if capex_gap else 'refinancing of near-term maturities')
                       + ' in every scenario (source, cost, dilution to current holders)')
            if capex_gap:
                cross.append('return on new capex versus cost of capital')
        if cyc['value'] and not cyc['normalize'] and v['method'] != 'not_reliably_valuable':
            nm = (cyc.get('normalized_operating_margin') or {}).get('value')
            lim.append('cyclical industry, but the reported history shows no cycle: not normalized to a historical mean'
                       + (f"; latest margin {_pct(om)} vs {_pct(nm)} average looks like a possible peak: confirm structural vs "
                          'cyclical (inputs.cyclical) before extrapolating it' if cyc.get('position') == 'peak' else '')
                       + (f"; {cyc['peak_question']}" if cyc.get('peak_question') else ''))
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
    if funding_flag:
        out['funding_flag'] = funding_flag
    ov = inputs.get('method_override')
    if ov is not None:
        if not isinstance(ov, dict):
            raise ValueError('method_override must be an object with method and reason')
        txt(ov.get('reason'), 'method_override.reason')
        if ov.get('method') not in METHODS:
            raise ValueError('method_override.method must be one of ' + ', '.join(METHODS))
        if ov.get('archetype') is not None and ov['archetype'] not in ROUTES:
            raise ValueError('method_override.archetype must be a valuation.ROUTES key')
        lender = flags.get('lender_balance_sheet') and flags.get('financial_label') and out['route'] != 'operating'
        if ov['method'] in OPERATING_METHODS and (balance_sheet or lender):
            if shaped:
                why_rej = ("the balance sheet is a lender's (liabilities >= 85% of mostly financial assets) under a non-financial label: an "
                           'FCFF route treats deposits/funding as free funding. Set business_model (primary and reason) when the '
                           'liabilities are customer prepayments or client funds')
            else:
                why_rej = (('sector text or business model identifies a bank/insurer' if balance_sheet else
                            'a financial label with a lender-like balance sheet (liabilities >= 85% of assets, no cost of revenue)')
                           + ': an FCFF route treats deposits/float as free funding. Correct the company sector/industry in the FactBook '
                           'if the label is wrong, or set business_model.fiduciary_balance_sheet with a reason when the liabilities are '
                           'client funds')
            out['override_rejected'] = {'method': ov['method'], 'reason': ov['reason'],
                                        'why_rejected': why_rej + '; the card does not relabel to force a generic model'}
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
    bm = _business_model(fb, an, rows, last, sig, inputs)
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
    val = _valuation(fb, an, qa, bm, stage, cyc, fund, cap, rows, inputs, cur)
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
