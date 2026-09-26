import argparse
import json
import os
import sys
from pathlib import Path
from .engine import analyze
from .report import render
from .sec import fetch_companyfacts, select_facts
from .store import Store
from .periods import normalize_concept
from .corpus import Corpus


def read_json(path):
    def reject(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)


def main():
    parser = argparse.ArgumentParser(description="Fundamental and emerging-potential research engine")
    subs = parser.add_subparsers(dest="command", required=True)
    a = subs.add_parser("analyze", help="Validate input, run calculations, save report and immutable snapshot")
    a.add_argument("input")
    a.add_argument("--out", default="runs/latest")
    a.add_argument("--db", default="runs/research.sqlite")
    v = subs.add_parser("validate")
    v.add_argument("input")
    s = subs.add_parser("sec-fetch", help="Fetch/cache raw SEC companyfacts; no silent normalization")
    s.add_argument("cik")
    s.add_argument("--cache", default="runs/sec")
    s.add_argument("--refresh", action="store_true")
    x = subs.add_parser("sec-extract", help="Extract an exact concept/unit at a date cutoff")
    x.add_argument("input")
    x.add_argument("--taxonomy", default="us-gaap")
    x.add_argument("--tag", required=True)
    x.add_argument("--unit", required=True)
    x.add_argument("--as-of", required=True)
    n = subs.add_parser("sec-periods", help="Exact concept annual/quarter/YTD/TTM normalization")
    n.add_argument("input")
    n.add_argument("--taxonomy", default="us-gaap")
    n.add_argument("--tag", required=True)
    n.add_argument("--unit", required=True)
    n.add_argument("--as-of", required=True)
    n.add_argument("--basis", choices=["GAAP", "IFRS"], default="GAAP")
    c = subs.add_parser("source-ingest", help="Ingest source JSON containing body and dated metadata")
    c.add_argument("input")
    c.add_argument("--db", default="runs/sources.sqlite")
    imp = subs.add_parser("source-import", help="Import text/Markdown/searchable PDF plus source metadata JSON")
    imp.add_argument("metadata")
    imp.add_argument("file")
    imp.add_argument("--db", default="runs/sources.sqlite")
    q = subs.add_parser("source-search")
    q.add_argument("query")
    q.add_argument("--as-of", required=True)
    q.add_argument("--db", default="runs/sources.sqlite")
    m = subs.add_parser("mcp", help="Start local stdio MCP server")
    m.add_argument("--corpus", default="runs/sources.sqlite")
    h = subs.add_parser("history")
    h.add_argument("company_id")
    h.add_argument("--db", default="runs/research.sqlite")
    d = subs.add_parser("compare")
    d.add_argument("first")
    d.add_argument("second")
    d.add_argument("--db", default="runs/research.sqlite")
    args = parser.parse_args()
    try:
        if args.command in {"validate", "analyze"}:
            inputs = read_json(args.input)
            result = analyze(inputs)
            if args.command == "validate":
                print(json.dumps({"valid": True, "issues": result["issues"]}, ensure_ascii=False, indent=2))
                return
            dest = Path(args.out)
            dest.mkdir(parents=True, exist_ok=True)
            store = Store(args.db)
            try:
                run_id = store.save(inputs, result)
            finally:
                store.close()
            # Per-run directories ensure subsequent reports never replace prior artifacts.
            dest = dest / run_id
            dest.mkdir()
            (dest / "report.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "report.html").write_text(render(result),encoding="utf-8")
            print(json.dumps({"run_id":run_id,"status":result['status'],"html":str((dest/'report.html').resolve())},indent=2))
        elif args.command == "sec-fetch":
            data = fetch_companyfacts(args.cik, os.environ.get("SEC_USER_AGENT", ""),args.cache,args.refresh)
            print(json.dumps({"cik":data['cik'],"entityName":data.get('entityName'),"cache":str(Path(args.cache).resolve())}))
        elif args.command == "sec-extract":
            print(json.dumps(select_facts(read_json(args.input),args.taxonomy,args.tag,args.unit,args.as_of),indent=2))
        elif args.command == "sec-periods":
            print(json.dumps(normalize_concept(read_json(args.input),args.taxonomy,args.tag,args.unit,args.as_of,args.basis),indent=2))
        elif args.command in {"source-ingest", "source-search", "source-import"}:
            corpus = Corpus(args.db)
            try:
                if args.command == "source-import":
                    from .imports import import_document
                    value = corpus.ingest(import_document(read_json(args.metadata),args.file))
                else:
                    value = corpus.ingest(read_json(args.input)) if args.command == "source-ingest" else corpus.search(args.query,args.as_of)
                print(json.dumps(value,ensure_ascii=False,indent=2))
            finally:
                corpus.close()
        elif args.command == "mcp":
            from .mcp import serve
            serve(args.corpus)
        else:
            store = Store(args.db)
            try:
                value = store.history(args.company_id) if args.command == "history" else store.compare(args.first,args.second)
                print(json.dumps(value,ensure_ascii=False,indent=2))
            finally:
                store.close()
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2)
