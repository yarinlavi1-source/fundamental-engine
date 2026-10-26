"""Fast ticker procedure: the stage instruction files (brain/ticker/<stage>.md) and their placeholders.

The main loop (CLAUDE.md "On a ticker") runs scripts/ticker_prefetch.py and the deterministic steps of ticker_run
(ticker-prep, ticker-value, ticker-status), and hands each agent a short prompt (`prompt()`, `ticker-stage <stage> --prompt`):
"cd <repo root> && python -m fundamental_engine ticker-stage <stage> --ticker T --as-of D --run <run folder>; follow its
output exactly". Each file carries every rule the matching part of the saved Workflow `.claude/workflows/ticker.js` carries
(that Workflow stays as the slower thorough fallback), with three placeholders: {TICKER}, {AS_OF} and {RUN} (the absolute
run folder runs/tickers/<T>_<as_of>). Pure module: reads packaged text, fetches nothing.

Minutes are design estimates from the measured NVDA stage times of the saved Workflow (25.10.2026: gather lanes 7.0-9.3 min
at 22-36 tool calls; review lenses about 8 min at 28-50 calls; Understand 22 min and Position 22 min, about 25 of those 44
minutes spent reading engine source for the JSON formats the stage files and skeletons now give); not yet measured end to
end (`ticker-status` records each run's stage times).
"""
from datetime import date
from pathlib import Path
import re

DIR = Path(__file__).parent / 'brain' / 'ticker'
TICKER_RE = re.compile(r'^[A-Z0-9.\-]{1,12}$')
PLACEHOLDERS = ('{TICKER}', '{AS_OF}', '{RUN}')
# step: 1 = the four gather lanes in parallel (with the prefetch), 2 = the three analyst parts and review_trace in parallel
# (after ticker-prep), 3 = the two reviewers in parallel (after ticker-value), 4 = the main loop delivers; repair runs at
# most once after ticker-value (pack problems) and once after the reviewers (critical findings). writes[0] is the stage's
# own output (relative to the run folder). model: the Agent tool model ('default' = the main loop's model).
STAGES = {
    'gather_numbers': {'file': 'gather_numbers.md', 'step': 1, 'model': 'sonnet', 'agent': 'background agent, model sonnet',
                       'minutes': '7-9', 'writes': ['gather/numbers.json', 'identity.json (identity check)',
                                                    'companies/<T>/raw/ (price, AV payloads)'],
                       'what': 'identity check, statements reconciled, IBKR price (READ-ONLY), own multiple history, '
                               'attributed consensus, guidance track record and its ratio'},
    'gather_documents': {'file': 'gather_documents.md', 'step': 1, 'model': 'sonnet', 'agent': 'background agent, model sonnet',
                         'minutes': '7-9', 'writes': ['gather/documents.json', 'understand/quarter_result.json',
                                                      'understand/econ_result.json'],
                         'what': 'targeted reading of the 10-K / 10-Q / release / transcript: segments, customers, KPIs, '
                                 'RPO and backlog, current guidance, one-offs, risk factors; quarter-review and econ-review'},
    'gather_field': {'file': 'gather_field.md', 'step': 1, 'model': 'sonnet', 'agent': 'background agent, model sonnet',
                     'minutes': '7-9', 'writes': ['gather/field.json'],
                     'what': 'named direct competitors (the canonical list) with their own numbers and why each competes, '
                             'bottlenecks, share'},
    'gather_macro': {'file': 'gather_macro.md', 'step': 1, 'model': 'sonnet', 'agent': 'background agent, model sonnet',
                     'minutes': '5-7', 'writes': ['gather/macro.json'],
                     'what': 'rates check, geography and country risk, export controls, regulation, news since the last filing'},
    'analyst': {'file': 'analyst.md', 'step': 2, 'model': 'default', 'agent': 'background agent, default model', 'minutes': '8-12',
                'part': 'core', 'writes': ['position/core.json', 'understand/business.json'],
                'what': 'the coherent scenario core: understand (business.json), drivers incl. current_year_revenue, '
                        'forecast_options, scenario events, key risk, falsifiers'},
    'analyst_field': {'file': 'analyst_field.md', 'step': 2, 'model': 'default', 'agent': 'background agent, default model',
                      'minutes': '5-7', 'part': 'field', 'writes': ['position/field.json'],
                      'what': 'the five potential readings and the model-card assessments (moat, management, ...)'},
    'analyst_path': {'file': 'analyst_path.md', 'step': 2, 'model': 'default', 'agent': 'background agent, default model',
                     'minutes': '4-6', 'part': 'path', 'writes': ['position/path.json'],
                     'what': 'company_path: the forward P/E path per scenario with its justification, own history, named peers'},
    'review_trace': {'file': 'review_trace.md', 'step': 2, 'model': 'default', 'agent': 'background agent, default model '
                     '(parallel with the analyst parts)', 'minutes': '5-7', 'writes': ['review/trace.json'],
                     'what': 'every gathered number traced to its source, period, basis and date; no look-ahead; no consensus '
                             'as a fact; syndication'},
    'review_numbers': {'file': 'review_numbers.md', 'step': 3, 'model': 'default',
                       'agent': 'background agent, default model (parallel with review_counter)', 'minutes': '5-7',
                       'writes': ['review/numbers.json'],
                       'what': 'company-first: tiers and evidence of every driver, guidance vs track record, multiples vs own '
                               'history and named peers, the table equals the executed casefile'},
    'review_counter': {'file': 'review_counter.md', 'step': 3, 'model': 'default',
                       'agent': 'background agent, default model (parallel with review_numbers)', 'minutes': '5-7',
                       'writes': ['review/counter.json'],
                       'what': 'counter-thesis, coherence of the three parts, guardrails, peak vs justified multiple, what '
                               'breaks, expectation gap and red team'},
    'repair': {'file': 'repair.md', 'step': 3, 'model': 'default', 'agent': 'one new agent, default model (at most once per '
               'trigger)', 'minutes': '4-8', 'writes': ['review/repair.json', 'value.json'],
               'what': 'the one repair pass: the open problems of ticker-value, or the critical review findings; reruns '
                       'ticker-value'},
    'deliver': {'file': 'deliver.md', 'step': 4, 'model': 'main loop', 'agent': 'the main loop itself', 'minutes': '2-3',
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


TRIGGERS = ('value', 'review')


def prompt(stage, ticker, as_of, root=None, run=None, trigger=None):
    """The short prompt the main loop hands an agent for one stage: it names the repository root and the run folder
    absolutely, so it works whatever the agent's working directory. The repair stage needs its trigger: value
    (ticker-value did not run the engine) or review (critical review findings)."""
    if stage not in STAGES:
        raise ValueError(f'unknown stage {stage!r}; stages: {", ".join(STAGES)}')
    if (stage == 'repair') != (trigger is not None) or (trigger is not None and trigger not in TRIGGERS):
        raise ValueError('the repair stage, and only it, takes a trigger: value or review')
    t, d = _check(ticker, as_of)
    repo = (Path(root) if root else Path.cwd()).resolve()
    folder = str(run) if run else run_folder(t, d, repo)
    return (f'You are the {stage} stage of the fast ticker procedure for {t} (as_of {d})'
            + (f', trigger {trigger}' if trigger else '') + f'. Run `cd {repo} && python -m '
            f'fundamental_engine ticker-stage {stage} --ticker {t} --as-of {d} --run {folder}` and follow its output exactly; '
            'work from that repository root with absolute paths; reply only as it says.')


def index():
    """The stages in order, for `ticker-stage list`."""
    return [{'stage': k, **{x: v[x] for x in ('step', 'model', 'agent', 'minutes', 'writes', 'what')}} for k, v in STAGES.items()]
