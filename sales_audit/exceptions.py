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
        k = int((bk["_branch"] == b).sum()) if bk is not None else 0
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
        "Bookings": len(bk) if bk is not None else 0, "Retails": len(inv),
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
             ("F10", "30-day", "Ratings", "Customer Comment", "Sales Consultant Contact")]
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
