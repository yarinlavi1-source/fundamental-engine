"""Quality + trend screen: decides where research time goes. Pure logic (no network); scripts/quality_momentum_screen.py
fetches the data. It is not a valuation and not a buy signal, and it is not backtested.

Quality gates come from SEC XBRL companyfacts (trailing twelve months, TTM, built from quarters; a fourth quarter is the
full year minus nine months). Trend gates come from daily adjusted closes. Banks and insurers are read on their own
measures; filers with annual XBRL only (20-F, 40-F) are read on annual gates.
"""
import re
from datetime import date, timedelta

GATES = {'rev_growth_ttm': 0.15, 'rev_growth_q': 0.15, 'margin_drop_pts': 1.0, 'share_growth': 0.03,
         'max_below_high': 0.25, 'roe': 0.15, 'rev_cagr_3y': 0.10}
HISTORY_DAYS = 1650                             # facts kept: four completed fiscal years back from the as-of date
MARGIN_JUMP_PTS = 10.0                          # a larger one-year margin change is flagged as a likely one-off (no gate)
EPS = 1e-9
RANK_KEYS = ('rev_growth_ttm', 'margin_change_pts', 'fcf_margin', 'rel_strength')

# metric -> concept candidates in priority order (revenue: the larger value of one period wins, a total is never below a part)
TAGS = {
    'us-gaap': {
        'revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
                    'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet', 'RevenuesNetOfInterestExpense',
                    'RegulatedAndUnregulatedOperatingRevenue'],
        'operating_income': ['OperatingIncomeLoss'],
        'pretax_income': ['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
                          'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
        'net_income': ['NetIncomeLoss', 'ProfitLoss', 'NetIncomeLossAvailableToCommonStockholdersBasic'],
        'cfo': ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'],
        'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'],
        'diluted_shares': ['WeightedAverageNumberOfDilutedSharesOutstanding', 'WeightedAverageNumberOfShareOutstandingBasicAndDiluted',
                           'WeightedAverageNumberOfSharesOutstandingBasic'],
        'equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    },
    'ifrs-full': {
        'revenue': ['Revenue', 'RevenueFromContractsWithCustomers'],
        'operating_income': ['ProfitLossFromOperatingActivities'],
        'pretax_income': ['ProfitLossBeforeTax'],
        'net_income': ['ProfitLossAttributableToOwnersOfParent', 'ProfitLoss'],
        'cfo': ['CashFlowsFromUsedInOperatingActivities'],
        'capex': ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities',
                  'PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets'],
        'diluted_shares': ['AdjustedWeightedAverageShares', 'WeightedAverageShares'],
        'equity': ['EquityAttributableToOwnersOfParent', 'Equity'],
    },
}
# capex: the largest of a total line, the sum of the component lines (an oil company reports wells and other equipment
# separately) and a net line (purchases less disposals: ship owners)
CAPEX_PARTS = {'us-gaap': ['PaymentsToAcquireOilAndGasPropertyAndEquipment', 'PaymentsToAcquireOilAndGasProperty',
                           'PaymentsToAcquireOtherPropertyPlantAndEquipment', 'PaymentsToAcquireMiningAssets',
                           'PaymentsToAcquireMachineryAndEquipment', 'PaymentsForCapitalImprovements'],
               'ifrs-full': []}
CAPEX_NET = {'us-gaap': ['PaymentsForProceedsFromProductiveAssets'], 'ifrs-full': []}
PROXY_FORM = re.compile(r'14[AC]')              # proxy statements repeat a few facts, often in another scale
STALE_DAYS = 153                                # a latest period older than about five months is flagged
INSTANT = ('equity',)
SHARE_METRICS = ('diluted_shares',)
ANNUAL_FORMS = ('20-F', '40-F', '20-F/A', '40-F/A')
# Commodity producers and processors by SIC code: revenue follows the price of the product, so one strong year passes a
# growth gate. They are read on the same gates but shown in their own table, never in the passers list.
COMMODITY_SIC = {
    1000: 'metal mining', 1040: 'gold and silver ores', 1090: 'miscellaneous metal ores',
    1220: 'coal mining', 1221: 'coal mining',
    1311: 'crude petroleum and natural gas', 1381: 'drilling oil and gas wells', 1382: 'oil and gas field exploration services',
    1389: 'oil and gas field services',
    2870: 'agricultural chemicals and fertilizers', 2873: 'nitrogenous fertilizers', 2874: 'phosphatic fertilizers',
    2875: 'fertilizer mixing',
    2911: 'petroleum refining', 2990: 'petroleum and coal products',
    3310: 'steel works and rolling mills', 3312: 'steel works and rolling mills', 3317: 'steel pipe and tubes',
    3330: 'primary smelting of nonferrous metals', 3334: 'primary aluminum', 3341: 'secondary smelting of nonferrous metals',
    4400: 'water transportation', 4412: 'deep sea freight transportation',
    4610: 'pipelines (no natural gas)', 4922: 'natural gas transmission', 4923: 'natural gas transmission and distribution',
    4924: 'natural gas distribution',
    5051: 'metals service centers', 5171: 'petroleum bulk stations', 5172: 'petroleum products wholesale',
    6792: 'oil royalty traders', 6795: 'mineral royalty traders',
}
_PASSENGER_LINE = re.compile(r'cruise|carnival|viking', re.I)       # SIC 4400 also holds passenger lines: not freight


def _d(s):
    return date.fromisoformat(s)


def _days(a, b):
    return (_d(b) - _d(a)).days


# ---------------------------------------------------------------- universe

_DROP_NAME = re.compile(r'\b(preferred|preference|warrants?|rights?|notes?|debentures?|etf|etn|fund|subordinated)\b|%', re.I)


def norm_symbol(s):
    return (s or '').strip().upper().replace('/', '-').replace('.', '-')


def _num(x):
    try:
        return float(str(x).replace('$', '').replace(',', '') or 0)
    except ValueError:
        return 0.


def is_common_listing(symbol, name):
    """False for preferreds, warrants, units, rights, notes and funds (by the listing symbol and name)."""
    n = name or ''
    low = n.lower()
    if '^' in (symbol or ''):
        return False
    if 'american depositary' in low:        # an ADR is the company's listed share, whatever class it represents
        return True
    if _DROP_NAME.search(n):
        return False
    if 'depositary share' in low and 'american depositary' not in low:      # preferred depositary shares
        return False
    if re.search(r'\bunits?\b', low) and 'common unit' not in low and 'limited partner' not in low:
        return False
    return True


def build_universe(listing_rows, cik_by_symbol, min_cap=5e9, keep=()):
    """Listing rows (Nasdaq feed) joined to the SEC ticker map: market cap >= min_cap, common shares only, one share class
    per company (the largest dollar volume of the feed's day). keep: symbols evaluated whatever their size (thesis book).
    Returns (rows, counts, no_cik)."""
    keep = {norm_symbol(k) for k in keep}
    counts = {'listed': len(listing_rows), 'cap_ok': 0, 'common': 0, 'with_cik': 0, 'companies': 0}
    best, no_cik = {}, []
    for r in listing_rows:
        sym, cap = norm_symbol(r.get('symbol')), _num(r.get('marketCap'))
        forced = sym in keep
        if cap < min_cap and not forced:
            continue
        counts['cap_ok'] += cap >= min_cap
        if not is_common_listing(r.get('symbol'), r.get('name')):
            continue
        counts['common'] += cap >= min_cap
        cik = cik_by_symbol.get(sym)
        if cik is None:
            no_cik.append({'symbol': sym, 'name': r.get('name'), 'market_cap': cap})
            continue
        counts['with_cik'] += cap >= min_cap
        price = _num(r.get('lastsale'))
        row = {'symbol': sym, 'cik': int(cik), 'name': (r.get('name') or '').strip(), 'market_cap': cap, 'price': price,
               'sector': r.get('sector') or '', 'industry': r.get('industry') or '', 'country': r.get('country') or '',
               'dollar_volume': price * _num(r.get('volume')), 'in_universe': cap >= min_cap}
        cur = best.get(row['cik'])
        if cur is None or (row['in_universe'], row['dollar_volume']) > (cur['in_universe'], cur['dollar_volume']):
            if cur is not None:
                row['other_classes'] = cur.get('other_classes', []) + [cur['symbol']]
            best[row['cik']] = row
        else:
            cur.setdefault('other_classes', []).append(sym)
    out = sorted(best.values(), key=lambda x: x['symbol'])
    counts['companies'] = sum(1 for r in out if r['in_universe'])
    return out, counts, no_cik


def is_financial(sic, sector):
    """Banks, insurers, brokers and lenders: SIC 6000-6499, or the listing sector when the SIC is unknown or agrees."""
    try:
        if 6000 <= int(sic) <= 6499:
            return True
    except (TypeError, ValueError):
        pass
    return (sector or '').strip().lower() == 'finance'


def is_commodity(sic, name=''):
    """A commodity producer or processor by SIC code (COMMODITY_SIC); passenger shipping lines are not."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return False
    if code not in COMMODITY_SIC:
        return False
    return not (code == 4400 and _PASSENGER_LINE.search(name or ''))


# ---------------------------------------------------------------- companyfacts -> series

def extract_series(payload, since='2022-01-01', as_of='9999-12-31'):
    """The few series the screen needs from one companyfacts payload (the raw file is not kept).
    Per (start, end) the latest filed value stands (later filings restate splits and reclassifications); facts filed
    after as_of and facts from proxy statements are not read."""
    facts = payload.get('facts') or {}

    def latest_end(tax):
        ends = [f.get('end', '') for tag in TAGS[tax]['revenue'] for fs in ((facts.get(tax) or {}).get(tag) or {}).get('units', {}).values()
                for f in fs if f.get('start')]
        return max(ends) if ends else ''
    tax = max(TAGS, key=lambda t: (latest_end(t), t == 'us-gaap'))
    if not latest_end(tax):
        return {'cik': payload.get('cik'), 'name': payload.get('entityName'), 'taxonomy': None, 'currency': None,
                'durations': {}, 'instants': {}, 'forms': {}}
    node = facts.get(tax) or {}
    by_unit = {}
    for tag in TAGS[tax]['revenue']:
        for unit, fs in (node.get(tag) or {}).get('units', {}).items():
            ends = [f['end'] for f in fs if f.get('start')]
            if ends:
                by_unit[unit] = max(by_unit.get(unit, ''), max(ends))
    # a USD figure beside another currency with the same period is a convenience translation
    currency = max(by_unit, key=lambda u: (by_unit[u], u != 'USD'))
    durations, instants, forms, by_tag = {}, {}, {}, {}

    def latest(tag, unit, instant=False):
        per = {}
        for f in ((node.get(tag) or {}).get('units', {}).get(unit) or []):
            if f.get('end', '') < since or f.get('val') is None or (f.get('filed') or '') > as_of:
                continue
            if PROXY_FORM.search(f.get('form') or '') or instant != (not f.get('start')):
                continue
            key = (f.get('start'), f['end'])
            if key not in per or (f.get('filed') or '') >= (per[key].get('filed') or ''):
                per[key] = f
            if f.get('form') and (f.get('filed') or '') > forms.get(f['form'], ''):
                forms[f['form']] = f.get('filed') or ''
        return per

    for metric, tags in TAGS[tax].items():
        unit = 'shares' if metric in SHARE_METRICS else currency
        merged = {}
        if metric == 'capex':
            total, parts, net = ([latest(t, unit) for t in ts] for ts in (tags, CAPEX_PARTS[tax], CAPEX_NET[tax]))
            for key in {k for per in total + parts + net for k in per}:
                tot = [(float(per[key]['val']), t, per[key]) for t, per in zip(tags, total) if key in per]
                prt = [(float(per[key]['val']), t, per[key]) for t, per in zip(CAPEX_PARTS[tax], parts) if key in per]
                alt = [(float(per[key]['val']), t, per[key]) for t, per in zip(CAPEX_NET[tax], net) if key in per]
                cands = tot + alt
                if prt:
                    cands.append((sum(x[0] for x in prt), '+'.join(x[1] for x in prt), max(prt, key=lambda x: x[2].get('filed') or '')[2]))
                best = max(cands, key=lambda x: x[0])
                merged[key] = {'start': key[0], 'end': key[1], 'val': best[0], 'tag': best[1], 'filed': best[2].get('filed'),
                               'form': best[2].get('form')}
            tags = ()
        for prio, tag in enumerate(tags):
            per = latest(tag, unit, metric in INSTANT)
            for key, f in per.items():
                cur = merged.get(key)
                if cur is None or (metric == 'revenue' and float(f['val']) > cur['val']):
                    merged[key] = {'start': key[0], 'end': key[1], 'val': float(f['val']), 'tag': tag, 'filed': f.get('filed'),
                                   'form': f.get('form')}
            if metric == 'revenue' and per:
                by_tag[tag] = sorted(({'start': k[0], 'end': k[1], 'val': float(f['val']), 'tag': tag, 'filed': f.get('filed'),
                                       'form': f.get('form')} for k, f in per.items()), key=lambda x: (x['end'], x['start']))
        rows = sorted(merged.values(), key=lambda x: (x['end'], x['start'] or ''))
        if metric in INSTANT:
            instants[metric] = [{k: v for k, v in r.items() if k != 'start'} for r in rows]
        else:
            durations[metric] = rows
    if len(by_tag) > 1:                         # several revenue concepts: fundamentals() picks one that covers every period
        durations['revenue_by_tag'] = by_tag
    return {'cik': payload.get('cik'), 'name': payload.get('entityName'), 'taxonomy': tax, 'currency': currency,
            'durations': durations, 'instants': instants, 'forms': forms}


def revenue_tags(series, since):
    """Revenue concepts behind the merged revenue facts that end after `since`."""
    return sorted({f['tag'] for f in (series.get('durations') or {}).get('revenue', []) if f['end'] > since and f.get('tag')})


def pick_revenue(dur, as_of):
    """(facts, mixed). One revenue concept is used for every period when it alone gives eight quarters up to the latest
    one (the largest such total); otherwise the per-period merge, with mixed=True when it draws on several concepts
    (a total in one period beside a component in another distorts growth)."""
    merged = [f for f in dur.get('revenue', []) if f['end'] <= as_of]
    mq = quarterly(merged)
    best = None
    for tag, fs in (dur.get('revenue_by_tag') or {}).items():
        fs = [f for f in fs if f['end'] <= as_of]
        q = quarterly(fs)
        if not q or not mq or max(q) != max(mq):
            continue
        t1, t0 = ttm(q, max(q)), ttm(q, max(q), 4)
        if t1 is not None and t0 is not None and (best is None or t1 > best[0]):
            best = (t1, fs)
    if best:
        return best[1], False
    if not merged:
        return merged, False
    cut = (_d(max(f['end'] for f in merged)) - timedelta(days=800)).isoformat()
    return merged, len({f.get('tag') for f in merged if f['end'] > cut}) > 1


def quarterly(facts, derive=True):
    """{end: 3-month value}. Reported 3-month facts first; otherwise a longer period minus the shorter one that starts on
    the same day and ends one quarter earlier (the fourth quarter: full year minus nine months; cash-flow statements report
    year-to-date only). derive=False for weighted share counts, which cannot be subtracted."""
    by = {(f['start'], f['end']): f['val'] for f in facts if f.get('start')}
    out = {e: v for (s, e), v in by.items() if 80 <= _days(s, e) <= 100}
    if not derive:                              # a weighted share count is often the same for the quarter and the year to date
        return out
    for (s, e), v in by.items():                # a 3-month flow that repeats a longer period's value is a tagging error
        if _days(s, e) > 100 and out.get(e) == v and v != 0:
            del out[e]
    grew = True
    while grew:                                 # a report missing from the data: year to date minus its last quarter
        grew = False
        for (s, e), v in list(by.items()):
            if _days(s, e) <= 100 or e not in out:
                continue
            for (s2, e2), v2 in list(by.items()):
                if e2 == e and 80 <= _days(s2, e2) <= 100 and out[e] == v2:
                    key = (s, (_d(s2) - timedelta(days=1)).isoformat())
                    if key not in by and _days(*key) >= 80:
                        by[key], grew = v - v2, True
                        if _days(*key) <= 100:
                            out.setdefault(key[1], v - v2)
    for (s, e), v in sorted(by.items(), key=lambda x: _days(*x[0])):
        if e in out or _days(s, e) <= 100:
            continue
        for (s2, e2), v2 in by.items():
            if abs(_days(s2, s)) <= 3 and 80 <= _days(e2, e) <= 100 and _days(s2, e2) > 60:
                out[e] = v - v2
                break
        else:
            if 340 <= _days(s, e) <= 380:                       # full year minus three reported quarters
                inside = [v3 for e3, v3 in out.items() if s < e3 < e and _days(e3, e) > 60]
                if len(inside) == 3:
                    out[e] = v - sum(inside)
    return out


def annual(facts):
    """{end: full-year value} (periods of 340-380 days)."""
    return {f['end']: f['val'] for f in facts if f.get('start') and 340 <= _days(f['start'], f['end']) <= 380}


def _chain(q, end, n):
    """n quarter ends going back from `end`, each 80-100 days before the next; None when a quarter is missing."""
    ends, cur = [end], end
    by_date = sorted(q)
    while len(ends) < n:
        prev = [e for e in by_date if 80 <= _days(e, cur) <= 100]
        if not prev:
            return None
        cur = prev[-1]
        ends.append(cur)
    return ends


def ttm(q, end, offset=0):
    """Sum of four quarters ending at `end` (offset=4: the four quarters before them). None when one is missing."""
    ends = _chain(q, end, offset + 4) if end in q else None
    return sum(q[e] for e in ends[offset:offset + 4]) if ends else None


def _near(rows, target, tol=10):
    """The instant value closest to a date (within tol days)."""
    best = None
    for r in rows:
        gap = abs(_days(r['end'], target))
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, r['val'])
    return best[1] if best else None


def _year_before(ends, end):
    c = [e for e in ends if 350 <= _days(e, end) <= 380]
    return c[-1] if c else None


def _ratio(a, b):
    return a / b if a is not None and b not in (None, 0) and b > 0 else None


def durability(facts, fallback=()):
    """Revenue over the last three completed fiscal years (four full-year values). facts: the revenue facts in use;
    fallback: the per-period merge of every revenue concept, read when the first has under four years.
    Returns fy_ends (oldest first), rev_year_growth (three changes), rev_min_year_growth, rev_cagr_3y; None when short."""
    for basis, fs in (('revenue', facts), ('merged_revenue_concepts', fallback)):
        a = annual(fs)
        if not a:
            continue
        ends = [max(a)]
        while len(ends) < 4:
            prev = _year_before(sorted(a), ends[-1])
            if prev is None:
                break
            ends.append(prev)
        if len(ends) < 4:
            continue
        ends.reverse()
        vals = [a[e] for e in ends]
        if min(vals) <= 0:
            continue
        growth = [vals[i + 1] / vals[i] - 1 for i in range(3)]
        return {'fy_ends': ends, 'fy_revenue': vals, 'rev_year_growth': growth, 'rev_min_year_growth': min(growth),
                'rev_cagr_3y': (vals[3] / vals[0]) ** (1 / 3) - 1, 'durability_basis': basis}
    return None


def _known(facts, as_of):
    """Facts for periods that ended, and were filed, on or before as_of (no look-ahead)."""
    return [f for f in facts if f['end'] <= as_of and (f.get('filed') or '') <= as_of]


def fundamentals(series, as_of):
    """Screen metrics from extracted series. basis 'quarterly' (TTM from eight quarters) when the quarters exist, else
    'annual' (the last two full years), else None with the reason in gaps."""
    gaps = []
    dur = {m: ({t: _known(fs, as_of) for t, fs in v.items()} if isinstance(v, dict) else _known(v, as_of))
           for m, v in (series.get('durations') or {}).items()}
    out = {'basis': None, 'currency': series.get('currency'), 'taxonomy': series.get('taxonomy'), 'gaps': gaps}
    rev, mixed = pick_revenue(dur, as_of)
    merged_rev = dur.get('revenue', [])
    out['revenue_mixed_tags'] = mixed
    if mixed:
        gaps.append('revenue is tagged under different XBRL concepts in different periods: growth not relied on')
    dur = dict(dur, revenue=rev)
    if not rev:
        gaps.append('no revenue facts in SEC XBRL')
        return out
    rq, ra = quarterly(rev), annual(rev)
    q_end = max(rq) if rq else None
    a_end = max(ra) if ra else None
    forms = series.get('forms') or {}
    annual_filer = any(f in forms for f in ANNUAL_FORMS) and not any(f in forms and forms[f] >= (_d(as_of) - timedelta(days=400)).isoformat()
                                                                    for f in ('10-Q', '10-K'))
    out['annual_filer'] = annual_filer
    use_q = q_end is not None and _days(q_end, as_of) <= 200 and ttm(rq, q_end, 4) is not None
    if use_q:
        out['basis'], end = 'quarterly', q_end
    elif a_end is not None and _days(a_end, as_of) <= 500 and _year_before(ra, a_end):
        out['basis'], end = 'annual', a_end
        if not annual_filer:
            gaps.append('fewer than eight usable quarters in XBRL: annual figures used')
    else:
        gaps.append('revenue history too short or stale (latest period %s)' % (q_end or a_end))
        return out
    out['period_end'] = end
    out['period_filed'] = max((f.get('filed') or '' for f in rev if f['end'] == end), default='') or None
    out['stale'] = _days(end, as_of) > STALE_DAYS              # also a full year read in place of quarters
    if out['stale']:
        gaps.append('latest period ended %s, more than five months before %s' % (end, as_of))

    def pair(metric):
        """(latest 12 months, the 12 months before) for one flow metric."""
        fs = [f for f in dur.get(metric, []) if f['end'] <= as_of]
        if out['basis'] == 'quarterly':
            q = quarterly(fs)
            return ttm(q, end), ttm(q, end, 4)
        a = annual(fs)
        prev = _year_before(a, end)
        return a.get(end), a.get(prev) if prev else None
    r1, r0 = pair('revenue')
    out['revenue'], out['revenue_prior'] = r1, r0
    out['rev_growth_ttm'] = r1 / r0 - 1 if _ratio(r1, r0) else None
    dy = durability(rev, merged_rev)
    out.update(dy or {'rev_cagr_3y': None, 'rev_min_year_growth': None})
    if dy is None:
        gaps.append('fewer than four completed fiscal years of revenue in XBRL: three-year growth not read')
    elif dy['durability_basis'] != 'revenue':
        gaps.append('three-year growth read across several revenue concepts')
    if out['basis'] == 'quarterly':
        chain = _chain(rq, end, 5)
        out['q_revenue'], out['q_revenue_year_ago'] = rq[end], rq[chain[4]]
        out['rev_growth_q'] = rq[end] / rq[chain[4]] - 1 if rq[chain[4]] > 0 else None
    o1, o0 = pair('operating_income')
    out['operating_income_basis'] = 'operating_income'
    if o1 is None or o0 is None:
        p1, p0 = pair('pretax_income')
        if p1 is not None and p0 is not None:
            o1, o0, out['operating_income_basis'] = p1, p0, 'pretax_income_proxy'
            gaps.append('no operating income tag: pre-tax income used')
        else:
            gaps.append('operating income missing')
    out['operating_income'] = o1
    out['op_margin'], out['op_margin_prior'] = _ratio(o1, r1), _ratio(o0, r0)
    out['margin_change_pts'] = ((out['op_margin'] - out['op_margin_prior']) * 100
                                if out['op_margin'] is not None and out['op_margin_prior'] is not None else None)
    out['margin_jump'] = out['margin_change_pts'] is not None and (out['margin_change_pts'] > MARGIN_JUMP_PTS
                                                                   or (o0 is not None and o0 <= 0 < (o1 or 0)))
    if out['margin_jump']:
        gaps.append('operating margin up %.1f points in a year: check for a one-off charge in the earlier year' % out['margin_change_pts'])
    c1, _ = pair('cfo')
    x1, _ = pair('capex')
    out['cfo'], out['capex'] = c1, x1
    if c1 is None:
        gaps.append('operating cash flow missing')
    if x1 is None:
        gaps.append('capital expenditure tag missing')
    out['fcf'] = c1 - x1 if c1 is not None and x1 is not None else None
    out['fcf_margin'] = _ratio(out['fcf'], r1)
    n1, _ = pair('net_income')
    out['net_income'] = n1
    # diluted weighted shares: the latest reported period against the same period a year earlier
    sh = [f for f in dur.get('diluted_shares', []) if f['end'] <= as_of]
    s1 = s0 = None
    for table in ((quarterly(sh, derive=False),) if out['basis'] == 'quarterly' else ()) + (annual(sh),):
        prev = _year_before(table, end) if end in table else None
        if prev:
            s1, s0 = table[end], table[prev]
            break
    out['shares'], out['shares_year_ago'] = s1, s0
    out['share_change'] = s1 / s0 - 1 if _ratio(s1, s0) else None
    if out['share_change'] is None:
        gaps.append('diluted share count missing')
    eq = _known((series.get('instants') or {}).get('equity', []), as_of)
    e1 = _near(eq, end)
    e0 = _near(eq, (_d(end) - timedelta(days=365)).isoformat())
    out['equity'], out['equity_year_ago'] = e1, e0
    out['roe'] = n1 / ((e1 + e0) / 2) if n1 is not None and e1 is not None and e0 is not None and e1 + e0 > 0 else None
    out['bvps_growth'] = ((e1 / s1) / (e0 / s0) - 1 if e1 and e0 and s1 and s0 and e0 > 0 and e1 > 0 else None)
    return out


# ---------------------------------------------------------------- gates

def _gate(name, value, ok, rule):
    return {'gate': name, 'value': value, 'pass': None if value is None else bool(ok), 'rule': rule}


def quality_gates(m):
    """The seven quality gates (six on annual data: no quarter). pass None = the number is missing."""
    v = m.get
    g = [_gate('rev_growth_ttm', v('rev_growth_ttm'), v('rev_growth_ttm') is not None and v('rev_growth_ttm') >= GATES['rev_growth_ttm'] - EPS,
               'revenue growth, last 12 months >= 15%')]
    if m.get('basis') != 'annual':
        g.append(_gate('rev_growth_q', v('rev_growth_q'), v('rev_growth_q') is not None and v('rev_growth_q') >= GATES['rev_growth_q'] - EPS,
                       'latest quarter against a year earlier >= 15%'))
    cagr, worst = v('rev_cagr_3y'), v('rev_min_year_growth')
    g.append(dict(_gate('durability', cagr, cagr is not None and cagr >= GATES['rev_cagr_3y'] - EPS and worst >= -EPS,
                        'revenue did not fall in any of the last 3 fiscal years and grew >= 10% a year over them'),
                  worst_year=worst))
    g += [_gate('operating_income', v('operating_income'), v('operating_income') is not None and v('operating_income') > 0,
                'operating income, last 12 months > 0'),
          _gate('margin_change_pts', v('margin_change_pts'), v('margin_change_pts') is not None and v('margin_change_pts') >= -GATES['margin_drop_pts'] - EPS,
                'operating margin not more than 1 point below a year earlier'),
          _gate('fcf', v('fcf'), v('fcf') is not None and v('fcf') > 0, 'free cash flow, last 12 months > 0'),
          _gate('share_change', v('share_change'), v('share_change') is not None and v('share_change') <= GATES['share_growth'] + EPS,
                'diluted shares up not more than 3% in a year')]
    return g


def financial_gates(m):
    """Banks and insurers: revenue growing, return on equity >= 15%, book value per share growing."""
    v = m.get
    return [_gate('rev_growth_ttm', v('rev_growth_ttm'), v('rev_growth_ttm') is not None and v('rev_growth_ttm') > 0, 'revenue growing'),
            _gate('roe', v('roe'), v('roe') is not None and v('roe') >= GATES['roe'] - EPS, 'return on equity >= 15%'),
            _gate('bvps_growth', v('bvps_growth'), v('bvps_growth') is not None and v('bvps_growth') > 0, 'book value per share growing')]


def trend_measures(adj, closes=None, highs=None):
    """Daily bars, oldest first, the last one on the as-of day. adj: closes adjusted for splits and dividends (the 12-1
    return, comparable with SPY's). closes, highs: adjusted for splits only (the 200-day average and the 52-week high are
    price levels; a dividend-adjusted high understates the distance for a high-yield stock). Without them adj stands in.
    None when under 253 bars."""
    closes, highs = closes or adj, highs or closes or adj
    bars = [(a, c, h if h is not None else c) for a, c, h in zip(adj, closes, highs) if a is not None and c is not None]
    if len(bars) < 253:
        return None
    a, c, h = ([b[i] for b in bars] for i in range(3))
    ma = sum(c[-200:]) / 200
    ma_prev = sum(c[-221:-21]) / 200
    high = max(max(h[-252:]), max(c[-252:]))
    return {'last': c[-1], 'ret_12_1': a[-22] / a[-253] - 1, 'ma200': ma, 'ma200_21d_ago': ma_prev, 'high_52w': high,
            'below_high': c[-1] / high - 1, 'bars': len(bars)}


def trend_gates(t, spy):
    """Three trend gates: 12-1 return above SPY's, price above a rising 200-day average, within 25% of the 52-week high."""
    if not t or not spy:
        return [_gate(n, None, False, r) for n, r in (('rel_strength', '12-month return (skipping the last month) above SPY'),
                                                      ('above_rising_200d', 'price above a rising 200-day average'),
                                                      ('near_high', 'not more than 25% below the 52-week high'))]
    rs = t['ret_12_1'] - spy['ret_12_1']
    return [_gate('rel_strength', rs, rs > 0, '12-month return (skipping the last month) above SPY'),
            _gate('above_rising_200d', t['last'] / t['ma200'] - 1, t['last'] > t['ma200'] and t['ma200'] > t['ma200_21d_ago'],
                  'price above a rising 200-day average'),
            _gate('near_high', t['below_high'], t['below_high'] >= -GATES['max_below_high'] - EPS, 'not more than 25% below the 52-week high')]


def failed(gates):
    return [g for g in gates if g['pass'] is False]


def missing(gates):
    return [g for g in gates if g['pass'] is None]


def classify(quality, trend):
    """'pass' (every gate), 'near_miss' (exactly one gate failed, none missing), 'incomplete' (a number is missing and at
    most one gate failed), 'fail'. trend None = not fetched because quality already failed twice."""
    q_fail = len(failed(quality))
    if trend is None:
        return 'fail' if q_fail >= 2 else 'incomplete'
    gates = quality + trend
    n_fail, n_missing = len(failed(gates)), len(missing(gates))
    if n_fail >= 2:
        return 'fail'
    if n_missing:
        return 'incomplete'
    return 'pass' if n_fail == 0 else 'near_miss'


def needs_trend(quality):
    return len(failed(quality)) <= 1


def percentile_ranks(values):
    """0-100 per value: share of the others below it, ties sharing the middle. One value alone gets 50."""
    n = len(values)
    if n == 1:
        return [50.]
    return [100. * (sum(1 for w in values if w < v) + 0.5 * (sum(1 for w in values if w == v) - 1)) / (n - 1) for v in values]


def rank(rows, keys=RANK_KEYS):
    """Adds rank_parts (percentile per component) and rank_score (their plain sum, no weights); best first."""
    rows = list(rows)
    for r in rows:
        r['rank_parts'] = {}
    for k in keys:
        for r, p in zip(rows, percentile_ranks([r[k] for r in rows]) if rows else []):
            r['rank_parts'][k] = round(p, 1)
    for r in rows:
        r['rank_score'] = round(sum(r['rank_parts'].values()), 1)
    return sorted(rows, key=lambda r: (-r['rank_score'], r['symbol']))


def price_columns(m, market_cap, fx=1.):
    """Triage columns, no gate: TTM P/E, price/sales, free-cash-flow yield. fx: units of the reporting currency per USD."""
    if not market_cap or not fx:
        return {'pe': None, 'ps': None, 'fcf_yield': None}
    ni, rev, fcf = (m.get(k) / fx if m.get(k) is not None else None for k in ('net_income', 'revenue', 'fcf'))
    return {'pe': market_cap / ni if ni and ni > 0 else None, 'ps': market_cap / rev if rev and rev > 0 else None,
            'fcf_yield': fcf / market_cap if fcf is not None else None}


def evaluate(row, series, sic, as_of):
    """One company -> row with route ('standard', 'commodity', 'financial', 'annual_only'), metrics and its fundamental gates."""
    m = fundamentals(series, as_of)
    out = dict(row, sic=sic, **{k: v for k, v in m.items() if k != 'gaps'}, gaps=list(m['gaps']))
    if is_commodity(sic, row.get('name')):
        out['route'], out['quality'] = 'commodity', quality_gates(m)
        out['commodity_group'] = COMMODITY_SIC[int(sic)]
    elif is_financial(sic, row.get('sector')):
        out['route'], out['quality'] = 'financial', financial_gates(m)
    elif m['basis'] != 'quarterly' and m.get('annual_filer'):
        out['route'], out['quality'] = 'annual_only', quality_gates(dict(m, basis='annual'))
    else:
        out['route'], out['quality'] = 'standard', quality_gates(m)
        if m['basis'] == 'annual':                      # a domestic filer without eight quarters: the quarter gate is missing
            out['quality'].insert(1, _gate('rev_growth_q', None, False, 'latest quarter against a year earlier >= 15%'))
    if m['basis'] is None:
        out['quality'] = [dict(g, **{'pass': None, 'value': None}) for g in out['quality']]
    elif m.get('revenue_mixed_tags'):
        out['quality'] = [dict(g, **{'pass': None}) if g['gate'] in ('rev_growth_ttm', 'rev_growth_q') else g for g in out['quality']]
    return out


# ---------------------------------------------------------------- Hebrew report

GATE_HE = {'rev_growth_ttm': 'צמיחת הכנסות ב-12 חודשים', 'rev_growth_q': 'צמיחת הרבעון האחרון מול שנה קודם',
           'durability': 'צמיחה עקבית בשלוש השנים האחרונות', 'operating_income': 'רווח תפעולי חיובי', 'margin_change_pts': 'שולי רווח תפעולי לא יורדים',
           'fcf': 'תזרים מזומנים חופשי חיובי', 'share_change': 'מספר המניות לא גדל מעל 3%',
           'rel_strength': 'תשואת 12 חודשים מעל ה-S&P 500', 'above_rising_200d': 'מחיר מעל ממוצע 200 יום עולה',
           'near_high': 'לא יותר מ-25% מתחת לשיא השנתי', 'roe': 'תשואה על ההון 15% ומעלה',
           'bvps_growth': 'הון עצמי למניה גדל'}
PCT_GATES = ('rev_growth_ttm', 'rev_growth_q', 'share_change', 'rel_strength', 'above_rising_200d', 'near_high', 'roe', 'bvps_growth')


def _pct(x, signed=False):
    return '—' if x is None else (f'{x * 100:+.0f}%' if signed else f'{x * 100:.0f}%')


def _x(x, d=1):
    return '—' if x is None else f'{x:.{d}f}'


def _cap(x):
    return '—' if not x else (f'{x / 1e12:.2f}T$' if x >= 1e12 else f'{x / 1e9:.0f}B$')


def gate_value_he(g):
    v = g['value']
    if v is None:
        return 'אין נתון'
    if g['gate'] == 'margin_change_pts':
        return f'{v:+.1f} נק׳'
    if g['gate'] == 'durability':
        return f"{v * 100:+.1f}% בשנה, השנה החלשה {g.get('worst_year', 0) * 100:+.1f}%"
    if g['gate'] in PCT_GATES:
        return f'{v * 100:+.1f}%'
    return f'{v / 1e9:+.2f} מיליארד'


def _marks(r):
    gates = r['quality'] + (r.get('trend') or [])
    return ' '.join('✓' if g['pass'] else ('?' if g['pass'] is None else '✗') for g in gates)


def _table(head, lines):
    return ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(str(c) for c in ln) + ' |' for ln in lines]


def _std_line(r):
    t = r.get('trend_measures') or {}
    return [r['symbol'], r['name'][:28], r.get('sector') or '—', _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')),
            _pct(r.get('rev_growth_q')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
            '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}" + ('*' if r.get('margin_jump') else ''),
            _pct(r.get('fcf_margin')), _pct(r.get('share_change'), True), _pct(t.get('ret_12_1'), True), _pct(t.get('below_high'), True),
            _x(r.get('pe'), 0), _x(r.get('ps')), _pct(r.get('fcf_yield'))]


def _period(r):
    return (r.get('period_end') or '—') + (' (ישן)' if r.get('stale') else '')


STD_HEAD = ['סימול', 'חברה', 'ענף', 'שווי שוק', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי',
            'שינוי מניות', 'תשואה 12-1', 'מרחק מהשיא', 'מכפיל רווח', 'מכפיל מכירות', 'תשואת תזרים']


def render_he(res, top=40):
    """screen_he.md: passers, near misses, commodity names, financials, annual-only filers, thesis-book names, and what the
    screen is not."""
    c, spy = res['counts'], res.get('spy') or {}
    L = [f"# חיפוש מניות: איכות + מגמה ({res['as_of']})", '',
         f"נסרקו {c['companies']} חברות בשווי שוק מעל {res['min_cap'] / 1e9:.0f} מיליארד דולר. "
         f"עברו את כל התנאים: **{c['passers']}**. כמעט עברו (נכשלו בתנאי אחד בלבד): {c['near_misses']}. "
         f"תשואת ה-S&P 500 (SPY) ב-12 החודשים בלי החודש האחרון: {_pct(spy.get('ret_12_1'), True)}.", '',
         '## מה החיפוש אומר ומה לא', '',
         '- הוא מסמן חברות שצומחות מהר, מרוויחות, מייצרות מזומן, לא מדללות, והמניה שלהן במגמת עלייה. הוא רק מחליט לאן להפנות זמן מחקר.',
         '- הוא **לא הערכת שווי ולא המלצת קנייה**: חברה שעוברת אותו יכולה להיות יקרה מדי ולהיכשל במבחן הקנייה.',
         '- הוא **לא נבדק על העבר** (לא נעשה מבחן היסטורי), ולכן אין לו שיעור הצלחה מוכח.',
         '- הנתונים מדוחות ה-SEC כפי שדווחו (בלי התאמות לאירועים חד-פעמיים); חברה שרכשה חברה אחרת תיראה צומחת מהר יותר ממה שהיא באמת.',
         '- חברות סחורות (נפט וזיקוק, מכרות, פלדה, דשנים, ספנות, צינורות גז) מוצגות בטבלה נפרדת ולא ברשימת העוברות: '
         'ההכנסות שלהן עולות ויורדות עם מחיר הסחורה, ושנה טובה אחת אינה צמיחה של העסק. הסיווג לפי קוד הענף של ה-SEC, והוא גס.', '',
         '## התנאים', '',
         f"- איכות (מהדוחות האחרונים): ההכנסות גדלו {GATES['rev_growth_ttm'] * 100:.0f}% ומעלה ב-12 החודשים האחרונים וגם ברבעון האחרון מול "
         f"אותו רבעון לפני שנה ({GATES['rev_growth_q'] * 100:.0f}% ומעלה); רווח תפעולי חיובי; שולי הרווח התפעולי לא ירדו "
         f"(ירידה של עד {GATES['margin_drop_pts']:.0f} נקודת אחוז נחשבת יציבה); תזרים מזומנים חופשי חיובי; מספר המניות לא גדל ביותר מ-"
         f"{GATES['share_growth'] * 100:.0f}% בשנה.",
         f"- צמיחה עקבית: ההכנסות לא ירדו באף אחת משלוש שנות הכספים האחרונות שהסתיימו, וגדלו בממוצע {GATES['rev_cagr_3y'] * 100:.0f}% "
         'בשנה ומעלה לאורכן. שנה טובה אחת לא מספיקה.',
         f"- מגמה: תשואת 12 חודשים בלי החודש האחרון מעל זו של ה-S&P 500; מחיר מעל ממוצע 200 יום שעולה; לא יותר מ-"
         f"{GATES['max_below_high'] * 100:.0f}% מתחת לשיא של 52 שבועות.",
         '- חברה שנכשלת בתנאי אחד בלבד מופיעה ברשימת "כמעט עברו" עם הסיבה. חברה שחסר לה נתון לא נספרת כעוברת.', '',
         f"## חברות שעברו את כל התנאים ({c['passers']})", '',
         'הדירוג: סכום פשוט של ארבעה דירוגי אחוזון (צמיחה, שינוי שוליים, שולי תזרים חופשי, עוצמה מול המדד), בלי משקלות.', '']
    ps = res['passers']
    L += _table(['#'] + STD_HEAD + ['רבעון אחרון עד', 'ציון דירוג'],
                [[i + 1] + _std_line(r) + [_period(r), f"{r['rank_score']:.0f}"]
                 for i, r in enumerate(ps[:top])])
    if any(r.get('margin_jump') for r in ps):
        L += ['', '* קפיצה חריגה בשולי הרווח (יותר מ-10 נקודות, או מעבר מהפסד לרווח): לרוב הוצאה חד-פעמית בשנה הקודמת ולא שיפור בעסק. לבדוק: '
              + ', '.join(r['symbol'] for r in ps if r.get('margin_jump'))]
    if any(r.get('stale') for r in ps):
        L += ['', '"ישן": התקופה האחרונה בנתונים הסתיימה לפני יותר מחמישה חודשים.']
    if any(r.get('operating_income_basis') == 'pretax_income_proxy' for r in ps):
        L += ['', 'בלי שורת רווח תפעולי בדוח (נלקח הרווח לפני מס במקומו): '
              + ', '.join(r['symbol'] for r in ps if r.get('operating_income_basis') == 'pretax_income_proxy')]
    scores = [r['rank_score'] for r in ps[:top]]
    if len(set(scores)) < len(scores):
        L += ['', 'חברות עם אותו ציון דירוג שוות בדירוג; הסדר ביניהן אלפביתי בלבד.']
    if len(ps) > top:
        L += ['', 'עוד עוברות (הפירוט המלא ב-screen.json): ' + ', '.join(r['symbol'] for r in ps[top:])]
    L += ['', 'רכיבי הדירוג (אחוזון 0-100 לכל רכיב):', '']
    L += _table(['סימול', 'צמיחה', 'שינוי שוליים', 'שולי תזרים', 'עוצמה מול המדד', 'סכום'],
                [[r['symbol']] + [f"{r['rank_parts'][k]:.0f}" for k in RANK_KEYS] + [f"{r['rank_score']:.0f}"] for r in ps[:top]])
    L += ['', f"## כמעט עברו: נכשלו בתנאי אחד בלבד ({c['near_misses']})", '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'התנאי שנכשל', 'המספר', 'צמיחה 12ח׳', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'תשואה 12-1', 'מרחק מהשיא'],
                [[r['symbol'], r['name'][:28], _cap(r['market_cap']), GATE_HE[r['failed'][0]['gate']], gate_value_he(r['failed'][0]),
                  _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')), _pct((r.get('trend_measures') or {}).get('ret_12_1'), True),
                  _pct((r.get('trend_measures') or {}).get('below_high'), True)] for r in res['near_misses']])
    cm = res.get('commodity') or []
    L += ['', f"## חברות סחורות מחזוריות: הצמיחה הולכת אחרי המחירים ({len(cm)} מתוך {c.get('route_commodity', len(cm))})", '',
          'נפט וגז, זיקוק, מכרות, פלדה, דשנים, ספנות וצינורות. הן לא נכנסות לרשימת העוברות גם כשהמספרים נראים טוב. '
          'מוצגות אלה שעברו את התנאים או נכשלו בתנאי אחד בלבד; השאר ב-screen.json.', '']
    L += _table(['סימול', 'חברה', 'תחום (קוד SEC)', 'שווי שוק', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'השנה החלשה', 'שולי תפעול',
                 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי', 'תשואה 12-1', 'מרחק מהשיא', 'מכפיל רווח', 'נתונים עד', 'מצב'],
                [[r['symbol'], r['name'][:28], f"{r.get('sic')}", _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_growth_q')),
                  _pct(r.get('rev_cagr_3y')), _pct(r.get('rev_min_year_growth'), True), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin')),
                  _pct((r.get('trend_measures') or {}).get('ret_12_1'), True), _pct((r.get('trend_measures') or {}).get('below_high'), True),
                  _x(r.get('pe'), 0), _period(r),
                  'עברה את התנאים' if r['status'] == 'pass' else 'נכשלה ב: ' + GATE_HE[r['failed'][0]['gate']] + ' (' + gate_value_he(r['failed'][0]) + ')']
                 for r in cm])
    L += ['', f"## בנקים, ביטוח ופיננסים (נמדדים בנפרד; {len(res['financials'])} עברו)", '',
          'תנאים: הכנסות גדלות, תשואה על ההון 15% ומעלה, הון עצמי למניה גדל, ושלושת תנאי המגמה.', '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'צמיחת הכנסות', 'תשואה על ההון', 'גידול הון למניה', 'תשואה 12-1', 'מרחק מהשיא', 'מכפיל רווח', 'נתונים', 'נתונים עד'],
                [[r['symbol'], r['name'][:28], _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')), _pct(r.get('roe')),
                  _pct(r.get('bvps_growth'), True), _pct((r.get('trend_measures') or {}).get('ret_12_1'), True),
                  _pct((r.get('trend_measures') or {}).get('below_high'), True), _x(r.get('pe'), 0),
                  'שנתי בלבד' if r.get('basis') == 'annual' else 'רבעוני', _period(r)] for r in res['financials']])
    if any(r.get('stale') for r in cm + res['financials']):
        L += ['', '"ישן": התקופה האחרונה בנתונים הסתיימה לפני יותר מחמישה חודשים (דוח שנתי בלבד).']
    fn = res.get('financial_near_misses') or []
    if fn:
        L += ['', f'פיננסים שנכשלו בתנאי אחד בלבד ({len(fn)}): '
              + '; '.join(f"{r['symbol']} ({GATE_HE[r['failed'][0]['gate']]}: {gate_value_he(r['failed'][0])})" for r in fn)]
    L += ['', f"## חברות זרות עם נתונים שנתיים בלבד ({len(res['annual_only'])})", '',
          'חברות שמדווחות ל-SEC רק דוח שנתי (20-F / 40-F): **נתונים שנתיים בלבד**, בלי תנאי הרבעון, ולכן הנתון יכול להיות ישן עד שנה. '
          'מוצגות אלה שעברו או נכשלו בתנאי אחד.', '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'שנת הדוח עד', 'צמיחה שנתית', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי', 'שינוי מניות',
                 'תשואה 12-1', 'מרחק מהשיא', 'מצב'],
                [[r['symbol'], r['name'][:28], _cap(r['market_cap']), _period(r), _pct(r.get('rev_growth_ttm')),
                  _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin')),
                  _pct(r.get('share_change'), True), _pct((r.get('trend_measures') or {}).get('ret_12_1'), True),
                  _pct((r.get('trend_measures') or {}).get('below_high'), True),
                  'עברה' if r['status'] == 'pass' else 'נכשלה ב: ' + GATE_HE[r['failed'][0]['gate']] + ' (' + gate_value_he(r['failed'][0]) + ')']
                 for r in res['annual_only']])
    L += ['', '## החברות שכבר בספר התזות', '', 'איפה עומדת כל חברה מוכרת מול אותם תנאים (✓ עבר, ✗ נכשל, ? אין נתון).', '']
    L += _table(['סימול', 'מסלול', 'מצב', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי', 'שינוי מניות',
                 'תשואה 12-1', 'מרחק מהשיא', 'מה נכשל או חסר'],
                [[r['symbol'], {'standard': 'רגיל', 'financial': 'פיננסים', 'annual_only': 'שנתי בלבד', 'commodity': 'סחורות'}.get(r.get('route'), '—'),
                  {'pass': 'עברה', 'near_miss': 'כמעט', 'fail': 'נכשלה', 'incomplete': 'חסר נתון', 'not_found': 'לא נמצאה'}[r['status']],
                  _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_growth_q')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin')),
                  _pct(r.get('share_change'), True), _pct((r.get('trend_measures') or {}).get('ret_12_1'), True),
                  _pct((r.get('trend_measures') or {}).get('below_high'), True),
                  '; '.join([GATE_HE[g['gate']] + ' (' + gate_value_he(g) + ')' for g in r.get('failed', [])] +
                            ['חסר: ' + GATE_HE[g['gate']] for g in r.get('missing', [])] + ([r['note']] if r.get('note') else [])) or '—']
                 for r in res['thesis_book']])
    un = res.get('unread') or []
    if un:
        L += ['', f'## לא נכשלו בשום תנאי, אבל חסר להן נתון ({len(un)})', '',
              'לא נספרו כעוברות. כדאי לבדוק ידנית את הנתון החסר לפני שמוותרים עליהן.', '']
        L += _table(['סימול', 'חברה', 'שווי שוק', 'מה חסר'],
                    [[r['symbol'], r['name'][:28], _cap(r['market_cap']),
                      ', '.join(GATE_HE[g['gate']] for g in r.get('missing', [])) or (r.get('note') or '—')] for r in un])
    L += ['', '## מספרים בכל שלב', '']
    L += _table(['שלב', 'מספר'], [[k, v] for k, v in res['counts_he']])
    if res.get('gaps_he'):
        L += ['', '## פערי נתונים', ''] + ['- ' + g for g in res['gaps_he']]
    L += ['', 'מקורות: [SEC XBRL companyfacts](https://data.sec.gov/api/xbrl/companyfacts/), [רשימת המניות של Nasdaq](https://www.nasdaq.com/market-activity/stocks/screener), '
          'מחירים יומיים מ-Yahoo Finance (מתואמים לפיצולים; התשואה מתואמת גם לדיבידנדים).', '']
    return '\n'.join(L)
