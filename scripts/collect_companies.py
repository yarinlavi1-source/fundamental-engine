"""Discovery handoff, collection step: SEC companyfacts + submissions for the research queue, saved verbatim.

    SEC_USER_AGENT="app contact@example.com" python scripts/collect_companies.py runs/universe_2026-10-20 [--symbols A B]

Without --symbols the queue in cards.json (written by `fundamental_engine universe`) is used, one company per lane.
Each company is checked before analysis (handoff.identity_check: reporting currency, depositary-share ratio); a blocked
company keeps its folder with BLOCKED.json and the next candidate of the same lane is collected instead (at most
--tries per lane). Writes companies/<T>/company.json, companies/<T>/raw/{sec__companyfacts.json, sec__submissions.json,
listing__price-<T>__current.json, MANIFEST.json} and companies/QUEUE.json. Then `fundamental_engine deep FOLDER
--as-of D` runs the full pipeline on the queue and writes the comparison. A broker quote saved as
raw/ib__price-snapshot-<T>__current.json (pipeline.collection_plan) takes precedence over the listing price.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
from collect_universe import fetch  # noqa: E402
from fundamental_engine import handoff  # noqa: E402


def collect(folder, c, ua):
    """Fetch one company's SEC payloads (skipped when already saved) and its listing price; -> identity check."""
    os.makedirs(c['raw'], exist_ok=True)
    with open(os.path.join(c['folder'], 'company.json'), 'w', encoding='utf-8') as f:
        json.dump(c['company'], f, indent=1, ensure_ascii=False)
    man_path = os.path.join(c['raw'], 'MANIFEST.json')
    old = {}
    if os.path.exists(man_path):
        with open(man_path, encoding='utf-8') as f:
            old = {x['file']: x for x in json.load(f)['calls']}
    calls = []
    for call in c['calls']:
        path = os.path.join(c['raw'], call['file'])
        if os.path.exists(path) and (old.get(call['file']) or {}).get('status') == 200:
            calls.append(old[call['file']])
            continue
        status, body = fetch(call['url'], {'User-Agent': ua, 'Accept-Encoding': 'gzip'})
        rec = {'file': call['file'], 'provider': 'sec', 'url': call['url'], 'status': status,
               'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'note': 'verbatim payload'}
        if status == 200:
            with open(path, 'wb') as f:
                f.write(body)
            rec['sha256'] = hashlib.sha256(body).hexdigest()
        calls.append(rec)
        time.sleep(0.12)
    raw_u = os.path.join(folder, 'raw')
    lp = handoff.listing_price(raw_u, c['symbol'])
    if lp:
        name = f"listing__price-{c['symbol']}__current.json"
        with open(os.path.join(c['raw'], name), 'w', encoding='utf-8') as f:
            json.dump(lp, f, indent=1)
        calls.append({'file': name, 'provider': 'nasdaq', 'status': 200, 'retrieved_at_utc': lp['retrieved_at_utc'],
                      'note': 'extracted from the universe listing file (sha256 ' + str(lp['sha256_of_listing_file']) + ')'})
    with open(man_path, 'w', encoding='utf-8') as f:
        json.dump({'ticker': c['symbol'], 'calls': calls, 'user_agent': 'redacted (contains a contact e-mail)'}, f, indent=1)
    facts = os.path.join(c['raw'], 'sec__companyfacts.json')
    if not os.path.exists(facts):
        return {'status': 'blocked', 'reasons': ['SEC companyfacts not retrieved']}
    with open(facts, encoding='utf-8') as f:
        chk = handoff.identity_check(json.load(f), handoff.listing_row(raw_u, c['symbol']), c['company'])
    blocked = os.path.join(c['folder'], 'BLOCKED.json')
    if chk['status'] == 'blocked':
        with open(blocked, 'w', encoding='utf-8') as f:
            json.dump(chk, f, indent=1)
    elif os.path.exists(blocked):
        os.remove(blocked)
    return chk


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--symbols', nargs='+')
    ap.add_argument('--tries', type=int, default=4, help='candidates tried per lane before the lane is reported empty')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    if '@' not in ua:
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    with open(os.path.join(a.folder, 'rows.json'), encoding='utf-8') as f:
        rows = json.load(f)
    if a.symbols:
        wanted = [{'lane': None, 'try': [s]} for s in a.symbols]
    else:
        with open(os.path.join(a.folder, 'cards.json'), encoding='utf-8') as f:
            cs = json.load(f)
        chosen = {q['symbol'] for q in cs['queue']}
        wanted = [{'lane': q['lane'], 'try': [q['symbol']] + [s for s in handoff.lane_candidates(cs, q['lane'])
                                                                if s != q['symbol'] and s not in chosen]}
                  for q in cs['queue']]
    queue, taken = [], set()
    for w in wanted:
        entry = {'lane': w['lane'], 'symbol': None, 'blocked': []}
        for s in [x for x in w['try'] if x not in taken][:a.tries]:
            p = handoff.plan(a.folder, [s], rows)
            if not p['companies']:
                entry['blocked'].append({'symbol': s, 'reasons': ['not in the universe rows']})
                continue
            chk = collect(a.folder, p['companies'][0], ua)
            if chk['status'] == 'ok':
                entry['symbol'] = s
                entry['identity'] = {k: chk.get(k) for k in ('reporting_currency', 'depositary_shares', 'note')}
                taken.add(s)
                break
            entry['blocked'].append({'symbol': s, 'reasons': chk['reasons']})
        queue.append(entry)
    os.makedirs(os.path.join(a.folder, 'companies'), exist_ok=True)
    with open(os.path.join(a.folder, 'companies', 'QUEUE.json'), 'w', encoding='utf-8') as f:
        json.dump({'queue': queue, 'built_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}, f, indent=1)
    print(json.dumps(queue, indent=1))


if __name__ == '__main__':
    main()
