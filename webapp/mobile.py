"""Physical audit on a phone.

The auditor walks the showroom with a phone and taps Yes / Partial / No / NA
against the 62 checklist items. Every tap is saved locally on the device and
posted to the server; if the signal drops in a basement delivery bay the
answers queue up and sync when it comes back.

On the laptop, the captured audits become an ordinary Physical_Audit_Sheet.xlsx
— the same layout the scorer has always read — so nothing downstream changes.

Routes (all under /m):

    /m                      pick a branch, start or resume an audit
    /m/<id>                 the checklist itself
    /m/<id>/save            POST one answer (JSON) — called on every tap
    /m/<id>/bulk            POST a queue of answers (JSON) — offline catch-up
    /m/<id>/submit          finish the audit
    /m/<id>/reopen          reopen a submitted audit to correct it
    /m/<id>/sheet.xlsx      this branch's sheet, as Excel
    /m/<id>/photo           POST a photograph against one check item
    /m/<id>/photo/<file>    GET a stored photograph
    /m/<id>/photo/delete    POST to remove one

Photographs are offered on Partial and No, the two answers that need
evidence. They are never required - a finding recorded without a photograph
is still a finding, and an auditor should not be blocked by a flat battery.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, jsonify, redirect,
                   render_template, request, send_file, url_for)

from sales_audit import mobile_store as store
from sales_audit.physical import CHECKLIST, SECTIONS

bp = Blueprint("mobile", __name__, url_prefix="/m")
log = logging.getLogger(__name__)


def _store() -> Path:
    return current_app.config["PHYSICAL_DIR"]


def _branches() -> list[str]:
    return current_app.config["LOAD_CONFIG"]().branches


def _rec(rec_id: str) -> dict:
    rec = store.load(_store(), rec_id)
    if rec is None:
        abort(404)
    return rec


def _items() -> list[dict]:
    """The checklist as plain dicts, grouped by section for the template."""
    out = []
    for section in SECTIONS:
        items = [c for c in CHECKLIST if c.section == section]
        out.append({
            "section": section,
            "short": section.split(". ", 1)[-1],
            "num": section.split(".", 1)[0],
            "checks": [{"code": c.code, "item": c.item, "what": c.what_to_look_for,
                       "weight": c.weight, "critical": c.critical} for c in items],
        })
    return out


# --- pages -------------------------------------------------------------------
@bp.route("/")
def home():
    recs = store.all_records(_store())
    open_rows = [store.summary_row(r) for r in recs if not r.get("submitted")]
    done_rows = [store.summary_row(r) for r in recs if r.get("submitted")][:12]
    # A link from the branch page arrives with the outlet already chosen.
    preset = (request.args.get("branch") or "").strip()
    return render_template("m_home.html", branches=_branches(),
                           open_rows=open_rows, done_rows=done_rows,
                           preset=preset if preset in _branches() else None,
                           total=len(CHECKLIST), sections=len(SECTIONS))


@bp.route("/start", methods=["POST"])
def start():
    branch = (request.form.get("branch") or "").strip()
    if branch not in _branches():
        abort(400, "Unknown branch")
    rec = store.blank(branch,
                      auditor=(request.form.get("auditor") or "").strip(),
                      audit_date=(request.form.get("audit_date") or "").strip())
    store.save(_store(), rec)
    return redirect(url_for("mobile.audit", rec_id=rec["id"]))


@bp.route("/<rec_id>")
def audit(rec_id: str):
    rec = _rec(rec_id)
    # Photographs live on the server, so their URLs are handed to the page
    # rather than rebuilt from the device's local copy of the answers.
    photos = {code: [_photo_json(rec_id, p) for p in (slot.get("photos") or [])]
              for code, slot in (rec.get("responses") or {}).items()
              if slot.get("photos")}
    return render_template("m_audit.html", rec=rec, groups=_items(),
                           prog=store.progress(rec),
                           sections=store.section_progress(rec),
                           photos=photos,
                           max_photos=store.MAX_PHOTOS_PER_ITEM)


@bp.route("/<rec_id>/done")
def done(rec_id: str):
    rec = _rec(rec_id)
    return render_template("m_done.html", rec=rec, prog=store.progress(rec),
                           sections=store.section_progress(rec))


# --- the API the phone talks to ----------------------------------------------
@bp.route("/<rec_id>/save", methods=["POST"])
def save_one(rec_id: str):
    rec = _rec(rec_id)
    if rec.get("submitted"):
        return jsonify(ok=False, error="This audit has been submitted. "
                                       "Reopen it before changing an answer."), 409
    body = request.get_json(silent=True) or {}
    ok = store.set_response(rec, str(body.get("code", "")),
                            response=body.get("response"),
                            observation=body.get("observation"),
                            owner=body.get("owner"), due=body.get("due"))
    if not ok:
        return jsonify(ok=False, error="Unknown check item or response"), 400
    store.save(_store(), rec)
    return jsonify(ok=True, progress=store.progress(rec),
                   sections=store.section_progress(rec))


@bp.route("/<rec_id>/bulk", methods=["POST"])
def save_bulk(rec_id: str):
    """Drain the offline queue in one request."""
    rec = _rec(rec_id)
    if rec.get("submitted"):
        return jsonify(ok=False, error="Already submitted"), 409
    body = request.get_json(silent=True) or {}
    applied = rejected = 0
    for entry in (body.get("answers") or [])[:400]:
        if store.set_response(rec, str(entry.get("code", "")),
                              response=entry.get("response"),
                              observation=entry.get("observation"),
                              owner=entry.get("owner"), due=entry.get("due")):
            applied += 1
        else:
            rejected += 1
    meta = body.get("meta") or {}
    if meta.get("auditor"):
        rec["auditor"] = str(meta["auditor"])[:60]
    if meta.get("audit_date"):
        rec["audit_date"] = str(meta["audit_date"])[:20]
    store.save(_store(), rec)
    return jsonify(ok=True, applied=applied, rejected=rejected,
                   progress=store.progress(rec),
                   sections=store.section_progress(rec))


@bp.route("/<rec_id>/photo", methods=["POST"])
def add_photo(rec_id: str):
    """Attach a photograph to one check item.

    Posted as multipart from the camera or the gallery. The phone shrinks the
    image before sending, so what arrives is already small.
    """
    rec = _rec(rec_id)
    if rec.get("submitted"):
        return jsonify(ok=False, error="This audit has been submitted. "
                                       "Reopen it before adding a photo."), 409
    code = str(request.form.get("code", "")).strip()
    f = request.files.get("photo")
    if f is None or not f.filename:
        return jsonify(ok=False, error="No photo received"), 400

    data = f.read()
    entry = store.add_photo(_store(), rec, code, data,
                            content_type=f.mimetype or "",
                            caption=request.form.get("caption", ""))
    if entry is None:
        return jsonify(ok=False, error="Could not attach that photo - it may be "
                                       "too large, or this item already has "
                                       f"{store.MAX_PHOTOS_PER_ITEM}."), 400
    store.save(_store(), rec)
    entry = dict(entry)
    entry["url"] = url_for("mobile.get_photo", rec_id=rec_id, name=entry["file"])
    return jsonify(ok=True, photo=entry,
                   photos=[_photo_json(rec_id, p)
                           for p in store.photos_for(rec, code)],
                   progress=store.progress(rec))


def _photo_json(rec_id: str, p: dict) -> dict:
    out = dict(p)
    out["url"] = url_for("mobile.get_photo", rec_id=rec_id, name=p["file"])
    return out


@bp.route("/<rec_id>/photo/<name>")
def get_photo(rec_id: str, name: str):
    _rec(rec_id)                      # 404 for an unknown audit
    path = store.photo_path(_store(), rec_id, name)
    if path is None:
        abort(404)
    return send_file(path, max_age=86400)


@bp.route("/<rec_id>/photo/delete", methods=["POST"])
def drop_photo(rec_id: str):
    rec = _rec(rec_id)
    if rec.get("submitted"):
        return jsonify(ok=False, error="Already submitted"), 409
    body = request.get_json(silent=True) or {}
    code, name = str(body.get("code", "")), str(body.get("file", ""))
    if not store.remove_photo(_store(), rec, code, name):
        return jsonify(ok=False, error="No such photo"), 404
    store.save(_store(), rec)
    return jsonify(ok=True,
                   photos=[_photo_json(rec_id, p)
                           for p in store.photos_for(rec, code)],
                   progress=store.progress(rec))


@bp.route("/<rec_id>/submit", methods=["POST"])
def submit(rec_id: str):
    rec = _rec(rec_id)
    prog = store.progress(rec)
    if prog["answered"] == 0:
        return jsonify(ok=False, error="Nothing has been answered yet."), 400
    rec["submitted"] = True
    rec["submitted_at"] = datetime.now().isoformat(timespec="seconds")
    store.save(_store(), rec)
    log.info("physical audit submitted: %s (%s/%s answered)",
             rec["branch"], prog["answered"], prog["total"])
    return jsonify(ok=True, redirect=url_for("mobile.done", rec_id=rec_id))


@bp.route("/<rec_id>/reopen", methods=["POST"])
def reopen(rec_id: str):
    rec = _rec(rec_id)
    rec["submitted"] = False
    rec["submitted_at"] = None
    store.save(_store(), rec)
    return redirect(url_for("mobile.audit", rec_id=rec_id))


@bp.route("/<rec_id>/delete", methods=["POST"])
def discard(rec_id: str):
    _rec(rec_id)
    store.delete(_store(), rec_id)
    return redirect(url_for("mobile.home"))


# --- hand-off to the laptop --------------------------------------------------
@bp.route("/<rec_id>/sheet.xlsx")
def sheet(rec_id: str):
    """This one audit as a Physical Audit Sheet, for the command-line flow."""
    import secrets
    rec = _rec(rec_id)
    tmp = current_app.config["RUNS_DIR"] / f"sheet_{secrets.token_hex(6)}"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "Physical_Audit_Sheet.xlsx"
    from sales_audit.physical import write_template
    write_template(path, [rec["branch"]],
                   answers={rec["branch"]: rec.get("responses", {})},
                   meta={rec["branch"]: {"auditor": rec.get("auditor"),
                                         "audit_date": rec.get("audit_date")}})
    # Name it after the branch. Three branches walked in a day means three
    # files landing in the same Downloads folder, and 'Physical_Audit_Sheet
    # (2).xlsx' tells nobody which outlet it is. The audit matches the name
    # loosely, so the suffix costs nothing.
    safe = store.slug(rec["branch"])
    return send_file(path, as_attachment=True,
                     download_name=f"Physical_Audit_Sheet_{safe}.xlsx")
