# stage0/

Reproduction baseline (plan: `docs/STAGE0_PLAN.md`, report: `docs/STAGE0_BASELINE.md`).

| Path | What |
|---|---|
| `golden_set.json` | the 20 frozen companies and as-of dates (approved 19.10.2026; not changed after results) |
| `price_audit.json` | as-traded price at each as-of date and its second-source check |
| `pr7_pit_check.json` | PR #7's PIT layer against the golden snapshots |
| `storage_check.json` | where the snapshots are stored and the read-back results |

Not in this public repository (they name held positions): the legacy code copies, fixtures, field-by-field
reproduction results, the known-error register, the reproduction harness and its tests. They are in the private
stage 0 store (`$STAGE0_DATA/private`, backed up as listed in `storage_check.json`). Vendor price files and the SEC
bulk file are not in Git either.

Re-run the public parts (needs the data store; nothing here is evidence of stock-picking quality):

```
python scripts/stage0_extract.py SESSION.jsonl $STAGE0_DATA/raw
python scripts/stage0_prices.py $STAGE0_DATA/raw YAHOO_PX_DIR stage0/price_audit.json
python scripts/stage0_golden.py companyfacts.zip $STAGE0_DATA/raw stage0/price_audit.json $STAGE0_DATA/golden --fetch-submissions
python scripts/stage0_pr7_check.py $STAGE0_DATA/golden stage0/pr7_pit_check.json
```
