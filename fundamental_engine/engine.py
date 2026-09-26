"""Validated, dated evidence plus deterministic financial and potential analysis."""
from copy import deepcopy
from datetime import date
from .finance import number, ratio, dcf, reverse_dcf, funding_path
from . import __version__

DIMENSIONS = ("market", "adoption", "advantage", "execution", "unit_economics")
SUPPORTED_DCF = {"software", "platform", "industrial", "infrastructure", "nonfinancial"}


def day(value):
    return date.fromisoformat(value)


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: required nonempty text")


def validate(data):
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    as_of = day(data["as_of"])
    company = data["company"]
    for key in ("id", "name", "ticker", "currency", "sector"):
        require_text(company[key], f"company.{key}")
    if company["currency"] != data["currency"] or data["unit"] != "absolute":
        raise ValueError("Use one currency and absolute monetary/share units")
    sources = {}
    for s in data["sources"]:
        for k in ("id", "title", "locator", "kind"):
            require_text(s[k], f"source.{k}")
        if s["id"] in sources:
            raise ValueError("Duplicate source id")
        if s["kind"] == "synthetic" and not data.get("is_demo"):
            raise ValueError("Synthetic sources require is_demo=true")
        if day(s["available_at"]) < day(s["published_at"]):
            raise ValueError("available_at precedes publication")
        sources[s["id"]] = s
    def known(refs):
        if not refs or any(r not in sources for r in refs):
            raise ValueError("Missing or unknown source references")
    periods = sorted(data["financials"], key=lambda x: x["end"])
    last_end = None
    for p in periods:
        start, end = day(p["start"]), day(p["end"])
        if not 330 <= (end-start).days+1 <= 380:
            raise ValueError("Financial input supports standalone annual periods only")
        if last_end is not None and start <= last_end:
            raise ValueError("Annual periods overlap")
        last_end = end
        if day(p["available_at"]) < end:
            raise ValueError("Historical financial data available before period end")
        if p["currency"] != data["currency"] or p["basis"] != "GAAP":
            raise ValueError("Financials require common currency and GAAP basis")
        known(p["source_ids"])
        for key in ("revenue", "gross_profit", "ebit", "net_income", "cfo", "capex",
                    "sbc", "cash", "debt", "shares", "invested_capital"):
            value = p.get(key)
            if value is not None:
                number(value, key, 0 if key in {"revenue", "capex", "sbc", "cash", "debt", "shares"} else None)
    quote = data.get("quote")
    if quote:
        number(quote["price"], "price", 1e-12)
        day(quote["as_of"])
        if quote["currency"] != data["currency"]:
            raise ValueError("Quote currency mismatch")
        known(quote["source_ids"])
    signal_ids = set()
    for sig in data.get("signals", []):
        require_text(sig["id"], "signal.id")
        require_text(sig["claim"], "signal.claim")
        if sig["id"] in signal_ids:
            raise ValueError("Duplicate signal id")
        signal_ids.add(sig["id"])
        if sig["dimension"] not in DIMENSIONS or sig["direction"] not in {"positive", "negative"}:
            raise ValueError("Unknown signal dimension/direction")
        if sig["review_status"] not in {"unverified", "supported", "contradicted"}:
            raise ValueError("Invalid review status")
        day(sig["observed_at"])
        day(sig["reviewed_at"])
        if day(sig["reviewed_at"]) < day(sig["observed_at"]):
            raise ValueError("Review precedes observation")
        known(sig["source_ids"])
    for milestone in data.get("milestones", []):
        for key in ("id", "description", "success_criterion", "failure_criterion"):
            require_text(milestone[key], key)
        day(milestone["due_at"])
    for section in ("valuation", "funding"):
        if data.get(section):
            day(data[section]["assumptions_as_of"])
            known(data[section]["source_ids"])
    if data.get("valuation"):
        v = data["valuation"]
        if v["sbc_policy"] != "future_expensed_existing_claims_in_shares":
            raise ValueError("Use explicit supported SBC policy")
        for key in ("share_equivalents", "excess_cash", "debt", "other_claims"):
            number(v[key], key, 1e-12 if key == "share_equivalents" else 0)
        names = [s["name"] for s in v["scenarios"]]
        if not names or len(set(names)) != len(names):
            raise ValueError("Scenarios need distinct names")
        if not all(s.get("rationale", "").strip() for s in v["scenarios"]):
            raise ValueError("Every scenario requires a rationale")
    return as_of, sources, periods


def analyze(data):
    data = deepcopy(data)
    as_of, sources, periods = validate(data)
    issues = []
    def eligible(refs):
        return all(day(sources[r]["available_at"]) <= as_of for r in refs)
    def assumptions_ok(section):
        return day(section["assumptions_as_of"]) <= as_of and eligible(section["source_ids"])
    active = [p for p in periods if day(p["available_at"]) <= as_of and eligible(p["source_ids"])]
    if len(active) != len(periods):
        issues.append("Some financial periods excluded: not available at analysis cutoff")
    metrics = []
    for i, p in enumerate(active):
        prev = active[i-1] if i else None
        contiguous = prev and (day(p["start"]) - day(prev["end"])).days == 1
        growth = (ratio(p.get("revenue"), prev.get("revenue")) if contiguous else None)
        fcf = p["cfo"] - p["capex"] if p.get("cfo") is not None and p.get("capex") is not None else None
        metrics.append({"end": p["end"], "revenue_growth": growth-1 if growth is not None else None,
                        "gross_margin": ratio(p.get("gross_profit"), p.get("revenue")),
                        "operating_margin": ratio(p.get("ebit"), p.get("revenue")),
                        "reported_fcf": fcf, "fcf_margin": ratio(fcf, p.get("revenue")),
                        "sbc_to_revenue": ratio(p.get("sbc"), p.get("revenue")),
                        "cash_conversion": ratio(p.get("cfo"), p.get("net_income")),
                        "source_ids": p["source_ids"]})
    latest = active[-1] if active else None
    if latest:
        if (as_of-day(latest["end"])).days > 550:
            issues.append("Annual financials are stale (>550 days); valuation is illustrative")
        if latest.get("net_income", 0) is not None and latest.get("cfo") is not None:
            if latest["net_income"] > 0 and latest["cfo"] < 0:
                issues.append("Positive earnings but negative operating cash flow: investigate")
        if metrics[-1]["sbc_to_revenue"] is not None and metrics[-1]["sbc_to_revenue"] > .15:
            issues.append("SBC exceeds 15% of revenue: review economic cost (research flag, not a verdict)")
    else:
        issues.append("No usable annual financial statements at cutoff")
    evidence = {k: {"supporting": [], "opposing": [], "unverified": [], "stale": []} for k in DIMENSIONS}
    for sig in data.get("signals", []):
        if day(sig["observed_at"]) > as_of or day(sig["reviewed_at"]) > as_of or not eligible(sig["source_ids"]):
            issues.append(f"Signal {sig['id']} excluded: future evidence/review")
            continue
        group = evidence[sig["dimension"]]
        # No evidence-count scores: duplicated articles cannot inflate potential.
        if (as_of-day(sig["observed_at"])).days > 365:
            bucket = "stale"
        elif sig["review_status"] == "unverified" or (sig["direction"] == "negative" and sig["review_status"] == "contradicted"):
            bucket = "unverified"
        elif sig["direction"] == "negative" or sig["review_status"] == "contradicted":
            bucket = "opposing"
        else:
            bucket = "supporting"
        group[bucket].append(sig)
    positive = [k for k, v in evidence.items() if v["supporting"]]
    missing = [k for k, v in evidence.items() if not v["supporting"] and not v["opposing"]]
    opposing = [k for k, v in evidence.items() if v["opposing"]]
    potential_status = "early_evidence" if positive else "hypothesis_only"
    if opposing:
        potential_status = "mixed_evidence"
    if len(positive) == len(DIMENSIONS) and not opposing:
        potential_status = "broad_evidence_requires_valuation"
    funding = None
    if data.get("funding"):
        f = data["funding"]
        if assumptions_ok(f):
            funding = funding_path(f["available_cash"], f["periods"])
            if funding["first_gap_period"]:
                issues.append("Funding gap: valuation does not include hypothetical financing dilution")
        else:
            issues.append("Funding assumptions excluded: after cutoff")
    quote = data.get("quote")
    if quote and (day(quote["as_of"]) > as_of or not eligible(quote["source_ids"])):
        quote = None
        issues.append("Quote excluded: unavailable at cutoff")
    if quote and (as_of-day(quote["as_of"])).days > 7:
        issues.append("Quote older than 7 days; do not interpret gap as current opportunity")
    valuations = []
    v = data.get("valuation")
    if v:
        if data["company"]["sector"] not in SUPPORTED_DCF:
            issues.append("DCF unsupported for this sector: dedicated model required")
        elif not assumptions_ok(v):
            issues.append("Valuation assumptions excluded: after cutoff")
        elif latest and latest.get("revenue") is not None:
            for scenario in v["scenarios"]:
                args = (latest["revenue"], v["share_equivalents"], v["excess_cash"], v["debt"], v["other_claims"])
                try:
                    result = dcf(*args, scenario)
                    sensitivity = []
                    for delta in (-.02, 0, .02):
                        sw = scenario["wacc"] + delta
                        if sw <= scenario["terminal_growth"] or sw > 1:
                            continue
                        sensitivity.append({"wacc": sw, "value_per_share": dcf(*args, {**scenario, "wacc": sw})["value_per_share"]})
                    result.update({"name": scenario["name"], "rationale": scenario["rationale"],
                                   "sensitivity": sensitivity, "source_ids": v["source_ids"],
                                   "gap_vs_quote": result["value_per_share"]/quote["price"]-1 if quote else None})
                    if quote:
                        result["reverse_dcf"] = reverse_dcf(quote["price"], *args, scenario)
                    valuations.append(result)
                except (ValueError, KeyError, OverflowError) as e:
                    issues.append(f"Scenario {scenario['name']} not valued: {e}")
    milestones = [{**m, "timing": "due_or_overdue" if day(m["due_at"]) <= as_of else "upcoming"}
                  for m in data.get("milestones", [])]
    status = "research_incomplete"
    if valuations:
        status = "conditional_valuation"
    if funding and funding["first_gap_period"]:
        status = "financing_required"
    if data.get("is_demo"):
        issues.insert(0, "SYNTHETIC DEMO: all figures, sources and claims are fictional")
    return {"engine_version": __version__, "as_of": data["as_of"], "company": data["company"],
            "currency": data["currency"], "is_demo": bool(data.get("is_demo")), "status": status,
            "financial_metrics": metrics, "potential": {"status": potential_status, "evidence": evidence,
            "missing_dimensions": missing, "note": "Review labels are supplied by the analyst, not automatically verified"},
            "funding": funding, "quote": quote, "valuations": valuations, "milestones": milestones,
            "issues": issues, "sources": [s for s in sources.values() if day(s["available_at"]) <= as_of],
            "thesis": data.get("thesis", {}),
            "limitations": ["Annual financial input; no automatic investment recommendation",
                            "Scenario assumptions are user supplied, not predictions or analyst consensus",
                            "No sector-specific valuation for banks, insurers, REITs or pre-revenue biotech",
                            "Funding simulation is separate; no automatic new-equity or debt financing model",
                            "No calibrated success probabilities or target-date price forecast"]}
