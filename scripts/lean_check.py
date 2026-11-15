"""The self-check before every delivery of a lean valuation (fundamental_engine/lean_check.py holds the logic).

Usage:
  python scripts/lean_check.py params.json [--prev prev_params.json] [--facts companyfacts.json] [--price P]
                               [--answers answers.json] [--today YYYY-MM-DD] [--facts-cutoff YYYY-MM-DD]
                               [--json] [--all] [--save]

It runs scripts/lean_model.py on the params, then:
  (a) every model warning (input checks, funding, calibrated diagnostics, method gap, a pass on the line, a rerun price
      lower than the stated one, the reverse reading, the band with distributions reinvested);
  (b) with --facts (a cached SEC companyfacts JSON, e.g. runs/sec_cache/CIK##########.json): base-year revenue against
      the reported year to date and last year, shares against the latest diluted and cover-page counts, net cash against
      the cash and debt tags; a gap above 3% needs a reason in the params notes;
  (c) the guided year against params.guidance {year, revenue_low, revenue_high, margin_low, margin_high, source};
  (d) the price: price_time_utc older than 3 trading days against as_of, --today more than 3 trading days after as_of,
      or --price more than 3% from the params price (the buy test is then rerun at the quote);
  (e) params.judgement through locked_rules.validate_judgement, and rf / ERP against research/market.json;
  (f) with --prev: every changed input with the base-value effect of that change alone, and the absolute revenue and
      operating-profit path year by year (a rebased base year or "growth unchanged" can hide a level change);
  (g) the manual questions of research/lessons.jsonl that apply, each needing a one-line answer in the --answers file
      ({"L11": "none: ...", "L16": "..."}); a file lean_check_answers.json next to the params is read by default.
The table shows pass / FAIL / OPEN / n/a per item; --all shows the passes and n/a too. Exit code 1 when an item that
blocks a buy is failed or open while the buy test passes (at the params price, or at a --price quote more than 3%
away); a "not a buy" result never blocks; 2 when the params cannot
be run. --save writes lean_check.json next to the params. Nothing else is written: params and values are never edited.
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
from fundamental_engine import lean_check as LC  # noqa: E402
from fundamental_engine import locked_rules as LR  # noqa: E402

LESSONS = os.path.join(ROOT, 'research', 'lessons.jsonl')
ANSWERS_NAME = 'lean_check_answers.json'


def load_lessons(path=LESSONS):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def check_file(params_path, prev=None, facts=None, price=None, answers=None, today=None, facts_cutoff=None, lessons=None,
               market=None):
    """The check document for one params file (paths in, a dict out; nothing is written)."""
    params = _load(params_path)
    if answers is None:
        default = os.path.join(os.path.dirname(os.path.abspath(params_path)), ANSWERS_NAME)
        answers = default if os.path.exists(default) else None
    if lessons is None:
        lessons = load_lessons()
    if market is None:
        try:
            market = LR.load_market()
        except OSError:
            market = None
    doc = LC.run_checks(lean_model, params, prev=_load(prev) if prev else None, facts=_load(facts) if facts else None,
                        price=price, answers=_load(answers) if answers else None, lessons=lessons, market=market,
                        today=today, facts_cutoff=facts_cutoff)
    doc['files'] = {'params': os.path.relpath(params_path, ROOT) if os.path.abspath(params_path).startswith(ROOT) else params_path,
                    'previous': prev, 'facts': os.path.basename(facts) if facts else None, 'answers': answers}
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('params')
    ap.add_argument('--prev', help='the previous params file of the same company')
    ap.add_argument('--facts', help='a cached SEC companyfacts JSON')
    ap.add_argument('--price', type=float, help="today's quote")
    ap.add_argument('--answers', help='JSON {lesson id: one-line answer}')
    ap.add_argument('--today', help='YYYY-MM-DD: also check the age of as_of against this date')
    ap.add_argument('--facts-cutoff', help='YYYY-MM-DD: ignore periods first filed after this date')
    ap.add_argument('--json', action='store_true', help='print the full JSON instead of the table')
    ap.add_argument('--all', action='store_true', help='show the passed and not-applicable items too')
    ap.add_argument('--save', action='store_true', help='write lean_check.json next to the params')
    a = ap.parse_args()
    try:
        doc = check_file(a.params, a.prev, a.facts, a.price, a.answers, a.today, a.facts_cutoff)
    except lean_model.ParamsError as e:
        print(f'lean_check: {a.params} cannot be run ({len(e.problems)} problem(s)):', file=sys.stderr)
        for x in e.problems:
            print('  - ' + x, file=sys.stderr)
        sys.exit(2)
    if a.save:
        out = os.path.join(os.path.dirname(os.path.abspath(a.params)), 'lean_check.json')
        with open(out, 'w', encoding='utf-8') as f:
            f.write(json.dumps(doc, indent=1, ensure_ascii=False, default=str) + '\n')
    print(json.dumps(doc, indent=1, ensure_ascii=False, default=str) if a.json else
          LC.render(doc, show=LC.STATUSES if a.all else ('fail', 'open')))
    sys.exit(doc['exit_code'])


if __name__ == '__main__':
    main()
