"""Snapshot provenance helpers (stage 0). No valuation or business logic lives here.

A snapshot file is only trusted through its manifest entry: source, retrieval time, information date,
code version and SHA-256 of the exact bytes. `verify` re-hashes files and reports every mismatch.
"""
import datetime as _dt
import hashlib
import json
import os

PIT_LAG_DAYS = 2  # a fact counts as known at the as-of date only if filed at least this many days before it


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def entry(root, rel, source, retrieved_utc, information_date, code_version, label, **extra):
    """Manifest entry for root/rel. Every field the stage 0 policy requires is mandatory."""
    for k, v in dict(source=source, retrieved_utc=retrieved_utc, information_date=information_date,
                     code_version=code_version, label=label).items():
        if not v:
            raise ValueError(f'manifest field {k} is required for {rel}')
    p = os.path.join(root, rel)
    return dict(file=rel, bytes=os.path.getsize(p), sha256=sha256_file(p), source=source,
                retrieved_utc=retrieved_utc, information_date=information_date, code_version=code_version,
                label=label, **extra)


def verify(root, manifest):
    """Re-hash every manifest file under root. Returns a list of problems (empty = all identical)."""
    bad = []
    for m in manifest:
        p = os.path.join(root, m['file'])
        if not os.path.exists(p):
            bad.append((m['file'], 'missing'))
        elif os.path.getsize(p) != m['bytes'] or sha256_file(p) != m['sha256']:
            bad.append((m['file'], 'sha256 mismatch'))
    return bad


def cutoff(asof, lag_days=PIT_LAG_DAYS):
    return (_dt.date.fromisoformat(asof) - _dt.timedelta(days=lag_days)).isoformat()


def known_at(rows, asof, lag_days=PIT_LAG_DAYS):
    """Split SEC fact rows into (known, excluded) by filed date <= as-of minus the lag."""
    c = cutoff(asof, lag_days)
    known = [r for r in rows if r['filed'] <= c]
    return known, len(rows) - len(known)


def period_views(rows):
    """For one tag/unit, among rows already filtered by `known_at`, per period (start, end):
    first  = the value as originally filed (the convention of fundamental_engine.pit)
    latest = the most recently filed version known at the as-of date
    changed = periods whose value differs between filings (restatements / recasts known by the as-of date)."""
    first, latest, changed = {}, {}, []
    for r in sorted(rows, key=lambda r: r['filed']):
        k = (r.get('start'), r['end'])
        first.setdefault(k, r)
        if k in latest and latest[k]['val'] != r['val']:
            changed.append(dict(start=k[0], end=k[1], before=latest[k]['val'], before_filed=latest[k]['filed'],
                                after=r['val'], after_filed=r['filed']))
        latest[k] = r
    return first, latest, changed


def dump(obj, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=True)
