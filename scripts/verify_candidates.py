"""Stage 3 of discovery: fetch the SEC filer profile (submissions) for screen candidates only.

    SEC_USER_AGENT="app contact@example.com" python scripts/verify_candidates.py runs/universe_2026-10-20 --scan scan.json

Reads the scan (lens candidates, near misses and rows held back by a critical data flag), fetches
data.sec.gov/submissions/CIK##########.json for each issuer (a few hundred calls, not the whole market) and saves them
verbatim under raw/submissions/ with a manifest. universe.sec_profiles() turns them into SIC, filer form and fiscal-year
facts that correct the coarse listing industry before the second scan.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from collect_universe import fetch  # noqa: E402


def candidates(scan, rows):
    syms = set()
    for lst in scan['lenses'].values():
        syms |= {c['symbol'] for c in lst}
    for lst in (scan.get('near_misses') or {}).values():
        syms |= {c['symbol'] for c in lst if isinstance(c, dict)}
    syms |= {r['symbol'] for r in rows if any(f.get('severity') == 'critical' for f in r.get('data_flags') or [])}
    return syms


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--scan', default='scan.json')
    ap.add_argument('--rows', default='rows.json')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    if '@' not in ua:
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    with open(os.path.join(a.folder, a.scan)) as f:
        scan = json.load(f)
    with open(os.path.join(a.folder, a.rows)) as f:
        rows = json.load(f)
    cik = {r['symbol']: (r.get('source') or {}).get('cik') for r in rows}
    want = sorted(s for s in candidates(scan, rows) if cik.get(s))
    out = os.path.join(a.folder, 'raw', 'submissions')
    os.makedirs(out, exist_ok=True)
    calls = []
    for s in want:
        c = int(cik[s])
        name = f'CIK{c:010d}.json'
        if not os.path.exists(os.path.join(out, name)):
            status, body = fetch(f'https://data.sec.gov/submissions/{name}', {'User-Agent': ua, 'Accept-Encoding': 'gzip'})
            if status == 200:
                with open(os.path.join(out, name), 'wb') as f:
                    f.write(body)
            time.sleep(0.12)
        else:
            status, body = 200, open(os.path.join(out, name), 'rb').read()
        calls.append({'symbol': s, 'cik': c, 'file': 'submissions/' + name, 'status': status,
                      'sha256': hashlib.sha256(body).hexdigest() if status == 200 else None,
                      'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    with open(os.path.join(a.folder, 'raw', 'SUBMISSIONS_MANIFEST.json'), 'w') as f:
        json.dump({'calls': calls, 'user_agent': 'redacted (contains a contact e-mail)'}, f, indent=1)
    print(json.dumps({'candidates': len(want), 'fetched_ok': sum(1 for c in calls if c['status'] == 200)}))


if __name__ == '__main__':
    main()
