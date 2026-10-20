"""The whole cycle, in order: collect -> discover (all channels) -> research -> calibrate -> propose -> report.

    SEC_USER_AGENT="app contact@example.com" python scripts/run_all.py runs/universe_2026-10-20 --as-of 2026-10-20 \
        --channels runs/discovery_sources --positions runs/private/positions.json --exposures runs/private/holdings_info.json \
        [--holdings NVDA MSFT ...] [--sweep] [--skip-collect] [--backtest runs/backtest/engine_v2/summary.json]

Steps (each one a command that can also be run alone; a failed step stops the cycle and says which):
 1. collect_universe.py           SEC frames, ticker map, listing file (network; skipped with --skip-collect)
 2. collect_channels.py           EDGAR form index, 8-K item lists, quarterly revenue frames (network)
 3. fundamental_engine universe   screen, six lanes, cards, channel leads, research queue
 4. collect_companies.py          SEC data and filings for the queue and the holdings (network)
 5. fundamental_engine deep       full pipeline for the queue, the holdings and (--sweep) the large-cap calibration sweep
 6. calibrate.py                  engine-consistent market return (needs the sweep, else the previous calibration is kept)
 7. fundamental_engine run        research list, proposed book against the holdings and the index, Hebrew report
Connector channels (news, estimates, social, analyst targets, IBKR links) are saved by the session into the channels
folder before step 3 (fundamental_engine.channels.plan lists the calls). No step places an order.
"""
import argparse
import os
import subprocess
import sys

PY = sys.executable
ROOT = os.path.join(os.path.dirname(__file__), '..')


def step(name, cmd):
    print(f'== {name}: ' + ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        raise SystemExit(f'step "{name}" failed (exit {r.returncode}); later steps not run')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--channels')
    ap.add_argument('--positions')
    ap.add_argument('--exposures')
    ap.add_argument('--holdings', nargs='*', default=[])
    ap.add_argument('--sweep', action='store_true', help='rerun the large-cap sweep (QUEUE_large500) and recalibrate')
    ap.add_argument('--skip-collect', action='store_true', help='use the data already saved (no network)')
    ap.add_argument('--backtest', help='engine_backtest summary.json for the edge reliability')
    a = ap.parse_args(argv)
    f, d = a.folder, a.as_of
    if not a.skip_collect:
        if not os.environ.get('SEC_USER_AGENT'):
            raise SystemExit('set SEC_USER_AGENT (contact e-mail for SEC fair access) or pass --skip-collect')
        step('collect universe', [PY, 'scripts/collect_universe.py', f, '--years', *[str(y) for y in range(int(d[:4]) - 5, int(d[:4]))]])
        if a.channels:
            step('collect channels', [PY, 'scripts/collect_channels.py', f, '--channels', a.channels, '--as-of', d])
    step('discover', [PY, '-m', 'fundamental_engine', 'universe', f, '--as-of', d] + (['--channels', a.channels] if a.channels else []))
    if not a.skip_collect:
        step('collect queue', [PY, 'scripts/collect_companies.py', f, '--docs-when-needed'])
        if a.holdings:
            step('collect holdings', [PY, 'scripts/collect_companies.py', f, '--symbols', *a.holdings, '--name', 'holdings',
                                      '--docs-when-needed'])
    cal = os.path.join(f, 'calibration.json')
    idx = ['--index', cal] if os.path.exists(cal) else []
    if a.sweep:
        step('research sweep', [PY, '-m', 'fundamental_engine', 'deep', f, '--as-of', d, '--name', 'large500'] + idx)
        step('calibrate', [PY, 'scripts/calibrate.py', f, '--name', 'large500', '--as-of', d])
        idx = ['--index', cal]
    step('research queue', [PY, '-m', 'fundamental_engine', 'deep', f, '--as-of', d] + idx)
    if a.holdings or os.path.exists(os.path.join(f, 'companies', 'QUEUE_holdings.json')):
        step('research holdings', [PY, '-m', 'fundamental_engine', 'deep', f, '--as-of', d, '--name', 'holdings'] + idx)
    run = [PY, '-m', 'fundamental_engine', 'run', f, '--as-of', d] + (['--calibration', cal] if os.path.exists(cal) else [])
    for k, v in (('--positions', a.positions), ('--exposures', a.exposures), ('--backtest', a.backtest)):
        if v:
            run += [k, v]
    step('propose and report', run)


if __name__ == '__main__':
    main()
