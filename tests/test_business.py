"""business.py: business map from inline XBRL. The document below is SYNTHETIC (invented company and numbers) and shaped
like a real 10-K: contexts with dimensions, scaled facts, a cover with two share classes and an Item 1/1A/7 text body."""
import unittest

from fundamental_engine import business as B


def ctx(cid, start=None, end=None, instant=None, dims=None):
    seg = ''.join(f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>' for a, m in (dims or {}).items())
    per = f'<xbrli:instant>{instant}</xbrli:instant>' if instant else f'<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>'
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>'
            + (f'<xbrli:segment>{seg}</xbrli:segment>' if seg else '') + f'</xbrli:entity><xbrli:period>{per}</xbrli:period></xbrli:context>')


def fact(name, cid, val, scale=6, sign=None):
    neg = ' sign="-"' if sign else ''
    return (f'<ix:nonFraction name="{name}" contextRef="{cid}" unitRef="U_USD" scale="{scale}" decimals="-6" '
            f'format="ixt:num-dot-decimal"{neg}>{val}</ix:nonFraction>')


SEG = 'us-gaap:StatementBusinessSegmentsAxis'
PROD = 'srt:ProductOrServiceAxis'
YEARS = (('2023-01-01', '2023-12-31'), ('2024-01-01', '2024-12-31'), ('2025-01-01', '2025-12-31'))
CLOUD, DEVICES = (100, 150, 225), (300, 300, 297)            # SYNTHETIC: a fast part and a flat part


def doc():
    c, f = [], []
    for i, (s, e) in enumerate(YEARS):
        c += [ctx(f'T{i}', s, e), ctx(f'C{i}', s, e, dims={SEG: 'syn:CloudMember'}), ctx(f'D{i}', s, e, dims={SEG: 'syn:DevicesMember'}),
              ctx(f'P{i}', s, e, dims={PROD: 'us-gaap:ProductMember'}), ctx(f'S{i}', s, e, dims={PROD: 'us-gaap:ServiceMember'}),
              ctx(f'K{i}', s, e, dims={'us-gaap:ConcentrationRiskByBenchmarkAxis': 'us-gaap:RevenueFromContractWithCustomerProductAndServiceBenchmarkMember',
                                       'srt:MajorCustomersAxis': 'syn:CustomerAMember'})]
        rev = 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax'
        f += [fact(rev, f'T{i}', f'{CLOUD[i] + DEVICES[i]:,}'), fact(rev, f'C{i}', CLOUD[i]), fact(rev, f'D{i}', DEVICES[i]),
              fact(rev, f'P{i}', DEVICES[i]), fact(rev, f'S{i}', CLOUD[i]),
              fact('us-gaap:OperatingIncomeLoss', f'C{i}', int(CLOUD[i] * .3)), fact('us-gaap:OperatingIncomeLoss', f'D{i}', 6, sign=True),
              fact('us-gaap:ConcentrationRiskPercentage1', f'K{i}', '0.18', scale=0)]
    c += [ctx('R', instant='2025-12-31'), ctx('A', instant='2026-02-10', dims={'us-gaap:StatementClassOfStockAxis': 'us-gaap:CommonClassAMember'}),
          ctx('BB', instant='2026-02-10', dims={'us-gaap:StatementClassOfStockAxis': 'us-gaap:CommonClassBMember'})]
    f += [fact('us-gaap:RevenueRemainingPerformanceObligation', 'R', 410),
          fact('dei:EntityCommonStockSharesOutstanding', 'A', '90,000,000', scale=0),
          fact('dei:EntityCommonStockSharesOutstanding', 'BB', '10,000,000', scale=0)]
    body = ('<p>Table of contents Item 1. Business 3 Item 1A. Risk Factors 9</p>'
            '<p>ITEM 1. B USINESS</p><p>' + 'Synthetic Co sells cloud subscriptions and devices. ' * 80 + '</p>'
            '<p>ITEM 1A. RIS K FACTORS</p><p>' + 'Customers may switch to cheaper alternatives. ' * 80 + '</p>'
            '<p>ITEM 1B. UNRESOLVED STAFF COMMENTS</p><p>None.</p>'
            '<p>ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS</p><p>' + 'Cloud revenue grew on new customers. ' * 80 + '</p>'
            '<p>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES</p>')
    return ('<html><body><ix:header><ix:resources>' + ''.join(c) + '</ix:resources></ix:header>' + body + ''.join(f) + '</body></html>')


class BusinessMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bm = B.business_map(doc(), {'form': '10-K', 'filed': '2026-02-20'})

    def test_segments_reconcile_with_growth_share_and_margin(self):
        seg = self.bm['tables']['segment']
        self.assertEqual(seg['reconciliation']['status'], 'reconciles')
        d = seg['derived']
        self.assertAlmostEqual(d['Cloud']['cagr'], .5, places=2)                 # 100 -> 225 over two years
        self.assertAlmostEqual(d['Cloud']['share_of_total'], 225 / 522)
        self.assertAlmostEqual(d['Devices']['share_of_growth'], -3 / 122)
        self.assertAlmostEqual(seg['operating_margin']['Cloud']['2025-12-31'], int(225 * .3) / 225)
        self.assertLess(seg['operating_income']['Devices']['2025-12-31'], 0)     # sign="-" read

    def test_standard_members_form_a_separate_revenue_type_table(self):
        self.assertIn('revenue_type', self.bm['tables'])
        self.assertEqual(self.bm['tables']['revenue_type']['reconciliation']['status'], 'reconciles')
        self.assertNotIn('product', self.bm['tables'])

    def test_concentration_rpo_and_cover_classes(self):
        self.assertEqual(self.bm['customer_concentration'][0]['customer'], 'Customer A')
        self.assertEqual(self.bm['rpo'], {'value': 410e6, 'as_of': '2025-12-31', 'share_in_first_period': None})
        cs = self.bm['cover_shares']
        self.assertEqual((cs['as_of'], cs['total']), ('2026-02-10', 100e6))
        self.assertIn('Up-C', cs['note'])

    def test_not_disclosed_is_explicit(self):
        items = [x['item'] for x in self.bm['not_disclosed']]
        self.assertTrue(any('retention' in i for i in items))

    def test_segment_path_and_cross_check(self):
        sp = B.segment_path(self.bm)
        self.assertEqual(sp['basis'], 'segment')
        self.assertGreater(sp['end_mix']['Cloud'], 225 / 522)                   # the fast part gains weight
        s = B.summary(self.bm, base_growth=[.05] * 5)
        self.assertEqual(s['cross_check']['reading'], 'segment mix implies faster growth than the consolidated base path')
        self.assertEqual(B.summary(None)['status'], 'no annual report parsed')

    def test_sections_skip_the_table_of_contents(self):
        sec = B.sections(B.html_text(doc()))
        self.assertTrue(sec['item1_business'].startswith('ITEM 1. B USINESS'))
        self.assertIn('cheaper alternatives', sec['item1a_risk_factors'])
        self.assertNotIn('UNRESOLVED', sec['item1a_risk_factors'])
        self.assertIn('new customers', sec['item7_mdna'])


class ProFormaTests(unittest.TestCase):
    def test_pro_forma_revenue_per_acquisition(self):
        ax = 'us-gaap:BusinessAcquisitionAxis'
        html = (ctx('P0', '2025-01-01', '2025-06-30', dims={ax: 'syn:TargetCoMember'}) + ctx('R0', '2025-01-01', '2025-06-30')
                + ctx('X0', '2025-01-01', '2025-06-30', dims={ax: 'syn:TargetCoMember', SEG: 'syn:CloudMember'})
                + fact('us-gaap:BusinessAcquisitionsProFormaRevenue', 'P0', '10,472') + fact('us-gaap:Revenues', 'R0', '6,821')
                + fact('us-gaap:BusinessAcquisitionsProFormaRevenue', 'X0', '1,000'))          # a segment slice is not the total
        self.assertEqual(B.pro_forma_revenue(html), [{'start': '2025-01-01', 'end': '2025-06-30', 'value': 10472e6,
                                                      'acquisition': 'Target Co'}])


class ConvertibleTermsTests(unittest.TestCase):
    def test_terms_per_instrument_and_both_ratio_conventions(self):
        ax = 'us-gaap:DebtInstrumentAxis'
        html = (ctx('A1', '2026-01-01', '2026-06-30', dims={ax: 'syn:ConvertibleSeniorNotesDue2030Member'})
                + ctx('A2', instant='2026-06-30', dims={ax: 'syn:ConvertibleSeniorNotesDue2030Member'})
                + ctx('B1', '2023-02-10', '2023-02-10', dims={ax: 'syn:TwoPctNotesDue2029Member'})
                + ctx('B2', instant='2026-06-30', dims={ax: 'syn:TwoPctNotesDue2029Member'})
                + ctx('C2', instant='2026-06-30', dims={ax: 'syn:TermLoanMember'})
                + fact('us-gaap:DebtInstrumentConvertibleConversionRatio1', 'A1', '0.003425', scale=0)
                + fact('us-gaap:DebtInstrumentCarryingAmount', 'A2', '2,750')
                + fact('us-gaap:DebtInstrumentConvertibleConversionRatio1', 'B1', '27.3553', scale=0)
                + fact('us-gaap:DebtInstrumentCarryingAmount', 'B2', '241.5')
                + fact('us-gaap:DebtInstrumentCarryingAmount', 'C2', '500'))           # a term loan is not convertible
        t = B.convertible_terms(html)
        self.assertEqual(set(t), {'syn:ConvertibleSeniorNotesDue2030Member', 'syn:TwoPctNotesDue2029Member'})
        self.assertAlmostEqual(t['syn:ConvertibleSeniorNotesDue2030Member']['conversion_price'], 1 / .003425)
        self.assertAlmostEqual(t['syn:TwoPctNotesDue2029Member']['conversion_price'], 1000 / 27.3553)
        self.assertEqual(t['syn:TwoPctNotesDue2029Member']['maturity'], '2029-12-31')


if __name__ == '__main__':
    unittest.main()
