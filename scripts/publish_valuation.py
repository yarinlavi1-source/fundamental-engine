"""Publish one valuation: every published file is written from one record (15.11.2026).

  python scripts/publish_valuation.py research/companies/<T>/<date>/lean_params.json [--thesis-inputs inputs.json]
  python scripts/publish_valuation.py research/companies/<T>/<date>            (a custom model folder: BNY, SPCX, META)
  python scripts/publish_valuation.py --check [research folder]

Publishing a lean valuation: runs scripts/lean_model.py on the params, writes lean_result.json with today's reporting
keys (refused when a result is already saved there and the rerun does not reproduce it: a changed valuation goes in a
new dated folder), writes valuation.json (fundamental_engine/valuation_record.py), projects
research/technical/<T>/<date>/fundamental.json (every v1 key, schema_version 2), replaces the company's row in
research/THESES_he.md with the one new row, and regenerates only the numbers block of thesis_he.md, between its two
markers. Without the markers it prints the block and leaves the thesis as it is. Prose is never written here.
A custom folder is not rerun (tests/test_saved_valuations.py reruns those models): its saved result is read.

The judgement no model computes (verdict, scenario conditions, typed conditions and falsifiers, next report, the
historical-multiple inputs, the index texts) comes from --thesis-inputs (keys: valuation_record.THESIS_INPUTS_KEYS).
Without it the judgement of the valuation.json already saved in the folder is carried over exactly (16.11.2026: a
republish after a code change keeps typed conditions); without that, the texts of the handoff saved for that date,
untyped. A calibrated_rule.json beside lean_params.json (lean_model --calibrated-rule) is applied to the record; the
lean_result.json written here stays the params' own result.

--check reads only. One line per problem, exit code 1 when there is any: a thesis without a handoff or the reverse,
more than one index row per company, a price or base value that differs between the params, the record, the thesis
numbers block, the index row and the handoff, an entry price that is not a ladder rung, text in a numeric field, a
saved lean result that no longer equals a rerun.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)
import lean_model  # noqa: E402
from fundamental_engine import lean_freeze, valuation_record as vr  # noqa: E402


class PublishError(Exception):
    pass


def _write(path, text, written):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    written.append(path)


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def publish(target, thesis_inputs=None, root=None, out=print):
    """Write every published file of one valuation. target: a lean_params.json or a dated folder. Returns
    {record, written: [paths], removed_index_rows, block_printed}."""
    target = os.path.abspath(target)
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    root = os.path.abspath(root) if root else os.path.dirname(os.path.dirname(os.path.dirname(folder)))
    ticker, as_of = os.path.basename(os.path.dirname(folder)), os.path.basename(folder)
    if os.path.dirname(os.path.dirname(folder)) != os.path.join(root, vr.STORE) or not vr.DATED.match(as_of):
        raise PublishError(f'{folder} is not <research>/companies/<TICKER>/<YYYY-MM-DD>')
    spec = lean_freeze.classify(folder)
    params_name = os.path.basename(target) if os.path.isfile(target) else spec.get('params')
    if os.path.isfile(target) and vr.is_lean_params(vr.load(target)):
        if params_name != 'lean_params.json':
            raise PublishError(f'{params_name} is a variant or a pre-review file: only lean_params.json is published')
        spec = {'kind': 'lean', 'model': 'lean_model', 'params': 'lean_params.json', 'result': 'lean_result.json'}
    if spec['kind'] is None:
        raise PublishError(f'{folder}: {spec["reason"]}')
    written, result = [], None
    if vr.is_lean_params(vr.load(os.path.join(folder, spec['params']))) if spec.get('params') else False:
        params = vr.load(os.path.join(folder, spec['params']))
        if params.get('ticker') != ticker or params.get('as_of') != as_of:
            raise PublishError(f'the params say {params.get("ticker")} {params.get("as_of")}, the folder is {ticker}/{as_of}')
        try:
            result = json.loads(json.dumps(lean_model.run(params)))
        except lean_model.ParamsError as e:
            raise PublishError('the params cannot be run: ' + '; '.join(e.problems))
        res_path = os.path.join(folder, spec['result'])
        if os.path.isfile(res_path):
            diffs = lean_freeze._differences(vr.load(res_path), result)
            if diffs:
                raise PublishError(f'{len(diffs)} saved value(s) of {spec["result"]} differ from a rerun (first: {diffs[0]}): a changed '
                                   'valuation is published in a new dated folder, the saved one is not overwritten')
        _write(res_path, json.dumps(result, indent=1), written)
    handoff_path = os.path.join(root, vr.TECH, ticker, as_of, 'fundamental.json')
    previous = vr.load(handoff_path) if os.path.isfile(handoff_path) else None
    if thesis_inputs is not None:
        inputs = vr.load(thesis_inputs) if isinstance(thesis_inputs, str) else thesis_inputs
        unknown = sorted(set(inputs) - set(vr.THESIS_INPUTS_KEYS))
        if unknown:
            raise PublishError('unknown thesis-inputs key(s): ' + ', '.join(unknown))
    elif os.path.isfile(os.path.join(folder, vr.RECORD_NAME)):
        inputs = vr.inputs_from_record(vr.load(os.path.join(folder, vr.RECORD_NAME)))
        out(f'no --thesis-inputs: the judgement of the saved {vr.RECORD_NAME} ({as_of}) is carried over, typed conditions included')
    elif previous is not None:
        inputs = vr.inputs_from_handoff(previous)
        out(f'no --thesis-inputs: the texts of the saved handoff {as_of} are carried over (conditions untyped)')
    else:
        inputs = {}
        out('no --thesis-inputs and no saved handoff: the record has numbers only (no verdict, conditions or next report)')
    record, _ = vr.record_for_folder(folder, root, inputs, result=result)
    if record is None:
        raise PublishError(f'{folder}: no record can be built ({_.get("reason")})')
    thesis_path = os.path.join(root, vr.STORE, ticker, 'thesis_he.md')
    if not os.path.isfile(thesis_path):
        record['thesis_link'] = None   # no link to a file that does not exist; --check lists the missing thesis
    problems = vr.validate(record)
    if problems:
        raise PublishError('the record is not valid:\n  - ' + '\n  - '.join(problems))
    _write(os.path.join(folder, vr.RECORD_NAME), vr.dumps(record), written)
    _write(handoff_path, vr.dumps(vr.project_handoff(record, previous)), written)
    removed = []
    index_path = os.path.join(root, vr.INDEX)
    if os.path.isfile(index_path):
        old_rows = vr.index_rows(_read(index_path)).get(ticker, [])
        keep = vr.parse_index_row(old_rows[-1][1])['cells'] if old_rows else None
        text, removed = vr.replace_index_row(_read(index_path), ticker, vr.index_row(record, keep))
        _write(index_path, text, written)
        if len(removed) > 1:
            out(f'{len(removed)} index rows of {ticker} were replaced by one; the older rows belong in the thesis history line:')
            for ln in removed[:-1]:
                out('  ' + ln)
    else:
        out(f'no {vr.INDEX} under {root}: no index row written')
    block, printed = vr.numbers_block(record), False
    if os.path.isfile(thesis_path):
        text, done = vr.replace_block(_read(thesis_path), block)
        if done:
            _write(thesis_path, text, written)
        else:
            printed = True
            out(f'{os.path.relpath(thesis_path, root)} has no numbers-block markers: nothing in it was changed. The block:')
            out(block)
    else:
        printed = True
        out(f'no thesis_he.md for {ticker}: the numbers block for a new thesis:')
        out(block)
    for w in record.get('warnings') or []:
        out('model warning: ' + w)
    return {'record': record, 'written': written, 'removed_index_rows': removed, 'block_printed': printed}


def check(root, out=print):
    problems = vr.check_tree(os.path.abspath(root), run=lean_model.run)
    for kind, ticker, text in problems:
        out(f'{kind} {ticker}: {text}')
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('target', nargs='?', help='lean_params.json or a dated custom-model folder; with --check: the research folder')
    ap.add_argument('--thesis-inputs', help='JSON file with the judgement fields (verdict, conditions, next report, ...)')
    ap.add_argument('--root', help='the research folder (default: three levels above the dated folder)')
    ap.add_argument('--check', action='store_true', help='check the research tree; writes nothing')
    a = ap.parse_args(argv)
    if a.check:
        problems = check(a.target or a.root or os.path.join(REPO, 'research'))
        print(f'publish --check: {len(problems)} problem(s)', file=sys.stderr)
        return 1 if problems else 0
    if not a.target:
        ap.error('give a lean_params.json or a dated folder, or --check')
    try:
        res = publish(a.target, a.thesis_inputs, a.root)
    except PublishError as e:
        print(f'publish_valuation: {e}', file=sys.stderr)
        return 1
    for w in res['written']:
        print('written: ' + w)
    return 0


if __name__ == '__main__':
    sys.exit(main())
