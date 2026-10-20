"""Universe-wide fundamentals from SEC XBRL frames (one call per concept and period, all filers).

Why: per-company vendor calls cannot cover a broad universe on a limited plan (the FMP plan in the
2026-10-20 session allowed only a sample of symbols and ~250 calls a day). The SEC frames API returns one
concept for every filer for one calendar period, so ~20 calls describe the whole US market. Frames carry
the accession number but not the filing date: a frame row is usable for a CURRENT screen; historical
backtests must use companyfacts (pit.py) with real filing dates instead.

URL pattern: https://data.sec.gov/api/xbrl/frames/us-gaap/<Tag>/<USD|shares>/<CY2025 | CY2025Q2 | CY2025Q2I>.json
"""
from .facts import DURATION_TAGS, INSTANT_TAGS

FRAME_METRICS = {   # metric -> (concept candidates in priority order, unit, frame kind)
    # the facts layer's revenue tags (Valero and other refiners include excise taxes; brokers report net of interest; utilities
    # tag regulated and unregulated revenue): with two tags, about 700 listed US companies above $200m never entered the screen
    'revenue': (['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax',
                 'SalesRevenueNet', 'RevenuesNetOfInterestExpense', 'RegulatedAndUnregulatedOperatingRevenue'], 'USD', 'duration'),
    'gross_profit': (['GrossProfit'], 'USD', 'duration'),
    'operating_income': (['OperatingIncomeLoss'], 'USD', 'duration'),
    'net_income': (['NetIncomeLoss', 'ProfitLoss'], 'USD', 'duration'),
    'cfo': (['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'], 'USD', 'duration'),
    'capex': (['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'], 'USD', 'duration'),
    'sbc': (['ShareBasedCompensation'], 'USD', 'duration'),
    'buybacks': (['PaymentsForRepurchaseOfCommonStock'], 'USD', 'duration'),
    'acquisitions': (['PaymentsToAcquireBusinessesNetOfCashAcquired'], 'USD', 'duration'),
    'cash': (['CashAndCashEquivalentsAtCarryingValue'], 'USD', 'instant'),
    'short_term_investments': (['ShortTermInvestments', 'MarketableSecuritiesCurrent'], 'USD', 'instant'),
    'long_term_debt_tagged': (['LongTermDebt'], 'USD', 'instant'),
    'long_term_debt': (['LongTermDebtNoncurrent'], 'USD', 'instant'),
    'current_portion_ltd': (['LongTermDebtCurrent', 'DebtCurrent'], 'USD', 'instant'),
    'equity': (['StockholdersEquity'], 'USD', 'instant'),
    'total_assets': (['Assets'], 'USD', 'instant'),
    'total_liabilities': (['Liabilities'], 'USD', 'instant'),
    'goodwill': (['Goodwill'], 'USD', 'instant'),
    'shares_outstanding': (['CommonStockSharesOutstanding'], 'shares', 'instant'),
}


# IFRS filers (20-F, 40-F) tag the ifrs-full taxonomy in their reporting currency: the same metrics from the IFRS elements.
# Their frames are fetched per currency; USD figures beside a reporting currency are convenience translations and are dropped
IFRS_FRAME_METRICS = {
    'revenue': ['Revenue', 'RevenueFromContractsWithCustomers'],
    'gross_profit': ['GrossProfit'],
    'operating_income': ['ProfitLossFromOperatingActivities'],
    'net_income': ['ProfitLoss'],
    'cfo': ['CashFlowsFromUsedInOperatingActivities'],
    'capex': ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
    'sbc': ['AdjustmentsForSharebasedPayments'],
    'cash': ['CashAndCashEquivalents'],
    'equity': ['EquityAttributableToOwnersOfParent'],
    'total_assets': ['Assets'],
    'total_liabilities': ['Liabilities'],
    'goodwill': ['Goodwill'],
    'long_term_debt': ['NoncurrentPortionOfNoncurrentBorrowings', 'LongtermBorrowings'],
    'current_portion_ltd': ['CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings', 'CurrentPortionOfLongtermBorrowings'],
}
IFRS_CURRENCIES = ('USD', 'EUR', 'GBP', 'JPY', 'TWD', 'DKK', 'CHF', 'SEK', 'NOK', 'CAD', 'AUD', 'NZD', 'KRW', 'INR', 'CNY',
                   'HKD', 'SGD', 'BRL', 'MXN', 'ZAR', 'THB')


def ifrs_frame_urls(year, currencies=IFRS_CURRENCIES, metrics=None):
    """IFRS frame URLs for one calendar year: revenue first for every currency (to learn which currencies have filers), then
    the other metrics for the currencies passed (collect_universe asks only for currencies that had revenue rows)."""
    out = []
    for metric, tags in IFRS_FRAME_METRICS.items():
        if metrics and metric not in metrics:
            continue
        kind = 'instant' if metric in ('cash', 'equity', 'total_assets', 'total_liabilities', 'goodwill', 'long_term_debt',
                                       'current_portion_ltd') else 'duration'
        period = f'CY{year}Q4I' if kind == 'instant' else f'CY{year}'
        for ccy in currencies:
            for tag in tags:
                out.append({'metric': metric, 'tag': tag, 'unit': ccy, 'period': period, 'taxonomy': 'ifrs-full',
                            'url': f'https://data.sec.gov/api/xbrl/frames/ifrs-full/{tag}/{ccy}/{period}.json'})
    return out


def merge_ifrs(payloads, rate):
    """IFRS frames -> {cik: row} in USD, like merge_frames. payloads: [{'metric','tag','unit','payload'}]; rate(ccy, date) ->
    units of ccy per USD or None. Per filer the reporting currency is the non-USD currency of its revenue rows (USD rows beside
    it are convenience translations), else USD. Every monetary value is converted at the rate of its own period end and the
    row records 'reporting_currency' and the rates used; a filer whose rate is unknown is left out (never converted at a
    guessed rate)."""
    by_ccy = {}
    for p in payloads:
        by_ccy.setdefault(p.get('unit') or 'USD', []).append(p)
    merged = {ccy: merge_frames([dict(p, metric=p['metric']) for p in ps], order=IFRS_FRAME_METRICS) for ccy, ps in by_ccy.items()}
    ciks = {c for m in merged.values() for c, r in m.items() if (r.get('revenue') or {}).get('value')}
    out, skipped = {}, []
    for cik in ciks:
        have = [ccy for ccy, m in merged.items() if (m.get(cik, {}).get('revenue') or {}).get('value')]
        ccy = next((c for c in have if c != 'USD'), 'USD')
        row = merged[ccy][cik]
        conv, rates = {'name': row.get('name'), 'reporting_currency': ccy, 'fx': {}}, {}
        ok = True
        for k, x in row.items():
            if not isinstance(x, dict) or 'value' not in x:
                continue
            r = 1. if ccy == 'USD' else rate(ccy, x.get('end'))
            if not r:
                ok = False
                break
            rates[x.get('end')] = r
            conv[k] = dict(x, value=x['value'] / r, value_reporting_currency=x['value'])
        if not ok:
            skipped.append((cik, ccy))
            continue
        conv['fx'] = rates
        out[cik] = conv
    return out, skipped


def frame_urls(year, quarter=None):
    """Frame URLs to fetch for one calendar period (annual durations, or a quarter with its instant)."""
    out = []
    for metric, (tags, unit, kind) in FRAME_METRICS.items():
        period = (f'CY{year}' if quarter is None else f'CY{year}Q{quarter}') + ('I' if kind == 'instant' else '')
        if kind == 'instant' and quarter is None:
            period = f'CY{year}Q4I'
        for tag in tags:
            out.append({'metric': metric, 'tag': tag, 'unit': unit, 'period': period,
                        'url': f'https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json'})
    return out


def merge_frames(payloads, order=None):
    """payloads: [{'metric', 'tag', 'payload': frames JSON}] -> {cik: {metric: {'value','end','accn','tag'}}}.

    Concept priority follows FRAME_METRICS order: a later tag never overwrites an earlier tag's value
    for the same company (no silent substitution of components for totals). Revenue is the exception that proves the
    rule: the larger value of the same period wins, because a total is never below a component (recorded in 'replaced')."""
    order = ({(m, t): i for m, tags in order.items() for i, t in enumerate(tags)} if order is not None else
             {(m, t): i for m, (tags, _, _) in FRAME_METRICS.items() for i, t in enumerate(tags)})
    out = {}
    for p in sorted(payloads, key=lambda x: order.get((x['metric'], x['tag']), 99)):
        for r in (p.get('payload') or {}).get('data', []):
            row = out.setdefault(int(r['cik']), {'name': r.get('entityName')})
            cur, val = row.get(p['metric']), float(r['val'])
            if cur is None:
                row[p['metric']] = {'value': val, 'end': r.get('end'), 'accn': r.get('accn'), 'tag': p['tag']}
            elif p['metric'] == 'revenue' and val > cur['value'] and r.get('end') == cur.get('end'):
                # total revenue is never below a component: an issuer that tags a segment or a single revenue stream as
                # 'Revenues' (Verra Mobility CY2025: $133m against ~$0.9bn) would otherwise enter the screen at a fraction
                row[p['metric']] = {'value': val, 'end': r.get('end'), 'accn': r.get('accn'), 'tag': p['tag'],
                                    'replaced': {'tag': cur['tag'], 'value': cur['value'], 'why': 'larger total revenue tag for the same period'}}
    return out


def rows(merged, tickers, sectors=None):
    """Universe rows for screen.py: tickers {cik: ticker}; sectors {ticker: {'sector','industry'}} optional.

    Every fetched frame reaches the row, including capital allocation (buybacks, acquisitions: the screen's organic-growth
    and capital-allocation inputs) and total liabilities (the lender-like balance-sheet test for mixed financial labels).

    Debt: noncurrent + current portion when the unambiguous noncurrent tag exists; otherwise the tagged
    LongTermDebt is reported with scope 'ambiguous' (a screen may use it, a valuation may not). Neither tag:
    debt None with scope 'missing', which means unknown (net debt, EV and ROIC stay unknown downstream),
    never zero debt or net cash."""
    out = []
    for cik, r in merged.items():
        t = tickers.get(cik)
        if not t:
            continue
        v = {k: (x['value'] if isinstance(x, dict) and 'value' in x else None) for k, x in r.items() if k not in ('name', 'fx', 'reporting_currency')}
        if v.get('long_term_debt') is not None:
            debt, scope = v['long_term_debt'] + (v.get('current_portion_ltd') or 0), 'noncurrent_plus_current'
        elif v.get('long_term_debt_tagged') is not None:
            debt, scope = v['long_term_debt_tagged'], 'ambiguous'
        else:
            debt, scope = None, 'missing'
        fcf = v['cfo'] - v['capex'] if v.get('cfo') is not None and v.get('capex') is not None else None
        out.append({'symbol': t, 'cik': cik, 'name': r.get('name'), **(sectors or {}).get(t, {}),
                    'revenue': v.get('revenue'), 'gross_profit': v.get('gross_profit'), 'operating_income': v.get('operating_income'),
                    'net_income': v.get('net_income'), 'cfo': v.get('cfo'), 'capex': v.get('capex'), 'fcf': fcf,
                    'sbc': v.get('sbc'), 'buybacks': v.get('buybacks'), 'acquisitions': v.get('acquisitions'),
                    'cash': v.get('cash'), 'short_term_investments': v.get('short_term_investments'),
                    'debt': debt, 'debt_scope': scope, 'equity': v.get('equity'), 'total_assets': v.get('total_assets'),
                    'total_liabilities': v.get('total_liabilities'),
                    'goodwill': v.get('goodwill'), 'shares_outstanding': v.get('shares_outstanding'),
                    'data_grade': 'sec_frames', 'source': 'SEC XBRL frames' + (f" (IFRS, {r['reporting_currency']} converted to USD at "
                                                                                   'the period-end H.10 rate)' if r.get('reporting_currency') else ''),
                    **({'reporting_currency': r['reporting_currency']} if r.get('reporting_currency') else {})})
    return sorted(out, key=lambda x: x['symbol'])


assert set(FRAME_METRICS) <= set(DURATION_TAGS) | set(INSTANT_TAGS)
