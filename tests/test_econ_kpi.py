"""WP2 of the econ layer: KPI intake. facts.add_kpi stores a point-in-time KPI named by its registry id with its basis,
scope and currency basis; econ.kpi_table reads those facts at an as-of date; model_card._evidence sees them with no
wiring. Every behaviour test here fails without WP2 (no add_kpi, no kpi_table, no facts.rank); FixtureNumbersTests only
pins the constants below to the WP0 fixtures.

Numbers: IBKR Q3-26 / Q2-26 and Uber Q3-25 figures of tests/fixtures/econ (arithmetic fixtures, not truth). Rows that
only probe ranking, grouping or units carry values marked SYNTHETIC below; they are never reported figures. Nothing
is fetched.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from fundamental_engine import econ, facts as F, model_card as M

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests' / 'fixtures' / 'econ'
Q3 = ('Q@2026-09-30', '2026-09-30')              # IBKR Q3-26: period label and period end
Q2 = ('Q@2026-06-30', '2026-06-30')              # IBKR Q2-26
POST_DAY = '2026-10-17'      # the IBKR Q3-26 post was written about 2026-10-17 (fixture note); the design's PIT test date
Q2_RELEASE_DAY = '2026-07-22'                    # ibkr_8k_ex99_2q26 (fixture ibkr_2026q2.json)
# ibkr_2026q3.json kpis: accounts, client equity, DARTs, margin loans at the end and on average, margin rate, net new accounts
ACCOUNTS, CLIENT_EQUITY, DARTS = 5.58e6, 9.65e11, 4.27e6
MARGIN_LOANS_END, MARGIN_LOANS_AVG, MARGIN_RATE, NET_NEW_ACCOUNTS = 1.052e11, 1.04e11, 0.0425, 391000.0
ACCOUNTS_Q2 = 5.19e6                             # ibkr_2026q2.json kpis.accounts (issuer release)
FILED_PROBE = 5.581e6        # SYNTHETIC: a filed figure that disagrees with the post, to test ordering; not reported
# uber_2025q3.json segments[Mobility].gross_bookings, analyst_post_uber_2025q3 available 2025-11-04
UBER_MOBILITY_GB, UBER_Q3_25, UBER_DAY = 25.1e9, ('Q@2025-09-30', '2025-09-30'), '2025-11-04'


def factbook(as_of='2026-10-21', currency='USD', ticker='IBKR'):
    fb = F.new({'ticker': ticker, 'name': f'{ticker} (test data)', 'currency': currency}, as_of)
    F.add_source(fb, 'post', 'user', 'analyst_post', 'secondary', '2026-10-21', note='analyst_post_ibkr_2026q3')
    F.add_source(fb, 'rel_q2', 'issuer', 'issuer_release', 'issuer_document', Q2_RELEASE_DAY, note='ibkr_8k_ex99_2q26')
    F.add_source(fb, 'rel', 'issuer', 'issuer_release', 'issuer_document', '2026-10-21', note='SYNTHETIC ranking probe')
    F.add_source(fb, '10q', 'sec', 'companyfacts', 'filing_derived', '2026-11-10', note='SYNTHETIC ranking probe')
    F.add_source(fb, 'synthetic', 'test', 'unit probe', 'synthetic', '2026-10-21', note='SYNTHETIC: units and labels only')
    return fb


def accounts(fb, source='post', value=ACCOUNTS, available=POST_DAY, **kw):
    return F.add_kpi(fb, 'accounts', *Q3, value, source, available, kpi_basis=kw.pop('kpi_basis', 'balance'), **kw)


class FixtureNumbersTests(unittest.TestCase):
    def test_test_constants_are_the_fixture_figures(self):
        """The constants above are read from the WP0 fixtures, not typed from memory."""
        load = lambda n: json.loads((FIX / n).read_text(encoding='utf-8'))
        q3, q2, uber = load('ibkr_2026q3.json'), load('ibkr_2026q2.json'), load('uber_2025q3.json')
        k3 = {(k['id'], k['kpi_basis']): k['value'] for k in q3['kpis']}
        self.assertEqual((k3[('accounts', 'balance')], k3[('client_equity', 'balance')], k3[('darts', 'average')]),
                         (ACCOUNTS, CLIENT_EQUITY, DARTS))
        self.assertEqual((k3[('margin_loans', 'balance')], k3[('margin_loans', 'average')], k3[('margin_rate', 'average')],
                          k3[('net_new_accounts', 'flow')]), (MARGIN_LOANS_END, MARGIN_LOANS_AVG, MARGIN_RATE, NET_NEW_ACCOUNTS))
        self.assertEqual(({k['id']: k['value'] for k in q2['kpis']}['accounts'], q2['sources']['ibkr_8k_ex99_2q26']['available_at']),
                         (ACCOUNTS_Q2, Q2_RELEASE_DAY))
        mobility = [s for s in uber['segments'] if s['name'] == 'Mobility'][0]
        self.assertEqual((mobility['gross_bookings'], uber['period_end'], uber['sources']['analyst_post_uber_2025q3']['available_at']),
                         (UBER_MOBILITY_GB, UBER_Q3_25[1], UBER_DAY))


class AddKpiTests(unittest.TestCase):
    def test_point_in_time_a_kpi_available_after_as_of_is_invisible(self):
        fb = factbook(as_of='2026-10-16')
        row = accounts(fb)                                        # available 2026-10-17, one day after the cutoff
        self.assertIn(row, fb['facts'])                           # stored ...
        self.assertEqual(F.candidates(fb, 'accounts'), [])        # ... but invisible through every accessor
        self.assertIsNone(F.get(fb, 'accounts'))
        self.assertEqual(F.series(fb, 'accounts', 'quarter'), [])
        self.assertEqual(econ.reading_plan('broker', fb)['have'], [])
        self.assertEqual(econ.kpi_table(fb, 'broker')['have'], [])
        later = factbook(as_of='2026-10-17')
        accounts(later)
        self.assertEqual(F.get(later, 'accounts')['value'], ACCOUNTS)
        self.assertEqual(econ.kpi_table(later, 'broker')['have'], ['accounts'])

    def test_units_come_from_the_registry_never_the_usd_default(self):
        fb = factbook()
        self.assertEqual(accounts(fb)['unit'], 'count')
        self.assertEqual(F.add_kpi(fb, 'margin_rate', *Q3, MARGIN_RATE, 'post', POST_DAY, kpi_basis='average')['unit'], 'rate')
        self.assertEqual(F.add_kpi(fb, 'darts', *Q3, DARTS, 'post', POST_DAY, kpi_basis='average')['unit'], 'count_per_day')
        self.assertEqual(F.add_kpi(fb, 'bits', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='flow')['unit'], 'bits')
        # a currency-class KPI carries the reporting currency in place of the registry token, not 'USD' by default
        self.assertEqual(F.add_kpi(fb, 'client_equity', *Q3, CLIENT_EQUITY, 'post', POST_DAY, kpi_basis='balance')['unit'], 'USD')
        eur = factbook(currency='EUR', ticker='EURB')
        self.assertEqual(F.add_kpi(eur, 'client_equity', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='balance')['unit'], 'EUR')
        self.assertEqual(F.add_kpi(eur, 'arpu', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='average')['unit'], 'EUR_per_user')
        self.assertEqual(F.add_kpi(eur, 'arpu', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='average', unit='USD')['unit'], 'USD_per_user')
        self.assertEqual(accounts(fb, unit='count')['unit'], 'count')          # a stated unit that agrees is fine
        for kpi, unit in (('accounts', 'shares'), ('client_equity', 'dollars'), ('arpu', 'USD_per_user')):
            with self.assertRaises(ValueError):
                F.add_kpi(fb, kpi, *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='balance', unit=unit)
        self.assertNotIn('accounts', F.UNITS)
        self.assertEqual(F.add_fact(fb, 'accounts', *Q3, ACCOUNTS, 'post', 'as_reported', POST_DAY)['unit'], 'USD')   # the default add_kpi avoids
        nocur = {'factbook_version': 1, 'company': {'ticker': 'X'}, 'as_of': '2026-10-21', 'sources': fb['sources'], 'facts': []}
        with self.assertRaises(ValueError):
            F.add_kpi(nocur, 'client_equity', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='balance')
        self.assertEqual(F.add_kpi(nocur, 'accounts', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='balance')['unit'], 'count')

    def test_unknown_id_is_rejected_and_nothing_stored(self):
        fb = factbook()
        with self.assertRaises(ValueError) as cm:
            F.add_kpi(fb, 'acounts', *Q3, ACCOUNTS, 'post', POST_DAY, kpi_basis='balance')
        self.assertIn('acounts', str(cm.exception))
        self.assertIn('economics.json', str(cm.exception))
        self.assertEqual(fb['facts'], [])
        with self.assertRaises(ValueError):                                   # a reported line is not a KPI id
            F.add_kpi(fb, 'revenue', *Q3, 1.87e9, 'post', POST_DAY, kpi_basis='flow')

    def test_row_carries_basis_scope_and_currency_basis_through_add_fact(self):
        fb = factbook()
        row = accounts(fb, period_start='2026-07-01')
        self.assertEqual({k: row[k] for k in ('metric', 'period', 'period_end', 'period_start', 'value', 'unit', 'source_id', 'basis', 'available_at')},
                         {'metric': 'accounts', 'period': 'Q@2026-09-30', 'period_end': '2026-09-30', 'period_start': '2026-07-01',
                          'value': ACCOUNTS, 'unit': 'count', 'source_id': 'post', 'basis': 'as_reported', 'available_at': POST_DAY})
        self.assertEqual((row['kpi_basis'], row['scope'], row['currency_basis'], row['available_at_estimated']), ('balance', 'total', 'reported', False))
        self.assertIs(row, fb['facts'][-1])
        self.assertEqual(F.get(fb, 'accounts'), row)
        uber = factbook(ticker='UBER')
        F.add_source(uber, 'uber_post', 'user', 'analyst_post', 'secondary', UBER_DAY, note='analyst_post_uber_2025q3')
        gb = F.add_kpi(uber, 'gross_bookings', *UBER_Q3_25, UBER_MOBILITY_GB, 'uber_post', UBER_DAY, kpi_basis='flow', scope=' mobility ')
        self.assertEqual((gb['scope'], gb['currency_basis'], gb['unit'], gb['kpi_basis']), ('mobility', 'reported', 'USD', 'flow'))
        # the constant-currency flag is stored as stated (SYNTHETIC level: uber_2026q2.json gives only the +21% cc growth)
        cc = F.add_kpi(uber, 'gross_bookings', 'Q@2026-06-30', '2026-06-30', 1.0, 'synthetic', '2026-08-06', kpi_basis='flow',
                       currency_basis='constant_currency')
        self.assertEqual((cc['scope'], cc['currency_basis']), ('total', 'constant_currency'))
        json.dumps(fb, allow_nan=False)
        json.dumps(uber, allow_nan=False)

    def test_validation_refuses_what_would_invent_data(self):
        fb = factbook()
        F.add_source(fb, 'cons', 'fmp', 'financial-estimates-annual', 'consensus', '2026-10-21')
        F.add_source(fb, 'ib', 'ib', 'price-snapshot', 'market_feed', '2026-10-21')
        good = dict(period='Q@2026-09-30', period_end='2026-09-30', value=ACCOUNTS, source_id='post', available_at=POST_DAY, kpi_basis='balance')
        bad = [dict(kpi_basis='active'),                  # quarter.KPI_BASES's word, not a registry basis
               dict(kpi_basis=None), dict(currency_basis='cc'), dict(currency_basis=None),
               dict(scope=''), dict(scope=' '), dict(scope=None),
               dict(available_at='2026-09-29'),           # known before the period ended: guidance, not a KPI
               dict(source_id='nope'),
               dict(source_id='cons'),                    # consensus is an expectation, not a reported KPI
               dict(source_id='ib'),                      # a market feed carries prices and rates
               dict(value=None),                          # a fixture KPI with only a growth rate has no level to store
               dict(value='5.58e6'), dict(value=True), dict(value=float('nan')), dict(value=float('inf')),
               dict(period=''), dict(period=None),
               dict(period='Q@2026-06-30'),               # the label names another quarter than period_end
               dict(period_start='2026-10-01')]           # a window that starts after it ends
        for kw in bad:
            with self.assertRaises(ValueError, msg=kw):
                F.add_kpi(fb, 'accounts', **{**good, **kw})
        self.assertEqual(fb['facts'], [])
        F.add_kpi(fb, 'accounts', **good)
        F.add_kpi(fb, 'accounts', **{**good, 'period': 'FY-Q3-2026'})    # a label without '@<date>' is not parsed
        F.add_kpi(fb, 'accounts', **{**good, 'value': 5580000})          # an int level is a number
        self.assertEqual(len(fb['facts']), 3)
        fb['facts'].clear()
        self.assertEqual(accounts(fb, available='2026-09-30')['available_at'], '2026-09-30')       # equal to the period end is allowed
        with self.assertRaises(TypeError):                                                       # basis is keyword-only, never positional
            F.add_kpi(fb, 'accounts', *Q3, ACCOUNTS, 'post', POST_DAY, 'balance')

    def test_a_basis_the_registry_does_not_declare_is_stored_as_stated(self):
        fb = factbook()
        row = accounts(fb, kpi_basis='average')                 # the registry declares accounts as a balance
        self.assertEqual(row['kpi_basis'], 'average')
        self.assertEqual(econ.kpi_table(fb, 'broker')['kpis']['accounts']['rows'][0]['basis_declared'], False)

    def test_driver_flip_model_card_evidence_sees_the_kpi_without_wiring(self):
        fb = factbook(ticker='UBER')
        F.add_source(fb, 'uber_post', 'user', 'analyst_post', 'secondary', UBER_DAY, note='analyst_post_uber_2025q3')
        self.assertEqual(M._evidence(fb, {}, ['gross_bookings']), [])
        F.add_kpi(fb, 'gross_bookings', *UBER_Q3_25, UBER_MOBILITY_GB, 'uber_post', UBER_DAY, kpi_basis='flow', scope='mobility')
        self.assertEqual(M._evidence(fb, {}, ['gross_bookings']), [{'kind': 'fact', 'key': 'gross_bookings', 'source_ids': ['uber_post']}])
        tree = M._driver_tree(fb, {}, {'primary': 'platform_marketplace', 'secondary': []})
        self.assertEqual((tree[0]['driver'], tree[0]['data_status']), ('gmv_or_users', 'available'))
        self.assertEqual((tree[1]['driver'], tree[1]['data_status']), ('take_rate', 'missing'))  # no fact, no invention
        early = factbook(ticker='UBER', as_of='2025-11-03')                                      # point in time holds here too
        F.add_source(early, 'uber_post', 'user', 'analyst_post', 'secondary', UBER_DAY)
        F.add_kpi(early, 'gross_bookings', *UBER_Q3_25, UBER_MOBILITY_GB, 'uber_post', UBER_DAY, kpi_basis='flow', scope='mobility')
        self.assertEqual(M._evidence(early, {}, ['gross_bookings']), [])


class KpiTableTests(unittest.TestCase):
    def ibkr(self, order=None):
        fb = factbook()
        adds = {
            'acc_post': lambda: accounts(fb),
            'acc_rel': lambda: accounts(fb, 'rel'),                                   # SYNTHETIC ranking probe (post's figure)
            'acc_10q': lambda: accounts(fb, '10q', FILED_PROBE, '2026-11-05'),        # SYNTHETIC ranking probe
            'acc_q2': lambda: F.add_kpi(fb, 'accounts', *Q2, ACCOUNTS_Q2, 'rel_q2', Q2_RELEASE_DAY, kpi_basis='balance'),
            'acc_pro': lambda: accounts(fb, scope='pro'),                             # SYNTHETIC scope label
            'acc_avg': lambda: accounts(fb, kpi_basis='average'),                     # SYNTHETIC basis label
            'equity': lambda: F.add_kpi(fb, 'client_equity', *Q3, CLIENT_EQUITY, 'post', POST_DAY, kpi_basis='balance'),
            'ml_end': lambda: F.add_kpi(fb, 'margin_loans', *Q3, MARGIN_LOANS_END, 'post', POST_DAY, kpi_basis='balance'),
            'ml_avg': lambda: F.add_kpi(fb, 'margin_loans', *Q3, MARGIN_LOANS_AVG, 'post', POST_DAY, kpi_basis='average'),
            'rate': lambda: F.add_kpi(fb, 'margin_rate', *Q3, MARGIN_RATE, 'post', POST_DAY, kpi_basis='average'),
            'nna_plain': lambda: F.add_fact(fb, 'net_new_accounts', *Q3, NET_NEW_ACCOUNTS, 'post', 'as_reported', POST_DAY),
        }
        for k in (order or list(adds)):
            adds[k]()
        return fb

    def test_same_kpis_in_a_different_order_give_an_identical_table(self):
        a = self.ibkr()
        b = self.ibkr(order=['nna_plain', 'rate', 'ml_avg', 'ml_end', 'equity', 'acc_avg', 'acc_pro', 'acc_q2', 'acc_10q', 'acc_rel', 'acc_post'])
        self.assertNotEqual([f['metric'] for f in a['facts']], [f['metric'] for f in b['facts']])
        ta, tb = econ.kpi_table(a, 'broker'), econ.kpi_table(b, 'broker')
        self.assertEqual(ta, tb)
        self.assertEqual(ta['sha256'], tb['sha256'])
        self.assertEqual(json.dumps(ta, sort_keys=True), json.dumps(tb, sort_keys=True))
        a['as_of'] = b['as_of'] = '2026-11-10'                     # with the 10-Q usable, the same holds
        self.assertEqual(econ.kpi_table(a, 'broker'), econ.kpi_table(b, 'broker'))
        self.assertNotEqual(econ.kpi_table(a, 'broker')['sha256'], ta['sha256'])
        json.dumps(ta, allow_nan=False, ensure_ascii=False)
        # SYNTHETIC: two facts that tie on every rank key and differ only in value: the smaller value wins in either order
        x, y = factbook(), factbook()
        accounts(x, value=ACCOUNTS), accounts(x, value=FILED_PROBE)
        accounts(y, value=FILED_PROBE), accounts(y, value=ACCOUNTS)
        self.assertEqual(econ.kpi_table(x, 'broker'), econ.kpi_table(y, 'broker'))
        row = econ.kpi_table(x, 'broker')['kpis']['accounts']['rows'][0]
        self.assertEqual((row['value'], row['alternates'], row['alternate_values']), (ACCOUNTS, 1, [FILED_PROBE]))   # the disagreement stays visible
        # SYNTHETIC: equal in every ranked field but the stated window start: the row is the same in either order
        x, y = factbook(), factbook()
        accounts(x, period_start='2026-07-01'), accounts(x)
        accounts(y), accounts(y, period_start='2026-07-01')
        self.assertEqual(econ.kpi_table(x, 'broker'), econ.kpi_table(y, 'broker'))
        self.assertNotIn('period_start', econ.kpi_table(x, 'broker')['kpis']['accounts']['rows'][0])

    def test_rows_group_by_period_scope_and_basis_and_prefer_the_better_source(self):
        fb = self.ibkr()
        t = econ.kpi_table(fb, 'broker')
        self.assertEqual((t['model'], t['label_he'], t['as_of'], t['currency'], t['economics_version']), ('broker', 'ברוקר', '2026-10-21', 'USD', 1))
        self.assertEqual(t['have'], ['accounts', 'client_equity', 'net_new_accounts', 'margin_loans', 'margin_rate'])   # core first
        self.assertEqual(list(t['kpis']), t['have'])
        self.assertEqual(t['missing'], ['darts', 'rate_sensitivity_25bp'])
        self.assertEqual((t['required_missing'], t['unstated']), ([], ['net_new_accounts']))
        acc = t['kpis']['accounts']
        self.assertEqual((acc['unit'], acc['bases'], acc['tag'], acc['core'], acc['period_ends']), ('count', ['balance'], 'structural', True, ['2026-06-30', '2026-09-30']))
        rows = acc['rows']
        self.assertEqual([(r['period_end'], r['scope'], r['kpi_basis']) for r in rows],
                         [('2026-06-30', 'total', 'balance'), ('2026-09-30', 'pro', 'balance'), ('2026-09-30', 'total', 'average'), ('2026-09-30', 'total', 'balance')])
        q3 = rows[-1]
        self.assertEqual((q3['value'], q3['source_id'], q3['source_kind'], q3['alternates'], q3['basis_declared']), (ACCOUNTS, 'rel', 'issuer_document', 1, True))
        self.assertNotIn('alternate_values', q3)                            # the beaten post figure equals the release figure
        self.assertEqual((rows[2]['basis_declared'], rows[2]['alternates']), (False, 0))                    # average is not a declared basis of accounts
        self.assertEqual((rows[0]['value'], rows[0]['source_id'], rows[0]['available_at'], rows[0]['alternates']), (ACCOUNTS_Q2, 'rel_q2', Q2_RELEASE_DAY, 0))
        self.assertEqual(set(q3), {'period', 'period_end', 'value', 'unit', 'kpi_basis', 'scope', 'currency_basis', 'basis_declared',
                                   'source_id', 'source_kind', 'available_at', 'available_at_estimated', 'alternates'})
        ml = t['kpis']['margin_loans']['rows']
        self.assertEqual([(r['kpi_basis'], r['value'], r['basis_declared'], r['unit']) for r in ml],
                         [('average', MARGIN_LOANS_AVG, True, 'USD'), ('balance', MARGIN_LOANS_END, True, 'USD')])
        nna = t['kpis']['net_new_accounts']['rows'][0]
        self.assertEqual((nna['kpi_basis'], nna['scope'], nna['currency_basis'], nna['basis_declared'], nna['unit']), (None, None, None, None, 'USD'))
        self.assertEqual(t['kpis']['margin_rate']['rows'][0]['unit'], 'rate')
        self.assertEqual(t['boundary'], econ.TABLE_BOUNDARY)
        self.assertEqual(t['sha256'], F.digest({'model': 'broker', 'as_of': '2026-10-21', 'economics_version': 1, 'kpis': t['kpis']}))
        # after the 10-Q is usable the filing-derived figure leads the Q3 total/balance group
        fb['as_of'] = '2026-11-10'                                 # the same facts in a FactBook built at a later cutoff
        later = econ.kpi_table(fb, 'broker')
        self.assertEqual(later, econ.kpi_table(fb, 'broker', '2026-11-10'))
        self.assertEqual(econ.kpi_table(fb, 'broker', '2026-10-21'), t)   # reading it back at the earlier date gives the earlier table
        q3 = [r for r in later['kpis']['accounts']['rows'] if r['scope'] == 'total' and r['kpi_basis'] == 'balance' and r['period_end'] == '2026-09-30'][0]
        self.assertEqual((q3['value'], q3['source_id'], q3['source_kind'], q3['alternates']), (FILED_PROBE, '10q', 'filing_derived', 2))
        self.assertEqual(q3['alternate_values'], [ACCOUNTS])                 # post and release agree with each other, not with the filing

    def test_values_in_different_units_are_separate_rows_never_alternates(self):
        fb = factbook(ticker='SUBS')
        F.add_kpi(fb, 'arpu', *Q3, 1.0, 'synthetic', POST_DAY, kpi_basis='average')                # SYNTHETIC: USD_per_user
        F.add_kpi(fb, 'arpu', *Q3, 2.0, 'synthetic', POST_DAY, kpi_basis='average', unit='EUR')    # SYNTHETIC: EUR_per_user
        rows = econ.kpi_table(fb, 'subscriber_network')['kpis']['arpu']['rows']
        self.assertEqual([(r['unit'], r['value'], r['alternates']) for r in rows], [('EUR_per_user', 2.0, 0), ('USD_per_user', 1.0, 0)])

    def test_as_of_view_never_widens_the_cutoff_and_the_table_is_read_only(self):
        fb = self.ibkr()
        before = F.fingerprint(fb)
        early = econ.kpi_table(fb, 'broker', '2026-10-16')
        self.assertEqual((early['as_of'], early['have'], early['required_missing']), ('2026-10-16', ['accounts'], ['client_equity']))
        self.assertEqual([r['period_end'] for r in early['kpis']['accounts']['rows']], ['2026-06-30'])   # only the Q2 release is public
        late_july = econ.kpi_table(fb, 'broker', '2026-07-21')
        self.assertEqual((late_july['kpis'], late_july['required_missing']), ({}, ['accounts', 'client_equity']))
        self.assertEqual(late_july['missing'], econ.kpi_ids('broker'))
        self.assertEqual(fb['as_of'], '2026-10-21')
        self.assertEqual(econ.kpi_table(fb, 'broker', '2026-10-21'), econ.kpi_table(fb, 'broker'))
        with self.assertRaises(ValueError):
            econ.kpi_table(fb, 'broker', '2026-10-22')
        with self.assertRaises(ValueError):
            econ.kpi_table(fb, 'bank')
        self.assertEqual(F.fingerprint(fb), before)
        other = econ.kpi_table(fb, 'marketplace')                 # a model none of these KPIs belong to
        self.assertEqual((other['kpis'], other['missing']), ({}, econ.kpi_ids('marketplace')))
        empty = F.new({'ticker': 'NEW', 'currency': 'USD'}, '2026-10-21')
        self.assertEqual(econ.kpi_table(empty, 'memory_cycle')['required_missing'], ['bits', 'asp'])


class NoInputInvarianceTests(unittest.TestCase):
    def test_facts_loads_the_econ_registry_only_when_a_kpi_is_added(self):
        code = ('import sys\n'
                'sys.path.insert(0, sys.argv[1])\n'
                'from fundamental_engine import facts as F\n'
                'a = "fundamental_engine.econ" in sys.modules\n'
                'fb = F.new({"ticker": "T", "currency": "USD"}, "2026-10-21")\n'
                'F.add_source(fb, "rel", "issuer", "issuer_release", "issuer_document", "2026-10-21")\n'
                'F.add_fact(fb, "revenue", "FY2025", "2025-12-31", 1.0, "rel", "as_reported", "2026-02-20")\n'
                'F.candidates(fb, "revenue")\n'
                'b = "fundamental_engine.econ" in sys.modules\n'
                'F.add_kpi(fb, "accounts", "Q@2026-09-30", "2026-09-30", 1.0, "rel", "2026-10-17", kpi_basis="balance")\n'
                'print(a, b, "fundamental_engine.econ" in sys.modules)')
        out = subprocess.run([sys.executable, '-I', '-c', code, str(ROOT)], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ['False', 'False', 'True'])

    def test_candidates_order_is_rank_order(self):
        """rank() is the key candidates() always sorted by: the ADBE fixture orders exactly as before."""
        with open(ROOT / 'tests' / 'fixtures' / 'adbe_factbook_2026-10-19.json', encoding='utf-8') as f:
            fb = json.load(f)
        for metric in sorted({f['metric'] for f in fb['facts']}):
            c = F.candidates(fb, metric)
            self.assertEqual(c, sorted(c, key=lambda f: F.rank(fb, f)), metric)
            self.assertTrue(all(F.usable(fb, f) for f in c), metric)
        fb = factbook()
        accounts(fb, 'post'), accounts(fb, 'rel'), accounts(fb, '10q', available='2026-10-20')
        self.assertEqual([f['source_id'] for f in F.candidates(fb, 'accounts')], ['10q', 'rel', 'post'])
        self.assertEqual(F.rank(fb, F.get(fb, 'accounts')), (False, 0, False, False, '2026-10-20'))


if __name__ == '__main__':
    unittest.main()
