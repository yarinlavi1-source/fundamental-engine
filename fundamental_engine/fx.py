"""Dated exchange rates for issuers that report in another currency (FRED daily H.10 series, saved as published).

rate(ccy, as_of, folder) -> {'value': units of ccy per 1 USD, 'date', 'series', 'source'} for the latest observation on or
before as_of (at most 7 days earlier: H.10 skips holidays), or None. Series quoted as USD per unit (EUR, GBP, AUD, NZD)
are inverted. A rate is a market fact on its date; converting a forecast at today's rate assumes no currency move, which
the report states.
"""
import os

FRED = {  # currency -> (series, quoted as 'ccy_per_usd' or 'usd_per_ccy')
    'EUR': ('DEXUSEU', 'usd_per_ccy'), 'GBP': ('DEXUSUK', 'usd_per_ccy'), 'AUD': ('DEXUSAL', 'usd_per_ccy'),
    'NZD': ('DEXUSNZ', 'usd_per_ccy'), 'JPY': ('DEXJPUS', 'ccy_per_usd'), 'CNY': ('DEXCHUS', 'ccy_per_usd'),
    'TWD': ('DEXTAUS', 'ccy_per_usd'), 'KRW': ('DEXKOUS', 'ccy_per_usd'), 'INR': ('DEXINUS', 'ccy_per_usd'),
    'DKK': ('DEXDNUS', 'ccy_per_usd'), 'SEK': ('DEXSDUS', 'ccy_per_usd'), 'NOK': ('DEXNOUS', 'ccy_per_usd'),
    'CHF': ('DEXSZUS', 'ccy_per_usd'), 'CAD': ('DEXCAUS', 'ccy_per_usd'), 'MXN': ('DEXMXUS', 'ccy_per_usd'),
    'BRL': ('DEXBZUS', 'ccy_per_usd'), 'HKD': ('DEXHKUS', 'ccy_per_usd'), 'SGD': ('DEXSIUS', 'ccy_per_usd'),
    'ZAR': ('DEXSFUS', 'ccy_per_usd'), 'THB': ('DEXTHUS', 'ccy_per_usd'),
}
URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'


def series(path):
    """FRED csv (observation_date,<id>) -> sorted [(date, value)] without missing observations."""
    out = []
    with open(path, encoding='utf-8') as f:
        next(f)
        for line in f:
            d, v = (line.strip().split(',') + [''])[:2]
            if v and v != '.':
                out.append((d, float(v)))
    return sorted(out)


def rate(ccy, as_of, folder):
    if ccy == 'USD':
        return {'value': 1., 'date': as_of, 'series': None, 'source': 'identity'}
    if ccy not in FRED:
        return None
    sid, quote = FRED[ccy]
    p = os.path.join(folder, f'{sid}.csv')
    if not os.path.exists(p):
        return None
    pts = [x for x in series(p) if x[0] <= as_of]
    if not pts:
        return None
    d, v = pts[-1]
    from datetime import date
    if (date.fromisoformat(as_of) - date.fromisoformat(d)).days > 7:
        return None
    return {'value': v if quote == 'ccy_per_usd' else 1 / v, 'date': d, 'series': sid, 'source': URL.format(sid),
            'quote': f'{ccy} per USD' + (' (inverted from USD per unit)' if quote == 'usd_per_ccy' else '')}
