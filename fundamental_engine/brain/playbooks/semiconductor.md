# Semiconductors and AI compute suppliers

Revenue = units x average selling price by product family and end market (data center, client,
auto, industrial, mobile). Track design wins, customer concentration (hyperscalers, a few OEMs),
inventory in the channel and at customers, and lead times. Separate a capacity-constrained boom
from durable demand: a sold-out year is not a mid-cycle year.

Gross margin reflects pricing power, mix and the cost curve of the process node. Fabless firms
depend on foundry capacity and pricing; integrated firms carry heavy capex and utilization risk.
Check prepayments and supply agreements (they can hide working-capital commitments), export
controls and single-country manufacturing concentration.

Valuation: normalize margins and growth across a cycle unless evidence shows a structural shift.
Price several states: digestion (orders pause, inventory correction), base, and a sustained
build-out. Record what the price requires in units/ASP terms, not only revenue growth. Stock-based
compensation and buybacks are material; per-share value needs point-in-time shares.

Failure signs to watch in filings: rising inventory days, falling prepayments, customer
concentration increasing, gross margin falling while revenue still grows, capex guidance cuts by
the largest customers, new export restrictions.

Economic model (registry `brain/economics.json`, packet `economic_models`): memory, DRAM, NAND or
HBM text, or bits/ASP KPIs, select `memory_cycle` (revenue = bits x ASP; price-cyclical, so a
record margin stays `peak_margin_run_rate` and the margin-only normalized run rate is a floor on
the correction; long-term agreements damp nothing until volume share, price mechanism, deposits
and tenor are all evidenced, `lta_unproven`). An accelerator vendor is `gpu_vendor` (revenue = GW
shipped x revenue per GW by generation) with the `vendor_financing` overlay: funded, committed and
contingent support for the buyers as shares of annualized revenue, a guarantee kept as a ceiling,
and CFO/NI and receivables checks; a supplier down that chain (a memory maker selling into the
funded buyer) links the vendor's review through `funded_customer_chain`. The lessor of the compute
rather than its vendor is `compute_leasing` (energized GW x utilization x price per GW-year).
