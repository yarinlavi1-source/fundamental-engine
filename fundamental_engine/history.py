"""Validated reported history shared by plain verdicts, company typing and scorecards.

Rows are fiscal years, oldest first, in absolute currency units. Only revenue and
source_ids are required; every other field is optional and a check that needs a
missing field reports itself as unavailable rather than guessing.
"""
from datetime import date
from .finance import number

SIGNED = ('gross_profit', 'operating_income', 'net_income', 'operating_cash_flow', 'pretax_income',
          'income_tax', 'retained_earnings', 'equity', 'acquired_revenue')
NONNEGATIVE = ('capex', 'shares_diluted', 'total_assets', 'current_assets', 'current_liabilities',
               'total_liabilities', 'receivables', 'sga', 'depreciation', 'ppe', 'long_term_debt',
               'total_debt', 'cash', 'dividends_paid', 'buybacks', 'interest_expense', 'goodwill')


def txt(v, k):
    if not isinstance(v, str) or not v.strip():
        raise ValueError(k + ': nonempty text required')
    return v


def opt(row, key, low=None):
    return None if row.get(key) is None else number(row[key], key, low)


def history(case):
    rows = case['history']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 15:
        raise ValueError('history: supply 1..15 fiscal years, oldest first')
    ends = []
    for r in rows:
        txt(r['fiscal_year'], 'fiscal_year')
        end = date.fromisoformat(r['period_end'])
        if end > date.fromisoformat(case['as_of']):
            raise ValueError('History period ends after as_of')
        number(r['revenue'], 'revenue', 0)
        for k in SIGNED:
            opt(r, k)
        for k in NONNEGATIVE:
            opt(r, k, 0)
        if r.get('acquired_revenue') is not None and not 0 <= r['acquired_revenue'] <= r['revenue']:
            raise ValueError('acquired_revenue must be between 0 and revenue')
        if not isinstance(r.get('source_ids'), list) or not r['source_ids']:
            raise ValueError('Every history row needs source_ids (where the number came from)')
        ends.append(end)
    if ends != sorted(set(ends)):
        raise ValueError('history must be ordered oldest first with distinct period_end')
    return rows


def cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def span_years(rows):
    return (date.fromisoformat(rows[-1]['period_end']) - date.fromisoformat(rows[0]['period_end'])).days / 365.25


def margins(rows, key):
    return [r[key] / r['revenue'] if r.get(key) is not None and r['revenue'] > 0 else None for r in rows]


def organic_growth(rows):
    """Last-year growth excluding revenue that acquisitions added this year, if supplied."""
    if len(rows) < 2 or rows[-2]['revenue'] <= 0:
        return None
    acquired = rows[-1].get('acquired_revenue') or 0
    return (rows[-1]['revenue'] - acquired) / rows[-2]['revenue'] - 1


def acquisition_years(rows):
    """Years whose growth was likely bought: supplied acquired_revenue, or goodwill
    rising by more than 10% of prior-year total assets (a large purchase)."""
    out = []
    for i in range(1, len(rows)):
        cur, prev = rows[i], rows[i - 1]
        if (cur.get('acquired_revenue') or 0) > .05 * prev['revenue']:
            out.append(cur['fiscal_year'])
        elif cur.get('goodwill') is not None and prev.get('goodwill') is not None and prev.get('total_assets'):
            if cur['goodwill'] - prev['goodwill'] > .1 * prev['total_assets']:
                out.append(cur['fiscal_year'])
    return out


def organic_cagr(rows):
    """Annualized growth over years without a detected large acquisition."""
    bought = set(acquisition_years(rows))
    steps = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev['revenue'] <= 0:
            continue
        if cur['fiscal_year'] in bought:
            if cur.get('acquired_revenue') is not None:
                steps.append((cur['revenue'] - cur['acquired_revenue']) / prev['revenue'])
            continue
        steps.append(cur['revenue'] / prev['revenue'])
    if not steps:
        return None
    product = 1
    for x in steps:
        product *= x
    return product ** (1 / len(steps)) - 1 if product > 0 else None


def quarters(case):
    """Optional recent standalone quarters (oldest first) to catch inflections that
    annual history hides. Revenue required; operating_income/gross_profit optional."""
    rows = case.get('recent_quarters')
    if rows is None:
        return []
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError('recent_quarters: supply 1..12 quarters, oldest first')
    ends = []
    for r in rows:
        end = date.fromisoformat(r['period_end'])
        if end > date.fromisoformat(case['as_of']):
            raise ValueError('Quarter ends after as_of')
        number(r['revenue'], 'quarter revenue', 0)
        for k in ('operating_income', 'gross_profit', 'net_income'):
            opt(r, k)
        if not isinstance(r.get('source_ids'), list) or not r['source_ids']:
            raise ValueError('Every quarter needs source_ids')
        ends.append(end)
    if ends != sorted(set(ends)):
        raise ValueError('recent_quarters must be ordered oldest first with distinct period_end')
    return rows


def quarter_momentum(qs):
    if len(qs) < 5 or qs[-5]['revenue'] <= 0:
        return None
    last = qs[-1]
    out = {'latest_quarter_end': last['period_end'], 'latest_quarter_yoy': last['revenue'] / qs[-5]['revenue'] - 1,
           'latest_quarter_operating_margin': last['operating_income'] / last['revenue']
           if last.get('operating_income') is not None and last['revenue'] > 0 else None}
    if len(qs) >= 8:
        now, before = sum(q['revenue'] for q in qs[-4:]), sum(q['revenue'] for q in qs[-8:-4])
        out['ttm_growth'] = now / before - 1 if before > 0 else None
    return out
