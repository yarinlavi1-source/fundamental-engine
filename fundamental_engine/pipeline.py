"""The single analysis route: sources -> validation -> understanding -> analysis -> forecast ->
valuation -> risk -> independent review -> decision (docs/ARCHITECTURE.md).

Python cannot call the user's connectors (they live in Claude's session), so collection is split in two:
`collection_plan` lists the exact connector calls and the file name each raw payload must be saved
under (outside Git), and `factbook_from_run_dir` turns that folder into a FactBook without retyping
numbers. Everything after that is deterministic and reproducible from the casefile's inputs.

A stage that cannot run records why and the later stages degrade honestly (e.g. no valuation ->
the decision says the price cannot be assessed). Exceptions are recorded, never swallowed silently.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os

from . import __version__
from . import facts as F

STAGES = ('dataqa', 'analysis', 'model_card', 'forecast', 'valuation', 'risk', 'review', 'decision')


def collection_plan(ticker, as_of, cik=None):
    """Connector calls for one company, in priority order, with the raw file name for each payload.

    Save each payload verbatim under runs/<TICKER>_<as_of>/raw/<file> (gitignored) and record retrieval
    time and sha256 in MANIFEST.json. Denied or rate-limited calls are recorded, not retried in loops."""
    t = ticker.upper()
    plan = [
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-income-statements', 'symbol': t, 'period': 'annual', 'limit': 6}, 'fmp__as-reported-income-statements__annual.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-balance-statements', 'symbol': t, 'period': 'annual', 'limit': 6}, 'fmp__as-reported-balance-statements__annual.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-cashflow-statements', 'symbol': t, 'period': 'annual', 'limit': 6}, 'fmp__as-reported-cashflow-statements__annual.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-income-statements', 'symbol': t, 'period': 'quarter', 'limit': 8}, 'fmp__as-reported-income-statements__quarter.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-balance-statements', 'symbol': t, 'period': 'quarter', 'limit': 8}, 'fmp__as-reported-balance-statements__quarter.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'as-reported-cashflow-statements', 'symbol': t, 'period': 'quarter', 'limit': 8}, 'fmp__as-reported-cashflow-statements__quarter.json'),
        ('fmp', 'mcp__FMP__company', {'endpoint': 'shares-float', 'symbol': t}, 'fmp__shares-float__current.json'),
        ('fmp', 'mcp__FMP__company', {'endpoint': 'profile-symbol', 'symbol': t}, 'fmp__profile-symbol__current.json'),
        ('fmp', 'mcp__FMP__analyst', {'endpoint': 'financial-estimates', 'symbol': t, 'period': 'annual', 'limit': 6}, 'fmp__financial-estimates__annual.json'),
        ('fmp', 'mcp__FMP__statements', {'endpoint': 'revenue-product-segmentation', 'symbol': t, 'period': 'annual'}, 'fmp__revenue-product-segmentation__annual.json'),
        ('alpha_vantage', 'mcp__Alpha_Vantage_MCP_Server__INCOME_STATEMENT', {'symbol': t}, 'av__income.json'),
        ('alpha_vantage', 'mcp__Alpha_Vantage_MCP_Server__BALANCE_SHEET', {'symbol': t}, 'av__balance.json'),
        ('alpha_vantage', 'mcp__Alpha_Vantage_MCP_Server__CASH_FLOW', {'symbol': t}, 'av__cashflow.json'),
        ('ib', 'mcp__Interactive_Brokers__search_contracts', {'query': t}, f'ib__search-contracts-{t}__current.json'),
        ('ib', 'mcp__Interactive_Brokers__get_price_snapshot', {'contract_id': '<from search>', 'market_data_names': ['last', 'prior_close', 'misc_statistics']}, f'ib__price-snapshot-{t}__current.json'),
        # splits after the last filing (the share count predates them): price history with corporate actions
        ('ib', 'mcp__Interactive_Brokers__get_price_history', {'contract_id': '<from search>', 'security_type': 'STK', 'step': 'ONE_MONTH', 'period': 'ONE_YEAR', 'outside_rth': False, 'include_corporate_actions': True}, f'ib__corporate-actions-{t}__current.json'),
    ]
    if cik:
        plan.append(('sec', 'curl data.sec.gov companyfacts (only where the network allows it)', {'cik': cik}, 'sec__companyfacts.json'))
    return {'ticker': t, 'as_of': as_of, 'folder': f'runs/{t}_{as_of}/raw/',
            'calls': [{'provider': p, 'tool': tool, 'args': a, 'file': f} for p, tool, a, f in plan],
            'rules': ['Save payloads verbatim (copy saved tool-result files byte for byte); never retype numbers.',
                      'Second source: SEC companyfacts or Alpha Vantage statements for cross-checks when available.',
                      'Primary documents (10-K/10-Q/8-K) resolve definition conflicts; note what was read directly.',
                      'Portfolio data is read-only and private: never inside Git.']}


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def factbook_from_run_dir(raw_dir, company, as_of):
    """Build a FactBook from a raw folder written per collection_plan (file names are the contract)."""
    def p(name):
        return os.path.join(raw_dir, name)
    manifest = _load(p('MANIFEST.json')) if os.path.exists(p('MANIFEST.json')) else {}
    when = {}
    for c in manifest.get('calls', manifest if isinstance(manifest, list) else []):
        if isinstance(c, dict) and c.get('file'):
            when[c['file']] = c.get('retrieved_at_utc') or c.get('retrieved_utc') or c.get('retrieved_at')
    cfacts = _load(p('sec__companyfacts.json')) if os.path.exists(p('sec__companyfacts.json')) else None
    rc = F.reporting_currency(cfacts) if cfacts else None
    if rc and rc != company.get('currency'):
        # the filings decide the currency of every number; the quote keeps its own (converted per ordinary share below)
        company = {**company, 'currency': rc, 'quote_currency': company.get('quote_currency') or company.get('currency') or 'USD'}
    fb = F.new(company, as_of)
    notes = fb.setdefault('collection_notes', [])
    if rc and company.get('quote_currency') and company['quote_currency'] != rc:
        notes.append(f"the filings report in {rc}; the quote is in {company['quote_currency']} and is converted per ordinary share")

    def later(d, what):
        # information dated after the cutoff is skipped, never relabelled to the cutoff (no look-ahead)
        if d and str(d)[:10] > as_of:
            notes.append(f'{what} dated {str(d)[:10]} is after as_of {as_of}; not used')
            return True
        return False
    for kind in ('annual', 'quarter'):
        parts = {}
        for part in ('income', 'balance', 'cashflow'):
            name = f'fmp__as-reported-{part}-statements__{kind}.json'
            if os.path.exists(p(name)):
                data = _load(p(name))
                if isinstance(data, list) and data:
                    parts[part] = data
        if parts:
            got = when.get(f'fmp__as-reported-income-statements__{kind}.json')
            if got and str(got)[:10] > as_of:
                notes.append(f'FMP as-reported {kind} retrieved {str(got)[:10]} after as_of; facts are dated by their filing dates')
            F.from_fmp_as_reported(fb, parts, got or as_of, kind=kind)
    av = {k: _load(p(f'av__{k}.json')) for k in ('income', 'balance', 'cashflow') if os.path.exists(p(f'av__{k}.json'))}
    if av:
        F.from_alpha_vantage(fb, av, when.get('av__income.json') or as_of)
    if cfacts is not None:
        F.from_sec_companyfacts(fb, cfacts, when.get('sec__companyfacts.json') or as_of)
        _annual_inline(fb, raw_dir, manifest, when, as_of, notes, later)
    sf = p('fmp__shares-float__current.json')
    if os.path.exists(sf):
        rows = _load(sf)
        r = rows[0] if isinstance(rows, list) and rows and rows[0].get('outstandingShares') else None
        end = r and (_period_end_from_filing_url(r.get('source')) or (r.get('date') or '')[:10])
        seen = r and ((r.get('date') or '')[:10] or end)
        if r and end and not (later(end, 'vendor share count period') or later(seen, 'vendor share count')):
            F.add_source(fb, 'fmp:shares_float', 'fmp', 'shares-float', 'vendor_normalized', when.get('fmp__shares-float__current.json') or as_of,
                         sha=F.digest(rows), url=r.get('source'), note='vendor reading of the latest periodic filing cover; dated by the vendor')
            F.add_fact(fb, 'shares_outstanding_vendor', 'I@' + end, end, r['outstandingShares'], 'fmp:shares_float', 'normalized',
                       seen, unit='shares')
    web_est = p('web__consensus-estimates__annual.json')
    if os.path.exists(web_est):
        ce = _load(web_est)
        if not later(ce.get('retrieved_at'), 'consensus page'):
            F.from_consensus_table(fb, ce)
            notes.append(f"consensus from {ce.get('provider')} ({ce.get('url')}), read {str(ce.get('retrieved_at'))[:10]}: a public page, secondary")
    est = p('fmp__financial-estimates__annual.json')
    if os.path.exists(est):
        rows = _load(est)
        got = when.get('fmp__financial-estimates__annual.json')
        if isinstance(rows, list) and not later(got, 'consensus estimates retrieval'):
            F.from_fmp_estimates(fb, rows, got or as_of, as_of=(got or as_of)[:10])
    ca = p(f"ib__corporate-actions-{company['ticker'].upper()}__current.json")
    if os.path.exists(ca):
        n = F.from_ib_corporate_actions(fb, _load(ca), when.get(os.path.basename(ca)) or as_of)
        if n:
            notes.append(f'{n} split(s) from IBKR corporate actions (market feed)')
    snap = p(f"ib__price-snapshot-{company['ticker'].upper()}__current.json")
    if os.path.exists(snap):
        s = _load(snap)
        last = (s.get('last') or {})
        ts = last.get('ts')
        d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else (when.get(os.path.basename(snap)) or '')[:10]
        if last.get('price') and not later(d, 'price snapshot'):
            if not d:
                notes.append('price snapshot has no timestamp; dated as_of')
            F.add_source(fb, 'ib:snapshot', 'ib', 'price-snapshot', 'market_feed', when.get(os.path.basename(snap)) or as_of, sha=F.digest(s))
            F.set_price(fb, last['price'], d or as_of, 'ib:snapshot', kind='close' if last.get('is_close') else 'last',
                        currency=s.get('currency') or company.get('quote_currency') or company['currency'])
    _business(fb, raw_dir, manifest, as_of, notes)
    lp = p(f"listing__price-{company['ticker'].upper()}__current.json")
    if os.path.exists(lp):
        s = _load(lp)
        if s.get('market_cap') and s.get('price') and s.get('date') and not later(s['date'], 'listing market cap'):
            # the vendor's market cap with its own last sale: dataqa's cross-check of the share count (a missed split)
            fb['market']['listing_market_cap'] = {'value': s['market_cap'], 'price': s['price'], 'date': s['date'],
                                                  'provider': s.get('provider')}
    if not fb['market'].get('price') and os.path.exists(lp):
        # fallback when no broker quote was saved: the listing feed's last sale at retrieval (handoff.listing_price)
        s = _load(lp)
        if s.get('price') and s.get('date') and not later(s['date'], 'listing price'):
            F.add_source(fb, 'listing:price', s.get('provider') or 'listing', s.get('dataset') or 'last sale', 'market_feed',
                         s.get('retrieved_at_utc') or s['date'], sha=F.digest(s), url=s.get('url'), note=s.get('note'))
            F.set_price(fb, s['price'], s['date'], 'listing:price', kind='last',
                        currency=s.get('currency') or company.get('quote_currency') or company['currency'])
            notes.append(f"price from the listing feed ({s.get('provider')}), not a broker quote: {s.get('note') or 'last sale at retrieval'}")
    _convert_quote(fb, raw_dir, as_of, notes)
    _local_rate(fb, as_of, notes)
    return fb


def _local_rate(fb, as_of, notes):
    """A non-USD reporter's cash flows are discounted at its own currency's long-term government yield (fx.local_rate),
    recorded as fb['market']['risk_free']; without one the forecast warns that the USD default is used."""
    from . import fx
    ccy = fb['company'].get('currency')
    if not ccy or ccy == 'USD':
        return
    r = fx.local_rate(ccy, as_of, FX_DIR)
    if not r:
        notes.append(f'no {ccy} long-term government yield saved ({fx.LOCAL_RF.get(ccy) or "no series known"}): the forecast '
                     'falls back to the USD risk-free default')
        return
    sid = 'fred:' + r['series']
    F.add_source(fb, sid, 'fred', r['series'], 'market_feed', r['date'], url=r['source'],
                 note=f'{ccy} long-term government bond yield, monthly average')
    fb['market']['risk_free'] = {'value': r['value'], 'as_of': r['date'], 'source_id': sid, 'currency': ccy}
    notes.append(f"{ccy} cash flows discounted from the {ccy} long-term government yield {r['value']:.2%} ({r['series']}, {r['date']})")


def _annual_inline(fb, raw_dir, manifest, when, as_of, notes, later):
    """companyfacts can lag a filing (TSMC's 2025 20-F, filed 2026-04-16, was absent in October 2026): when the saved annual
    report covers a later fiscal year than companyfacts, that year's facts are read from the document's own inline XBRL."""
    from . import business
    calls = manifest.get('calls', []) if isinstance(manifest, dict) else manifest
    ann = next((c for c in calls if isinstance(c, dict) and c.get('file') == 'sec__annual_report.htm' and c.get('status') == 200
                and c.get('filed') and c.get('report_date')), None)
    path = os.path.join(raw_dir, 'sec__annual_report.htm')
    if not ann or not os.path.exists(path) or later(ann['filed'], 'annual report'):
        return
    have = [f['period_end'] for f in fb['facts'] if f['metric'] == 'revenue' and f['source_id'] == 'sec:companyfacts'
            and f['period'].startswith('FY')]
    last = max(have, default=None)
    if last is not None and (F._d(ann['report_date']) - F._d(last)).days <= F.SAME_PERIOD_DAYS:
        return
    with open(path, encoding='utf-8', errors='replace') as f:
        html = f.read()
    payload = F.inline_payload(business.parse_ixbrl(html), business.ix_units(html), ann['filed'], ann.get('accession'),
                               ann.get('form') or '10-K')
    n = F.from_annual_inline(fb, payload, last, when.get('sec__annual_report.htm') or as_of, ann.get('url'))
    if n:
        notes.append(f"companyfacts has no {ann.get('form') or 'annual report'} for {ann['report_date']} (filed {ann['filed']}): {n} facts "
                     "read from the saved document's own inline XBRL")


from .fx import DEFAULT_DIR as FX_DIR  # noqa: E402  (saved FRED series)


def _convert_quote(fb, raw_dir, as_of, notes):
    """A quote in another currency than the filings (a USD depositary share of a TWD, DKK, EUR or CNY reporter) becomes the
    price of one ordinary share in the reporting currency: quote x (reporting currency per USD on the quote date, FRED H.10)
    / ordinary shares per depositary share (read from the annual report: 'each ADS represents five common shares'). Without
    a dated rate or a stated ratio the quote is left as it is and the forecast reports that no quote in the company currency
    exists (never a guessed rate or ratio)."""
    from . import business, fx
    price, ccy = (fb.get('market') or {}).get('price'), fb['company'].get('currency')
    ads = bool(fb['company'].get('depositary_shares'))
    if not price or (price.get('currency', ccy) == ccy and not ads):
        return
    pccy = price.get('currency', ccy)
    rate = fx.rate(ccy, price['as_of'], FX_DIR) if pccy == 'USD' else None
    doc = os.path.join(raw_dir, 'sec__annual_report.htm')
    ratio = None
    if os.path.exists(doc):
        with open(doc, encoding='utf-8', errors='replace') as f:
            ratio = business.ads_ratio(f.read())
    if ratio and ratio.get('direct_listing') and ads:
        ratio = None                     # a depositary share is never one-for-one by default
    if not rate or not ratio or ratio.get('ambiguous'):
        notes.append(f"quote in {pccy} not converted to {ccy} per ordinary share: " + ('no dated exchange rate' if not rate else
                     'the depositary ratio is not stated in the saved annual report' if not ratio else
                     f"the annual report states several depositary ratios {ratio['candidates']}"))
        if ads and pccy == ccy:
            # the price stays per depositary share; dataqa sees the unresolved security class and the valuation is withheld
            fb['market']['price']['unresolved'] = 'depositary share without a stated ratio'
        return
    value = price['value'] * rate['value'] / ratio['ratio']
    if rate['series']:
        F.add_source(fb, 'fx:fred', 'fred', rate['series'], 'market_feed', rate['date'], url=rate['source'], note=rate.get('quote'))
    conv = {'quote': price['value'], 'quote_currency': pccy, 'quote_source_id': price['source_id'],
            'fx': rate, 'shares_per_depositary_share': ratio['ratio'], 'ratio_evidence': ratio['evidence'],
            'direct_listing': bool(ratio.get('direct_listing')),
            'formula': 'quote x reporting currency per USD / ordinary shares per depositary share'}
    F.set_price(fb, value, price['as_of'], price['source_id'], kind=price.get('kind', 'last'), currency=ccy)
    fb['market']['price']['conversion'] = conv
    notes.append(f"quote {price['value']} {pccy} per {'ordinary share (direct listing)' if ratio.get('direct_listing') else 'depositary share'}"
                 f" = {value:,.2f} {ccy} per ordinary share ({rate['value']:.4f} {ccy}/USD on {rate['date']}, {ratio['ratio']:g} shares per "
                 'listed share)')


def _business(fb, raw_dir, manifest, as_of, notes):
    """Annual report -> fb['business_map'] (segments, products, geography, concentration, RPO); annual and quarterly
    report covers -> point-in-time share counts per class. A document filed after as_of is skipped (no look-ahead)."""
    from . import business
    calls = {c.get('file'): c for c in manifest.get('calls', []) if isinstance(c, dict)}
    convs, latest_bs = {}, None
    for name in ('sec__annual_report.htm', 'sec__latest_quarterly.htm', 'sec__proxy.htm'):
        path, rec = os.path.join(raw_dir, name), calls.get(name) or {}
        if not os.path.exists(path):
            continue
        filed = rec.get('filed')
        if not filed or filed > as_of:
            notes.append(f"{name}: filing date {filed or 'unknown'} not on or before as_of {as_of}; not used")
            continue
        with open(path, encoding='utf-8', errors='replace') as f:
            html = f.read()
        src = {'url': rec.get('url'), 'accession': rec.get('accession'), 'form': rec.get('form'), 'filed': filed,
               'period': rec.get('report_date')}
        sid = 'sec:' + name.split('__')[1].split('.')[0]
        F.add_source(fb, sid, 'sec', rec.get('form') or 'filing', 'filing_derived', rec.get('retrieved_at_utc') or as_of,
                     sha=rec.get('sha256'), url=rec.get('url'), note='inline XBRL of the primary document')
        if name == 'sec__proxy.htm':
            pay = business.pay_measures(html)
            if pay:
                fb['management'] = {**pay, 'source_id': sid, 'source': src}
            continue
        bm = business.business_map(html, src)
        avail = (date.fromisoformat(filed) + timedelta(days=1)).isoformat()
        for m, x in business.convertible_terms(html).items():
            cur = convs.setdefault(m, {'member': m, 'name': x['name'], 'amounts': {}, 'source_ids': set()})
            cur['source_ids'].add(sid)
            for k in ('conversion_price', 'price_basis', 'maturity', 'maturity_basis'):
                if x.get(k) and not cur.get(k):
                    cur[k] = x[k]
            for k, by in x['amounts'].items():
                cur['amounts'].setdefault(k, {}).update({d: v for d, v in by.items() if d <= as_of})
        for pf in business.pro_forma_revenue(html):
            if pf['end'] <= as_of and not any(x['metric'] == 'revenue_pro_forma' and x.get('period_start') == pf['start']
                                              and x['period_end'] == pf['end'] for x in fb['facts']):
                F.add_fact(fb, 'revenue_pro_forma', f"PF@{pf['start']}..{pf['end']}", pf['end'], pf['value'], sid, 'as_reported', avail,
                           period_start=pf['start'], concept=business.PRO_FORMA_REVENUE + (f" [{pf['acquisition']}]" if pf['acquisition'] else ''))
        cs = bm.get('cover_shares')
        if cs and cs.get('as_of') and cs['as_of'] <= as_of and not any(
                x['metric'] == 'shares_outstanding_cover' and x['period_end'] == cs['as_of'] for x in fb['facts']):
            F.add_fact(fb, 'shares_outstanding_cover', 'I@' + cs['as_of'], cs['as_of'], cs['total'], sid, 'as_reported', avail,
                       unit='shares', concept='dei:EntityCommonStockSharesOutstanding (sum of classes on the cover)'
                       if len(cs['by_class']) > 1 else 'dei:EntityCommonStockSharesOutstanding')
        if name == 'sec__annual_report.htm':
            bm['source_id'] = sid
            fb['business_map'] = bm
        if rec.get('report_date') and rec['report_date'] <= as_of:
            latest_bs = max(latest_bs or rec['report_date'], rec['report_date'])
    # convertible notes outstanding at the latest balance sheet: an instrument with no amount within 100 days of it was repaid
    out = []
    for m, x in convs.items():
        pts = sorted((d, v, k) for k, by in x['amounts'].items() for d, v in by.items())
        if not pts or not latest_bs or (date.fromisoformat(latest_bs) - date.fromisoformat(pts[-1][0])).days > 100:
            continue
        last = pts[-1][0]
        principal = (x['amounts'].get('face') or {}).get(last) or (x['amounts'].get('carrying') or {}).get(last)
        out.append({'id': m, 'name': x['name'], 'principal': principal, 'amount_as_of': last, 'conversion_price': x.get('conversion_price'),
                    'price_basis': x.get('price_basis'), 'maturity': x.get('maturity'), 'maturity_basis': x.get('maturity_basis'),
                    'source_ids': sorted(x['source_ids'])})
    if out:
        fb['convertible_terms'] = {'as_of': latest_bs, 'instruments': out,
                                   'basis': 'inline XBRL of the saved annual and quarterly reports (DebtInstrumentAxis)'}


def _period_end_from_filing_url(url):
    """'.../adbe-20260828.htm' -> '2026-08-28' (the filing's period date); None when absent."""
    if not url:
        return None
    base = url.rsplit('/', 1)[-1]
    digits = ''.join(ch for ch in base if ch.isdigit())
    if len(digits) >= 8:
        d = digits[-8:]
        try:
            return date(int(d[:4]), int(d[4:6]), int(d[6:])).isoformat()
        except ValueError:
            return None
    return None


def valuate(fb, qa, fc, inputs=None):
    """Execute the forecast with the one canonical valuation route and its diagnostics."""
    from .drivers import build_case
    from .valuation import value_company, sensitivity
    from . import valuation_tools as vt
    if not fc or not fc.get('spec'):
        return {'status': 'not_computed', 'reason': (fc or {}).get('reason') or 'no forecast spec for this valuation method'}
    # an operating forecast is a driver spec; the residual-income forecast hands over a valuation case directly
    case = fc['spec'] if fc['spec'].get('valuation_version') == 1 else build_case(fc['spec'])
    result = value_company(case)
    diag, errors = {}, []
    for name, fn in (('discount_rate_band', lambda: vt.discount_rate_band(case)),
                     ('implied_cost_of_equity', lambda: {s['name']: vt.implied_cost_of_equity(case, s['name']) for s in case['scenarios']}),
                     ('reverse_price', lambda: vt.reverse_price(case)),
                     ('stress', lambda: vt.stress_test(case)),
                     ('sensitivity', lambda: sensitivity(case))):
        try:
            diag[name] = fn()
        except (ValueError, ZeroDivisionError, KeyError) as e:
            errors.append({'diagnostic': name, 'error': str(e)})
    wmbt = None
    try:
        from .forecast import what_must_be_true
        wmbt = what_must_be_true(fc['spec'], fb['market']['price']['value'])
    except (ValueError, ZeroDivisionError, KeyError, ImportError) as e:
        errors.append({'diagnostic': 'what_must_be_true', 'error': str(e)})
    cap = None
    cs = fb.get('capital_structure')
    if cs and fb.get('market', {}).get('price'):
        from .capital import market_view
        mv = market_view(cs, fb['market']['price']['value'])
        cap = {'market_view': mv, 'opening_shares': case['opening']['shares'],
               'difference': mv['fully_diluted_shares'] / case['opening']['shares'] - 1}
    return {'status': result['status'], 'case': case, 'result': result, 'diagnostics': diag,
            'what_must_be_true': wmbt, 'capital_check': cap, 'errors': errors}


def run(fb, inputs=None):
    """Run every stage on one FactBook. inputs: {'card': ..., 'forecast': ..., 'reviewer': ..., 'decision': ..., 'analyst': name(s)?,
    'analyst_sheet': the analyst's evidenced readings of the six questions (analyst_sheet.py)}.

    The reviewer signs valuation_sha256 (the executed valuation) and inputs_sha256 = review.inputs_digest(val, inputs), which
    also covers inputs.decision/card/forecast as supplied (rv['binding'] shows the value to sign): an analyst override added
    after the review voids it. inputs.analyst and every inputs.forecast.reviews signer can never be the independent reviewer."""
    from . import dataqa, analysis, model_card, forecast, risk, review, decision
    inputs = deepcopy(inputs or {})
    signed = deepcopy(inputs)                     # the inputs as supplied, before any stage reads them
    cf = {'casefile_version': 1, 'engine_version': __version__, 'company': fb['company'], 'as_of': fb['as_of'],
          'factbook_sha256': F.fingerprint(fb), 'inputs_sha256': F.digest(inputs), 'errors': [], 'stages': {},
          'collection_notes': list(fb.get('collection_notes') or [])}

    def stage(name, fn):
        try:
            out = fn()
            cf['stages'][name] = 'ok'
            return out
        except (ValueError, KeyError, ZeroDivisionError, TypeError) as e:
            cf['stages'][name] = 'failed'
            cf['errors'].append({'stage': name, 'error': f'{type(e).__name__}: {e}'})
            return None

    qa = stage('dataqa', lambda: dataqa.validate(fb, fb.get('capital_structure')))
    an = stage('analysis', lambda: analysis.analyze(fb, qa)) if qa else None
    card = stage('model_card', lambda: model_card.build(fb, an, qa, inputs.get('card'))) if an else None
    qa = route_qa(qa, card)
    fc = stage('forecast', lambda: forecast.build(fb, an, card, qa, inputs.get('forecast'))) if card else None
    val = stage('valuation', lambda: valuate(fb, qa, fc, inputs)) if fc else {'status': 'not_computed', 'reason': 'forecast unavailable'}
    if val is None:
        val = {'status': 'not_computed', 'reason': 'valuation failed: ' + cf['errors'][-1]['error']}
    rk = stage('risk', lambda: risk.assess(fb, qa, an, card, fc, val)) if card else None
    rv = stage('review', lambda: review.audit(fb, qa, an, card, fc, val, rk, inputs.get('reviewer'), inputs_sha256=review.inputs_digest(val, signed),
                                              authors=review.input_authors(signed))) if rk else None
    parts = {'fb': fb, 'qa': qa, 'an': an, 'card': card, 'fc': fc, 'val': val, 'rk': rk, 'rv': rv}
    dc = stage('decision', lambda: decision.decide(parts, inputs.get('decision')))
    cf.update({'qa': qa, 'an': an, 'card': card, 'fc': fc, 'val': val, 'rk': rk, 'rv': rv, 'dc': dc})
    from . import business
    base = [p.get('growth') for p in ((fc or {}).get('paths') or {}).get('base') or [] if isinstance(p, dict) and p.get('growth') is not None]
    cf['business'] = stage('business', lambda: business.summary(fb.get('business_map'), base[:5]))
    cf['segment_view'] = stage('segment_view', lambda: segment_view(fb, an, card, qa, fc, val, cf['business'], inputs))
    cf['management'] = fb.get('management')           # how management is paid (proxy statement), evidence for the analyst
    from . import analyst_sheet
    cond = stage('conditional_values', lambda: analyst_sheet.conditional_values(fb, an, card, qa, inputs.get('forecast'))) if fc else None
    cf['analyst_sheet'] = stage('analyst_sheet', lambda: analyst_sheet.build(fb, cf, inputs, cond))
    json.dumps(cf, allow_nan=False, default=str)
    cf['casefile_sha256'] = F.digest(json.loads(json.dumps({k: v for k, v in cf.items() if k != 'casefile_sha256'}, default=str)))
    return cf


def segment_view(fb, an, card, qa, fc, val, biz, inputs):
    """What the company is worth if its segments grow at their own pace (business.segment_path) instead of the consolidated
    base path, when the two differ by more than 2 points a year: the base scenario is rebuilt with the segment-built growth for
    the five full years after the current fiscal year (then fading to the base path's last rate) and valued again. A second
    view for the analyst, never a silent replacement of the base case. None when not applicable."""
    from . import forecast
    cross, sp = (biz or {}).get('cross_check'), (biz or {}).get('segment_path')
    if not cross or not sp or abs(cross['difference']) <= .02 or not fc or fc.get('status') != 'built' or (fc.get('spec') or {}).get('valuation_version'):
        return None
    if (val or {}).get('status') in (None, 'not_computed'):
        return None
    sc = next(x for x in fc['spec']['scenarios'] if x['name'] == 'base')
    g = list(sc['growth'])
    seg = sp['growth']
    alt = g[:1] + [seg[i - 1] if i <= len(seg) else None for i in range(1, len(g))]
    last = next((i for i in range(len(alt) - 1, -1, -1) if alt[i] is not None), 0)
    rest = len(alt) - last - 1
    if rest:
        from .business import _fade
        alt[last + 1:] = _fade(alt[last], g[-1], 2.5, rest)
    src = (fb.get('business_map') or {}).get('source_id')
    if not src:
        return None
    fin = deepcopy((inputs or {}).get('forecast') or {})
    fin['overrides'] = list(fin.get('overrides') or []) + [{'path': 'growth', 'scenario': 'base', 'value': [float(x) for x in alt],
                                                           'reason': 'segment-built growth path (business.segment_path): second view',
                                                           'evidence_ids': [src]}]
    fc2 = forecast.build(fb, an, card, qa, fin)
    if fc2.get('status') != 'built':
        return {'status': 'not_computed', 'reason': fc2.get('reason')}
    v2 = valuate(fb, qa, fc2, inputs)
    today = ((v2.get('result') or {}).get('annual_values') or [{}])[0]
    now = ((val.get('result') or {}).get('annual_values') or [{}])[0]
    b1, b2 = now.get('base'), today.get('base')
    return {'status': 'computed', 'reading': cross['reading'], 'basis': sp['basis'], 'growth_consolidated': g[1:6], 'growth_segment_path': alt[1:6],
            'base_value_consolidated': b1, 'base_value_segment_path': b2,
            'difference': (b2 / b1 - 1) if b1 and b2 else None, 'quote': now.get('current_quote'),
            'meaning': 'the base case valued again with each segment at its own pace; which view holds is a research question '
                       '(mix shift, acquisitions inside a segment, guidance by segment)'}


def route_qa(qa, card):
    """dataqa as it applies to the card's valuation route. A residual-income valuation (banks, insurers) does not use debt,
    operating cash flow, capex, revenue or cash fields: their blocks become limits, listed in 'not_used_by_route' (Travelers'
    debt tags and UnitedHealth's cash-flow TTM had blocked valuations that never read them). Other routes are unchanged."""
    from .forecast_financials import RI_IRRELEVANT_FIELDS
    if not qa or not card or (card.get('valuation') or {}).get('method') != 'residual_income':
        return qa
    moved = [f for f in qa.get('blocking') or [] if f in RI_IRRELEVANT_FIELDS]
    if not moved:
        return qa
    out = dict(qa, blocking=[f for f in qa['blocking'] if f not in moved], limited=list(qa.get('limited') or []) + moved,
               not_used_by_route={'route': 'residual_income', 'fields': moved, 'status_before': qa.get('status')})
    if not out['blocking'] and out.get('status') == 'blocked':
        out['status'] = 'limited'
    return out


def _risk_adjustment(cf):
    """Net business-risk adjustment of the cost of equity over risk-free + ERP (the add-on rows, market-neutral row included);
    None without a forecast cost of equity."""
    comp = (((cf.get('fc') or {}).get('cost_of_equity') or {}).get('components') or {})
    rows = comp.get('adjustments')
    return sum(a.get('value') or 0 for a in rows) if isinstance(rows, list) else None


def summary(cf):
    """Compact record used by the portfolio layer, monitoring and discovery comparisons. margin (marginal|clear|None), hurdle
    and value_at_required_return come from the decision contract (decision_version 3); None when absent."""
    dc, val = cf.get('dc') or {}, cf.get('val') or {}
    er = dc.get('expected_return') or {}
    today = ((val.get('result') or {}).get('annual_values') or [{}])[0]
    v = dc.get('verdict') or {}
    return {'symbol': cf['company']['ticker'], 'as_of': cf['as_of'], 'verdict': v.get('code'),
            'verdict_status': v.get('status'), 'price_claim_withheld': bool(v.get('price_claim_withheld', True)),
            'confidence': (dc.get('confidence') or {}).get('level'),
            'quality': ((dc.get('assessments') or {}).get('business_quality') or {}).get('level'),
            'risk_level': ((cf.get('rk') or {}).get('error_size') or {}).get('level'),
            'price': today.get('current_quote'), 'bear_value': today.get('bear'), 'base_value': today.get('base'),
            'bull_value': today.get('bull'), 'base_irr': er.get('base_irr'), 'bear_irr': er.get('bear_irr'),
            'cost_of_equity': (dc.get('valuation_view') or {}).get('model_cost_of_equity'),
            'market_consistent_value': ((dc.get('valuation_view') or {}).get('market_consistent') or {}).get('value'),
            'risk_adjustment': _risk_adjustment(cf),
            'decision_version': dc.get('decision_version'), 'margin': v.get('margin'), 'hurdle': er.get('hurdle'),
            'value_at_required_return': v.get('value_at_required_return', er.get('value_at_required_return')),
            'review_status': (cf.get('rv') or {}).get('status'), 'casefile_sha256': cf.get('casefile_sha256')}
