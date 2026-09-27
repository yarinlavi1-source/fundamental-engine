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
    rp = subs.add_parser("research-plan", help="Plan research using existing client connectors")
    rp.add_argument("input")
    rr = subs.add_parser("research-review", help="Audit a structured research dossier")
    rr.add_argument("input")
    rr.add_argument("--out", help="Optional JSON report destination")
    pk = subs.add_parser("research-packet", help="Read a research protocol/playbook")
    pk.add_argument("topic")
    cp = subs.add_parser("research-checkpoint")
    cp.add_argument("input")
    cp.add_argument("--case-id")
    cp.add_argument("--expected-revision",type=int,default=0)
    cp.add_argument("--db",default="runs/research-journal.sqlite")
    cl = subs.add_parser("research-load")
    cl.add_argument("case_id")
    cl.add_argument("--revision",type=int)
    cl.add_argument("--db",default="runs/research-journal.sqlite")
    ch = subs.add_parser("research-history")
    ch.add_argument("company_id")
    ch.add_argument("--db",default="runs/research-journal.sqlite")
    cc = subs.add_parser("research-compare")
    cc.add_argument("case_id")
    cc.add_argument("first_revision",type=int)
    cc.add_argument("second_revision",type=int)
    cc.add_argument("--db",default="runs/research-journal.sqlite")
    h = subs.add_parser("history")
    h.add_argument("company_id")
    h.add_argument("--db", default="runs/research.sqlite")
    d = subs.add_parser("compare")
    d.add_argument("first")
    d.add_argument("second")
    d.add_argument("--db", default="runs/research.sqlite")
    vt = subs.add_parser("value", help="Annual fair-value scenarios with dated funding and dilution")
    vt.add_argument("input")
    vt.add_argument("--out", default="runs/valuation")
    vt.add_argument("--diagnostics", action="store_true")
    ds = subs.add_parser("discover", help="Emerging opportunity research, independent of mature-profitability filters")
    ds.add_argument("input")
    ds.add_argument("--out", default="runs/discovery")
    pl = subs.add_parser("plain", help="Eye-level Hebrew verdict: good/not good per metric plus price versus value")
    pl.add_argument("input")
    pl.add_argument("--valuation", help="value_company input JSON to execute and explain")
    pl.add_argument("--out", default="runs/plain")
    pl.add_argument("--json", action="store_true", help="Print the full JSON result instead of the Hebrew text")
    args = parser.parse_args()
    try:
        if args.command == "plain":
            from .plain import plain_verdict
            inputs = read_json(args.input)
            if args.valuation:
                inputs["valuation_case"] = read_json(args.valuation)
            result = plain_verdict(inputs)
            dest = Path(args.out) / result["input_sha256"][:16]
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "input.json").write_text(json.dumps(inputs,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "plain.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "plain.md").write_text(result["text"],encoding="utf-8")
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False) if args.json else result["text"])
        elif args.command == "discover":
            from .discovery import scan
            inputs=read_json(args.input)
            result=scan(inputs)
            dest=Path(args.out)/result['input_sha256'][:16]
            dest.mkdir(parents=True,exist_ok=True)
            for name,obj in [('input',inputs),('discovery',result)]:
                (dest/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
        elif args.command == "value":
            from .valuation import value_company, sensitivity
            from .valuation_report import render_valuation
            from .valuation_tools import stress_test, reverse_price
            from hashlib import sha256
            inputs = read_json(args.input)
            result = value_company(inputs)
            if args.diagnostics and inputs["method"] == "operating":
                result["sensitivity"] = sensitivity(inputs)
                result["stress_tests"] = stress_test(inputs)
                result["reverse_price"] = reverse_price(inputs)
            digest = sha256(json.dumps(inputs,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
            dest = Path(args.out) / digest
            dest.mkdir(parents=True,exist_ok=True)
            (dest / "input.json").write_text(json.dumps(inputs,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "valuation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "valuation.html").write_text(render_valuation(result),encoding="utf-8")
            print(json.dumps({"status":result["status"],"annual_values":result["annual_values"],"report":str((dest/"valuation.html").resolve())},ensure_ascii=False,indent=2))
        elif args.command in {"validate", "analyze"}:
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
        elif args.command in {"research-plan", "research-review", "research-packet"}:
            from .supervisor import plan, review, packet
            if args.command == "research-plan": value = plan(read_json(args.input))
            elif args.command == "research-packet": value = packet(args.topic)
            else: value = review(read_json(args.input))
            rendered = json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
            if args.command == "research-review" and args.out:
                dest = Path(args.out);dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_text(rendered,encoding="utf-8")
                print(json.dumps({"status":value["status"],"report":str(dest.resolve())}))
            else: print(rendered)
        elif args.command in {"research-checkpoint", "research-load", "research-history", "research-compare"}:
            from .journal import Journal
            journal = Journal(args.db)
            try:
                if args.command == "research-checkpoint": value = journal.save(read_json(args.input),args.case_id,args.expected_revision)
                elif args.command == "research-load": value = journal.load(args.case_id,args.revision)
                elif args.command == "research-history": value = journal.history(args.company_id)
                else: value = journal.compare(args.case_id,args.first_revision,args.second_revision)
                print(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
            finally: journal.close()
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
