"""Orchestration: run every pillar, derive findings and corrective actions."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from . import exceptions as exc
from . import targets as targets_mod
from . import history, metrics, physical
from .config import Config
from .loaders import AuditData, audit_period
from .scoring import AuditResult, Pillar, pct

log = logging.getLogger(__name__)

# Owner names now live in config/norms.yaml under `owners:` so they can be
# changed without touching code. See Config.owners for the fallbacks.

def run_audit(data: AuditData, cfg: Config,
              physical_path: Path | None = None,
              branch_scope: str | None = None) -> tuple[AuditResult, pd.DataFrame]:
    # The sales target file, if supplied, drives lines 1.1, 3.1, 3.5 and 3.6.
    # Read once here rather than in each builder.
    if data.targets is None and data.get("F12") is not None:
        ds = data.ds("F12")
        try:
            data.targets = targets_mod.load_targets(
                (data.input_dir or Path(".")) / ds.filename, cfg)
        except Exception as e:  # a malformed plan must not stop the audit
            log.warning("Sales target file could not be read (%s)", e)
    if branch_scope and not data.target_branch:
        data.target_branch = branch_scope

    pillars: list[Pillar] = [b(data, cfg) for b in metrics.BUILDERS]

    # --- pillar J: physical audit --------------------------------------------
    detail = pd.DataFrame()
    if physical_path and Path(physical_path).exists():
        by_branch = physical.read_physical(physical_path)
        # A branch audit must not be scored on other outlets' showrooms. Tab
        # names are truncated to 28 characters by the template writer, so match
        # on that prefix rather than on equality.
        if branch_scope:
            key = str(branch_scope).strip()[:28].upper()
            by_branch = {k: v for k, v in by_branch.items()
                         if str(k).strip().upper() == key}
            if not by_branch:
                log.warning("Physical Audit Sheet has no tab for %s — "
                            "pillar J will not be scored for this branch", branch_scope)
        pj, detail = physical.score_physical(
            by_branch, cfg.weights["J_physical"], cfg.rag, Path(physical_path).name)
    else:
        pj, detail = physical.score_physical({}, cfg.weights["J_physical"], cfg.rag, "")
    pillars.append(pj)

    result = AuditResult(pillars=pillars)
    result.context = _build_context(data, cfg, result, detail)
    result.exceptions = {
        "branch_funnel": exc.branch_funnel(data, cfg),
        "target_branch": exc.target_vs_actual(data, cfg, "branch"),
        "target_manager": exc.target_vs_actual(data, cfg, "manager"),
        "target_unmapped": exc.unmapped_target_locations(data),
        "vap_fields": exc.vap_field_audit(data),
        "retail_exceptions": exc.retail_exceptions(data, cfg),
        "sc_exceptions": exc.sc_exceptions(data, cfg),
        "aged_enquiries": exc.aged_enquiries(data),
        "complaints": exc.complaints(data, cfg),
        "duplicate_phones": exc.duplicate_phones(data),
    }
    result.strengths = _strengths(result, data, cfg)
    result.findings = _findings(result, cfg)
    result.actions = _actions(result, cfg, detail)
    return result, detail


# -----------------------------------------------------------------------------
def _build_context(data: AuditData, cfg: Config, result: AuditResult,
                   detail: pd.DataFrame) -> dict:
    start, end = audit_period(data)
    period = (f"{start:%d-%b-%Y} to {end:%d-%b-%Y}" if start is not None and end is not None
              else "—")
    enq, td, bk, rt = data.get("F3"), data.get("F5"), data.get("F4"), data.get("F6")
    n = len(enq) if enq is not None else 0
    td_done = int(td["_completed"].sum()) if td is not None else 0
    bookings = len(bk) if bk is not None else 0
    inv = rt[rt["_invoiced"]] if rt is not None else pd.DataFrame()
    invoiced = len(inv)
    cancelled = int((~rt["_invoiced"]).sum()) if rt is not None else 0

    days = metrics.period_days(data)
    ctx: dict = {
        "period": period,
        "period_start": f"{start:%Y-%m-%d}" if start is not None else None,
        "period_end": f"{end:%Y-%m-%d}" if end is not None else None,
        "days_covered": days,
        "counts": {"enquiries": n, "test_drives": td_done,
                   "bookings": bookings, "retails": invoiced},
        "audit_date": f"{end:%d-%b-%Y}" if end is not None else "",
        "population": (f"{n:,} enquiries · {td_done:,} test drives · {bookings:,} bookings "
                       f"· {invoiced:,} retails"),
        "index_text": (f"{result.index:.1f} / 100" if result.index is not None else "not scored"),
    }
    if days and days < 28:
        ctx["period_note"] = (
            f"This extract covers {days} day(s), not a full month. Rate-based norms "
            f"(conversion percentages) are unaffected. Monthly volume norms — retail "
            f"target, productivity per consultant, order bank cover — have been "
            f"pro-rated to the period, and are marked as such on the line.")
    ctx["scope_text"] = (
        f"{period}, {len(cfg.branches)} branches, full population across every funnel stage: "
        f"{ctx['population']}. {result.scorable_weight:.1f} of {result.total_weight:g} weightage "
        f"points were auditable from the data supplied; the remainder are listed as not scored "
        f"with the missing file named.")

    # funnel table
    delnote = int(inv["_delnote_dt"].notna().sum()) if invoiced else 0
    ctx["funnel"] = [
        ["Enquiries received", "—", f"{n:,}", "—", "—", "Denominator for the funnel"],
        ["Test drives completed",
         f"Walk-in ≥{cfg.norm('td_conversion_walkin_pct')}% / Digital ≥{cfg.norm('td_conversion_digital_pct')}%",
         f"{td_done:,}", f"{len(td) - td_done:,} cancelled or open" if td is not None else "—",
         f"{pct(td_done, n)}% of enquiries", "Against source-wise norms"],
        ["Bookings created", f"≥{cfg.norm('td_to_booking_pct')}% of test drives",
         f"{bookings:,}", "—", f"{pct(bookings, td_done)}% of completed TDs", ""],
        ["Retails invoiced", f"≥{cfg.norm('booking_to_retail_pct')}% of bookings",
         f"{invoiced:,}", f"{cancelled:,} invoices cancelled",
         f"{pct(invoiced, bookings)}% of bookings",
         f"{bookings - invoiced:,} bookings unconverted"],
        ["Net enquiry to retail", f"≥{cfg.norm('enquiry_to_retail_pct')}%", "—", "—",
         f"{pct(invoiced, n)}%", "End-to-end conversion"],
        ["Delivery notes raised", f"{cfg.norm('delivery_note_pct')}% of invoices",
         f"{delnote:,}", f"{invoiced - delnote:,} missing", f"{pct(delnote, invoiced)}%", ""],
    ]

    # branch notes: worst three on net conversion
    bf = exc.branch_funnel(data, cfg)
    if not bf.empty and "Enq→Retail %" in bf.columns:
        body = bf[bf["Branch"] != "TOTAL"].copy()
        body = body[body["Enquiries"] >= 20].sort_values("Enq→Retail %")
        ctx["branch_notes"] = [
            (f"{r['Branch']} — {int(r['Enquiries']):,} enquiries, "
             f"{int(r['TD completed']):,} test drives, {int(r['Retails']):,} retails "
             f"({r['Enq→Retail %']}% net conversion)")
            for _, r in body.head(4).iterrows()]

    # physical summary rows for the report
    if not detail.empty:
        rows = []
        for section in physical.SECTIONS:
            sub = detail[(detail["section"] == section) & detail["possible"].notna()]
            if sub.empty:
                continue
            fails = sub[sub["points"] == 0]
            ach = pct(sub["earned"].sum(), sub["possible"].sum())
            crit = fails[fails["critical"]]
            note = (f"{len(crit)} critical item(s) failed" if len(crit)
                    else ("all items compliant" if fails.empty else f"{len(fails)} item(s) failed"))
            rows.append([section, str(len(sub)), str(len(fails)), f"{ach:.0f}%", note])
        ctx["physical_summary"] = rows
    return ctx


def _strengths(result: AuditResult, data: AuditData, cfg: Config) -> list[str]:
    out = []
    for l in sorted([l for l in result.all_lines if l.scored],
                    key=lambda l: -l.achievement)[:5]:
        if l.achievement >= cfg.rag["amber_min"]:
            out.append(f"{l.name} — {l.actual_text} ({l.achievement:.0f}% of norm). "
                       f"[{l.code}]")
    rt = data.get("F6")
    if rt is not None and "Booking No." in rt.columns:
        inv = rt[rt["_invoiced"]]
        share = pct(inv["Booking No."].notna().sum(), len(inv))
        if share >= 99:
            out.append(f"Booking-to-retail chain fully traceable: booking number present on "
                       f"{share}% of invoices.")
    return out or ["No line reached the Amber threshold in this cycle."]


def _findings(result: AuditResult, cfg: Config) -> list[str]:
    out = []
    for l in sorted([l for l in result.all_lines if l.scored],
                    key=lambda l: l.achievement):
        if l.achievement >= cfg.rag["amber_min"]:
            continue
        out.append(f"{l.name} — {l.actual_text}. Norm: {l.norm_text}. "
                   f"Achievement {l.achievement:.0f}%. [{l.code}, source: {l.source}]")
    return out[:12]


def _actions(result: AuditResult, cfg: Config, detail: pd.DataFrame) -> list[dict]:
    """Every line below the Amber threshold becomes a corrective action."""
    owners = cfg.owners
    actions: list[dict] = []
    for l in sorted([l for l in result.all_lines if l.scored], key=lambda l: l.achievement):
        if l.achievement >= cfg.rag["amber_min"]:
            continue
        actions.append({
            "pillar": l.pillar.split("_", 1)[-1].replace("_", " ").title(),
            "gap": f"{l.name}: {l.actual_text}",
            "action": _suggest(l.code, l.name),
            "owner": owners.get(l.pillar, owners["default"]),
            "target": "",
            "source": l.source,
        })
    # critical physical failures always raise an action, even if the section scored
    if not detail.empty:
        crit = physical.critical_failures(detail)
        for _, r in crit.iterrows():
            actions.append({
                "pillar": "Physical",
                "gap": f"{r['branch']} — {r['item']} answered No"
                       + (f": {r['observation']}" if r["observation"] else ""),
                "action": f"Restore compliance: {r['what']}",
                "owner": owners.get("J_physical", owners["default"]),
                "target": "",
                "source": "Physical audit checklist",
            })
    return actions


SUGGESTIONS = {
    "1.2": "Rebalance lead spend towards digital and referral; launch a structured referral "
           "scheme with partner names captured in DMS",
    "1.3": "Make assign date a system-stamped, non-editable field; publish a daily "
           "unassigned-lead exception report to sales managers",
    "1.4": "Reconcile the gate register against DMS daily; investigate back-dated entries",
    "1.5": "Restrict the generic loss code; require a sub-reason and a manager countersign",
    "2.1": "Record the test drive offer with an accept or decline reason on every enquiry; "
           "daily TD board by consultant; verify demo availability per branch",
    "2.2": "Quotation to be raised before the customer leaves after a test drive",
    "2.3": "Age the open order bank weekly; every booking beyond 30 days to carry a reason "
           "and a revised committed date",
    "2.4": "Address the weakest funnel stage first; review conversion by consultant weekly",
    "2.5": "Re-contact the aged list; revive or close each enquiry with a reason code",
    "3.2": "Build the order bank through pre-booking campaigns on fast-moving variants",
    "4.2": "Counsel the nil-retail consultants; reallocate enquiries from non-performers",
    "5.3": "Investigate units breaching the allotment-to-invoice window",
    "6.2": "Establish whether SHIELD is unsold or sold outside the DMS; reconcile the SHIELD "
           "portal against the retail register and make Shield Reg ID mandatory before "
           "invoice release",
    "6.3": "Establish whether RSA is unsold or sold outside the DMS; reconcile the RSA portal "
           "against the retail register and make RSA Reg ID mandatory before invoice release",

    "6.5": "Review financier tie-ups and payout; set a per-consultant finance target",
    "6.6": "Correct the insurer master so the actual company is selected; make insurance "
           "amount mandatory at invoice",
    "6.7": "Make exchange evaluation mandatory at enquiry; track evaluations per branch weekly",
    "7.4": "No vehicle to leave the yard without a delivery note raised in DMS",
    "7.5": "Make the promised delivery date mandatory at booking; publish a weekly "
           "overdue-delivery report by branch",
    "8.1": "Make qualification fields mandatory before an enquiry can be saved; weekly "
           "sample audit of 20 enquiries per branch",
    "8.2": "Reinstate the daily follow-up calendar review in the morning meeting",
    "9.1": "Tag the demo vehicle in DMS so every test drive records the vehicle used",
}


def _suggest(code: str, name: str) -> str:
    if code in SUGGESTIONS:
        return SUGGESTIONS[code]
    if code.startswith("J"):
        return f"Restore compliance across the {name.lower()} checklist items"
    return f"Root-cause review of {name.lower()} with the branch managers; agree a "
