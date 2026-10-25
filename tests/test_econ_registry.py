"""WP1 of the econ layer: the registry brain/economics.json, its validation in econ.py, the reading plan, the packet and
the packaging. Every test here fails without WP1 (no registry, no econ module, no economic_models packet, no playbook
paragraph). Fixture KPIs are synthetic; nothing is fetched (the network is blocked while a plan is built).
"""
import fnmatch
import importlib.resources
import json
import re
import subprocess
import sys
import tomllib
import unittest
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest import mock

from fundamental_engine import econ, facts as F, model_card as M
from fundamental_engine.supervisor import catalog, packet

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'fundamental_engine'
MODELS = ('marketplace', 'broker', 'data_platform_software', 'consumer_innovator', 'memory_cycle', 'gpu_vendor',
          'compute_leasing', 'subscriber_network', 'digital_bank', 'ads_platform')
OVERLAYS = ('vendor_financing', 'funded_customer_chain', 'acquisition_sbc', 'segments', 'contract_coverage', 'bank_capital')
PREREG_RULES = {'identity_tolerance': 0.02, 'growth_identity_pp': 0.01, 'partial_below': 0.90, 'firm_exposure_high': 0.25,
                'cfo_to_ni_min': 0.80, 'near_threshold': 0.03, 'receivables_gap_pp': 0.10, 'minor_line_share': 0.10,
                'flat_units_abs': 0.02, 'segment_material': 0.25, 'leverage_share_min': 0.667, 'econ_view_min_pp': 0.02}
PLAYBOOK_MODELS = {'platform': ('marketplace', 'ads_platform'), 'financials': ('broker', 'digital_bank'),
                   'software': ('data_platform_software',), 'consumer': ('consumer_innovator',),
                   'semiconductor': ('memory_cycle', 'gpu_vendor')}


def driver_keys():
    keys = set()
    for rows in M.DRIVERS.values():
        for row in rows:
            keys.update(row[3])
    return keys


def factbook(kpis, as_of='2026-10-21'):
    """A synthetic FactBook whose facts are named by registry KPI ids: {id: (period_end, value, available_at)}."""
    fb = F.new({'ticker': 'SYNB', 'name': 'Synthetic broker (test data)', 'currency': 'USD'}, as_of)
    F.add_source(fb, 'rel', 'issuer', 'issuer_release', 'issuer_document', as_of)
    for k, (end, value, available) in kpis.items():
        F.add_fact(fb, k, 'Q@' + end, end, value, 'rel', 'as_reported', available, unit='count')
    return fb


def no_network(*a, **k):
    raise AssertionError('the reading plan must not open a socket')


class RegistrySchemaTests(unittest.TestCase):
    def setUp(self):
        self.reg = econ.registry()

    def test_ten_models_six_overlays_version_and_vocabularies(self):
        self.assertEqual(self.reg['economics_version'], econ.ECONOMICS_VERSION)
        self.assertEqual(tuple(self.reg['models']), MODELS)
        self.assertEqual(tuple(self.reg['overlays']), OVERLAYS)
        self.assertEqual(set(self.reg['bases']), set(econ.BASES))
        self.assertEqual(set(self.reg['tags']), set(econ.TAGS))
        self.assertEqual(set(self.reg['statuses']), set(econ.STATUSES))
        for t in econ.TAGS:
            self.assertTrue(self.reg['tags'][t]['label_he'], t)
        self.assertEqual(econ.problems(self.reg), [])

    def test_every_model_has_a_core_identity_whose_references_are_declared(self):
        for mid, m in self.reg['models'].items():
            cores = [i for i in m['identities'] if i.get('core')]
            self.assertTrue(cores, mid)
            known = set(m['kpis']) | set(self.reg['lines']) | {i['id'] for i in m['identities']}
            for i in m['identities']:
                self.assertIn(i['type'], econ.IDENTITY_TYPES, f'{mid}.{i["id"]}')
                refs = econ.identity_refs(i)
                self.assertTrue(refs, f'{mid}.{i["id"]}')
                self.assertTrue(set(refs) <= known, f'{mid}.{i["id"]}: {set(refs) - known}')
            self.assertTrue(set(econ.required_kpis(mid)) <= set(m['kpis']), mid)
            self.assertTrue(econ.required_kpis(mid), mid)
            self.assertTrue(m['label_he'], mid)
            self.assertIsInstance(m['price_cyclical'], bool, mid)
        self.assertEqual(econ.required_kpis('broker'), ['accounts', 'client_equity'])
        self.assertEqual(econ.required_kpis('memory_cycle'), ['bits', 'asp'])
        self.assertTrue({'memory_cycle', 'gpu_vendor'} <= {m for m in MODELS if self.reg['models'][m]['price_cyclical']})

    def test_every_kpi_has_unit_basis_tag_and_one_unit_per_id(self):
        units = {}
        for mid, m in self.reg['models'].items():
            self.assertTrue(m['kpis'], mid)
            for kid, spec in m['kpis'].items():
                self.assertTrue(spec['unit'], f'{mid}.{kid}')
                self.assertTrue(spec['basis'] and set(spec['basis']) <= set(econ.BASES), f'{mid}.{kid}')
                self.assertIn(spec['tag'], econ.TAGS, f'{mid}.{kid}')
                self.assertEqual(units.setdefault(kid, spec['unit']), spec['unit'], kid)
        self.assertEqual(econ.kpi('accounts')['unit'], 'count')
        self.assertEqual(econ.kpi_unit('margin_rate'), 'rate')
        self.assertEqual(econ.kpi('arpu')['models'], {'subscriber_network': 'mix', 'ads_platform': 'cyclical'})
        self.assertEqual(econ.kpi('products', 'digital_bank')['basis'], ['cumulative'])
        with self.assertRaises(ValueError):
            econ.kpi('acounts')

    def test_rules_block_carries_status_and_the_frozen_prereg_values(self):
        rules = self.reg['rules']
        self.assertIn('uncalibrated', rules['_status'])
        self.assertIn('prereg_econ', rules['_status'])
        for k, v in PREREG_RULES.items():
            self.assertEqual(rules[k], v, k)
        self.assertEqual(econ.FROZEN_RULES, tuple(PREREG_RULES))
        self.assertEqual(econ.rules()['renewal_window_years'], 4)
        self.assertEqual(econ.rules()['cycle_history_min_years'], 6)

    def test_prereg_table_and_log_name_every_registry_rule_with_its_value(self):
        """The registry's thresholds are the prereg's: the section-1 table gives the twelve frozen rules, and every
        other numeric rule key is named in docs/prereg_econ.md with the same value (the WP1 deviations-log entry), so a
        rule added to the registry without a line in the prereg fails here."""
        text = (ROOT / 'docs' / 'prereg_econ.md').read_text(encoding='utf-8')
        table = {m.group(1): float(m.group(2)) for m in re.finditer(r'^\| (\w+) \| ([0-9.]+) \|', text, re.M)}
        self.assertEqual(set(table), set(econ.FROZEN_RULES))
        rules = self.reg['rules']
        for k, v in table.items():
            self.assertEqual(rules[k], v, k)
        others = {k: v for k, v in rules.items() if k not in table and not k.startswith('_')}
        self.assertIn('cycle_history_min_years', others)
        for k, v in others.items():
            m = re.search(r'`' + re.escape(k) + r'` ([0-9.]+)', text)
            self.assertIsNotNone(m, f'{k}: not named in docs/prereg_econ.md')
            self.assertEqual(float(m.group(1)), v, k)
            self.assertIsInstance(v, (int, float), k)

    def test_checks_falsifiers_overlays_and_select_reference_declared_things(self):
        checks = self.reg['checks']
        for cid, spec in checks.items():
            self.assertIn(spec['kind'], econ.CHECK_KINDS, cid)
            if spec['kind'] == 'flag':
                self.assertIn(spec['severity'], econ.SEVERITIES, cid)
                self.assertIsInstance(spec['fixed'], bool, cid)
        for fixed_high in ('segment_unexplained', 'nco_basis'):
            self.assertEqual((checks[fixed_high]['severity'], checks[fixed_high]['fixed']), ('high', True))
        self.assertEqual((checks['per_unit_vs_units']['severity'], checks['lta_unproven']['severity']), ('info', 'info'))
        for mid, m in self.reg['models'].items():
            self.assertTrue(set(m['checks']) <= set(checks), mid)
            self.assertTrue(set(m['overlays']) <= set(OVERLAYS), mid)
            self.assertTrue(set(m['select']['card_types']) <= set(M.TYPES), mid)
            self.assertTrue(set(m['select']['kpi_any']) <= set(m['kpis']), mid)
            ids = set(m['kpis']) | {i['id'] for i in m['identities']}
            self.assertTrue(m['falsifier_kpis'] and set(m['falsifier_kpis']) <= ids, mid)
            self.assertTrue(m['reading'] and m['questions'], mid)
        self.assertEqual(econ.falsifiers('broker'), [{'id': 'net_new_accounts', 'kind': 'kpi', 'tag': 'structural'},
                                                    {'id': 'revenue_yield_on_equity', 'kind': 'identity', 'tag': 'cyclical'}])
        self.assertEqual(econ.factor_tags('broker')['equity_per_account'], 'mix')
        for oid, o in self.reg['overlays'].items():
            self.assertTrue(set(o['checks']) <= set(checks), oid)
            self.assertTrue(set(o['rules_used']) <= set(self.reg['rules']), oid)
            self.assertTrue(set(o['applies_to']) <= set(MODELS) | {'*'}, oid)
            self.assertTrue(o['label_he'] and o['boundary'], oid)
        self.assertEqual(econ.checks('gpu_vendor')['funding_level']['kind'], 'reading')

    def test_driver_keys_new_keys_is_exactly_the_set_outside_model_card_drivers(self):
        """Read-only import of model_card: a registry KPI id either already is a driver evidence key or is listed."""
        existing = driver_keys()
        all_ids = set(econ.kpi_ids())
        self.assertEqual(set(self.reg['new_keys']), all_ids - existing)
        self.assertEqual(set(self.reg['new_keys']) & existing, set())
        self.assertTrue({'gross_bookings', 'customers', 'net_revenue_retention', 'dau', 'subscribers'} <= all_ids & existing)
        self.assertTrue({'accounts', 'darts', 'client_equity', 'bits', 'gw_shipped'} <= set(self.reg['new_keys']))

    def test_registry_is_a_copy_deterministic_and_hashed(self):
        a, b = econ.registry(), econ.registry()
        self.assertEqual(a, b)
        a['models']['broker']['kpis'].clear()
        self.assertEqual(econ.registry()['models']['broker']['kpis'], b['models']['broker']['kpis'])
        self.assertEqual(econ.registry_sha256(), sha256(econ.REGISTRY_PATH.read_bytes()).hexdigest())
        json.dumps(b, allow_nan=False)
        self.assertEqual(econ.models(), list(MODELS))
        with self.assertRaises(ValueError):
            econ.model('bank')
        with self.assertRaises(ValueError):
            econ.overlay('sbc')

    def test_registry_holds_no_company_numbers(self):
        """Anti-invention: outside `rules` every number is a small parameter (annualize, version), never a company figure."""
        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from walk(v, f'{path}.{k}')
            elif isinstance(node, list):
                for n, v in enumerate(node):
                    yield from walk(v, f'{path}[{n}]')
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                yield path, node
        for path, v in walk({k: v for k, v in self.reg.items() if k != 'rules'}, 'registry'):
            self.assertLessEqual(abs(v), 12, path)

    def test_validation_rejects_bad_registries_naming_the_path(self):
        bad = deepcopy(self.reg)
        bad['models']['broker']['kpis']['darts']['basis'] = ['daily']
        self.assertTrue(any('models.broker.kpis.darts.basis' in p for p in econ.problems(bad)))
        bad = deepcopy(self.reg)
        bad['models']['broker']['identities'][0]['unit_kpi'] = 'acounts'
        self.assertTrue(any('acounts' in p for p in econ.problems(bad)))
        bad = deepcopy(self.reg)
        for i in bad['models']['memory_cycle']['identities']:
            i.pop('core', None)
        self.assertTrue(any('no core identity' in p for p in econ.problems(bad)))
        bad = deepcopy(self.reg)
        del bad['rules']['_status']
        with self.assertRaises(ValueError) as cm:
            econ.validate(bad)
        self.assertIn('rules._status', str(cm.exception))
        bad = deepcopy(self.reg)
        bad['models']['ads_platform']['kpis']['arpu']['unit'] = 'currency'
        self.assertTrue(any('one unit per KPI id' in p for p in econ.problems(bad)))
        bad = deepcopy(self.reg)
        bad['models']['broker']['checks'].append('vibes')
        self.assertTrue(any("'vibes'" in p for p in econ.problems(bad)))
        bad = deepcopy(self.reg)
        bad['new_keys'].append('customers')
        self.assertTrue(any('new_keys' in p for p in econ.problems(bad)))


class ReadingPlanTests(unittest.TestCase):
    def test_broker_plan_marks_accounts_present_darts_missing_and_fetches_nothing(self):
        fb = factbook({'accounts': ('2026-09-30', 5.58e6, '2026-10-17')})
        with mock.patch('socket.socket', no_network), mock.patch('socket.create_connection', no_network):
            plan = econ.reading_plan('broker', fb)
        kpi_step = plan['steps'][1]
        self.assertEqual(kpi_step['doc'], 'monthly metrics (IR site)')
        self.assertEqual(kpi_step['have'], ['accounts'])
        self.assertIn('darts', kpi_step['missing'])
        self.assertIn('client_equity', kpi_step['missing'])
        self.assertEqual(kpi_step['find'][:2], ['accounts', 'client_equity'])          # core first
        self.assertEqual(kpi_step['periods'], ['latest', 'prior_quarter', 'year_ago'])
        self.assertEqual(kpi_step['state'], ['kpi_basis', 'scope', 'currency_basis'])
        self.assertEqual(plan['have'], [{'id': 'accounts', 'period': 'Q@2026-09-30', 'period_end': '2026-09-30', 'source_ids': ['rel'], 'facts': 1}])
        self.assertEqual([s['step'] for s in plan['steps']], [1, 2, 3, 4, 5])
        self.assertEqual(plan['steps'][0]['find'], ['why_customers_pay', 'why_they_stay'])
        self.assertEqual(plan['steps'][2]['doc'], '10-Q Item 3')
        self.assertIn('loan_sales', plan['steps'][3]['find'])
        self.assertIn('pass-through fees', plan['steps'][4]['find'])
        self.assertTrue(plan['steps'][4]['questions'])
        self.assertEqual((plan['fetches'], plan['as_of'], plan['required']), ('nothing', '2026-10-21', ['accounts', 'client_equity']))
        self.assertEqual(plan['missing'], kpi_step['missing'])
        json.dumps(plan, allow_nan=False)

    def test_point_in_time_a_fact_available_after_as_of_is_missing(self):
        fb = factbook({'accounts': ('2026-09-30', 5.58e6, '2026-10-17')}, as_of='2026-10-16')
        plan = econ.reading_plan('broker', fb)
        self.assertEqual(plan['steps'][1]['have'], [])
        self.assertIn('accounts', plan['missing'])

    def test_plan_without_factbook_and_unknown_model(self):
        plan = econ.reading_plan('memory_cycle')
        self.assertEqual((plan['have'], plan['as_of'], plan['label_he']), ([], None, 'זיכרון (מחזורי)'))
        self.assertEqual(plan['missing'][:2], ['bits', 'asp'])
        self.assertEqual(plan['steps'][1]['doc'], 'release supplemental / MD&A key metrics')
        with self.assertRaises(ValueError):
            econ.reading_plan('bank')
        self.assertEqual(econ.reading_plan('broker'), econ.reading_plan('broker'))


class PackagingAndBrainTests(unittest.TestCase):
    def test_pyproject_package_data_covers_registry_and_protocol(self):
        globs = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['setuptools']['package-data']['fundamental_engine']
        for rel in ('brain/economics.json', 'brain/protocols/economic_models.md'):
            self.assertTrue((PKG / rel).is_file(), rel)
            self.assertTrue(any(fnmatch.fnmatch(rel, g) for g in globs), rel)

    def test_registry_and_protocol_are_package_resources(self):
        files = importlib.resources.files('fundamental_engine')
        self.assertEqual(json.loads(files.joinpath('brain/economics.json').read_text(encoding='utf-8'))['economics_version'], 1)
        self.assertTrue(files.joinpath('brain/protocols/economic_models.md').read_text(encoding='utf-8').startswith('# Economic models'))

    def test_catalog_packet_economic_models_names_every_model_and_overlay(self):
        ids = [p['id'] for p in catalog()['packets']]
        self.assertIn('economic_models', ids)
        self.assertEqual(len(ids), len(set(ids)))
        content = packet('economic_models')['content']
        self.assertTrue(content.startswith('#'))
        for m in MODELS + OVERLAYS:
            self.assertIn(m, content, m)
        for word in ('basis_mismatch', 'scope_mismatch', 'unclassified', 'prereg_econ', 'not a valuation'):
            self.assertIn(word, content, word)

    def test_playbooks_name_their_economic_models(self):
        for book, ids in PLAYBOOK_MODELS.items():
            text = (PKG / 'brain' / 'playbooks' / f'{book}.md').read_text(encoding='utf-8')
            self.assertIn('economics.json', text, book)
            for m in ids:
                self.assertIn(m, text, f'{book}: {m}')


class NoInputInvarianceTests(unittest.TestCase):
    def test_pipeline_never_imports_econ(self):
        """WP1 wires nothing: the pipeline, decision, render, quarter, review and forecast stages do not import econ, so
        every existing output and hash is byte-identical."""
        code = ('import sys\n'
                'sys.path.insert(0, sys.argv[1])\n'
                'for m in ("pipeline", "decision", "render", "quarter", "review", "forecast", "model_card", "risk", "redteam", "plain"):\n'
                '    __import__("fundamental_engine." + m)\n'
                'print("fundamental_engine.econ" in sys.modules)')
        out = subprocess.run([sys.executable, '-I', '-c', code, str(ROOT)], cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), 'False')


if __name__ == '__main__':
    unittest.main()
