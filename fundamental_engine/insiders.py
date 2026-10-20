"""Insider open-market buying from the SEC Form 3/4/5 bulk data sets: a discovery signal, never a buy signal.

Why: insiders sell for many reasons (diversification, taxes, planned sales) but buy with their own money mostly for one,
and clusters of purchases by several insiders, especially officers, have carried information in the literature
(Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012 on non-routine trades). Sales are ignored here.

Input: the quarterly zip files saved verbatim from sec.gov (Insider Transactions Data Sets). A purchase counts only when it
is an open-market or private purchase (transaction code P), acquired, non-derivative, with a price, filed on or before
as_of (no look-ahead) and dated within the window. Output per issuer: distinct buyers, officers among them, dollars,
first/last dates, the filings (source and date for every statement), and the size relative to market value when known.
"""
import csv
from datetime import date, datetime, timedelta
import io
import zipfile


def _d(s):
    try:
        return datetime.strptime(s.strip(), '%d-%b-%Y').date()
    except (ValueError, AttributeError):
        return None


def _rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding='utf-8', errors='replace'), delimiter='\t')


def purchases(zip_paths, as_of, window_days=180):
    """[{issuer_cik, symbol, issuer, owner, relationship, title, date, filed, shares, price, value, accession}] from the zips."""
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=window_days)
    out = []
    for path in zip_paths:
        with zipfile.ZipFile(path) as z:
            sub = {r['ACCESSION_NUMBER']: r for r in _rows(z, 'SUBMISSION.tsv') if r.get('DOCUMENT_TYPE') in ('4', '4/A')}
            own = {}
            for r in _rows(z, 'REPORTINGOWNER.tsv'):
                own.setdefault(r['ACCESSION_NUMBER'], []).append(r)
            for r in _rows(z, 'NONDERIV_TRANS.tsv'):
                if r.get('TRANS_CODE') != 'P' or r.get('TRANS_ACQUIRED_DISP_CD') != 'A':
                    continue
                s = sub.get(r['ACCESSION_NUMBER'])
                if not s:
                    continue
                td, fd = _d(r.get('TRANS_DATE')), _d(s.get('FILING_DATE'))
                if not td or not fd or fd > end or not start <= td <= end:
                    continue
                try:
                    sh, px = float(r.get('TRANS_SHARES') or 0), float(r.get('TRANS_PRICEPERSHARE') or 0)
                except ValueError:
                    continue
                if sh <= 0 or px <= 0:
                    continue
                for o in own.get(r['ACCESSION_NUMBER'], [{}])[:1]:
                    out.append({'issuer_cik': int(s['ISSUERCIK']), 'symbol': (s.get('ISSUERTRADINGSYMBOL') or '').upper().strip(),
                                'issuer': s.get('ISSUERNAME'), 'owner': o.get('RPTOWNERNAME'), 'owner_cik': o.get('RPTOWNERCIK'),
                                'relationship': o.get('RPTOWNER_RELATIONSHIP') or '', 'title': o.get('RPTOWNER_TITLE') or '',
                                'date': td.isoformat(), 'filed': fd.isoformat(), 'shares': sh, 'price': px, 'value': sh * px,
                                'accession': r['ACCESSION_NUMBER']})
    return out


def clusters(rows, market_caps=None, min_buyers=2, min_value=100_000):
    """Per issuer: distinct buyers and dollars. A cluster needs min_buyers distinct insiders (officers or directors; a 10%
    owner alone is a fund, not management) and min_value dollars. Ordered by buyers, then by value relative to market cap."""
    by = {}
    for r in rows:
        mgmt = any(k in r['relationship'] for k in ('Officer', 'Director'))
        e = by.setdefault(r['issuer_cik'], {'issuer_cik': r['issuer_cik'], 'symbol': r['symbol'], 'issuer': r['issuer'], 'buyers': {},
                                            'value': 0., 'first': r['date'], 'last': r['date'], 'filings': []})
        if mgmt:
            b = e['buyers'].setdefault(r['owner_cik'], {'name': r['owner'], 'relationship': r['relationship'], 'title': r['title'], 'value': 0.})
            b['value'] += r['value']
            e['value'] += r['value']
        e['first'], e['last'] = min(e['first'], r['date']), max(e['last'], r['date'])
        e['filings'].append({'accession': r['accession'], 'filed': r['filed'], 'owner': r['owner'], 'value': r['value']})
    out = []
    for e in by.values():
        n = len(e['buyers'])
        if n < min_buyers or e['value'] < min_value:
            continue
        cap = (market_caps or {}).get(e['symbol'])
        officers = sum(1 for b in e['buyers'].values() if 'Officer' in b['relationship'])
        out.append({**e, 'buyers': sorted(e['buyers'].values(), key=lambda b: -b['value']), 'n_buyers': n, 'n_officers': officers,
                    'share_of_market_cap': e['value'] / cap if cap else None,
                    'statement': (f"{n} insiders ({officers} officers) bought {e['value']:,.0f} USD of {e['symbol']} in the open market "
                                  f"between {e['first']} and {e['last']} (SEC Form 4, {len(e['filings'])} filings)")})
    return sorted(out, key=lambda e: (-e['n_buyers'], -(e['share_of_market_cap'] or 0), -e['value']))


def evidence(cluster):
    """Exploratory evidence for candidates.cards: source, date, statement (the latest filing dates the statement)."""
    last = max(cluster['filings'], key=lambda f: f['filed'])
    return {'source': f"SEC Form 4 (accession {last['accession']}; insider transactions data set)", 'date': last['filed'],
            'statement': cluster['statement'], 'kind': 'insider_purchase_cluster'}
