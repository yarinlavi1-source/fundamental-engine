"""Deterministic mappers from existing client connector payloads to history rows.

The client (Claude) retrieves statements with its own connectors; this module only
maps field names. It fetches nothing. Provider data remains a secondary source that
should be reconciled to filings for material conclusions.
"""
from datetime import date

AV_MAP = {
    'income': {'revenue': 'totalRevenue', 'gross_profit': 'grossProfit', 'operating_income': 'operatingIncome',
               'net_income': 'netIncome', 'pretax_income': 'incomeBeforeTax', 'income_tax': 'incomeTaxExpense',
               'sga': 'sellingGeneralAndAdministrative', 'interest_expense': 'interestExpense'},
    'balance': {'total_assets': 'totalAssets', 'current_assets': 'totalCurrentAssets',
                'current_liabilities': 'totalCurrentLiabilities', 'total_liabilities': 'totalLiabilities',
                'retained_earnings': 'retainedEarnings', 'receivables': 'currentNetReceivables',
                'ppe': 'propertyPlantEquipment', 'long_term_debt': 'longTermDebt', 'total_debt': 'shortLongTermDebtTotal',
                'cash': 'cashAndShortTermInvestments', 'equity': 'totalShareholderEquity',
                'shares_diluted': 'commonStockSharesOutstanding', 'goodwill': 'goodwill'},
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


def from_alpha_vantage(income, balance=None, cash_flow=None, years=5, as_of=None):
    """Return history rows (oldest first) from AV INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW."""
    if not 1 <= years <= 15:
        raise ValueError('years must be 1..15')
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
            if as_of and d > as_of:
                continue
            cur = rep.get('reportedCurrency')
            if currency and cur and cur != currency:
                raise ValueError('Mixed reporting currencies; convert explicitly before grading')
            currency = currency or cur
            row = by_date.setdefault(d, {'fiscal_year': 'FY' + d[:4], 'period_end': d,
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
    return {'ticker': symbol, 'currency': currency, 'history': rows,
            'notes': ['shares_diluted mapped from period-end common shares outstanding (AV); prefer diluted weighted shares from filings when material',
                      'cash = cash and short-term investments; confirm restricted cash in filings',
                      'Provider-normalized fields; reconcile material figures to the 10-K/20-F']}
