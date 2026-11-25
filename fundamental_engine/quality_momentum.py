"""Quality + business-momentum screen: decides where research time goes. Pure logic (no network);
scripts/quality_momentum_screen.py fetches the data. It is not a valuation and not a buy signal, and it is not backtested.

Data source, stated exactly: quality gates come from the SEC companyfacts API read per company
(data.sec.gov/api/xbrl/companyfacts/CIK##########.json, one request per company above the market value floor, plus its
submissions file for the SIC code), not from the XBRL frames API (one concept for every filer in one period). TTM
(trailing twelve months) is built from quarters; a fourth quarter is the full year minus nine months. Banks and insurers
are read on their own measures; filers with annual XBRL only (20-F, 40-F) are read on annual gates.

Version 2 (25.11.2026, the owner's correction after DELL): price is information, never a gate. The three price gates
(12-1 return above SPY, price above a rising 200-day average, within 25% of the 52-week high) threw out DELL, 43% below
its high, just before its best quarter. trend_measures() and price_info() still describe the price, and no status reads
them. What replaced them is business_gates(): two gates from analyst data (Nasdaq analyst pages, data attributed to
Zacks, an unofficial public API), parsed by parse_estimates():
- eps_revisions: analysts are not cutting (revisions up minus down over 4 weeks, current and next fiscal year, >= 0; and
  the current fiscal year's consensus not more than 1% lower over the longest window we have: one month from Nasdaq, or
  our own saved readings of the same fiscal year, up to 100 days back, when they are on the same per-share scale);
- earnings_beats: reported EPS at or above consensus in each of the last two reported quarters (a miss of up to 2%, or
  of up to one cent, counts as met).
What each part stops: a business that stopped growing (the PayPal pattern) is stopped by the quality gates (PYPL fails
four of them and passes both analyst gates). The analyst gates only add a check that the last two quarters and the last
month of estimates are not getting worse. The two analyst gates count as ONE failed condition (fail_count): a missed
quarter lowers the full-year consensus by itself, so both gates read the same event. An EPS series that swings far
above and far below the consensus in consecutive quarters (investment gains, a tiny base) is not readable: both gates
are missing, never failed. A good business whose stock fell while analysts raise estimates and the company beats is
flagged 'price_down_business_up' (divergence()). earnings_radar() lists who reported lately and who reports next.

Saved valuations (15.11.2026, the MSFT slip of 06.11.2026): saved_valuation() reads what the research folder remembers
about a screened name (the newest dated valuation through valuation_record.latest_valuation, and research/rejected.jsonl
for checks that ended 'not a buy') and runs the buy test at the screen's price: lean params are repriced and rerun with
lean_model; a custom model is compared with its saved buy-test price. A name that fails is marked, never dropped: the
screen's gates and ranking do not read it. This part reads files under research/; it fetches nothing and writes nothing.
"""
import json
import os
import re
from datetime import date, timedelta

from . import lean_freeze, valuation_record

GATES = {'rev_growth_ttm': 0.15, 'rev_growth_q': 0.15, 'margin_drop_pts': 1.0, 'share_growth': 0.03,
         'roe': 0.15, 'rev_cagr_3y': 0.10,
         # business momentum (analyst data): the consensus may slip by up to 1%; a miss of up to 2%, or of up to one
         # cent a share, counts as met (2% of a small EPS is less than the cent EPS is reported in)
         'eps_revision_min': -0.01, 'surprise_min_pct': -2.0, 'surprise_cent_tolerance': 0.01,
         # information only, no gate: this far below the 52-week high with the business improving is flagged
         'divergence_below_high': 0.25}
HISTORY_DAYS = 1650                             # facts kept: four completed fiscal years back from the as-of date
MARGIN_JUMP_PTS = 10.0                          # a larger one-year margin change is flagged as a likely one-off (no gate)
EPS = 1e-9
RANK_KEYS = ('rev_growth_ttm', 'margin_change_pts', 'fcf_margin', 'eps_revision_pct')
RANK_FILL_MEDIAN = ('eps_revision_pct',)        # a passer without this number gets the median of the others
REVISION_MONTH_DAYS = 30                        # Nasdaq's estimate-momentum page: the consensus now against one month ago
REVISION_HISTORY_DAYS = 100                     # our own saved readings of the same fiscal year are read this far back
REVISION_MIN_OWN_DAYS = 7                       # an own reading younger than this never stands in for a missing month-ago number
SCALE_LIMIT = 1.8                               # a consensus this far from our saved reading: a split, not a revision
RESTATED_EPS = 0.02                             # a reported quarter's EPS this far from what we saved: the per-share scale changed
MOMENTUM_NO_VALUE = 999.0                       # the estimate-momentum page writes 999 where it has no consensus
MOMENTUM_TABLE_TOLERANCE = 0.011                # its current consensus must match the forecast table's to the cent
ERRATIC_SURPRISE_PCT = 25.0                     # two consecutive surprises beyond this, in opposite directions: EPS is not readable
SURPRISE_STALE_DAYS = 200                       # the newest reported quarter older than this: the beats gate is missing
RADAR_BACK_DAYS, RADAR_AHEAD_DAYS = 10, 14      # the earnings radar: calendar days before and after the as-of date
RADAR_MIN_SURPRISE_PCT = 5.0                    # a screened name is listed as 'reported' from this EPS surprise up
DIVERGENCE = 'price_down_business_up'
DIVERGENCE_TOP = 12                             # rows per list in the report's top section; the rest are named in one line

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


def price_info(t, spy):
    """Price as information, never a gate (25.11.2026): no status reads this. t, spy: trend_measures() of the stock and
    of SPY. rel_strength: the 12-1 return minus SPY's; vs_ma200: the last close against the 200-day average;
    ma200_rising: that average against itself 21 days ago; below_high: the last close against the 52-week high.
    None when the stock has no usable bars."""
    if not t:
        return None
    return {'rel_strength': t['ret_12_1'] - spy['ret_12_1'] if spy else None, 'vs_ma200': t['last'] / t['ma200'] - 1,
            'ma200_rising': t['ma200'] > t['ma200_21d_ago'], 'below_high': t['below_high']}


# ---------------------------------------------------------------- business momentum (analyst data)

_MONTHS = {m: i + 1 for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}
_MONTH_YEAR = re.compile(r'([A-Za-z]{3})[A-Za-z]*\.?[\s/,-]+(\d{4})')
_MDY = re.compile(r'^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$')


def _fnum(x):
    """A float from a number or a numeric string ('$1,234.5', '(0.62)', '39.85%'). None for 'N/A', '', '--', a missing
    value or anything else that is not a finite number."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        t = str(x).strip().replace('$', '').replace(',', '').replace('%', '')
        neg = t.startswith('(') and t.endswith(')')
        try:
            v = float(t[1:-1] if neg else t)
        except ValueError:
            return None
        v = -v if neg else v
    return v if v == v and abs(v) != float('inf') else None


def _int(x):
    v = _fnum(x)
    return None if v is None else int(v)


def _month(text):
    """'Jan 2027', 'Oct/2026' or 'Fiscal Year End Jan 2027' -> '2027-01'; None when there is no month and year."""
    for m in _MONTH_YEAR.finditer(text if isinstance(text, str) else ''):
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            return f'{m.group(2)}-{mon:02d}'
    return None


def _mdy(text):
    """'11/24/2026' -> '2026-11-24'; an ISO date stays; None otherwise."""
    if not isinstance(text, str):
        return None
    m = _MDY.match(text)
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat() if m else _d(text.strip()[:10]).isoformat()
    except ValueError:
        return None


def _data(j):
    """The 'data' node of a Nasdaq API answer (or the node itself when it was saved alone); {} when there is none."""
    if isinstance(j, dict) and 'data' in j:
        j = j['data']
    return j if isinstance(j, dict) else {}


def _rows(node):
    rows = (node or {}).get('rows') if isinstance(node, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _surprise(eps, consensus, given=None):
    """EPS surprise in percent: the feed's number, else (reported - consensus) / |consensus|. None when it cannot be told
    (a zero consensus has no percentage)."""
    if given is not None:
        return given
    if eps is None or consensus is None or consensus == 0:
        return None
    return (eps - consensus) / abs(consensus) * 100


def symbol_variants(symbol):
    """Spellings to try on the analyst pages for a share-class symbol (BRK-B, BRK.B, BRK/B): the symbol as given, then
    the dotted and the hyphen forms. A slash is never sent (it would change the URL path)."""
    s = (symbol or '').strip().upper()
    out = []
    for v in (s.replace('/', '.'), s.replace('/', '.').replace('-', '.'), s.replace('/', '-').replace('.', '-')):
        if v and v not in out:
            out.append(v)
    return out


def sibling_symbols(symbol, other_classes=()):
    """Other share classes of the same company, tried when the screened class has no analyst record (Moog: MOG-B has
    none, MOG.A has all three pages): the other classes the listing gave for the same SEC number, then for a symbol
    with a class suffix its A/B sister and its root (MOG-B -> MOG-A, MOG). The estimates are per share of the same
    company and the gates read percentages, so any class of it answers for the rest."""
    s = norm_symbol(symbol)
    out = [norm_symbol(o) for o in other_classes or ()]
    if '-' in s:
        root, cls = s.rsplit('-', 1)
        if cls in ('A', 'B'):
            out.append(root + '-' + ('B' if cls == 'A' else 'A'))
        out.append(root)
    return [x for x in dict.fromkeys(out) if x and x != s]


def parse_estimates(surprise_json, forecast_json, momentum_json):
    """One company's analyst record from three Nasdaq pages (the raw JSON of each; None or {} when a page gave nothing):
    company/<S>/earnings-surprise, analyst/<S>/earnings-forecast and analyst/<S>/estimate-momentum (data by Zacks).
    Values come as numbers, numeric strings, 'N/A' or not at all: each is read with _fnum and a missing one is None.
    The estimate-momentum page writes 999 where it has no consensus (MOMENTUM_NO_VALUE): read as missing, with a gap.

    Returns {'surprises': reported quarters, newest first [{fiscal_qtr_end 'YYYY-MM', date_reported, eps, consensus,
    surprise_pct}]; 'years': fiscal-year consensus rows, the current year first [{fiscal_year_end 'YYYY-MM', consensus,
    high, low, n, up, down}] (up, down: analysts who raised or cut over the last 4 weeks); 'momentum': the current
    fiscal year's consensus {fiscal_year_end, current, week_ago, month_ago} or None; 'gaps': what was not there}."""
    gaps = []
    table = _data(surprise_json).get('earningsSurpriseTable')
    surprises = []
    for r in _rows(table):
        eps, cons = _fnum(r.get('eps')), _fnum(r.get('consensusForecast'))
        if eps is None and cons is None:
            continue
        surprises.append({'fiscal_qtr_end': _month(r.get('fiscalQtrEnd')), 'date_reported': _mdy(r.get('dateReported')), 'eps': eps,
                          'consensus': cons, 'surprise_pct': _surprise(eps, cons, _fnum(r.get('percentageSurprise')))})
    if surprises and all(x['date_reported'] for x in surprises):
        surprises.sort(key=lambda x: x['date_reported'], reverse=True)         # else the feed's order stands (newest first)
    if not surprises:
        gaps.append('no reported quarters on the earnings-surprise page')
    years = []
    for r in _rows(_data(forecast_json).get('yearlyForecast')):
        fy, cons = _month(r.get('fiscalEnd')), _fnum(r.get('consensusEPSForecast'))
        if fy is None:
            continue
        years.append({'fiscal_year_end': fy, 'consensus': cons, 'high': _fnum(r.get('highEPSForecast')), 'low': _fnum(r.get('lowEPSForecast')),
                      'n': _int(r.get('noOfEstimates')), 'up': _int(r.get('up')), 'down': _int(r.get('down'))})
    years.sort(key=lambda x: x['fiscal_year_end'])
    if not years:
        gaps.append('no yearly rows on the earnings-forecast page')
    cc = _data(momentum_json).get('changeInConsensus')
    cc = cc if isinstance(cc, dict) else {}
    mean, no_value = {}, []
    for k in ('currentData', 'weekData', 'monthData'):
        v = _fnum(cc[k].get('yrMean')) if isinstance(cc.get(k), dict) else None
        if v is not None and abs(abs(v) - MOMENTUM_NO_VALUE) < 1e-6:        # 999 = the page has no consensus for that day
            no_value.append(k[:-4])
            v = None
        mean[k] = v
    if no_value:
        gaps.append('estimate-momentum gives %g (no value) for: %s' % (MOMENTUM_NO_VALUE, ', '.join(no_value)))
    momentum = None
    if any(v is not None for v in mean.values()):
        momentum = {'fiscal_year_end': _month(cc.get('fiscalYearEndText')), 'current': mean['currentData'],
                    'week_ago': mean['weekData'], 'month_ago': mean['monthData']}
        if years and momentum['fiscal_year_end'] and momentum['fiscal_year_end'] != years[0]['fiscal_year_end']:
            gaps.append('estimate-momentum describes fiscal year %s, the forecast table starts at %s'
                        % (momentum['fiscal_year_end'], years[0]['fiscal_year_end']))
    else:
        gaps.append('no consensus change on the estimate-momentum page')
    return {'surprises': surprises, 'years': years, 'momentum': momentum, 'gaps': gaps}


def history_lines(est, as_of):
    """Our own record of one reading of the consensus, one line per fiscal year: [{as_of, fiscal_year_end, consensus, n,
    ref_reported, ref_eps}]. as_of: the day the pages were read. ref_reported, ref_eps: the newest reported quarter and
    its EPS as the page showed them that day; a later run that finds another EPS for the same quarter knows the
    per-share scale changed (a split) and does not compare the two readings. The script appends the lines to
    _estimates_history/<SYMBOL>.jsonl once per such day; eps_revision() reads them on later runs."""
    est = est or {}
    ref = next((q for q in est.get('surprises') or [] if q.get('date_reported') and q.get('eps') is not None), {})
    return [{'as_of': as_of, 'fiscal_year_end': y['fiscal_year_end'], 'consensus': y['consensus'], 'n': y['n'],
             'ref_reported': ref.get('date_reported'), 'ref_eps': ref.get('eps')}
            for y in est.get('years') or [] if y.get('consensus') is not None]


def _scale_break(est, reading, now):
    """Why one saved reading cannot be compared with today's consensus, or None. Our saved number is per share at the
    share count of its day, and the feed restates its own numbers after a split: a 10-for-1 split would read as a 90%
    cut. Two checks: the consensus moved by more than SCALE_LIMIT either way; or the EPS of the quarter we saved as a
    reference is shown differently today (more than RESTATED_EPS)."""
    then = _fnum(reading.get('consensus'))
    if then and now and then > 0 and now > 0 and not 1 / SCALE_LIMIT < now / then < SCALE_LIMIT:
        return 'consensus %g against %g saved on %s' % (now, then, reading.get('as_of'))
    ref_eps = _fnum(reading.get('ref_eps'))
    if ref_eps and reading.get('ref_reported'):
        cur = next((q.get('eps') for q in est.get('surprises') or [] if q.get('date_reported') == reading['ref_reported']), None)
        if cur and abs(cur / ref_eps - 1) > RESTATED_EPS:
            return 'the EPS reported on %s reads %g, we saved %g on %s' % (reading['ref_reported'], cur, ref_eps, reading.get('as_of'))
    return None


def eps_revision(est, history=(), as_of=None, read_on=None):
    """The change in the current fiscal year's consensus EPS over the longest window we have.
    Nasdaq gives the consensus now against one month ago (REVISION_MONTH_DAYS). history: our own saved readings
    (history_lines of earlier runs). read_on: the day the pages were read (default as_of); the age of a saved reading is
    measured from it, so a reading saved from these same pages is never compared with itself. When history holds a
    reading of the SAME fiscal year end that is older than Nasdaq's month and at most REVISION_HISTORY_DAYS old, the
    oldest such reading is the starting point; when Nasdaq gives no month-ago number, an own reading at least
    REVISION_MIN_OWN_DAYS old stands in. A saved reading on another per-share scale (_scale_break) is skipped and named
    in notes. The month pair is not used when the page's current consensus disagrees with the forecast table's for the
    same fiscal year (a stale or broken page; notes say so).
    Returns {fiscal_year_end, now, then, pct (a fraction), window_days, source ('nasdaq_month' | 'own_history'), state,
    notes}. state 'ok'; 'not_positive' when either consensus is zero or negative (a loss, or a change of sign: no usable
    percentage, pct None); 'no_data' when there is nothing to compare (pct None)."""
    est = est or {}
    mom, years = est.get('momentum') or {}, est.get('years') or []
    fy = mom.get('fiscal_year_end') or (years[0]['fiscal_year_end'] if years else None)
    row = next((y for y in years if y['fiscal_year_end'] == fy), None)
    table_now = row['consensus'] if row else None
    out = {'fiscal_year_end': fy, 'now': None, 'then': None, 'pct': None, 'window_days': None, 'source': None, 'state': 'no_data',
           'notes': []}
    month_pair = mom.get('current') is not None and mom.get('month_ago') is not None
    if month_pair and table_now is not None and abs(mom['current'] - table_now) > MOMENTUM_TABLE_TOLERANCE:
        out['notes'].append('estimate-momentum says the consensus is %g, the forecast table says %g: the month-ago number is not used'
                            % (mom['current'], table_now))
        month_pair = False
    ref = read_on or as_of
    own = []
    if ref and fy and table_now is not None:
        for h in history or ():
            if h.get('fiscal_year_end') != fy or _fnum(h.get('consensus')) is None or not h.get('as_of'):
                continue
            try:
                age = _days(h['as_of'], ref)
            except (TypeError, ValueError):
                continue
            if 0 < age <= REVISION_HISTORY_DAYS:
                own.append((age, h))
    old = None
    for age, h in sorted(own, key=lambda x: -x[0]):                 # the oldest reading on today's per-share scale
        why = _scale_break(est, h, table_now)
        if why is None:
            old = (age, _fnum(h['consensus']))
            break
        out['notes'].append('saved reading not compared (per-share scale changed: a split?): ' + why)
    if old and (old[0] > REVISION_MONTH_DAYS or (not month_pair and old[0] >= REVISION_MIN_OWN_DAYS)):
        out.update(now=table_now, then=old[1], window_days=old[0], source='own_history')
    elif month_pair:
        out.update(now=mom['current'], then=mom['month_ago'], window_days=REVISION_MONTH_DAYS, source='nasdaq_month')
    else:
        return out
    if out['now'] <= 0 or out['then'] <= 0:
        out['state'] = 'not_positive'
    else:
        out.update(pct=out['now'] / out['then'] - 1, state='ok')
    return out


RULE_REVISIONS = ('analysts are not cutting: EPS revisions up minus down over 4 weeks (current and next fiscal year) >= 0 '
                  'and the current-year consensus not more than 1% lower over the window')
RULE_BEATS = ('reported EPS at or above consensus in each of the last two reported quarters (a miss of up to 2%, or of up '
              'to one cent a share, counts as met)')
# why a business gate is missing (gate key 'why'); the Hebrew report words them in MISSING_HE
NOTE = {'no_analyst_data': 'no analyst data',
        'erratic': 'reported EPS swung more than %.0f%% above the consensus in one of the last two quarters and more than '
                   '%.0f%% below it in the other (investment gains or a tiny base): not readable, so not failed'
                   % (ERRATIC_SURPRISE_PCT, ERRATIC_SURPRISE_PCT),
        'no_count': 'no count of raised and cut estimates',
        'no_earlier': 'no earlier consensus to compare with',
        'few_quarters': 'fewer than two reported quarters',
        'no_consensus': 'a reported quarter without EPS or consensus',
        'stale': 'the newest reported quarter (%s) is more than %d days old'}


def _met(q):
    """One reported quarter against its consensus: True (met: a surprise of -2% or better, or a miss of up to one cent
    a share), False (missed), None (EPS or consensus unknown)."""
    eps, cons, pct = q.get('eps'), q.get('consensus'), q.get('surprise_pct')
    if pct is None and (eps is None or cons is None):
        return None
    if pct is not None and pct >= GATES['surprise_min_pct'] - EPS:
        return True
    return eps is not None and cons is not None and eps >= cons - GATES['surprise_cent_tolerance'] - EPS


def business_gates(est, history=(), as_of=None, read_on=None):
    """The two business-momentum gates from one company's parsed analyst record (parse_estimates); est None = nothing was
    fetched or the pages were empty: both gates are missing (pass None, 'why' says which reason), and a row with a
    missing gate is 'incomplete', never a passer, exactly like a missing fundamental. read_on: the day the pages were
    read (default as_of), see eps_revision().

    eps_revisions has two legs: (analysts who raised) - (analysts who cut) over the current and the next fiscal year is
    >= 0, AND the consensus change of eps_revision() is >= -1%. value = that change as a fraction (eps_revision_pct).
    A leg that is known to fail fails the gate (failed_legs: 'count', 'consensus'); the gate is missing only when no
    known leg fails and one is unknown. A consensus that is zero or negative, or that changed sign, has no usable
    percentage: value None, and the up/down count alone decides (note says so).
    earnings_beats: each of the last two quarters reported on or before as_of met its consensus (_met: a surprise of
    -2% or better, or a miss of up to one cent a share). value = the smaller of the two surprises, in percent. A known
    miss fails the gate; otherwise fewer than two reported quarters, or one without numbers: missing. The newest
    quarter older than SURPRISE_STALE_DAYS: missing.
    Erratic EPS: when the last two surprises are both beyond ERRATIC_SURPRISE_PCT and point in opposite directions, the
    reported EPS is not tracking the business (marks on investments, a base of a few cents): BOTH gates are missing
    with why 'erratic', never failed (the full-year consensus absorbs the same swings)."""
    rev = {'gate': 'eps_revisions', 'value': None, 'pass': None, 'rule': RULE_REVISIONS, 'up': None, 'down': None, 'net': None,
           'window_days': None, 'source': None, 'fiscal_year_end': None, 'now': None, 'then': None, 'failed_legs': [],
           'note': None, 'why': None}
    beat = {'gate': 'earnings_beats', 'value': None, 'pass': None, 'rule': RULE_BEATS, 'quarters': [], 'note': None, 'why': None}

    def unknown(g, why, *args):
        g.update(why=why, note=NOTE[why] % args if args else NOTE[why])
    if not est or not (est.get('surprises') or est.get('years') or est.get('momentum')):
        unknown(rev, 'no_analyst_data')
        unknown(beat, 'no_analyst_data')
        return [rev, beat]
    known = [q for q in est.get('surprises') or [] if not (as_of and q.get('date_reported') and q['date_reported'] > as_of)]
    last2 = known[:2]
    beat['quarters'] = last2
    met = [_met(q) for q in last2]
    pcts = [q['surprise_pct'] for q in last2 if q.get('surprise_pct') is not None]
    newest = last2[0].get('date_reported') if last2 else None
    erratic = len(pcts) == 2 and min(pcts) < -ERRATIC_SURPRISE_PCT and max(pcts) > ERRATIC_SURPRISE_PCT
    if as_of and newest and _days(newest, as_of) > SURPRISE_STALE_DAYS:
        unknown(beat, 'stale', newest, SURPRISE_STALE_DAYS)
    elif erratic:
        unknown(beat, 'erratic')
        beat['value'] = min(pcts)
    elif False in met:
        beat.update({'pass': False, 'value': min(pcts) if pcts else None})
    elif len(last2) < 2:
        unknown(beat, 'few_quarters')
    elif None in met:
        unknown(beat, 'no_consensus')
    else:
        beat.update({'pass': True, 'value': min(pcts) if pcts else None})
        if len(pcts) < 2:
            beat['note'] = 'a zero consensus has no surprise percentage: reported EPS is compared with it directly'

    w = eps_revision(est, history, as_of, read_on)
    years = est.get('years') or []
    first = next((i for i, y in enumerate(years) if y['fiscal_year_end'] == w['fiscal_year_end']), 0)
    counted = [y for y in years[first:first + 2] if y.get('up') is not None and y.get('down') is not None]
    if counted:                                 # the fiscal year the consensus change describes, and the one after it
        rev.update(up=sum(y['up'] for y in counted), down=sum(y['down'] for y in counted))
        rev['net'] = rev['up'] - rev['down']
    rev.update({k: w[k] for k in ('window_days', 'source', 'fiscal_year_end', 'now', 'then')}, value=w['pct'])
    if w['notes']:
        rev['note'] = '; '.join(w['notes'])
    legs = [name for name, bad in (('count', rev['net'] is not None and rev['net'] < 0),
                                   ('consensus', w['state'] == 'ok' and w['pct'] < GATES['eps_revision_min'] - EPS)) if bad]
    if erratic:
        unknown(rev, 'erratic')
    elif legs:
        rev.update({'pass': False, 'failed_legs': legs})
    elif rev['net'] is None:
        unknown(rev, 'no_count')
    elif w['state'] == 'no_data':
        rev.update(why='no_earlier', note='; '.join([NOTE['no_earlier']] + w['notes']))
    elif w['state'] == 'not_positive':
        rev['pass'] = True
        rev['note'] = 'consensus EPS zero or negative (or it changed sign): no usable percentage, the up/down count decides'
    else:
        rev['pass'] = True
    return [rev, beat]


def business_columns(business):
    """Flat row keys from the business gates: eps_revision_pct (a fraction), eps_revision_window_days, surprise_min_pct."""
    g = {x['gate']: x for x in business or []}
    return {'eps_revision_pct': (g.get('eps_revisions') or {}).get('value'),
            'eps_revision_window_days': (g.get('eps_revisions') or {}).get('window_days'),
            'surprise_min_pct': (g.get('earnings_beats') or {}).get('value')}


def improving(business):
    """True when the analyst data shows the business getting better, not only 'not worse': both gates pass, more
    analysts raised than cut, the consensus is higher than at the start of the window, and reported EPS was at or above
    the consensus in each of the last two quarters. A gate passes with no analyst move at all, with a consensus up to
    1% lower and with misses of up to 2%; none of those is an improvement."""
    g = {x['gate']: x for x in business or []}
    rev, beat = g.get('eps_revisions'), g.get('earnings_beats')
    if not rev or not beat or rev['pass'] is not True or beat['pass'] is not True:
        return False
    qs = beat.get('quarters') or []
    at_or_above = [q['surprise_pct'] >= 0 if q.get('surprise_pct') is not None
                   else q.get('eps') is not None and q.get('consensus') is not None and q['eps'] >= q['consensus'] for q in qs]
    return (rev.get('net') or 0) > 0 and (rev.get('value') or 0) > 0 and len(qs) == 2 and all(at_or_above)


def divergence(info, business, status=None):
    """The DELL pattern, as information (no status reads it): the price is 25% or more below its 52-week high while the
    business improves (improving(): analysts raise, the consensus is up, the last two quarters were at or above it).
    info: price_info(); business: business_gates(); status: when given, only a 'pass' or 'near_miss' row is flagged."""
    if status is not None and status not in ('pass', 'near_miss'):
        return False
    return bool(info and info.get('below_high') is not None
                and info['below_high'] <= -GATES['divergence_below_high'] + EPS and improving(business))


def failed(gates):
    return [g for g in gates if g['pass'] is False]


def missing(gates):
    return [g for g in gates if g['pass'] is None]


def fail_count(quality, business):
    """Failed conditions of one row. Each quality gate counts; the two analyst gates count ONCE together: a missed
    quarter lowers the full-year consensus by about the same cents and analysts log their cuts, so eps_revisions and
    earnings_beats fail on the same event (the run of 24.11.2026: of the names that reported within 30 days, 33 of the
    45 that missed by more than 2% also failed a revisions leg, against 56 of the 235 that did not miss)."""
    return len(failed(quality)) + (1 if failed(business or []) else 0)


def classify(quality, business):
    """'pass' (no gate failed and none missing across quality + business), 'near_miss' (exactly one failed condition,
    none missing; the two analyst gates are one condition, see fail_count), 'incomplete' (a number is missing and at
    most one condition failed), 'fail'. business None = not fetched because quality already failed twice. Price is not
    an input: no status depends on it."""
    if business is None:
        return 'fail' if len(failed(quality)) >= 2 else 'incomplete'
    n_fail, n_missing = fail_count(quality, business), len(missing(quality + business))
    if n_fail >= 2:
        return 'fail'
    if n_missing:
        return 'incomplete'
    return 'pass' if n_fail == 0 else 'near_miss'


def needs_business(quality):
    """Analyst data (and prices) are fetched only for a company with at most one failed quality gate."""
    return len(failed(quality)) <= 1


def percentile_ranks(values):
    """0-100 per value: share of the others below it, ties sharing the middle. One value alone gets 50."""
    n = len(values)
    if n == 1:
        return [50.]
    return [100. * (sum(1 for w in values if w < v) + 0.5 * (sum(1 for w in values if w == v) - 1)) / (n - 1) for v in values]


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def rank(rows, keys=RANK_KEYS):
    """Adds rank_parts (percentile per component) and rank_score (their plain sum, no weights); best first.
    A row without a number for a RANK_FILL_MEDIAN key (eps_revision_pct: a zero or negative consensus has no percentage)
    is ranked on that component as the median of the others (0 when no row has it) and named in rank_filled."""
    rows = list(rows)
    for r in rows:
        r['rank_parts'], r['rank_filled'] = {}, []
    for k in keys:
        vals = [r.get(k) if k in RANK_FILL_MEDIAN else r[k] for r in rows]
        if None in vals:
            mid = _median([v for v in vals if v is not None])
            for r, v in zip(rows, vals):
                if v is None:
                    r['rank_filled'].append(k)
            vals = [(0. if mid is None else mid) if v is None else v for v in vals]
        for r, p in zip(rows, percentile_ranks(vals) if rows else []):
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


# ---------------------------------------------------------------- earnings radar (who reported, who reports next)

def calendar_days(as_of, back=RADAR_BACK_DAYS, ahead=RADAR_AHEAD_DAYS):
    """The weekdays from `back` calendar days before as_of to `ahead` after it (ISO dates, oldest first)."""
    d0 = _d(as_of)
    days = [d0 + timedelta(days=i) for i in range(-back, ahead + 1)]
    return [d.isoformat() for d in days if d.weekday() < 5]


def parse_calendar(day, payload):
    """One day of the Nasdaq earnings calendar (api/calendar/earnings?date=day; the raw answer, its 'data' node or the
    rows; a weekend or a holiday has none) -> [{date, symbol, name, fiscal_quarter_end 'YYYY-MM', eps, eps_forecast,
    surprise_pct, n_estimates, time}]. eps and surprise_pct are None before the company reports; a surprise the feed
    leaves as 'N/A' is computed from EPS and the forecast."""
    rows = payload if isinstance(payload, list) else _rows(_data(payload))
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get('symbol'):
            continue
        eps, fc = _fnum(r.get('eps')), _fnum(r.get('epsForecast'))
        out.append({'date': day, 'symbol': norm_symbol(r['symbol']), 'name': (r.get('name') or '').strip(),
                    'fiscal_quarter_end': _month(r.get('fiscalQuarterEnding')), 'eps': eps, 'eps_forecast': fc,
                    'surprise_pct': _surprise(eps, fc, _fnum(r.get('surprise'))) if eps is not None else None,
                    'n_estimates': _int(r.get('noOfEsts')), 'time': r.get('time')})
    return out


def earnings_radar(calendar, rows, book, as_of):
    """Two lists from the earnings calendar. calendar: parse_calendar rows of the days around as_of; rows: the screen's
    rows with their status; book: the thesis-book symbols.
    reported (the last RADAR_BACK_DAYS days, EPS published): universe companies whose status is pass or near_miss and
    whose EPS surprise is +5% or more, plus every thesis-book company that reported, whatever the surprise. A
    thesis-book company on a past day of the calendar without an EPS number is listed too (eps None): the calendar has
    the day and not yet the result.
    upcoming (the next RADAR_AHEAD_DAYS days, and today when EPS is not out yet): passers, near misses and thesis-book
    companies with their date. A company is matched by its screened symbol or another share class of it.
    in_quality says whether the row's quality numbers already cover the reported quarter (the period the SEC data ends
    at against the calendar's fiscal quarter; None when either is unknown). The lists say who to look at, they do not
    rescore anyone."""
    by = {}
    for r in rows:
        by[r['symbol']] = r
    for r in rows:
        for o in r.get('other_classes') or []:
            by.setdefault(o, r)
    booked = {norm_symbol(b) for b in book or ()}
    reported, upcoming, seen = [], [], set()
    for c in sorted(calendar, key=lambda x: (x['date'], x['symbol'])):
        r = by.get(c['symbol'])
        symbol = r['symbol'] if r else c['symbol']
        in_book = symbol in booked or c['symbol'] in booked
        listed = bool(r and r.get('in_universe') and r.get('status') in ('pass', 'near_miss'))
        age = _days(c['date'], as_of)                           # days since the report; negative = still ahead
        if (symbol, c['date']) in seen or not (in_book or listed):
            continue
        fails = (r or {}).get('failed') or []
        near = (r or {}).get('status') == 'near_miss'
        period, quarter = (r or {}).get('period_end'), c['fiscal_quarter_end']
        item = {'date': c['date'], 'symbol': symbol, 'name': (r or {}).get('name') or c['name'], 'fiscal_quarter_end': quarter,
                'eps': c['eps'], 'eps_forecast': c['eps_forecast'], 'surprise_pct': c['surprise_pct'], 'n_estimates': c['n_estimates'],
                'time': c['time'], 'status': (r or {}).get('status'), 'route': (r or {}).get('route'),
                'in_universe': bool(r and r.get('in_universe')), 'in_book': in_book,
                'failed_gate': fails[0]['gate'] if len(fails) == 1 else None, 'failed': fails[0] if len(fails) == 1 else None,
                'failed_gates': [f['gate'] for f in fails] if near else [],
                'in_quality': period[:7] >= quarter if period and quarter else None}
        if 0 <= age <= RADAR_BACK_DAYS and (c['eps'] is not None or (in_book and age > 0)):
            if in_book or (c['surprise_pct'] is not None and c['surprise_pct'] >= RADAR_MIN_SURPRISE_PCT - EPS):
                reported.append(item)
                seen.add((symbol, c['date']))
        elif -RADAR_AHEAD_DAYS <= age < 0 or (age == 0 and c['eps'] is None):
            upcoming.append(item)
            seen.add((symbol, c['date']))
    reported.sort(key=lambda x: (x['date'], x['surprise_pct'] if x['surprise_pct'] is not None else -1e9), reverse=True)
    upcoming.sort(key=lambda x: (x['date'], x['symbol']))
    return {'reported': reported, 'upcoming': upcoming}


# ---------------------------------------------------------------- saved valuations (the MSFT slip, 06.11.2026)

SAVED_STATES = ('pass', 'fail', 'unknown', 'none')
PRICE_SCALE_LIMIT = 1.8      # a screen price this far from the valuation's: a split or a unit gap first (forecast_tracker's guard)
BASIS = {
    'lean_rerun': "the saved lean params rerun with lean_model at the screen's price",
    'record_price': "the saved record's buy-test price (custom model, not rerun)",
    'handoff_price': "the handoff's buy-test price (custom model, not rerun)",
    'rejected_price': 'the buy-test price saved with the rejected check (not rerun at this price)',
    'failed_lower': 'failed the buy test at a lower price; a higher price only lowers the returns',
    'no_test_price': 'the saved model has no buy-test price and failed nowhere below this price: rerun it before proposing',
    'engine': 'a heavy-engine valuation: no buy-test price to compare',
    'no_price': 'no price in the screen',
    'scale': f'the price moved more than {PRICE_SCALE_LIMIT}x since the valuation (a split or a unit gap?): check before comparing',
    'error': 'the saved lean params did not run',
}


def lean_module():
    """scripts/lean_model.py of this checkout (run, reprice), or None when the script is not there."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'lean_model.py')
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('lean_model', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def saved_book(research_root):
    """What the research folder remembers, by normalized symbol: {'root', 'folders': {symbol: folder name},
    'rejected': {symbol: [rows of research/rejected.jsonl, oldest first]}}. Missing files are empty."""
    folders, rejected = {}, {}
    base = os.path.join(research_root, valuation_record.STORE)
    if os.path.isdir(base):
        for t in sorted(os.listdir(base)):
            if os.path.isdir(os.path.join(base, t)):
                folders[norm_symbol(t)] = t
    path = os.path.join(research_root, 'rejected.jsonl')
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    rejected.setdefault(norm_symbol(row.get('ticker')), []).append(row)
    for rows in rejected.values():
        rows.sort(key=lambda r: r.get('date') or '')
    return {'root': research_root, 'folders': folders, 'rejected': rejected}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _valued(root, ticker, run):
    """The resolver's newest folder; when it holds no lean or custom valuation (a heavy-engine or gather-only folder), the
    newest older folder that does, with the newer folder named in the note."""
    lv = valuation_record.latest_valuation(root, ticker, run=run)
    if lv is None or lv['record'] is not None:
        return lv
    for d in reversed(lv['older_folders']):
        folder = os.path.join(os.path.dirname(lv['folder']), d)
        if lean_freeze.classify(folder)['kind'] is None:
            continue
        try:
            rec, spec = valuation_record.record_for_folder(folder, root, run=run)
        except (ValueError, KeyError):
            continue
        if rec is not None:
            return dict(lv, as_of=d, folder=folder, kind=spec['kind'], model=spec.get('model'), record=rec,
                        record_source='built in memory from the saved files',
                        note=f"the newest folder {lv['as_of']} is {lv['kind']} (no buy test); the newest valuation is {d}")
    return lv


def _compare(out, price, test_price, basis):
    out.update(buy_test_price=test_price, buy_test_pass=price <= test_price, basis=basis)


def _from_rejected(out, rej, price, as_of):
    out.update(date=rej.get('date'), kind='rejected', model=rej.get('model'), base_value=rej.get('base_value_today'),
               price_then=rej.get('price'), buy_test_price_saved=rej.get('buy_test_price'), verdict=rej.get('verdict'),
               source='research/rejected.jsonl')
    if price is None or not _is_num(rej.get('price')):
        out['basis'] = 'no_price'
    elif not 1 / PRICE_SCALE_LIMIT < price / rej['price'] < PRICE_SCALE_LIMIT:
        out['basis'] = 'scale'
    elif _is_num(rej.get('buy_test_price')):
        _compare(out, price, rej['buy_test_price'], 'rejected_price')
    elif rej.get('buy_test_pass') is False and price >= rej['price']:
        out.update(buy_test_pass=False, basis='failed_lower')
    else:
        out['basis'] = 'no_test_price'


def _from_folder(out, lv, price, price_date, lean, root):
    rec = lv['record'] or {}
    out.update(date=lv['as_of'], kind=lv['kind'], model=lv.get('model'),
               source=os.path.relpath(lv['folder'], os.path.dirname(os.path.abspath(root))))
    if lv.get('note'):
        out['note'] = lv['note']
    if not rec:
        out['basis'] = 'engine' if lv['kind'] == 'engine' else 'error'
        return
    price_then = (rec.get('price') or {}).get('value')
    bt_saved = (rec.get('hurdles') or {}).get('buy_test_price')
    out.update(base_value=((rec.get('scenarios') or {}).get('base') or {}).get('value_today'), price_then=price_then,
               buy_test_price_saved=bt_saved, verdict=(rec.get('verdict') or {}).get('code'),
               model=(rec.get('model') or {}).get('kind') or out['model'])
    if price is None or not _is_num(price_then):
        out['basis'] = 'no_price'
        return
    if not 1 / PRICE_SCALE_LIMIT < price / price_then < PRICE_SCALE_LIMIT:
        out['basis'] = 'scale'
        return
    spec = lean_freeze.classify(lv['folder'])
    if (spec['kind'] == 'lean' or spec.get('model') == 'remapped_lean') and lean is not None:
        params = valuation_record.load(os.path.join(lv['folder'], spec['params']))
        later = price_date if price_date and str(price_date) > str(params.get('as_of')) else None
        # weighted worlds (worlds.json beside lean_params.json, or the params' own key): the published buy test is the
        # weighted one (valuation_record), so the screen tests that one too
        wspec, wdir = (valuation_record.worlds_spec(lv['folder'], params)
                       if spec['params'] == 'lean_params.json' or params.get('worlds') is not None else (None, None))
        crule = (valuation_record.calibrated_rule(lv['folder'])[0]
                 if wspec is None and spec['params'] == 'lean_params.json' else None)
        try:
            if crule is not None:                    # the published second lens is the one with the rule (FIVE)
                params = lean.apply_calibrated_rule(params, crule)
            q = lean.reprice(params, price=price, as_of=later)
            bt = (lean.run(q, worlds=wspec, base_dir=wdir)['worlds']['weighted']['buy_test'] if wspec is not None
                  else lean.run(q)['buy_test'])
        except Exception as e:                                      # noqa: BLE001 (a file that does not run is a recorded gap)
            out.update(basis='error', note=f'{type(e).__name__}: {str(e)[:200]}')
        else:
            rerun = bt.get('rerun') or {}
            warns = bool(rerun.get('lower_than_stated'))
            out.update(buy_test_pass=bt.get('pass'), basis='lean_rerun',
                       buy_test_price=rerun.get('max_price_passing') if warns else bt.get('max_price_passing'),
                       binding_leg=rerun.get('binding_leg') if warns else bt.get('binding_leg'),
                       repriced={'dates_moved': q['repriced'].get('dates_moved'), 'note': q['repriced'].get('note')})
            if bt.get('no_price_passes'):
                out['note'] = bt['no_price_passes']
            return
    if _is_num(bt_saved):
        _compare(out, price, bt_saved, 'record_price')
        return
    hand = os.path.join(root, valuation_record.TECH, os.path.basename(os.path.dirname(lv['folder'])), lv['as_of'], 'fundamental.json')
    hp = valuation_record.load(hand).get('buy_test_price') if os.path.isfile(hand) else None
    if _is_num(hp):
        _compare(out, price, hp, 'handoff_price')
    elif (rec.get('model_reading') or {}).get('buy_test_pass') is False and price >= price_then:
        out.update(buy_test_pass=False, basis='failed_lower')
    elif out['basis'] != 'error':
        out['basis'] = 'no_test_price'


def saved_valuation(book, symbol, price, as_of, price_date=None, lean=None):
    """The saved valuation of one screened name, tested at the screen's price. The search rule of 15.11.2026: never propose
    a name whose saved valuation fails the buy test (the MSFT slip of 06.11.2026), names rejected earlier included.

    book: saved_book(research_root). price: the screen's close (the listing price when no bars were read). as_of: the
    screen's date (the age). price_date: the day of that close; the saved params' dates move to it only when it is later
    than their as_of and the file has base_year_end (lean_model.reprice), else only the price changes. lean: the lean_model
    module (run, reprice); default this checkout's script.
    The newest of the research folder's valuation and the newest research/rejected.jsonl row is tested; the other is kept
    as context. state: pass | fail (the buy test at the screen's price) | unknown (a valuation exists but no buy-test price
    can be compared: see basis) | none (nothing saved). The buy-test price follows the record's rule: the rerun price when
    lean_model warns that a rerun at the stated price fails."""
    sym = norm_symbol(symbol)
    rej_rows = book['rejected'].get(sym) or []
    rej = rej_rows[-1] if rej_rows else None
    out = {'state': 'none', 'date': None, 'kind': None, 'model': None, 'age_days': None, 'base_value': None, 'price_then': None,
           'price': price, 'price_date': price_date, 'buy_test_pass': None, 'buy_test_price': None, 'buy_test_price_saved': None,
           'binding_leg': None, 'basis': None, 'verdict': None, 'source': None, 'note': None,
           'rejected': None if rej is None else {k: rej.get(k) for k in ('date', 'price', 'model', 'verdict', 'buy_test_price',
                                                                          'margin_of_safety_price_13pct', 'source')}}
    ticker = book['folders'].get(sym)
    if lean is None:
        lean = lean_module()
    lv = _valued(book['root'], ticker, lean.run if lean else None) if ticker else None
    if lv is not None and lv['kind'] == 'gather_only':
        out['note'] = f"research folder {lv['as_of']} holds gathered data only, no valuation"
        lv = None
    if rej is not None and (lv is None or str(rej.get('date') or '') > str(lv['as_of'])):
        if lv is not None:
            out['note'] = f"an older research valuation exists ({lv['as_of']}); the newer rejected check is tested"
        _from_rejected(out, rej, price, as_of)
    elif lv is not None:
        _from_folder(out, lv, price, price_date, lean, book['root'])
    else:
        return out
    if out['date']:
        out['age_days'] = _days(out['date'], as_of)
    out['state'] = 'unknown' if out['buy_test_pass'] is None else 'pass' if out['buy_test_pass'] else 'fail'
    out['basis_text'] = BASIS.get(out['basis'])
    return out


def attach_saved(rows, book, as_of, price_date=None, lean=None):
    """rows get 'saved_valuation'; price_date applies to rows whose price is a close of that day (trend bars read)."""
    if lean is None:
        lean = lean_module()
    for r in rows:
        r['saved_valuation'] = saved_valuation(book, r['symbol'], r.get('price') or None, as_of,
                                               price_date if r.get('trend_measures') else None, lean)
    return rows


# ---------------------------------------------------------------- Hebrew report

GATE_HE = {'rev_growth_ttm': 'צמיחת הכנסות ב-12 חודשים', 'rev_growth_q': 'צמיחת הרבעון האחרון מול שנה קודם',
           'durability': 'צמיחה עקבית בשלוש השנים האחרונות', 'operating_income': 'רווח תפעולי חיובי', 'margin_change_pts': 'שולי רווח תפעולי לא יורדים',
           'fcf': 'תזרים מזומנים חופשי חיובי', 'share_change': 'מספר המניות לא גדל מעל 3%',
           'eps_revisions': 'אנליסטים לא מורידים תחזיות רווח', 'earnings_beats': 'עמידה בתחזית הרווח בשני הרבעונים האחרונים',
           'roe': 'תשואה על ההון 15% ומעלה', 'bvps_growth': 'הון עצמי למניה גדל'}
PCT_GATES = ('rev_growth_ttm', 'rev_growth_q', 'share_change', 'roe', 'bvps_growth')
BUSINESS_GATES = ('eps_revisions', 'earnings_beats')
# a failed eps_revisions gate is named by the half that failed (failed_legs)
LEG_HE = {'count': 'יותר אנליסטים הורידו את תחזית הרווח מאשר העלו', 'consensus': 'תחזית הרווח הממוצעת ירדה ביותר מ-1%'}
# why a business gate is missing (gate key 'why')
MISSING_HE = {'no_analyst_data': 'אין נתוני אנליסטים', 'erratic': 'לא קריא: הרווח למניה קפץ מעל התחזית ומתחתיה',
              'no_count': 'אין ספירה של העלאות והורדות', 'no_earlier': 'אין תחזית קודמת להשוואה',
              'few_quarters': 'פחות משני דוחות', 'no_consensus': 'דוח בלי תחזית להשוואה', 'stale': 'הדוח האחרון ישן מדי'}
MARK = '◆'                                      # a row flagged 'price_down_business_up'
STATUS_HE = {'pass': 'עברה', 'near_miss': 'כמעט', 'fail': 'נכשלה', 'incomplete': 'חסר נתון', 'not_found': 'לא נמצאה'}
ROUTE_HE = {'standard': 'רגיל', 'financial': 'פיננסים', 'annual_only': 'שנתי בלבד', 'commodity': 'סחורות'}
TIME_HE = {'time-pre-market': 'לפני המסחר', 'time-after-hours': 'אחרי המסחר'}
SMALL_EPS = 1.0                                 # a consensus under one dollar a share: its percentage change is named as coarse


def _pct(x, signed=False):
    return '—' if x is None else (f'{x * 100:+.0f}%' if signed else f'{x * 100:.0f}%')


def _x(x, d=1):
    return '—' if x is None else f'{x:.{d}f}'


def _cap(x):
    return '—' if not x else (f'{x / 1e12:.2f}T$' if x >= 1e12 else f'{x / 1e9:.0f}B$')


def _signed(x, d=1):
    """'+3.2%'; a number that rounds to zero prints '0.0%' with no sign."""
    if x is None:
        return '—'
    return f'{0:.{d}f}%' if round(x, d) == 0 else f'{x:+.{d}f}%'


def revision_he(g):
    """The eps_revisions gate in words: the consensus change with its window, and how many analysts raised and cut."""
    if not g or (g.get('value') is None and g.get('net') is None):
        return 'אין נתון'
    bits = []
    if g.get('value') is not None:
        bits.append(f"{_signed(g['value'] * 100)} ב-{g['window_days']} יום")
    elif g.get('now') is not None:
        bits.append('בלי אחוז (תחזית רווח אפס או שלילית)')
    if g.get('net') is not None:
        bits.append(f"{g['up']} העלו, {g['down']} הורידו")
    return '; '.join(bits)


def beats_he(g):
    """The earnings_beats gate in words: the EPS surprise of the last two reported quarters, the newest first."""
    qs = (g or {}).get('quarters') or []
    if not qs:
        return 'אין נתון'
    return ' / '.join(_signed(q['surprise_pct']) if q.get('surprise_pct') is not None else
                      ('—' if q.get('eps') is None or q.get('consensus') is None else f"{q['eps']:.2f} מול {q['consensus']:.2f}")
                      for q in qs)


def gate_name_he(g):
    """A gate's name for the 'what failed' columns; a failed eps_revisions gate is named by the half that failed."""
    if g['gate'] == 'eps_revisions' and g.get('pass') is False and g.get('failed_legs'):
        return ' וגם '.join(LEG_HE[x] for x in g['failed_legs'])
    return GATE_HE[g['gate']]


def missing_he(g):
    """A missing gate for the 'what is missing' columns: its name, and the reason when the analyst data gives one."""
    why = MISSING_HE.get(g.get('why'))
    return GATE_HE[g['gate']] + (f' ({why})' if why else '')


def gate_value_he(g):
    v = g['value']
    if g['gate'] == 'eps_revisions':
        return revision_he(g)
    if g['gate'] == 'earnings_beats':
        return beats_he(g)
    if v is None:
        return 'אין נתון'
    if g['gate'] == 'margin_change_pts':
        return f'{v:+.1f} נק׳'
    if g['gate'] == 'durability':
        return f"{v * 100:+.1f}% בשנה, השנה החלשה {g.get('worst_year', 0) * 100:+.1f}%"
    if g['gate'] in PCT_GATES:
        return f'{v * 100:+.1f}%'
    return f'{v / 1e9:+.2f} מיליארד'


def _table(head, lines):
    return ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(str(c) for c in ln) + ' |' for ln in lines]


SAVED_HE = {'pass': 'עוברת', 'fail': 'לא עוברת', 'unknown': 'לא ידוע', 'none': 'אין'}
BASIS_HE = {'lean_rerun': 'המודל השמור הורץ מחדש במחיר החיפוש',
            'record_price': 'מחיר מבחן הקנייה השמור (מודל מותאם, לא הורץ מחדש)',
            'handoff_price': 'מחיר מבחן הקנייה מקובץ ההעברה (מודל מותאם, לא הורץ מחדש)',
            'rejected_price': 'מחיר מבחן הקנייה שנשמר עם הבדיקה שנדחתה (לא הורץ מחדש)',
            'failed_lower': 'נכשלה במבחן במחיר נמוך יותר; מחיר גבוה יותר רק מוריד את התשואה',
            'no_test_price': 'אין במודל מחיר מבחן להשוואה: להריץ מחדש לפני שמציעים',
            'engine': 'הערכה של המנוע הכבד, בלי מחיר מבחן קנייה',
            'no_price': 'אין מחיר בחיפוש',
            'scale': f'המחיר זז יותר מפי {PRICE_SCALE_LIMIT} מאז ההערכה (פיצול או יחידה אחרת?): לבדוק לפני השוואה',
            'error': 'המודל השמור לא רץ'}
KIND_HE = {'lean': 'מודל רזה', 'custom': 'מודל מותאם', 'engine': 'מנוע כבד', 'rejected': 'בדיקה שנדחתה'}


def _dmy(iso):
    return '—' if not iso else f'{iso[8:10]}.{iso[5:7]}.{iso[:4]}'


def _money(x):
    return '—' if x is None else f'{x:,.2f}'


def saved_he(sv):
    """The column 'הערכה שמורה': עוברת / לא עוברת / לא ידוע / אין, with the date and the buy-test price."""
    if not sv:
        return '—'
    if sv['state'] == 'none':
        return 'אין'
    word = SAVED_HE[sv['state']]
    bits = [('נדחתה ' if sv['kind'] == 'rejected' else '') + _dmy(sv['date'])]
    if sv.get('buy_test_price') is not None:
        bits.append('מחיר מבחן ' + _money(sv['buy_test_price']))
    elif sv['state'] == 'unknown':
        bits.append(BASIS_HE.get(sv.get('basis'), '—'))
    return ('**%s**' % word if sv['state'] == 'fail' else word) + ' (' + ', '.join(bits) + ')'


def _bgate(r, name):
    return next((g for g in r.get('business') or [] if g['gate'] == name), None)


def _sym(r):
    """The symbol, marked when the row is flagged 'the price fell, the business improves'."""
    return r['symbol'] + (' ' + MARK if r.get('divergence') else '')


def _biz(r):
    """The two business columns: the change in the EPS forecast (with its window) and the EPS surprise of two quarters."""
    rev, beat = _bgate(r, 'eps_revisions'), _bgate(r, 'earnings_beats')
    return ['—' if rev is None else revision_he(rev), '—' if beat is None else beats_he(beat)]


def _price(r):
    """The two price columns, information only: the 12-1 return and the distance from the 52-week high."""
    t = r.get('trend_measures') or {}
    return [_pct(t.get('ret_12_1'), True), _pct(t.get('below_high'), True)]


def _std_line(r):
    return [_sym(r), r['name'][:28], r.get('sector') or '—', _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')),
            _pct(r.get('rev_growth_q')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
            '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}" + ('*' if r.get('margin_jump') else ''),
            _pct(r.get('fcf_margin')), _pct(r.get('share_change'), True)] + _biz(r) + _price(r) + [
            _x(r.get('pe'), 0), _x(r.get('ps')), _pct(r.get('fcf_yield'))]


def _period(r):
    return (r.get('period_end') or '—') + (' (ישן)' if r.get('stale') else '')


BIZ_HEAD = ['שינוי בתחזית הרווח', 'הפתעה ברווח, 2 רבעונים']
PRICE_HEAD = ['תשואה 12-1 (מידע)', 'מרחק מהשיא (מידע)']
STD_HEAD = ['סימול', 'חברה', 'ענף', 'שווי שוק', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי',
            'שינוי מניות'] + BIZ_HEAD + PRICE_HEAD + ['מכפיל רווח', 'מכפיל מכירות', 'תשואת תזרים']


LIST_HE = (('passers', 'עברה'), ('near_misses', 'כמעט'), ('financials', 'פיננסים'), ('financial_near_misses', 'פיננסים, כמעט'),
           ('commodity', 'סחורות'), ('annual_only', 'שנתי בלבד'), ('unread', 'חסר נתון'))


def saved_rows(res):
    """(list label, row) for every listed name that has a saved valuation, in the report's order, each name once."""
    seen, out = set(), []
    for key, label in LIST_HE:
        for r in res.get(key) or []:
            sv = r.get('saved_valuation')
            if sv and sv['state'] != 'none' and r['symbol'] not in seen:
                seen.add(r['symbol'])
                out.append((label, r))
    return out


def _saved_warning(res):
    """The lines under the headline: listed names whose saved valuation fails the buy test, or cannot be compared."""
    L = []
    for state, text in (('fail', '**הערכה שמורה שנכשלת במבחן הקנייה במחיר של היום** (לא מציעים אותן בלי הערכה חדשה אחרי דוח או '
                                 'עובדה מהותית; הכלל מאז טעות MSFT, 06.11.2026)'),
                        ('unknown', 'יש הערכה שמורה, אבל בלי מחיר מבחן קנייה שאפשר להשוות אליו (לבדוק לפני שמציעים)')):
        parts = []
        for key, label in (('passers', 'מהעוברות'), ('near_misses', 'מאלה שכמעט עברו')):
            names = [r['symbol'] for r in res.get(key) or [] if (r.get('saved_valuation') or {}).get('state') == state]
            if names:
                parts.append(f"{label}: {', '.join(names)}")
        if parts:
            L.append(f"{text}: {'; '.join(parts)}.")
    return L


def _one_condition(f):
    """True when the failed gates are one failed condition: one gate, or the two analyst gates together (fail_count)."""
    return len(f) == 1 or (len(f) == 2 and all(g['gate'] in BUSINESS_GATES for g in f))


def _fail_he(r):
    """'נכשלה ב: <gate> (<number>)' for a near miss (both analyst gates when the pair failed); '' when nothing failed or
    more than one condition did."""
    f = r.get('failed') or []
    return 'נכשלה ב: ' + '; '.join(f'{gate_name_he(g)} ({gate_value_he(g)})' for g in f) if f and _one_condition(f) else ''


def _missing_list_he(gates):
    """The missing gates of one row in words; the two analyst gates missing for one reason are named once."""
    biz = [g for g in gates if g['gate'] in BUSINESS_GATES]
    out = [GATE_HE[g['gate']] for g in gates if g['gate'] not in BUSINESS_GATES]
    if len(biz) == 2 and biz[0].get('why') == biz[1].get('why'):
        why = MISSING_HE.get(biz[0].get('why'))
        out.append('שני תנאי האנליסטים' + (f' ({why})' if why else ''))
    else:
        out += [missing_he(g) for g in biz]
    return ', '.join(out)


def _radar_status(x):
    """Where a radar company stands in the screen, in a few words."""
    if x.get('status') is None:
        return 'לא נסרקה'
    word = STATUS_HE[x['status']]
    gates = x.get('failed_gates') or ([x['failed_gate']] if x.get('failed_gate') else [])
    if x['status'] == 'near_miss' and gates:
        word += ' (נכשל: ' + '; '.join(GATE_HE[g] for g in gates) + ')'
    return word + ('' if x.get('in_universe') else ', מתחת לסף השווי')


def _eps(x):
    return '—' if x is None else f'{x:.2f}$'


def _radar_he(res):
    """The earnings radar: who reported in the last days and who reports in the next."""
    radar = res.get('radar')
    if radar is None:
        return []
    rep, up = radar.get('reported') or [], radar.get('upcoming') or []
    yes_no = {True: 'כן', False: 'לא', None: '—'}
    L = ['', '## רדאר דוחות', '',
         'מי דיווחה לאחרונה ומי מדווחת בקרוב, לפי לוח הדוחות של Nasdaq. הרדאר אומר על מי להסתכל, הוא לא מחשב מחדש את התנאים. '
         'דוח חדש מגיע לנתוני ה-SEC רק כמה ימים אחרי הפרסום. העמודה "מספרי האיכות כוללים את הדוח" אומרת לכל חברה אם מספרי '
         'האיכות בטבלאות כבר כוללים את הדוח הזה ("כן"), או שהם עדיין מהרבעון שלפניו ("לא").', '',
         f'### דיווחו ב-{RADAR_BACK_DAYS} הימים האחרונים ({len(rep)})', '',
         f'חברות שעברו או כמעט עברו את התנאים והפתיעו ברווח למניה ב-{RADAR_MIN_SURPRISE_PCT:.0f}% ומעלה, וכל חברה מספר התזות '
         'שדיווחה (בכל הפתעה).', '']
    if rep:
        L += _table(['תאריך הדוח', 'סימול', 'חברה', 'רבעון עד', 'רווח למניה', 'תחזית', 'הפתעה', 'מספרי האיכות כוללים את הדוח',
                     'מצב בחיפוש', 'בספר התזות'],
                    [[_dmy(x['date']), x['symbol'], (x.get('name') or '')[:28], x.get('fiscal_quarter_end') or '—', _eps(x.get('eps')),
                      _eps(x.get('eps_forecast')), _signed(x.get('surprise_pct')), yes_no[x.get('in_quality')], _radar_status(x),
                      'כן' if x.get('in_book') else '—'] for x in rep])
        if any(x.get('eps') is None for x in rep):
            L += ['', 'חברה מספר התזות בלי רווח למניה בטבלה ("—"): התאריך שלה בלוח עבר, והתוצאה עדיין לא רשומה שם. '
                  'לבדוק בהודעת החברה אם הדוח יצא או נדחה.']
    else:
        L += ['אין חברות כאלה.']
    L += ['', f'### מדווחות ב-{RADAR_AHEAD_DAYS} הימים הקרובים ({len(up)})', '',
          'חברות שעברו או כמעט עברו את התנאים, וחברות מספר התזות, עם תאריך הדוח הצפוי. תאריכים עתידיים יכולים להשתנות.', '']
    if up:
        L += _table(['תאריך', 'מתי', 'סימול', 'חברה', 'רבעון עד', 'תחזית רווח למניה', 'מספר תחזיות', 'מצב בחיפוש', 'בספר התזות'],
                    [[_dmy(x['date']), TIME_HE.get(x.get('time'), '—'), x['symbol'], (x.get('name') or '')[:28], x.get('fiscal_quarter_end') or '—',
                      _eps(x.get('eps_forecast')), '—' if x.get('n_estimates') is None else x['n_estimates'], _radar_status(x),
                      'כן' if x.get('in_book') else '—'] for x in up])
    else:
        L += ['אין חברות כאלה.']
    if radar.get('gaps'):
        L += ['', 'ימים שלוח הדוחות שלהם לא נקרא: ' + ', '.join(radar['gaps']) + '.']
    return L


def divergence_rows(lists):
    """The rows flagged 'price_down_business_up', in the order the report shows them. lists: [(key, Hebrew label, rows)]
    in the report's order. Passers keep their rank order; the other lists are ordered by revenue growth over the last 12
    months, highest first (where the business grows fastest now). Each row is a copy with 'list' and 'list_he'."""
    out = []
    for key, label, rows in lists:
        flagged = [r for r in rows if r.get('divergence')]
        if key != 'passers':
            flagged.sort(key=lambda r: (-(r.get('rev_growth_ttm') or 0), r['symbol']))
        out += [dict(r, list=key, list_he=label) for r in flagged]
    return out


BIZ_COLUMNS_HE = ('"שינוי בתחזית הרווח": בכמה אחוזים השתנתה תחזית הרווח למניה לשנת הכספים הנוכחית, ובכמה ימים, וכמה אנליסטים '
                  'העלו והורידו תחזית ב-4 השבועות האחרונים. "הפתעה ברווח": הרווח שדווח מול התחזית בשני הרבעונים האחרונים, החדש קודם.')


def _divergence_he(res, top=DIVERGENCE_TOP):
    """The short section at the top: listed names whose price fell while the business improves (the DELL pattern).
    The table holds up to `top` passers and `top` near misses; the rest, and the other lists, are named in one line each."""
    dv = res.get('divergence')
    if dv is None:
        return []
    limit = GATES['divergence_below_high'] * 100

    def named(rows):
        return ', '.join(f"{r['symbol']} ({_pct((r.get('price_info') or {}).get('below_high'), True)})" for r in rows)
    L = [f'## המחיר ירד, העסק משתפר ({len(dv)})', '',
         f'חברות מהרשימות שלמטה שהמניה שלהן נמצאת {limit:.0f}% ומעלה מתחת לשיא של 52 שבועות, ובאותו זמן נתוני האנליסטים מראים '
         'שיפור: יותר אנליסטים העלו את תחזית הרווח מאשר הורידו, התחזית הממוצעת עלתה, והרווח שדווח היה שווה לתחזית או מעליה בשני '
         f'הדוחות האחרונים. זה הדפוס של DELL: עסק טוב שהמניה שלו ירדה יחד עם הענף. הן מסומנות {MARK} גם בטבלאות. '
         'זה מידע, לא המלצה: צריך לבדוק למה המחיר ירד.', '']
    if not dv:
        return L + ['אין היום חברות כאלה ברשימות.', '']
    groups = [[r for r in dv if r.get('list') == key] for key in ('passers', 'near_misses')]
    other = [r for r in dv if r.get('list') not in ('passers', 'near_misses')]
    shown = groups[0][:top] + groups[1][:top]
    if shown:
        L += _table(['סימול', 'חברה', 'רשימה', 'מרחק מהשיא', 'שינוי בתחזית הרווח', 'הפתעה ברווח, 2 רבעונים', 'צמיחה 12ח׳', 'מה נכשל',
                     'הערכה שמורה'],
                    [[r['symbol'], r['name'][:28], r.get('list_he') or '—', _pct((r.get('price_info') or {}).get('below_high'), True)] + _biz(r)
                     + [_pct(r.get('rev_growth_ttm')), _fail_he(r).replace('נכשלה ב: ', '') or '—', saved_he(r.get('saved_valuation'))]
                     for r in shown])
        L += ['', BIZ_COLUMNS_HE,
              '', f'בטבלה: עד {top} מהעוברות (לפי הדירוג) ועד {top} מאלה שכמעט עברו (לפי צמיחת ההכנסות ב-12 החודשים האחרונים, '
              'הגבוהה קודם). בסוגריים בשורות שלמטה: המרחק מהשיא.']
    for label, rows in (('עוד מהעוברות', groups[0][top:]), ('עוד מאלה שכמעט עברו', groups[1][top:]),
                        ('ברשימות האחרות (פיננסים, זרות עם נתונים שנתיים)', other)):
        if rows:
            L += ['', f'{label} ({len(rows)}): {named(rows)}.']
    return L + ['']


def _where_he(res, symbol):
    """Where one symbol stands in this run, in a few words; '' when it is in no list."""
    for r in res.get('passers') or []:
        if r['symbol'] == symbol:
            return 'עברה את כל התנאים'
    for r in res.get('near_misses') or []:
        if r['symbol'] == symbol:
            return 'כמעט עברה (' + _fail_he(r) + ')'
    for r in res.get('thesis_book') or []:
        if r['symbol'] == symbol and r.get('status') in STATUS_HE:
            return STATUS_HE[r['status']]
    return ''


def _paypal_he(res):
    """What this run says about PayPal, the owner's example of a business that weakened; '' when it was not screened or
    fails fewer than two quality gates."""
    r = next((x for x in res.get('rows') or [] if x.get('symbol') == 'PYPL'), None)
    n = len(failed(r.get('quality') or [])) if r else 0
    if n < 2:
        return ''
    b = r.get('business')
    both = ', אף שהיא עוברת את שני תנאי האנליסטים' if b and all(g['pass'] is True for g in b) else ''
    return f' בהרצה הזאת PayPal נכשלת ב-{n} תנאי איכות{both}.'


def _near_line(r):
    """One row of the near-miss table. The number of a failed analyst gate is already in the two analyst columns."""
    f = r['failed']
    nums = list(dict.fromkeys('בעמודות האנליסטים' if g['gate'] in BUSINESS_GATES else gate_value_he(g) for g in f))
    return [_sym(r), r['name'][:28], _cap(r['market_cap']), '; '.join(gate_name_he(g) for g in f), '; '.join(nums),
            _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin'))] + _biz(r) + _price(r) + [
            saved_he(r.get('saved_valuation'))]


def render_he(res, top=40):
    """screen_he.md: the headline, the names whose price fell while the business improves, what the screen is and is not,
    the conditions, passers, near misses, the earnings radar, commodity names, financials, annual-only filers, thesis-book
    names, the saved valuations of the listed names, and the data gaps. Price columns are labelled information."""
    c, spy = res['counts'], res.get('spy') or {}
    warn = _saved_warning(res)
    read = res.get('analyst_read') or {}
    read_he = (_dmy(read['last']) if read.get('first') == read.get('last') else f"{_dmy(read.get('first'))} עד {_dmy(read['last'])}") \
        if read.get('last') else ''
    L = [f"# חיפוש מניות: איכות העסק + תנופה עסקית ({res['as_of']})", '',
         f"נסרקו {c['companies']} חברות בשווי שוק מעל {res['min_cap'] / 1e9:.0f} מיליארד דולר. "
         f"עברו את כל התנאים: **{c['passers']}**. כמעט עברו (נכשלו בתנאי אחד בלבד): {c['near_misses']}. "
         f"תשואת ה-S&P 500 (SPY) ב-12 החודשים בלי החודש האחרון, למידע: {_pct(spy.get('ret_12_1'), True)}.", '']
    if read.get('last') and (read['last'] != res['as_of'] or read.get('first') != res['as_of']):
        L += [f"**שים לב:** נתוני האנליסטים נקראו ב-{read_he}, ולא בתאריך החיפוש ({_dmy(res['as_of'])}). תחזיות הרווח וספירת "
              'האנליסטים הן של יום הקריאה; דוחות שפורסמו אחרי תאריך החיפוש לא נכנסו לתנאי העמידה בתחזית.', '']
    if warn:
        L += sum(([w, ''] for w in warn), [])
    L += _divergence_he(res)
    dell = _where_he(res, 'DELL')
    L += ['## מה החיפוש אומר ומה לא', '',
          '- הוא מסמן חברות שצומחות מהר, מרוויחות, מייצרות מזומן ולא מדללות, ושנתוני האנליסטים שלהן לא מראים החמרה: '
          'אנליסטים לא מורידים להן תחזיות רווח, והן עמדו בתחזית בשני הדוחות האחרונים. הוא רק מחליט לאן להפנות זמן מחקר.',
          '- **המחיר הוא מידע בלבד, לא תנאי** (מאז 25.11.2026). מניה שירדה הרבה, או שנמצאת מתחת לממוצע שלה, לא נפסלת בגלל זה: '
          'תנאי המחיר הישנים זרקו את DELL (43% מתחת לשיא) רגע לפני הרבעון הכי טוב שלה.'
          + (f' בהרצה הזאת DELL: {dell}.' if dell else ''),
          '- מה כן פוסל חברה: עסק שהפסיק לצמוח נפסל בתנאי האיכות.' + _paypal_he(res)
          + ' תנאי האנליסטים רק מוסיפים בדיקה ששני הרבעונים האחרונים והחודש האחרון של התחזיות לא מחמירים.',
          '- הוא **לא הערכת שווי ולא המלצת קנייה**: חברה שעוברת אותו יכולה להיות יקרה מדי ולהיכשל במבחן הקנייה.',
          '- לכל חברה ברשימות מוצגת ההערכה השמורה שלה (אם יש) מול המחיר של היום: חברה שההערכה השמורה שלה נכשלת במבחן הקנייה, '
          'או שנדחתה בבדיקה קודמת, מסומנת ולא נמחקת. היא לא מוצעת בלי הערכה חדשה. התנאים והדירוג לא קוראים את הסימון.',
          '- מקור נתוני העסק: קובץ ה-companyfacts של ה-SEC לכל חברה בנפרד (בקשה אחת לכל חברה מעל סף השווי, ועוד קובץ ההגשות '
          'לקוד הענף), ולא ה-XBRL frames (מושג אחד לכל החברות בתקופה אחת).',
          '- מקור נתוני האנליסטים: דפי האנליסטים של Nasdaq (ממשק ציבורי לא רשמי, שיכול להשתנות או להיות חסר לחברה; '
          'Nasdaq מציינת ש-Zacks היא ספק הנתונים שלה).' + (f' הדפים נקראו ב-{read_he}.' if read_he else '')
          + f' השינוי בתחזית נמדד על תקופה של כחודש ({REVISION_MONTH_DAYS} יום), וספירת האנליסטים שהעלו או הורידו על 4 שבועות. '
          'בכל הרצה אנחנו שומרים את התחזית בתיקייה פרטית (תחת runs/, לא ב-Git). אם התיקייה נשמרת בין הרצות, אחרי חודש התקופה '
          f'מתארכת (עד {REVISION_HISTORY_DAYS} יום); אם לא, היא נשארת כחודש. התקופה כתובה ליד כל מספר, והיא יכולה להיות שונה '
          'מחברה לחברה.',
          '- הרווח למניה בדפי האנליסטים כולל לפעמים רווחי השקעות וסעיפים חד-פעמיים. כשהוא קופץ ביותר מ-'
          f'{ERRATIC_SURPRISE_PCT:.0f}% מעל התחזית ברבעון אחד ומתחתיה ברבעון הסמוך, שני תנאי האנליסטים מסומנים "לא קריא": '
          'החברה לא נכשלת בגללם ולא עוברת בזכותם, והיא מופיעה ברשימת "חסר להן נתון".',
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
          '- תנופה עסקית, תנאי 1, תחזיות הרווח לא יורדות: ב-4 השבועות האחרונים מספר האנליסטים שהעלו את תחזית הרווח למניה (לשנת '
          'הכספים הנוכחית ולבאה) לא קטן ממספר אלה שהורידו. בנוסף, התחזית הממוצעת לשנה הנוכחית לא ירדה ביותר מ-'
          f"{-GATES['eps_revision_min'] * 100:.0f}%. כשהתחזית היא הפסד או אפס אין אחוז שינוי, ואז קובעת רק הספירה.",
          '- תנופה עסקית, תנאי 2, עמידה בתחזית: הרווח למניה שדווח היה שווה לתחזית או מעליה בכל אחד משני הרבעונים האחרונים '
          f"(פספוס של עד {-GATES['surprise_min_pct']:.0f}%, או של עד סנט אחד למניה, נחשב עמידה).",
          '- שני תנאי התנופה העסקית נספרים יחד כתנאי אחד: רבעון שפספס את התחזית מוריד מעצמו גם את התחזית השנתית, ולכן זה אותו '
          'אירוע. חברה שעברה את כל תנאי האיכות ונכשלה רק בהם מופיעה ב"כמעט עברו", עם שתי הסיבות.',
          '- מחיר (מידע בלבד, לא תנאי): תשואת 12 חודשים בלי החודש האחרון והמרחק מהשיא של 52 שבועות מוצגים בטבלאות. '
          f"מניה שנמצאת {GATES['divergence_below_high'] * 100:.0f}% ומעלה מתחת לשיא, ונתוני האנליסטים שלה מראים שיפור (ההסבר בראש הדוח), "
          f'מסומנת {MARK}.',
          '- חברה שנכשלת בתנאי אחד בלבד מופיעה ברשימת "כמעט עברו" עם הסיבה. חברה שחסר לה נתון לא נספרת כעוברת.', '',
          f"## חברות שעברו את כל התנאים ({c['passers']})", '',
          'הדירוג: כל חברה מקבלת ארבעה ציונים מ-0 עד 100, כל אחד לפי המקום שלה בין העוברות: צמיחה, שינוי שוליים, שולי תזרים חופשי '
          'ושינוי בתחזית הרווח. ציון הדירוג הוא הסכום שלהם, בלי משקלות. '
          'חברה בלי אחוז שינוי בתחזית (תחזית רווח אפס או שלילית) מקבלת ברכיב הזה את הערך האמצעי של האחרות.', '']
    ps = res['passers']
    L += _table(['#'] + STD_HEAD + ['רבעון אחרון עד', 'ציון דירוג', 'הערכה שמורה'],
                [[i + 1] + _std_line(r) + [_period(r), f"{r['rank_score']:.0f}", saved_he(r.get('saved_valuation'))]
                 for i, r in enumerate(ps[:top])])
    L += ['', BIZ_COLUMNS_HE + ' העמודות המסומנות "מידע" הן מחיר: הן לא משפיעות על שום תנאי ולא על הדירוג.']
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
    L += ['', 'רכיבי הדירוג (ציון 0-100 לכל רכיב):', '']
    L += _table(['סימול', 'צמיחה', 'שינוי שוליים', 'שולי תזרים', 'שינוי בתחזית הרווח', 'סכום'],
                [[r['symbol']] + [f"{r['rank_parts'][k]:.0f}" + ('~' if k in (r.get('rank_filled') or []) else '') for k in RANK_KEYS]
                 + [f"{r['rank_score']:.0f}"] for r in ps[:top]])
    if any(r.get('rank_filled') for r in ps[:top]):
        L += ['', '~ אין לחברה אחוז שינוי בתחזית; ברכיב הזה היא קיבלה את הערך האמצעי של האחרות.']
    small = [(r['symbol'], g) for r in ps[:top] for g in [_bgate(r, 'eps_revisions')]
             if g and g.get('value') is not None and g.get('then') is not None and abs(g['then']) < SMALL_EPS]
    L += ['', 'הרכיב "שינוי בתחזית הרווח" חופף בחלקו להפתעה ברבעון האחרון: רבעון טוב מעלה מעצמו את התחזית השנתית.'
          + (' כשתחזית הרווח קטנה מדולר למניה, שינוי של כמה סנטים נראה כאחוז גדול. כך אצל: '
             + ', '.join(f"{sym} (מ-{g['then']:.2f}$ ל-{g['now']:.2f}$)" for sym, g in small) + '.' if small else '')]
    L += ['', f"## כמעט עברו: נכשלו בתנאי אחד בלבד ({c['near_misses']})", '',
          'שני תנאי התנופה העסקית (תחזיות הרווח ועמידה בתחזית) נספרים כתנאי אחד, ולכן יכולות להופיע בשורה שתי סיבות.', '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'התנאי שנכשל', 'המספר', 'צמיחה 12ח׳', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול'] + BIZ_HEAD + PRICE_HEAD
                + ['הערכה שמורה'], [_near_line(r) for r in res['near_misses']])
    L += _radar_he(res)
    cm = res.get('commodity') or []
    L += ['', f"## חברות סחורות מחזוריות: הצמיחה הולכת אחרי המחירים ({len(cm)} מתוך {c.get('route_commodity', len(cm))})", '',
          'נפט וגז, זיקוק, מכרות, פלדה, דשנים, ספנות וצינורות. הן לא נכנסות לרשימת העוברות גם כשהמספרים נראים טוב. '
          'מוצגות אלה שעברו את התנאים או נכשלו בתנאי אחד בלבד; השאר ב-screen.json.', '']
    L += _table(['סימול', 'חברה', 'תחום (קוד SEC)', 'שווי שוק', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'השנה החלשה', 'שולי תפעול',
                 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי'] + BIZ_HEAD + PRICE_HEAD + ['מכפיל רווח', 'נתונים עד', 'מצב'],
                [[_sym(r), r['name'][:28], f"{r.get('sic')}", _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_growth_q')),
                  _pct(r.get('rev_cagr_3y')), _pct(r.get('rev_min_year_growth'), True), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin'))] + _biz(r) + _price(r)
                 + [_x(r.get('pe'), 0), _period(r), 'עברה את התנאים' if r['status'] == 'pass' else _fail_he(r)]
                 for r in cm])
    L += ['', f"## בנקים, ביטוח ופיננסים (נמדדים בנפרד; {len(res['financials'])} עברו)", '',
          'תנאים: הכנסות גדלות, תשואה על ההון 15% ומעלה, הון עצמי למניה גדל, ושני תנאי התנופה העסקית.', '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'צמיחת הכנסות', 'תשואה על ההון', 'גידול הון למניה'] + BIZ_HEAD + PRICE_HEAD
                + ['מכפיל רווח', 'נתונים', 'נתונים עד'],
                [[_sym(r), r['name'][:28], _cap(r['market_cap']), _pct(r.get('rev_growth_ttm')), _pct(r.get('roe')),
                  _pct(r.get('bvps_growth'), True)] + _biz(r) + _price(r) + [_x(r.get('pe'), 0),
                  'שנתי בלבד' if r.get('basis') == 'annual' else 'רבעוני', _period(r)] for r in res['financials']])
    if any(r.get('stale') for r in cm + res['financials']):
        L += ['', '"ישן": התקופה האחרונה בנתונים הסתיימה לפני יותר מחמישה חודשים (דוח שנתי בלבד).']
    fn = res.get('financial_near_misses') or []
    if fn:
        L += ['', f'פיננסים שנכשלו בתנאי אחד בלבד ({len(fn)}): '
              + '; '.join(f"{_sym(r)} ({'; '.join(gate_name_he(g) + ': ' + gate_value_he(g) for g in r['failed'])})" for r in fn)]
    L += ['', f"## חברות זרות עם נתונים שנתיים בלבד ({len(res['annual_only'])})", '',
          'חברות שמדווחות ל-SEC רק דוח שנתי (20-F / 40-F): **נתונים שנתיים בלבד**, בלי תנאי הרבעון, ולכן הנתון יכול להיות ישן עד שנה. '
          'מוצגות אלה שעברו או נכשלו בתנאי אחד.', '']
    L += _table(['סימול', 'חברה', 'שווי שוק', 'שנת הדוח עד', 'צמיחה שנתית', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי',
                 'שינוי מניות'] + BIZ_HEAD + PRICE_HEAD + ['מצב'],
                [[_sym(r), r['name'][:28], _cap(r['market_cap']), _period(r), _pct(r.get('rev_growth_ttm')),
                  _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin')),
                  _pct(r.get('share_change'), True)] + _biz(r) + _price(r) + ['עברה' if r['status'] == 'pass' else _fail_he(r)]
                 for r in res['annual_only']])
    L += ['', '## החברות שכבר בספר התזות', '', 'איפה עומדת כל חברה מוכרת מול אותם תנאים.', '']
    L += _table(['סימול', 'מסלול', 'מצב', 'צמיחה 12ח׳', 'צמיחת רבעון', 'צמיחה לשנה ב-3 שנים', 'שולי תפעול', 'שינוי שוליים (נק׳)', 'שולי תזרים חופשי',
                 'שינוי מניות'] + BIZ_HEAD + PRICE_HEAD + ['מה נכשל או חסר', 'הערכה שמורה'],
                [[_sym(r), ROUTE_HE.get(r.get('route'), '—'), STATUS_HE[r['status']],
                  _pct(r.get('rev_growth_ttm')), _pct(r.get('rev_growth_q')), _pct(r.get('rev_cagr_3y')), _pct(r.get('op_margin')),
                  '—' if r.get('margin_change_pts') is None else f"{r['margin_change_pts']:+.1f}", _pct(r.get('fcf_margin')),
                  _pct(r.get('share_change'), True)] + _biz(r) + _price(r) + [
                  '; '.join([gate_name_he(g) + ' (' + gate_value_he(g) + ')' for g in r.get('failed', [])] +
                            (['חסר: ' + _missing_list_he(r['missing'])] if r.get('missing') else []) + ([r['note']] if r.get('note') else [])) or '—',
                  saved_he(r.get('saved_valuation'))]
                 for r in res['thesis_book']])
    sr = saved_rows(res)
    L += ['', f'## הערכות שמורות מול מחיר החיפוש ({len(sr)})', '',
          'כל חברה ברשימות שלמעלה שיש לה הערכה שמורה (תיקייה מתוארכת ב-research/companies) או בדיקה שנדחתה (research/rejected.jsonl). '
          'מבחן הקנייה: תשואה שנתית בתרחיש הבסיסי של 12% ומעלה בשתי העדשות, שורי 17% ומעלה, דובי לא נמוך ממינוס 10% בשנה, '
          'מהמחיר של היום עד סוף שנת היעד. מודל רזה מורץ מחדש במחיר החיפוש; מודל מותאם מושווה למחיר מבחן הקנייה השמור שלו. '
          '"מחיר מבחן הקנייה" הוא המחיר הגבוה ביותר שבו כל ארבעת התנאים עוברים (מחיר ההרצה החוזרת כשהמודל מזהיר). '
          'ההערכה עצמה לא עודכנה: רק המחיר. ככל שההערכה ישנה יותר, כך המספר פחות אמין.', '']
    if sr:
        L += _table(['סימול', 'ברשימה', 'הערכה שמורה', 'תאריך', 'גיל (ימים)', 'סוג', 'שווי בסיסי אז', 'מחיר אז', 'מחיר בחיפוש',
                     'מחיר מבחן הקנייה', 'איך נבדק', 'נדחתה קודם'],
                    [[r['symbol'], label, SAVED_HE[sv['state']], _dmy(sv['date']), '—' if sv['age_days'] is None else sv['age_days'],
                      KIND_HE.get(sv['kind'], sv['kind'] or '—'), _money(sv.get('base_value')), _money(sv.get('price_then')),
                      _money(sv.get('price')), _money(sv.get('buy_test_price')), BASIS_HE.get(sv.get('basis'), '—'),
                      '—' if not sv.get('rejected') else _dmy(sv['rejected'].get('date')) + ' במחיר ' + _money(sv['rejected'].get('price'))]
                     for label, r in sr for sv in [r['saved_valuation']]])
    else:
        L += ['אין לאף חברה ברשימות הערכה שמורה.']
    un = res.get('unread') or []
    if un:
        L += ['', f'## חסר להן נתון ({len(un)})', '',
              'חברות שלא נכשלו ביותר מתנאי אחד, אבל חסר להן נתון, ולכן לא נספרו כעוברות. כדאי לבדוק ידנית את הנתון החסר לפני '
              'שמוותרים עליהן. "לא קריא": הרווח למניה בדפי האנליסטים קפץ ביותר מ-'
              f'{ERRATIC_SURPRISE_PCT:.0f}% מעל התחזית ברבעון אחד ומתחתיה ברבעון הסמוך (לרוב רווחי השקעות, או רווח של סנטים '
              'בודדים), ולכן שני תנאי האנליסטים לא נקראו. זה לא כישלון של העסק.', '']
        L += _table(['סימול', 'חברה', 'שווי שוק', 'מה נכשל', 'מה חסר', 'הפתעה ברווח, 2 רבעונים'],
                    [[r['symbol'], r['name'][:28], _cap(r['market_cap']),
                      '; '.join(f'{gate_name_he(g)} ({gate_value_he(g)})' for g in r.get('failed') or []) or '—',
                      _missing_list_he(r.get('missing') or []) or (r.get('note') or '—'), _biz(r)[1]] for r in un])
    L += ['', '## מספרים בכל שלב', '']
    L += _table(['שלב', 'מספר'], [[k, v] for k, v in res['counts_he']])
    if res.get('gaps_he'):
        L += ['', '## פערי נתונים', ''] + ['- ' + g for g in res['gaps_he']]
    L += ['', 'מקורות: [SEC XBRL companyfacts](https://data.sec.gov/api/xbrl/companyfacts/) לכל חברה בנפרד (לא XBRL frames) '
          'וקובץ ההגשות שלה ([submissions](https://data.sec.gov/submissions/)), [רשימת המניות של Nasdaq](https://www.nasdaq.com/market-activity/stocks/screener), '
          '[דפי האנליסטים של Nasdaq](https://www.nasdaq.com/market-activity/stocks/dell/earnings) לכל חברה (תחזיות רווח, שינויי תחזית '
          'והפתעות רווח; הקישור הוא לדף של DELL כדוגמה. Nasdaq מציינת ש-Zacks Investment Research היא ספק הנתונים שלה; '
          'בתשובות עצמן הייחוס לא מופיע) '
          'ו[לוח הדוחות של Nasdaq](https://www.nasdaq.com/market-activity/earnings), '
          'מחירים יומיים מ-Yahoo Finance (מתואמים לפיצולים; התשואה מתואמת גם לדיבידנדים; למידע בלבד).', '']
    return '\n'.join(L)
