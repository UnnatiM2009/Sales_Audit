"""Physical audits captured on a phone.

The Physical Audit Sheet is walked, not exported — so it is collected on a
mobile device in the showroom and read back on the laptop when the DMS
extracts are scored.

This module is the store in between. One JSON file per audit, one audit per
branch visit:

    <store>/<branch-slug>__<id>.json

Nothing here scores the audit itself. `physical.score_physical` remains the
only scorer; this module converts stored responses into the same workbook
layout `physical.read_physical` already understands, so the phone and the
blank Excel sheet are interchangeable inputs to the audit.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path

from .physical import CHECKLIST, RESPONSES, SECTIONS

VALID = {"YES", "PARTIAL", "NO", "NA"}
_LOOKUP = {c.code: c for c in CHECKLIST}


# --- identity ----------------------------------------------------------------
def new_id() -> str:
    return secrets.token_hex(6)


def slug(name: str) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(name).strip()]
    return "".join(keep).strip("_")[:40] or "branch"


def _path(store: Path, rec_id: str, branch: str) -> Path:
    return Path(store) / f"{slug(branch)}__{rec_id}.json"


# --- create / read / write ---------------------------------------------------
def blank(branch: str, auditor: str = "", audit_date: str = "") -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": new_id(),
        "branch": str(branch),
        "auditor": auditor.strip()[:60],
        "audit_date": audit_date or datetime.now().strftime("%Y-%m-%d"),
        "created": now,
        "updated": now,
        "submitted": False,
        "submitted_at": None,
        "responses": {},
    }


def save(store: Path, rec: dict) -> Path:
    """Write atomically — a phone on a flaky connection can retry mid-write."""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    rec["updated"] = datetime.now().isoformat(timespec="seconds")
    dest = _path(store, rec["id"], rec["branch"])
    fd, tmp = tempfile.mkstemp(dir=store, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, dest)
    return dest


def load(store: Path, rec_id: str) -> dict | None:
    if not rec_id or not rec_id.isalnum() or len(rec_id) > 24:
        return None
    for p in Path(store).glob(f"*__{rec_id}.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def delete(store: Path, rec_id: str) -> bool:
    for p in Path(store).glob(f"*__{rec_id}.json"):
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


def all_records(store: Path) -> list[dict]:
    """Every stored audit, newest first."""
    out = []
    store = Path(store)
    if not store.is_dir():
        return out
    for p in store.glob("*__*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda r: r.get("updated", ""), reverse=True)


def latest_per_branch(store: Path, submitted_only: bool = True) -> dict[str, dict]:
    """The most recent audit for each branch — what a scoring run should use."""
    out: dict[str, dict] = {}
    for rec in all_records(store):          # already newest first
        if submitted_only and not rec.get("submitted"):
            continue
        out.setdefault(rec.get("branch", ""), rec)
    out.pop("", None)
    return out


# --- response handling -------------------------------------------------------
def set_response(rec: dict, code: str, response: str | None = None,
                 observation: str | None = None, owner: str | None = None,
                 due: str | None = None) -> bool:
    """Apply one answer. Returns False for an unknown code or response."""
    if code not in _LOOKUP:
        return False
    slot = rec.setdefault("responses", {}).setdefault(code, {})
    if response is not None:
        r = str(response).strip().upper()
        if r in ("", "CLEAR", "NONE"):
            slot.pop("r", None)
        elif r in VALID:
            slot["r"] = r
        else:
            return False
    if observation is not None:
        slot["obs"] = str(observation)[:600]
    if owner is not None:
        slot["owner"] = str(owner)[:60]
    if due is not None:
        slot["due"] = str(due)[:20]
    if not slot:
        rec["responses"].pop(code, None)
    return True


def progress(rec: dict) -> dict:
    """Answered count, live score and critical failures for one audit.

    Scoring mirrors `physical.score_physical` exactly: Yes 2, Partial 1, No 0,
    each weighted by the item weight; NA is excluded from both sides.
    """
    responses = rec.get("responses", {})
    answered = earned = possible = 0
    na = partial = failed = 0
    criticals: list[dict] = []
    for item in CHECKLIST:
        r = (responses.get(item.code) or {}).get("r")
        if not r:
            continue
        answered += 1
        if r == "NA":
            na += 1
            continue
        pts = RESPONSES[r]
        earned += item.weight * pts
        possible += item.weight * 2
        if pts == 1:
            partial += 1
        if pts == 0:
            failed += 1
            if item.critical:
                criticals.append({
                    "code": item.code, "item": item.item, "section": item.section,
                    "observation": (responses.get(item.code) or {}).get("obs", ""),
                })
    total = len(CHECKLIST)
    return {
        "answered": answered,
        "total": total,
        "remaining": total - answered,
        "complete": answered >= total,
        "pct_done": round(answered / total * 100, 1) if total else 0.0,
        "score": round(earned / possible * 100, 1) if possible else None,
        "na": na, "partial": partial, "failed": failed,
        "criticals": criticals,
    }


def section_progress(rec: dict) -> list[dict]:
    """Per-section answered counts, for the section chips on the phone."""
    responses = rec.get("responses", {})
    out = []
    for section in SECTIONS:
        items = [c for c in CHECKLIST if c.section == section]
        done = sum(1 for c in items if (responses.get(c.code) or {}).get("r"))
        out.append({"section": section, "short": section.split(". ", 1)[-1],
                    "n": len(items), "done": done})
    return out


def summary_row(rec: dict) -> dict:
    """One line about an audit, for the branch table on the laptop."""
    p = progress(rec)
    return {
        "id": rec.get("id"), "branch": rec.get("branch"),
        "auditor": rec.get("auditor") or "—",
        "audit_date": rec.get("audit_date") or "—",
        "updated": (rec.get("updated") or "")[:16].replace("T", " "),
        "submitted": bool(rec.get("submitted")),
        "answered": p["answered"], "total": p["total"], "pct_done": p["pct_done"],
        "score": p["score"], "criticals": len(p["criticals"]),
    }


# --- hand-off to the scoring run ---------------------------------------------
def answers_by_branch(records: list[dict]) -> dict[str, dict[str, dict]]:
    """{branch: {code: {r, obs, owner, due}}} — the shape the sheet writer wants.

    Later records win, so pass them newest-last if you are merging.
    """
    out: dict[str, dict[str, dict]] = {}
    for rec in records:
        branch = rec.get("branch")
        if not branch:
            continue
        out.setdefault(branch, {}).update(rec.get("responses", {}))
    return out


def build_sheet(store: Path, path: Path, branches: list[str],
                only: str | None = None, include_drafts: bool = False) -> dict:
    """Write a Physical_Audit_Sheet.xlsx from the phone captures.

    The file is byte-for-byte the same layout as the blank template, so the
    audit reads it through the ordinary `read_physical` path and cannot tell
    whether it was typed in Excel or tapped on a phone.

    Returns {branch: summary_row} for the branches that contributed.
    """
    from .physical import write_template

    latest = latest_per_branch(store, submitted_only=not include_drafts)
    if only:
        latest = {b: r for b, r in latest.items() if b == only}
    answers = answers_by_branch(list(latest.values()))
    tabs = [b for b in branches if b in answers] if branches else list(answers)
    for b in answers:                      # a branch captured but not in norms.yaml
        if b not in tabs:
            tabs.append(b)
    if only and only not in tabs:
        tabs.append(only)
    write_template(Path(path), tabs or list(branches), answers=answers)
    return {b: summary_row(r) for b, r in latest.items()}


def has_any(store: Path, submitted_only: bool = True) -> bool:
    return bool(latest_per_branch(store, submitted_only=submitted_only))
