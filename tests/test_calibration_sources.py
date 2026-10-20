"""calibration.py and sources.py, on SYNTHETIC summaries."""
import unittest

from fundamental_engine import calibration as C, insiders as I, sources as S


class CalibrationTests(unittest.TestCase):
    def test_cap_weighted_engine_market_return_and_index_input(self):
        rows = [{'symbol': 'BIG', 'base_irr': .06, 'bear_irr': .03}, {'symbol': 'SMALL', 'base_irr': .12, 'bear_irr': .05},
                {'symbol': 'NOVAL', 'base_irr': None}]
        out = C.market_implied(rows, {'BIG': 9e11, 'SMALL': 1e11, 'NOVAL': 1e11}, '2026-10-20')
        self.assertAlmostEqual(out['engine_market_return'], .066)
        self.assertAlmostEqual(out['coverage_by_value'], 1e12 / 1.1e12)
        self.assertAlmostEqual(out['index_input']['low'], .056)
        self.assertIn('reject', out['reading'])                              # 6.6% vs 8.75%: the assumption would reject

    def test_outliers_are_clipped(self):
        out = C.market_implied([{'symbol': 'X', 'base_irr': 3.0}], {'X': 1e9}, '2026-10-20')
        self.assertEqual(out['engine_market_return'], .40)


class SourceTests(unittest.TestCase):
    def test_roles_and_attention_reading(self):
        self.assertTrue(all(c['role'] in ('screen', 'signal', 'attention', 'lead', 'evidence', 'event') for c in S.CHANNELS))
        self.assertEqual(S.attention_reading(.0047, .0001, .03), 'under_followed')
        self.assertEqual(S.attention_reading(.13, .01, .137), 'crowded')
        with self.assertRaises(ValueError):
            S.lead_evidence('X post', '2026-10-01', 'great stock', 'buy')

    def test_insider_clusters_need_several_managers(self):
        base = {'issuer_cik': 1, 'symbol': 'SYN', 'issuer': 'Syn', 'date': '2026-09-01', 'filed': '2026-09-03', 'accession': 'a'}
        rows = [dict(base, owner='A', owner_cik='1', relationship='Officer', title='CEO', value=300000.),
                dict(base, owner='B', owner_cik='2', relationship='Director', title='', value=50000.),
                dict(base, owner='F', owner_cik='3', relationship='TenPercentOwner', title='', value=9e6)]
        cl = I.clusters(rows, {'SYN': 1e9})
        self.assertEqual((cl[0]['n_buyers'], cl[0]['n_officers'], cl[0]['value']), (2, 1, 350000.))   # the fund is not management
        self.assertEqual(I.evidence(cl[0])['kind'], 'insider_purchase_cluster')
        self.assertEqual(I.clusters(rows[:1]), [])                                                  # one buyer is not a cluster


if __name__ == '__main__':
    unittest.main()
