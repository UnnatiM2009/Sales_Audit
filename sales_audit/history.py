"""Run history — so a daily audit becomes a trend rather than 30 loose files.

Every run appends one row to `history.json` in the output folder. The row
carries the index, each pillar's achievement, the funnel counts and the period
covered, which is enough to answer "is this getting better?" without reopening
the reports.

Runs are keyed by (period_start, period_end, label). Re-running the same day's
data replaces that row instead of adding a duplicate, so an accidental double
run doesn't distort the trend.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HISTORY_FILE = "history.json"
SCHEMA = 1


def _path(output_dir: str | Path) -> Path:
    return Path(output_dir) / HISTORY_FILE


def load(output_dir: str | Path) -> list[dict[str, Any]]:
    """Read the history, tolerating a missing or corrupt file."""
    p = _path(output_dir)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, dict):           # schema-wrapped
        return raw.get("runs", [])
    return raw if isinstance(raw, list) else []


def save(output_dir: str | Path, runs: list[dict[str, Any]]) -> None:
    p = _path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "updated": datetime.now().isoformat(timespec="seconds"),
               "runs": runs}
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def record(result, cfg, ctx: dict, label: str = "") -> dict[str, Any]:
    """Build one history row from a completed audit."""
    band = result.band(cfg.grading)
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "period_start": ctx.get("period_start"),
        "period_end": ctx.get("period_end"),
        "days_covered": ctx.get("days_covered"),
        "index": result.index,
        "band": band["band"],
        "total_score": result.total_score,
        "scorable_weight": result.scorable_weight,
        "enquiries": ctx.get("counts", {}).get("enquiries"),
        "test_drives": ctx.get("counts", {}).get("test_drives"),
        "bookings": ctx.get("counts", {}).get("bookings"),
        "retails": ctx.get("counts", {}).get("retails"),
        "pillars": {p.title.split(". ", 1)[-1]: p.achievement for p in result.pillars},
        "open_actions": len(result.actions),
    }


def key(row: dict[str, Any]) -> tuple:
    return (row.get("period_start"), row.get("period_end"), row.get("label", ""))


def append(output_dir: str | Path, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Add a run, replacing any earlier run covering the same period and label."""
    runs = [r for r in load(output_dir) if key(r) != key(row)]
    runs.append(row)
    runs.sort(key=lambda r: (r.get("period_end") or "", r.get("run_at") or ""))
    save(output_dir, runs)
    return runs


def previous(runs: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    """The most recent earlier run, for movement arrows."""
    earlier = [r for r in runs if key(r) != key(current)
               and (r.get("period_end") or "") <= (current.get("period_end") or "")]
    return earlier[-1] if earlier else None


def movement(current: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    """Index delta and per-pillar deltas against the previous run."""
    if not prior or current.get("index") is None or prior.get("index") is None:
        return {"index_delta": None, "pillars": {}, "prior_label": None}
    deltas = {}
    for name, val in (current.get("pillars") or {}).items():
        old = (prior.get("pillars") or {}).get(name)
        if val is not None and old is not None:
            deltas[name] = round(val - old, 1)
    return {
        "index_delta": round(current["index"] - prior["index"], 1),
        "pillars": deltas,
        "prior_label": prior.get("label") or prior.get("period_end") or prior.get("run_at"),
        "prior_index": prior.get("index"),
    }


def as_table(runs: list[dict[str, Any]], limit: int = 20) -> list[list[str]]:
    """Compact trend table for the console and the web page."""
    rows = []
    for r in runs[-limit:]:
        rows.append([
            (r.get("period_end") or r.get("run_at", ""))[:10],
            r.get("label", "") or "—",
            f"{r['index']:.1f}" if r.get("index") is not None else "—",
            r.get("band", "").split(" ")[0],
            f"{r.get('enquiries') or 0:,}",
            f"{r.get('test_drives') or 0:,}",
            f"{r.get('bookings') or 0:,}",
            f"{r.get('retails') or 0:,}",
            str(r.get("open_actions") or 0),
        ])
    return rows


TABLE_HEADERS = ["Period end", "Label", "Index", "Band", "Enquiries",
                 "Test drives", "Bookings", "Retails", "Actions"]


def sparkline(runs: list[dict[str, Any]], width: int = 24) -> str:
    """Tiny console trend of the index."""
    vals = [r["index"] for r in runs if r.get("index") is not None][-width:]
    if len(vals) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    return "".join(blocks[min(int((v - lo) / span * (len(blocks) - 1)), len(blocks) - 1)]
                   for v in vals)
