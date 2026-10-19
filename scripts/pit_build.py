"""Build the point-in-time fundamentals store from SEC companyfacts.zip.

usage: python scripts/pit_build.py /path/companyfacts.zip /path/tickers.json out.pkl
Keeps companies with >= 8 standalone revenue quarters. Output: {cik: record} (see fundamental_engine.pit.company).
"""
import json, pickle, sys, zipfile, time
from fundamental_engine.pit import company

zpath, tpath, out = sys.argv[1:4]
tick = {}
t = json.load(open(tpath))
for cik, name, tk, ex in t['data']:
    tick.setdefault(int(cik), []).append((tk, ex))
store, n, kept, t0 = {}, 0, 0, time.time()
with zipfile.ZipFile(zpath) as z:
    for info in z.infolist():
        if not info.filename.endswith('.json'):
            continue
        n += 1
        try:
            j = json.loads(z.read(info))
        except Exception:
            continue
        cik = int(j.get('cik', 0) or 0)
        if not j.get('facts', {}).get('us-gaap'):
            continue
        rec = company(j)
        if len(rec['rev']) < 8:
            continue
        rec['cik'] = cik
        rec['tickers'] = tick.get(cik, [])
        store[cik] = rec
        kept += 1
        if n % 2000 == 0:
            print(n, kept, round(time.time() - t0), flush=True)
pickle.dump(store, open(out, 'wb'))
print('files', n, 'kept', kept, 'with ticker', sum(1 for r in store.values() if r['tickers']), flush=True)
