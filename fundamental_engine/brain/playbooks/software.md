# Software and subscription economics

Map customer count × net spend by cohort/segment. Separate seats, usage, price,
expansion, contraction and churn. ARR/bookings/RPO are different constructs; retain
contract length, cancellation rights and inclusion of services/consumption estimates.

Critical evidence: gross retention and net retention definitions; paid versus free
users; current versus remaining performance obligations; billings versus cash;
implementation burden and gross margin after hosting/support; SBC and capitalized
software. A rising NRR can coexist with shrinking new-customer acquisition.

Test durability through renewals and realized pricing after increases, multi-product
adoption and actual replacement cost. AI product announcements require paid adoption,
inference cost, customer productivity evidence and cannibalization analysis.

Failure mechanisms: distribution lost to platforms, bundling pressure, low-quality
seats, consumption volatility, long sales cycles, customer concentration and high
SBC-funded apparent FCF. Check layoffs against sales capacity and product reliability.

Forecast customers/cohorts or disclosed drivers -> revenue -> contribution -> sales,
R&D and overhead -> net investment -> owner cash. Value a mature recurring business
separately from speculative new products. Use nonfinancial DCF only with supported
margins and investment; funding model for cash-burning expansion.

Economic model (registry `brain/economics.json`, packet `economic_models`):
`data_platform_software` reads revenue = customers x revenue per customer on one scope
(US commercial customers against US commercial revenue, or `scope_mismatch`), splits
growth into new customers and expansion (`expansion_led` needs NDR above 1.10), checks a
top-N figure only against TTM revenue on the same window, and carries SBC through the
`acquisition_sbc` overlay so an acquisition tranche ends when its vesting ends instead of
being carried at today's ratio forever.
