"""Append-only forecast ledger: the engine's predictions, frozen before outcomes, scored after.

Weakness #7: picks and deep-research base cases lived in prose, so the engine could not measure
whether it improves. Every forecast is a hashed JSON line chained to the previous one; editing an
old line breaks the chain. Outcomes are appended as separate lines and may never pre-date the
forecast or the period they resolve.

Scores: revenue/margin/FCF forecast error, scenario calibration (did the realized value fall in
the bear..bull range at the stated frequency), and pick returns versus a stated benchmark.
"""
import hashlib, json, os
from datetime import date


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, 'w').close()

    def lines(self):
        with open(self.path, encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]

    def verify(self):
        prev = None
        for i, row in enumerate(self.lines()):
            body = {k: v for k, v in row.items() if k != 'hash'}
            if row.get('prev') != prev or row.get('hash') != _h(body):
                return {'ok': False, 'broken_at': i}
            prev = row['hash']
        return {'ok': True, 'entries': len(self.lines())}

    def _append(self, row):
        rows = self.lines()
        if not self.verify()['ok']:
            raise ValueError('ledger chain broken: refuse to append')
        row = dict(row, prev=rows[-1]['hash'] if rows else None)
        row['hash'] = _h(row)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def forecast(self, *, id, entity, made_at, metric, period_end, scenarios, price=None, benchmark=None,
                 model_version, inputs_hash=None, thesis_breakers=(), note=''):
        """scenarios: {'bear': v, 'base': v, 'bull': v} with optional 'prob' dict; values in the metric's unit."""
        if any(r.get('id') == id for r in self.lines()):
            raise ValueError('forecast id exists: new information = new id that references the old one')
        date.fromisoformat(made_at); date.fromisoformat(period_end)
        if not scenarios.get('bear') <= scenarios.get('base') <= scenarios.get('bull'):
            raise ValueError('scenario values must be ordered bear <= base <= bull')
        return self._append({'type': 'forecast', 'id': id, 'entity': entity, 'made_at': made_at, 'metric': metric,
                             'period_end': period_end, 'scenarios': scenarios, 'price': price, 'benchmark': benchmark,
                             'model_version': model_version, 'inputs_hash': inputs_hash, 'thesis_breakers': list(thesis_breakers), 'note': note})

    def outcome(self, *, forecast_id, recorded_at, actual, source_id, benchmark_return=None, price_now=None):
        f = next((r for r in self.lines() if r.get('type') == 'forecast' and r['id'] == forecast_id), None)
        if f is None:
            raise ValueError('unknown forecast id')
        rec = date.fromisoformat(recorded_at)
        if rec < date.fromisoformat(f['made_at']) or rec < date.fromisoformat(f['period_end']):
            raise ValueError('outcome cannot be recorded before the forecast or before the period ends')
        return self._append({'type': 'outcome', 'forecast_id': forecast_id, 'recorded_at': recorded_at, 'actual': actual,
                             'source_id': source_id, 'benchmark_return': benchmark_return, 'price_now': price_now})

    def score(self):
        rows = self.lines()
        fc = {r['id']: r for r in rows if r['type'] == 'forecast'}
        out, inside, n_rng = [], 0, 0
        for o in (r for r in rows if r['type'] == 'outcome'):
            f = fc[o['forecast_id']]; s = f['scenarios']; a = o['actual']
            err = (a - s['base']) / abs(s['base']) if s['base'] else None
            within = s['bear'] <= a <= s['bull']
            inside += within; n_rng += 1
            item = {'id': f['id'], 'entity': f['entity'], 'metric': f['metric'], 'base': s['base'], 'actual': a,
                    'base_error': err, 'inside_bear_bull': within}
            if f.get('price') and o.get('price_now'):
                item['stock_return'] = o['price_now'] / f['price'] - 1
                if o.get('benchmark_return') is not None:
                    item['excess_return'] = item['stock_return'] - o['benchmark_return']
            out.append(item)
        errs = [abs(x['base_error']) for x in out if x['base_error'] is not None]
        ex = [x['excess_return'] for x in out if 'excess_return' in x]
        return {'resolved': len(out), 'median_abs_base_error': sorted(errs)[len(errs) // 2] if errs else None,
                'bear_bull_coverage': inside / n_rng if n_rng else None,
                'mean_excess_return': sum(ex) / len(ex) if ex else None, 'items': out,
                'meaning': 'Coverage far below the intended range (e.g. <70% for bear..bull) means scenarios are too narrow.'}
