"""Sales Process Audit — web front end.

Upload the DMS extracts, run the audit in the browser, download the report and
the findings workbook. Each run gets its own scratch directory keyed by a run
id, so concurrent users never see each other's files.

Deployed on Render with gunicorn; see render.yaml.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

# gunicorn is started with --chdir webapp, so the project root is not
# necessarily on the import path. Put it there before importing sales_audit.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)
from werkzeug.utils import secure_filename

from sales_audit import history
from sales_audit import mobile_store as mstore
from sales_audit.audit import run_audit
from sales_audit.config import INPUT_FILES, Config
from sales_audit.loaders import branches_present, filter_branches, load_all
from sales_audit import physical as physical_mod
from sales_audit.physical import write_template
from sales_audit.report_docx import write_report
from sales_audit.report_xlsx import write_findings

# --- app ---------------------------------------------------------------------
BASE = Path(__file__).resolve().parent.parent
RUNS = Path(os.environ.get("AUDIT_RUNS_DIR", tempfile.gettempdir())) / "sales_audit_runs"
RUNS.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = Path(os.environ.get("AUDIT_CONFIG", BASE / "config" / "norms.yaml"))
# History outlives individual runs, so it lives outside the run directories.
# On Render, point AUDIT_HISTORY_DIR at a persistent disk to keep the trend
# across deploys; on the free plan it resets when the instance restarts.
HISTORY_DIR = Path(os.environ.get("AUDIT_HISTORY_DIR", RUNS / "_history"))
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
RUN_TTL_HOURS = int(os.environ.get("AUDIT_RUN_TTL_HOURS", "6"))
MAX_MB = int(os.environ.get("AUDIT_MAX_UPLOAD_MB", "80"))
# Physical audits captured on a phone outlive a scoring run — the walk happens
# on Tuesday and the extracts are pulled on Friday. They live beside the
# history rather than inside a run directory, and the sweeper leaves them
# alone. On Render, point this at the same persistent disk as the history.
PHYSICAL_DIR = Path(os.environ.get("AUDIT_PHYSICAL_DIR", HISTORY_DIR / "physical"))
PHYSICAL_DIR.mkdir(parents=True, exist_ok=True)
# When no sheet is uploaded, fall back to whatever was captured on phones.
# Set AUDIT_MOBILE_AUTOPICK=0 to switch that off and require the exported
# Excel every time, so pillar J is only ever scored on a file someone
# deliberately attached.
AUTOPICK = os.environ.get("AUDIT_MOBILE_AUTOPICK", "1") != "0"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

app.config["PHYSICAL_DIR"] = PHYSICAL_DIR
app.config["RUNS_DIR"] = RUNS

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("webapp")


# --- housekeeping ------------------------------------------------------------
def sweep_old_runs() -> None:
    """Delete run directories older than the TTL. Uploads are dealership data;
    they should not linger on a shared host."""
    cutoff = time.time() - RUN_TTL_HOURS * 3600
    for d in RUNS.glob("*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _sweeper() -> None:
    while True:
        sweep_old_runs()
        time.sleep(1800)


threading.Thread(target=_sweeper, daemon=True).start()


def run_dir(run_id: str) -> Path:
    """Resolve a run id to its directory, rejecting anything path-like."""
    if not run_id or not run_id.isalnum() or len(run_id) > 40:
        abort(404)
    d = RUNS / run_id
    if not d.is_dir():
        abort(404)
    return d


def load_config() -> Config:
    return Config.load(CONFIG_PATH)


app.config["LOAD_CONFIG"] = load_config

try:                                    # noqa: E402 — needs app.config above
    from mobile import bp as mobile_bp
except ImportError:                     # imported as a package (webapp.app)
    from webapp.mobile import bp as mobile_bp

app.register_blueprint(mobile_bp)


# --- routes ------------------------------------------------------------------
@app.route("/")
def index():
    cfg = load_config()
    required = [s for s in INPUT_FILES if s.required]
    optional = [s for s in INPUT_FILES if not s.required]
    return render_template("index.html", required=required, optional=optional,
                           cfg=cfg, max_mb=MAX_MB, branches=cfg.branches)


@app.route("/checklist")
@app.route("/physical-audit-sheet")
def checklist():
    """Blank Physical Audit Sheet, generated on demand.

    With `?branch=<name>` it carries a single tab for that outlet. A sheet with
    eleven tabs is awkward to work from when you are only visiting one branch,
    and it invites filling in the wrong one.
    """
    cfg = load_config()
    choice = (request.args.get("branch") or "").strip()
    if choice and choice.lower() != "all" and choice not in cfg.branches:
        flash(f"'{choice}' is not a branch in config/norms.yaml.", "error")
        return redirect(url_for("branch_hub"))
    branches = [choice] if choice and choice.lower() != "all" else cfg.branches
    tmp = RUNS / f"sheet_{secrets.token_hex(6)}"
    tmp.mkdir(parents=True, exist_ok=True)
    suffix = f"_{_slug(choice)}" if len(branches) == 1 and choice else ""
    path = tmp / f"Physical_Audit_Sheet_BLANK{suffix}.xlsx"
    write_template(path, branches)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/branches")
def branch_hub():
    """Branch audit facility — one outlet at a time, physical audit included.

    A network audit answers 'how is the dealership doing'. This page answers
    'how is this branch doing', which is the question a branch manager can
    actually act on, and it is where the phone captures surface.
    """
    cfg = load_config()
    captured = {b: mstore.summary_row(r)
                for b, r in mstore.latest_per_branch(PHYSICAL_DIR).items()}
    drafts = {b: mstore.summary_row(r) for b, r in
              mstore.latest_per_branch(PHYSICAL_DIR, submitted_only=False).items()
              if b not in captured}
    rows = [{"branch": b, "physical": captured.get(b), "draft": drafts.get(b)}
            for b in cfg.branches]
    stray = [{"branch": b, "physical": r, "draft": None}
             for b, r in captured.items() if b not in cfg.branches]
    return render_template("branches.html", rows=rows + stray, cfg=cfg,
                           max_mb=MAX_MB, ready=len(captured))


@app.route("/physical/sheet.xlsx")
def physical_sheet():
    """Every captured branch audit, as one Physical_Audit_Sheet.xlsx.

    For the command-line flow: download this into `input/` and run
    `python run_audit.py`. The web app does not need it — it builds the same
    file itself when a run has no sheet uploaded.
    """
    cfg = load_config()
    only = (request.args.get("branch") or "").strip() or None
    drafts = request.args.get("drafts") == "1"
    if not mstore.latest_per_branch(PHYSICAL_DIR, submitted_only=not drafts):
        flash("No physical audits have been captured on mobile yet.", "error")
        return redirect(url_for("branch_hub"))
    tmp = RUNS / f"sheet_{secrets.token_hex(6)}"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "Physical_Audit_Sheet.xlsx"
    mstore.build_sheet(PHYSICAL_DIR, path, cfg.branches, only=only,
                       include_drafts=drafts)
    return send_file(path, as_attachment=True,
                     download_name="Physical_Audit_Sheet.xlsx")


def _physical_for_run(d: Path, cfg, scope: str | None) -> tuple[Path | None, str]:
    """The Physical Audit Sheet this run should score, and where it came from.

    An uploaded sheet always wins — if someone went to the trouble of filling
    in Excel, that is what they mean to be scored. Otherwise the audits
    captured on phones are assembled into the same file, which is what makes
    'walk it on mobile, score it on the laptop' work with nothing in between.
    """
    uploaded = _find_sheet(d / "input")
    if uploaded:
        return uploaded, "uploaded"
    if not AUTOPICK or not mstore.latest_per_branch(PHYSICAL_DIR):
        return None, "none"
    # Built outside input/ and scoped in its filename, so a branch audit never
    # picks up the sheet assembled for a different branch.
    folder = d / "physical"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"mobile_{_slug(scope) if scope else 'network'}.xlsx"
    if path.exists():
        return path, "mobile"
    try:
        mstore.build_sheet(PHYSICAL_DIR, path, cfg.branches, only=scope)
    except Exception:  # noqa: BLE001 — a bad capture must not sink the run
        log.exception("could not build the sheet from mobile captures")
        return None, "none"
    return path, "mobile"


@app.route("/run", methods=["POST"])
def run():
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        flash("No files selected.", "error")
        return redirect(url_for("index"))

    run_id = secrets.token_hex(8)
    d = RUNS / run_id
    (d / "input").mkdir(parents=True, exist_ok=True)
    (d / "output").mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        if not f.filename:
            continue
        name = secure_filename(f.filename)
        if not name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            continue
        f.save(d / "input" / name)
        saved.append(name)

    if not saved:
        shutil.rmtree(d, ignore_errors=True)
        flash("No Excel files were uploaded. Expected .xlsx exports from the DMS.", "error")
        return redirect(url_for("index"))

    # Capture the loader's warnings so the page can show which file bound where.
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, rec):
            records.append(f"{rec.levelname}|{rec.getMessage()}")

    handler = Capture()
    for name in ("sales_audit.loaders", "sales_audit.audit"):
        logging.getLogger(name).addHandler(handler)

    try:
        cfg = load_config()
        data = load_all(d / "input", cfg)
        branches = branches_present(data)
        # A branch chosen on the upload form scopes this run to that outlet.
        choice = (request.form.get("branch") or "").strip()
        scope = choice if choice and choice.lower() != "all" else None
        if scope and scope not in branches:
            shutil.rmtree(d, ignore_errors=True)
            flash(f"No records for '{scope}' in the files uploaded. "
                  f"Branches found: {', '.join(branches) or 'none'}.", "error")
            return redirect(url_for("index"))
        physical, physical_source = _physical_for_run(d, cfg, scope)
        result, detail, scoped_data = _build(d, data, cfg, physical, scope)

    except FileNotFoundError as e:
        shutil.rmtree(d, ignore_errors=True)
        return render_template("error.html", title="Required input missing",
                               message=str(e), saved=saved,
                               notes=[r.split("|", 1)[1] for r in records
                                      if r.startswith("WARNING")]), 400
    except Exception as e:  # noqa: BLE001 — surface the reason, do not 500 blankly
        log.exception("audit failed")
        return render_template("error.html", title="The audit could not be completed",
                               message=f"{type(e).__name__}: {e}", saved=saved,
                               notes=[r.split("|", 1)[1] for r in records]), 500
    finally:
        for name in ("sales_audit.loaders", "sales_audit.audit"):
            logging.getLogger(name).removeHandler(handler)

    # Record the run so daily uploads build a trend, and compute movement.
    label = (request.form.get("label") or "").strip()[:40]
    if scope:
        label = (label + " · " if label else "") + scope
    move = {}
    try:
        row = history.record(result, cfg, result.context, label)
        runs = history.append(HISTORY_DIR, row)
        move = history.movement(row, history.previous(runs, row))
    except OSError:
        log.warning("history could not be written")

    # Persist a small summary so /results/<id> can be reloaded or shared.
    summary = _summarise(result, scoped_data, cfg, records, saved)
    summary["movement"] = move
    summary["branches"] = branches
    summary["scope"] = scope
    summary["physical_supplied"] = physical is not None
    summary["physical_source"] = physical_source
    summary["label"] = label
    summary["period_note"] = result.context.get("period_note")
    summary["days_covered"] = result.context.get("days_covered")
    (d / "summary.json").write_text(_dumps(summary), encoding="utf-8")
    return redirect(url_for("results", run_id=run_id))


def _find_sheet(input_dir: Path, merge_to: Path | None = None):
    """The completed Physical Audit Sheet among the uploads, or None.

    Auditing branch by branch on a phone means one exported sheet per branch,
    and people upload all of them at once. Several are merged into a single
    workbook; one is used as it is.
    """
    found = physical_mod.find_sheets(input_dir)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    target = merge_to or (input_dir.parent / "physical" / "merged.xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)
    branches = physical_mod.merge_sheets(found, target)
    if not branches:
        return found[0]
    log.info("merged %d physical audit sheets: %s", len(found), ", ".join(branches))
    return target


def _slug(name: str) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(name).strip()]
    return "".join(keep).strip("_") or "branch"


def _build(d: Path, data, cfg, physical, scope: str | None):
    """Run the audit for one scope and write its two files.

    `scope` is None for the whole network, otherwise a branch name. Each scope
    gets its own output folder so branch files never overwrite each other.
    """
    scoped = filter_branches(data, [scope]) if scope else data
    result, detail = run_audit(scoped, cfg, physical, branch_scope=scope)
    folder = d / "output" / (_slug(scope) if scope else "_network")
    folder.mkdir(parents=True, exist_ok=True)
    stem = "Sales_Process_Audit" + (f"_{_slug(scope)}" if scope else "")
    docx_path, xlsx_path = folder / f"{stem}.docx", folder / f"{stem}_Findings.xlsx"
    write_report(docx_path, result, scoped, cfg)
    write_findings(xlsx_path, result, scoped, cfg, detail)
    with zipfile.ZipFile(folder / "bundle.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(docx_path, docx_path.name)
        z.write(xlsx_path, xlsx_path.name)
    return result, detail, scoped


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, indent=2, default=str)


def _summarise(result, data, cfg, records, saved) -> dict:
    band = result.band(cfg.grading)
    bound = []
    for spec in INPUT_FILES:
        ds = data.ds(spec.ref)
        bound.append({
            "ref": spec.ref, "label": spec.label,
            "filename": ds.filename if ds else "—",
            "rows": ds.rows if ds else None,
            "status": "loaded" if ds else ("missing" if spec.required else "not supplied"),
            "required": spec.required,
        })
    return {
        "generated": datetime.now().strftime("%d-%b-%Y %H:%M"),
        "period": result.context.get("period", "—"),
        "population": result.context.get("population", "—"),
        "index": result.index,
        "band": band["band"],
        "response": band["response"],
        "action": band["action"],
        "total_score": result.total_score,
        "scorable": result.scorable_weight,
        "total_weight": result.total_weight,
        "pillars": [{
            "title": p.title, "weight": p.weight, "scorable": p.scorable_weight,
            "score": p.score, "achievement": p.achievement, "rag": p.rag(cfg.rag),
        } for p in result.pillars],
        "lines": [{
            "code": l.code, "name": l.name, "actual": l.actual_text,
            "norm": l.norm_text, "weight": round(l.weight, 1),
            "achievement": l.achievement, "score": l.score if l.scored else None,
            "rag": l.rag(cfg.rag), "remark": l.rag_remark(cfg.rag),
            "source": l.source,
        } for l in result.all_lines],
        "actions": result.actions,
        "findings": result.findings,
        "strengths": result.strengths,
        "funnel": result.context.get("funnel", []),
        "physical": result.context.get("physical_summary"),
        "bound": bound,
        "uploaded": saved,
        "notes": [r.split("|", 1)[1] for r in records if r.startswith("WARNING")],
    }


@app.route("/results/<run_id>")
def results(run_id: str):
    d = run_dir(run_id)
    import json
    path = d / "summary.json"
    if not path.exists():
        abort(404)
    summary = json.loads(path.read_text(encoding="utf-8"))
    return render_template("results.html", s=summary, run_id=run_id)


@app.route("/results/<run_id>/branch/<branch>")
def branch_results(run_id: str, branch: str):
    """Audit a single branch from the files already uploaded for this run.

    Built on demand and cached, so the first click on a branch takes a moment
    and later ones are instant.
    """
    d = run_dir(run_id)
    import json
    slug = _slug(branch)
    cached = d / f"summary_{slug}.json"
    if cached.exists():
        return render_template("results.html", s=json.loads(cached.read_text("utf-8")),
                               run_id=run_id)

    cfg = load_config()
    try:
        data = load_all(d / "input", cfg)
    except FileNotFoundError:
        abort(404)
    if branch not in branches_present(data):
        abort(404)
    physical, physical_source = _physical_for_run(d, cfg, branch)
    try:
        result, detail, scoped = _build(d, data, cfg, physical, branch)
    except Exception as e:  # noqa: BLE001
        log.exception("branch audit failed")
        return render_template("error.html", title=f"Could not audit {branch}",
                               message=f"{type(e).__name__}: {e}", saved=[], notes=[]), 500

    summary = _summarise(result, scoped, cfg, [], [])
    summary["branches"] = branches_present(data)
    summary["scope"] = branch
    summary["physical_supplied"] = physical is not None
    summary["physical_source"] = physical_source
    summary["movement"] = {}
    summary["period_note"] = result.context.get("period_note")
    summary["days_covered"] = result.context.get("days_covered")
    cached.write_text(_dumps(summary), encoding="utf-8")
    return render_template("results.html", s=summary, run_id=run_id)


@app.route("/download/<run_id>/<what>")
@app.route("/download/<run_id>/<what>/<branch>")
def download(run_id: str, what: str, branch: str | None = None):
    d = run_dir(run_id)
    folder = d / "output" / (_slug(branch) if branch else "_network")
    stem = "Sales_Process_Audit" + (f"_{_slug(branch)}" if branch else "")
    names = {
        "report": (f"{stem}.docx", f"{stem}.docx"),
        "findings": (f"{stem}_Findings.xlsx", f"{stem}_Findings.xlsx"),
        "bundle": ("bundle.zip", f"{stem}.zip"),
    }
    if what not in names:
        abort(404)
    src, download_name = names[what]
    path = folder / src
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=download_name)


@app.route("/history")
def trend():
    runs = history.load(HISTORY_DIR)
    return render_template("history.html", runs=list(reversed(runs)),
                           headers=history.TABLE_HEADERS,
                           chart=_chart_points(runs))


def _chart_points(runs) -> dict:
    """Points for the inline SVG trend, normalised to the plot box."""
    pts = [(r.get("period_end") or r.get("run_at", ""))[:10] for r in runs
           if r.get("index") is not None]
    vals = [r["index"] for r in runs if r.get("index") is not None]
    if len(vals) < 2:
        return {}
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pad = span * 0.15
    lo, hi = lo - pad, hi + pad
    # Inset the plot so the first and last markers and their labels are not
    # clipped by the viewBox edge.
    w, h, pad_x, top = 720, 170, 46, 22
    step = (w - 2 * pad_x) / (len(vals) - 1)
    coords = [(round(pad_x + i * step, 1),
               round(top + (h - top) - (v - lo) / (hi - lo) * (h - top), 1))
              for i, v in enumerate(vals)]
    return {
        "path": " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(coords)),
        "points": [{"x": x, "y": y, "v": v, "label": p}
                   for (x, y), v, p in zip(coords, vals, pts)],
        "w": w, "h": h, "lo": round(lo, 1), "hi": round(hi, 1),
    }


@app.route("/healthz")
def healthz():
    """Render pings this to confirm the service is up."""
    try:
        load_config()
    except Exception as e:  # noqa: BLE001
        return jsonify(status="error", detail=str(e)), 500
    return jsonify(status="ok", runs=len(list(RUNS.glob("*"))))


@app.errorhandler(413)
def too_large(_):
    return render_template("error.html", title="Upload too large",
                           message=f"The total upload exceeds {MAX_MB} MB. "
                                   "Upload the extracts in two goes, or trim unused "
                                   "columns from the largest file.",
                           saved=[], notes=[]), 413


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", title="Not found",
                           message="That run has expired or the link is wrong. "
                                   f"Runs are deleted after {RUN_TTL_HOURS} hours.",
                           saved=[], notes=[]), 404


def _lan_ip() -> str | None:
    """This machine's address on the local network.

    The phone has to reach the laptop by IP — 'localhost' on a phone means the
    phone. Opening a UDP socket towards a public address makes the OS pick the
    interface it would actually route over; nothing is sent.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        s.close()


def _banner(port: int) -> None:
    ip = _lan_ip()
    line = "=" * 62
    print(f"\n{line}\n  Sales Process Audit is running.\n")
    print(f"  On this laptop : http://localhost:{port}")
    if ip and not ip.startswith("127."):
        print(f"  On your phone  : http://{ip}:{port}/m")
        print("                   Same Wi-Fi as this laptop.")
        if os.name == "nt":
            print("                   If it will not open, allow Python through the")
            print("                   Windows firewall when the prompt appears.")
    else:
        print("  On your phone  : not available — this machine is not on a network.")
    print(f"\n  Branch audits  : http://localhost:{port}/branches")
    print("\n  Keep this window open while you work. Ctrl+C stops the server.")
    print(f"{line}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("AUDIT_DEBUG", "").lower() in ("1", "true", "yes")
    # Under the reloader this module runs twice; only the child should speak.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _banner(port)
        if os.environ.get("AUDIT_OPEN_BROWSER", "1") != "0":
            import webbrowser
            threading.Timer(1.5, webbrowser.open,
                            [f"http://localhost:{port}/"]).start()
    app.run(host="0.0.0.0", port=port, debug=debug)
