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

LAG_DAYS = 2                       # a filing is usable two days after it is filed (snapshot.PIT_LAG_DAYS)
EST_LAG = {'annual': 75, 'quarter': 45}   # deadline-based estimate when the filing date is unknown (flagged)

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
KINDS = {'filing_derived', 'vendor_normalized', 'market_feed', 'consensus', 'document', 'synthetic'}
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


def _est_available(period_end, kind):
    return (_d(period_end) + timedelta(days=EST_LAG[kind])).isoformat()


def _fy_label(fy, kind, fp=None):
    return f'FY{fy}' if kind == 'annual' else f'{fy}{fp}'


def _map_rows(fb, rows, tags, source_id, kind, filed_by_end=None, statement=None):
    """FMP as-reported rows: [{'fiscalYear','period','date','data':{concept: value}}]."""
    added = 0
    for r in rows:
        end = r['date'][:10]
        data = {k.lower(): v for k, v in (r.get('data') or {}).items()}
        filed = (filed_by_end or {}).get(end)
        avail = (_d(filed) + timedelta(days=LAG_DAYS)).isoformat() if filed else _est_available(end, kind)
        label = _fy_label(r.get('fiscalYear'), kind, r.get('period'))
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

    First-filed value per period (later restatements are ignored for point-in-time use), quarters derived
    from year-to-date facts only when both inputs were filed (pit.quarterly)."""
    from .pit import quarterly
    add_source(fb, sid, 'sec', 'companyfacts', 'filing_derived', retrieved_at, sha=digest(payload),
               url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(payload.get('cik', 0)):010d}.json")
    gaap = payload.get('facts', {}).get('us-gaap', {})
    lower = {k.lower(): k for k in gaap}
    n = 0
    for metric, cands in DURATION_TAGS.items():
        unit = 'USD/shares' if metric == 'eps_diluted' else ('shares' if metric.startswith('shares_') else 'USD')
        for c in cands:
            if c not in lower:
                continue
            rows = gaap[lower[c]].get('units', {}).get(unit, [])
            annual = {}
            for r in rows:
                if not r.get('start') or r.get('form') not in ('10-K', '10-K/A', '20-F', '40-F'):
                    continue
                if not 350 <= (_d(r['end']) - _d(r['start'])).days <= 380:
                    continue
                if r['end'] not in annual or r['filed'] < annual[r['end']]['filed']:
                    annual[r['end']] = r
            for end, r in annual.items():
                add_fact(fb, metric, f"FY{_d(end).year if _d(end).month > 6 else _d(end).year - 1}", end, r['val'], sid,
                         'as_reported', (_d(r['filed']) + timedelta(days=LAG_DAYS)).isoformat(), period_start=r['start'],
                         concept=lower[c], unit=UNITS.get(metric))
                n += 1
            if unit == 'USD':
                for end, (v, filed) in quarterly({'us-gaap': {lower[c]: gaap[lower[c]]}}, [lower[c]]).items():
                    q4 = any(abs((_d(end) - _d(a)).days) <= SAME_PERIOD_DAYS for a in annual)   # Q4 = FY - 9M YTD
                    add_fact(fb, metric, f'Q@{end}', end, v, sid, 'derived' if q4 else 'as_reported',
                             (_d(filed) + timedelta(days=LAG_DAYS)).isoformat(), concept=lower[c])
                    n += 1
            if annual:
                break
    for metric, cands in INSTANT_TAGS.items():
        unit = 'shares' if metric in UNITS else 'USD'
        for c in cands:
            if c not in lower:
                continue
            first = {}
            for r in gaap[lower[c]].get('units', {}).get(unit, []):
                if r['end'] not in first or r['filed'] < first[r['end']]['filed']:
                    first[r['end']] = r
            for end, r in first.items():
                add_fact(fb, metric, 'I@' + end, end, r['val'], sid, 'as_reported',
                         (_d(r['filed']) + timedelta(days=LAG_DAYS)).isoformat(), concept=lower[c], unit=UNITS.get(metric))
                n += 1
            if first:
                break
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
    and flagged, so historical as_of use is limited (connectors.py note)."""
    n = 0
    for part in ('income', 'balance', 'cashflow'):
        p = payloads.get(part)
        if not p:
            continue
        sid = add_source(fb, f'{prefix}:{part}', 'alpha_vantage', part, 'vendor_normalized', retrieved_at, sha=digest(p))
        for kind, key in (('annual', 'annualReports'), ('quarter', 'quarterlyReports')):
            for r in p.get(key, []):
                end = r['fiscalDateEnding']
                for field, metric in AV_MAP.items():
                    raw = r.get(field)
                    if raw in (None, 'None', ''):
                        continue
                    try:
                        v = float(raw)
                    except ValueError:
                        continue
                    if metric == 'capex':
                        v = abs(v)
                    period = 'I@' + end if metric in AV_INSTANT else (f'AVFY@{end}' if kind == 'annual' else f'Q@{end}')
                    add_fact(fb, metric, period, end, v, sid, 'normalized', _est_available(end, kind), estimated=True,
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


def from_fmp_estimates(fb, rows, retrieved_at, as_of=None, sid='fmp:financial_estimates'):
    """FMP analyst financial-estimates (annual): consensus is a vendor estimate, never a reported fact."""
    add_source(fb, sid, 'fmp', 'financial-estimates-annual', 'consensus', retrieved_at, sha=digest(rows))
    for r in rows:
        for metric, k, lo, hi, nk in (('revenue', 'revenueAvg', 'revenueLow', 'revenueHigh', 'numAnalystsRevenue'),
                                      ('eps', 'epsAvg', 'epsLow', 'epsHigh', 'numAnalystsEps'),
                                      ('ebitda', 'ebitdaAvg', 'ebitdaLow', 'ebitdaHigh', 'numAnalystsRevenue'),
                                      ('net_income', 'netIncomeAvg', 'netIncomeLow', 'netIncomeHigh', 'numAnalystsEps')):
            if r.get(k) is None:
                continue
            fb['estimates'].append({'metric': metric, 'period_end': r['date'][:10], 'fiscal_year': int(r['date'][:4]),
                                    'value': float(r[k]), 'low': r.get(lo), 'high': r.get(hi), 'n_analysts': r.get(nk),
                                    'source_id': sid, 'as_of': as_of or retrieved_at[:10]})


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
    'cash': 'cash', 'short_term_investments': 'short_term_investments', 'commercial_paper': 'short_term_borrowings',
    'debt': 'long_term_debt_tagged',            # issuer totals: scope (incl. current portion or not) resolved by dataqa
    'deferred_revenue': 'deferred_revenue_total', 'cash_and_investments': 'cash_and_investments_total',
}
BUNDLE_INSTANT = {'shares_outstanding_cover', 'rpo', 'arr', 'deferred_revenue_total', 'cash_and_investments_total', 'cash',
                  'short_term_investments', 'short_term_borrowings', 'long_term_debt_tagged'}
SOURCE_KIND = {'issuer_release': 'document', 'filing': 'document', 'press': 'document', 'analysis': 'document'}


def add_qualitative_bundle(fb, bundle, provider='web'):
    """Load a researched evidence bundle: {sources, quarterly_facts, guidance, items, conflicts, open_questions}.

    Sources keep their publication date; a fact or item is usable only from its source's publication date
    (unknown date -> period end + deadline, flagged). Issuer releases and filings read through search excerpts
    stay 'document' sources with retrieval recorded, so confidence reflects that the filing was not read."""
    pub = {}
    for src in bundle.get('sources', []):
        sid = 'web:' + src['id']
        prov = 'issuer' if src.get('kind') in ('issuer_release', 'filing') else provider
        add_source(fb, sid, prov, src.get('kind', 'document'), SOURCE_KIND.get(src.get('kind'), 'document'),
                   bundle.get('retrieved_at') or fb['as_of'], url=src.get('url'),
                   note=f"{src.get('title', '')} | {src.get('publisher', '')} | retrieval={src.get('retrieval', 'search_excerpt')}")
        fb['sources'][sid]['published_at'] = src.get('published_at')
        fb['sources'][sid]['retrieval'] = src.get('retrieval', 'search_excerpt')
        pub[src['id']] = src.get('published_at')
    for q in bundle.get('quarterly_facts', []):
        ytd = q['metric'].endswith('_ytd')
        metric = BUNDLE_METRICS.get(q['metric'][:-4] if ytd else q['metric'])
        if metric is None or not q.get('source_ids') or q.get('value') is None:
            continue
        dates = [pub.get(s) for s in q['source_ids'] if pub.get(s)]
        avail, est = (max(dates), False) if dates else (_est_available(q['period_end'], 'quarter'), True)
        end = q.get('measured_at') if metric == 'shares_outstanding_cover' and q.get('measured_at') else q['period_end']
        period = ('YTD@' if ytd else 'I@' if metric in BUNDLE_INSTANT else 'Q@') + end
        add_fact(fb, metric, period, end, q['value'], 'web:' + q['source_ids'][0],
                 'as_reported' if not est else 'normalized', avail, estimated=est,
                 unit='shares' if metric == 'shares_outstanding_cover' else None, concept='bundle:' + q['metric'])
    for g in bundle.get('guidance', []):
        if _d(g.get('issued_at') or fb['as_of']) <= _d(fb['as_of']):
            fb.setdefault('guidance', []).append({**g, 'source_ids': ['web:' + s for s in g.get('source_ids', [])]})
    for it in bundle.get('items', []):
        ids = ['web:' + s for s in it.get('source_ids', []) if 'web:' + s in fb['sources']]
        if ids and _d(it.get('as_of') or fb['as_of']) <= _d(fb['as_of']):
            add_qualitative(fb, it['id'], it['topic'], it['statement'], it['kind'], ids, it.get('as_of') or fb['as_of'])
    fb.setdefault('research_notes', {}).update({'conflicts': bundle.get('conflicts', []), 'open_questions': bundle.get('open_questions', [])})
    return fb


# ---------------------------------------------------------------- accessors (point-in-time)

def usable(fb, fact):
    return _d(fact['available_at']) <= _d(fb['as_of'])


def candidates(fb, metric, period=None, period_end=None):
    out = [f for f in fb['facts'] if f['metric'] == metric and usable(fb, f)
           and (period is None or f['period'] == period) and (period_end is None or f['period_end'] == period_end)]
    return sorted(out, key=lambda f: (BASIS_RANK[f['basis']], fb['sources'][f['source_id']]['kind'] != 'filing_derived',
                                      f['available_at']))


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


def ttm(fb, metric):
    """Trailing twelve months: four contiguous standalone quarters if available, else the latest fiscal year.

    Returns {'value', 'period_end', 'method': quarters|latest_fiscal_year, 'facts': [...]} or None."""
    q = series(fb, metric, 'quarter')
    a = series(fb, metric, 'annual')
    if len(q) >= 4:
        last4 = q[-4:]
        span = (_d(last4[-1][0]) - _d(last4[0][0])).days
        if 250 <= span <= 300 and (not a or _d(last4[-1][0]) >= _d(a[-1][0])):
            return {'value': sum(x[1] for x in last4), 'period_end': last4[-1][0], 'method': 'quarters',
                    'facts': [x[2] for x in last4]}
    if a:
        a_end = _d(a[-1][0])
        post = [x for x in q if _d(x[0]) > a_end]
        if post:                                   # TTM = last FY + year-to-date - same quarters a year earlier
            prior = []
            for end, _, _ in post:
                want = _d(end) - timedelta(days=364)
                match = [x for x in q if abs((_d(x[0]) - want).days) <= 10]
                prior.append(match[0] if match else None)
            if all(prior):
                v = a[-1][1] + sum(x[1] for x in post) - sum(x[1] for x in prior)
                return {'value': v, 'period_end': post[-1][0], 'method': 'annual_plus_ytd',
                        'facts': [a[-1][2]] + [x[2] for x in post] + [x[2] for x in prior]}
        ytd = [f for f in candidates(fb, metric) if f['period'].startswith('YTD@')]
        cur = sorted((f for f in ytd if _d(f['period_end']) > a_end), key=lambda f: f['period_end'])
        if cur:                                   # TTM = last FY + YTD now - YTD a year earlier (10-Q cash-flow statements)
            now = cur[-1]
            want = _d(now['period_end']) - timedelta(days=364)
            prev = [f for f in ytd if abs((_d(f['period_end']) - want).days) <= 10]
            if prev:
                return {'value': a[-1][1] + now['value'] - prev[0]['value'], 'period_end': now['period_end'],
                        'method': 'annual_plus_ytd', 'facts': [a[-1][2], now, prev[0]]}
        return {'value': a[-1][1], 'period_end': a[-1][0], 'method': 'latest_fiscal_year', 'facts': [a[-1][2]],
                'newer_quarters_unmatched': [x[0] for x in post]}
    return None


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
