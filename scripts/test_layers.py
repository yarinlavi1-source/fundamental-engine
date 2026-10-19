"""Run the unit tests grouped by what they can prove (owner requirement: keep the layers separate).

Layers
- software:          code does what it says (contracts, arithmetic, gates, parsing).
- data_accuracy:     numbers match their sources and accounting identities; point-in-time discipline.
- analysis_quality:  the analysis behaves like an analyst on known situations (splits are not dilution,
                     a bank is not valued on FCFF, acquired growth is flagged, ...). Behaviour, not accuracy.
- decision_quality:  decision rules behave as specified (quality does not move with price, a value trap is
                     not 'attractive', disqualifying risks cannot be overridden, ...). Not proof of good picks.
- excess_return:     evidence about beating an index. No unit test can provide it; the layer lists the
                     empirical status from docs/EVIDENCE_STATUS.md so nobody mistakes green tests for alpha.

A TestCase class may declare LAYER = '<layer>'; otherwise the module map below decides (default software).
Usage: python scripts/test_layers.py [--json out.json]
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_LAYER = {
    'test_stage0': 'data_accuracy', 'test_pit': 'data_accuracy', 'test_data_accuracy': 'data_accuracy',
    'test_dataqa': 'data_accuracy', 'test_facts': 'data_accuracy', 'test_sec_frames': 'data_accuracy',
    'test_analysis': 'analysis_quality', 'test_model_card': 'analysis_quality', 'test_inflection': 'analysis_quality',
    'test_forecast': 'analysis_quality', 'test_gap_verdict': 'analysis_quality',
    'test_decision_render': 'decision_quality', 'test_risk_review': 'decision_quality', 'test_hardening': 'decision_quality',
    'test_screen': 'decision_quality', 'test_portfolio': 'decision_quality', 'test_decision_quality': 'decision_quality',
}
LAYERS = ('software', 'data_accuracy', 'analysis_quality', 'decision_quality', 'excess_return')


def _iter(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _iter(t)
        else:
            yield t


def layer_of(test):
    cls = type(test)
    explicit = getattr(cls, 'LAYER', None)
    if explicit:
        return explicit
    module = cls.__module__.rsplit('.', 1)[-1]
    return MODULE_LAYER.get(module, 'software')


def main(argv):
    sys.path.insert(0, ROOT)
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, 'tests'))
    groups = {k: unittest.TestSuite() for k in LAYERS}
    for t in _iter(suite):
        groups.setdefault(layer_of(t), unittest.TestSuite()).addTest(t)
    report = {}
    for name in LAYERS:
        if name == 'excess_return':
            path = os.path.join(ROOT, 'docs', 'EVIDENCE_STATUS.md')
            report[name] = {'tests': 0, 'status': 'not provable by unit tests',
                            'evidence_file': 'docs/EVIDENCE_STATUS.md' if os.path.exists(path) else None}
            continue
        res = unittest.TextTestRunner(stream=open(os.devnull, 'w'), verbosity=0).run(groups[name])
        report[name] = {'tests': res.testsRun, 'failures': len(res.failures), 'errors': len(res.errors),
                        'skipped': len(res.skipped), 'ok': res.wasSuccessful(),
                        'failed': [str(t) for t, _ in res.failures + res.errors]}
    for name in LAYERS:
        r = report[name]
        print(f"{name:18s} " + (f"{r['tests']:4d} tests  failures={r['failures']} errors={r['errors']} skipped={r['skipped']}"
                                if 'failures' in r else r['status']))
    if '--json' in argv:
        with open(argv[argv.index('--json') + 1], 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
    return 0 if all(report[k].get('ok', True) for k in LAYERS) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
