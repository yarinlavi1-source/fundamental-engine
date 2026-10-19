"""Stage 0: build the point-in-time golden snapshots for the frozen golden set (no business logic).

For every company in stage0/golden_set.json:
  sec/companyfacts/CIK##########.json   the raw companyfacts file, byte-exact from the bulk zip
  sec/submissions/CIK##########*.json   the raw SEC filing history (fetched now; public data)
  pit/<TK>_<ASOF>.json                  every fact filed <= as-of - 2 days, nothing later; restatements known by then
  index/<TK>_<ASOF>.json                filings up to the as-of date that stage 1 must read by hand
  availability.json                     per company and field: the latest value known at the as-of date, or NONE
  prices/                               Alpha Vantage / IB files and the audited as-traded price (private: vendor data)
  MANIFEST.json                         source, retrieval time, information date, code version, SHA-256 for every file

Honest label: these are RECONSTRUCTED point-in-time views. SEC data was retrieved on 2026-10-19 and filtered by
filing date; for the historical as-of dates no same-day snapshot exists. Nothing here is an original snapshot
taken on the as-of date, and no file says so.

usage: python scripts/stage0_golden.py COMPANYFACTS_ZIP RAW_DIR PRICE_AUDIT_JSON OUT_DIR [--fetch-submissions]
"""
import datetime as dt, json, os, shutil, subprocess, sys, time, urllib.request, zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from fundamental_engine import snapshot as S  # noqa: E402

zpath, raw, audit_path, out = sys.argv[1:5]
fetch = '--fetch-submissions' in sys.argv
here = os.path.dirname(os.path.abspath(__file__))
golden = json.load(open(os.path.join(here, '..', 'stage0', 'golden_set.json')))
audit = {c['tk']: c for c in json.load(open(audit_path))['companies']}
git = subprocess.run(['git', '-C', here, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(['git', '-C', here, 'status', '--porcelain', '--', 'scripts/stage0_golden.py',
                             'fundamental_engine/snapshot.py'], capture_output=True, text=True).stdout.strip())
CODE = git + ('-dirty' if dirty else '')
ZIP_RETRIEVED = dt.datetime.utcfromtimestamp(os.path.getmtime(zpath)).strftime('%Y-%m-%dT%H:%M:%SZ')
ZIP_SHA = S.sha256_file(zpath)
UA = {'User-Agent': 'Yarin Lavi research yarinlavi1@gmail.com'}  # same SEC contact header as scripts/gap_card.py
FIELDS = {
    'shares_out_cover': [('dei', 'EntityCommonStockSharesOutstanding')],
    'shares_out_bs': [('us-gaap', 'CommonStockSharesOutstanding')],
    'dil_wavg': [('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding')],
    'options_out': [('us-gaap', 'ShareBasedCompensationArrangementByShareBasedPaymentAwardOptionsOutstandingNumber')],
    'rsu_unvested': [('us-gaap', 'ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsNonvestedNumber')],
    'cash': [('us-gaap', 'CashAndCashEquivalentsAtCarryingValue'), ('us-gaap', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'), ('ifrs-full', 'CashAndCashEquivalents')],
    'st_inv': [('us-gaap', 'ShortTermInvestments'), ('us-gaap', 'MarketableSecuritiesCurrent'), ('us-gaap', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'), ('us-gaap', 'AvailableForSaleSecuritiesCurrent')],
    'lt_inv': [('us-gaap', 'LongTermInvestments'), ('us-gaap', 'MarketableSecuritiesNoncurrent'), ('us-gaap', 'AvailableForSaleSecuritiesDebtSecuritiesNoncurrent')],
    'debt_carrying': [('us-gaap', 'LongTermDebt'), ('us-gaap', 'LongTermDebtNoncurrent'), ('us-gaap', 'DebtInstrumentCarryingAmount'), ('us-gaap', 'ConvertibleNotesPayable'), ('us-gaap', 'ConvertibleDebtNoncurrent'), ('us-gaap', 'SeniorNotes')],
    'debt_fair_value_only': [('us-gaap', 'LongTermDebtFairValue')],
    'convert_terms': [('us-gaap', 'DebtInstrumentConvertibleConversionPrice1'), ('us-gaap', 'DebtInstrumentConvertibleConversionRatio1')],
    'leases': [('us-gaap', 'OperatingLeaseLiability'), ('us-gaap', 'FinanceLeaseLiability')],
    'ocf': [('us-gaap', 'NetCashProvidedByUsedInOperatingActivities'), ('us-gaap', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'), ('ifrs-full', 'CashFlowsFromUsedInOperatingActivities')],
    'capex': [('us-gaap', 'PaymentsToAcquirePropertyPlantAndEquipment'), ('us-gaap', 'PaymentsToAcquireProductiveAssets'), ('ifrs-full', 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities')],
    'revenue': [('us-gaap', 'Revenues'), ('us-gaap', 'RevenueFromContractWithCustomerExcludingAssessedTax'), ('us-gaap', 'SalesRevenueNet'), ('us-gaap', 'RevenueFromContractWithCustomerIncludingAssessedTax'), ('ifrs-full', 'Revenue')],
    'op_income': [('us-gaap', 'OperatingIncomeLoss'), ('ifrs-full', 'ProfitLossFromOperatingActivities')],
    'sbc': [('us-gaap', 'ShareBasedCompensation'), ('us-gaap', 'AllocatedShareBasedCompensationExpense')],
}
MANUAL_FORMS = ('10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '6-K', '8-K', '8-K/A', 'S-1', 'S-1/A', 'F-1',
                '424B4', 'DEF 14A', '10-KT')
PIT_LABEL = ('RECONSTRUCTED point-in-time view: SEC companyfacts bulk file retrieved {r} (UTC), filtered to facts '
             'filed on or before {c} (as-of {a} minus 2 days). Not an original snapshot taken on the as-of date.')
manifest = []


def put(rel, data):
    p = os.path.join(out, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(data)
    return rel


def fetch_json(url):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError('failed ' + url)


avail_all, summary = {}, []
z = zipfile.ZipFile(zpath)
for co in golden['companies']:
    tk, cik, asof = co['tk'], co['cik'], co['asof']
    cut = S.cutoff(asof)
    member = f'CIK{cik:010d}.json'
    info = z.getinfo(member)
    data = z.read(member)
    rel = put(f'sec/companyfacts/{member}', data)
    manifest.append(S.entry(out, rel, source=f'SEC EDGAR companyfacts.zip member {member} (zip sha256 {ZIP_SHA})',
                            retrieved_utc=ZIP_RETRIEVED, information_date=f'all filings to {ZIP_RETRIEVED[:10]}',
                            code_version=CODE, label='raw, byte-exact; contains filings AFTER the as-of date; '
                            'use only through pit/', zip_member_date=list(info.date_time), zip_crc=info.CRC, tk=tk))
    j = json.loads(data)
    rows = []
    for tx, tags in j.get('facts', {}).items():
        for tag, node in tags.items():
            for unit, rs in node.get('units', {}).items():
                for r in rs:
                    rows.append(dict(tx=tx, tag=tag, unit=unit, **r))
    known, excluded = S.known_at(rows, asof)
    max_filed = max((r['filed'] for r in known), default=None)
    assert max_filed is None or max_filed <= cut, (tk, max_filed, cut)
    # restatements known at the as-of date, per tag/unit
    groups = {}
    for r in known:
        groups.setdefault((r['tx'], r['tag'], r['unit']), []).append(r)
    restated = {}
    for k, g in groups.items():
        _, _, ch = S.period_views(g)
        if ch:
            restated['|'.join(k)] = ch
    pit = dict(tk=tk, cik=cik, asof=asof, cutoff_filed_on_or_before=cut, entity=j.get('entityName'),
               label=PIT_LABEL.format(r=ZIP_RETRIEVED, c=cut, a=asof), n_rows_total=len(rows),
               n_rows_known=len(known), n_rows_excluded_filed_after_cutoff=excluded, max_filed_in_view=max_filed,
               restatements_known_by_asof=restated, facts=known)
    rel = f'pit/{tk}_{asof}.json'
    S.dump(pit, os.path.join(out, rel))
    manifest.append(S.entry(out, rel, source=f'derived from sec/companyfacts/{member}', retrieved_utc=ZIP_RETRIEVED,
                            information_date=f'filed on or before {cut}', code_version=CODE,
                            label=pit['label'], tk=tk))
    # availability of the fields the engine needs
    av = {}
    for fld, cands in FIELDS.items():
        best = None
        for tx, tag in cands:
            g = [r for r in known if r['tx'] == tx and r['tag'] == tag]
            for unit in sorted({r['unit'] for r in g}):
                first, latest, _ = S.period_views([r for r in g if r['unit'] == unit])
                for (s, e), r in latest.items():
                    if best is None or (e, r['filed']) > (best['end'], best['filed']):
                        best = dict(tag=f'{tx}:{tag}', unit=unit, start=s, end=e, val=r['val'], filed=r['filed'],
                                    form=r.get('form'), accn=r.get('accn'),
                                    age_days=(dt.date.fromisoformat(asof) - dt.date.fromisoformat(e)).days)
        av[fld] = best or 'NONE known at as-of'
    # multi-class / dimensional share counts: the cover-page count is missing from companyfacts when it is
    # reported per class (dimensional facts are not in companyfacts)
    periodic = sorted({(r['filed'], r.get('accn'), r.get('form')) for r in known
                       if r.get('form') in ('10-K', '10-Q', '20-F', '10-K/A', '10-Q/A')})
    last_periodic = periodic[-1] if periodic else None
    cover_in_last = last_periodic and any(r['tag'] == 'EntityCommonStockSharesOutstanding' and r.get('accn') == last_periodic[1]
                                          for r in known)
    manual = []
    if not cover_in_last:
        manual.append('cover-page shares outstanding (per class) from the last periodic filing: not in companyfacts')
    for fld in ('options_out', 'rsu_unvested', 'convert_terms', 'op_income', 'st_inv', 'debt_carrying', 'revenue', 'ocf'):
        v = av[fld]
        if isinstance(v, str) or v['age_days'] > 200:
            manual.append(f'{fld}: ' + ('not tagged before as-of' if isinstance(v, str) else f"last tagged value is {v['age_days']} days old"))
    if isinstance(av['debt_carrying'], str) and not isinstance(av['debt_fair_value_only'], str):
        manual.append('debt principal per instrument: only fair value tagged')
    if any(r['form'] in ('20-F',) for r in known) and not any(r.get('form') == '10-Q' for r in known):
        manual.append('quarterly figures: only annual XBRL (20-F); quarters from 6-K text')
    avail_all[tk] = dict(asof=asof, cutoff=cut, last_periodic_filing=last_periodic, fields=av, manual_extraction=manual)
    # filing index from SEC submissions (fetched now)
    idx = dict(tk=tk, cik=cik, asof=asof, note='filings dated on or before the as-of date; stage 1 reads these by hand')
    if fetch:
        sub = fetch_json(f'https://data.sec.gov/submissions/CIK{cik:010d}.json')
        now = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        rel = put(f'sec/submissions/CIK{cik:010d}.json', sub)
        manifest.append(S.entry(out, rel, source=f'https://data.sec.gov/submissions/CIK{cik:010d}.json', retrieved_utc=now,
                                information_date=f'filing history to {now[:10]}', code_version=CODE,
                                label='raw, byte-exact; lists filings after the as-of date too', tk=tk))
        sj = json.loads(sub)
        tables = [sj['filings']['recent']]
        for f in sj['filings'].get('files', []):
            if f['filingFrom'] <= asof and f['filingTo'] >= (dt.date.fromisoformat(asof) - dt.timedelta(days=550)).isoformat():
                b = fetch_json('https://data.sec.gov/submissions/' + f['name'])
                rel = put('sec/submissions/' + f['name'], b)
                manifest.append(S.entry(out, rel, source='https://data.sec.gov/submissions/' + f['name'], retrieved_utc=now,
                                        information_date=f"filings {f['filingFrom']} to {f['filingTo']}", code_version=CODE,
                                        label='raw, byte-exact', tk=tk))
                tables.append(json.loads(b))
                time.sleep(0.2)
        lo = (dt.date.fromisoformat(asof) - dt.timedelta(days=550)).isoformat()
        fil = []
        for t in tables:
            for i in range(len(t['accessionNumber'])):
                d, form = t['filingDate'][i], t['form'][i]
                if lo <= d <= asof and form in MANUAL_FORMS:
                    a = t['accessionNumber'][i]
                    fil.append(dict(filed=d, form=form, accn=a, report_date=t['reportDate'][i], items=t.get('items', [''] * 99)[i],
                                    url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{a.replace('-', '')}/{t['primaryDocument'][i]}"))
        fil.sort(key=lambda x: x['filed'])
        lp = last_periodic[0] if last_periodic else lo
        idx['filings'] = fil
        idx['events_after_last_periodic_filing'] = [f for f in fil if f['filed'] > lp and
                                                    (f['form'].startswith('8-K') or f['form'] == '6-K')]
        time.sleep(0.2)
    else:
        idx['filings'] = 'not fetched (run with --fetch-submissions)'
    rel = f'index/{tk}_{asof}.json'
    S.dump(idx, os.path.join(out, rel))
    manifest.append(S.entry(out, rel, source='derived from sec/submissions', retrieved_utc=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                            information_date=f'filings on or before {asof}', code_version=CODE,
                            label='filing index for manual extraction (values not extracted in stage 0)', tk=tk))
    summary.append((tk, asof, len(rows), len(known), excluded, max_filed, len(restated), len(manual)))

S.dump(avail_all, os.path.join(out, 'availability.json'))
manifest.append(S.entry(out, 'availability.json', source='derived from pit/', retrieved_utc=ZIP_RETRIEVED,
                        information_date='per company as-of', code_version=CODE,
                        label='latest value known at each as-of date; NONE when not tagged'))
# prices: vendor files + audited points (private storage only, never committed)
for sub in ('alphavantage', 'ibkr'):
    src = os.path.join(raw, 'prices', sub)
    em = {m['file']: m for m in json.load(open(os.path.join(raw, 'extract_manifest.json')))}
    for f in sorted(os.listdir(src)):
        rel = f'prices/{sub}/{f}'
        os.makedirs(os.path.join(out, 'prices', sub), exist_ok=True)
        shutil.copyfile(os.path.join(src, f), os.path.join(out, rel))
        m = em[rel]
        manifest.append(S.entry(out, rel, source=m['source'] + ' via ' + m['tool'], retrieved_utc=m['retrieved_utc'],
                                information_date='history to ' + m['retrieved_utc'][:10], code_version=CODE,
                                label='vendor data, private; extracted byte-exact from the session transcript',
                                args=m['args']))
shutil.copyfile(audit_path, os.path.join(out, 'prices', 'price_audit.json'))
manifest.append(S.entry(out, 'prices/price_audit.json', source='scripts/stage0_prices.py', retrieved_utc=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                        information_date='as-traded close at each as-of date', code_version=CODE,
                        label='audited as-of prices with second-source checks'))
S.dump(manifest, os.path.join(out, 'MANIFEST.json'))
print('code', CODE, 'zip', ZIP_SHA[:16], 'retrieved', ZIP_RETRIEVED)
for s in summary:
    print('%-5s %s rows %7d known %7d excluded %7d max_filed %s restated_tags %4d manual %d' % s)
print(len(manifest), 'manifest entries')
