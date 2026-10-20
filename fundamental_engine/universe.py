"""Broad discovery universe from saved raw files: SEC XBRL frames + SEC ticker map + Nasdaq listings.

Pure (no network): scripts/collect_universe.py writes the raw folder; this module turns it into screen rows and a
coverage report that says exactly which markets, exchanges and company types are in and out, and why.

Coverage by construction:
- IN: companies listed on NYSE, Nasdaq or NYSE American (Nasdaq listings file) that file US-GAAP XBRL with the SEC
  (10-K/10-Q filers, any country of incorporation), with a reported annual revenue frame for the latest year.
- OUT (counted in the report): OTC and other venues; foreign private issuers reporting under IFRS (20-F/40-F, no us-gaap
  frames); funds, ETFs, SPACs and shells without revenue; companies below the market-cap floor; listings without an SEC
  ticker match. Banks/insurers are IN the rows but the screen's support rules route them away from operating lenses.
Frames carry no filing dates: rows are for a CURRENT screen; historical tests use filing-dated data (discovery_eval).
"""
from collections import Counter
import glob
import json
import os
from . import sec_frames
from .screen import rows_from_sec_frames

EXCHANGES = {'Nasdaq', 'NYSE'}          # SEC map labels; NYSE American listings appear as NYSE in the SEC file


def _num(text):
    if text in (None, '', 'NA', 'N/A'):
        return None
    try:
        return float(str(text).replace('$', '').replace(',', ''))
    except ValueError:
        return None


def load(raw_dir):
    """Read the raw folder into {tickers, nasdaq, frames_by_year, manifest}."""
    def j(name):
        with open(os.path.join(raw_dir, name), encoding='utf-8') as f:
            return json.load(f)
    man = j('MANIFEST.json')
    tick = j('sec__company_tickers_exchange.json')
    tickers = [dict(zip(tick['fields'], row)) for row in tick['data']]
    nas = j('nasdaq__screener_stocks.json')['data']
    frames = {}
    for c in man['calls']:
        if c.get('metric') and c['status'] == 200:
            with open(os.path.join(raw_dir, c['file']), encoding='utf-8') as f:
                frames.setdefault(c['year'], []).append({'metric': c['metric'], 'tag': c['tag'], 'payload': json.load(f)})
    return {'tickers': tickers, 'nasdaq': nas, 'frames': frames, 'manifest': man}


def build(raw_dir, as_of, min_market_cap=2e8, data=None):
    """-> (rows for screen.scan, coverage report). One row per issuer (CIK); multi-class market caps summed."""
    d = data or load(raw_dir)
    nas = {r['symbol'].replace('/', '-').replace('^', '-').upper(): r for r in d['nasdaq']['rows']}
    by_cik = {}
    for t in d['tickers']:
        by_cik.setdefault(int(t['cik']), []).append(t)
    years = sorted(d['frames'])
    merged = {y: sec_frames.merge_frames(d['frames'][y]) for y in years}
    latest = merged[years[-1]]
    excluded = Counter()
    chosen, prices, sectors, notes = {}, {}, {}, {}
    for cik, ts in by_cik.items():
        listed = [t for t in ts if t['exchange'] in EXCHANGES and t['ticker'].upper() in nas]
        if not listed:
            excluded['not_listed_on_nyse_nasdaq_or_no_listing_match'] += 1
            continue
        if cik not in latest or not (latest[cik].get('revenue') or {}).get('value'):
            excluded['no_us_gaap_revenue_frame_latest_year (IFRS filer, fund, shell or not yet reported)'] += 1
            continue
        caps = [(_num(nas[t['ticker'].upper()].get('marketCap')), t) for t in listed]
        caps = [(c, t) for c, t in caps if c]
        if not caps:
            excluded['no_market_cap'] += 1
            continue
        mcap = sum(c for c, _ in caps)
        if mcap < min_market_cap:
            excluded[f'market_cap_below_{min_market_cap:,.0f}'] += 1
            continue
        primary = max(caps, key=lambda x: x[0])[1]
        sym = primary['ticker'].upper()
        n = nas[sym]
        chosen[cik] = sym
        prices[sym] = {'price': _num(n.get('lastsale')), 'market_cap': mcap}
        sectors[sym] = {'sector': n.get('sector') or None, 'industry': n.get('industry') or None,
                        'country': n.get('country') or None, 'exchange': primary['exchange']}
        if len(caps) > 1:
            notes[sym] = 'multi-class: market cap summed over listed classes ' + ', '.join(t['ticker'] for _, t in caps)
    history = [sec_frames.rows(merged[y], chosen, sectors) for y in years[:-1]]
    current = sec_frames.rows(latest, chosen, sectors)
    rows = rows_from_sec_frames(current, prices=prices, history=history, as_of=as_of)
    for r in rows:
        sanity(r)
        extra = sectors.get(r['symbol'], {})
        r['country'], r['exchange'] = extra.get('country'), extra.get('exchange')
        if r['symbol'] in notes:
            r.setdefault('notes', []).append(notes[r['symbol']])
    report = {
        'as_of': as_of, 'frames_years': years, 'latest_frame': f'CY{years[-1]} annual (not trailing twelve months)',
        'issuers_in_sec_map': len(by_cik), 'nasdaq_listings': len(d['nasdaq']['rows']), 'rows': len(rows),
        'excluded': dict(excluded), 'min_market_cap': min_market_cap,
        'by_exchange': dict(Counter(r.get('exchange') for r in rows)),
        'by_country': dict(Counter(r.get('country') for r in rows).most_common(12)),
        'by_sector': dict(Counter(r.get('sector') for r in rows).most_common()),
        'market_cap_bands': dict(Counter(_band(r['metrics'].get('market_cap')) for r in rows)),
        'coverage': {'in': 'NYSE, Nasdaq and NYSE American listings of US-GAAP XBRL filers with an annual revenue frame for '
                           f'CY{years[-1]} and market value >= {min_market_cap:,.0f}',
                     'out': 'OTC and non-US venues; IFRS foreign private issuers (20-F/40-F); funds, ETFs, SPACs and shells; '
                            'issuers without a CY revenue frame; listings without an SEC ticker match; below the size floor',
                     'caveats': ['annual calendar frames lag up to a year for December filers by October',
                                 'Nasdaq listing data is an unofficial public endpoint (price snapshot, market cap as published)',
                                 'non-calendar fiscal years are mapped by SEC to the closest calendar frame']},
        'sources': {c['file']: c.get('sha256') for c in d['manifest']['calls'] if not c.get('metric')},
        'frame_files': sum(1 for c in d['manifest']['calls'] if c.get('metric') and c['status'] == 200)}
    return rows, report


def _band(m):
    if not m:
        return 'unknown'
    for lim, name in ((1e9, '<1bn'), (1e10, '1-10bn'), (1e11, '10-100bn')):
        if m < lim:
            return name
    return '>100bn'


def sanity(row):
    """Plausibility checks on one screen row (stage 3 of discovery: a cheap data check before ranking).

    Removes values that cannot be right as reported and records why in row['data_flags'] ({code, severity, message}).
    critical: the row cannot be ranked until a filing check (scripts/verify_candidates.py) clears it; major: ranked with
    lower data confidence; minor: one derived ratio dropped. Thresholds are sanity bounds, not economic judgements."""
    m, b = row['metrics'], row.get('basis') or {}
    flags = row.setdefault('data_flags', [])

    def drop(*keys):
        for k in keys:
            m.pop(k, None)
            b.pop(k, None)
    om, nm, gm = m.get('operating_margin'), m.get('net_margin'), m.get('gross_margin')
    if (om is not None and om > 1.0) or (nm is not None and nm > 2.0) or (gm is not None and (gm > 1.0 or gm < -.5)):
        flags.append({'code': 'implausible_margin', 'severity': 'critical',
                      'message': f"margins above 100% of revenue (operating {om}, net {nm}, gross {gm}): revenue is probably a "
                                 'component or a different unit'})
        drop('operating_margin', 'net_margin', 'gross_margin', 'pe', 'fcf_margin', 'fcf_conversion', 'roic')
        return row
    if nm is not None and abs(nm) < .005 and m.get('pe') is not None:
        flags.append({'code': 'negligible_earnings', 'severity': 'minor', 'message': f'net margin {nm:.2%}: P/E not meaningful'})
        drop('pe')
    fc = m.get('fcf_conversion')
    if fc is not None and abs(fc) > 10:
        flags.append({'code': 'fcf_conversion_outlier', 'severity': 'minor', 'message': f'FCF / net income {fc:.1f}: dropped'})
        drop('fcf_conversion')
    if nm is not None and om is not None and nm - om > .15 and m.get('pe') is not None:
        flags.append({'code': 'non_operating_earnings', 'severity': 'major',
                      'message': f'net margin {nm:.0%} exceeds operating margin {om:.0%}: gains outside operations; P/E dropped'})
        drop('pe')
    pe = m.get('pe')
    if pe is not None and pe < 2.5:
        flags.append({'code': 'pe_below_2_5', 'severity': 'critical',
                      'message': f'P/E {pe:.1f}: check reporting currency, one-off gains, a lender balance sheet or a stale price'})
    return row


# SEC Standard Industrial Classification ranges whose economics live on the balance sheet (deposits, loans, float,
# reserves, leverage as the product): FCF, EV and gross-margin screens misread them. Fee businesses inside the 6xxx
# division (investment advice 6282, insurance brokers 6411, royalty and patent owners 679x, lessors 6519) stay in.
BALANCE_SHEET_SIC = ((6000, 6099, 'bank or savings institution'), (6100, 6199, 'credit or finance company'),
                     (6200, 6221, 'securities or commodity broker-dealer'), (6300, 6399, 'insurance carrier'),
                     (6770, 6770, 'blank check company'), (6798, 6798, 'real estate investment trust'))
FEE_SIC = {6411: 'insurance broker: client funds can inflate liabilities and cash; exclude fiduciary balances from net debt and EV'}


def sec_profiles(raw_dir, symbols_by_cik):
    """{symbol: profile} from saved SEC submissions files (scripts/verify_candidates.py). Profile: cik, sic, sic_description,
    balance_sheet_economics (reason or None), fee_flag, fiscal_year_end, category, entity_type, annual_form (10-K, 20-F or
    40-F, from the latest annual report), latest_annual_filed, latest_quarterly_filed, state_of_incorporation."""
    out = {}
    for path in glob.glob(os.path.join(raw_dir, 'submissions', 'CIK*.json')):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        sym = symbols_by_cik.get(int(d['cik']))
        if not sym:
            continue
        sic = int(d['sic']) if str(d.get('sic') or '').isdigit() else None
        bs = next((why for lo, hi, why in BALANCE_SHEET_SIC if sic is not None and lo <= sic <= hi), None)
        rec = d.get('filings', {}).get('recent', {})
        forms = list(zip(rec.get('form', []), rec.get('filingDate', [])))
        annual = next(((f, dt) for f, dt in forms if f in ('10-K', '20-F', '40-F', '10-K/A', '20-F/A')), (None, None))
        quarterly = next((dt for f, dt in forms if f in ('10-Q', '6-K')), None)
        out[sym] = {'cik': int(d['cik']), 'sic': sic, 'sic_description': d.get('sicDescription'),
                    'balance_sheet_economics': bs, 'fee_flag': FEE_SIC.get(sic), 'fiscal_year_end': d.get('fiscalYearEnd'),
                    'category': d.get('category'), 'entity_type': d.get('entityType'), 'annual_form': annual[0],
                    'latest_annual_filed': annual[1], 'latest_quarterly_filed': quarterly,
                    'state_of_incorporation': d.get('stateOfIncorporation')}
    return out


def apply_profiles(rows, profiles):
    """Attach SEC profiles to rows: the SIC description becomes the industry the screen types from (the listing label is
    kept in industry_listing), and balance-sheet SIC ranges are routed away from operating lenses by screen._support."""
    for r in rows:
        p = profiles.get(r['symbol'])
        if not p:
            continue
        r['sec_profile'] = p
        if p.get('sic_description'):
            r.setdefault('industry_listing', r.get('industry'))
            r['industry'] = p['sic_description']
    return rows
