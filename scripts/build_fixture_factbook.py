"""Rebuild a committed test FactBook from a raw connector folder (runs/<T>_<date>/raw, not in Git).

Vendor consensus estimates are dropped before writing: only filing values, the 10-Q share count and the
dated price are stored in the repository. Usage:
  python scripts/build_fixture_factbook.py runs/ADBE_2026-10-20/raw ADBE 2026-10-19 tests/fixtures/adbe_factbook_2026-10-19.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fundamental_engine.pipeline import factbook_from_run_dir  # noqa: E402

COMPANIES = {'ADBE': {'ticker': 'ADBE', 'name': 'Adobe Inc.', 'cik': '796343', 'currency': 'USD', 'exchange': 'NASDAQ',
                      'sector': 'Information Technology', 'industry': 'Application Software', 'country': 'US',
                      'fiscal_year_end': '11-30'}}


def main(raw, ticker, as_of, out):
    fb = factbook_from_run_dir(raw, COMPANIES[ticker], as_of)
    fb['estimates'] = []
    fb['sources'] = {k: v for k, v in fb['sources'].items() if v['kind'] != 'consensus'}
    fb['fixture_note'] = ('Reported values are 10-K figures read through the FMP as-reported endpoints (filing dates not '
                          'available, availability estimated and flagged); the 10-Q share count is the vendor reading of the '
                          'filing cover; price is the dated IB last trade. Vendor consensus is deliberately not stored in Git.')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(fb, f, indent=0)
    print(len(fb['facts']), 'facts ->', out)


if __name__ == '__main__':
    main(*sys.argv[1:5])
