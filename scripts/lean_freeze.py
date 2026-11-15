"""Freeze published lean valuations into research/ledger.jsonl (fundamental_engine/lean_freeze.py holds the logic).

Usage:
  python scripts/lean_freeze.py research/companies/UBER/2026-11-13 [--next-report 2027-02-04] [--falsifiers FILE]
  python scripts/lean_freeze.py --all [--skip LLY,FIVE] [--next-reports FILE]
  python scripts/lean_freeze.py --verify

Run it as the last step of every delivery, before the company's next report. One folder: a thesis row (hashes of the
params, the saved result and scripts/lean_model.py, price, bear/base/bull value today, dated year-end values, the
calibrated base, the buy test, pre-review and post-review base) and forecast rows (base revenue, operating margin and
operating EPS per model year with the bear-bull envelope). --all: every lean or custom valuation folder under
research/companies in as_of order. Idempotent: a second run adds nothing when nothing changed; changed inputs add a
thesis that supersedes the old one. Custom models (bank, segment, three-lane, a remapped lean file) get a thesis row
from their saved result file with the note 'custom model'.
--next-report: the date of the company's next report ('2026-11-17', '~2027-02-04', 'late January 2027'); default: the
next_report of research/technical/<T>/<date>/fundamental.json. --next-reports: a JSON file {'T' or 'T/<date>': the
same}. --falsifiers: a JSON list stored with the thesis. Company research only: nothing private is read or written.
The legacy research/forecast_ledger.jsonl is never touched; the first row of the new ledger holds its sha256.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import lean_model  # noqa: E402
from fundamental_engine import lean_freeze as LF  # noqa: E402

LEAN_MODEL = os.path.join(HERE, 'lean_model.py')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _line(r):
    return (f"{r['ticker']:<6}{r['as_of']:<12}{r['model']:<14}base {r['base_value_today']!s:>9}  rows {r['rows_written']:>3}  "
            f"{r['status']}; on time: {'yes' if r['on_time'] else 'no' if r['on_time'] is False else 'unknown'}"
            f"{'; recorded late' if r.get('late_recording') else ''}"
            f"{'; rejected ' + str(len(r['rejected'])) if r['rejected'] else ''}{'; ' + r['note'] if r.get('note') else ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description='Freeze published lean valuations into the chained research ledger.')
    ap.add_argument('folder', nargs='?', help='one valuation folder: research/companies/<T>/<date>')
    ap.add_argument('--all', action='store_true', help='every published valuation under research/companies, in as_of order')
    ap.add_argument('--verify', action='store_true', help='verify the chain and list the theses in force')
    ap.add_argument('--ledger', default=os.path.join(ROOT, LF.LEDGER))
    ap.add_argument('--root', default=ROOT)
    ap.add_argument('--skip', default='', help='tickers not to freeze with --all, comma separated')
    ap.add_argument('--next-report', help="the next report date of the one folder (e.g. 2026-11-17, '~2027-02-04')")
    ap.add_argument('--next-reports', help='JSON file with next report dates per ticker for --all')
    ap.add_argument('--falsifiers', help='JSON list stored with the thesis of the one folder')
    ap.add_argument('--json', action='store_true', help='print the full result as JSON')
    a = ap.parse_args(argv)
    if a.verify:
        out = LF.status(a.ledger)
    elif a.all:
        out = LF.freeze_all(a.root, a.ledger, run=lean_model.run, lean_model_path=LEAN_MODEL,
                            next_reports=_load(a.next_reports) if a.next_reports else None,
                            skip=tuple(t for t in a.skip.split(',') if t))
    elif a.folder:
        r = LF.freeze_folder(a.folder, a.ledger, root=a.root, run=lean_model.run, lean_model_path=LEAN_MODEL,
                             next_report=a.next_report, falsifiers=_load(a.falsifiers) if a.falsifiers else None)
        out = {'frozen': [r], 'left_out': [], 'ledger': LF.open_ledger(a.ledger).verify()}
    else:
        ap.error('give a valuation folder, --all or --verify')
    if a.json or a.verify:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for r in out['frozen']:
            print(_line(r))
            for x in r['rejected']:
                print(f"      refused by the ledger: {x['id']}: {x['reason']}")
        for x in out['left_out']:
            print(f"left out: {x['folder']}: {x['reason']}")
        print('ledger:', json.dumps(out['ledger']))
    return 0 if (out.get('verify') or out.get('ledger'))['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
