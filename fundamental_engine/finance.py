"""Pure financial functions. Monetary inputs are absolute currency units."""
import math
from datetime import date, timedelta


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}: below {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label}: above {maximum}")
    return float(value)


def ratio(a, b):
    return a / b if a is not None and b is not None and b > 0 else None


def standalone_ytd(current, previous):
    """Only subtract matching YTD definitions, currency and fiscal start."""
    for key in ("metric", "currency", "unit", "start", "basis", "scope"):
        if current[key] != previous[key]:
            raise ValueError(f"YTD mismatch: {key}")
    if date.fromisoformat(current["end"]) <= date.fromisoformat(previous["end"]):
        raise ValueError("YTD periods out of order")
    return {**current, "start": (date.fromisoformat(previous["end"]) + timedelta(days=1)).isoformat(),
            "value": number(current["value"], "current") - number(previous["value"], "previous"),
            "derived_from": [current.get("source_id"), previous.get("source_id")]}


def ttm(quarters):
    if len(quarters) != 4:
        raise ValueError("TTM requires four standalone quarters")
    rows = sorted(quarters, key=lambda r: r["start"])
    for i, row in enumerate(rows):
        if row.get("kind") != "duration":
            raise ValueError("Cannot sum balance-sheet instants")
        for key in ("metric", "currency", "unit", "basis", "scope"):
            if row[key] != rows[0][key]:
                raise ValueError(f"TTM mismatch: {key}")
        start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
        if not 70 <= (end - start).days + 1 <= 105:
            raise ValueError("Not a standalone quarter")
        if i and start != date.fromisoformat(rows[i-1]["end"]) + timedelta(days=1):
            raise ValueError("TTM periods overlap or have gaps")
    return sum(number(r["value"], "quarter value") for r in rows)


def dcf(base_revenue, shares, excess_cash, debt, other_claims, scenario):
    """FCFF with end-year discounting; SBC expense included in operating margin.

    Base share equivalents cover EXISTING claims; future SBC is expensed in
    margins. No additional future SBC dilution is imposed. Financing issuance
    is not modeled: funding gaps must be reviewed separately.
    """
    rev = number(base_revenue, "base_revenue", 0)
    shares = number(shares, "share_equivalents", 1e-12)
    cash = number(excess_cash, "excess_cash", 0)
    debt = number(debt, "debt", 0)
    claims = number(other_claims, "other_claims", 0)
    wacc = number(scenario["wacc"], "wacc", 0.000001, 1)
    g = number(scenario["terminal_growth"], "terminal_growth", 0, 0.1)
    roc = number(scenario["terminal_roic"], "terminal_roic", 0.000001, 1)
    if g >= wacc or g > roc:
        raise ValueError("Terminal growth must be below WACC and no greater than ROIC")
    years = scenario["years"]
    if not 1 <= len(years) <= 30:
        raise ValueError("Use 1..30 explicit forecast years")
    rows, pv = [], 0.0
    for i, year in enumerate(years, 1):
        growth = number(year["growth"], "growth", -0.99, 5)
        margin = number(year["operating_margin"], "operating_margin", -5, 1)
        tax = number(year["cash_tax_rate"], "cash_tax_rate", 0, 1)
        previous_revenue = rev
        rev *= 1 + growth
        if scenario.get("reinvestment_mode", "absolute") == "sales_to_capital":
            efficiency = number(year["sales_to_capital"], "sales_to_capital", 1e-12)
            base = number(year.get("base_net_reinvestment", 0), "base_net_reinvestment", 0)
            reinvest = max(rev-previous_revenue, 0)/efficiency + base
        elif scenario.get("reinvestment_mode", "absolute") == "absolute":
            reinvest = number(year["reinvestment"], "reinvestment", 0)
        else:
            raise ValueError("Unknown reinvestment mode")
        ebit = rev * margin
        # No automatic cash benefit on losses; tax/NOL model is analyst supplied.
        nopat = ebit - max(ebit, 0) * tax
        fcff = nopat - reinvest
        pv += fcff / (1 + wacc) ** i
        rows.append({"year": i, "revenue": rev, "ebit": ebit,
                     "nopat": nopat, "reinvestment": reinvest, "fcff": fcff})
    if rows[-1]["nopat"] <= 0:
        raise ValueError("Perpetuity requires positive final NOPAT; extend forecast or use another model")
    terminal_fcff = rows[-1]["nopat"] * (1 + g) * (1 - g / roc)
    terminal_pv = terminal_fcff / (wacc - g) / (1 + wacc) ** len(years)
    operating = pv + terminal_pv
    equity = operating + cash - debt - claims
    return {"operating_value": operating, "equity_value": equity,
            "value_per_share": max(0, equity) / shares,
            "raw_equity_negative": equity < 0, "terminal_pv": terminal_pv,
            "terminal_fraction": ratio(terminal_pv, operating), "forecast": rows,
            "equity_bridge": {"excess_cash": cash, "debt": debt, "other_claims": claims,
                              "share_equivalents": shares}}


def reverse_dcf(target_price, base_revenue, shares, cash, debt, claims, scenario,
                lower=-0.2, upper=0.8):
    """Solve a constant annual growth rate, holding ALL other inputs fixed.

    In particular absolute reinvestment stays fixed: this is a sensitivity,
    not an inferred market consensus nor a feasible growth funding plan.
    """
    number(target_price, "target_price", 0.000001)
    def value(growth):
        s = {**scenario, "years": [{**r, "growth": growth} for r in scenario["years"]]}
        return dcf(base_revenue, shares, cash, debt, claims, s)["value_per_share"]
    samples = [value(lower + (upper-lower)*i/20) for i in range(21)]
    if any(b < a - 1e-8 for a, b in zip(samples, samples[1:])):
        return {"status": "non_monotonic", "growth": None}
    if not samples[0] <= target_price <= samples[-1] or samples[0] == samples[-1]:
        return {"status": "no_bracket", "growth": None, "bounds": [lower, upper]}
    lo, hi = lower, upper
    for _ in range(80):
        mid = (lo + hi) / 2
        if value(mid) < target_price:
            lo = mid
        else:
            hi = mid
    return {"status": "solved", "growth": (lo+hi)/2, "bounds": [lower, upper],
            "reconstructed_price": value((lo+hi)/2),
            "limitation": ("Constant growth; reinvestment follows revenue through sales-to-capital; margins and WACC fixed; financing separate" if scenario.get("reinvestment_mode") == "sales_to_capital" else "Constant growth; margins, absolute reinvestment, financing and WACC held fixed")}


def funding_path(cash, periods):
    balance = number(cash, "available_cash", 0)
    first_gap, rows = None, []
    for i, p in enumerate(periods, 1):
        cfo = number(p["operating_cash_flow"], "operating_cash_flow")
        capex = number(p["capex"], "capex", 0)
        repayments = number(p["debt_repayment"], "debt_repayment", 0)
        committed = number(p["committed_financing"], "committed_financing", 0)
        balance += cfo - capex - repayments + committed
        if balance < 0 and first_gap is None:
            first_gap = i
        rows.append({"period": i, "cash_before_uncommitted_financing": balance,
                     "cumulative_shortfall": max(0, -balance)})
    return {"first_gap_period": first_gap, "periods": rows,
            "maximum_shortfall": max([0] + [r["cumulative_shortfall"] for r in rows])}
