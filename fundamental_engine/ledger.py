"""Append-only forecast ledger: the engine's predictions, frozen before outcomes, scored after.

Weakness #7: picks and deep-research base cases lived in prose, so the engine could not measure
whether it improves. Every entry is a hashed JSON line chained to the previous one; editing an old
line breaks the chain.

Time is the point of a ledger, so it is not left to the caller (v2, review of 2026-10-19):
- every entry carries `recorded_at`, the system clock at append time (injectable for tests); the
  caller's `made_at` may not be later than recorded_at, nor earlier than any made_at already on file
  (no backdating behind existing entries), nor more than `max_recording_lag_days` (default 7) before
  recorded_at, even on a fresh ledger; recorded_at itself may never run backwards;
- a recording more than LATE_RECORDING_DAYS after made_at (written before that refusal existed, or
  allowed by a larger configured lag) stays on file flagged `late_recording` and is excluded from
  coverage, return and error aggregates: hindsight cannot be ruled out;
- a forecast whose target period had already ended at recorded_at is refused: that is not a forecast;
- an outcome needs the period to have ended by the clock, and there is one outcome per forecast; a
  later fix is an explicit correction that names the outcome it replaces and why (both stay on file);
- forecasts and outcomes declare unit, currency and basis; an outcome on another definition is refused
  and scoring never compares different definitions;
- external events (used to attribute a miss to a shock) need evidence and are timestamped like the rest;
- a revised forecast is a new id that `supersedes` the old one; the original stays and is still scored.
Entries written before v2 (no recorded_at / ledger_version) stay readable and are reported as legacy:
their timing was never verified, so learning reports exclude them.
The file is created by the first successful append: a refused call leaves nothing on disk.

Scores: forecast error, scenario calibration (did the realized value fall inside the scenario
envelope at the stated frequency), and pick returns versus a stated benchmark.
"""
import hashlib, json, os
from datetime import date, datetime, timezone

LEDGER_VERSION = 2
LATE_RECORDING_DAYS = 7          # made_at more than this before recorded_at: hindsight possible
NON_MONETARY_UNITS = {'shares', 'ratio', 'pct', 'count', 'x', 'years', 'bp'}
EVENT_KINDS = {'external_shock', 'company_event', 'guidance', 'financing', 'note'}
ORDERS = {'ascending', 'descending', 'crossing'}


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse(s):
    """(aware UTC datetime, has_time) from 'YYYY-MM-DD' or an ISO timestamp."""
    s = str(s)
    if len(s) == 10:
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc), False
    t = datetime.fromisoformat(s)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc), True


def stamp(t=None):
    """UTC ISO timestamp (seconds) from a datetime, date or ISO string; the wall clock when None."""
    if t is None:
        t = datetime.now(timezone.utc)
    if isinstance(t, str):
        t = parse(t)[0]
    if not isinstance(t, datetime):
        t = datetime(t.year, t.month, t.day, tzinfo=timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat(timespec='seconds')


def day(s):
    return parse(s)[0].date()


def cmp(a, b):
    """-1/0/1 at the coarser precision of the two (a bare date equals any time on that day)."""
    (ta, ha), (tb, hb) = parse(a), parse(b)
    if not (ha and hb):
        ta, tb = ta.date(), tb.date()
    return (ta > tb) - (ta < tb)


def _num(v, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f'{label}: number required')
    v = float(v)
    if v != v or v in (float('inf'), float('-inf')):
        raise ValueError(f'{label}: finite number required')
    return v


def definition(unit, currency, basis):
    """What a number means. Monetary units need an ISO currency; every number needs a basis."""
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError('unit required (e.g. USD, shares, ratio)')
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError('basis required (the definition the number follows, e.g. reported_revenue)')
    if unit not in NON_MONETARY_UNITS and not (isinstance(currency, str) and len(currency) == 3 and currency.isalpha()
                                                and currency.isupper()):
        raise ValueError('currency (ISO code) required for monetary units')
    return {'unit': unit, 'currency': currency, 'basis': basis}


def is_legacy(row):
    return 'ledger_version' not in row


def recording_lag_days(row):
    """Days between made_at and the system recording time; None when unknown (legacy rows)."""
    if is_legacy(row) or not row.get('recorded_at') or not row.get('made_at'):
        return None
    return (day(row['recorded_at']) - day(row['made_at'])).days


def is_late(row, days=LATE_RECORDING_DAYS):
    """Recorded more than `days` after it was made (whether refused today or not)."""
    lag = recording_lag_days(row)
    return lag is not None and lag > days


def observed(outcome):
    """Date the actual became known. Legacy outcomes stored it, caller-supplied, under recorded_at."""
    return outcome.get('observed_at') or outcome.get('recorded_at')


def envelope(scenarios):
    v = [scenarios[k] for k in ('bear', 'base', 'bull')]
    return min(v), max(v)


def mismatch(forecast, outcome):
    """Reason the outcome is not comparable with the forecast, or None (unknown on legacy rows)."""
    if is_legacy(forecast) or 'basis' not in outcome:
        return None
    for k in ('unit', 'currency', 'basis'):
        if forecast.get(k) != outcome.get(k):
            return f"{k} differs: forecast {forecast.get(k)!r} vs outcome {outcome.get(k)!r}"
    return None


def effective_outcomes(rows):
    """({forecast_id: outcome in force}, {forecast_id: outcomes on file}); corrections replace, never delete."""
    heads, counts = {}, {}
    for r in rows:
        if r.get('type') == 'outcome':
            heads[r['forecast_id']] = r
            counts[r['forecast_id']] = counts.get(r['forecast_id'], 0) + 1
    return heads, counts


class Ledger:
    def __init__(self, path, clock=None, max_recording_lag_days=LATE_RECORDING_DAYS):
        """path: the JSON-lines file (created by the first successful append). max_recording_lag_days:
        refuse made_at more than this many days before the system recording time (None: no refusal;
        recordings later than LATE_RECORDING_DAYS are still flagged late and excluded from scores)."""
        if max_recording_lag_days is not None and (isinstance(max_recording_lag_days, bool) or not isinstance(max_recording_lag_days, int)
                                                   or max_recording_lag_days < 0):
            raise ValueError('max_recording_lag_days must be a non-negative integer or None')
        self.path, self.clock, self.max_recording_lag_days = path, clock, max_recording_lag_days

    def now(self):
        return stamp(self.clock() if self.clock else None)

    def lines(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]

    def entries(self):
        """Rows with a computed `legacy` flag (not stored, so hashes are untouched)."""
        return [dict(r, legacy=is_legacy(r)) for r in self.lines()]

    def verify(self):
        prev, last, rows = None, None, self.lines()
        for i, row in enumerate(rows):
            body = {k: v for k, v in row.items() if k != 'hash'}
            if row.get('prev') != prev or row.get('hash') != _h(body):
                return {'ok': False, 'broken_at': i}
            if not is_legacy(row):
                if last and cmp(row['recorded_at'], last) < 0:
                    return {'ok': False, 'broken_at': i, 'reason': 'recorded_at runs backwards'}
                last = row['recorded_at']
            prev = row['hash']
        return {'ok': True, 'entries': len(rows), 'legacy_entries': sum(is_legacy(r) for r in rows)}

    def _append(self, row):
        rows = self.lines()
        if not self.verify()['ok']:
            raise ValueError('ledger chain broken: refuse to append')
        last = next((r['recorded_at'] for r in reversed(rows) if not is_legacy(r)), None)
        if last and cmp(row['recorded_at'], last) < 0:
            raise ValueError('system clock is earlier than the last entry: refuse to append')
        row = dict(row, prev=rows[-1]['hash'] if rows else None)
        row['hash'] = _h(row)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def _when(self, rows, made_at):
        now = self.now()
        parse(made_at)
        if cmp(made_at, now) > 0:
            raise ValueError(f'made_at {made_at} is later than the system recording time {now}')
        lag = (day(now) - day(made_at)).days
        if self.max_recording_lag_days is not None and lag > self.max_recording_lag_days:
            raise ValueError(f'made_at {made_at} is {lag} days before the system recording time {now} (limit '
                             f'{self.max_recording_lag_days}): record a forecast when it is made; a later analysis is a new entry dated today')
        for r in rows:
            if r.get('made_at') and cmp(made_at, r['made_at']) < 0:
                raise ValueError(f"made_at {made_at} is earlier than an entry already on file ({r['made_at']}): backdating refused")
        return now

    def forecast(self, *, id, entity, made_at, metric, period_end, scenarios, model_version, unit=None, currency=None,
                 basis=None, price=None, benchmark=None, inputs_hash=None, thesis_breakers=(), note='', thesis_id=None,
                 supersedes=None, scenario_order='ascending', context=None):
        """scenarios: {'bear','base','bull'} (+ optional 'prob') in the metric's unit.

        scenario_order: 'ascending' (bear <= base <= bull, the default), 'descending' (e.g. share count,
        costs) or 'crossing' (paths cross, e.g. bull invests more early; needs a note). Calibration
        always uses the envelope min..max of the three values."""
        rows = self.lines()
        if any(r.get('id') == id for r in rows):
            raise ValueError('forecast id exists: new information = new id that references the old one')
        defn = definition(unit, currency, basis)
        now = self._when(rows, made_at)
        if day(period_end) < day(now):
            raise ValueError(f'target period ended {period_end}, before recording ({now}): cannot forecast the past')
        s = {k: _num(scenarios.get(k), 'scenarios.' + k) for k in ('bear', 'base', 'bull')}
        if scenario_order not in ORDERS:
            raise ValueError('scenario_order must be ascending, descending or crossing')
        if scenario_order == 'ascending' and not s['bear'] <= s['base'] <= s['bull']:
            raise ValueError('scenario values must be ordered bear <= base <= bull')
        if scenario_order == 'descending' and not s['bear'] >= s['base'] >= s['bull']:
            raise ValueError('scenario values must be ordered bear >= base >= bull')
        if scenario_order == 'crossing' and not str(note).strip():
            raise ValueError('crossing scenario paths need a note explaining why')
        if supersedes is not None:
            old = next((r for r in rows if r.get('type') == 'forecast' and r.get('id') == supersedes), None)
            if old is None or old['entity'] != entity or old['metric'] != metric:
                raise ValueError('supersedes must name an earlier forecast of the same entity and metric')
        row = {'type': 'forecast', 'ledger_version': LEDGER_VERSION, 'id': id, 'entity': entity, 'made_at': made_at,
               'recorded_at': now, 'recording_lag_days': (day(now) - day(made_at)).days, 'metric': metric,
               'period_end': period_end, 'scenarios': dict(scenarios), 'scenario_order': scenario_order, **defn,
               'price': price, 'benchmark': benchmark, 'model_version': model_version, 'inputs_hash': inputs_hash,
               'thesis_breakers': list(thesis_breakers), 'note': note, 'thesis_id': thesis_id, 'supersedes': supersedes,
               'context': context or {}}
        return self._append(row)

    def thesis(self, *, thesis_id, entity, made_at, record, model_version):
        """The decision record a set of forecasts belongs to (verdict, values, assumptions, triggers)."""
        rows = self.lines()
        if any(r.get('type') == 'thesis' and r.get('thesis_id') == thesis_id for r in rows):
            raise ValueError('thesis already recorded: a new analysis is a new thesis_id')
        now = self._when(rows, made_at)
        return self._append({'type': 'thesis', 'ledger_version': LEDGER_VERSION, 'thesis_id': thesis_id, 'entity': entity,
                             'made_at': made_at, 'recorded_at': now, 'model_version': model_version, 'record': record})

    def event(self, *, id, entity, kind, occurred_at, description, evidence_ids=(), affects=(), why_unforeseeable=None):
        """A dated fact about the world. kind='external_shock' is the only route by which a miss is attributed
        to something outside the analysis, and it needs evidence plus a reason it could not be foreseen."""
        rows = self.lines()
        if any(r.get('id') == id for r in rows):
            raise ValueError('event id exists')
        if kind not in EVENT_KINDS:
            raise ValueError(f'event kind must be one of {sorted(EVENT_KINDS)}')
        if not str(description).strip():
            raise ValueError('event description required')
        if kind == 'external_shock' and (not evidence_ids or not str(why_unforeseeable or '').strip()):
            raise ValueError('external_shock needs evidence_ids and why_unforeseeable')
        now = self.now()
        if cmp(occurred_at, now) > 0:
            raise ValueError('event dated in the future')
        return self._append({'type': 'event', 'ledger_version': LEDGER_VERSION, 'id': id, 'entity': entity, 'kind': kind,
                             'occurred_at': occurred_at, 'recorded_at': now, 'description': description,
                             'evidence_ids': list(evidence_ids), 'affects': list(affects), 'why_unforeseeable': why_unforeseeable})

    def outcome(self, *, forecast_id, actual, source_id, unit=None, currency=None, basis=None, observed_at=None,
                recorded_at=None, benchmark_return=None, price_now=None, corrects=None, correction_reason=None):
        """observed_at: date the actual became public (`recorded_at=` is accepted as its legacy name;
        the stored recorded_at is always the system clock). One outcome per forecast; a correction names
        the hash of the outcome it replaces (corrects=) and a reason."""
        rows = self.lines()
        f = next((r for r in rows if r.get('type') == 'forecast' and r['id'] == forecast_id), None)
        if f is None:
            raise ValueError('unknown forecast id')
        obs = observed_at or recorded_at
        if obs is None:
            raise ValueError('observed_at required')
        defn = definition(unit, currency, basis)
        actual = _num(actual, 'actual')
        now = self.now()
        if day(obs) < day(f['period_end']) or cmp(obs, f['made_at']) < 0:
            raise ValueError('outcome cannot be recorded before the forecast or before the period ends')
        if day(now) < day(f['period_end']):
            raise ValueError(f"period ends {f['period_end']}; the system clock ({now}) says it has not ended")
        if cmp(obs, now) > 0:
            raise ValueError('observed_at is in the future')
        why = mismatch(f, defn)
        if why:
            raise ValueError('outcome definition differs from the forecast (' + why + '): convert to the forecast basis first')
        prior = [r for r in rows if r.get('type') == 'outcome' and r['forecast_id'] == forecast_id]
        if prior:
            if corrects is None:
                raise ValueError('outcome already recorded: a correction must name the earlier outcome (corrects=hash) and a reason')
            if corrects != prior[-1]['hash']:
                raise ValueError('corrects must name the outcome currently in force for this forecast')
            if not str(correction_reason or '').strip():
                raise ValueError('correction_reason required')
        elif corrects is not None:
            raise ValueError('nothing to correct: no outcome recorded for this forecast')
        return self._append({'type': 'outcome', 'ledger_version': LEDGER_VERSION, 'forecast_id': forecast_id,
                             'observed_at': obs, 'recorded_at': now, 'actual': actual, **defn, 'source_id': source_id,
                             'benchmark_return': benchmark_return, 'price_now': price_now, 'corrects': corrects,
                             'correction_reason': correction_reason})

    def score(self):
        """Late recordings (made_at more than LATE_RECORDING_DAYS before recorded_at) are listed but
        excluded from coverage, error and return aggregates."""
        rows = self.lines()
        fc = {r['id']: r for r in rows if r.get('type') == 'forecast'}
        heads, counts = effective_outcomes(rows)
        out, not_scored = [], []
        for fid, o in heads.items():
            f = fc.get(fid)
            if f is None:
                continue
            why = mismatch(f, o)
            if why:
                not_scored.append({'id': fid, 'reason': 'different definitions: ' + why})
                continue
            s, a = f['scenarios'], o['actual']
            lo, hi = envelope(s)
            err = (a - s['base']) / abs(s['base']) if s['base'] else None
            within = lo <= a <= hi
            item = {'id': f['id'], 'entity': f['entity'], 'metric': f['metric'], 'base': s['base'], 'actual': a,
                    'base_error': err, 'inside_bear_bull': within, 'legacy': is_legacy(f),
                    'basis_unverified': is_legacy(f) or 'basis' not in o, 'corrected': counts[fid] > 1,
                    'late_recording': is_late(f), 'recording_lag_days': recording_lag_days(f)}
            if f.get('price') and o.get('price_now'):
                item['stock_return'] = o['price_now'] / f['price'] - 1
                if o.get('benchmark_return') is not None:
                    item['excess_return'] = item['stock_return'] - o['benchmark_return']
            out.append(item)
        on_time = [x for x in out if not x['late_recording']]
        errs = sorted(abs(x['base_error']) for x in on_time if x['base_error'] is not None)
        ex = [x['excess_return'] for x in on_time if 'excess_return' in x]
        return {'resolved': len(out), 'excluded_late_recordings': len(out) - len(on_time),
                'median_abs_base_error': errs[len(errs) // 2] if errs else None,
                'bear_bull_coverage': sum(x['inside_bear_bull'] for x in on_time) / len(on_time) if on_time else None,
                'mean_excess_return': sum(ex) / len(ex) if ex else None, 'items': out, 'not_scored': not_scored,
                'meaning': 'Coverage far below the intended range (e.g. <70% for bear..bull) means scenarios are too narrow. '
                           f'Aggregates exclude forecasts recorded more than {LATE_RECORDING_DAYS} days after made_at.'}
