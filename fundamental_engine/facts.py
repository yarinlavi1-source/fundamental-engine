"""FactBook: one schema for every number the pipeline uses, with provenance and availability.

Why: the engine had four financial-history schemas and five names for share counts
(docs/ARCHITECTURE.md). Every adapter here maps a connector payload into the same fact rows:
metric, period, value, unit, source, basis (as_reported / normalized / derived), raw concept
and the date the number became public. Accessors never return a fact that was not available
at the FactBook's as_of date, so downstream modules cannot look ahead by accident.

Adapters take payloads exactly as the connectors return them; they do not fetch anything.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json

LAG_DAYS = 2                       # a filing, or a dated bundle document, is usable two days after it is published (snapshot.PIT_LAG_DAYS)
# filing deadlines used when the filing date is unknown (flagged): 10-K 90 days (non-accelerated filer), 10-Q 45. A quarter
# or balance sheet ending at a fiscal-year end is first filed with the annual report: annual lag. Foreign filers
# (company['filer'] == 'foreign' or company['annual_form'] == '20-F'): 20-F 120 days; their interims have no 10-Q deadline
# (the exchanges require the half-year report on a 6-K within six months): 180 days.
EST_LAG = {'annual': 90, 'quarter': 45}
FOREIGN_ANNUAL_LAG = 120
FOREIGN_INTERIM_LAG = 180
FOREIGN_FILERS = {'foreign', 'foreign_private_issuer', 'fpi', '20-f', '40-f'}
# cash payments and proceeds: sources tag them positive or with the cash-flow sign; every sum uses their magnitudes
PAYMENT_METRICS = {'capex', 'buybacks', 'dividends_paid', 'acquisitions', 'debt_issued', 'debt_repaid'}

# Canonical metric -> raw XBRL concept names (lower case; SEC camel case is lower-cased before lookup).
# Order = priority. Only exact concepts are used; no component tags stand in for totals.
DURATION_TAGS = {
    'revenue': ['revenues', 'revenuefromcontractwithcustomerexcludingassessedtax',
                'revenuefromcontractwithcustomerincludingassessedtax', 'salesrevenuenet', 'revenuesnetofinterestexpense'],
    'cost_of_revenue': ['costofrevenue', 'costofgoodsandservicessold', 'costofgoodssold'],
    'gross_profit': ['grossprofit'],
    'rd': ['researchanddevelopmentexpense', 'researchanddevelopmentexpensesoftwareexcludingacquiredinprocesscost',
           'researchanddevelopmentexpenseexcludingacquiredinprocesscost'],
    'sm': ['sellingandmarketingexpense'],
    'ga': ['generalandadministrativeexpense'],
    'sga': ['sellinggeneralandadministrativeexpense'],
    'operating_income': ['operatingincomeloss'],
    'interest_expense': ['interestexpense', 'interestexpensenonoperating', 'interestexpensedebt'],
    'pretax_income': ['incomelossfromcontinuingoperationsbeforeincometaxesextraordinaryitemsnoncontrollinginterest',
                      'incomelossfromcontinuingoperationsbeforeincometaxesminorityinterestandincomelossfromequitymethodinvestments'],
    'income_tax': ['incometaxexpensebenefit'],
    'net_income': ['netincomeloss', 'profitloss'],
    'eps_diluted': ['earningspersharediluted'],
    'shares_basic_wavg': ['weightedaveragenumberofsharesoutstandingbasic'],
    'shares_diluted_wavg': ['weightedaveragenumberofdilutedsharesoutstanding'],
    'd_and_a': ['depreciationdepletionandamortization', 'depreciationamortizationandaccretionnet', 'depreciationandamortization'],
    'sbc': ['sharebasedcompensation', 'allocatedsharebasedcompensationexpense'],
    'cfo': ['netcashprovidedbyusedinoperatingactivities', 'netcashprovidedbyusedinoperatingactivitiescontinuingoperations'],
    'capex': ['paymentstoacquirepropertyplantandequipment', 'paymentstoacquireproductiveassets'],
    'acquisitions': ['paymentstoacquirebusinessesnetofcashacquired'],
    'buybacks': ['paymentsforrepurchaseofcommonstock'],
    'dividends_paid': ['paymentsofdividendscommonstock', 'paymentsofdividends'],
    'debt_issued': ['proceedsfromissuanceoflongtermdebt', 'proceedsfromissuanceofdebt'],
    'debt_repaid': ['repaymentsoflongtermdebt', 'repaymentsofdebt'],
    'deferred_revenue_change': ['increasedecreaseindeferredrevenue', 'increasedecreaseincontractwithcustomerliability'],
    'change_in_cash': ['cashcashequivalentsrestrictedcashandrestrictedcashequivalentsperiodincreasedecreaseincludingexchangerateeffect',
                       'cashandcashequivalentsperiodincreasedecrease'],
    'cfi': ['netcashprovidedbyusedininvestingactivities'],
    'cff': ['netcashprovidedbyusedinfinancingactivities'],
    'fx_effect': ['effectofexchangerateoncashcashequivalentsrestrictedcashandrestrictedcashequivalents'],
}
INSTANT_TAGS = {
    'cash': ['cashandcashequivalentsatcarryingvalue'],
    'cash_incl_restricted': ['cashcashequivalentsrestrictedcashandrestrictedcashequivalents'],
    'short_term_investments': ['shortterminvestments', 'marketablesecuritiescurrent', 'availableforsalesecuritiesdebtsecuritiescurrent'],
    'long_term_investments': ['longterminvestments', 'marketablesecuritiesnoncurrent', 'availableforsalesecuritiesdebtsecuritiesnoncurrent'],
    'restricted_cash': ['restrictedcashcurrent', 'restrictedcash'],
    'receivables': ['accountsreceivablenetcurrent'],
    'inventory': ['inventorynet'],
    'current_assets': ['assetscurrent'],
    'ppe': ['propertyplantandequipmentnet'],
    'goodwill': ['goodwill'],
    'intangibles': ['intangibleassetsnetexcludinggoodwill', 'otherintangibleassetsnet', 'finitelivedintangibleassetsnet'],
    'total_assets': ['assets'],
    'payables': ['accountspayablecurrent'],
    'deferred_revenue_current': ['contractwithcustomerliabilitycurrent', 'deferredrevenuecurrent'],
    'deferred_revenue_noncurrent': ['contractwithcustomerliabilitynoncurrent', 'deferredrevenuenoncurrent'],
    'current_liabilities': ['liabilitiescurrent'],
    'current_portion_ltd': ['longtermdebtcurrent', 'debtcurrent'],
    'short_term_borrowings': ['shorttermborrowings', 'commercialpaper'],
    'long_term_debt': ['longtermdebtnoncurrent'],
    # scope ambiguous: issuers tag the noncurrent face line or the total as LongTermDebt; dataqa resolves it
    # with the debt roll-forward (prior + issued - repaid) before any use
    'long_term_debt_tagged': ['longtermdebt', 'longtermdebtandcapitalleaseobligations'],
    'operating_lease_current': ['operatingleaseliabilitycurrent'],
    'operating_lease_noncurrent': ['operatingleaseliabilitynoncurrent'],
    'total_liabilities': ['liabilities'],
    'equity': ['stockholdersequity', 'stockholdersequityincludingportionattributabletononcontrollinginterest'],
    'retained_earnings': ['retainedearningsaccumulateddeficit'],
    'convertible_debt': ['convertiblenotespayable', 'convertibledebtnoncurrent', 'convertibledebt', 'convertiblenotespayablecurrent'],
    'debt_fair_value': ['longtermdebtfairvalue', 'debtinstrumentfairvalue'],
    'warrants_outstanding': ['classofwarrantorrightoutstanding'],
    'liabilities_and_equity': ['liabilitiesandstockholdersequity'],
    'shares_outstanding': ['commonstocksharesoutstanding'],
    'treasury_shares': ['treasurystockcommonshares'],
}
UNITS = {'eps_diluted': 'USD/share', 'shares_basic_wavg': 'shares', 'shares_diluted_wavg': 'shares',
         'shares_outstanding': 'shares', 'treasury_shares': 'shares', 'warrants_outstanding': 'shares'}
# issuer_document: the issuer's own release or filing read outside XBRL (dataset keeps the bundle kind, retrieval how it
# was read); primary_document: a primary non-issuer document (regulator filing, official statistics), graded like an
# issuer document; secondary: press, analysis and anything else - never graded above single_source. 'document' is the
# legacy name of issuer_document and is graded the same way.
KINDS = {'filing_derived', 'vendor_normalized', 'market_feed', 'consensus', 'issuer_document', 'primary_document', 'secondary',
         'document', 'synthetic'}
REPORTED_KINDS = {'filing_derived', 'issuer_document', 'primary_document', 'document'}
RELEASE_DATASETS = {'issuer_release', 'earnings_release'}   # issuer documents that a later filing may revise
BASIS_RANK = {'as_reported': 0, 'derived': 1, 'normalized': 2}
SAME_PERIOD_DAYS = 7


def _d(s):
    return date.fromisoformat(str(s)[:10])


def digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def new(company, as_of):
    """Empty FactBook for a company at an information cutoff."""
    for k in ('ticker', 'currency'):
        if not company.get(k):
            raise ValueError(f'company.{k} required')
    _d(as_of)
    return {'factbook_version': 1, 'company': deepcopy(company), 'as_of': as_of, 'sources': {}, 'facts': [],
            'market': {}, 'estimates': [], 'qualitative': []}


def add_source(fb, sid, provider, dataset, kind, retrieved_at, sha=None, url=None, note=None):
    if kind not in KINDS:
        raise ValueError(f'source kind must be one of {sorted(KINDS)}')
    if sid in fb['sources'] and fb['sources'][sid]['dataset'] != dataset:
        raise ValueError(f'duplicate source id {sid}')
    fb['sources'][sid] = {'id': sid, 'provider': provider, 'dataset': dataset, 'kind': kind,
                          'retrieved_at': retrieved_at, 'sha256': sha, 'url': url, 'note': note}
    return sid


def add_fact(fb, metric, period, period_end, value, source_id, basis, available_at, estimated=False,
             unit=None, period_start=None, concept=None, derived_from=None):
    if source_id not in fb['sources']:
        raise ValueError(f'unknown source {source_id}')
    if basis not in BASIS_RANK:
        raise ValueError('basis must be as_reported, normalized or derived')
    v = float(value)
    if v != v or v in (float('inf'), float('-inf')):
        raise ValueError(f'{metric}: non-finite value')
    row = {'metric': metric, 'period': period, 'period_end': period_end, 'value': v,
           'unit': unit or UNITS.get(metric, 'USD'), 'source_id': source_id, 'basis': basis,
           'available_at': available_at, 'available_at_estimated': bool(estimated)}
    if period_start:
        row['period_start'] = period_start
    if concept:
        row['concept'] = concept
    if derived_from:
        row['derived_from'] = derived_from
    fb['facts'].append(row)
    return row


def _lag(fb, kind):
    c = (fb or {}).get('company') or {}
    if str(c.get('filer') or c.get('annual_form') or '').lower() in FOREIGN_FILERS:
        return FOREIGN_ANNUAL_LAG if kind == 'annual' else FOREIGN_INTERIM_LAG
    return EST_LAG[kind]


def _est_available(period_end, kind, fb=None):
    return (_d(period_end) + timedelta(days=_lag(fb, kind))).isoformat()


def _plus(d, days):
    return (_d(d) + timedelta(days=days)).isoformat()


def _lag_kind(end, fy_ends):
    """'annual' for a quarter or balance sheet ending at a fiscal-year end (first public with the annual report: there is
    no 10-Q for a fourth quarter), else 'quarter'."""
    return 'annual' if any(abs((_d(end) - _d(e)).days) <= SAME_PERIOD_DAYS for e in fy_ends) else 'quarter'


def _annual_ends(fb):
    return {f['period_end'] for f in fb['facts'] if _is_annual(f)}


def fiscal_year(end):
    """Fiscal year named by the calendar year it ends in (MSFT June, ORCL May, NVDA late January: FMP's convention);
    a 52/53-week year ending in the first week of January belongs to the year before."""
    d = _d(end)
    return d.year - 1 if d.month == 1 and d.day <= 7 else d.year


def _fy_label(fy, kind, fp=None, end=None):
    if fy in (None, ''):
        return f'FY{fiscal_year(end)}' if kind == 'annual' else f'Q@{end}'
    return f'FY{fy}' if kind == 'annual' else f'{fy}{fp}'


def _map_rows(fb, rows, tags, source_id, kind, filed_by_end=None, statement=None):
    """FMP as-reported rows: [{'fiscalYear','period','date','data':{concept: value}}]. Without a filing date a Q4 or
    fiscal-year-end quarter row is estimated at the annual deadline (it is first filed in the 10-K)."""
    added = 0
    fy_ends = _annual_ends(fb) | {r['date'][:10] for r in rows if r.get('period') in ('FY', 'Q4')}
    for r in rows:
        end = r['date'][:10]
        data = {k.lower(): v for k, v in (r.get('data') or {}).items()}
        filed = (filed_by_end or {}).get(end)
        lag = 'annual' if kind == 'annual' else _lag_kind(end, fy_ends)
        avail = (_d(filed) + timedelta(days=LAG_DAYS)).isoformat() if filed else _est_available(end, lag, fb)
        label = _fy_label(r.get('fiscalYear'), kind, r.get('period'), end)
        if kind == 'quarter' and r.get('period') == 'FY':
            continue
        for metric, cands in tags.items():
            for c in cands:
                if c in data and isinstance(data[c], (int, float)):
                    add_fact(fb, metric, label if tags is DURATION_TAGS else 'I@' + end, end, data[c], source_id,
                             'as_reported', avail, estimated=not filed, concept=c)
                    added += 1
                    break
    return added


def from_fmp_as_reported(fb, payloads, retrieved_at, kind='annual', filed_by_end=None, prefix='fmp'):
    """payloads: {'income': [...], 'balance': [...], 'cashflow': [...]} as returned by FMP as-reported endpoints.

    The as-reported endpoints reproduce the filing's XBRL concepts; they are filing-derived but read
    through a vendor (retrieval provider_normalized), and carry no filing date: availability is then
    estimated from filing deadlines and flagged."""
    n = 0
    for part, tags in (('income', DURATION_TAGS), ('cashflow', DURATION_TAGS), ('balance', INSTANT_TAGS)):
        rows = payloads.get(part)
        if not rows:
            continue
        sid = add_source(fb, f'{prefix}:as_reported_{part}_{kind}', 'fmp', f'as-reported-{part}-{kind}', 'filing_derived',
                         retrieved_at, sha=digest(rows))
        n += _map_rows(fb, rows, tags, sid, kind, filed_by_end, part)
    return n


def from_sec_companyfacts(fb, payload, retrieved_at, sid='sec:companyfacts'):
    """SEC companyfacts JSON: annual (10-K, ~1 year) and standalone quarterly facts with real filing dates.

    Every candidate tag is read and merged per period end: the first-filed value wins (later restatements are ignored
    for point-in-time use), tag order only breaks ties - an issuer that moved from Revenues to the ASC 606 tag keeps
    both eras. Quarters are derived from year-to-date facts only when both inputs were filed (pit.quarterly)."""
    from .pit import quarterly
    add_source(fb, sid, 'sec', 'companyfacts', 'filing_derived', retrieved_at, sha=digest(payload),
               url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(payload.get('cik', 0)):010d}.json")
    gaap = payload.get('facts', {}).get('us-gaap', {})
    lower = {k.lower(): k for k in gaap}
    n = 0
    for metric, cands in DURATION_TAGS.items():
        unit = 'USD/shares' if metric == 'eps_diluted' else ('shares' if metric.startswith('shares_') else 'USD')
        tags = [lower[c] for c in cands if c in lower]
        annual = {}                                         # end -> ((filed, tag order), row, tag)
        for order, tag in enumerate(tags):
            for r in gaap[tag].get('units', {}).get(unit, []):
                if not r.get('start') or r.get('form') not in ('10-K', '10-K/A', '20-F', '40-F'):
                    continue
                if not 350 <= (_d(r['end']) - _d(r['start'])).days <= 380:
                    continue
                key = (r['filed'], order)
                same = next((e for e in annual if abs((_d(e) - _d(r['end'])).days) <= SAME_PERIOD_DAYS), None)
                if same is None or key < annual[same][0]:
                    annual.pop(same, None)
                    annual[r['end']] = (key, r, tag)
        for end, (_, r, tag) in sorted(annual.items()):
            add_fact(fb, metric, f'FY{fiscal_year(end)}', end, r['val'], sid, 'as_reported',
                     (_d(r['filed']) + timedelta(days=LAG_DAYS)).isoformat(), period_start=r['start'], concept=tag,
                     unit=UNITS.get(metric))
            n += 1
        if unit != 'USD':
            continue
        qs = sorted((filed, order, end, v, tag) for order, tag in enumerate(tags)
                    for end, (v, filed) in quarterly({'us-gaap': {tag: gaap[tag]}}, [tag]).items())
        seen = []
        for filed, _, end, v, tag in qs:                     # earliest filed per quarter across tags (pit.quarterly rule)
            if any(abs((_d(end) - _d(x)).days) <= 15 for x in seen):
                continue
            seen.append(end)
            q4 = any(abs((_d(end) - _d(a)).days) <= SAME_PERIOD_DAYS for a in annual)   # Q4 = FY - 9M YTD
            add_fact(fb, metric, f'Q@{end}', end, v, sid, 'derived' if q4 else 'as_reported',
                     (_d(filed) + timedelta(days=LAG_DAYS)).isoformat(), concept=tag)
            n += 1
    for metric, cands in INSTANT_TAGS.items():
        unit = 'shares' if metric in UNITS else 'USD'
        first = {}
        for order, tag in enumerate(lower[c] for c in cands if c in lower):
            for r in gaap[tag].get('units', {}).get(unit, []):
                key = (r['filed'], order)
                if r['end'] not in first or key < first[r['end']][0]:
                    first[r['end']] = (key, r, tag)
        for end, (_, r, tag) in sorted(first.items()):
            add_fact(fb, metric, 'I@' + end, end, r['val'], sid, 'as_reported',
                     (_d(r['filed']) + timedelta(days=LAG_DAYS)).isoformat(), concept=tag, unit=UNITS.get(metric))
            n += 1
    cover = payload.get('facts', {}).get('dei', {}).get('EntityCommonStockSharesOutstanding', {}).get('units', {}).get('shares', [])
    per = {}
    for r in cover:                      # multi-class issuers: one row per class in the same filing
        per.setdefault((r['end'], r.get('accn'), r['filed']), set()).add(float(r['val']))
    best = {}
    for (end, _, filed), vals in per.items():
        if end not in best or filed < best[end][1]:
            best[end] = (sum(vals), filed)
    for end, (v, filed) in best.items():
        add_fact(fb, 'shares_outstanding_cover', 'I@' + end, end, v, sid, 'as_reported',
                 (_d(filed) + timedelta(days=LAG_DAYS)).isoformat(), unit='shares', concept='dei:entitycommonstocksharesoutstanding')
        n += 1
    return n


AV_MAP = {  # Alpha Vantage normalized statement fields
    'totalRevenue': 'revenue', 'costOfRevenue': 'cost_of_revenue', 'grossProfit': 'gross_profit',
    'researchAndDevelopment': 'rd', 'sellingGeneralAndAdministrative': 'sga', 'operatingIncome': 'operating_income',
    'interestExpense': 'interest_expense', 'incomeBeforeTax': 'pretax_income', 'incomeTaxExpense': 'income_tax',
    'netIncome': 'net_income', 'depreciationAndAmortization': 'd_and_a',
    'operatingCashflow': 'cfo', 'capitalExpenditures': 'capex', 'paymentsForRepurchaseOfCommonStock': 'buybacks',
    'dividendPayout': 'dividends_paid',
    'cashAndCashEquivalentsAtCarryingValue': 'cash', 'shortTermInvestments': 'short_term_investments',
    'longTermInvestments': 'long_term_investments', 'currentNetReceivables': 'receivables', 'inventory': 'inventory',
    'totalCurrentAssets': 'current_assets', 'propertyPlantEquipment': 'ppe', 'goodwill': 'goodwill',
    'intangibleAssetsExcludingGoodwill': 'intangibles', 'totalAssets': 'total_assets', 'currentAccountsPayable': 'payables',
    'deferredRevenue': 'deferred_revenue_current', 'totalCurrentLiabilities': 'current_liabilities',
    'currentLongTermDebt': 'current_portion_ltd', 'longTermDebtNoncurrent': 'long_term_debt',
    'shortLongTermDebtTotal': 'debt_total_vendor', 'totalLiabilities': 'total_liabilities',
    'totalShareholderEquity': 'equity', 'commonStockSharesOutstanding': 'shares_outstanding', 'retainedEarnings': 'retained_earnings',
}
AV_INSTANT = {v for k, v in AV_MAP.items() if k in {
    'cashAndCashEquivalentsAtCarryingValue', 'shortTermInvestments', 'longTermInvestments', 'currentNetReceivables', 'inventory',
    'totalCurrentAssets', 'propertyPlantEquipment', 'goodwill', 'intangibleAssetsExcludingGoodwill', 'totalAssets',
    'currentAccountsPayable', 'deferredRevenue', 'totalCurrentLiabilities', 'currentLongTermDebt', 'longTermDebtNoncurrent',
    'shortLongTermDebtTotal',
    'totalLiabilities', 'totalShareholderEquity', 'commonStockSharesOutstanding', 'retainedEarnings'}}


def from_alpha_vantage(fb, payloads, retrieved_at, prefix='av'):
    """payloads: {'income':..., 'balance':..., 'cashflow':...} AV statement JSON (annualReports/quarterlyReports).

    AV returns current (possibly restated) values without filing dates: availability is estimated
    and flagged, so historical as_of use is limited (connectors.py note). A quarter or balance sheet ending at a
    fiscal-year end (an annualReports date) is estimated at the annual deadline, like the annual row itself."""
    n = 0
    fy_ends = _annual_ends(fb) | {r['fiscalDateEnding'] for p in payloads.values() if isinstance(p, dict)
                                  for r in p.get('annualReports', []) if r.get('fiscalDateEnding')}
    for part in ('income', 'balance', 'cashflow'):
        p = payloads.get(part)
        if not p:
            continue
        sid = add_source(fb, f'{prefix}:{part}', 'alpha_vantage', part, 'vendor_normalized', retrieved_at, sha=digest(p))
        for kind, key in (('annual', 'annualReports'), ('quarter', 'quarterlyReports')):
            for r in p.get(key, []):
                end = r['fiscalDateEnding']
                lag = 'annual' if kind == 'annual' else _lag_kind(end, fy_ends)
                for field, metric in AV_MAP.items():
                    raw = r.get(field)
                    if raw in (None, 'None', ''):
                        continue
                    try:
                        v = float(raw)
                    except ValueError:
                        continue
                    if metric in PAYMENT_METRICS:
                        v = abs(v)
                    period = 'I@' + end if metric in AV_INSTANT else (f'AVFY@{end}' if kind == 'annual' else f'Q@{end}')
                    add_fact(fb, metric, period, end, v, sid, 'normalized', _est_available(end, lag, fb), estimated=True,
                             unit=UNITS.get(metric), concept='av:' + field)
                    n += 1
    return n


def set_price(fb, value, as_of, source_id, kind='last', currency=None):
    if source_id not in fb['sources']:
        raise ValueError('unknown price source')
    if _d(as_of) > _d(fb['as_of']):
        raise ValueError('price dated after the FactBook as_of (look-ahead)')
    fb['market']['price'] = {'value': float(value), 'currency': currency or fb['company']['currency'], 'as_of': as_of,
                             'source_id': source_id, 'kind': kind}


def from_fmp_estimates(fb, rows, retrieved_at, as_of=None, sid='fmp:financial_estimates', dated_snapshot=False):
    """FMP analyst financial-estimates (annual): consensus is a vendor estimate, never a reported fact.

    The endpoint returns today's consensus, so it is dated no earlier than its retrieval: an earlier as_of is honoured
    only for a dated vendor snapshot (dated_snapshot=True); otherwise it is raised to the retrieval date and the asked
    date kept in 'as_of_requested' (a consensus fetched today is never treated as known in the past)."""
    add_source(fb, sid, 'fmp', 'financial-estimates-annual', 'consensus', retrieved_at, sha=digest(rows))
    got = str(retrieved_at)[:10]
    when, asked = str(as_of or got)[:10], None
    if as_of and not dated_snapshot and _d(as_of) < _d(got):
        when, asked = got, str(as_of)[:10]
    for r in rows:
        for metric, k, lo, hi, nk in (('revenue', 'revenueAvg', 'revenueLow', 'revenueHigh', 'numAnalystsRevenue'),
                                      ('eps', 'epsAvg', 'epsLow', 'epsHigh', 'numAnalystsEps'),
                                      ('ebitda', 'ebitdaAvg', 'ebitdaLow', 'ebitdaHigh', 'numAnalystsRevenue'),
                                      ('net_income', 'netIncomeAvg', 'netIncomeLow', 'netIncomeHigh', 'numAnalystsEps')):
            if r.get(k) is None:
                continue
            fb['estimates'].append({'metric': metric, 'period_end': r['date'][:10], 'fiscal_year': int(r['date'][:4]),
                                    'value': float(r[k]), 'low': r.get(lo), 'high': r.get(hi), 'n_analysts': r.get(nk),
                                    'source_id': sid, 'as_of': when, **({'as_of_requested': asked} if asked else {})})


def add_qualitative(fb, qid, topic, statement, kind, source_ids, as_of):
    if kind not in {'reported', 'guidance', 'opinion', 'assumption'}:
        raise ValueError('qualitative kind must be reported, guidance, opinion or assumption')
    if any(s not in fb['sources'] for s in source_ids):
        raise ValueError(f'{qid}: unknown source')
    if _d(as_of) > _d(fb['as_of']):
        raise ValueError(f'{qid}: dated after as_of')
    fb['qualitative'].append({'id': qid, 'topic': topic, 'statement': statement, 'kind': kind,
                              'source_ids': list(source_ids), 'as_of': as_of})


BUNDLE_METRICS = {  # evidence-bundle metric names -> FactBook metrics (duration unless listed in BUNDLE_INSTANT)
    'revenue': 'revenue', 'operating_income_gaap': 'operating_income', 'operating_income': 'operating_income',
    'net_income_gaap': 'net_income', 'net_income': 'net_income', 'cfo': 'cfo', 'capex': 'capex', 'buybacks': 'buybacks',
    'sbc': 'sbc', 'rpo': 'rpo', 'arr': 'arr', 'shares_outstanding': 'shares_outstanding_cover',
    'acquisitions': 'acquisitions',             # cash paid for acquisitions, net of cash acquired
    'cash': 'cash', 'short_term_investments': 'short_term_investments',
    'commercial_paper': 'commercial_paper_info',   # issuers' debt totals usually include it: informational only
    'deferred_revenue_current': 'deferred_revenue_current', 'deferred_revenue_noncurrent': 'deferred_revenue_noncurrent',
    'debt': 'long_term_debt_tagged',            # issuer totals: scope (incl. current portion or not) resolved by dataqa
    'deferred_revenue': 'deferred_revenue_total', 'cash_and_investments': 'cash_and_investments_total',
}
BUNDLE_INSTANT = {'shares_outstanding_cover', 'rpo', 'arr', 'deferred_revenue_total', 'cash_and_investments_total', 'cash',
                  'short_term_investments', 'commercial_paper_info', 'long_term_debt_tagged', 'deferred_revenue_current',
                  'deferred_revenue_noncurrent'}
# bundle source kind -> FactBook kind; anything else: 'secondary'
SOURCE_KIND = {'issuer_release': 'issuer_document', 'earnings_release': 'issuer_document', 'filing': 'issuer_document',
               'regulator': 'primary_document', 'official_statistics': 'primary_document'}
_YTD_MARKS = ('YTD', 'H1', '1H', 'HY', '6M', '9M')
_HALF2_MARKS = ('H2', '2H')


def _bundle_period(q, metric):
    """(period type of a bundle row, reason when it cannot be used): 'instant', 'annual', 'quarter', 'ytd' or None.

    The duration decides when period_start is given (350-380 days annual, 80-100 a quarter, 170-290 a year to date),
    else an explicit period_type, else the label: YTD/H1/6M/9M marks a year to date, FY<digits> alone a fiscal year,
    anything else a standalone quarter. A second half (H2) is neither a quarter, a year nor a year to date: skipped."""
    if metric in BUNDLE_INSTANT:
        return 'instant', None
    label = ''.join(str(q.get('period') or '').upper().split())
    ptype = str(q.get('period_type') or '').lower()
    if q.get('period_start'):
        n = (_d(q['period_end']) - _d(q['period_start'])).days
        kind = 'annual' if 350 <= n <= 380 else 'quarter' if 80 <= n <= 100 else 'ytd' if 170 <= n <= 290 else None
        if kind == 'ytd' and (any(m in label for m in _HALF2_MARKS) or ptype in ('h2', 'second_half')):
            kind = None
        return kind, None if kind else f'period_start..period_end spans {n} days: not a fiscal year, quarter or year to date'
    if q['metric'].endswith('_ytd') or ptype in ('ytd', 'year_to_date', 'h1', 'first_half', 'six_months', 'nine_months'):
        return 'ytd', None
    if ptype in ('annual', 'fy', 'fiscal_year'):
        return 'annual', None
    if ptype == 'quarter':
        return 'quarter', None
    if any(m in label for m in _HALF2_MARKS) or ptype in ('h2', 'second_half'):
        return None, f"period {q.get('period')!r} is a second half: neither a quarter, a fiscal year nor a year to date"
    if any(m in label for m in _YTD_MARKS):
        return 'ytd', None
    if label.startswith('FY') and label[2:].isdigit():
        return 'annual', None
    return 'quarter', None


def add_qualitative_bundle(fb, bundle, provider='web'):
    """Load a researched evidence bundle: {sources, quarterly_facts, guidance, items, conflicts, open_questions}.

    Kinds: issuer releases, earnings releases and filings become 'issuer_document', regulator and official-statistics
    documents 'primary_document', press, analysis and anything else 'secondary'; the bundle kind stays in
    source['dataset'] and how it was read in source['retrieval'] (default search_excerpt). Every dated document is
    usable LAG_DAYS after publication, like a filing. Only sources usable at fb['as_of'] are registered (citable); an
    undated source is registered and flagged 'undated'.

    Availability of a fact: its cited issuer documents decide when any is cited (secondary citations neither advance nor
    delay it), else its secondary sources. A document dated before the period end cannot state the value and is
    ignored; the earliest remaining dated document makes it public and the fact is attributed to it. With none, an
    undated document counts at the filing deadline (period end + lag; annual for fiscal-year ends) and no earlier than
    the deadline of the latest period it alone states in the bundle (a later 10-Q quoted for an earlier quarter dates
    the value to that 10-Q), capped at the bundle's retrieval date (every cited source was public then); the date is
    flagged estimated. The row's reported_in (the date of the document that states the value) decides when given; a
    reported_not_before lower bound raises the date and flags it estimated. An item (a statement that may combine its
    sources) is available when the latest of its cited documents is, an undated one at its deadline estimate (flagged).
    Rows whose period length cannot be told (a second half) are skipped and listed in research_notes['skipped_rows'].
    Guidance needs an issue date usable by as_of and keeps only sources usable by then; a later reaffirmed_at is
    dropped. Conflicts and open questions written after as_of are kept only when every source they cite is dated and
    usable by as_of; research_notes['withheld_at_as_of'] counts what was left out."""
    pub, kinds = {}, {}
    as_of = fb['as_of']
    raw_retrieved = bundle.get('retrieved_at') or bundle.get('as_of')
    retrieved = str(raw_retrieved)[:10] if raw_retrieved else None
    for src in bundle.get('sources', []):
        bk = src.get('kind') or 'unspecified'
        p = str(src['published_at'])[:10] if src.get('published_at') else None
        pub[src['id']], kinds[src['id']] = p, SOURCE_KIND.get(bk, 'secondary')
        if p and _d(_plus(p, LAG_DAYS)) > _d(as_of):
            continue                                    # not yet usable at as_of: not a citable source
        sid, kind = 'web:' + src['id'], kinds[src['id']]
        retrieval = src.get('retrieval') or 'search_excerpt'
        add_source(fb, sid, 'issuer' if kind == 'issuer_document' else bk if kind == 'primary_document' else provider, bk, kind,
                   raw_retrieved or as_of, url=src.get('url'),
                   note=f"{src.get('title', '')} | {src.get('publisher', '')} | retrieval={retrieval}")
        fb['sources'][sid].update(published_at=p, retrieval=retrieval)
        if not p:
            fb['sources'][sid]['undated'] = True
    registered = {s for s in pub if 'web:' + s in fb['sources']}

    def estimate(ref, lag):
        est = _est_available(ref, lag, fb)
        if retrieved and _d(retrieved) < _d(est):
            est = max(retrieved, ref)                   # every cited source was public when the bundle was retrieved
        return est

    def fact_avail(ids, pe, end, lag, q):
        pool = [s for s in ids if kinds[s] != 'secondary'] or ids
        dated = sorted((pub[s], s) for s in pool if pub[s] and pub[s] >= end)
        if dated:
            av, est, src = _plus(dated[0][0], LAG_DAYS), False, dated[0][1]
        else:
            und = [s for s in pool if not pub[s]]
            av, est, src = max([estimate(pe, lag), end] + [floor[s] for s in und if s in floor]), True, (und or pool)[0]
        if q.get('reported_in'):
            av, est = max(_plus(str(q['reported_in'])[:10], LAG_DAYS), end), False
        elif q.get('reported_not_before'):
            nb = _plus(str(q['reported_not_before'])[:10], LAG_DAYS)
            if nb > av:
                av, est = nb, True
        if src not in registered:                       # attribute to a cited document citable at as_of
            src = next((s for s in pool if s in registered), next((s for s in ids if s in registered), None))
        return av, est, src

    def item_avail(ids, ref, lag):
        av = max([_plus(pub[s], LAG_DAYS) for s in ids if pub[s]] + [ref])
        und = [s for s in ids if not pub[s]]
        if und:
            return max([av, estimate(ref, lag)] + [floor[s] for s in und if s in floor]), True
        return av, False

    rows = bundle.get('quarterly_facts', [])
    period_of = {id(q): _bundle_period(q, BUNDLE_METRICS.get(q['metric'][:-4] if q['metric'].endswith('_ytd') else q['metric']))
                 for q in rows}
    fy_ends = _annual_ends(fb) | {str(q['period_end'])[:10] for q in rows if period_of[id(q)][0] == 'annual'}
    floor = {}      # undated source -> earliest it can have been public: the filing deadline of the latest period it alone states
    for q in rows:
        ids = [s for s in q.get('source_ids') or [] if s in kinds]
        kind = period_of[id(q)][0]
        if len(ids) != 1 or pub[ids[0]] or kind is None:
            continue
        pe = str(q['period_end'])[:10]
        d = max(estimate(pe, 'annual' if kind == 'annual' else _lag_kind(pe, fy_ends)), str(q.get('measured_at') or pe)[:10])
        if not q.get('reported_in') and q.get('reported_not_before'):
            d = max(d, _plus(str(q['reported_not_before'])[:10], LAG_DAYS))
        floor[ids[0]] = max(floor.get(ids[0], d), d)
    skipped = []
    for q in rows:
        ytd = q['metric'].endswith('_ytd')
        metric = BUNDLE_METRICS.get(q['metric'][:-4] if ytd else q['metric'])
        ids = [s for s in q.get('source_ids') or [] if s in kinds]
        if metric is None or not ids or q.get('value') is None:
            continue
        kind, why = period_of[id(q)]
        if kind is None:
            skipped.append({'metric': q['metric'], 'period': q.get('period'), 'period_end': q['period_end'], 'reason': why})
            continue
        pe = str(q['period_end'])[:10]
        end = str(q['measured_at'])[:10] if metric == 'shares_outstanding_cover' and q.get('measured_at') else pe
        av, est, src = fact_avail(ids, pe, end, 'annual' if kind == 'annual' else _lag_kind(pe, fy_ends), q)
        if src is None:
            continue                                    # every cited document is dated after as_of
        label = ''.join(str(q.get('period') or '').upper().split())
        period = {'ytd': 'YTD@' + end, 'instant': 'I@' + end, 'quarter': 'Q@' + end}.get(kind) or (
            label if label.startswith('FY') and label[2:].isdigit() else f'FY{fiscal_year(end)}')
        add_fact(fb, metric, period, end, q['value'], 'web:' + src, 'as_reported' if not est else 'normalized', av,
                 estimated=est, unit='shares' if metric == 'shares_outstanding_cover' else None, concept='bundle:' + q['metric'],
                 period_start=q.get('period_start'))
    withheld = {'guidance': 0, 'conflicts': 0, 'open_questions': 0}
    for g in bundle.get('guidance', []):
        sids = ['web:' + s for s in g.get('source_ids', []) if s in registered]
        issued = str(g.get('issued_at') or '')[:10]
        if not issued or _d(_plus(issued, LAG_DAYS)) > _d(as_of) or (g.get('source_ids') and not sids):
            withheld['guidance'] += 1                   # undated, issued after as_of, or no source usable by then
            continue
        row = {**g, 'source_ids': sids}
        if row.get('reaffirmed_at') and _d(_plus(str(row['reaffirmed_at'])[:10], LAG_DAYS)) > _d(as_of):
            row.pop('reaffirmed_at')                    # a reaffirmation after as_of is not known at as_of
        fb.setdefault('guidance', []).append(row)
    for it in bundle.get('items', []):
        ids = [s for s in it.get('source_ids', []) if s in kinds]
        ref = str(it.get('as_of') or retrieved or as_of)[:10]
        if not ids:
            continue
        av, est = item_avail(ids, ref, _lag_kind(ref, fy_ends))
        if _d(av) > _d(as_of) or not all(s in registered for s in ids):
            continue
        add_qualitative(fb, it['id'], it['topic'], it['statement'], it['kind'], ['web:' + s for s in ids], ref)
        fb['qualitative'][-1].update(available_at=av, available_at_estimated=est)

    def knowable(x):        # a note written at retrieval is knowable at as_of only through sources dated and usable by then
        ids = x.get('source_ids') if isinstance(x, dict) else None
        return bool(ids) and all(s in registered and pub.get(s) for s in ids)
    notes = {}
    for key in ('conflicts', 'open_questions'):
        xs = list(bundle.get(key, []))
        if retrieved and _d(retrieved) > _d(as_of):
            keep = [x for x in xs if knowable(x)]
            withheld[key] = len(xs) - len(keep)
            xs = keep
        notes[key] = xs
    fb.setdefault('research_notes', {}).update(notes)
    if skipped:
        fb['research_notes']['skipped_rows'] = skipped
    if any(withheld.values()):
        fb['research_notes']['withheld_at_as_of'] = withheld
    return fb


# ---------------------------------------------------------------- accessors (point-in-time)

def usable(fb, fact):
    """Published by as_of and about a period that had ended by then."""
    return _d(fact['available_at']) <= _d(fb['as_of']) and _d(fact['period_end']) <= _d(fb['as_of'])


def item_usable(fb, item, missing=False):
    """A qualitative item is usable once it is available (available_at, else its as_of: usable()'s rule for facts); an
    undated item counts only where the caller says that is the conservative reading (missing=True)."""
    when = item.get('available_at') or item.get('as_of')
    return _d(when) <= _d(fb['as_of']) if when else missing


def candidates(fb, metric, period=None, period_end=None):
    """Usable facts best first: secondary sources last, then basis, filing-derived before other kinds, an issuer's
    filing before its earlier release (the filing is the revised figure), earliest available."""
    out = [f for f in fb['facts'] if f['metric'] == metric and usable(fb, f)
           and (period is None or f['period'] == period) and (period_end is None or f['period_end'] == period_end)]
    src = lambda f: fb['sources'][f['source_id']]
    return sorted(out, key=lambda f: (src(f)['kind'] == 'secondary', BASIS_RANK[f['basis']], src(f)['kind'] != 'filing_derived',
                                      src(f).get('dataset') in RELEASE_DATASETS, f['available_at']))


def get(fb, metric, period=None, period_end=None):
    c = candidates(fb, metric, period, period_end)
    return c[0] if c else None


def value(fb, metric, period=None, period_end=None, default=None):
    f = get(fb, metric, period, period_end)
    return f['value'] if f else default


def _is_annual(f):
    return f['period'].startswith(('FY', 'AVFY@'))


def series(fb, metric, kind='annual'):
    """[(period_end, value, fact)] ascending; one fact per period end (best basis first)."""
    keep = {}
    for f in candidates(fb, metric):
        if kind == 'annual' and not _is_annual(f):
            continue
        if kind == 'quarter' and not f['period'].startswith('Q@') and not f['period'][-2:] in ('Q1', 'Q2', 'Q3', 'Q4'):
            continue
        if kind == 'instant' and not f['period'].startswith('I@'):
            continue
        if f['period_end'] in keep or any(abs((_d(f['period_end']) - _d(e)).days) <= SAME_PERIOD_DAYS for e in keep):
            continue                     # same fiscal period dated differently (52/53-week calendar vs month-end)
        keep[f['period_end']] = f
    return [(e, keep[e]['value'], keep[e]) for e in sorted(keep)]


def latest_instant(fb, metric, max_age_days=None):
    s = series(fb, metric, 'instant')
    if not s:
        return None
    end, v, f = s[-1]
    if max_age_days is not None and (_d(fb['as_of']) - _d(end)).days > max_age_days:
        return None
    return f


def _fv(metric, f):
    """A fact's value for summing: payments and proceeds (PAYMENT_METRICS) by magnitude, whatever sign the source used."""
    return abs(f['value']) if metric in PAYMENT_METRICS else f['value']


def ttm(fb, metric):
    """Trailing twelve months, the most recent of: four contiguous standalone quarters; the latest fiscal year plus the
    standalone quarters since it less the same quarters a year earlier; the latest fiscal year plus the year to date
    less the year to date a year earlier (ties go to that order). Else the latest fiscal year.

    Returns {'value', 'period_end', 'method': quarters|annual_plus_ytd|latest_fiscal_year, 'facts': [...],
    'signs': [+1/-1 per fact]} or None; value == sum(sign * fact value), payments (PAYMENT_METRICS) by magnitude.
    'alternatives' lists the older candidates not chosen. A year-to-date figure ending more than 366 days after the
    latest fiscal year (that year's successor missing) is not used: 'ytd_rejected' says why."""
    q = series(fb, metric, 'quarter')
    a = series(fb, metric, 'annual')
    same = timedelta(days=SAME_PERIOD_DAYS)
    v = lambda f: _fv(metric, f)
    cands = []                                  # (preference, result)
    if len(q) >= 4:
        last4 = q[-4:]
        span = (_d(last4[-1][0]) - _d(last4[0][0])).days
        if 250 <= span <= 300 and (not a or _d(last4[-1][0]) >= _d(a[-1][0]) - same):
            cands.append((0, {'value': sum(v(x[2]) for x in last4), 'period_end': last4[-1][0], 'method': 'quarters',
                              'facts': [x[2] for x in last4], 'signs': [1] * 4}))
    out = None
    if a:
        a_end, fy = _d(a[-1][0]), a[-1][2]
        post = [x for x in q if _d(x[0]) > a_end + same]
        steps = [(_d(b[0]) - _d(a0[0])).days for a0, b in zip([(a[-1][0],)] + post, post)]
        if post and not all(60 <= d <= 120 for d in steps):
            post = []                              # a missing quarter would silently drop its growth: not a TTM
        matched = False
        if post:                                   # TTM = last FY + quarters since - same quarters a year earlier
            prior = []
            for end, _, _ in post:
                want = _d(end) - timedelta(days=364)
                match = [x for x in q if abs((_d(x[0]) - want).days) <= 10]
                prior.append(match[0] if match else None)
            if all(prior):
                matched = True
                cands.append((1, {'value': v(fy) + sum(v(x[2]) for x in post) - sum(v(x[2]) for x in prior),
                                  'period_end': post[-1][0], 'method': 'annual_plus_ytd',
                                  'facts': [fy] + [x[2] for x in post] + [x[2] for x in prior],
                                  'signs': [1] * (1 + len(post)) + [-1] * len(prior)}))
        out = {'value': v(fy), 'period_end': a[-1][0], 'method': 'latest_fiscal_year', 'facts': [fy], 'signs': [1],
               'newer_quarters_unmatched': [] if matched else [x[0] for x in post]}
        ytd = [f for f in candidates(fb, metric) if f['period'].startswith('YTD@')]
        late = sorted(f['period_end'] for f in ytd if (_d(f['period_end']) - a_end).days > 366)
        cur = sorted((f for f in ytd if a_end + same < _d(f['period_end']) <= a_end + timedelta(days=366)),
                     key=lambda f: f['period_end'])
        if cur:                                   # TTM = last FY + YTD now - YTD a year earlier (10-Q cash-flow statements)
            now = cur[-1]
            want = _d(now['period_end']) - timedelta(days=364)
            prev = [f for f in ytd if abs((_d(f['period_end']) - want).days) <= 10]
            if prev:
                cands.append((2, {'value': v(fy) + v(now) - v(prev[0]), 'period_end': now['period_end'],
                                  'method': 'annual_plus_ytd', 'facts': [fy, now, prev[0]], 'signs': [1, 1, -1]}))
            else:
                out['ytd_rejected'] = f"no year-to-date figure about a year before {now['period_end']}"
        if late:
            out['ytd_rejected'] = (f'year-to-date ending {late[-1]} is more than 366 days after the latest fiscal year '
                                   f'({a[-1][0]}): the fiscal year in between is missing, so it is not a trailing twelve months')
    if not cands:
        return out
    latest = max(_d(r['period_end']) for _, r in cands)      # ends within a week are the same period (52/53-week dating)
    pref, res = min((c for c in cands if (latest - _d(c[1]['period_end'])).days <= SAME_PERIOD_DAYS), key=lambda c: c[0])
    names = ('four standalone quarters', 'fiscal year + standalone quarters since', 'fiscal year + year to date')
    alt = [{'method': r['method'], 'basis': names[p], 'period_end': r['period_end'], 'value': r['value']}
           for p, r in cands if r is not res]
    if alt:
        res['alternatives'] = alt
    return res


def annual_table(fb, metrics):
    """{period_end: {metric: value}} for fiscal years present in any of the metrics."""
    out = {}
    for m in metrics:
        for end, v, _ in series(fb, m, 'annual'):
            out.setdefault(end, {})[m] = v
    for m in metrics:
        if m in INSTANT_TAGS or m in AV_INSTANT:
            for end, v, _ in series(fb, m, 'instant'):
                if end in out:
                    out[end].setdefault(m, v)
    return dict(sorted(out.items()))


def fingerprint(fb):
    return digest(fb)
