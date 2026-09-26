import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, as_of TEXT NOT NULL,
            created_at TEXT NOT NULL, input_hash TEXT NOT NULL,
            input_json TEXT NOT NULL, report_json TEXT NOT NULL)""")
        self.db.commit()

    def save(self, inputs, report):
        raw = json.dumps(inputs, ensure_ascii=False, sort_keys=True, allow_nan=False)
        run_id = str(uuid.uuid4())
        with self.db:
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (
                run_id, inputs["company"]["id"], inputs["as_of"],
                datetime.now(timezone.utc).isoformat(), hashlib.sha256(raw.encode()).hexdigest(),
                raw, json.dumps(report, ensure_ascii=False, allow_nan=False)))
        return run_id

    def history(self, company_id):
        rows = self.db.execute("SELECT id, as_of, created_at, input_hash FROM runs WHERE company_id=? ORDER BY as_of, created_at", (company_id,))
        return [dict(zip(("id", "as_of", "created_at", "input_hash"), r)) for r in rows]

    def report(self, run_id):
        row = self.db.execute("SELECT report_json FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown run id")
        return json.loads(row[0])

    def compare(self, first, second):
        a, b = self.report(first), self.report(second)
        if a["company"]["id"] != b["company"]["id"]:
            raise ValueError("Compare runs for the same company")
        def flatten(obj, prefix=""):
            out = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    out.update(flatten(v, f"{prefix}[{i}]"))
            else:
                out[prefix] = obj
            return out
        fa, fb = flatten(a), flatten(b)
        return [{"path": k, "before": fa.get(k), "after": fb.get(k)}
                for k in sorted(fa.keys() | fb.keys()) if fa.get(k) != fb.get(k)]

    def close(self):
        self.db.close()
