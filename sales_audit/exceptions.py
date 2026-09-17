"""Exception lists — the actionable half of the audit.

A score tells a manager how bad it is. An exception list tells them which
twelve invoices, which four consultants and which branch to go and fix. Every
table here is built so it can be handed to a named owner unmodified.
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .loaders import AuditData
from .scoring import pct


def _stage_actuals(data: AuditData, by: str) -> dict:
    """Actual counts per branch or per sales manager, all four stages.

    `by` is "_branch" or "_sm". The manager column is the DMS Sales Manager;
    the plan names managers its own way, so the two are matched on a
    case-folded exact name and anything unmatched is shown rather than hidden.
    """
    enq, td, bk, rt = data.get("F3"), data.get("F5"), data.get("F4"), data.get("F6")
    inv = rt[rt["_invoiced"]] if rt is not None and "_invoiced" in rt.columns else None
    out: dict = {}

    def add(df, stage, mask=None):
        if df is None or by not in df.columns:
            return
        d = df if mask is None else df[mask]
        for k, v in d.groupby(by).size().items():
            key = str(k).strip()
            if not key or key.lower() == "nan":
                continue
            out.setdefault(key, {}).setdefault(stage, 0)
            out[key][stage] += int(v)

    add(enq, "enquiry")
    add(td, "test_drive", td["_completed"] if td is not None and "_completed" in td else None)
    add(bk, "booking", bk["_booked"] if bk is not None and "_booked" in bk else None)
    add(inv, "retail")

    # A stage whose key column is entirely blank cannot be attributed. The
    # retail extract's Team Lead column is routinely empty, and reporting
    # every manager at 0% retail would read as total failure rather than as
    # the missing attribution it is.
    available = set()
    for df, stage, in ((enq, "enquiry"), (td, "test_drive"),
                       (bk, "booking"), (inv, "retail")):
        if df is not None and by in df.columns:
            col_ = df[by].astype(str).str.strip()
            if (col_.notna() & (col_ != "") & (col_.str.lower() != "nan")).any():
                available.add(stage)
    return out, available


def target_vs_actual(data: AuditData, cfg: Config, by: str = "branch") -> pd.DataFrame:
    """Plan against achievement, by branch or by sales manager.

    Targets are monthly and scaled to each extract's own period, exactly as
    the scored lines are, so the percentages here and on the scorecard agree.
    """
    from .metrics import stage_months
    from .targets import STAGES

    book = getattr(data, "targets", None)
    if book is None or book.empty:
        return pd.DataFrame()

    key_col = "_branch" if by == "branch" else "_sm"
    actuals, available = _stage_actuals(data, key_col)
    variants: dict = {}
    if by == "manager":
        from .targets import merge_dms_variants
        actuals, variants = merge_dms_variants(actuals)
    scope = getattr(data, "target_branch", None)

    if by == "branch":
        keys = book.branches()
        if scope:
            keys = [k for k in keys if k.upper() == scope.upper()]
        resolved = {k: k for k in keys}
        why = {}
    else:
        from .targets import match_manager
        keys = book.managers(scope)
        dms_names = sorted(actuals)
        overrides = cfg.raw.get("target_managers") or {}
        resolved, why = {}, {}
        for k in keys:
            hit, reason = match_manager(k, dms_names, overrides)
            resolved[k] = hit
            why[k] = reason
        # Two plan names resolving to one DMS manager means the plan lists the
        # same person twice. Their actuals would otherwise be counted against
        # each row, so both are flagged rather than quietly double-counted.
        seen: dict = {}
        for k, hit in resolved.items():
            if hit:
                seen.setdefault(hit, []).append(k)
        for hit, names in seen.items():
            if len(names) > 1:
                for k in names:
                    others = [n for n in names if n != k]
                    why[k] += (f" — plan also lists {', '.join(others)} against "
                               f"this manager; actuals shown are the combined "
                               f"figure, targets are not")

    months = {s: stage_months(data, s) for s in STAGES.values()}
    rows = []
    for k in keys:
        rec = {"Branch" if by == "branch" else "Manager": k}
        if by == "manager":
            rec["Location(s)"] = ", ".join(sorted(
                {r.location for r in book.rows if r.manager == k and r.branch
                 and (not scope or r.branch.upper() == scope.upper())}))
        got = actuals.get(resolved.get(k) or "", {})
        if by == "manager" and resolved.get(k) and not any(
                r.manager == k and r.branch for r in book.rows):
            why[k] += " — plan location not mapped, so no target is scored"
        if by == "manager":
            rec["DMS name"] = resolved.get(k) or "—"
            alt = sorted({v for v, c in variants.items()
                          if c == resolved.get(k) and v != c})
            rec["Match"] = (why.get(k, "")
                            + (f" (also spelled {'; '.join(alt)})" if alt else ""))
        for stage in STAGES.values():
            monthly = book.total(stage, k if by == "branch" else None,
                                 None if by == "branch" else k)
            monthly = monthly / max(len(book.months), 1)
            m = months.get(stage) or 1.0
            tgt = round(monthly * m, 1)
            label = stage.replace("_", " ").title()
            rec[f"{label} target"] = tgt
            if stage not in available:
                rec[f"{label} actual"] = "not attributable"
                rec[f"{label} %"] = None
            else:
                act = int(got.get(stage, 0))
                rec[f"{label} actual"] = act
                rec[f"{label} %"] = pct(act, tgt) if tgt else None
        rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # A manager in the DMS but absent from the plan still needs to be seen.
    if by == "manager":
        planned = {(resolved.get(k) or "").upper() for k in keys}
        extra = [k for k in actuals if k.upper() not in planned]
        extra_rows: list[dict] = []
        for k in sorted(extra):
            rec = {"Manager": k, "Location(s)": "not in plan",
                   "DMS name": k, "Match": "in DMS, absent from plan"}
            for stage in STAGES.values():
                label = stage.replace("_", " ").title()
                rec[f"{label} target"] = None
                rec[f"{label} actual"] = (int(actuals[k].get(stage, 0))
                                          if stage in available else "not attributable")
                rec[f"{label} %"] = None
            # Build the frame from a list rather than growing it row by row:
            # concatenating an all-NA frame is deprecated in pandas 2.2.
            extra_rows.append(rec)
    if by == "manager" and extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows).reindex(columns=df.columns)],
                       ignore_index=True)
    return df


def unmapped_target_locations(data: AuditData) -> pd.DataFrame:
    """Plan locations with no DMS branch, and what they were carrying."""
    book = getattr(data, "targets", None)
    if book is None or book.empty or not book.unmapped:
        return pd.DataFrame()
    n = max(len(book.months), 1)
    rows = []
    for loc in book.unmapped:
        rs = [r for r in book.rows if r.location == loc]
        rows.append({
            "Plan location": loc,
            "Managers": ", ".join(sorted({r.manager for r in rs if r.manager})),
            "Enquiry target": round(sum(r.enq for r in rs) / n, 1),
            "Test Drive target": round(sum(r.test_drive for r in rs) / n, 1),
            "Booking target": round(sum(r.booking for r in rs) / n, 1),
            "Retail target": round(sum(r.retail for r in rs) / n, 1),
            "Status": "No DMS branch mapped - target excluded from scoring",
        })
    return pd.DataFrame(rows)


def branch_funnel(data: AuditData, cfg: Config) -> pd.DataFrame:
    """Full funnel by branch, one row per outlet."""
    enq, td, bk, rt = data.get("F3"), data.get("F5"), data.get("F4"), data.get("F6")
    if enq is None or "_branch" not in enq.columns:
        return pd.DataFrame()
    inv = rt[rt["_invoiced"]] if rt is not None else pd.DataFrame()
    rows = []
    for b in sorted(enq["_branch"].dropna().unique()):
        e = int((enq["_branch"] == b).sum())
        t = int(((td["_branch"] == b) & td["_completed"]).sum()) if td is not None else 0
        k = (int(((bk["_branch"] == b) & bk["_booked"]).sum())
             if bk is not None and "_booked" in bk
             else (int((bk["_branch"] == b).sum()) if bk is not None else 0))
        r = int((inv["_branch"] == b).sum()) if len(inv) else 0
        sc = enq.loc[enq["_branch"] == b, "_sc"].nunique()
        rows.append({
            "Branch": b, "SCs": sc, "Enquiries": e, "TD completed": t,
            "Enq→TD %": pct(t, e), "Bookings": k, "TD→Book %": pct(k, t),
            "Retails": r, "Book→Retail %": pct(r, k), "Enq→Retail %": pct(r, e),
            "Retails per SC": round(r / sc, 1) if sc else 0.0,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    total = {
        "Branch": "TOTAL", "SCs": enq["_sc"].nunique(), "Enquiries": len(enq),
        "TD completed": int(td["_completed"].sum()) if td is not None else 0,
        "Bookings": (int(bk["_booked"].sum()) if bk is not None and "_booked" in bk
                     else (len(bk) if bk is not None else 0)),
        "Retails": len(inv),
    }
    total["Enq→TD %"] = pct(total["TD completed"], total["Enquiries"])
    total["TD→Book %"] = pct(total["Bookings"], total["TD completed"])
    total["Book→Retail %"] = pct(total["Retails"], total["Bookings"])
    total["Enq→Retail %"] = pct(total["Retails"], total["Enquiries"])
    total["Retails per SC"] = round(total["Retails"] / total["SCs"], 1) if total["SCs"] else 0.0
    return pd.concat([df, pd.DataFrame([total])], ignore_index=True)


def vap_field_audit(data: AuditData) -> pd.DataFrame:
    """Field-by-field population of the VAP, RSA and EW columns on invoices."""
    rt = data.get("F6")
    if rt is None or "_invoiced" not in rt.columns:
        return pd.DataFrame()
    inv = rt[rt["_invoiced"]]
    n = len(inv)
    if not n:
        return pd.DataFrame()
    fields = [
        ("RSA Scheme Reg ID", "RSA policy registered against the VIN", True),
        ("RSA Scheme Amount", "RSA value collected", True),
        ("Shield Scheme Reg ID", "SHIELD (extended warranty) registered", True),
        ("Shield Scheme Amount", "SHIELD (extended warranty) value", True),
        ("Total Accessories Amount", "Accessory revenue on the invoice", True),
        ("Insurance Amount", "Insurance value", True),
        ("Finance Amount", "Finance value", True),
        ("Initial Promised Delivery date", "Delivery commitment to the customer", True),
        ("Mitra Name", "Referral partner", False),
        ("Delivery Note Date", "Delivery note raised", True),
        ("Financier", "Financier named", False),
        ("Booking No.", "Booking traceable from the invoice", False),
    ]
    rows = []
    for col, meaning, critical in fields:
        if col not in inv.columns:
            rows.append({"Retail field": col, "Meaning": meaning, "Populated": 0,
                         "Invoiced units": n, "Fill rate %": 0.0,
                         "RAG": "Red", "Remark": "Column absent from the extract"})
            continue
        filled = int(inv[col].notna().sum())
        rate = pct(filled, n)
        rag = "Green" if rate >= 90 else "Amber" if rate >= 50 else "Red"
        if rate == 0 and critical:
            remark = "Nil on every invoice — product unsold or sold outside the DMS"
        elif rate < 50:
            remark = f"{n - filled} invoices with no value"
        else:
            remark = "Working"
        rows.append({"Retail field": col, "Meaning": meaning, "Populated": filled,
                     "Invoiced units": n, "Fill rate %": rate, "RAG": rag,
                     "Remark": remark})
    return pd.DataFrame(rows)


def retail_exceptions(data: AuditData, cfg: Config) -> pd.DataFrame:
    """Invoices breaching a delivery control."""
    rt = data.get("F6")
    if rt is None or "_invoiced" not in rt.columns:
        return pd.DataFrame()
    inv = rt[rt["_invoiced"]].copy()
    inv["_b2i"] = (inv["_invoice_dt"] - inv["_booking_dt"]).dt.days
    limit = cfg.norm("booking_to_invoice_days")
    mask = (inv["_b2i"] > limit) | inv["_delnote_dt"].isna()
    exc = inv[mask].copy().sort_values("_b2i", ascending=False)
    rows = []
    for _, r in exc.iterrows():
        flags = []
        if pd.notna(r["_b2i"]) and r["_b2i"] > limit:
            flags.append(f"Booking→invoice > {limit} days")
        if pd.isna(r["_delnote_dt"]):
            flags.append("No delivery note")
        if "RSA Scheme Reg ID" in exc.columns and pd.isna(r.get("RSA Scheme Reg ID")):
            flags.append("No RSA")
        rows.append({
            "Invoice number": r.get("Invoice Number", ""),
            "Branch": r["_branch"], "Sales consultant": r["_sc"],
            "Customer": str(r.get("Customer Name", ""))[:35],
            "Model": r.get("Model Group", ""),
            "Invoice date": r["_invoice_dt"].strftime("%d-%b-%Y") if pd.notna(r["_invoice_dt"]) else "",
            "Booking→invoice days": int(r["_b2i"]) if pd.notna(r["_b2i"]) else "",
            "Exception": "; ".join(flags),
        })
    return pd.DataFrame(rows)


def sc_exceptions(data: AuditData, cfg: Config) -> pd.DataFrame:
    """Consultants below the productivity or conversion norm."""
    enq, rt = data.get("F3"), data.get("F6")
    if enq is None or "_branch" not in enq.columns:
        return pd.DataFrame()
    inv = rt[rt["_invoiced"]] if rt is not None else pd.DataFrame()
    g = enq.groupby(["_branch", "_sc"]).agg(
        enquiries=("_sc", "size"),
        td=("_td_done", "sum"),
        zero_fup=("_followups", lambda s: int((s == 0).sum())),
    ).reset_index()
    retail_counts = inv.groupby("_sc").size() if len(inv) else pd.Series(dtype=int)
    g["retails"] = g["_sc"].map(retail_counts).fillna(0).astype(int)
    g["TD %"] = (g["td"] / g["enquiries"] * 100).round(1)
    g["Zero follow-up %"] = (g["zero_fup"] / g["enquiries"] * 100).round(1)
    norm = cfg.norm("retails_per_sc_per_month")
    exc = g[(g["enquiries"] >= 20) & ((g["TD %"] < 20) | (g["retails"] < norm / 2))]
    exc = exc.sort_values("enquiries", ascending=False)
    return exc.rename(columns={"_branch": "Branch", "_sc": "Sales consultant",
                               "enquiries": "Enquiries", "td": "TD completed",
                               "retails": "Retails"})[
        ["Branch", "Sales consultant", "Enquiries", "TD completed", "TD %",
         "Retails", "Zero follow-up %"]]


def aged_enquiries(data: AuditData) -> pd.DataFrame:
    """Live enquiries ageing beyond the working threshold."""
    enq = data.get("F3")
    if enq is None or "Stage" not in enq.columns or "_enq_dt" not in enq.columns:
        return pd.DataFrame()
    live = enq[enq["Stage"].isin(["Enquiry", "Quotation", "Test Drive"])].copy()
    if live.empty or live["_enq_dt"].isna().all():
        return pd.DataFrame()
    as_on = enq["_enq_dt"].max()
    live["Age (days)"] = (as_on - live["_enq_dt"]).dt.days
    span = (enq["_enq_dt"].max() - enq["_enq_dt"].min()).days
    thresh = 30 if span > 45 else 15
    out = live[live["Age (days)"] > thresh].sort_values("Age (days)", ascending=False)
    cols = {"Enquiry Number": "Enquiry number", "_branch": "Branch", "_sc": "Sales consultant",
            "Enquiry Date": "Enquiry date", "Stage": "Stage", "Product Family": "Product",
            "Enquiry Type": "Source", "Age (days)": "Age (days)",
            "_followups": "Follow-ups done", "Next Planned Followup": "Next planned"}
    keep = [c for c in cols if c in out.columns]
    return out[keep].rename(columns=cols)


def complaints(data: AuditData, cfg: Config) -> pd.DataFrame:
    """All complaints across stages, flagged in-scope or not."""
    branches = set(cfg.branches)
    rows = []
    specs = [("F7", "Enquiry", "Rating", "Comment", "Sales Consultant Name"),
             ("F8", "Test drive", "Rating", "Comment", "Sales Consultant Name"),
             ("F9", "Delivery", "Response", "Customer Comment", "Sales Consultant Contact"),
             ("F10", "30-day", "Ratings", "Customer Comment", "Sales Consultant Contact"),
             # Both of these are surveys sent after the customer walked away,
             # so they say why rather than how it felt. That makes them the
             # only direct evidence behind a lost enquiry or a cancelled
             # booking, which the DMS records only as a reason code.
             ("F13", "Lost enquiry", "Response 1", "Response 2",
              "Sales Consultant Name"),
             ("F14", "Booking cancelled", "Booking Cancellation Response",
              "Customer Comment", "Sales Consultant Name")]
    for ref, stage, rating_col, comment_col, sc_col in specs:
        df = data.get(ref)
        if df is None:
            continue
        ds = data.ds(ref)
        for _, r in df.iterrows():
            rows.append({
                "Stage": stage,
                "Location": r.get("_branch", ""),
                "Scope": "In scope" if r.get("_branch") in branches else "Other outlet",
                "Product": r.get("Product", ""),
                "Rating / response": str(r.get(rating_col, ""))[:120],
                "Customer comment": str(r.get(comment_col, "") or "")[:200],
                "Sales consultant": r.get(sc_col, ""),
                "Source file": ds.filename if ds else "",
            })
    return pd.DataFrame(rows)


def duplicate_phones(data: AuditData) -> pd.DataFrame:
    """Same mobile number worked by more than one consultant."""
    enq = data.get("F3")
    if enq is None or "Customer Phone" not in enq.columns:
        return pd.DataFrame()
    ph = enq["Customer Phone"].astype(str).str.replace(r"\D", "", regex=True)
    enq = enq.assign(_phone=ph)
    rep = ph.value_counts()
    dups = enq[enq["_phone"].isin(rep[rep > 1].index) & (ph.str.len() == 10)]
    if dups.empty:
        return pd.DataFrame()
    g = dups.groupby("_phone").agg(
        Names=("Customer Name", lambda s: " | ".join(sorted(set(s.astype(str))))[:70]),
        Enquiries=("_phone", "size"),
        Consultants=("_sc", "nunique"),
        Branches=("_branch", "nunique"),
        Stages=("Stage", lambda s: ", ".join(sorted(set(s.astype(str))))),
    ).reset_index().rename(columns={"_phone": "Customer phone"})
    g["Risk"] = g["Consultants"].apply(
        lambda c: "Red — worked by multiple consultants" if c > 1 else "Amber — repeat enquiry")
    return g.sort_values(["Consultants", "Enquiries"], ascending=False)
