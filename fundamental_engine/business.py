"""Business map from the annual report itself: how the company makes money, part by part.

Pure module (stdlib). Input: the primary document of a 10-K / 20-F with inline XBRL (saved verbatim by
scripts/collect_companies.py). Output: the dimensional facts that consolidated companyfacts drops:

- revenue (and operating income where tagged) by reportable segment, by product or service, by geography, and the
  segment x product cross-table when the filer tags it, for every fiscal year in the report (usually three);
- customer concentration (share of revenue or receivables per named or anonymous customer);
- remaining performance obligations (contracted revenue not yet recognized) and the share expected within 12 months;
- cover-page share counts per class (multi-class issuers, which companyfacts omits).

Each table is reconciled to the consolidated total in the same report; parts that do not add up are reported, not
forced. Member names are humanized from the XBRL member QName (the label linkbase is not read): 'msft:IntelligentCloudMember'
-> 'Intelligent Cloud'. What the XBRL does not carry (units, prices, customers, retention, segment capital) is listed under
not_disclosed with where to look: those come from reading the text (Item 1, Item 7), never from a guess.
"""
from datetime import date
from html import unescape
import re

REVENUE = ('us-gaap:Revenues', 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
           'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax', 'us-gaap:SalesRevenueNet')
OPERATING = ('us-gaap:OperatingIncomeLoss',)
GROSS = ('us-gaap:GrossProfit',)
AXES = {'segment': 'us-gaap:StatementBusinessSegmentsAxis', 'product': 'srt:ProductOrServiceAxis',
        'geography': 'srt:StatementGeographicalAxis'}
TRANSPARENT = {('us-gaap:ConsolidationItemsAxis', 'us-gaap:OperatingSegmentsMember')}
ELIMINATION = {'us-gaap:IntersegmentEliminationMember', 'us-gaap:CorporateNonSegmentMember', 'us-gaap:MaterialReconcilingItemsMember',
               'srt:ConsolidationEliminationsMember'}
NOT_IN_XBRL = [
    ('customers, units, volumes and price per unit', 'Item 1 (Business) and Item 7 (MD&A) key metrics'),
    ('retention, churn, net revenue retention and expansion', 'Item 7 key metrics or earnings materials'),
    ('customer acquisition cost and unit economics', 'Item 7; often not disclosed at all'),
    ('capital employed and capex by segment', 'segment note (total assets / capex by segment are often omitted)'),
    ('why customers pay and what stops them switching', 'Item 1 (Business), Item 1A (Risk Factors)'),
    ('competitors and substitutes', 'Item 1 (Competition), Item 1A'),
]


def _attrs(s):
    return dict(re.findall(r'([\w:.-]+)="([^"]*)"', s))


def _human(qname):
    name = qname.split(':', 1)[-1]
    name = re.sub(r'Member$', '', name)
    name = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', name)
    return name.strip()


def _number(text, a):
    t = unescape(re.sub(r'<[^>]+>', '', text)).strip()
    fmt = a.get('format', '')
    if 'zero' in fmt or t in ('', '-', '—', '–'):
        v = 0.0
    else:
        t = t.replace(' ', '').replace('\xa0', '')
        if 'numcommadecimal' in fmt.replace('-', '') or 'num-comma-decimal' in fmt:
            t = t.replace('.', '').replace(',', '.')
        else:
            t = t.replace(',', '')
        try:
            v = float(t)
        except ValueError:
            return None
    v *= 10 ** int(a.get('scale', '0') or 0)
    return -v if a.get('sign') == '-' else v


def parse_ixbrl(html):
    """-> {'contexts': {id: {start, end, instant, dims}}, 'facts': [{concept, context, value, unit}]} from inline XBRL."""
    ctx = {}
    for cid, body in re.findall(r'<xbrli:context\s+id="([^"]+)"\s*>(.*?)</xbrli:context>', html, re.S):
        dims = dict(re.findall(r'<xbrldi:explicitMember[^>]*dimension="([^"]+)"[^>]*>\s*([^<\s]+)\s*</xbrldi:explicitMember>', body))
        if re.search(r'<xbrldi:typedMember', body):
            dims['__typed__'] = 'typed'
        g = lambda tag: (re.search(rf'<xbrli:{tag}>\s*([0-9-]+)\s*</xbrli:{tag}>', body) or [None, None])[1]
        ctx[cid] = {'start': g('startDate'), 'end': g('endDate'), 'instant': g('instant'), 'dims': dims}
    facts = []
    for at, inner in re.findall(r'<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>', html, re.S):
        a = _attrs(at)
        if not a.get('name') or not a.get('contextRef'):
            continue
        v = _number(inner, a)
        if v is not None:
            facts.append({'concept': a['name'], 'context': a['contextRef'], 'value': v, 'unit': a.get('unitRef')})
    return {'contexts': ctx, 'facts': facts}


def ix_units(html):
    """Inline XBRL unit ids -> companyfacts unit names: 'TWD', 'shares', 'TWD/shares', 'pure'."""
    out = {}
    for uid, body in re.findall(r'<xbrli:unit\b[^>]*id="([^"]+)"[^>]*>(.*?)</xbrli:unit>', html, re.S):
        m = [x.split(':')[-1] for x in re.findall(r'<xbrli:measure>\s*([^<\s]+)\s*</xbrli:measure>', body)]
        out[uid] = '/'.join(m[:2]) if '<xbrli:divide>' in body and len(m) >= 2 else (m[0] if m else uid)
    return out


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _annual(c):
    return c['start'] and c['end'] and 330 <= _days(c['start'], c['end']) <= 380


def _clean_dims(dims):
    return {k: v for k, v in dims.items() if (k, v) not in TRANSPARENT}


def _table(ix, concepts, axis):
    """{member: {fy_end: value}} for one concept family on one axis (single-dimension contexts), plus the totals."""
    out, total = {}, {}
    for f in ix['facts']:
        if f['concept'] not in concepts:
            continue
        c = ix['contexts'].get(f['context'])
        if not c or not _annual(c):
            continue
        dims = _clean_dims(c['dims'])
        if not dims:
            total.setdefault(c['end'], {})[f['concept']] = f['value']
        elif set(dims) == {axis}:
            m = dims[axis]
            out.setdefault(m, {}).setdefault(c['end'], {})[f['concept']] = f['value']
    pick = lambda d: {e: next(v[k] for k in concepts if k in v) for e, v in d.items()}
    return {m: pick(v) for m, v in out.items()}, pick(total)


def _cross(ix, concepts):
    """segment x product revenue when tagged with both axes."""
    out = {}
    for f in ix['facts']:
        if f['concept'] not in concepts:
            continue
        c = ix['contexts'].get(f['context'])
        if not c or not _annual(c):
            continue
        dims = _clean_dims(c['dims'])
        if set(dims) == {AXES['segment'], AXES['product']}:
            out.setdefault(_human(dims[AXES['segment']]), {}).setdefault(_human(dims[AXES['product']]), {})[c['end']] = f['value']
    return out


def _derive(members, total):
    """Share of the latest year's total, growth over the report's span and contribution to total growth, per member."""
    ends = sorted({e for v in members.values() for e in v} | set(total))
    if not ends:
        return {}
    last, first = ends[-1], ends[0]
    out = {}
    for m, v in members.items():
        r1, r0 = v.get(last), v.get(first)
        yrs = _days(first, last) / 365.25 if first != last else None
        cagr = (r1 / r0) ** (1 / yrs) - 1 if r1 and r0 and r0 > 0 and r1 > 0 and yrs else None
        d_tot = (total.get(last) or 0) - (total.get(first) or 0)
        out[m] = {'latest': r1, 'share_of_total': r1 / total[last] if r1 is not None and total.get(last) else None,
                  'cagr': cagr, 'span': f'{first}..{last}' if yrs else None,
                  'share_of_growth': (r1 - r0) / d_tot if r1 is not None and r0 is not None and d_tot else None}
    return out


CONVERT_KEYS = {'us-gaap:DebtInstrumentConvertibleConversionRatio1': 'ratio', 'us-gaap:DebtInstrumentConvertibleConversionPrice1': 'price',
                'us-gaap:DebtInstrumentFaceAmount': 'face', 'us-gaap:DebtInstrumentCarryingAmount': 'carrying'}
DEBT_AXIS = 'us-gaap:DebtInstrumentAxis'


def convertible_terms(html):
    """Convertible notes by instrument (DebtInstrumentAxis member) from inline XBRL: conversion ratio or price, face and carrying
    amounts by date, maturity year from the member name ('...Due2030Member'). A ratio below 1 is shares per 1 unit of principal,
    else per 1,000 (both conventions occur: DoorDash 0.003425, Collegium 27.3553). -> {member: {...}}; only members with a
    conversion term or 'Convertible' in the name."""
    ix = parse_ixbrl(html)
    out = {}
    for f in ix['facts']:
        k = CONVERT_KEYS.get(f['concept'])
        c = ix['contexts'].get(f['context'])
        if not k or not c:
            continue
        dims = _clean_dims(c['dims'])
        m = dims.get(DEBT_AXIS)
        if not m or set(dims) - {DEBT_AXIS, 'us-gaap:LongtermDebtTypeAxis'}:
            continue
        x = out.setdefault(m, {'member': m, 'name': _human(m), 'amounts': {}, 'convertible_label': 'convertible' in m.lower()
                               or dims.get('us-gaap:LongtermDebtTypeAxis') == 'us-gaap:ConvertibleDebtMember'})
        when = c['instant'] or c['end']
        if k in ('face', 'carrying'):
            x['amounts'].setdefault(k, {})[when] = f['value']
        elif k == 'ratio' and f['value'] > 0:
            x['conversion_price'] = 1 / f['value'] if f['value'] < 1 else 1000 / f['value']
            x['price_basis'] = 'conversion ratio ' + ('per 1 of principal' if f['value'] < 1 else 'per 1,000 of principal')
        elif k == 'price' and f['value'] > 0:
            x['conversion_price'], x['price_basis'] = f['value'], 'conversion price tagged'
    res = {}
    for m, x in out.items():
        if not (x.get('conversion_price') or x['convertible_label']):
            continue
        y = re.search(r'Due(20\d\d)', m)
        x['maturity'] = f'{y.group(1)}-12-31' if y else None
        x['maturity_basis'] = 'year from the instrument name (date not tagged)' if y else None
        res[m] = x
    return res


PER_SHARE_WORDS = ('per share', 'eps', 'return on', 'roic', 'rotce', 'roe', 'roa', 'total shareholder return', 'tsr', 'free cash flow per',
                   'economic profit', 'economic value', 'cash flow per')
SIZE_WORDS = ('revenue', 'sales', 'bookings', 'ebitda', 'operating income', 'net income', 'gross profit', 'arr', 'growth', 'volume',
              'subscribers', 'users')


def pay_measures(html):
    """Pay-versus-performance disclosure of a proxy statement (ecd taxonomy): the company-selected measure, the 'most important
    measures' list, CEO (PEO) total and actually-paid pay and total shareholder return by fiscal year, and an orientation:
    'per_share_or_returns' when the measures reward per-share value or returns on capital, 'size' when only revenue, EBITDA or
    growth, 'mixed' otherwise (a reading of the stated measures, not of the plan's weights)."""
    ix = parse_ixbrl(html)
    names = []
    for name, inner in re.findall(r'<ix:nonNumeric\b[^>]*name="ecd:(CoSelectedMeasureName|MeasureName)"[^>]*>(.*?)</ix:nonNumeric>', html, re.S):
        t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', inner)).strip()
        if t and t not in names:
            names.append(t)
    sel = re.search(r'<ix:nonNumeric\b[^>]*name="ecd:CoSelectedMeasureName"[^>]*>(.*?)</ix:nonNumeric>', html, re.S)
    by = {}
    for f in ix['facts']:
        c = ix['contexts'].get(f['context'])
        if f['concept'].startswith('ecd:') and c and not _clean_dims(c['dims']):
            by.setdefault(f['concept'].split(':', 1)[1], {})[c['end'] or c['instant']] = f['value']
    if not names and not by:
        return None
    low = ' '.join(names).lower()
    ps = [w for w in PER_SHARE_WORDS if w in low]
    sz = [w for w in SIZE_WORDS if re.search(r'(?<![a-z])' + re.escape(w), low)]
    orient = 'per_share_or_returns' if ps and not sz else 'size' if sz and not ps else 'mixed' if ps and sz else 'unknown'
    return {'company_selected_measure': re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', sel.group(1))).strip() if sel else None,
            'measures': names, 'orientation': orient, 'per_share_words': ps, 'size_words': sz,
            'peo_total_comp': by.get('PeoTotalCompAmt'), 'peo_comp_actually_paid': by.get('PeoActuallyPaidCompAmt'),
            'total_shareholder_return': by.get('TotalShareholderRtnAmt'), 'peer_tsr': by.get('PeerGroupTotalShareholderRtnAmt')}


NUM_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
             'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'twenty-five': 25, 'thirty': 30, 'forty': 40, 'fifty': 50,
             'one hundred': 100, 'one-half': .5, 'one-third': 1 / 3, 'one-quarter': .25, 'one-fourth': .25, 'one-fifth': .2,
             'one-tenth': .1}
ADS_RE = re.compile(r'(?:ADSs?|ADRs?|American depositary (?:shares?|receipts?)|New York Registry [Ss]hares?)[^.;]{0,120}?represent(?:s|ing)?\s+'
                    r'(?:the right to receive\s+)?(?P<n>\d+(?:\.\d+)?|one hundred|twenty-five|one-(?:half|third|quarter|fourth|fifth|tenth)|'
                    r'[a-z]+)\s+(?:\(\d+\)\s+)?(?:of\s+(?:our|the)\s+)?(?:[A-Z][\w.]*\s+){0,3}(?:class\s+[ab]\s+)?(?:ordinary|common|registered|'
                    r'(?:class\s+)?[ab]|series\s+[ab])\s+shares?', re.I)


def ads_ratio(html):
    """Ordinary shares per depositary share from the annual report text ('each ADS represents five common shares'), with the
    sentence it came from: {'ratio', 'evidence', 'candidates'} or None. Several different stated ratios are returned with
    the most frequent first and flagged, never averaged."""
    text = html_text(html)
    found = []
    for m in ADS_RE.finditer(text):
        raw = m.group('n').lower()
        v = float(raw) if raw[0].isdigit() else NUM_WORDS.get(raw)
        if v:
            found.append((v, text[max(0, m.start() - 40):m.end() + 20].strip()))
    if not found:
        # no depositary shares: ordinary or common shares registered directly on the exchange (ASML) trade one for one
        i = text.find('Title of each class')
        cover = text[i:i + 600] if i >= 0 else ''
        if cover and re.search(r'(?i)(ordinary|common) shares', cover) and not re.search(r'(?i)depositary|registry', cover):
            return {'ratio': 1., 'evidence': cover[:200].strip(), 'candidates': {1.: 1}, 'ambiguous': False, 'direct_listing': True}
        return None
    counts = {}
    for v, _ in found:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts, key=counts.get)
    return {'ratio': best, 'evidence': next(e for v, e in found if v == best), 'candidates': counts,
            'ambiguous': len(counts) > 1}


# Industry vocabulary read from the annual report's own words, per 100,000 words: evidence for the business type where the SEC
# code is ambiguous (SIC 3559 'Special Industry Machinery' covers wafer-fab equipment and food machinery alike)
INDUSTRY_TERMS = {'semiconductor': re.compile(r'semiconductor|wafer|lithograph', re.I)}


def industry_terms(html=None, text=None):
    """{'words', 'counts': {group: mentions}, 'per_100k_words': {group: density}} of the document text."""
    text = text if text is not None else html_text(html)
    words = len(text.split())
    counts = {k: len(rx.findall(text)) for k, rx in INDUSTRY_TERMS.items()}
    return {'words': words, 'counts': counts, 'per_100k_words': {k: (v / words * 1e5 if words else 0.) for k, v in counts.items()}}


# What the business section (Item 1) talks about, per 10,000 of its words: evidence for the business model where the labels
# are generic (SIC 7389 'Business Services, NEC' covers Visa, Uber, Accenture and DoorDash). Vocabularies and the cut-offs
# in model_card.BUSINESS_TERM_RULES were set on 34 annual reports of 2025-2026 (payment networks, marketplaces, IT and payroll
# services, consumer brands, media, autos, defense): a heuristic with the reason shown, not a validated classifier.
BUSINESS_TERMS = {
    'payment_network': re.compile(r'payments? network|payments? volume|cross-border|processed transactions|issuers?\b|acquirers?\b|'
                                  r'cardholders|account holders', re.I),
    'marketplace': re.compile(r'marketplaces?|two-sided|gross bookings|gross merchandise|\bGMV\b|take rate|hosts and guests|'
                              r'buyers and sellers|drivers?\b|couriers|dashers|merchants|room nights|travel partners', re.I),
    'it_services': re.compile(r'consulting|outsourc|managed services|systems integration|digital transformation|'
                              r'professional services|clients?\b|engagements?', re.I),
    'defense': re.compile(r'defen[cs]e|department of defense|u\.s\. government|government customers|missiles?|aircraft|'
                          r'programs?\b|contracts?\b', re.I),
    'vehicles': re.compile(r'vehicles?|automotive|dealers?', re.I),
}


def business_terms(text):
    """{'item1_words', 'per_10k_words': {group: density}} of the annual report's Item 1, or None when no Item 1 is found."""
    item1 = sections(text).get('item1_business')
    if not item1:
        return None
    words = len(item1.split())
    return {'item1_words': words,
            'per_10k_words': {k: len(rx.findall(item1)) / words * 1e4 for k, rx in BUSINESS_TERMS.items()}}


PRO_FORMA_REVENUE = 'us-gaap:BusinessAcquisitionsProFormaRevenue'


def pro_forma_revenue(html):
    """Pro forma revenue of the combined company as if an acquisition had closed at the start of the comparison period
    (ASC 805 disclosure): [{'start', 'end', 'value', 'acquisition'}]. The prior-year pro forma figure is the like-for-like
    base that separates acquired revenue from organic growth (Dick's Sporting Goods with Foot Locker, 2026)."""
    ix = parse_ixbrl(html)
    out, seen = [], set()
    for f in ix['facts']:
        c = ix['contexts'].get(f['context'])
        if f['concept'] != PRO_FORMA_REVENUE or not c or not c['start'] or not c['end']:
            continue
        dims = _clean_dims(c['dims'])
        if dims and set(dims) != {'us-gaap:BusinessAcquisitionAxis'}:
            continue
        key = (c['start'], c['end'], dims.get('us-gaap:BusinessAcquisitionAxis'))
        if key not in seen:
            seen.add(key)
            out.append({'start': c['start'], 'end': c['end'], 'value': f['value'], 'acquisition': key[2] and _human(key[2])})
    return out


def business_map(html, source=None, consolidated_revenue=None):
    """Business map from one annual report's inline XBRL. source: {'url','accession','form','filed','period'}."""
    ix = parse_ixbrl(html)
    tables, disclosed, notes = {}, [], []
    groups = []
    for name, axis in AXES.items():
        rev, tot = _table(ix, REVENUE, axis)
        if name == 'product':
            # standard members (us-gaap:ProductMember, ServiceOtherMember, LicenseMember...) are a revenue-TYPE split that
            # overlaps the company's own product lines: two tables, each reconciled on its own
            std = {m: v for m, v in rev.items() if m.split(':')[0] in ('us-gaap', 'srt')}
            groups += [('revenue_type', axis, std, tot), ('product', axis, {m: v for m, v in rev.items() if m not in std}, tot)]
        else:
            groups.append((name, axis, rev, tot))
    for name, axis, rev, tot in groups:
        if consolidated_revenue and not tot:
            tot = consolidated_revenue
        named = {_human(m): v for m, v in rev.items() if m not in ELIMINATION}
        if not named:
            continue
        elim = {_human(m): v for m, v in rev.items() if m in ELIMINATION}
        last = max(e for v in named.values() for e in v)
        parts = sum(v.get(last, 0) for v in named.values()) + sum(v.get(last, 0) for v in elim.values())
        recon = parts / tot[last] if tot.get(last) else None
        t = {'axis': axis, 'revenue': named, 'eliminations_or_other': elim or None, 'total_revenue': tot,
             'reconciliation': {'year': last, 'parts': parts, 'total': tot.get(last), 'ratio': recon,
                                'status': ('reconciles' if recon is not None and abs(recon - 1) <= .01 else
                                           'partial (other members or overlapping categories)' if recon is not None else 'no total in report')},
             'derived': _derive(named, tot)}
        if name == 'segment':
            oi, _ = _table(ix, OPERATING, axis)
            gp, _ = _table(ix, GROSS, axis)
            if oi:
                t['operating_income'] = {_human(m): v for m, v in oi.items() if m not in ELIMINATION}
                t['operating_margin'] = {m: {e: o / t['revenue'][m][e] for e, o in v.items() if t['revenue'].get(m, {}).get(e)}
                                         for m, v in t['operating_income'].items()}
            if gp:
                t['gross_profit'] = {_human(m): v for m, v in gp.items() if m not in ELIMINATION}
        tables[name] = t
        disclosed.append(f"revenue by {name} ({len(named)} members, {t['reconciliation']['status']})")
        if name == 'segment':
            disclosed.append('operating income by segment' if t.get('operating_income') else None)
    disclosed = [d for d in disclosed if d]
    cross = _cross(ix, REVENUE)
    if cross:
        tables['segment_x_product'] = cross
        disclosed.append('revenue by product within segment')
    conc = []
    for f in ix['facts']:
        if f['concept'] != 'us-gaap:ConcentrationRiskPercentage1':
            continue
        c = ix['contexts'].get(f['context']) or {}
        d = c.get('dims', {})
        bench = d.get('us-gaap:ConcentrationRiskByBenchmarkAxis', '')
        who = next((v for k, v in d.items() if 'Customer' in k or 'MajorCustomers' in k), None)
        conc.append({'customer': _human(who) if who else 'unnamed', 'benchmark': _human(bench) if bench else None,
                     'share': f['value'], 'period_end': c.get('end') or c.get('instant'),
                     'type': _human(d.get('us-gaap:ConcentrationRiskByTypeAxis', '')) or None})
    if conc:
        disclosed.append('customer or supplier concentration')
    rpo = None
    for f in ix['facts']:
        c = ix['contexts'].get(f['context']) or {}
        if f['concept'] == 'us-gaap:RevenueRemainingPerformanceObligation' and not _clean_dims(c.get('dims', {})) and c.get('instant'):
            if not rpo or c['instant'] > rpo['as_of']:
                rpo = {'value': f['value'], 'as_of': c['instant']}
    if rpo:
        pct = [f['value'] for f in ix['facts'] if f['concept'] == 'us-gaap:RevenueRemainingPerformanceObligationPercentage'
               and (ix['contexts'].get(f['context']) or {}).get('instant') == rpo['as_of']]
        rpo['share_in_first_period'] = pct[0] if pct else None
        disclosed.append('remaining performance obligations (contracted, unrecognized revenue)')
    cover = {}
    for f in ix['facts']:
        if f['concept'] == 'dei:EntityCommonStockSharesOutstanding':
            c = ix['contexts'].get(f['context']) or {}
            dims = c.get('dims', {})
            # us-gaap StatementClassOfStockAxis; IFRS filers use ifrs-full ClassesOfShareCapitalAxis (Novo Nordisk A and B shares)
            cls = next((v for k, v in dims.items() if 'class' in k.lower()), 'all')
            when = c.get('instant') or c.get('end')
            if when:
                cover.setdefault(when, {})[_human(cls) if cls != 'all' else 'all'] = f['value']
    shares = None
    if cover:
        when = max(cover)
        by = cover[when]
        shares = {'as_of': when, 'by_class': by, 'total': by['all'] if list(by) == ['all'] else sum(v for k, v in by.items() if k != 'all')}
        if len(by) > 1:
            shares['note'] = ('sum of the classes on the cover: right for share classes with equal economic rights (Alphabet, Meta); '
                              'an Up-C or voting-only class (e.g. units exchangeable into Class A) needs the capital structure instead')
    not_disclosed = [{'item': i, 'where': w} for i, w in NOT_IN_XBRL]
    if 'segment' in tables and not tables['segment'].get('operating_income'):
        not_disclosed.insert(0, {'item': 'operating income by segment', 'where': 'segment note (the measure may be a non-GAAP segment profit)'})
    if 'segment' not in tables:
        not_disclosed.insert(0, {'item': 'reportable segments (single segment or not tagged)', 'where': 'segment note'})
    if not conc:
        notes.append('no tagged concentration: either no customer is material (often stated in Item 1/1A) or it is untagged')
    text = html_text(html)
    return {'business_map_version': 1, 'source': source, 'tables': tables, 'customer_concentration': conc, 'rpo': rpo,
            'cover_shares': shares, 'disclosed': disclosed, 'not_disclosed': not_disclosed, 'notes': notes,
            'industry_terms': industry_terms(text=text), 'business_terms': business_terms(text),
            'fact_count': len(ix['facts']),
            'meaning': 'facts as tagged in the annual report; member names humanized from XBRL; not adjusted, not forecast'}


def _w(word):
    """A heading word that may be typeset in split small caps ('B USINESS')."""
    return r'\s?'.join(word)


ITEMS = {'item1_business': (rf'item\s*1\.\s*{_w("business")}', rf'item\s*1a\.?\s*{_w("risk")}\s*{_w("factors")}'),
         'item1a_risk_factors': (rf'item\s*1a\.\s*{_w("risk")}\s*{_w("factors")}',
                                 rf'item\s*1b\.\s*{_w("unresolved")}|item\s*1c\.\s*{_w("cybersecurity")}|item\s*2\.\s*{_w("properties")}'),
         'item7_mdna': (rf'item\s*7\.\s*{_w("management")}', rf'item\s*7a\.\s*{_w("quantitative")}|item\s*8\.\s*{_w("financial")}')}


def html_text(html):
    """Plain text of a filing: hidden XBRL header removed, tags stripped, entities decoded, whitespace collapsed."""
    html = re.sub(r'<ix:header>.*?</ix:header>', ' ', html, flags=re.S)
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    html = re.sub(r'<br\s*/?>|</p>|</div>|</tr>|</h\d>', '\n', html, flags=re.I)
    t = unescape(re.sub(r'<[^>]+>', ' ', html))
    t = re.sub(r'[ \t\xa0]+', ' ', t)
    return re.sub(r'\n\s*\n+', '\n\n', t).strip()


def sections(text):
    """{item: text} for Item 1, 1A and 7 of a 10-K: the longest span between an item heading and the next item heading
    (the table of contents produces short spans and is skipped). Missing items are omitted, not guessed."""
    out = {}
    low = text.lower()
    for key, (start, stop) in ITEMS.items():
        best = None
        for m in re.finditer(start, low):
            n = re.search(stop, low[m.end():])
            end = m.end() + n.start() if n else min(len(low), m.end() + 400000)
            if best is None or end - m.start() > best[1] - best[0]:
                best = (m.start(), end)
        if best and best[1] - best[0] > 2000:
            out[key] = text[best[0]:best[1]].strip()
    return out


def _fade(g0, terminal, half_life, n):
    return [terminal + (g0 - terminal) * .5 ** (t / half_life) for t in range(1, n + 1)]


def segment_path(bm, years=5, half_life=2.5, terminal=.03, cap=(-.3, .6)):
    """Revenue growth built part by part: each segment (else each product line, when that table reconciles) grows at its own
    span CAGR, bounded to the cap and fading to the terminal rate with the half-life; the parts are summed. A cross-check on the
    consolidated forecast, not a forecast: acquisitions inside a part's CAGR and disclosed mix plans are not modelled."""
    tables = (bm or {}).get('tables') or {}
    for name in ('segment', 'product'):
        t = tables.get(name)
        if not t or (t.get('reconciliation') or {}).get('status') != 'reconciles':
            continue
        parts = {m: d for m, d in (t.get('derived') or {}).items() if d.get('latest') and d.get('cagr') is not None}
        if len(parts) < 2:
            continue
        rev = {m: d['latest'] for m, d in parts.items()}
        paths = {m: _fade(min(max(d['cagr'], cap[0]), cap[1]), terminal, half_life, years) for m, d in parts.items()}
        total, prev = [], sum(rev.values())
        for i in range(years):
            for m in rev:
                rev[m] *= 1 + paths[m][i]
            cur = sum(rev.values())
            total.append(cur / prev - 1)
            prev = cur
        start = sum(d['latest'] for d in parts.values())
        return {'basis': name, 'parts': {m: {'share': d['latest'] / start, 'cagr': d['cagr'], 'span': d.get('span'),
                                             'capped': not cap[0] <= d['cagr'] <= cap[1]} for m, d in parts.items()},
                'growth': total, 'end_mix': {m: v / sum(rev.values()) for m, v in rev.items()},
                'rule': f'each part: its span CAGR bounded to {cap[0]:.0%}..{cap[1]:.0%}, fading to {terminal:.0%} with a {half_life}-year '
                        'half-life; parts summed', 'meaning': 'cross-check of the consolidated path, not a forecast'}
    return None


def summary(bm, base_growth=None):
    """Compact business map for the casefile: the parts, their weight, growth and margin; contracted revenue; concentration;
    the segment-built growth path against the consolidated base path; what is not disclosed."""
    if not bm:
        return {'status': 'no annual report parsed', 'not_disclosed': [{'item': 'segments, products and geography',
                                                                        'where': 'the annual report (not retrieved)'}]}
    tables = bm.get('tables') or {}
    parts = {}
    for name in ('segment', 'revenue_type', 'product', 'geography'):
        t = tables.get(name)
        if not t:
            continue
        rows = []
        for m, d in sorted((t.get('derived') or {}).items(), key=lambda x: -(x[1].get('latest') or 0)):
            om = (t.get('operating_margin') or {}).get(m) or {}
            last_om = om[max(om)] if om else None
            first_om = om[min(om)] if om else None
            rows.append({'name': m, 'revenue': d.get('latest'), 'share': d.get('share_of_total'), 'cagr': d.get('cagr'), 'span': d.get('span'),
                         'share_of_growth': d.get('share_of_growth'), 'operating_margin': last_om,
                         'margin_change': last_om - first_om if last_om is not None and first_om is not None and len(om) > 1 else None})
        parts[name] = {'rows': rows, 'reconciliation': (t.get('reconciliation') or {}).get('status')}
    conc = [c for c in bm.get('customer_concentration') or [] if c.get('benchmark') and 'Revenue' in c['benchmark']
            or (c.get('benchmark') or '').startswith('Sales')]
    latest = max((c['period_end'] or '' for c in conc), default=None)
    conc = sorted([c for c in conc if c['period_end'] == latest], key=lambda c: -(c['share'] or 0))[:5]
    sp = segment_path(bm)
    cross = None
    if sp and base_growth:
        n = min(len(sp['growth']), len(base_growth))
        if n:
            seg_avg = sum(sp['growth'][:n]) / n
            base_avg = sum(base_growth[:n]) / n
            cross = {'years': n, 'segment_built_avg_growth': seg_avg, 'consolidated_base_avg_growth': base_avg, 'difference': seg_avg - base_avg,
                     'reading': ('consistent' if abs(seg_avg - base_avg) <= .02 else
                                 'segment mix implies faster growth than the consolidated base path' if seg_avg > base_avg else
                                 'segment mix implies slower growth than the consolidated base path')}
    return {'status': 'parsed', 'source': bm.get('source'), 'parts': parts, 'rpo': bm.get('rpo'), 'customer_concentration_revenue': conc,
            'segment_path': sp, 'cross_check': cross, 'disclosed': bm.get('disclosed'), 'not_disclosed': bm.get('not_disclosed'),
            'notes': bm.get('notes'), 'cover_shares': bm.get('cover_shares')}
