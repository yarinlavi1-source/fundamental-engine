"""SEC Companyfacts transport and exact-concept extraction.

No silent tag substitution, quarterly inference, or company-name matching.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def fetch_companyfacts(cik, user_agent, cache_dir, refresh=False):
    if not re.fullmatch(r"\d{1,10}", str(cik)):
        raise ValueError("CIK must have 1..10 digits")
    if "@" not in user_agent or "\n" in user_agent or "\r" in user_agent:
        raise ValueError("SEC_USER_AGENT must identify the application and a contact email")
    cik = str(cik).zfill(10)
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"CIK{cik}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    # Sequential CLI, below SEC's published 10 req/s ceiling.
    for attempt in range(3):
        time.sleep(max(.2, 2**attempt if attempt else .2))
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError("SEC response exceeds 32 MiB")
            payload = json.loads(content)
            if str(payload.get("cik")).zfill(10) != cik or "facts" not in payload:
                raise ValueError("SEC response has wrong identity or schema")
            digest = hashlib.sha256(content).hexdigest()
            archive = root / f"CIK{cik}-{digest}.json"
            archive.write_bytes(content)
            path.write_bytes(content)
            archive.with_suffix(".meta.json").write_text(json.dumps({
                "url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": digest}, indent=2), encoding="utf-8")
            return payload
        except HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
    raise RuntimeError("SEC request exhausted")


def select_facts(payload, taxonomy, tag, unit, as_of):
    """Latest filed record per EXACT period as of end of cutoff date.

    Companyfacts provides filing dates, not intraday availability. This is a
    date-level research view, unsuitable for intraday backtests. Amendments
    on the same filing date with conflicting values require analyst review.
    """
    cutoff = date.fromisoformat(as_of)
    concept = payload["facts"][taxonomy][tag]
    selected = {}
    for row in concept["units"][unit]:
        if date.fromisoformat(row["filed"]) > cutoff or date.fromisoformat(row["end"]) > cutoff:
            continue
        key = (row.get("start"), row["end"])
        prior = selected.get(key)
        if prior and row["filed"] == prior["filed"] and row["val"] != prior["val"]:
            raise ValueError(f"Conflicting facts on same filing date for {key}")
        if prior is None or row["filed"] > prior["filed"]:
            selected[key] = row
    return [{"metric": f"{taxonomy}:{tag}", "unit": unit, "value": r["val"],
             "start": r.get("start"), "end": r["end"], "filed": r["filed"],
             "accession": r.get("accn"), "form": r.get("form"),
             "kind": "duration" if r.get("start") else "instant"}
            for _, r in sorted(selected.items(), key=lambda kv: (kv[0][1], kv[0][0] or ""))]
