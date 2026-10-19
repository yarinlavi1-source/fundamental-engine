"""Stage 0: pull tool outputs out of the session transcript byte-exact, so no file is ever retyped.

Every connector result (claude.ai Project file read, Alpha Vantage, Interactive Brokers) is recorded by the
harness in the session transcript (JSONL). This script finds each tool call, pairs it with its result, and writes
the result text unchanged, plus a manifest line: tool, arguments, retrieval timestamp (from the transcript), and
SHA-256 of the bytes written. When a path was read more than once, the LAST read wins and earlier hashes are kept
in the manifest so a change between reads would be visible.

usage: python scripts/stage0_extract.py TRANSCRIPT.jsonl OUT_DIR
"""
import hashlib, json, os, sys

transcript, out_dir = sys.argv[1:3]
uses, results = {}, {}
for line in open(transcript, encoding='utf-8'):
    d = json.loads(line)
    c = d.get('message', {}).get('content')
    if not isinstance(c, list):
        continue
    for x in c:
        if not isinstance(x, dict):
            continue
        if x.get('type') == 'tool_use':
            uses[x['id']] = (x['name'], x.get('input', {}))
        elif x.get('type') == 'tool_result':
            body = x.get('content')
            if isinstance(body, list):
                body = ''.join(b.get('text', '') for b in body if isinstance(b, dict))
            results[x['tool_use_id']] = (body, d.get('timestamp'))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def write(rel, text, meta):
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = text.encode('utf-8')
    open(path, 'wb').write(data)
    return dict(meta, file=rel, sha256=sha(data), bytes=len(data))


manifest, seen = [], {}
for tid, (name, inp) in uses.items():
    if tid not in results:
        continue
    body, ts = results[tid]
    meta = dict(tool=name, args=inp, retrieved_utc=ts)
    if name == 'Projects' and inp.get('method') == 'project_read':
        try:
            j = json.loads(body)
        except Exception:
            continue
        if 'content' not in j:
            continue
        rel = 'project/' + inp['path']
        seen.setdefault(rel, []).append(sha(j['content'].encode('utf-8')))
        manifest.append(write(rel, j['content'], dict(meta, source='claude.ai Project (connector read)', project_created_at=j.get('created_at'))))
    elif name == 'mcp__Alpha_Vantage_MCP_Server__TOOL_CALL':
        a = inp.get('arguments') or {}
        if isinstance(a, str):
            a = json.loads(a)
        try:
            j = json.loads(body)
        except Exception:
            continue
        if 'result' not in j:
            continue
        rel = f"prices/alphavantage/{a['symbol']}_{inp['tool_name']}.csv"
        manifest.append(write(rel, j['result'], dict(meta, source='Alpha Vantage MCP')))
    elif name == 'mcp__Alpha_Vantage_MCP_Server__SPLITS':
        rel = f"prices/alphavantage/{inp['symbol']}_SPLITS.json"
        manifest.append(write(rel, body, dict(meta, source='Alpha Vantage MCP')))
    elif name == 'mcp__Interactive_Brokers__get_price_history':
        rel = f"prices/ibkr/{inp['contract_id']}_{inp['step']}_{inp.get('period') or inp.get('step_count')}.json"
        manifest.append(write(rel, body, dict(meta, source='Interactive Brokers MCP')))
    elif name == 'mcp__Interactive_Brokers__search_contracts':
        rel = f"prices/ibkr/search_{inp['query']}.json"
        manifest.append(write(rel, body, dict(meta, source='Interactive Brokers MCP')))

# keep only the last entry per file, but record whether earlier reads differed
last = {}
for m in manifest:
    last[m['file']] = m
for rel, hs in seen.items():
    last[rel]['earlier_read_hashes'] = hs[:-1]
    last[rel]['all_reads_identical'] = len(set(hs)) == 1
json.dump(sorted(last.values(), key=lambda m: m['file']), open(os.path.join(out_dir, 'extract_manifest.json'), 'w'), indent=1, ensure_ascii=False)
print(len(last), 'files written to', out_dir)
