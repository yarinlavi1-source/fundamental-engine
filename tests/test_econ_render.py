"""WP8 of the econ layer: the owner's Hebrew text (render_he), estimates against price (price_vs_estimates) and the
standalone tool (econ.econ_review, MCP econ_review, CLI econ-review).

The numbers are the analyst posts' figures in tests/fixtures/econ (arithmetic fixtures, not truth); the Hebrew texts are
the design's section 4, generated from those fixtures. Names the review shows in English (a segment, a scope, a category,
a ticker) reach the text in Hebrew only through names_he, a display label the caller supplies (NAMES below). The RDDT
revisions are the design's figures (fixture _provenance: the AV snapshot dates and the second close were not recorded and
must be pinned before the reading is called evidence); the second close here is SYNTHETIC, the first close x (1 - 21%).
Every test here fails without WP8 (no render_he, price_vs_estimates or econ_review; no econ_review tool or command).
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import econ
from fundamental_engine import quarter as Q
from fundamental_engine.mcp import Server, TOOLS

from test_econ import MODELS, case, fixture, numbers, run

ROOT = Path(__file__).resolve().parents[1]
# display labels for the names the fixtures carry in English (supplied by the caller, never part of the review)
NAMES = {'Mobility': 'תחבורה', 'us_commercial': 'המסחריים בארה״ב', 'Cleaning': 'השואבים',
         'SuperIntelligence': 'הבינה המלאכותית', 'NVDA': 'Nvidia', 'us': 'בארה״ב'}
# design section 4, word for word
SECTION_4 = {
    ('ibkr_2026q3', 'how_it_earns'): '5.6 מיליון חשבונות (+35% בשנה, צמיחה מבנית) × כ־1,340$ הכנסה לחשבון בשנה (−14%: כ־6% כי '
                                     'החשבונות החדשים קטנים יותר, וכ־9% כי הריבית והמסחר ירדו, מחזורי)',
    ('uber_2025q3', 'how_it_earns'): 'יותר משתמשים (+17%) × יותר נסיעות לכל משתמש (+4%); מכל 100$ של הזמנות בתחבורה נשארים כ־8$ '
                                     'רווח תפעולי',
    ('pltr_2026q2', 'how_it_earns'): 'לקוחות קיימים קונים יותר: מספר הלקוחות המסחריים בארה״ב +6% ברבעון, וההכנסה מכל לקוח +21%',
    ('sn_2025', 'how_it_earns'): 'ממציאה מחדש קטגוריות ותיקות: השואבים ירדו בכ־8% וחזרו לשיא חדש (+13%), וההוצאה על פיתוח נשארה '
                                 'כ־6% מהמכירות',
    ('sofi_2026q2', 'how_it_earns'): '15.8 מיליון לקוחות × 1.5 מוצרים ללקוח (ספירה מצטברת); הפיקדונות זולים בכ־710 מיליון$ בשנה '
                                     'ממימון אחר',
    ('spcx_2026q3', 'how_it_earns'): 'עוד לא ברור איך מגזר הבינה המלאכותית מרוויח (52% מההכנסות): חסרים מחיר החוזים, משך החוזים '
                                     'ואיכות הלקוחות',
    ('sofi_2026q2', 'price_requires_units'): 'במונחי הבנק: תשואה של כ־17% על ההון המוחשי, מול כ־8.5% היום. גם החזרה של כל ההון '
                                             'העודף מביאה רק לכ־10.5%–13%; השאר צריך לבוא מרווח גבוה יותר',
    ('nvda_2026q2', 'risk_fallback'): 'החברה מממנת חלק מהקונים שלה: השקעות, התחייבויות ורכישת קיבולת מהלקוחות בהיקף של כ־42% '
                                      'מהמכירות השנתיות, ועוד ערבות של עד 105 מיליארד$. במחצית הראשונה נכנס מזומן מהפעילות רק '
                                      'כ־63% מהרווח',
    ('skhy_2026q2', 'risk_fallback'): 'חלק מהביקוש תלוי בשרשרת מימון (Nvidia מממנת לקוחות שקונים זיכרון); כמה מההכנסות תלוי בה '
                                      'עוד לא ידוע',
}
NOTE = 'מבני = נבנה לאורך זמן ולא נעלם בשנה רעה; מחזורי = עולה ויורד עם השוק והריבית.'


def he(name, **kw):
    return econ.render_he(run(name), names_he=kw.pop('names_he', NAMES), **kw)


def revision(value, as_of, **kw):
    row = {'metric': 'eps', 'target_period': 'FY2027', 'basis': 'consensus_mean', 'currency': 'USD', 'unit': 'USD_per_share',
           'value': value, 'as_of': as_of, 'source_ids': ['av_earnings_estimates_rddt']}
    row.update(kw)
    return row


def rddt_inputs():
    """The RDDT fixture's revisions block as growth.momentum rows, and the two closes (the second SYNTHETIC: -21%)."""
    d = fixture('rddt_2026q2')
    rv, px = d['revisions'], d['prices']
    revs = [revision(rv[k]['value'], rv[k]['as_of'], source_ids=rv[k]['source_ids']) for k in ('from', 'to')]
    prices = [{'date': px['from']['date'], 'value': px['from']['value'], 'source_ids': px['from']['source_ids']},
              {'date': px['to']['date'], 'value': px['from']['value'] * (1 + px['change']), 'source_ids': px['change_source_ids']}]
    return revs, prices


class HebrewTextTests(unittest.TestCase):
    def test_ibkr_how_it_earns_names_both_parts_and_fits_a_row(self):
        text = he('ibkr_2026q3')['how_it_earns']
        for s in ('1,340', '35%', 'מבני', 'מחזורי'):
            self.assertIn(s, text)
        self.assertLessEqual(len(text), 220)
        self.assertLessEqual(len(text), econ.HE_MAX)
        self.assertEqual(he('ibkr_2026q3')['note'], NOTE)          # the text says מבני / מחזורי, so the note line comes

    def test_section_4_texts_word_for_word(self):
        for (name, field), text in SECTION_4.items():
            self.assertEqual(he(name)[field], text, (name, field))

    def test_shape_four_fields_and_the_note_only_beside_tag_words(self):
        for name in MODELS:
            out = he(name)
            self.assertEqual(list(out), ['how_it_earns', 'price_requires_units', 'risk_fallback', 'note'])
            used = any(w in (out[k] or '') for k in ('how_it_earns', 'price_requires_units', 'risk_fallback') for w in ('מבני', 'מחזורי'))
            self.assertEqual(out['note'], NOTE if used else None, name)
        self.assertIsNone(he('uber_2025q3')['note'])
        self.assertEqual(econ.registry()['text_he']['note'], NOTE)

    def test_no_econ_input_every_field_is_null(self):
        none = dict.fromkeys(econ.HE_FIELDS)
        self.assertEqual(econ.render_he(None), none)
        self.assertEqual(econ.render_he(econ.review(case('ibkr_2026q3'))), none)              # no model: not assessed
        self.assertEqual(econ.render_he(econ.review(case('ibkr_2026q3'), 'unclassified')), none)
        out = econ.econ_review(case('ibkr_2026q3'))
        self.assertEqual((out['review']['status'], out['text_he'], out['price_vs_estimates']), ('not_assessed', none, None))
        with self.assertRaises(ValueError):
            econ.render_he({'status': 'assessed'})                                            # not an econ review

    def test_how_it_earns_fits_a_table_row_on_every_fixture(self):
        for name in MODELS:
            for names in (NAMES, None):
                text = he(name, names_he=names)['how_it_earns']
                self.assertTrue(text, name)
                self.assertLessEqual(len(text), econ.HE_MAX, (name, text))

    def test_every_number_in_the_text_is_a_rounded_figure_of_the_review(self):
        """Anti-invention: each number printed is a rounding of a number in the review (the per-100 template constant
        aside); a planted constant fails."""
        def forms(v):
            out = set()
            for x in (v, v * 100, v / 1e3, v / 1e6, v / 1e9):
                if x:
                    out |= {econ._sig(abs(x), n) for n in (1, 2, 3)}
                    out |= {f'{round(abs(x), 1):.1f}'.removesuffix('.0'), str(econ._half_up(abs(x)))}
            return out
        allowed = {'100'}                        # 'מכל 100$ של הזמנות': the template's unit of bookings
        for name in MODELS:
            rv = run(name)
            seen = set().union(*(forms(v) for v in numbers(rv)))
            for field, text in econ.render_he(rv, names_he=NAMES).items():
                for n in re.findall(r'\d[\d,]*(?:\.\d+)?', text or ''):
                    self.assertIn(n, seen | allowed, (name, field, n))
        planted = deepcopy(run('ibkr_2026q3'))
        txt = econ.render_he(planted)['how_it_earns'].replace('1,340', '1,777')
        self.assertNotIn('1,777', set().union(*(forms(v) for v in numbers(planted))))
        self.assertIn('1,777', txt)

    def test_names_are_display_labels_outside_the_review(self):
        rv = run('spcx_2026q3')
        before = deepcopy(rv)
        plain = econ.render_he(rv)['how_it_earns']
        self.assertTrue(plain.startswith('עוד לא ברור איך מגזר SuperIntelligence מרוויח (52% מההכנסות)'))
        self.assertEqual(rv, before)                                              # rendering never changes the review
        self.assertEqual(he('pltr_2026q2', names_he=None)['how_it_earns'],
                         'לקוחות קיימים קונים יותר: מספר הלקוחות us commercial +6% ברבעון, וההכנסה מכל לקוח +21%')
        self.assertIn('ב־Mobility', he('uber_2025q3', names_he=None)['how_it_earns'])
        self.assertIn('(NVDA מממנת', he('skhy_2026q2', names_he=None)['risk_fallback'])
        self.assertEqual(econ.render_he(rv, names_he=NAMES), econ.render_he(rv, names_he=NAMES))   # deterministic
        for bad in ([('a', 'b')], {'Mobility': ''}, {'Mobility': 3}):
            with self.assertRaises(ValueError):
                econ.render_he(rv, names_he=bad)

    def test_templates_follow_the_direction_of_each_part(self):
        # IBKR Q2: per-account revenue -4.5% = equity per account +4.5% (mix) x revenue yield -8.6% (cyclical): signed parts
        self.assertEqual(he('ibkr_2026q2')['how_it_earns'],
                         '5.2 מיליון חשבונות (+34% בשנה, צמיחה מבנית) × כ־1,460$ הכנסה לחשבון בשנה (−4%: +4% כי ההון הממוצע '
                         'לחשבון עלה, ו־−9% כי הריבית והמסחר ירדו, מחזורי)')
        # the generic template: a factor the identity misses is marked, a known one carries its change and tag
        self.assertEqual(he('skhy_2026q2')['how_it_earns'], 'ביטים (חסר) × מחיר ממוצע (+30% בשנה, מחזורי)')
        self.assertEqual(he('rddt_2026q2')['how_it_earns'], 'בארה״ב: משתמשים יומיים (+6% בשנה, מבני) × הכנסה למשתמש (מחזורי)')
        self.assertEqual(he('nvda_2026q2')['how_it_earns'], 'עוד לא ברור איך העסק מרוויח: חסרים ג׳יגה־וואט שנשלחו והכנסה לג׳יגה־וואט')
        self.assertEqual(he('uber_2026q2')['how_it_earns'], 'עוד לא ברור איך העסק מרוויח: חסרים משתמשים פעילים בחודש ונסיעות לכל משתמש')

    def test_price_extension_only_for_the_bank_case_phase_a_scope(self):
        for name in MODELS:
            if name != 'sofi_2026q2':
                self.assertIsNone(he(name)['price_requires_units'], name)
        # SYNTHETIC P/TBV values: the capital lever reaches the price-implied ROTE at the top end, or at both ends
        for ptbv, tail in ((1.2, '; רק הקצה העליון מגיע למה שהמחיר מניח'), (1.05, ', יותר ממה שהמחיר מניח')):
            c = case('sofi_2026q2')
            c['bank']['price_to_tangible_book'] = ptbv
            text = econ.render_he(econ.review(c, 'digital_bank'))['price_requires_units']
            self.assertTrue(text.endswith('מביאה עד כ־10.5%–13% (גבול עליון)' + tail), text)
        with self.assertRaises(ValueError) as cm:                     # translate_requirement is WP10 (phase B)
            econ.render_he(run('ibkr_2026q3'), {'units_required': 0.173})
        self.assertIn('WP10', str(cm.exception))

    def test_risk_fallback_order_funding_chain_then_a_high_flag(self):
        self.assertTrue(he('nvda_2026q2')['risk_fallback'].startswith('החברה מממנת חלק מהקונים שלה'))
        self.assertTrue(he('skhy_2026q2')['risk_fallback'].startswith('חלק מהביקוש תלוי בשרשרת מימון'))
        self.assertEqual(he('sofi_2026q2')['risk_fallback'], Q.ECON_FLAG_HE['nco_basis'])     # the first high flag
        # an explanation gap is not a business risk: segment_unexplained (high) alone gives no fallback
        self.assertEqual([f['key'] for f in run('uber_2025q3')['flags'] if f['severity'] == 'high'], ['segment_unexplained'])
        self.assertIsNone(he('uber_2025q3')['risk_fallback'])
        self.assertIsNone(he('ibkr_2026q3')['risk_fallback'])
        # a quantified chain (the fixture's SYNTHETIC 30% share x the NVDA analyst estimate 25%, the review linked) names
        # the share and says it is an estimate, not a reported figure
        c = case('skhy_2026q2')
        c['chain']['share_of_revenue_via_chain'] = {'value': 0.3, 'source_ids': ['test_assumption_chain_share']}
        rv = econ.review(c, 'memory_cycle', upstream=run('nvda_2026q2'))
        self.assertAlmostEqual(rv['overlays']['funded_customer_chain']['exposure']['value'], 0.075, places=12)
        self.assertTrue(econ.render_he(rv, names_he=NAMES)['risk_fallback'].endswith(
            '; כ־8% מההכנסות תלויות בה (הערכה, לא נתון מדווח)'))

    def test_registry_has_a_hebrew_word_for_every_kpi_and_factor(self):
        reg = econ.registry()
        words = reg['text_he']['words']
        for mid, m in reg['models'].items():
            for k in list(m['kpis']) + [f for f in m['growth_split'].get('factors') or []]:
                self.assertTrue(words.get(k), (mid, k))
        bad = deepcopy(reg)
        del bad['text_he']['words']['accounts']
        self.assertTrue(any('text_he.words' in p and 'accounts' in p for p in econ.problems(bad)))
        bad = deepcopy(reg)
        bad['text_he']['products']['vibes'] = 'x'
        self.assertTrue(any("'vibes'" in p for p in econ.problems(bad)))


class EstimatesVsPriceTests(unittest.TestCase):
    def test_rddt_estimates_rose_while_the_multiple_fell(self):
        revs, prices = rddt_inputs()
        r = econ.price_vs_estimates(revs, prices, as_of='2026-10-21')
        exp = fixture('rddt_2026q2')['expected']
        self.assertAlmostEqual(r['estimate_change']['value'], 6.98 / 6.52 - 1, places=12)
        self.assertEqual(round(r['estimate_change']['value'] * 100, 2), 7.06)
        self.assertAlmostEqual(r['price_change']['value'], -0.21, places=12)
        self.assertAlmostEqual(r['implied_forward_multiple_change']['value'], 0.79 / (6.98 / 6.52) - 1, places=12)
        self.assertEqual(round(r['implied_forward_multiple_change']['value'] * 100, 1), -26.2)
        self.assertEqual(r['reading'], 'multiple_moved_more')
        self.assertEqual(r['boundary'], 'what moved; not a signal; not backtested.')
        self.assertEqual(r['text_he'], exp['render_he']['estimates_vs_price'])
        self.assertEqual(r['text_he'], 'התחזיות לרווח עלו כ־7%, אבל המחיר ירד 21%: השוק משלם היום כ־26% פחות על כל דולר של רווח צפוי.')
        self.assertEqual((r['from']['date'], r['to']['date'], r['revisions_between']), ('2026-08-26', '2026-10-21', 0))
        self.assertEqual(r, econ.price_vs_estimates(list(reversed(revs)), list(reversed(prices)), as_of='2026-10-21'))

    def test_same_direction_estimate_moved_more(self):
        # SYNTHETIC: the estimate +20% and the price +10%: the multiple fell ~8%, less than the estimate moved
        revs = [revision(5.0, '2026-01-02'), revision(5.5, '2026-03-01'), revision(6.0, '2026-06-01')]
        prices = [{'date': '2026-01-02', 'value': 100.0, 'source_ids': ['ib']}, {'date': '2026-06-01', 'value': 110.0, 'source_ids': ['ib']}]
        r = econ.price_vs_estimates(revs, prices)
        self.assertAlmostEqual(r['implied_forward_multiple_change']['value'], 1.1 / 1.2 - 1, places=12)
        self.assertEqual((r['reading'], r['revisions_between']), ('estimate_moved_more', 1))
        self.assertEqual(r['text_he'], 'התחזיות לרווח עלו 20%, והמחיר עלה 10%: השוק משלם היום כ־8% פחות על כל דולר של רווח צפוי.')

    def test_mismatched_definitions_and_unsourced_or_late_inputs_raise(self):
        revs, prices = rddt_inputs()
        bad_revisions = [
            [revs[0], dict(revs[1], target_period='FY2028')],                # mismatched target periods
            [revs[0], dict(revs[1], basis='consensus_median')],
            [revs[0], dict(revs[1], currency='EUR')],
            [revs[0], dict(revs[1], metric='revenue')],
            [revs[0], dict(revs[1], source_ids=[])],
            [revs[0], dict(revs[1], value=-1.0)],
            [revs[0], dict(revs[1], as_of='2026-10-22')],                    # after the case date
            [revs[0], {k: v for k, v in revs[1].items() if k != 'unit'}],
            [revs[0], dict(revs[1], period_end='2027-12-31')],               # two names for the target period that differ
            [revs[0], dict(revs[1], as_of=revs[0]['as_of'])],
            [revs[0]],
        ]
        for bad in bad_revisions:
            with self.assertRaises(ValueError):
                econ.price_vs_estimates(bad, prices, as_of='2026-10-21')
        with self.assertRaises(ValueError) as cm:
            econ.price_vs_estimates([revs[0], dict(revs[1], target_period='FY2028')], prices)
        self.assertIn('target period', str(cm.exception))
        for bad in ([prices[0]], [prices[0], dict(prices[1], source_ids=None)], [prices[0], dict(prices[1], currency='EUR')],
                    prices + [dict(prices[1], value=1.0)], [prices[0], dict(prices[1], date='2026-10-20')], None):
            with self.assertRaises(ValueError):
                econ.price_vs_estimates(revs, bad, as_of='2026-10-21')
        momentum_rows = [{k: v for k, v in r.items() if k != 'target_period'} | {'period_end': 'FY2027'} for r in revs]
        self.assertEqual(econ.price_vs_estimates(momentum_rows, prices)['target_period'], 'FY2027')   # growth.momentum's rows


class ToolTests(unittest.TestCase):
    def test_tool_selects_by_economics_reviews_and_renders(self):
        out = econ.econ_review(case('ibkr_2026q3'), {'card_type': 'financials', 'profile_text': 'brokerage and capital markets'})
        self.assertEqual((out['selection']['primary'], out['selection']['source'], out['review']['status']), ('broker', 'rule', 'assessed'))
        self.assertEqual(out['text_he']['how_it_earns'], SECTION_4[('ibkr_2026q3', 'how_it_earns')])
        self.assertEqual(out['review'], econ.review(case('ibkr_2026q3'), out['selection']))
        lines = out['text'].split('\n')
        self.assertEqual(lines[0], 'איך העסק מרוויח: ' + SECTION_4[('ibkr_2026q3', 'how_it_earns')])
        self.assertEqual(lines[1:], [NOTE, econ.registry()['text_he']['boundary']])
        self.assertEqual(out, econ.econ_review(case('ibkr_2026q3'), {'card_type': 'financials', 'profile_text': 'brokerage and capital markets'}))

    def test_tool_options_model_names_prior_and_estimates(self):
        revs, prices = rddt_inputs()
        out = econ.econ_review(case('rddt_2026q2'), {'model': 'ads_platform', 'names_he': NAMES, 'revisions': revs, 'prices': prices})
        self.assertIsNone(out['selection'])
        self.assertEqual(out['price_vs_estimates']['as_of'], '2026-10-21')
        self.assertIn(out['price_vs_estimates']['text_he'], out['text'].split('\n'))
        prior = run('ibkr_2026q2')
        out = econ.econ_review(case('ibkr_2026q3'), {'model': 'broker', 'prior': prior})
        self.assertEqual(out['review']['prior_review']['input_sha256'], prior['input_sha256'])
        for bad in ({'colour': 1}, {'revisions': revs}, {'prices': prices}, [1]):
            with self.assertRaises(ValueError):
                econ.econ_review(case('rddt_2026q2'), bad)
        late = deepcopy(revs)
        late[1]['as_of'] = '2026-10-22'
        with self.assertRaises(ValueError):          # the revisions are read as of the case date
            econ.econ_review(case('rddt_2026q2'), {'model': 'ads_platform', 'revisions': late, 'prices': prices})

    def test_mcp_tool(self):
        self.assertIn('econ_review', [t['name'] for t in TOOLS])
        srv = Server(':memory:')
        srv.initialized = True
        call = lambda args, i=1: srv.handle({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                                             'params': {'name': 'econ_review', 'arguments': args}})['result']
        res = call({'case': case('sofi_2026q2'), 'options': {'model': 'digital_bank'}})
        self.assertFalse(res['isError'])
        body = json.loads(res['content'][0]['text'])
        self.assertEqual(body['text_he']['price_requires_units'], SECTION_4[('sofi_2026q2', 'price_requires_units')])
        self.assertTrue(call({'case': case('sofi_2026q2'), 'options': {'colour': 1}}, 2)['isError'])
        self.assertTrue(call({'options': {}}, 3)['isError'])

    def test_cli_prints_the_hebrew_text_or_the_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ibkr.json'
            p.write_text(json.dumps(case('ibkr_2026q3')), encoding='utf-8')
            cmd = [sys.executable, '-m', 'fundamental_engine.cli', 'econ-review', str(p), '--options', '{"model": "broker"}']
            text = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', cwd=ROOT, check=True).stdout
            self.assertEqual(text.splitlines()[0], 'איך העסק מרוויח: ' + SECTION_4[('ibkr_2026q3', 'how_it_earns')])
            body = json.loads(subprocess.run(cmd + ['--json'], capture_output=True, text=True, encoding='utf-8', cwd=ROOT,
                                             check=True).stdout)
            self.assertEqual(body['review']['input_sha256'], run('ibkr_2026q3')['input_sha256'])

    def test_only_the_tool_entry_points_call_it_and_the_pipeline_never_does(self):
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'econ.py' and re.search(
            r'\becon_review\b|\b(?:econ|E)\.(?:render_he|price_vs_estimates)\b', p.read_text(encoding='utf-8')))
        self.assertEqual(callers, ['cli.py', 'mcp.py'])
        self.assertIsNone(re.search(r'from \.econ import|from \. import [\w, ]*\becon\b|\becon\.', (pkg / 'pipeline.py').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
