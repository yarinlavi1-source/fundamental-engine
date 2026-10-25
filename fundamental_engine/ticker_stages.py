"""Fast ticker procedure: the stage instruction files (brain/ticker/<stage>.md) and their placeholders.

The main loop (CLAUDE.md "On a ticker") runs scripts/ticker_prefetch.py, then hands each agent a short prompt: "follow the
output of `python -m fundamental_engine ticker-stage <stage> --ticker T --as-of D`". Each file carries every rule the
matching part of the saved Workflow `.claude/workflows/ticker.js` carries (that Workflow stays as the slower thorough
fallback), with three placeholders: {TICKER}, {AS_OF} and {RUN} (the absolute run folder runs/tickers/<T>_<as_of>).
Pure module: reads packaged text, fetches nothing.
"""
from datetime import date
from pathlib import Path
import re

DIR = Path(__file__).parent / 'brain' / 'ticker'
TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,12}$')
PLACEHOLDERS = ('{TICKER}', '{AS_OF}', '{RUN}')
# step: 1 = the four gather lanes in parallel (with the prefetch), 2 = the analyst, 3 = the two reviewers in parallel,
# 4 = the main loop delivers. writes[0] is the stage's own output (relative to the run folder).
STAGES = {
    'gather_numbers': {'file': 'gather_numbers.md', 'step': 1, 'agent': 'background agent, model sonnet', 'minutes': '5-6',
                       'writes': ['gather/numbers.json', 'identity.json (identity check)', 'companies/<T>/raw/ (price, AV payloads)'],
                       'what': 'identity check, statements reconciled, IBKR price (READ-ONLY), own multiple history, '
                               'attributed consensus, guidance track record'},
    'gather_documents': {'file': 'gather_documents.md', 'step': 1, 'agent': 'background agent, model sonnet', 'minutes': '5-6',
                         'writes': ['gather/documents.json'],
                         'what': 'targeted reading of the 10-K / 10-Q / release / transcript: segments, customers, KPIs, '
                                 'RPO and backlog, current guidance, one-offs, risk factors'},
    'gather_field': {'file': 'gather_field.md', 'step': 1, 'agent': 'background agent, model sonnet', 'minutes': '5-6',
                     'writes': ['gather/field.json'],
                     'what': 'named direct competitors with their own numbers and why each competes, bottlenecks, share'},
    'gather_macro': {'file': 'gather_macro.md', 'step': 1, 'agent': 'background agent, model sonnet', 'minutes': '4-5',
                     'writes': ['gather/macro.json'],
                     'what': 'rates check, geography and country risk, export controls, regulation, news since the last filing'},
    'analyst': {'file': 'analyst.md', 'step': 2, 'agent': 'one agent, default model', 'minutes': '8-10',
                'writes': ['analyst.json', 'understand/business.json', 'position.json', 'evidence_pack.json',
                           'companies/<T>/inputs.json', 'companies/<T>/out/'],
                'what': 'understand + position + value: pack-merge, pack-to-inputs, deep for this one company; '
                        'also the one repair pass after a critical review finding'},
    'review_numbers': {'file': 'review_numbers.md', 'step': 3, 'agent': 'agent, default model (parallel with review_counter)',
                       'minutes': '4-5', 'writes': ['review/numbers.json'],
                       'what': 'every table number traced to its source, tiers, no other-company default, guidance vs track record'},
    'review_counter': {'file': 'review_counter.md', 'step': 3, 'agent': 'agent, default model (parallel with review_numbers)',
                       'minutes': '4-5', 'writes': ['review/counter.json'],
                       'what': 'counter-thesis, peak vs justified multiple, what breaks, events and falsifiers'},
    'deliver': {'file': 'deliver.md', 'step': 4, 'agent': 'the main loop itself', 'minutes': '1-2',
                'writes': ['deliver/table_he.md', 'deliver/reasons.json', 'runs/tickers/forecast_ledger.jsonl'],
                'what': 'the owner message: one table, 3-5 short explanations, sources as links; ledger freeze'},
}


def _check(ticker, as_of):
    t = str(ticker or '').upper().strip()
    if not TICKER_RE.match(t):
        raise ValueError(f'ticker {ticker!r}: 1-12 characters A-Z 0-9 . -')
    try:
        d = date.fromisoformat(str(as_of or ''))
    except ValueError:
        raise ValueError(f'as_of {as_of!r}: a date YYYY-MM-DD') from None
    if len(str(as_of)) != 10:
        raise ValueError(f'as_of {as_of!r}: a date YYYY-MM-DD')
    return t, d.isoformat()


def source(stage):
    """The packaged instruction file of one stage, placeholders unfilled."""
    if stage not in STAGES:
        raise ValueError(f'unknown stage {stage!r}; stages: {", ".join(STAGES)}')
    return (DIR / STAGES[stage]['file']).read_text(encoding='utf-8')


def run_folder(ticker, as_of, root=None):
    t, d = _check(ticker, as_of)
    return str((Path(root) if root else Path.cwd()).resolve() / 'runs' / 'tickers' / f'{t}_{d}')


def render(stage, ticker, as_of, run=None, root=None):
    """The stage's instructions with {TICKER}, {AS_OF} and {RUN} filled (run: the run folder; default
    <root or cwd>/runs/tickers/<TICKER>_<as_of>). Plain replacement: the JSON shapes in the text keep their braces."""
    t, d = _check(ticker, as_of)
    text = source(stage)
    folder = str(run) if run else run_folder(t, d, root)
    for k, v in (('{TICKER}', t), ('{AS_OF}', d), ('{RUN}', folder.rstrip('/'))):
        text = text.replace(k, v)
    return text


def prompt(stage, ticker, as_of):
    """The short prompt the main loop hands an agent for one stage."""
    t, d = _check(ticker, as_of)
    return (f'You are the {stage} stage of the fast ticker procedure for {t} (as_of {d}). From the fundamental-engine repository '
            f'root run `python -m fundamental_engine ticker-stage {stage} --ticker {t} --as-of {d}` and follow its output exactly; '
            'reply only as it says.')


def index():
    """The stages in order, for `ticker-stage list`."""
    return [{'stage': k, **{x: v[x] for x in ('step', 'agent', 'minutes', 'writes', 'what')}} for k, v in STAGES.items()]
