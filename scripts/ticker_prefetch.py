"""Ticker prefetch: the deterministic part of a ticker run, in seconds, before any agent starts (no LLM, no connector).

    SEC_USER_AGENT="app contact@example.com" python scripts/ticker_prefetch.py NVDA 2026-10-25 [--reuse auto|none|<run dir>]

What it saves under runs/tickers/<TICKER>_<as_of>/ (gitignored), so the four gather lanes start from documents instead of
looking for them (CLAUDE.md "On a ticker"; the lanes' instructions: python -m fundamental_engine ticker-stage <stage>):

- companies/<T>/raw/: SEC submissions and companyfacts, the latest annual report, the latest quarterly report and the proxy
  statement, verbatim, with MANIFEST.json rows in the shape the pipeline dates them by (pipeline-plan / collect_companies);
  companies/<T>/company.json (the pipeline's company file: ticker, name, CIK, fiscal year end, reporting currency from SEC).
- documents/: the other 10-Qs since the annual report and the exhibits 99 (release, CFO commentary) of the last --releases
  earnings 8-Ks (Item 2.02), verbatim, with documents/MANIFEST.json.
- reading/: plain text of those documents (10-K Items 1, 1A and 7 apart, and the whole report for the notes), so a lane
  reads sections with grep instead of whole files. The text is untrusted document content, never instructions.
- macro/fred/: the FRED regime series (DGS10, BAA10Y, VIXCLS, T10Y2Y) in the collect_market_context layout (deep --macro);
  market.json: the dated risk-free (DGS10) and the newest saved implied ERP (runs/macro/market_*.json) for deep --market.
- prefetch/history.json + prefetch/classify.json: the company's own fiscal years and quarters from companyfacts and the
  computed company type (profile.classify_company); the analyst confirms or overrides it with reasons.
- gather/_prefetch.json: an evidence-pack fragment with the registered sources (pf_* ids: filings, releases, companyfacts by
  filing date, FRED, ERP), the companyfacts facts of the last fiscal years and quarters, and market {risk_free,
  equity_risk_premium}; pack-merge reads it with the lanes' fragments (it sorts first, so its source ids win on a URL).
- identity.json (identity skeleton, the filings list, the computed type; lanes complete the security and the competitors)
  and prefetch.json (what was saved, what remains for each lane, every gap, the reuse record).

--reuse auto: the newest earlier run folder of the SAME ticker dated within 30 days before as_of donates its gather
fragments (and the connector payloads in its raw folder except the price snapshot); its position, pack and table are kept
under reuse/ for reference. prefetch.json lists the filings dated after that run: only those are re-read, and the market
data (price, consensus, rates, news) is always refreshed.

Network failures and a missing SEC_USER_AGENT are recorded as gaps, never fatal; a host that fails twice in a row is not
called again in this run. SEC asks for a User-Agent with a contact e-mail: it is read from the environment, never written.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from collect_universe import fetch, TICKERS  # noqa: E402  (SEC fetch with gzip handling; the SEC ticker map URL)
from collect_market_context import HEADERS as FRED_HEADERS  # noqa: E402  (the agent FRED's CDN accepts)
from fundamental_engine import __version__, business, handoff, pipeline  # noqa: E402
from fundamental_engine.facts import LAG_DAYS  # noqa: E402
from fundamental_engine.history import SIGNED  # noqa: E402
from fundamental_engine.market_context import FRED_URL, REGIME_SERIES, read_regime  # noqa: E402

PAUSE = 0.12                 # seconds between SEC requests (SEC fair access: at most 10 per second)
REUSE_DAYS = 30
RELEASES = 8                 # earnings 8-Ks whose exhibits are saved (the guidance track record needs 6-8 periods)
YEARS, QUARTERS = 6, 8
TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,12}$')
ANNUAL_METRICS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a', 'rd', 'sga',
                  'eps_diluted', 'shares_diluted_wavg', 'buybacks', 'dividends_paid', 'cash', 'short_term_investments',
                  'total_assets', 'equity', 'long_term_debt', 'goodwill', 'inventory', 'receivables')
QUARTER_METRICS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'eps_diluted', 'shares_diluted_wavg', 'sbc',
                   'cfo', 'capex')
# FactBook metric -> plain history key (history.SIGNED / NONNEGATIVE): classify_company and the forensic screens read them
HISTORY_KEYS = {'revenue': 'revenue', 'gross_profit': 'gross_profit', 'operating_income': 'operating_income',
                'net_income': 'net_income', 'cfo': 'operating_cash_flow', 'capex': 'capex', 'dividends_paid': 'dividends_paid',
                'buybacks': 'buybacks', 'goodwill': 'goodwill', 'total_assets': 'total_assets', 'cash': 'cash',
                'pretax_income': 'pretax_income', 'income_tax': 'income_tax', 'retained_earnings': 'retained_earnings',
                'equity': 'equity', 'current_assets': 'current_assets', 'current_liabilities': 'current_liabilities',
                'total_liabilities': 'total_liabilities', 'receivables': 'receivables', 'sga': 'sga', 'd_and_a': 'depreciation',
                'ppe': 'ppe', 'long_term_debt': 'long_term_debt', 'interest_expense': 'interest_expense',
                'shares_diluted_wavg': 'shares_diluted'}
OUTFLOWS = ('capex', 'dividends_paid', 'buybacks')       # cash outflows some filers tag negative: the history wants the size
EXHIBIT = re.compile(r'(?i)ex[-_]?99|exhibit[-_]?99|99[-_.]?[12]\b|pr\.htm|press|release|earnings|commentary')
# a reuse copies the earlier run's raw payloads except the price snapshot (always refreshed); the SEC files are refetched
# (submissions and companyfacts always, a document when its accession changed) and the copy stays only when SEC fails
REUSE_SKIP = re.compile(r'^(MANIFEST\.json$|ib__price-snapshot-)')
LANES = ('gather_numbers', 'gather_documents', 'gather_field', 'gather_macro')
# which stage maintains a fragment copied by a reuse (edits it in place: updates or removes superseded items); reports and
# expectations are the lanes of a run made by the saved Workflow ticker.js
OWNERS = {'numbers.json': 'gather_numbers', 'documents.json': 'gather_documents', 'field.json': 'gather_field',
          'macro.json': 'gather_macro', 'understand.json': 'analyst',
          'reports.json': 'gather_documents (its guidance_track rows: gather_numbers)',
          'expectations.json': 'gather_numbers (its kpis, segments and customers: gather_documents)'}
# what is left for each lane after a full prefetch (the lanes' stage files say how)
REMAINS = {
    'gather_numbers': ['identity check: share class, ADR ratio, exchange, quote currency, IBKR contract (search_contracts, READ-ONLY)',
                       'price: IBKR snapshot and month-end history (READ-ONLY), saved under the pipeline-plan names',
                       'Alpha Vantage statements / estimates: one bounded probe each (FMP statements and analyst are ACCESS DENIED)',
                       'attributed consensus (n, low, high, provider, date)', 'own trailing and forward P/E history',
                       'guidance track record from reading/release_*.txt', 'reconcile the pf_ facts to the 10-K / 10-Q'],
    'gather_documents': ['the latest call transcript (AV EARNINGS_CALL_TRANSCRIPT, else IR)',
                         'segments, customers, KPIs, RPO / backlog, current guidance, one-offs and risk factors from reading/'],
    'gather_field': ['the competitors the 10-K names (reading/10k_item1_business.txt) and their own numbers',
                     'sector bottlenecks, capacity and share trajectory against the named competitors'],
    'gather_macro': ['revenue and long-lived assets by country; export controls, tariffs, sanctions, regulation',
                     'news and 8-Ks since the latest periodic filing'],
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _load(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, default=str)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


class Net:
    """Every request through fetch (collect_universe); a failure is a recorded gap, and a host that failed twice in a row is
    skipped for the rest of the run (a blocked egress costs seconds, not minutes)."""

    def __init__(self, ua):
        self.ua, self.calls, self.fails, self.down = ua, [], {}, set()

    def get(self, url, attempts=2, headers=None):
        host = url.split('/')[2]
        if host in self.down:
            self.calls.append({'url': url, 'status': 'skipped', 'reason': f'{host} failed twice in this run'})
            return None
        if host.endswith('sec.gov'):
            time.sleep(PAUSE)
            headers = headers or {'User-Agent': self.ua, 'Accept-Encoding': 'gzip'}
        try:
            status, body = fetch(url, headers or {}, attempts=attempts)
        except Exception as e:      # RuntimeError after the retries, a malformed response: a gap, never fatal
            self.fails[host] = self.fails.get(host, 0) + 1
            if self.fails[host] >= 2:
                self.down.add(host)
            self.calls.append({'url': url, 'status': 'error', 'error': str(e)[:240]})
            return None
        self.fails[host] = 0
        self.calls.append({'url': url, 'status': status, 'bytes': len(body or b'')})
        return body if status == 200 else None


def run_folder(root, ticker, as_of):
    return os.path.join(root, 'runs', 'tickers', f'{ticker}_{as_of}')


# ---------------------------------------------------------------- reuse of an earlier run of the same ticker

def find_reuse(root, ticker, as_of, spec):
    """-> (folder, its as_of) or (None, reason). auto: the newest runs/tickers/<T>_<date> dated before as_of within REUSE_DAYS
    that holds gather fragments; an explicit folder must be the same ticker and dated before as_of (no look-ahead)."""
    if spec in (None, '', 'none'):
        return None, 'reuse not requested'
    cut = date.fromisoformat(as_of)
    pat = re.compile(re.escape(ticker) + r'_(\d{4}-\d{2}-\d{2})$')
    if spec != 'auto':
        path = os.path.abspath(spec)
        m = pat.match(os.path.basename(path.rstrip(os.sep)))
        if not os.path.isdir(path) or not m:
            return None, f'--reuse {spec}: not a run folder of {ticker} (runs/tickers/{ticker}_<date>)'
        d = date.fromisoformat(m.group(1))
        if d >= cut:
            return None, f'--reuse {spec}: dated {d}, not before as_of {as_of} (a later run cannot donate evidence)'
        return (path, d.isoformat()), None
    base = os.path.join(root, 'runs', 'tickers')
    best = None
    for name in (os.listdir(base) if os.path.isdir(base) else []):
        m = pat.match(name)
        if not m:
            continue
        d = date.fromisoformat(m.group(1))
        if d < cut and (cut - d).days <= REUSE_DAYS and os.path.isdir(os.path.join(base, name, 'gather')):
            if best is None or d > best[0]:
                best = (d, os.path.join(base, name))
    if not best:
        return None, f'no earlier run of {ticker} within {REUSE_DAYS} days before {as_of}'
    return (best[1], best[0].isoformat()), None


def apply_reuse(prev, prev_as_of, run, ticker):
    """Copy the earlier run's gather fragments (never over this run's own files) and its connector payloads; keep its
    judgement files under reuse/ for reference only (the analyst redoes the judgement on the new evidence)."""
    out = {'from': prev, 'from_as_of': prev_as_of, 'copied_fragments': [], 'copied_raw': [], 'reference': []}
    g_old, g_new = os.path.join(prev, 'gather'), os.path.join(run, 'gather')
    for name in sorted(os.listdir(g_old)) if os.path.isdir(g_old) else []:
        if name == '_prefetch.json':
            continue
        src, dst = os.path.join(g_old, name), os.path.join(g_new, name)
        if os.path.exists(dst):
            continue
        os.makedirs(g_new, exist_ok=True)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        out['copied_fragments'].append(name)
    out['owners'] = {n: OWNERS.get(n, 'the analyst decides') for n in out['copied_fragments'] if n.endswith('.json')}
    r_old, r_new = os.path.join(prev, 'companies', ticker, 'raw'), os.path.join(run, 'companies', ticker, 'raw')
    old_man = {c.get('file'): c for c in (_load(os.path.join(r_old, 'MANIFEST.json'), {}) or {}).get('calls', [])
               if isinstance(c, dict)}
    rows = []
    for name in sorted(os.listdir(r_old)) if os.path.isdir(r_old) else []:
        src, dst = os.path.join(r_old, name), os.path.join(r_new, name)
        if REUSE_SKIP.match(name) or os.path.isdir(src) or os.path.exists(dst):
            continue
        os.makedirs(r_new, exist_ok=True)
        shutil.copy2(src, dst)
        out['copied_raw'].append(name)
        rows.append({**(old_man.get(name) or {'file': name}), 'reused_from': prev})
    for folder in ('documents', 'reading'):          # saved filings: a document is refetched only when its accession changed
        src, dst = os.path.join(prev, folder), os.path.join(run, folder)
        if os.path.isdir(src) and not os.path.exists(dst):
            shutil.copytree(src, dst)
            out['copied_' + folder] = True
    ref = os.path.join(run, 'reuse')
    for rel in ('identity.json', 'position.json', 'evidence_pack.json', 'analyst.json', 'understand', 'review', 'deliver',
                f'companies/{ticker}/inputs.json', f'companies/{ticker}/out/table_he.md',
                f'companies/{ticker}/out/scenarios_he.md'):
        src = os.path.join(prev, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(ref, rel.replace('/', '__') if rel.startswith('companies/') else rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(src):
            if not os.path.exists(dst):
                shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        out['reference'].append(os.path.relpath(dst, run))
    return out, rows


# ---------------------------------------------------------------- SEC identity, filings and documents

def _ticker_map(path):
    t = _load(path)
    if not isinstance(t, dict) or 'fields' not in t:
        return {}
    return {str(d.get('ticker', '')).upper(): d for d in (dict(zip(t['fields'], x)) for x in t.get('data', []))}


def lookup_cik(net, root, ticker, run, gaps):
    """CIK from the saved SEC ticker map (runs/universe_*/raw, newest first), else the live map. -> (cik, row, source)."""
    variants = list(dict.fromkeys([ticker, ticker.replace('.', '-'), ticker.replace('-', '.'), ticker.replace('.', '')]))
    saved = sorted([os.path.join(root, 'runs', d, 'raw', 'sec__company_tickers_exchange.json')
                    for d in (os.listdir(os.path.join(root, 'runs')) if os.path.isdir(os.path.join(root, 'runs')) else [])
                    if d.startswith('universe_')], reverse=True)
    for path in [os.path.join(run, 'prefetch', 'sec__company_tickers_exchange.json')] + saved:
        m = _ticker_map(path)
        hit = next((m[v] for v in variants if v in m), None)
        if hit:
            return int(hit['cik']), hit, os.path.relpath(path, root)
    if not net.ua:
        return None, None, None
    body = net.get(TICKERS)
    if body is None:
        gaps.append('SEC ticker map not reachable: CIK not resolved (pass --cik, or the numbers lane resolves it)')
        return None, None, None
    path = os.path.join(run, 'prefetch', 'sec__company_tickers_exchange.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(body)
    m = _ticker_map(path)
    hit = next((m[v] for v in variants if v in m), None)
    if not hit:
        gaps.append(f'{ticker} is not in the SEC ticker map (not an SEC filer, or another ticker): identity for the numbers lane')
        return None, None, TICKERS
    return int(hit['cik']), hit, TICKERS


def submissions_until(sub, as_of):
    """The SEC submissions payload with its recent filings cut to those filed on or before as_of (no look-ahead)."""
    r = (sub.get('filings') or {}).get('recent') or {}
    keep = [i for i, d in enumerate(r.get('filingDate') or []) if d <= as_of]
    cut = {k: [v[i] for i in keep] for k, v in r.items() if isinstance(v, list)}
    return {**sub, 'filings': {**(sub.get('filings') or {}), 'recent': cut}}


def filing_rows(sub, cik):
    r = (sub.get('filings') or {}).get('recent') or {}
    n = len(r.get('form') or [])
    col = lambda k, d='': r.get(k) or [d] * n  # noqa: E731
    rows = []
    for form, acc, doc, filed, rep, items, ixbrl in zip(col('form'), col('accessionNumber'), col('primaryDocument'),
                                                        col('filingDate'), col('reportDate'), col('items'),
                                                        col('isInlineXBRL', 0)):
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
        rows.append({'form': form, 'filed': filed, 'report_date': rep or None, 'accession': acc, 'items': items or None,
                     'url': base + doc if doc else None, 'index_url': base, 'primary': doc, 'inline_xbrl': bool(ixbrl)})
    return rows      # newest first, as SEC lists them


def select_filings(rows, n_releases):
    annual_forms = handoff.ANNUAL_FORMS
    annual = next((r for r in rows if r['form'] in annual_forms), None)
    since = annual['filed'] if annual else '0000-00-00'
    quarterlies = [r for r in rows if r['form'] == '10-Q' and r['filed'] >= since]
    releases = [r for r in rows if r['form'] in ('8-K', '8-K/A') and '2.02' in (r['items'] or '')][:n_releases]
    foreign = [r for r in rows if r['form'] == '6-K'][:n_releases] if annual and annual['form'] in ('20-F', '40-F') else []
    proxy = next((r for r in rows if r['form'] == 'DEF 14A'), None)
    later = [r for r in rows if r['filed'] >= since and r['form'] in ('8-K', '8-K/A') and r not in releases]
    return {'annual': annual, 'quarterlies': quarterlies, 'releases': releases, 'foreign_interim': foreign, 'proxy': proxy,
            'other_8k_since_annual': later}


def exhibits(index, primary):
    """Exhibit files of an earnings 8-K from its index.json: the release (99.1) and commentary (99.2) first; [] when the
    names do not say which file is the release (the lane then opens the index)."""
    items = ((index or {}).get('directory') or {}).get('item') or []
    htm = [i['name'] for i in items if str(i.get('name', '')).lower().endswith(('.htm', '.html')) and i['name'] != primary
           and not re.match(r'(?i)^r\d+\.htm', i['name']) and 'index' not in i['name'].lower()
           and not i['name'].lower().startswith('filingsummary')]
    hits = [n for n in htm if EXHIBIT.search(n)]
    if not hits and len(htm) == 1:
        hits = htm
    rank = lambda n: (0 if re.search(r'(?i)99[-_.]?1\b|ex[-_]?991|pr\.htm|press|release', n) else 1, n)  # noqa: E731
    return sorted(hits, key=rank)[:3]


def _source(sid, kind, title, url, day, publisher, independent=True, retrieval='primary_document'):
    return {'id': sid, 'kind': kind, 'title': title, 'url': url, 'date': day, 'independent': independent,
            'publisher': publisher, 'retrieval': retrieval, 'syndicated_from': None}


def _text_file(run, name, header, text):
    path = os.path.join(run, 'reading', name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(header + '\n\n' + text + '\n')
    return {'file': os.path.relpath(path, run), 'chars': len(text)}


# ---------------------------------------------------------------- companyfacts -> facts, history, type

def companyfacts_view(raw, company, as_of, cik, gaps):
    """FactBook facts from the saved companyfacts (pipeline.factbook_from_run_dir, the engine's own reader) -> (facts by
    (metric, period), FactBook) for the last YEARS fiscal years and QUARTERS quarters available by as_of."""
    try:
        fb = pipeline.factbook_from_run_dir(raw, {**company, 'currency': company.get('currency') or 'USD'}, as_of)
    except Exception as e:      # a malformed payload is a gap for the numbers lane, never fatal
        gaps.append(f'companyfacts could not be read by the engine ({str(e)[:160]}): the numbers lane builds the facts')
        return {}, None
    rows = {}
    for f in fb.get('facts') or []:
        if f.get('source_id') != 'sec:companyfacts' or not f.get('available_at') or f['available_at'] > as_of:
            continue
        if not (f['period'].startswith('FY') or f['period'].startswith('Q@') or f['period'].startswith('I@')):
            continue
        rows.setdefault((f['metric'], f['period']), f)
    return rows, fb


def _filed(f):
    return (date.fromisoformat(f['available_at'][:10]) - timedelta(days=LAG_DAYS)).isoformat()


def pack_facts(rows, cik, name):
    """companyfacts facts as pack facts citing a companyfacts source per filing date (citable LAG_DAYS after that filing)."""
    fy = sorted({p for _, p in rows if p.startswith('FY')}, key=lambda p: rows.get(('revenue', p), {}).get('period_end') or p)
    fy = [p for p in fy if ('revenue', p) in rows][-YEARS:]
    ends = {rows[('revenue', p)]['period_end'] for p in fy}
    qs = sorted({p for _, p in rows if p.startswith('Q@')})[-QUARTERS:]
    facts, sources = [], {}
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json'

    def add(f, label):
        filed = _filed(f)
        sid = f'pf_cf_{filed.replace("-", "")}'
        sources.setdefault(sid, _source(sid, 'structured_data', f'SEC XBRL companyfacts of {name}: facts of the filing(s) of '
                                        f'{filed}', f'{url}#filed={filed}', filed, 'U.S. SEC (XBRL from the filer\'s own filings)',
                                        independent=False, retrieval='provider_normalized'))
        slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
        facts.append({'id': f'pf_{f["metric"]}_{slug}', 'metric': f['metric'], 'period': label, 'value': f['value'],
                      'unit': f.get('unit') or 'USD', 'source_ids': [sid], 'period_end': f['period_end'],
                      'basis': 'derived' if f.get('basis') == 'derived' else 'reported'})
    for p in fy:
        for m in ANNUAL_METRICS:
            f = rows.get((m, p))
            if f is None and m in ('cash', 'short_term_investments', 'total_assets', 'equity', 'long_term_debt', 'goodwill',
                                   'inventory', 'receivables'):
                f = next((x for (mm, pp), x in rows.items() if mm == m and pp.startswith('I@') and x['period_end'] in ends
                          and x['period_end'] == rows[('revenue', p)]['period_end']), None)
            if f is not None and isinstance(f.get('value'), (int, float)):
                add(f, p)
    for p in qs:
        for m in QUARTER_METRICS:
            f = rows.get((m, p))
            if f is not None and isinstance(f.get('value'), (int, float)):
                add(f, p)
    return facts, list(sources.values()), fy, qs


def history_case(rows, fy, qs, ticker, name, as_of):
    hist = []
    for p in fy:
        rev = rows[('revenue', p)]
        row = {'fiscal_year': p, 'period_end': rev['period_end'], 'available_at': rev['available_at'][:10],
               'source_ids': [f'pf_cf_{_filed(rev).replace("-", "")}']}
        for m, k in HISTORY_KEYS.items():
            f = rows.get((m, p)) or next((x for (mm, pp), x in rows.items() if mm == m and pp.startswith('I@')
                                          and x['period_end'] == rev['period_end']), None)
            if f is not None and isinstance(f.get('value'), (int, float)):
                v = abs(f['value']) if k in OUTFLOWS else f['value']
                if v >= 0 or k in SIGNED:
                    row[k] = v                      # a negative value under a non-negative key is left out, not flipped
        if isinstance(row.get('revenue'), (int, float)) and row['revenue'] >= 0:
            hist.append(row)
    quarters = []
    for p in qs:
        rev = rows.get(('revenue', p))
        if not rev or not isinstance(rev.get('value'), (int, float)) or rev['value'] < 0:
            continue
        q = {'quarter': p, 'period_end': rev['period_end'], 'available_at': rev['available_at'][:10],
             'source_ids': [f'pf_cf_{_filed(rev).replace("-", "")}'], 'revenue': rev['value']}
        for k in ('operating_income', 'gross_profit', 'net_income'):
            if isinstance((rows.get((k, p)) or {}).get('value'), (int, float)):
                q[k] = rows[(k, p)]['value']
        quarters.append(q)
    case = {'plain_version': 1, 'company': name or ticker, 'ticker': ticker, 'as_of': as_of, 'archetype': 'nonfinancial',
            'history': hist, '_provenance': 'SEC companyfacts via pipeline.factbook_from_run_dir (scripts/ticker_prefetch.py); '
                                             'first-filed value per period; available_at = filing date + facts.LAG_DAYS'}
    if quarters:
        case['recent_quarters'] = quarters[-12:]
    return case


def company_type(case, gaps):
    """profile.classify_company and the forensic screens (scores.scorecards: Piotroski, Altman, Beneish, ROIC, Rule of 40)
    on the prefetched history -> (type, forensic); screens, not verdicts, and every flag goes to the counter-thesis."""
    from fundamental_engine.history import history
    from fundamental_engine.profile import classify_company
    from fundamental_engine.scores import scorecards
    if not case['history']:
        gaps.append('no fiscal-year revenue in companyfacts: company type not computed (the analyst classifies)')
        return None, None
    typ = forensic = None
    try:
        typ = classify_company(case)
    except Exception as e:
        gaps.append(f'classify_company failed on the prefetched history ({str(e)[:160]}): the analyst classifies')
    try:
        forensic = scorecards(history(case), case.get('archetype', 'nonfinancial'), case.get('market_cap'))
    except Exception as e:
        gaps.append(f'forensic screens failed on the prefetched history ({str(e)[:160]}): the analyst runs forensic_scores')
    return typ, forensic


# ---------------------------------------------------------------- rates

def macro(net, root, run, as_of, gaps):
    """FRED regime series into <run>/macro/fred (or today's runs/macro/fred_<as_of> when already collected) and the dated
    risk-free; the newest saved implied ERP dated on or before as_of. -> (fred folder, risk_free, erp, erp file)."""
    folder = os.path.join(root, 'runs', 'macro', f'fred_{as_of}')
    if not os.path.exists(os.path.join(folder, 'MANIFEST.json')):
        folder = os.path.join(run, 'macro', 'fred')
        os.makedirs(folder, exist_ok=True)
        start = (date.fromisoformat(as_of) - timedelta(days=400)).isoformat()
        calls = [c for c in (_load(os.path.join(folder, 'MANIFEST.json'), {}) or {}).get('calls', [])
                 if os.path.exists(os.path.join(folder, c.get('file') or ''))]
        have = {c['series'] for c in calls}
        for s in REGIME_SERIES:
            if s in have:
                continue
            url = FRED_URL.format(s) + f'&cosd={start}'
            body = net.get(url, attempts=2, headers=FRED_HEADERS)
            if body is None or not (body.startswith(b'observation_date') or body.startswith(b'DATE')):
                gaps.append(f'FRED {s} not retrieved: ' + ('the regime line misses it' if s != 'DGS10' else
                                                          'risk-free for the macro lane (AV TREASURY_YIELD)'))
                continue
            with open(os.path.join(folder, f'{s}.csv'), 'wb') as f:
                f.write(body)
            calls.append({'file': f'{s}.csv', 'url': url, 'sha256': _sha(body), 'bytes': len(body), 'retrieved_at_utc': _now(),
                          'series': s, 'title': REGIME_SERIES[s]})
        _dump(os.path.join(folder, 'MANIFEST.json'), {'retrieved_at_utc': _now(), 'source': 'FRED, Federal Reserve Bank of '
                                                      'St. Louis', 'start': start, 'calls': calls})
    rf = None
    try:
        reading = read_regime(folder, as_of)['readings'].get('DGS10')
    except (OSError, ValueError, KeyError):
        reading = None
    if reading:
        rf = {'value': round(reading['value'] / 100, 6), 'as_of': reading['date'], 'title': reading['title'],
              'url': 'https://fred.stlouisfed.org/series/DGS10', 'retrieval': 'market_feed'}
    erp, erp_file = None, None
    for path in sorted((os.path.join(root, 'runs', 'macro', n) for n in (os.listdir(os.path.join(root, 'runs', 'macro'))
                        if os.path.isdir(os.path.join(root, 'runs', 'macro')) else []) if re.match(r'market_\d{4}-\d{2}-\d{2}\.json$', n)),
                       reverse=True):
        e = (_load(path, {}) or {}).get('equity_risk_premium')
        if isinstance(e, dict) and isinstance(e.get('value'), (int, float)) and str(e.get('as_of', ''))[:10] <= as_of:
            erp, erp_file = {k: e[k] for k in ('value', 'as_of', 'title', 'url', 'retrieval') if k in e}, os.path.relpath(path, root)
            break
    if erp is None:
        gaps.append('no saved implied ERP dated on or before as_of (runs/macro/market_*.json): the macro lane reads Damodaran\'s page')
    return folder, rf, erp, erp_file


# ---------------------------------------------------------------- records kept across reruns

def _merge_record(path, fresh, owned):
    """Existing keys win (a lane's correction survives a rerun), except the prefetch-owned ones, which are refreshed."""
    old = _load(path, {}) or {}
    out = {**fresh, **{k: v for k, v in old.items() if v is not None and k not in owned}}
    _dump(path, out)
    return out


def _manifest(path, rows):
    old = (_load(path, {}) or {}).get('calls', [])
    new = {r['file']: r for r in rows}
    calls = [c for c in old if isinstance(c, dict) and c.get('file') not in new] + list(new.values())
    _dump(path, {'calls': calls, 'user_agent': 'redacted (contains a contact e-mail)'})


def _save(net, url, path, rec, refresh, rows=None):
    """One document saved verbatim -> (manifest row, usable). Skipped when the folder already holds it with the same
    accession (a rerun, or a reuse whose document did not change); when the fetch fails, a copy already there (a reuse)
    stays usable with its own dated row and the failure noted on it."""
    man = {c.get('file'): c for c in (_load(os.path.join(os.path.dirname(path), 'MANIFEST.json'), {}) or {}).get('calls', [])
           if isinstance(c, dict)}
    man.update({r['file']: r for r in rows or [] if r.get('file')})
    old = man.get(os.path.basename(path)) or {}
    if (not refresh and os.path.exists(path) and old.get('status') == 200
            and old.get('accession') == rec.get('accession') and old.get('url', url) == url):
        return old, True
    body = net.get(url)
    row = {**rec, 'file': os.path.basename(path), 'url': url, 'provider': 'sec', 'retrieved_at_utc': _now(),
           'note': rec.get('note') or 'verbatim payload (scripts/ticker_prefetch.py)'}
    if body is None:
        if os.path.exists(path) and old.get('status') == 200:
            return {**old, 'refresh_failed_utc': _now()}, True
        return {**row, 'status': 'failed'}, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(body)
    return {**row, 'status': 200, 'sha256': _sha(body)}, True


def _read_text(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return business.html_text(f.read())


# ---------------------------------------------------------------- the whole prefetch

def prefetch(ticker, as_of, root=ROOT, reuse='auto', cik=None, releases=RELEASES, docs=True, refresh=False, ua=None):
    t0 = time.time()
    ticker = str(ticker).upper().strip()
    if not TICKER_RE.match(ticker):
        raise ValueError(f'ticker {ticker!r}: 1-12 characters A-Z 0-9 . -')
    as_of = date.fromisoformat(as_of).isoformat()
    ua = os.environ.get('SEC_USER_AGENT', '') if ua is None else ua
    ua = ua if ('@' in ua and '\n' not in ua and '\r' not in ua) else ''
    run = run_folder(root, ticker, as_of)
    co, raw = os.path.join(run, 'companies', ticker), os.path.join(run, 'companies', ticker, 'raw')
    os.makedirs(raw, exist_ok=True)
    gaps, got, reading, sources = [], [], [], []
    net = Net(ua)
    info = {'ticker': ticker, 'as_of': as_of, 'run': run, 'engine_version': __version__, 'started_at_utc': _now(),
            'sec_user_agent': 'set' if ua else 'missing'}

    hit, why = find_reuse(root, ticker, as_of, reuse)
    reused, raw_rows = None, []
    if hit:
        reused, raw_rows = apply_reuse(hit[0], hit[1], run, ticker)
        prev_ident = _load(os.path.join(hit[0], 'identity.json'))
        if prev_ident and not os.path.exists(os.path.join(run, 'identity.json')):
            _dump(os.path.join(run, 'identity.json'), prev_ident)
        prev_co = _load(os.path.join(hit[0], 'companies', ticker, 'company.json'))
        if prev_co and not os.path.exists(os.path.join(co, 'company.json')):
            _dump(os.path.join(co, 'company.json'), prev_co)
    elif reuse not in (None, '', 'none', 'auto'):
        gaps.append(why)
    if not ua:
        gaps.append('SEC_USER_AGENT is not set ("application contact-email", SEC fair access): SEC EDGAR was not fetched; the '
                    'lanes read the filings through the connectors and WebFetch, or set it and rerun the prefetch')

    sub, facts_payload, rec_row = None, None, None
    if cik is None:
        cik, rec_row, map_src = lookup_cik(net, root, ticker, run, gaps)
        if cik:
            got.append({'what': 'CIK from the SEC ticker map', 'source': map_src})
    cik = int(cik) if cik else None
    man_rows = list(raw_rows)
    if cik and ua:
        for name, url in (('sec__submissions.json', handoff.SEC_SUBMISSIONS.format(cik=cik)),
                          ('sec__companyfacts.json', handoff.SEC_FACTS.format(cik=cik))):
            path = os.path.join(raw, name)
            row, ok = _save(net, url, path, {'accession': None}, True, man_rows)
            man_rows.append(row)
            if ok and row.get('refresh_failed_utc'):
                gaps.append(f"{url} not retrieved: {name} kept from the reused run (retrieved {row.get('retrieved_at_utc')})")
            elif ok:
                got.append({'what': name.split('__')[1].split('.')[0], 'file': os.path.relpath(path, run)})
            else:
                gaps.append(f'{url} not retrieved: {name} missing (the numbers lane uses AV statements and the filings)')
        sub = _load(os.path.join(raw, 'sec__submissions.json'))
        facts_payload = os.path.exists(os.path.join(raw, 'sec__companyfacts.json'))
    elif cik:
        sub = _load(os.path.join(raw, 'sec__submissions.json'))           # saved by an earlier prefetch of this folder
        facts_payload = os.path.exists(os.path.join(raw, 'sec__companyfacts.json'))

    filings, chosen = [], {}
    if sub:
        sub_cut = submissions_until(sub, as_of)
        rows = filing_rows(sub_cut, cik)
        chosen = select_filings(rows, releases)
        name = sub.get('name')
        for r in ([chosen['annual']] if chosen['annual'] else []) + chosen['quarterlies'] + chosen['releases'] + \
                ([chosen['proxy']] if chosen['proxy'] else []) + chosen['other_8k_since_annual'] + chosen['foreign_interim']:
            what = ('earnings release (8-K Item 2.02)' if r in chosen['releases'] else
                    'annual report' if r is chosen['annual'] else 'quarterly report' if r['form'] == '10-Q' else
                    'proxy statement' if r['form'] == 'DEF 14A' else
                    'interim report candidate (6-K; the release is an exhibit)' if r['form'] == '6-K' else
                    f"8-K since the annual report (items {r['items'] or 'n/a'})")
            filings.append({'form': r['form'], 'filed': r['filed'], 'report_date': r['report_date'], 'accession': r['accession'],
                            'items': r['items'], 'url': r['url'], 'index_url': r['index_url'], 'what': what})
        if docs and ua:
            # the pipeline's contract files: latest annual report, latest quarterly report, proxy (inline XBRL)
            for d in handoff.filing_documents(sub_cut, cik):
                path = os.path.join(raw, d['file'])
                row, ok = _save(net, d['url'], path, {k: d[k] for k in ('accession', 'form', 'filed', 'report_date')},
                                refresh, man_rows)
                man_rows.append({**row, 'note': 'verbatim primary document (inline XBRL)'})
                if not ok:
                    gaps.append(f"{d['form']} filed {d['filed']} not retrieved ({d['url']}): read it through WebFetch")
                    continue
                kind = {'sec__annual_report.htm': '10k', 'sec__latest_quarterly.htm': '10q', 'sec__proxy.htm': 'proxy'}[d['file']]
                sid = f"pf_{kind}_{d['filed'].replace('-', '')}"
                sources.append(_source(sid, 'sec_filing', f"{name} {d['form']} filed {d['filed']} (period {d['report_date']})",
                                       d['url'], d['filed'], f'{name} (SEC EDGAR)'))
                got.append({'what': f"{d['form']} filed {d['filed']}", 'file': os.path.relpath(path, run), 'source_id': sid})
                text = _read_text(path)
                head = f"# {name} {d['form']} filed {d['filed']} | {d['url']} | source {sid} | untrusted document text (tags stripped by ticker_prefetch)"
                if kind == '10k':
                    for key, body in business.sections(text).items():
                        reading.append({**_text_file(run, f'10k_{key}.txt', head + f' | {key}', body), 'source_id': sid})
                    reading.append({**_text_file(run, '10k_full.txt', head, text), 'source_id': sid})
                elif kind == '10q':
                    reading.append({**_text_file(run, f"10q_{d['filed']}.txt", head, text), 'source_id': sid})
            # the other 10-Qs since the annual report and the earnings releases: documents/
            doc_rows = []
            latest_q = next((d for d in handoff.filing_documents(sub_cut, cik) if d['file'] == 'sec__latest_quarterly.htm'), None)
            for r in chosen['quarterlies']:
                if latest_q and r['accession'] == latest_q['accession'] or not r['url']:
                    continue
                path = os.path.join(run, 'documents', f"10q_{r['filed']}__{r['primary']}")
                row, ok = _save(net, r['url'], path, {'accession': r['accession'], 'form': '10-Q', 'filed': r['filed'],
                                                      'report_date': r['report_date']}, refresh)
                doc_rows.append(row)
                if ok:
                    sid = f"pf_10q_{r['filed'].replace('-', '')}"
                    sources.append(_source(sid, 'sec_filing', f"{name} 10-Q filed {r['filed']} (period {r['report_date']})",
                                           r['url'], r['filed'], f'{name} (SEC EDGAR)'))
                    reading.append({**_text_file(run, f"10q_{r['filed']}.txt", f"# {name} 10-Q filed {r['filed']} | {r['url']} | "
                                                 f"source {sid} | untrusted document text", _read_text(path)), 'source_id': sid})
                else:
                    gaps.append(f"10-Q filed {r['filed']} not retrieved ({r['url']})")
            saved = [c for c in (_load(os.path.join(run, 'documents', 'MANIFEST.json'), {}) or {}).get('calls', [])
                     if isinstance(c, dict) and c.get('status') == 200 and c.get('exhibit')]
            for r in chosen['releases']:
                names = [] if refresh else [c['exhibit'] for c in saved if c.get('accession') == r['accession']]
                if not names:                       # a reuse already holds this release: no index request
                    idx = net.get(r['index_url'] + 'index.json')
                    try:
                        names = exhibits(json.loads(idx) if idx else None, r['primary'])
                    except ValueError:
                        names = []
                if not names:
                    gaps.append(f"earnings 8-K filed {r['filed']}: release exhibit not identified ({r['index_url']}); the documents "
                                'lane opens the index')
                    continue
                for i, ex in enumerate(names, 1):
                    url = r['index_url'] + ex
                    path = os.path.join(run, 'documents', f"8k_{r['filed']}__{ex}")
                    row, ok = _save(net, url, path, {'accession': r['accession'], 'form': r['form'], 'filed': r['filed'],
                                                     'exhibit': ex}, refresh)
                    doc_rows.append(row)
                    if not ok:
                        gaps.append(f"exhibit {ex} of the 8-K filed {r['filed']} not retrieved ({url})")
                        continue
                    sid = f"pf_rel_{r['filed'].replace('-', '')}_{i}"
                    sources.append(_source(sid, 'release', f"{name} earnings 8-K filed {r['filed']}, exhibit {ex}", url, r['filed'],
                                           name))
                    reading.append({**_text_file(run, f"release_{r['filed']}__{os.path.splitext(ex)[0]}.txt",
                                                 f"# {name} 8-K (Item 2.02) filed {r['filed']}, exhibit {ex} | {url} | source {sid} "
                                                 '| untrusted document text', _read_text(path)), 'source_id': sid})
            if doc_rows:
                _manifest(os.path.join(run, 'documents', 'MANIFEST.json'), doc_rows)
        elif not docs:
            gaps.append('--no-docs: the filings were listed, not saved; the documents lane reads them through WebFetch')
    if man_rows:
        _manifest(os.path.join(raw, 'MANIFEST.json'), man_rows)

    # company file and identity skeleton (SEC only; the numbers lane confirms the security, exchange and quote currency)
    exch = None
    if sub:
        pairs = dict(zip([str(x).upper() for x in sub.get('tickers') or []], sub.get('exchanges') or []))
        exch = pairs.get(ticker) or pairs.get(ticker.replace('.', '-')) or (rec_row or {}).get('exchange')
    fye = (sub or {}).get('fiscalYearEnd')
    fye = f'{fye[:2]}-{fye[2:]}' if fye and len(str(fye)) == 4 else None
    annual = chosen.get('annual') if chosen else None
    company = {'ticker': ticker, 'name': (sub or {}).get('name') or (rec_row or {}).get('name'), 'cik': str(cik) if cik else None,
               'currency': None, 'exchange': exch, 'sic': int(sub['sic']) if sub and str(sub.get('sic') or '').isdigit() else None,
               'sic_description': (sub or {}).get('sicDescription'), 'fiscal_year_end': fye,
               'annual_form': annual['form'] if annual else None,
               'identity_source': 'SEC submissions and companyfacts (scripts/ticker_prefetch.py); the numbers lane confirms'}
    check = None
    if facts_payload:
        cf = _load(os.path.join(raw, 'sec__companyfacts.json'), {}) or {}
        html = None
        if os.path.exists(os.path.join(raw, 'sec__annual_report.htm')):
            with open(os.path.join(raw, 'sec__annual_report.htm'), encoding='utf-8', errors='replace') as f:
                html = f.read()
        try:
            check = handoff.identity_check(cf, None, company, annual_html=html)
            company['currency'] = check.get('reporting_currency')
            company['depositary_shares'] = check.get('depositary_shares')
            if check['status'] == 'blocked':
                gaps.append('identity check: ' + '; '.join(check['reasons']))
        except Exception as e:
            gaps.append(f'identity check failed on companyfacts ({str(e)[:160]})')
    if not company['currency']:
        gaps.append('reporting currency not established from companyfacts: the numbers lane sets company.json currency')
    company = _merge_record(os.path.join(co, 'company.json'), {k: v for k, v in company.items() if v is not None}, owned=())

    facts, cf_sources, case, typ = [], [], None, None
    if facts_payload and cik:
        rows, _ = companyfacts_view(raw, company, as_of, cik, gaps)
        if rows:
            facts, cf_sources, fy, qs = pack_facts(rows, cik, company.get('name') or ticker)
            case = history_case(rows, fy, qs, ticker, company.get('name'), as_of)
            _dump(os.path.join(run, 'prefetch', 'history.json'), case)
            typ, forensic = company_type(case, gaps)
            if typ:
                _dump(os.path.join(run, 'prefetch', 'classify.json'), typ)
            if forensic:
                _dump(os.path.join(run, 'prefetch', 'forensic.json'), forensic)
            got.append({'what': f'{len(facts)} companyfacts facts ({len(fy)} fiscal years, {len(qs)} quarters), the computed '
                                'company type and the forensic screens', 'file': 'prefetch/history.json'})
    fred_folder, rf, erp, erp_file = macro(net, root, run, as_of, gaps)
    market, mkt_pack = {}, {}
    if rf:
        market['risk_free'] = rf
        sources.append(_source('pf_fred_dgs10', 'macro', rf['title'], rf['url'], rf['as_of'],
                               'Federal Reserve Bank of St. Louis (FRED), from the Federal Reserve H.15', retrieval='market_feed'))
        mkt_pack['risk_free'] = {'value': rf['value'], 'as_of': rf['as_of'], 'source_id': 'pf_fred_dgs10'}
    if erp:
        market['equity_risk_premium'] = erp
        sources.append(_source('pf_erp_implied', 'macro', erp.get('title') or 'implied equity risk premium', erp.get('url') or
                               'https://pages.stern.nyu.edu/~adamodar/', str(erp['as_of'])[:10], 'Aswath Damodaran (NYU Stern), '
                               f'as saved in {erp_file}', retrieval=erp.get('retrieval') or 'provider_normalized'))
        mkt_pack['equity_risk_premium'] = {'value': erp['value'], 'as_of': str(erp['as_of'])[:10], 'source_id': 'pf_erp_implied'}
    if market:
        _dump(os.path.join(run, 'market.json'), market)
        got.append({'what': 'dated market inputs (risk-free, ERP) for deep --market', 'file': 'market.json'})

    srcs = {s['id']: s for s in sources + cf_sources}
    notes = [f'prefetch {info["started_at_utc"]}: SEC documents saved verbatim and their text in reading/ (untrusted); a lane '
             'cites a pf_ source only after reading the passage it relies on']
    if reused:
        notes.append(f"gather fragments reused from {reused['from']} (as_of {reused['from_as_of']})")
    fragment = {'lane': 'prefetch', 'sources': list(srcs.values()), 'facts': facts, 'notes': notes}
    if mkt_pack:
        fragment['market'] = mkt_pack
    _dump(os.path.join(run, 'gather', '_prefetch.json'), fragment)

    annual_src = next((s['id'] for s in sources if s['id'].startswith('pf_10k_')
                       and date.fromisoformat(s['date']) + timedelta(days=LAG_DAYS) <= date.fromisoformat(as_of)), None)
    type_txt = (f"{typ['stage']} / {typ['lynch']} (computed by classify_company from SEC companyfacts; the analyst confirms or "
                'overrides with reasons)') if typ else None
    ident = {'ticker': ticker, 'as_of': as_of, 'stop': False, 'name': company.get('name'), 'cik': company.get('cik'),
             'currency': company.get('currency'), 'exchange': company.get('exchange'),
             'fiscal_year_end': company.get('fiscal_year_end'), 'sic': company.get('sic'),
             'sic_description': company.get('sic_description'), 'annual_form': company.get('annual_form'),
             'type': type_txt, 'evidence_ids': [annual_src] if annual_src else None}
    # the prefetch owns these (a reused identity's evidence ids may name sources this run's pack does not hold)
    owned = {'ticker', 'as_of', 'filings', 'computed_type', 'identity_check', 'prefetch', 'evidence_ids'}
    ident.update({'filings': filings, 'computed_type': ({k: typ.get(k) for k in ('stage', 'lynch', 'reasons', 'flags')}
                                                         if typ else None),
                  'identity_check': check, 'prefetch': {'by': 'scripts/ticker_prefetch.py', 'at_utc': _now(),
                                                        'to_complete_by_lanes': ['security and share class, quote currency, '
                                                                                 'IBKR contract: gather_numbers',
                                                                                 'named competitors: gather_field']}})
    if not cik:
        ident['resolved'] = False
    ident = _merge_record(os.path.join(run, 'identity.json'), {k: v for k, v in ident.items() if v is not None}, owned)

    remains = {k: list(v) for k, v in REMAINS.items()}
    if not any(s['kind'] == 'sec_filing' for s in sources):
        remains['gather_documents'].insert(0, 'the 10-K, 10-Q and the latest release were NOT saved by the prefetch: fetch them '
                                              '(SEC EDGAR / IR via WebFetch) and read the sections')
    if not facts:
        remains['gather_numbers'].insert(0, 'no companyfacts facts were prefetched: statements from AV or the filings')
    if not rf:
        remains['gather_macro'].insert(0, 'risk-free not prefetched: AV TREASURY_YIELD (10year, daily) as market.risk_free')
    if reused:
        since = [f for f in filings if f['filed'] > reused['from_as_of']]
        reused['new_filings_since'] = since
        reused['always_refresh'] = ['price (IBKR snapshot, READ-ONLY)', 'consensus', 'rates', f"news since {reused['from_as_of']}"]
        reused['rule'] = ('keep what is still valid in the copied fragments; re-read only the filings in new_filings_since; '
                          'refresh the market data; the analyst redoes the judgement (reuse/ is reference only)')
    info.update({'cik': cik, 'name': company.get('name'), 'currency': company.get('currency'),
                 'fiscal_year_end': company.get('fiscal_year_end'), 'got': got, 'filings': filings, 'reading': reading,
                 'macro': {'fred_folder': fred_folder, 'risk_free': rf, 'equity_risk_premium': erp, 'erp_file': erp_file,
                           'market_file': 'market.json' if market else None},
                 'fragment': 'gather/_prefetch.json', 'sources': len(srcs), 'facts': len(facts),
                 'type': ident.get('type'), 'reuse': reused, 'reuse_note': None if reused else why,
                 'remains_for_lanes': remains, 'gaps': gaps, 'calls': net.calls,
                 'finished_at_utc': _now(), 'seconds': round(time.time() - t0, 1)})
    _dump(os.path.join(run, 'prefetch.json'), info)
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('ticker')
    ap.add_argument('as_of', help='information cutoff YYYY-MM-DD (today on a live run)')
    ap.add_argument('--reuse', default='auto', help="auto (an earlier run of the same ticker within 30 days), none, or a run folder")
    ap.add_argument('--cik', type=int, help='skip the ticker-map lookup')
    ap.add_argument('--releases', type=int, default=RELEASES, help='earnings 8-Ks whose exhibits are saved (default 8)')
    ap.add_argument('--no-docs', action='store_true', help='list the filings without saving the documents')
    ap.add_argument('--refresh', action='store_true', help='refetch files this run folder already saved')
    ap.add_argument('--root', default=ROOT, help='repository root (runs/tickers/ is under it)')
    a = ap.parse_args(argv)
    info = prefetch(a.ticker, a.as_of, root=os.path.abspath(a.root), reuse=a.reuse, cik=a.cik, releases=a.releases,
                    docs=not a.no_docs, refresh=a.refresh)
    print(json.dumps({k: info[k] for k in ('run', 'cik', 'name', 'currency', 'fiscal_year_end', 'type', 'sources', 'facts',
                                           'seconds')}
                     | {'reading': [r['file'] for r in info['reading']], 'reuse': (info['reuse'] or {}).get('from'),
                        'new_filings_since_reuse': [f"{f['form']} {f['filed']}" for f in (info['reuse'] or {}).get('new_filings_since', [])],
                        'gaps': info['gaps']}, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
