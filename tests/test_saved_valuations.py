"""Golden test for the saved valuations in research/companies/ (15.11.2026).

A later change to scripts/lean_model.py, or to a custom model kept in Git, must not move a published value without this
test failing. It only reads: nothing under research/ is written, and a saved result is never regenerated to make it pass.

- Lean files: every research/companies/<T>/<date>/lean_params*.json in lean format (detected by its keys, not its name)
  is run through lean_model.run() and compared with the lean_result*.json of the same suffix next to it. Every key the
  saved result holds must come back with exactly the same value (the model's own rounding, after the JSON round trip
  the command line does). Keys the saved result does not hold are ignored, so a result saved before a reporting key
  existed still passes. A lean params file without a saved result (the pre-review files) is only run.
- Custom models (CUSTOM below): each script is run as a command with its output in a temp folder, and the output must
  equal the saved file in both directions.
- Coverage: a baseline listed in BASELINES cannot disappear; a lean_params*.json that is not in lean format, a
  lean_result*.json and a script under research/companies/ must each be covered or listed with a reason; a thesis-book
  company needs a dated folder with runnable inputs or a note saying "custom, not runnable".

When this test fails after a model change: the change moved a published number. Either the change is wrong, or the
valuation is republished on purpose as a new dated folder; the old folder keeps its files.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIES = os.path.join(ROOT, 'research', 'companies')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402

DATED = re.compile(r'^\d{4}-\d{2}-\d{2}$')
LEAN_TOP = ('price', 'rf', 'erp', 'beta', 'beta_mature', 'shares_m', 'net_cash_m', 'rev0_m', 'base_year',
            'days_to_base_year_end', 'years_to_horizon', 'buy_return', 'probabilities')
LEAN_SCENARIO = ('g', 'm', 'conv', 'pe')
NOTE_PHRASE = 'custom, not runnable'
MAX_SHOWN = 15

# Published lean baselines on 15.11.2026 (<T>/<date>/<params file>). They may be joined by newer folders, never removed.
BASELINES = (
    'APH/2026-11-06/lean_params.json', 'APP/2026-11-06/lean_params.json', 'AVGO/2026-11-07/lean_params.json',
    'AVGO/2026-11-07/lean_params_no_slowdown.json', 'CRDO/2026-11-09/lean_params.json',
    'CRDO/2026-11-09/lean_params_no_digestion.json', 'DELL/2026-11-11/lean_params.json',
    'GOOGL/2026-11-04/lean_params.json', 'MELI/2026-11-06/lean_params.json', 'MSFT/2026-11-04/lean_params.json',
    'NBIS/2026-11-11/lean_params.json', 'NBIS/2026-11-12/lean_params.json', 'NBIS/2026-11-13/lean_params.json',
    'NET/2026-11-06/lean_params.json', 'NOW/2026-11-04/lean_params.json', 'NU/2026-11-13/lean_params.json',
    'NVDA/2026-11-12/lean_params.json', 'NVDA/2026-11-12/lean_params_A_huang_stays.json',
    'NVDA/2026-11-12/lean_params_B2_messy_succession.json', 'NVDA/2026-11-12/lean_params_C_export_hit.json',
    'ORCL/2026-11-06/lean_params.json', 'PANW/2026-11-05/lean_params.json', 'RDDT/2026-11-04/lean_params.json',
    'TSM/2026-11-05/lean_params.json', 'UBER/2026-11-06/lean_params.json', 'UBER/2026-11-13/lean_params.json',
    'V/2026-11-09/lean_params.json', 'WSM/2026-11-11/lean_params.json',
)

# Custom models kept in Git. dir: the dated folder; script: run with that folder's files; args: '{out}' is the temp
# folder; cwd_out: run inside the temp folder after copying `copy` into it (for scripts that read and write the current
# folder); env: variable that receives the temp folder; compare: {file written in the temp folder: saved file}.
CUSTOM = (
    {'name': 'BNY bank model', 'dir': 'BNY/2026-11-11', 'script': 'bank_model.py',
     'args': ['lean_params.json', '--out', '{out}/lean_result.json'], 'compare': {'lean_result.json': 'lean_result.json'}},
    {'name': 'SPCX model', 'dir': 'SPCX/2026-11-13', 'script': 'model.py',
     'args': ['params.json', '--out', '{out}/results.json'], 'compare': {'results.json': 'results.json'}},
    {'name': 'SPCX params builder', 'dir': 'SPCX/2026-11-13', 'script': 'mkparams.py', 'cwd_out': True,
     'compare': {'params.json': 'params.json'}},
    {'name': 'SPCX side calculations', 'dir': 'SPCX/2026-11-13', 'script': 'side.py', 'cwd_out': True,
     'copy': ['params.json', 'results.json'], 'compare': {'side.json': 'side.json'}},
    {'name': 'META 13.11 model and finish lane', 'dir': 'META/2026-11-13', 'script': 'model/finish.py',
     'env': 'META_RERUN_OUT', 'uses': ['model/model13.py', 'model/pe.py', 'model/dcf_redteam_model.py'],
     'compare': {'model_result.json': 'model_result.json', 'finish_result.json': 'finish_result.json'}},
    {'name': 'META 13.11 red-team variants', 'dir': 'META/2026-11-13', 'script': 'model/redteam_variants.py',
     'env': 'META_RERUN_OUT', 'uses': ['model/model13.py', 'model/pe.py', 'model/dcf_redteam_model.py'],
     'compare': {'variants_result.json': 'model/redteam_variants_result.json'}},
    {'name': 'NBIS 13.11 checks (financing realistic)', 'dir': 'NBIS/2026-11-13', 'script': 'checks13.py',
     'env': 'NBIS_RERUN_OUT', 'compare': {'checks.json': 'checks13_result.json'}},
)

# Scripts in research/companies/ that this test does not run, each with its reason.
NOT_RERUN = {
    'NBIS/2026-11-12/repair.py': 'record of the 12.11 repair bridge; it needs run-folder files that were not copied',
    'META/2026-11-13/model/reverse_model.py': 'origin of reverse_params.json and reverse_result.json; writes to the run folder',
}


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def is_lean(p):
    """A lean_model params file, by its keys (BNY's lean_params.json is a bank model and is not)."""
    if not isinstance(p, dict) or not all(k in p for k in LEAN_TOP):
        return False
    sc = p.get('scenarios')
    return (isinstance(sc, dict) and 'base' in sc
            and all(isinstance(d, dict) and all(k in d for k in LEAN_SCENARIO) for d in sc.values()))


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def differences(saved, rerun, key='', both_ways=False):
    """Every key of `saved` whose value differs in `rerun`, as (key path, text). Keys only in `rerun` are ignored
    unless both_ways."""
    out = []
    if isinstance(saved, dict) and isinstance(rerun, dict):
        for k, v in saved.items():
            path = f'{key}/{k}' if key else str(k)
            if k not in rerun:
                out.append((path, 'in the saved result, missing in the rerun'))
            else:
                out += differences(v, rerun[k], path, both_ways)
        if both_ways:
            out += [(f'{key}/{k}' if key else str(k), 'in the rerun, not in the saved result') for k in rerun if k not in saved]
    elif isinstance(saved, list) and isinstance(rerun, list):
        if len(saved) != len(rerun):
            out.append((key, f'saved has {len(saved)} items, rerun has {len(rerun)}'))
        else:
            for i, (a, b) in enumerate(zip(saved, rerun)):
                out += differences(a, b, f'{key}[{i}]', both_ways)
    elif (is_number(saved) and is_number(rerun) and saved == rerun) or (type(saved) is type(rerun) and saved == rerun):
        pass
    else:
        out.append((key, f'saved {saved!r}, rerun {rerun!r}'))
    return out


def report(saved_path, diffs):
    lines = [f'{rel(saved_path)}: {len(diffs)} key(s) differ from the rerun (the saved file is the published value):']
    lines += [f'  {rel(saved_path)} :: {k}: {text}' for k, text in diffs[:MAX_SHOWN]]
    if len(diffs) > MAX_SHOWN:
        lines.append(f'  ... and {len(diffs) - MAX_SHOWN} more')
    return '\n'.join(lines)


def dated_folders(company_dir):
    return sorted(os.path.join(company_dir, d) for d in os.listdir(company_dir)
                  if DATED.match(d) and os.path.isdir(os.path.join(company_dir, d)))


def lean_files():
    """(params path, parsed params or None, saved result path or None) for every lean_params*.json in a dated folder."""
    rows = []
    for fp in sorted(glob.glob(os.path.join(COMPANIES, '*', '*', 'lean_params*.json'))):
        if not DATED.match(os.path.basename(os.path.dirname(fp))):
            continue
        result = os.path.join(os.path.dirname(fp), os.path.basename(fp).replace('lean_params', 'lean_result', 1))
        rows.append((fp, load(fp), result if os.path.exists(result) else None))
    return rows


def custom_path(job, name):
    return os.path.join(COMPANIES, *job['dir'].split('/'), *name.split('/'))


def folder_state(folder):
    state = {}
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            st = os.stat(os.path.join(base, f))
            state[rel(os.path.join(base, f))] = (st.st_size, st.st_mtime_ns)
    return state


class SavedLeanValuationsTest(unittest.TestCase):
    def test_every_saved_lean_result_reproduces(self):
        compared = 0
        for fp, p, result in lean_files():
            if not is_lean(p):
                continue
            with self.subTest(params=rel(fp)):
                rerun = json.loads(json.dumps(lean_model.run(p)))   # the same round trip as lean_model.py --out
                if result is None:
                    continue                                          # e.g. pre-review params: must run, nothing to compare
                diffs = differences(load(result), rerun)
                compared += 1
                if diffs:
                    self.fail(report(result, diffs))
        self.assertTrue(compared, 'no saved lean result was found under research/companies/')

    def test_no_listed_baseline_is_missing(self):
        found = {rel(fp)[len('research/companies/'):] for fp, p, result in lean_files() if is_lean(p) and result}
        missing = sorted(set(BASELINES) - found)
        if missing:
            self.fail('published lean baselines without a lean-format params file and its saved result (a baseline may be '
                      'superseded by a newer folder, never removed): ' + ', '.join(missing))

    def test_every_params_and_result_file_is_covered(self):
        custom_inputs = {rel(custom_path(j, a)) for j in CUSTOM for a in j.get('args', [])
                         if '{out}' not in a and not a.startswith('-')}
        custom_saved = {rel(custom_path(j, s)) for j in CUSTOM for s in j['compare'].values()}
        problems = []
        lean_results = set()
        for fp, p, result in lean_files():
            if is_lean(p):
                if result:
                    lean_results.add(rel(result))
            elif rel(fp) not in custom_inputs:
                problems.append(f'{rel(fp)}: not in lean format and not the input of a custom model listed in CUSTOM')
        for rp in sorted(glob.glob(os.path.join(COMPANIES, '*', '*', 'lean_result*.json'))):
            if rel(rp) not in lean_results and rel(rp) not in custom_saved:
                problems.append(f'{rel(rp)}: a saved result with no lean-format params of the same suffix and no custom rerun')
        if problems:
            self.fail('saved files this test cannot guard:\n' + '\n'.join(problems))


class SavedCustomModelsTest(unittest.TestCase):
    def test_custom_models_reproduce_their_saved_results(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', FE_ROOT=ROOT)
        folders = sorted({os.path.join(COMPANIES, *j['dir'].split('/')) for j in CUSTOM})
        before = {f: folder_state(f) for f in folders if os.path.isdir(f)}
        for job in CUSTOM:
            with self.subTest(model=job['name']), tempfile.TemporaryDirectory(prefix='saved_valuations_') as out:
                script = custom_path(job, job['script'])
                needed = [script] + [custom_path(job, n) for n in job.get('copy', []) + job.get('uses', [])
                                     + list(job['compare'].values())]
                missing = [rel(f) for f in needed if not os.path.exists(f)]
                self.assertEqual(missing, [], f"{job['name']}: files of a custom model listed in CUSTOM are missing")
                for name in job.get('copy', []):
                    with open(custom_path(job, name), 'rb') as src, open(os.path.join(out, name), 'wb') as dst:
                        dst.write(src.read())
                args = [a.replace('{out}', out) if '{out}' in a or a.startswith('-') else custom_path(job, a)
                        for a in job.get('args', [])]
                run_env = dict(env, **{job['env']: out}) if job.get('env') else env
                done = subprocess.run([sys.executable, script] + args, cwd=out if job.get('cwd_out') else os.path.dirname(script),
                                      env=run_env, capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, 0, f'{rel(script)} failed:\n{done.stderr[-2000:]}')
                for written, saved in job['compare'].items():
                    new = os.path.join(out, written)
                    self.assertTrue(os.path.exists(new), f'{rel(script)} did not write {written}')
                    diffs = differences(load(custom_path(job, saved)), load(new), both_ways=True)
                    if diffs:
                        self.fail(report(custom_path(job, saved), diffs))
        changed = sorted(k for f, state in before.items() for k in set(state) | set(folder_state(f))
                         if state.get(k) != folder_state(f).get(k))
        if changed:
            self.fail('a rerun wrote into research/ (results must go to the temp folder): ' + ', '.join(changed))

    def test_every_script_is_rerun_or_listed(self):
        covered = {f"{j['dir']}/{n}" for j in CUSTOM for n in [j['script']] + j.get('uses', [])} | set(NOT_RERUN)
        scripts = sorted(rel(f)[len('research/companies/'):]
                         for f in glob.glob(os.path.join(COMPANIES, '**', '*.py'), recursive=True))
        unguarded = [s for s in scripts if s not in covered]
        if unguarded:
            self.fail('scripts under research/companies/ that this test neither reruns (CUSTOM) nor lists with a reason '
                      '(NOT_RERUN): ' + ', '.join(unguarded))


class ThesisBookInputsTest(unittest.TestCase):
    def test_every_thesis_company_has_runnable_inputs_or_a_note(self):
        custom_dirs = {j['dir'] for j in CUSTOM}
        files = lean_files()
        lean_dirs = {os.path.dirname(fp) for fp, p, result in files if is_lean(p) and result}
        no_result = {os.path.dirname(fp): rel(fp) for fp, p, result in files if is_lean(p) and not result}
        failures = []
        for company in sorted(os.listdir(COMPANIES)):
            cdir = os.path.join(COMPANIES, company)
            if not os.path.isfile(os.path.join(cdir, 'thesis_he.md')):
                continue
            folders = dated_folders(cdir)
            if any(d in lean_dirs or f'{company}/{os.path.basename(d)}' in custom_dirs for d in folders):
                continue
            if any(self._has_note(d) for d in [cdir] + folders):
                continue
            unsaved = [no_result[d] for d in folders if d in no_result]
            failures.append(f'{company}: thesis_he.md, but no dated folder with a lean params file and its saved result, no '
                            f'custom model listed in CUSTOM, and no SOURCE.md / note saying "{NOTE_PHRASE}"'
                            + (f' (lean params without a saved lean_result next to them: {", ".join(unsaved)})' if unsaved else
                               f' (dated folders: {", ".join(os.path.basename(d) for d in folders) or "none"})'))
        if failures:
            self.fail('thesis-book companies whose published value cannot be rerun and compared:\n' + '\n'.join(failures))

    @staticmethod
    def _has_note(folder):
        for name in os.listdir(folder):
            low = name.lower()
            if (low == 'source.md' or low.startswith('note')) and low.endswith(('.md', '.txt')):
                with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as f:
                    if NOTE_PHRASE in ' '.join(f.read().lower().split()):
                        return True
        return False


class DifferencesTest(unittest.TestCase):
    """The comparison itself: it must see a moved value and must ignore keys the saved result does not have."""

    def test_moved_value_missing_key_and_new_key(self):
        saved = {'scenarios': {'base': {'value_today': 239.41, 'year_end_values': {'2031': 400.0}}}, 'g': [1.0, 2.0]}
        same = {'scenarios': {'base': {'value_today': 239.41, 'year_end_values': {'2031': 400}}}, 'g': [1.0, 2.0], 'new': 1}
        self.assertEqual(differences(saved, same), [])
        self.assertEqual([k for k, _ in differences(saved, same, both_ways=True)], ['new'])
        moved = json.loads(json.dumps(same))
        moved['scenarios']['base']['value_today'] = 239.42
        moved['g'][1] = 2.5
        del moved['scenarios']['base']['year_end_values']
        self.assertEqual([k for k, _ in differences(saved, moved)],
                         ['scenarios/base/value_today', 'scenarios/base/year_end_values', 'g[1]'])
        self.assertEqual(len(differences({'a': True, 'b': None}, {'a': 1, 'b': 0})), 2)

    def test_lean_format_is_read_from_the_keys(self):
        p = load(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json'))
        self.assertTrue(is_lean(p))
        self.assertFalse(is_lean({k: v for k, v in p.items() if k != 'rev0_m'}))
        self.assertFalse(is_lean(dict(p, scenarios={'base': {'ni_g': [0.1], 'pe_exit': 14}})))


if __name__ == '__main__':
    unittest.main()
