"""Regression: the report's segment second view when the consolidated base value is missing (NWS, 26.10.2026: the whole
`deep` chunk stopped on a None formatted as a number)."""
import unittest

from fundamental_engine import render


class SegmentViewWithoutConsolidatedBase(unittest.TestCase):
    def test_missing_consolidated_base_is_said_not_formatted(self):
        cf = {'business': {'status': 'parsed', 'source': {}, 'parts': {}},
              'segment_view': {'status': 'computed', 'base_value_segment_path': 12.5, 'base_value_consolidated': None,
                               'difference': None, 'quote': 10.0}}
        text = '\n'.join(render._business(cf))
        self.assertIn('12.50', text)
        self.assertIn('no consolidated base value', text)

    def test_both_values_still_shown(self):
        cf = {'business': {'status': 'parsed', 'source': {}, 'parts': {}},
              'segment_view': {'status': 'computed', 'base_value_segment_path': 12.5, 'base_value_consolidated': 10.0,
                               'difference': .25, 'quote': 9.0}}
        text = '\n'.join(render._business(cf))
        self.assertIn('12.50 against 10.00 on the consolidated path', text)


if __name__ == '__main__':
    unittest.main()
