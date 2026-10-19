import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _res(excess_h, t, top_dd, add):
    s = lambda c, dd=-0.2: {'cagr': c, 'max_dd': dd, 'months': 90}
    return {'H1': {'holdout': [{'q': i, 'realized': v} for i, v in enumerate([.05, .1, .2, .5, .9])]},
            'H2': {'design': {'excess_vs_universe_per_yr': 0.03, 't_stat': 1, 'top_quintile': s(.1), 'universe': s(.07)},
                   'holdout': {'excess_vs_universe_per_yr': excess_h, 't_stat': t, 'top_quintile': s(.1, top_dd), 'universe': s(.08)}},
            'H3': {'holdout': {'momentum_near_high': s(.1), 'plus_pbeat_top2': s(.1 + add), 'halved12_a': .05, 'halved12_b': .04}}}


def run(res):
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(res, f)
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'gap_verdict.py'), f.name], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class TestVerdict(unittest.TestCase):
    def test_all_pass(self):
        v = run(_res(0.03, 2.5, -0.25, 0.03))
        self.assertTrue(v['signal_useful_per_prereg'])

    def test_t_stat_fails(self):
        v = run(_res(0.03, 1.5, -0.25, 0.03))
        self.assertTrue(v['H1']['pass']); self.assertFalse(v['H2']['pass']); self.assertFalse(v['signal_useful_per_prereg'])

    def test_drawdown_fails(self):
        v = run(_res(0.03, 2.5, -0.35, 0.03))
        self.assertFalse(v['H2']['holdout']['pass_dd'])


if __name__ == '__main__':
    unittest.main()
