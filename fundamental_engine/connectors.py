"""Deterministic mappers from existing client connector payloads to history rows.

The client (Claude) retrieves statements with its own connectors; this module only
maps field names. It fetches nothing. Provider data remains a secondary source that
should be reconciled to filings for material conclusions.

Alpha Vantage rows carry a fiscal period end but no filing date. A historical as_of must
not see a period that was not yet filed, so each row gets an ESTIMATED availability
(period end + annual_lag_days / quarterly_lag_days, defaults 75 / 45 = 10-K / 10-Q
accelerated-filer deadlines) and rows not available by as_of are excluded. The estimate
is labelled; replace it with the actual filing date when known.
"""
from datetime import date, timedelta

AV_MAP = {
    'income': {'revenue': 'totalRevenue', 'gross_profit': 'grossProfit', 'operating_income': 'operatingIncome',
               'net_income': 'netIncome', 'pretax_income': 'incomeBeforeTax', 'income_tax': 'incomeTaxExpense',
               'sga': 'sellingGeneralAndAdministrative', 'interest_expense': 'interestExpense'},
    'balance': {'total_assets': 'totalAssets', 'current_assets': 'totalCurrentAssets',
                'current_liabilities': 'totalCurrentLiabilities', 'total_liabilities': 'totalLiabilities',
                'retained_earnings': 'retainedEarnings', 'receivables': 'currentNetReceivables',
                'ppe': 'propertyPlantEquipment', 'long_term_debt': 'longTermDebt', 'total_debt': 'shortLongTermDebtTotal',
                'cash': 'cashAndCashEquivalentsAtCarryingValue',
                'cash_including_short_term_investments': 'cashAndShortTermInvestments',
                'short_term_investments': 'shortTermInvestments', 'long_term_investments': 'longTermInvestments', 'equity': 'totalShareholderEquity',
                'shares_outstanding': 'commonStockSharesOutstanding', 'goodwill': 'goodwill'},
    'cash': {'operating_cash_flow': 'operatingCashflow', 'capex': 'capitalExpenditures',
             'depreciation': 'depreciationDepletionAndAmortization', 'dividends_paid': 'dividendPayout'},
}
NONNEGATIVE = {'goodwill', 'capex', 'dividends_paid', 'total_debt', 'long_term_debt', 'cash', 'depreciation', 'sga', 'interest_expense'}


def _num(v):
    if v in (None, 'None', '', '-'):
        return None
    return float(v)


def _payload(p):
    if isinstance(p, list) and p and isinstance(p[0], dict) and 'text' in p[0]:
        import json
        p = json.loads(p[0]['text'])
    if not isinstance(p, dict) or not isinstance(p.get('annualReports'), list):
        raise ValueError('Expected an Alpha Vantage statement payload with annualReports')
    return p


def _available(period_end, lag):
    return (date.fromisoformat(period_end) + timedelta(days=lag)).isoformat()


def from_alpha_vantage(income, balance=None, cash_flow=None, years=5, as_of=None, annual_lag_days=75, quarterly_lag_days=45):
    """Return history rows (oldest first) from AV INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW."""
    if not 1 <= years <= 15:
        raise ValueError('years must be 1..15')
    for lag in (annual_lag_days, quarterly_lag_days):
        if isinstance(lag, bool) or not isinstance(lag, int) or not 0 <= lag <= 366:
            raise ValueError('filing lag days must be an integer 0..366')
    if as_of:
        as_of = date.fromisoformat(as_of[:10]).isoformat()
    parts = {'income': _payload(income)}
    if balance is not None: parts['balance'] = _payload(balance)
    if cash_flow is not None: parts['cash'] = _payload(cash_flow)
    symbol = parts['income'].get('symbol')
    by_date = {}
    currency = None
    for kind, payload in parts.items():
        if payload.get('symbol') != symbol:
            raise ValueError('Statements belong to different symbols')
        for rep in payload['annualReports']:
            d = rep['fiscalDateEnding']
            date.fromisoformat(d)
            if as_of and _available(d, annual_lag_days) > as_of:
                continue
            cur = rep.get('reportedCurrency')
            if currency and cur and cur != currency:
                raise ValueError('Mixed reporting currencies; convert explicitly before grading')
            currency = currency or cur
            row = by_date.setdefault(d, {'fiscal_year': 'FY' + d[:4], 'period_end': d,
                                         'available_at': _available(d, annual_lag_days), 'available_at_estimated': True,
                                         'source_ids': []})
            row['source_ids'].append(f'alphavantage:{symbol}:{kind}:{d}')
            for field, key in AV_MAP[kind].items():
                v = _num(rep.get(key))
                if v is not None and field in NONNEGATIVE:
                    v = abs(v)
                if v is not None:
                    row[field] = v
    dates = sorted(d for d, r in by_date.items() if r.get('revenue') is not None)[-years:]
    if not dates:
        raise ValueError('No annual revenue found')
    rows = [by_date[d] for d in dates]
    qrows = []
    for rep in sorted([r for r in parts['income'].get('quarterlyReports', []) if not as_of or _available(r['fiscalDateEnding'], quarterly_lag_days) <= as_of], key=lambda x: x['fiscalDateEnding'])[-8:]:
        d = rep['fiscalDateEnding']
        if _num(rep.get('totalRevenue')) is None:
            continue
        q = {'period_end': d, 'revenue': _num(rep['totalRevenue']), 'available_at': _available(d, quarterly_lag_days),
             'available_at_estimated': True, 'source_ids': [f'alphavantage:{symbol}:income_q:{d}']}
        for field, key in (('operating_income', 'operatingIncome'), ('gross_profit', 'grossProfit'), ('net_income', 'netIncome')):
            if _num(rep.get(key)) is not None:
                q[field] = _num(rep[key])
        qrows.append(q)
    return {'ticker': symbol, 'currency': currency, 'history': rows, 'recent_quarters': qrows,
            'notes': ['shares_outstanding is a point-in-time balance, never weighted-average diluted EPS shares',
                      'cash is cash/equivalents only; combined cash-and-short-term-investments remains a separate field. Do not add combined and component balances.',
                      'Provider-normalized fields; reconcile material figures to the 10-K/20-F',
                      f'available_at is ESTIMATED as period end + {annual_lag_days} days (annual) / {quarterly_lag_days} days (quarterly); rows not available by as_of were excluded'],
            'availability': {'annual_lag_days': annual_lag_days, 'quarterly_lag_days': quarterly_lag_days, 'estimated': True}}
