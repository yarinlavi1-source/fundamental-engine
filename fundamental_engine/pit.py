"""Point-in-time quarterly fundamentals from SEC companyfacts (stdlib only).

Rules (no look-ahead):
- For every period the FIRST filed value is kept (later restatements are ignored for PIT use).
- A fact becomes usable at `filed + lag_days` (default 2) — `available()` enforces it.
- Missing standalone quarters are derived from year-to-date facts of the same fiscal start
  (e.g. Q4 = FY - 9M YTD); the derived value is available only when BOTH inputs were filed.
- Instants (balance sheet, cover-page shares) keep the first-filed value per date.
"""
from datetime import date, timedelta

REV = ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax',
       'SalesRevenueNet', 'SalesRevenueGoodsNet', 'SalesRevenueServicesNet', 'RevenuesNetOfInterestExpense']
DURATION = {'rev': REV, 'gp': ['GrossProfit'], 'oi': ['OperatingIncomeLoss'], 'ni': ['NetIncomeLoss', 'ProfitLoss'],
            'ocf': ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
            'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'],
            'sbc': ['ShareBasedCompensation', 'AllocatedShareBasedCompensationExpense']}
INSTANT = {'cash': ['CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'],
           'sti': ['ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'],
           'ltd': ['LongTermDebt', 'LongTermDebtNoncurrent'], 'ltd_cur': ['LongTermDebtCurrent', 'DebtCurrent'],
           'conv': ['ConvertibleNotesPayable', 'ConvertibleDebtNoncurrent'],
           'goodwill': ['Goodwill'], 'assets': ['Assets'], 'equity': ['StockholdersEquity']}
FIN_MARKERS = ['Deposits', 'InterestAndDividendIncomeOperating', 'PremiumsEarnedNet', 'LoansAndLeasesReceivableNetReportedAmount']


# 16-week quarters (Costco Q4, Kroger Q1, ...) are real fiscal quarters: 80..120 days (bug fix 19.10.2026, was 100)
Q_MAX_DAYS = 120


def _d(s):
    return date.fromisoformat(s)


def _rows(facts, concept, unit='USD'):
    g = facts.get('us-gaap', {}).get(concept)
    if not g:
        return []
    return g.get('units', {}).get(unit, [])


def quarterly(facts, concepts, unit='USD'):
    """{period_end: (value, filed)} for standalone ~quarters; per period the earliest filed value across concepts."""
    out, cands = {}, []
    for order, c in enumerate(concepts):
        rows = _rows(facts, c, unit)
        if not rows:
            continue
        direct, ytd = {}, {}
        for r in rows:
            if not r.get('start') or r.get('form') not in ('10-Q', '10-K', '10-Q/A', '10-K/A', '10-KT', '20-F', '40-F'):
                continue
            s, e, f = r['start'], r['end'], r['filed']
            L = (_d(e) - _d(s)).days
            if 80 <= L <= Q_MAX_DAYS:
                if e not in direct or f < direct[e][1]:
                    direct[e] = (float(r['val']), f)
            ytd.setdefault(s, {})
            if e not in ytd[s] or f < ytd[s][e][1]:
                ytd[s][e] = (float(r['val']), f)
        derived = {}
        for s, ends in ytd.items():
            es = sorted(ends)
            for i, e in enumerate(es):
                if e in derived:
                    continue
                if (_d(e) - _d(s)).days < 150:
                    continue
                prev = [p for p in es[:i] if 80 <= (_d(e) - _d(p)).days <= Q_MAX_DAYS]
                if prev:
                    p = prev[-1]
                    derived[e] = (ends[e][0] - ends[p][0], max(ends[e][1], ends[p][1]))
        # direct and derived compete on filing date too (bug fix 19.10.2026: a direct quarter shown a YEAR
        # later in the next 10-K's quarterly note overrode the on-time FY - 9M derivation, e.g. MSFT Q4 FY15)
        for e, (v, f) in direct.items():
            cands.append((f, order, 0, e, v))
        for e, (v, f) in derived.items():
            cands.append((f, order, 1, e, v))
    # Across concepts the EARLIEST filed value per period wins (ties: concept order). Bug fix 19.10.2026:
    # "first concept wins" let a comparative filed a year later (e.g. ASC 606 tag in the next 10-K) replace
    # the value filed on time under the old tag, hiding ~1/3 of companies in 2018 from the panel.
    for f, order, _, e, v in sorted(cands):
        if not any(abs((_d(e) - _d(x)).days) <= 15 for x in out):
            out[e] = (v, f)
    return dict(sorted(out.items()))


def instant(facts, concepts, taxonomy='us-gaap', unit='USD', sum_classes=False):
    for c in concepts:
        g = facts.get(taxonomy, {}).get(c)
        if not g:
            continue
        rows = g.get('units', {}).get(unit, [])
        out = {}
        if sum_classes:
            # multi-class issuers report one cover-page line per class in the same filing (same accn, same date)
            per = {}
            for r in rows:
                per.setdefault((r['end'], r.get('accn'), r['filed']), set()).add(float(r['val']))
            for (e, _, f), vals in per.items():
                if e not in out or f < out[e][1]:
                    out[e] = (sum(vals), f)
        for r in rows if not sum_classes else []:
            e, f = r['end'], r['filed']
            if e not in out or f < out[e][1]:
                out[e] = (float(r['val']), f)
        if out:
            return dict(sorted(out.items()))
    return {}


def share_durations(facts, concepts=('WeightedAverageNumberOfSharesOutstandingBasic',)):
    """{period_end: (weighted average shares, filed)} for standalone quarters only. Never derived from YTD
    (an average cannot be differenced)."""
    for c in concepts:
        rows = facts.get('us-gaap', {}).get(c, {}).get('units', {}).get('shares', [])
        out = {}
        for r in rows:
            if not r.get('start') or r.get('form') not in ('10-Q', '10-K', '10-Q/A', '10-K/A', '10-KT'):
                continue
            if 80 <= (_d(r['end']) - _d(r['start'])).days <= Q_MAX_DAYS:
                e, f = r['end'], r['filed']
                if e not in out or f < out[e][1]:
                    out[e] = (float(r['val']), f)
        if out:
            return dict(sorted(out.items()))
    return {}


def shares_at(rec, t, max_age_days=200, lag_days=2):
    """(as_of_date, shares, source) usable at t. Chain: dei cover page (summed across classes) ->
    us-gaap CommonStockSharesOutstanding (balance sheet) -> weighted-average basic of the latest quarter.
    Multi-class issuers often tag cover shares only with a class dimension, which companyfacts omits
    (bug found 19.10.2026: CRWD, DDOG, V, ZM, PLTR had no usable share count)."""
    for key, src in (('shares', 'dei_cover'), ('shares_bs', 'balance_sheet'), ('shares_wavg', 'weighted_basic')):
        av = available(rec.get(key) or {}, t, lag_days)
        if av:
            d, v = max(av)
            if (t - _d(d)).days <= max_age_days and v > 0:
                return d, v, src
    return None, None, None


def company(facts):
    """Compact PIT record for one company."""
    f = facts.get('facts', {})
    rec = {k: quarterly(f, v) for k, v in DURATION.items()}
    rec.update({k: instant(f, v) for k, v in INSTANT.items()})
    rec['shares'] = instant(f, ['EntityCommonStockSharesOutstanding'], taxonomy='dei', unit='shares', sum_classes=True)
    rec['shares_bs'] = instant(f, ['CommonStockSharesOutstanding'], unit='shares')
    rec['shares_wavg'] = share_durations(f)
    rec['financial'] = any(m in f.get('us-gaap', {}) for m in FIN_MARKERS)
    rec['name'] = facts.get('entityName')
    return rec


def available(series, t, lag_days=2):
    """Items of a {period: (value, filed)} series usable at date t (filed + lag <= t)."""
    cut = t - timedelta(days=lag_days)
    return [(p, v) for p, (v, f) in series.items() if _d(f) <= cut]


def ttm_at(series, t, lag_days=2, offset=0):
    """TTM sum of the four latest standalone quarters usable at t (offset quarters back)."""
    q = available(series, t, lag_days)
    q = sorted(q)
    if len(q) < 4 + offset:
        return None, None
    window = q[len(q) - 4 - offset: len(q) - offset]
    span = (_d(window[-1][0]) - _d(window[0][0])).days
    if not 250 <= span <= 300:
        return None, None
    return sum(v for _, v in window), window[-1][0]


def latest_at(series, t, lag_days=2, max_age_days=200):
    q = available(series, t, lag_days)
    if not q:
        return None
    p, v = max(q)
    return v if (t - _d(p)).days <= max_age_days else None
