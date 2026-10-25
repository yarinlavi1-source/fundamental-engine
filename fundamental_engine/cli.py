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


def index_input_for(arg, as_of, candidates):
    """Decision index for a run: --index FILE takes that calibration's index_input; --index none applies none (the
    generic risk-free + ERP assumption unless the inputs set an index); without --index, the newest calibration among
    candidates dated on or before as_of, at most calibration.MAX_AGE_DAYS older and computed by this engine version
    (calibration.pick), else none. Returns (index_input, used)."""
    from .calibration import pick, MAX_AGE_DAYS, ENGINE_VERSION
    if arg and arg.strip().lower() == "none":
        return None, "generic market assumption (--index none)"
    if arg:
        return read_json(arg).get("index_input"), arg
    hit = pick(as_of, candidates)
    if hit:
        return hit["index_input"], (f"{hit['path']} (as_of {hit['as_of']}, engine {hit['engine_version']}, newest calibration on "
                                    "or before the run)")
    return None, (f"generic market assumption: no calibration file of engine {ENGINE_VERSION} dated on or before {as_of} within "
                  f"{MAX_AGE_DAYS} days (a sweep of another engine version is another engine's market: rerun scripts/calibrate.py)")


def with_index(inp, index_input, explicit):
    """inputs with decision.index set: an explicit --index replaces the inputs' own index; an automatic pick only fills it."""
    inp = inp or {}
    if not index_input or (not explicit and (inp.get("decision") or {}).get("index")):
        return inp
    return {**inp, "decision": {**(inp.get("decision") or {}), "index": index_input}}


def json_object_arg(value, option):
    """A JSON object given inline (the argument starts with '{') or as a path to a JSON file."""
    def reject(constant):
        raise ValueError(f"Non-finite JSON number: {constant}")
    text = (value or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text, parse_constant=reject)
        except json.JSONDecodeError as error:
            raise ValueError(f"{option}: inline JSON is invalid ({error.msg} at line {error.lineno} column {error.colno})") from None
    elif text and Path(text).is_file():
        data = read_json(text)
    else:
        raise ValueError(f"{option}: expected a JSON file path or an inline JSON object starting with '{{'; "
                         f"got {value!r}, which is neither an existing file nor inline JSON")
    if not isinstance(data, dict):
        raise ValueError(f"{option}: expected a JSON object, got {type(data).__name__}")
    return data


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
    vd = subs.add_parser("value-drivers", help="Build a valuation case from a ratio driver spec, execute it and run diagnostics")
    vd.add_argument("input"); vd.add_argument("--out", default="runs/valuation"); vd.add_argument("--diagnostics", action="store_true")
    va = subs.add_parser("valuation-audit", help="Execute and reconcile valuation inputs before a price conclusion")
    va.add_argument("input")
    ds = subs.add_parser("discover", help="Emerging opportunity research, independent of mature-profitability filters")
    ds.add_argument("input")
    ds.add_argument("--out", default="runs/discovery")
    pl = subs.add_parser("plain", help="Eye-level Hebrew verdict: good/not good per metric plus price versus value")
    pl.add_argument("input")
    pl.add_argument("--valuation", help="value_company input JSON to execute and explain")
    pl.add_argument("--dossier", help="Full research dossier to re-execute and bind to the valuation input")
    pl.add_argument("--out", default="runs/plain")
    pl.add_argument("--json", action="store_true", help="Print the full JSON result instead of the Hebrew text")
    ty = subs.add_parser("classify", help="Company type: life-cycle stage, Lynch category and research focus")
    ty.add_argument("input")
    pp = subs.add_parser("pipeline-plan", help="Connector calls and raw file names for one company (fetches nothing)")
    pp.add_argument("ticker")
    pp.add_argument("as_of")
    pp.add_argument("--cik")
    pr = subs.add_parser("pipeline", help="Single decision path from a raw connector folder to verdict, report and Hebrew paragraph")
    pr.add_argument("raw_dir", nargs="?", help="Raw connector folder written per pipeline-plan")
    pr.add_argument("--factbook", help="Existing FactBook JSON instead of a raw folder")
    pr.add_argument("--company", help="JSON file, or inline JSON object starting with '{', with ticker, name, currency, sector, "
                                      "industry, ... (with raw_dir)")
    pr.add_argument("--as-of", help="Information cutoff (with raw_dir)")
    pr.add_argument("--inputs", help="JSON with analyst inputs (card, forecast, reviewer, decision)")
    pr.add_argument("--qualitative", help="JSON with dated sources and qualitative items to add to the FactBook")
    pr.add_argument("--index", help="calibration JSON (calibration.market_implied) whose index_input becomes inputs.decision.index; "
                                    "'none' applies no calibration (the generic risk-free + ERP assumption unless --inputs sets "
                                    "decision.index); default: the newest runs/universe_*/calibration.json of this engine version dated on or "
                                    "before the run, within calibration.MAX_AGE_DAYS (calibration.pick), filling decision.index only "
                                    "when --inputs has none")
    pr.add_argument("--out", required=True, help="Output folder (keep it outside Git for private or vendor data)")
    pr.add_argument("--macro", help="saved FRED folder (scripts/collect_market_context.py, runs/macro/fred_<date>): its readings at "
                                    "as_of become inputs.market_context.regime (context only: moves no value)")
    pr.add_argument("--country-risk", action="store_true", help="switch the market_context stage on: a country-risk row in the "
                    "cost of equity from the annual report's revenue / long-lived assets by geography (market_context.py)")
    sc = subs.add_parser("screen", help="Discovery scan of universe rows (JSON list or {rows: [...]}) into lenses and a shortlist")
    sc.add_argument("rows")
    sc.add_argument("--options", help="JSON options (themes, top_n, sector cap, anchors)")
    sc.add_argument("--out")
    un = subs.add_parser("universe", help="Build broad discovery rows from a saved raw folder (scripts/collect_universe.py) and scan them")
    un.add_argument("folder", help="runs/universe_<date> (raw/ inside; written by scripts/collect_universe.py)")
    un.add_argument("--as-of", required=True)
    un.add_argument("--min-market-cap", type=float, default=2e8)
    un.add_argument("--options", help="JSON options passed to screen.scan")
    un.add_argument("--exploratory", help="JSON {symbol: {reason, evidence: [{source, date, statement}]}}: business evidence for names "
                                          "the numeric lanes did not admit")
    un.add_argument("--max-full", type=int, default=6, help="research queue size (full analyses to run next)")
    un.add_argument("--insiders", help="insider purchase clusters JSON (insiders.clusters) attached to cards as signals")
    un.add_argument("--channels", help="discovery channels folder (fundamental_engine.channels layout): leads from every source "
                                       "attached to cards; writes leads.json and channels_he.md")
    rp = subs.add_parser("run", help="From discovery to a portfolio proposal on collected data: research list, proposed book vs "
                                     "the read-only holdings and the index, Hebrew report (runbook.py); never an order")
    rp.add_argument("folder", help="universe folder (cards, companies/*/out/casefile.json)")
    rp.add_argument("--as-of", required=True)
    rp.add_argument("--positions", help="read-only broker positions JSON ({'positions': [...]}, kept outside Git)")
    rp.add_argument("--exposures", help="JSON {symbol: [economic driver tags]} or a holdings_info file")
    rp.add_argument("--calibration", help="calibration.json (engine-consistent market return)")
    rp.add_argument("--backtest", help="engine_backtest summary.json (edge reliability)")
    rp.add_argument("--names", nargs="+", default=["large500", "holdings", ""])
    rp.add_argument("--out", help="output folder (default <folder>/run_<as_of>)")
    dp = subs.add_parser("deep", help="Full pipeline for the discovery research queue (companies/QUEUE.json from "
                                      "scripts/collect_companies.py) and a cross-company comparison")
    dp.add_argument("folder", help="universe folder (cards.json, companies/<T>/raw)")
    dp.add_argument("--as-of", required=True)
    dp.add_argument("--symbols", nargs="+", help="run these company folders instead of the queue")
    dp.add_argument("--name", help="read companies/QUEUE_<name>.json and write comparison_<name>.json/_he.md (e.g. holdings)")
    dp.add_argument("--index", help="calibration JSON (calibration.market_implied): its index_input becomes inputs.index for every company; "
                                    "'none' applies no calibration; default: the newest calibration.json in the folder or a sibling "
                                    "runs/universe_* folder of this engine version dated on or before --as-of, within calibration.MAX_AGE_DAYS "
                                    "(calibration.pick), "
                                    "for companies whose inputs.json sets no decision.index")
    dp.add_argument("--market", help="dated market inputs JSON {risk_free, equity_risk_premium} (each {value, as_of, title, url}): "
                                     "forecast inputs for every company that does not set its own; risk_free only for USD reporters")
    dp.add_argument("--macro", help="saved FRED folder (scripts/collect_market_context.py): the regime readings at --as-of become "
                                    "inputs.market_context.regime for every company (context only: moves no value)")
    dp.add_argument("--country-risk", action="store_true", help="switch the market_context stage on for every company: a "
                    "country-risk row from its own geography disclosures (a company's inputs.market_context overrides apply)")
    pf = subs.add_parser("portfolio", help="READ-ONLY analysis of saved Interactive Brokers payloads; prints to stdout, stores nothing")
    pf.add_argument("positions")
    pf.add_argument("--summary")
    pf.add_argument("--as-of", required=True)
    pf.add_argument("--holdings", help="JSON {symbol: {sector, exposures, exposure_weights, casefile_summary, ...}}")
    pf.add_argument("--options", help="JSON file, or inline JSON object starting with '{', of analysis options: scenarios, "
                                      "single_name_cap, link_threshold, factor_tags, driver_tags, factor_weight_share, ...")
    qr = subs.add_parser("quarter-review", help="Results-day review: one-offs, operating leverage, guidance, cycle, listing premium, bank checks")
    qr.add_argument("input")
    qr.add_argument("--json", action="store_true", help="Print the full JSON result instead of the Hebrew text")
    er = subs.add_parser("econ-review", help="Business economics: identities, growth split, overlays and the Hebrew rows (not a valuation)")
    er.add_argument("input", help="The econ case: lines, kpis, segments and overlay blocks, each with its sources")
    er.add_argument("--options", help="JSON file, or inline JSON object starting with '{': model, or card_type / profile_text / "
                                      "override; prior, upstream (earlier econ reviews); names_he; revisions and prices")
    er.add_argument("--json", action="store_true", help="Print the full JSON result instead of the Hebrew text")
    ep = subs.add_parser("pack-to-inputs", help="Evidence pack (ticker workflow) -> pipeline inputs.json and the evidence bundle "
                                               "that registers its sources (strict: unknown keys, missing evidence, tiers)")
    ep.add_argument("pack", help="evidence_pack.json (fundamental_engine/evidence_pack.py documents the format)")
    ep.add_argument("--out", required=True, help="inputs.json to write")
    ep.add_argument("--bundle", help="evidence_bundle.json to write (load it into the FactBook so the cited ids resolve)")
    im = subs.add_parser("import-av", help="Map saved Alpha Vantage statement JSON files into plain history rows")
    im.add_argument("income")
    im.add_argument("--balance")
    im.add_argument("--cash-flow")
    im.add_argument("--years", type=int, default=5)
    im.add_argument("--as-of")
    args = parser.parse_args()
    try:
        if args.command == "pipeline-plan":
            from .pipeline import collection_plan
            print(json.dumps(collection_plan(args.ticker, args.as_of, args.cik), indent=2))
            return
        if args.command == "pipeline":
            if bool(args.raw_dir) == bool(args.factbook):
                raise ValueError("give either raw_dir (with --company and --as-of) or --factbook")
            if not args.factbook and not (args.company and args.as_of):
                raise ValueError("raw_dir needs --company and --as-of")
            company = None if args.factbook else json_object_arg(args.company, "--company")
            from .pipeline import factbook_from_run_dir, run, summary
            from .render import markdown, hebrew_paragraph, hebrew_table
            from .decision import price_implied_growth
            from .facts import add_qualitative_bundle
            if args.factbook:
                fb = read_json(args.factbook)
            else:
                fb = factbook_from_run_dir(args.raw_dir, company, args.as_of)
            if args.qualitative:
                add_qualitative_bundle(fb, read_json(args.qualitative))
            index_input, index_used = index_input_for(args.index, args.as_of or fb.get('as_of'),
                                                      sorted(Path('runs').glob('universe_*/calibration.json')))
            inp = with_index(read_json(args.inputs) if args.inputs else None, index_input, bool(args.index)) or None
            if args.country_risk:
                from .market_context import with_country_risk
                inp = with_country_risk(inp)
            if args.macro:
                from .market_context import read_regime, with_regime
                inp = with_regime(inp, read_regime(args.macro, fb['as_of']))
            cf = run(fb, inp)
            out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
            (out / "factbook.json").write_text(json.dumps(fb, indent=1, ensure_ascii=False), encoding="utf-8")
            (out / "casefile.json").write_text(json.dumps(cf, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            (out / "report.md").write_text(markdown(cf), encoding="utf-8")
            (out / "paragraph.txt").write_text(hebrew_paragraph(cf), encoding="utf-8")
            cf['price_implied_growth'] = price_implied_growth(cf)       # reverse-DCF for the table; not run inside the pipeline
            (out / "table_he.md").write_text(hebrew_table(cf), encoding="utf-8")
            print(json.dumps({'summary': summary(cf), 'stages': cf['stages'], 'errors': cf['errors'], 'index': index_used, 'out': str(out)},
                             indent=2, ensure_ascii=False))
            return
        if args.command == "screen":
            from .screen import scan
            data = read_json(args.rows)
            result = scan(data['rows'] if isinstance(data, dict) else data, read_json(args.options) if args.options else None)
            text = json.dumps(result, indent=2, ensure_ascii=False)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
            print(json.dumps({'shortlist': [s['symbol'] for s in result['shortlist']], 'lenses': {k: [c['symbol'] for c in v] for k, v in result['lenses'].items()},
                              'no_data': len(result['no_data']), 'notes': result['notes'][:3]}, indent=2, ensure_ascii=False))
            return
        if args.command == "universe":
            from .universe import build, sec_profiles, apply_profiles
            from .screen import scan
            raw = Path(args.folder) / 'raw'
            rows, report = build(str(raw), args.as_of, args.min_market_cap)
            prof = sec_profiles(str(raw), {int(r['source']['cik']): r['symbol'] for r in rows if (r.get('source') or {}).get('cik')})
            apply_profiles(rows, prof)
            from .candidates import cards, research_queue, render_he
            result = scan(rows, read_json(args.options) if args.options else None)
            sig, chan = None, None
            if args.insiders:
                from .insiders import evidence as insider_evidence
                sig = {c['symbol']: [insider_evidence(c)] for c in read_json(args.insiders)
                       if c.get('n_buyers', 0) >= 3 or (c.get('share_of_market_cap') or 0) >= .0005}
            if args.channels:
                # every saved discovery channel (SEC events, 8-K items, revenue frames, insiders, news, estimates, social...)
                from . import channels as CH, leads as LD
                chan = CH.collect(args.channels, rows, args.as_of, submissions_dir=str(raw / 'submissions'))
                merged = LD.merge(chan['leads'])
                sig = {**(sig or {}), **LD.card_signals(merged)}
                (Path(args.folder) / 'leads.json').write_text(json.dumps({'as_of': args.as_of, 'channels': chan['channels'],
                                                                          'missing': chan['missing'], 'leads': chan['leads'],
                                                                          'prelisting': chan['prelisting']}, ensure_ascii=False,
                                                                         allow_nan=False, default=str), encoding='utf-8')
                (Path(args.folder) / 'channels_he.md').write_text(CH.summary_he(merged, chan['prelisting']), encoding='utf-8')
            card_set = cards(result, read_json(args.exploratory) if args.exploratory else None, signals=sig)
            queue = research_queue(card_set, args.max_full)
            card_set['queue'] = queue
            for name, obj in (('rows.json', rows), ('scan.json', result), ('coverage.json', report), ('cards.json', card_set)):
                (Path(args.folder) / name).write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            order = {q['symbol']: i for i, q in enumerate(queue)}
            first = sorted(card_set['cards'], key=lambda c: order.get(c['symbol'], len(order)))
            (Path(args.folder) / 'cards_he.md').write_text(render_he({'cards': first}, len(queue) or None), encoding='utf-8')
            print(json.dumps({'rows': len(rows), 'sec_profiles': len(prof), 'excluded': report['excluded'],
                              'lenses': {k: [c['symbol'] for c in v] for k, v in result['lenses'].items()},
                              'shortlist': [c['symbol'] for c in result['shortlist']],
                              'research_queue': queue, 'cards': len(card_set['cards']), 'card_notes': card_set['notes'],
                              'channels': chan and {'leads_by_channel': chan['channels'], 'missing': chan['missing'],
                                                    'prelisting': len(chan['prelisting'])},
                              'next': 'scripts/verify_candidates.py fetches SEC profiles for new candidates; rerun this command'},
                             indent=2, ensure_ascii=False))
            return
        if args.command == "run":
            from . import runbook
            exp = read_json(args.exposures) if args.exposures else None
            if isinstance(exp, dict) and 'holdings_info' in exp:
                exp = {k: v.get('exposures') or [] for k, v in exp['holdings_info'].items()}
            res = runbook.run(args.folder, args.as_of, read_json(args.positions) if args.positions else None, exp, tuple(args.names),
                              args.calibration, args.backtest)
            path = runbook.write(res, args.out or str(Path(args.folder) / f'run_{args.as_of}'))
            print(json.dumps({'report': path, 'index_weight': res['plan']['index_weight'], 'active_weight': res['plan']['active_weight'],
                              'edge_reliability': res['reliability'], 'research_list': [r['symbol'] for r in res['research']],
                              'holdings_to_review': res['plan']['holdings_to_review'], 'notes': res['notes'],
                              'meaning': 'illustrative research output; no order is placed and no position changes without explicit approval'},
                             indent=2, ensure_ascii=False))
            return
        if args.command == "deep":
            from . import handoff
            from .render import markdown, hebrew_paragraph, hebrew_table
            from .decision import price_implied_growth
            from .pipeline import summary
            base = Path(args.folder)
            suffix = f'_{args.name}' if args.name else ''
            qf = base / 'companies' / f'QUEUE{suffix}.json'
            q = read_json(qf) if qf.exists() else None
            if not args.symbols and q is None:
                raise ValueError(f'{qf} not found: run scripts/collect_companies.py first or pass --symbols')
            syms = args.symbols or [e['symbol'] for e in q['queue'] if e['symbol']]
            card_set = read_json(base / 'cards.json') if (base / 'cards.json').exists() else None
            cfs, ran = [], []
            index_input, index_used = index_input_for(args.index, args.as_of, [base / 'calibration.json']
                                                      + sorted(base.parent.glob('universe_*/calibration.json')))
            market = read_json(args.market) if args.market else None
            regime = None
            if args.macro:
                from .market_context import read_regime, with_regime
                regime = read_regime(args.macro, args.as_of)
            for s in syms:
                folder = base / 'companies' / s
                inp = read_json(folder / 'inputs.json') if (folder / 'inputs.json').exists() else {}
                inp = with_index(inp, index_input, bool(args.index))
                if market:
                    inp = handoff.with_market_inputs(inp, market, str(folder))
                if args.country_risk:
                    from .market_context import with_country_risk
                    inp = with_country_risk(inp)
                if regime:
                    inp = with_regime(inp, regime)
                fb, cf = handoff.run_company(str(folder), args.as_of, inputs=inp or None)
                out = folder / 'out'; out.mkdir(parents=True, exist_ok=True)
                (out / "factbook.json").write_text(json.dumps(fb, indent=1, ensure_ascii=False), encoding="utf-8")
                (out / "casefile.json").write_text(json.dumps(cf, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
                (out / "report.md").write_text(markdown(cf), encoding="utf-8")
                (out / "paragraph.txt").write_text(hebrew_paragraph(cf), encoding="utf-8")
                cf['price_implied_growth'] = price_implied_growth(cf)       # reverse-DCF for the table; not run inside the pipeline
                (out / "table_he.md").write_text(hebrew_table(cf), encoding="utf-8")
                pack = handoff.reading_pack(str(folder))
                if pack:
                    (out / 'reading').mkdir(exist_ok=True)
                    for k, txt in pack.items():
                        (out / 'reading' / f'{k}.txt').write_text(txt, encoding='utf-8')
                cfs.append(json.loads(json.dumps(cf, default=str)))
                ran.append({'symbol': s, 'stages': cf['stages'], 'errors': cf['errors'], 'summary': summary(cf)})
            comp = handoff.compare(cfs, card_set)
            comp['blocked'] = [b for e in (q or {}).get('queue', []) for b in e.get('blocked', [])]
            comp['empty_lanes'] = [e['lane'] for e in (q or {}).get('queue', []) if not e['symbol']]
            (base / 'companies' / f'comparison{suffix}.json').write_text(json.dumps(comp, indent=1, ensure_ascii=False, allow_nan=False),
                                                                      encoding='utf-8')
            (base / 'companies' / f'comparison{suffix}_he.md').write_text(handoff.render_he(comp), encoding='utf-8')
            print(json.dumps({'ran': ran, 'blocked': comp['blocked'], 'empty_lanes': comp['empty_lanes'], 'drafts': comp['drafts'], 'index': index_used},
                             indent=2, ensure_ascii=False, default=str))
            return
        if args.command == "portfolio":
            options = json_object_arg(args.options, "--options") if args.options else None
            from .portfolio import from_ib, analyze as analyze_book
            book = from_ib(read_json(args.positions), read_json(args.summary) if args.summary else None, as_of=args.as_of)
            analysis = analyze_book(book, read_json(args.holdings) if args.holdings else None, options)
            print(json.dumps({'book': book, 'analysis': analysis}, indent=2, ensure_ascii=False))
            return
        if args.command == "valuation-audit":
            from .valuation import value_company
            r = value_company(read_json(args.input))
            print(json.dumps({'input_sha256': r['input_sha256'], **r['underwriting_audit']},ensure_ascii=False,indent=2,allow_nan=False))
        elif args.command == "classify":
            from .profile import classify_company
            print(json.dumps(classify_company(read_json(args.input)),ensure_ascii=False,indent=2,allow_nan=False))
        elif args.command == "quarter-review":
            from .quarter import quarter_review
            result = quarter_review(read_json(args.input))
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False) if args.json else result["text"])
        elif args.command == "econ-review":
            from .econ import econ_review
            result = econ_review(read_json(args.input), json_object_arg(args.options, "--options") if args.options else None)
            print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False) if args.json else result["text"])
        elif args.command == "pack-to-inputs":
            from .evidence_pack import to_inputs, to_bundle
            pack = read_json(args.pack)
            inputs = to_inputs(pack)
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(inputs, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            if args.bundle:
                Path(args.bundle).parent.mkdir(parents=True, exist_ok=True)
                Path(args.bundle).write_text(json.dumps(to_bundle(pack), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            meta = inputs["evidence_pack"]
            print(json.dumps({"inputs": args.out, "bundle": args.bundle, "pack_sha256": meta["pack_sha256"],
                              "overrides": len(inputs.get("forecast", {}).get("overrides", [])),
                              "company_path": sorted((inputs.get("company_path") or {}).get("scenarios", {})),
                              "guidance_adjustments": meta["guidance_adjustments"], "notes": meta["notes"]},
                             ensure_ascii=False, indent=2))
        elif args.command == "import-av":
            from .connectors import from_alpha_vantage
            value = from_alpha_vantage(read_json(args.income), read_json(args.balance) if args.balance else None,
                                       read_json(args.cash_flow) if args.cash_flow else None, args.years, args.as_of)
            print(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
        elif args.command == "plain":
            from .plain import plain_verdict
            inputs = read_json(args.input)
            if args.valuation:
                inputs["valuation_case"] = read_json(args.valuation)
            if args.dossier:
                inputs["research_dossier"] = read_json(args.dossier)
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
        elif args.command == "value-drivers":
            from .drivers import build_case, implied_growth_shift
            from .valuation import value_company, sensitivity
            from .valuation_report import render_valuation
            from .valuation_tools import stress_test, reverse_price, implied_cost_of_equity, discount_rate_band
            spec = read_json(args.input)
            case = build_case(spec)
            result = value_company(case)
            if args.diagnostics:
                result["sensitivity"] = sensitivity(case)
                result["stress_tests"] = stress_test(case)
                result["reverse_price"] = reverse_price(case)
                result["implied_cost_of_equity"] = implied_cost_of_equity(case)
                result["discount_rate_band"] = discount_rate_band(case)
                result["implied_growth_shift"] = implied_growth_shift(spec)
            dest = Path(args.out) / result["input_sha256"][:16]
            dest.mkdir(parents=True,exist_ok=True)
            (dest / "spec.json").write_text(json.dumps(spec,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "case.json").write_text(json.dumps(case,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "valuation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
            (dest / "valuation.html").write_text(render_valuation(result),encoding="utf-8")
            summary = {"status":result["status"],"annual_values":result["annual_values"],"case":str((dest/"case.json").resolve()),
                       "report":str((dest/"valuation.html").resolve())}
            for key in ("implied_cost_of_equity","discount_rate_band","implied_growth_shift"):
                if key in result: summary[key] = result[key]
            print(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
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


if __name__ == "__main__":
    main()
