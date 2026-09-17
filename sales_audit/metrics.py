"""Compute every data-driven audit line from the loaded extracts.

Each `build_pillar_*` function returns a Pillar. A line is only scored when
both the actual and the norm are available; otherwise it carries an explicit
`unscored_reason` so the report can say why rather than showing a blank.
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .loaders import AuditData
from .scoring import Line, Pillar, achievement, pct
from .targets import months_covered, stage_target

NOT_SUPPLIED = "source data not supplied"

# Each funnel stage is judged against its own extract's period. The enquiry
# book routinely spans three months while retails span one, and a monthly
# target compared against a three-month count would fail a branch that is
# actually on plan.
STAGE_SOURCE = {
    "enquiry": ("F3", "_enq_dt"),
    "test_drive": ("F5", "_created_dt"),
    "booking": ("F4", "_created_dt"),
    "retail": ("F6", "_invoice_dt"),
}


def stage_window(data: AuditData, stage: str):
    """(first, last) date covered by the extract governing a stage."""
    import pandas as pd
    ref, col = STAGE_SOURCE[stage]
    df = data.get(ref)
    if df is None or col not in df.columns:
        return None
    sr = df[col].dropna()
    if sr.empty:
        return None
    counts = sr.dt.to_period("M").value_counts()
    keep = counts[counts >= max(1, len(sr) * 0.02)].index
    sr = sr[sr.dt.to_period("M").isin(keep)]
    return (sr.min(), sr.max()) if not sr.empty else None


def periods_overlap(data: AuditData, a: str, b: str) -> float | None:
    """How much two stages' extracts cover the same days, 0.0 to 1.0.

    Conversion between two stages is only meaningful when both extracts
    describe the same period. Comparing September bookings against August
    invoices produces a number above 100% and no insight at all.
    """
    wa, wb = stage_window(data, a), stage_window(data, b)
    if not wa or not wb:
        return None
    start, end = max(wa[0], wb[0]), min(wa[1], wb[1])
    if end < start:
        return 0.0
    shared = (end.normalize() - start.normalize()).days + 1
    span = max((wa[1].normalize() - wa[0].normalize()).days + 1,
               (wb[1].normalize() - wb[0].normalize()).days + 1)
    return round(shared / span, 3) if span else None


def stage_months(data: AuditData, stage: str) -> float | None:
    """Months covered by the extract that governs this stage."""
    ref, col = STAGE_SOURCE[stage]
    return months_covered(data.get(ref), col)


def stage_off_period(data: AuditData, stage: str) -> str:
    """Why this stage's extract does not describe the audit month, if so.

    An enquiry file full of September rows cannot answer an August target.
    Scoring it anyway would put the wrong month's number under the right
    month's heading, which is worse than leaving the line unscored.
    """
    notes = getattr(data, "period_notes", None) or {}
    period = getattr(data, "period", None)
    if not notes or period is None:
        return ""
    ref = STAGE_SOURCE[stage][0]
    note = notes.get(ref)
    if not note or not note.get("empty"):
        return ""
    covers = ", ".join(note.get("months", {})) or "no dated rows"
    return (f"{note['file']} has no {period.label} rows (it covers {covers}), "
            f"so this cannot be judged against the {period.label} target")


def target_for(data: AuditData, stage: str,
               branch: str | None = None) -> tuple[float | None, str]:
    """Target for a stage from the plan, scaled to its extract's period."""
    off = stage_off_period(data, stage)
    if off:
        return None, off
    book = getattr(data, "targets", None)
    return stage_target(book, stage, stage_months(data, stage), branch)


def _target_branch(data: AuditData) -> str | None:
    """The branch this run is scoped to, if any.

    A branch-scoped run must be judged against that branch's slice of the
    plan, not the whole network's.
    """
    return getattr(data, "target_branch", None)


def period_days(data: AuditData) -> int | None:
    """Calendar days covered by the enquiry extract, inclusive."""
    enq = data.get("F3")
    if enq is None or "_enq_dt" not in enq.columns:
        return None
    s = enq["_enq_dt"].dropna()
    if s.empty:
        return None
    return int((s.max().normalize() - s.min().normalize()).days) + 1


def prorate(norm: float | None, data: AuditData, cfg: Config) -> tuple[float | None, str]:
    """Scale a per-month norm to the days the extract actually covers.

    Returns (scaled_norm, note). A None norm means the line should not be
    scored: either the norm is missing, or the period is too short to judge a
    monthly volume target fairly.
    """
    if norm is None:
        return None, ""
    days = period_days(data)
    p = cfg.period
    if not days or not p.get("prorate_volume_norms", True):
        return norm, ""
    if days >= 28:
        return norm, ""
    if days < int(p.get("min_days_to_score_volume", 7)):
        return None, (f"period covers {days} day(s); a monthly volume norm cannot be "
                      f"judged on fewer than {p.get('min_days_to_score_volume', 7)} days")
    enq = data.get("F3")
    dim = enq["_enq_dt"].dropna().max().days_in_month
    scaled = round(norm * days / dim, 2)
    return scaled, f"norm pro-rated to {days} of {dim} days: {scaled:g}"


def _fill_pct(df: pd.DataFrame | None, column: str) -> float | None:
    """Percentage of non-blank values in a column."""
    if df is None or column not in df.columns:
        return None
    return pct(df[column].notna().sum(), len(df))


def _blank_pct(df: pd.DataFrame | None, column: str) -> float | None:
    f = _fill_pct(df, column)
    return None if f is None else round(100 - f, 1)


# =============================================================================
# A. Demand generation and enquiry health
# =============================================================================
def build_pillar_a(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("A_enquiry", "A. Demand generation and enquiry health",
               cfg.weights["A_enquiry"])
    enq = data.get("F3")
    n = len(enq) if enq is not None else 0

    # --- 1.1 enquiry volume vs target ---------------------------------------
    target = cfg.target("enquiry_target")
    line = Line("1.1", p.key, "Enquiry volume against target",
                "Enquiries punched in DMS against the month's target",
                "100% of target", weight=p.weight * 0.27,
                source=data.src("F3", "Enquiry Number", "Enquiry Date"))
    months = stage_months(data, "enquiry")
    line.actual_text = (f"{n:,} enquiries in the audit period"
                        + (f" ({months:.2f} months)" if months else ""))

    plan, plan_note = target_for(data, "enquiry", _target_branch(data))
    if plan:
        line.achievement = achievement(n, plan)
        line.remark = plan_note or f"target {plan:,.0f}"
        line.source = (line.source + "  |  " + getattr(data, "targets").filename
                       if getattr(data, "targets", None) else line.source)
    else:
        scaled, note = prorate(target, data, cfg)
        if scaled:
            line.achievement = achievement(n, scaled)
            line.remark = note or f"target {target:,}"
        elif target:
            line.unscored_reason = note or "period too short to judge the monthly target"
        else:
            line.unscored_reason = (plan_note or
                                    "no sales target supplied - add sales_target.xlsx "
                                    "or set enquiry_target in norms.yaml")
    p.lines.append(line)

    # --- 1.2 source mix -------------------------------------------------------
    line = Line("1.2", p.key, "Enquiry source mix",
                "Split of walk-in, digital, field, telephone and referral",
                f"Digital ≥{cfg.norm('digital_share_pct')}%, referral "
                f"≥{cfg.norm('referral_share_pct')}%, no source "
                f">{cfg.norm('max_single_source_pct')}%",
                weight=p.weight * 0.18,
                source=data.src("F3", "Enquiry Type", "Enquiry Source"))
    if enq is not None and "Enquiry Type" in enq.columns:
        mix = enq["Enquiry Type"].value_counts(normalize=True).mul(100).round(1)
        digital = float(mix.get("Digital", 0.0))
        top_share = float(mix.iloc[0]) if len(mix) else 0.0
        top_name = str(mix.index[0]) if len(mix) else "—"
        line.actual_text = "; ".join(f"{k} {v}%" for k, v in mix.items())
        # Score is the mean of the three sub-norms.
        a_digital = achievement(digital, cfg.norm("digital_share_pct")) or 0
        a_conc = 100.0 if top_share <= cfg.norm("max_single_source_pct") else round(
            cfg.norm("max_single_source_pct") / top_share * 100, 1)
        line.achievement = round((a_digital + a_conc) / 2, 1)
        breaches = []
        if digital < cfg.norm("digital_share_pct"):
            breaches.append(f"digital {digital}% below {cfg.norm('digital_share_pct')}%")
        if top_share > cfg.norm("max_single_source_pct"):
            breaches.append(f"{top_name} {top_share}% exceeds concentration cap")
        line.remark = "; ".join(breaches) if breaches else "mix within norm"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # --- 1.3 lead response TAT ------------------------------------------------
    line = Line("1.3", p.key, "Lead response TAT",
                "Enquiry creation to consultant assignment",
                f"First contact within {cfg.norm('lead_response_minutes')} minutes",
                weight=p.weight * 0.27,
                source=data.src("F3", "Enq Assign Date", "Enquiry Date"))
    if enq is not None and "_assign_dt" in enq.columns:
        blank = enq["_assign_dt"].isna().sum()
        lag = (enq["_assign_dt"] - enq["_enq_dt"]).dt.total_seconds().div(60).dropna()
        negative = int((lag < 0).sum())
        within = int((lag.between(0, cfg.norm("lead_response_minutes"))).sum())
        line.actual_text = (
            f"Assign date blank on {blank:,} of {n:,} ({pct(blank, n)}%). "
            f"Of {len(lag):,} dated records, {negative:,} ({pct(negative, len(lag))}%) "
            f"show assignment before enquiry creation")
        # Achievement = share of the whole book that is both dated and on time.
        line.achievement = pct(within, n) if n else None
        line.remark = ("field unusable as evidence" if blank / max(n, 1) > 0.5
                       else "measurable but incomplete")
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # --- 1.4 same-day capture -------------------------------------------------
    line = Line("1.4", p.key, "Same-day enquiry capture",
                "Walk-in register reconciled against DMS punching",
                "100% punched same day, nil back-dating",
                weight=p.weight * 0.10,
                source=data.src("F3", "Enquiry Date", "Enq Assign Date"))
    if enq is not None and "_assign_dt" in enq.columns:
        lag = (enq["_assign_dt"] - enq["_enq_dt"]).dt.total_seconds().dropna()
        negative = int((lag < 0).sum())
        late = int((enq["_enq_dt"].dt.hour >= 20).sum()) if "_enq_dt" in enq else 0
        line.actual_text = (f"{negative:,} back-dated assignments; {late:,} "
                            f"({pct(late, n)}%) enquiries punched after 20:00. "
                            "Footfall register not supplied for reconciliation")
        clean = n - negative - late
        line.achievement = pct(max(clean, 0), n)
        line.remark = "back-dating and end-of-day bulk entry indicated"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # --- 1.5 lost enquiry coding ---------------------------------------------
    line = Line("1.5", p.key, "Lost enquiry reason coding",
                "Reason code on every lost enquiry and quality of the coding",
                "100% reason-coded, no single generic code dominating",
                weight=p.weight * 0.18,
                source=data.src("F3", "Stage", "Lost-Reason"))
    if enq is not None and "Lost-Reason" in enq.columns and "Stage" in enq.columns \
            and (enq["Stage"] == "Lost").any():
        lost = enq[enq["Stage"] == "Lost"]
        coded = lost["Lost-Reason"].notna().sum()
        top = lost["Lost-Reason"].value_counts()
        top_share = pct(top.iloc[0], len(lost)) if len(top) else 0
        top_name = str(top.index[0]) if len(top) else "—"
        line.actual_text = (f"{len(lost):,} lost, {pct(coded, len(lost))}% reason-coded; "
                            f"{top_share}% coded \u201c{top_name}\u201d")
        capture = pct(coded, len(lost))
        quality = 100.0 if top_share <= 50 else round(max(0, 100 - (top_share - 50) * 1.5), 1)
        line.achievement = round((capture + quality) / 2, 1)
        line.remark = ("capture complete, coding generic" if top_share > 50
                       else "capture and coding acceptable")
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)
    return p


# =============================================================================
# B. Funnel conversion
# =============================================================================
def build_pillar_b(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("B_conversion", "B. Funnel conversion health", cfg.weights["B_conversion"])
    enq, td, bk, rt = data.get("F3"), data.get("F5"), data.get("F4"), data.get("F6")
    n = len(enq) if enq is not None else 0
    td_done = int(td["_completed"].sum()) if td is not None else 0
    bookings = int(bk["_booked"].sum()) if bk is not None and "_booked" in bk else (
        len(bk) if bk is not None else 0)
    invoiced = int(rt["_invoiced"].sum()) if rt is not None else 0

    # 2.1 enquiry to test drive, by source
    line = Line("2.1", p.key, "Enquiry to test drive",
                "Test drives completed against enquiries, split by source",
                f"Walk-in ≥{cfg.norm('td_conversion_walkin_pct')}%, "
                f"digital ≥{cfg.norm('td_conversion_digital_pct')}%",
                weight=p.weight * 0.27,
                source=data.src("F3", "Enquiry Type", "Test Drive Stage"))
    if enq is not None and "Enquiry Type" in enq.columns:
        by = enq.groupby("Enquiry Type")["_td_done"].mean().mul(100).round(1)
        walkin, digital = float(by.get("Walk-in", 0)), float(by.get("Digital", 0))
        line.actual_text = "; ".join(f"{k} {v}%" for k, v in by.items())
        a_w = achievement(walkin, cfg.norm("td_conversion_walkin_pct")) or 0
        a_d = achievement(digital, cfg.norm("td_conversion_digital_pct")) or 0
        line.achievement = round((a_w + a_d) / 2, 1)
        line.remark = (f"walk-in {walkin}% vs {cfg.norm('td_conversion_walkin_pct')}%, "
                       f"digital {digital}% vs {cfg.norm('td_conversion_digital_pct')}%")
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # 2.2 test drive to booking
    line = Line("2.2", p.key, "Test drive to booking",
                "Bookings created against completed test drives",
                f"≥{cfg.norm('td_to_booking_pct')}%", weight=p.weight * 0.20,
                source=f"{data.src('F4')} + {data.src('F5')}")
    # Same reasoning as 2.3: a test drive that led to a booking counts even
    # if that booking has since been invoiced.
    _all_bk = len(bk) if bk is not None else 0
    if td_done and _all_bk:
        v = pct(_all_bk, td_done)
        line.actual_text = (f"{_all_bk:,} bookings from {td_done:,} completed "
                            f"test drives = {v}%")
        line.achievement = achievement(v, cfg.norm("td_to_booking_pct"))
        line.remark = f"{round(cfg.norm('td_to_booking_pct') - v, 1)} points below norm" if v < cfg.norm("td_to_booking_pct") else "within norm"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # 2.3 booking to retail
    line = Line("2.3", p.key, "Booking to retail",
                "Invoices raised against bookings created",
                f"≥{cfg.norm('booking_to_retail_pct')}%", weight=p.weight * 0.20,
                source=f"{data.src('F4', 'Booking Stage')} + {data.src('F6', 'Invoice Status')}")
    # Conversion needs every booking in the denominator, not just those still
    # sitting at "Booked". A booking that reached Invoiced converted - leaving
    # it out would divide by the failures alone and report over 100%.
    all_bookings = len(bk) if bk is not None else 0
    overlap = periods_overlap(data, "booking", "retail")
    if overlap is not None and overlap < 0.5:
        wb, wr = stage_window(data, "booking"), stage_window(data, "retail")
        line.actual_text = (
            f"{all_bookings:,} bookings ({wb[0]:%d-%b} to {wb[1]:%d-%b}) against "
            f"{invoiced:,} invoices ({wr[0]:%d-%b} to {wr[1]:%d-%b})")
        line.unscored_reason = (
            "the booking and retail extracts cover different periods, so this "
            "conversion would be meaningless - pull both for the same month")
    elif all_bookings and invoiced:
        v = pct(invoiced, all_bookings)
        line.actual_text = (f"{invoiced:,} invoiced from {all_bookings:,} bookings "
                            f"= {v}%")
        line.achievement = achievement(v, cfg.norm("booking_to_retail_pct"))
        line.remark = f"{all_bookings - invoiced:,} bookings unconverted in the period"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # 2.4 net conversion
    line = Line("2.4", p.key, "Net enquiry to retail",
                "Retails against enquiries for the period",
                f"≥{cfg.norm('enquiry_to_retail_pct')}%", weight=p.weight * 0.20,
                source=f"{data.src('F3')} + {data.src('F6')}")
    if n and invoiced:
        v = pct(invoiced, n)
        line.actual_text = f"{invoiced:,} retails from {n:,} enquiries = {v}%"
        line.achievement = achievement(v, cfg.norm("enquiry_to_retail_pct"))
        line.remark = f"{round(cfg.norm('enquiry_to_retail_pct') - v, 1)} points below norm" if v < cfg.norm("enquiry_to_retail_pct") else "within norm"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    # 2.5 live enquiry ageing
    line = Line("2.5", p.key, "Live enquiry ageing",
                "Age profile of open enquiries at the audit date",
                f"<{cfg.norm('live_enquiry_over_30d_pct')}% older than 30 days",
                weight=p.weight * 0.13,
                source=data.src("F3", "Stage", "Enquiry Date"))
    if enq is not None and "Stage" in enq.columns and "_enq_dt" in enq.columns \
            and enq["_enq_dt"].notna().any():
        live = enq[enq["Stage"].isin(["Enquiry", "Quotation", "Test Drive"])]
        as_on = enq["_enq_dt"].max()
        age = (as_on - live["_enq_dt"]).dt.days
        # Within a single-month extract, 30-day ageing cannot occur; fall back
        # to a 15-day threshold and say so, rather than reporting a false pass.
        span = (enq["_enq_dt"].max() - enq["_enq_dt"].min()).days
        thresh = 30 if span > 45 else int(cfg.period.get("short_period_ageing_days", 15))
        over = int((age > thresh).sum())
        share = pct(over, len(live))
        if live.empty:
            line.unscored_reason = "no live enquiries in the extract"
            p.lines.append(line)
            return p
        line.actual_text = (f"{len(live):,} live; {over:,} ({share}%) beyond {thresh} days"
                            + ("" if thresh == 30 else " (extract spans one month, so a "
                                                       "15-day threshold is used)"))
        line.achievement = achievement(share, cfg.norm("live_enquiry_over_30d_pct"),
                                       higher_is_better=False)
        line.remark = "open book ageing beyond norm" if share > cfg.norm("live_enquiry_over_30d_pct") else "ageing within norm"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)
    return p


# =============================================================================
# C. Volume, order bank and market share
# =============================================================================
def build_pillar_c(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("C_volume", "C. Volume, order bank and market share", cfg.weights["C_volume"])
    rt, bk = data.get("F6"), data.get("F4")
    invoiced = int(rt["_invoiced"].sum()) if rt is not None else 0
    cancelled = int((~rt["_invoiced"]).sum()) if rt is not None else 0

    branch = _target_branch(data)
    book = getattr(data, "targets", None)

    line = Line("3.1", p.key, "Retail against target",
                "Invoices raised against the retail target for the period",
                "100% of target", weight=p.weight * 0.30,
                source=data.src("F6", "Invoice Status"))
    line.actual_text = (f"{invoiced:,} invoiced, {cancelled:,} cancelled "
                        f"({pct(cancelled, invoiced + cancelled)}%)")
    plan, plan_note = target_for(data, "retail", branch)
    if plan:
        line.achievement = achievement(invoiced, plan)
        line.remark = plan_note or f"target {plan:,.0f}"
        line.source += "  |  " + book.filename
    else:
        tgt, note = prorate(cfg.target("retail_target"), data, cfg)
        if tgt:
            line.achievement = achievement(invoiced, tgt)
            line.remark = note
        elif cfg.target("retail_target"):
            line.unscored_reason = note or "period too short to judge the monthly target"
        else:
            line.unscored_reason = (plan_note or
                                    "no sales target supplied - add sales_target.xlsx "
                                    "or set retail_target in norms.yaml")
    p.lines.append(line)

    line = Line("3.2", p.key, "Order bank cover",
                "Open confirmed bookings divided by monthly retail",
                f"≥{cfg.norm('order_bank_cover_months')} months of cover",
                weight=p.weight * 0.16, source=data.src("F4", "Booking Stage"))
    if bk is not None and "Booking Stage" in bk.columns and invoiced:
        open_bk = int(bk["Booking Stage"].isin(["Booked", "Alloted"]).sum())
        days = period_days(data)
        # Cover is open bookings ÷ *monthly* retail rate. On a short extract the
        # raw retail count understates the monthly rate, so annualise it first.
        monthly_rate = invoiced
        note = ""
        if days and days < 28 and cfg.period.get("prorate_volume_norms", True):
            dim = data.get("F3")["_enq_dt"].dropna().max().days_in_month
            monthly_rate = invoiced * dim / days
            note = f" (retail rate extrapolated from {days} days)"
        cover = round(open_bk / monthly_rate, 2) if monthly_rate else 0
        line.actual_text = (f"{open_bk:,} open bookings ÷ {monthly_rate:,.0f} monthly "
                            f"retail rate = {cover} months{note}")
        line.achievement = achievement(cover, cfg.norm("order_bank_cover_months"))
        line.remark = "cover below norm" if cover < cfg.norm("order_bank_cover_months") else "order bank healthy"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    line = Line("3.3", p.key, "Market share in territory",
                "Registrations in the territory against segment total",
                "≥ OEM share norm", weight=p.weight * 0.14,
                unscored_reason="VAHAN registration data not supplied")
    p.lines.append(line)

    line = Line("3.4", p.key, "Model and variant mix",
                "Retail mix against planned mix",
                "Within ±10% of plan for every model", weight=p.weight * 0.10,
                source=data.src("F6", "Model Group"))
    if rt is not None and "Model Group" in rt.columns:
        top = rt[rt["_invoiced"]]["Model Group"].value_counts().head(4)
        line.actual_text = "; ".join(f"{k} {v}" for k, v in top.items())
    line.unscored_reason = "planned model mix not supplied"
    p.lines.append(line)

    # --- 3.5 and 3.6: the middle of the funnel, against the same plan -------
    # Retail alone can be met by burning the order bank while enquiry and test
    # drive collapse. Scoring all four stages against plan shows which part of
    # the funnel is actually carrying the number.
    td = data.get("F5")
    td_done = int(td["_completed"].sum()) if td is not None else 0
    _bk = data.get("F4")
    bookings = (int(_bk["_booked"].sum()) if _bk is not None and "_booked" in _bk
                else (len(_bk) if _bk is not None else 0))

    for code, stage, name, count, wt, src in (
            ("3.5", "test_drive", "Test drive against target", td_done, 0.15,
             data.src("F5", "Stage")),
            ("3.6", "booking", "Booking against target", bookings, 0.15,
             data.src("F4", "Booking Number"))):
        months = stage_months(data, stage)
        line = Line(code, p.key, name,
                    f"{name.split(' against')[0]}s recorded against the target "
                    f"for the period",
                    "100% of target", weight=p.weight * wt, source=src)
        line.actual_text = (f"{count:,} recorded"
                            + (f" over {months:.2f} months" if months else ""))
        plan, plan_note = target_for(data, stage, branch)
        if plan:
            line.achievement = achievement(count, plan)
            line.remark = plan_note or f"target {plan:,.0f}"
            line.source += "  |  " + book.filename
        else:
            line.unscored_reason = (plan_note or
                                    "no sales target supplied - add sales_target.xlsx")
        p.lines.append(line)

    return p


# =============================================================================
# D. Manpower
# =============================================================================
def build_pillar_d(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("D_manpower", "D. Manpower health and productivity", cfg.weights["D_manpower"])
    enq, rt = data.get("F3"), data.get("F6")
    sc_enq = enq["_sc"].nunique() if enq is not None else 0
    inv = rt[rt["_invoiced"]] if rt is not None else None
    sc_retail = inv["_sc"].nunique() if inv is not None else 0
    invoiced = len(inv) if inv is not None else 0

    line = Line("4.1", p.key, "Consultant strength against norm",
                "On-roll consultants against sanctioned manpower",
                "100% of sanctioned strength", weight=p.weight * 0.22,
                source=f"{data.src('F3', 'Sales Consultant')} + {data.src('F6', 'SC Name')}")
    line.actual_text = f"{sc_enq} consultants raising enquiries, {sc_retail} with at least one retail"
    if cfg.target("sanctioned_sc"):
        line.achievement = achievement(sc_enq, cfg.target("sanctioned_sc"))
    else:
        line.unscored_reason = "sanctioned manpower not supplied in norms.yaml"
    p.lines.append(line)

    norm_sc, note = prorate(cfg.norm("retails_per_sc_per_month"), data, cfg)
    line = Line("4.2", p.key, "Productivity per consultant",
                "Retails per consultant for the period",
                f"≥{cfg.norm('retails_per_sc_per_month')} retails per consultant per month"
                + (f" ({note})" if note else ""),
                weight=p.weight * 0.33, source=data.src("F6", "SC Name"))
    if sc_enq and invoiced:
        per = round(invoiced / sc_enq, 1)
        counts = inv.groupby("_sc").size()
        nil = sc_enq - sc_retail
        line.actual_text = (f"{per} retails per consultant over {period_days(data) or '?'} "
                            f"day(s); {nil} consultants with nil retail")
        if norm_sc is None:
            line.unscored_reason = note or "productivity norm not set"
        else:
            line.achievement = achievement(per, norm_sc)
            line.remark = note or f"norm is {cfg.norm('retails_per_sc_per_month')}"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    for code, name, checkpoint, w, reason in [
        ("4.3", "Attrition", "Separations against average on-roll strength", 0.22,
         "separation data not supplied"),
        ("4.4", "Certification and training", "Product and process certification status", 0.12,
         "certification records not supplied"),
        ("4.5", "Review discipline", "Morning meeting and daily sales report records", 0.11,
         "meeting records not supplied"),
    ]:
        p.lines.append(Line(code, p.key, name, checkpoint, "As per norm",
                            weight=p.weight * w, unscored_reason=reason))
    return p


# =============================================================================
# E. Stock and inventory
# =============================================================================
def build_pillar_e(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("E_stock", "E. Stock and inventory health", cfg.weights["E_stock"])
    rt = data.get("F6")

    for code, name, checkpoint, w, reason in [
        ("5.1", "Stock days of supply", "Closing stock ÷ average daily retail", 0.27,
         "stock statement not supplied"),
        ("5.2", "Ageing stock", "Units beyond 60 and 90 days with liquidation plan", 0.27,
         "stock ageing not supplied"),
    ]:
        p.lines.append(Line(code, p.key, name, checkpoint, "As per norm",
                            weight=p.weight * w, unscored_reason=reason))

    line = Line("5.3", p.key, "VIN allotment to invoice TAT",
                "Allotment date against invoice date per unit",
                f"≤{cfg.norm('allotment_to_invoice_days')} days",
                weight=p.weight * 0.18,
                source=data.src("F6", "Allotment Date and Time", "Invoice Date and Time"))
    if rt is not None and "_allot_dt" in rt.columns:
        inv = rt[rt["_invoiced"]]
        d = (inv["_invoice_dt"] - inv["_allot_dt"]).dt.days.dropna()
        if len(d):
            over = int((d > cfg.norm("allotment_to_invoice_days")).sum())
            line.actual_text = (f"median {d.median():.0f} days; {over:,} units "
                                f"({pct(over, len(d))}%) beyond "
                                f"{cfg.norm('allotment_to_invoice_days')} days, longest {int(d.max())}")
            line.achievement = pct(len(d) - over, len(d))
            line.remark = "control working" if line.achievement >= 90 else "TAT breaches"
        else:
            line.unscored_reason = "allotment dates not populated"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    for code, name, checkpoint, w, reason in [
        ("5.4", "Stock mix against demand mix", "Availability of top-selling variants", 0.14,
         "stock versus booking mix not supplied"),
        ("5.5", "Yard and PDI condition", "PDI completion and damage register", 0.14,
         "PDI register not supplied"),
    ]:
        p.lines.append(Line(code, p.key, name, checkpoint, "As per norm",
                            weight=p.weight * w, unscored_reason=reason))
    return p


# =============================================================================
# F. Profitability — including RSA and extended warranty
# =============================================================================
def build_pillar_f(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("F_profitability", "F. Financial and profitability health",
               cfg.weights["F_profitability"])
    rt = data.get("F6")
    inv = rt[rt["_invoiced"]] if rt is not None else None
    n = len(inv) if inv is not None else 0

    def vap_line(code, name, id_col, amt_col, norm_key, w, note=""):
        """Shared logic for the VAP products recorded against a VIN."""
        line = Line(code, p.key, name,
                    f"{name} recorded against every invoiced VIN, verified on the "
                    "policy portal rather than on amount collected",
                    (f"Penetration ≥{cfg.norm(norm_key)}%" if cfg.norm(norm_key)
                     else "Penetration norm to be confirmed"),
                    weight=w, source=data.src("F6", id_col, amt_col))
        if inv is None or id_col not in inv.columns:
            line.unscored_reason = f"{id_col} column absent from the retail extract"
            return line
        sold = int(inv[id_col].notna().sum())
        amt = pd.to_numeric(inv[amt_col], errors="coerce") if amt_col in inv.columns else None
        value = float(amt.sum()) if amt is not None else 0.0
        share = pct(sold, n)
        line.actual_text = (f"{id_col}: {sold} of {n} invoices ({share}%). "
                            f"Value recorded: ₹{value:,.0f}")
        if sold == 0:
            # A zero is a hard finding, not an absent measurement.
            line.achievement = 0.0
            line.remark = ("nil recorded on every invoice — establish whether the product "
                           "is unsold or sold outside the DMS")
        elif cfg.norm(norm_key):
            line.achievement = achievement(share, cfg.norm(norm_key))
            line.remark = note or f"norm {cfg.norm(norm_key)}%"
        else:
            line.achievement = None
            line.unscored_reason = (f"penetration norm '{norm_key}' not set in norms.yaml "
                                    f"(observed {share}%)")
        return line

    line = Line("6.1", p.key, "Gross margin per unit",
                "Net margin per unit after discount, before VAP",
                "≥ business-plan norm", weight=p.weight * 0.14,
                source=data.src("F6", "Dealer Discount", "Selling Price"))
    if inv is not None and "Dealer Discount" in inv.columns:
        disc = pd.to_numeric(inv["Dealer Discount"], errors="coerce")
        line.actual_text = f"Dealer discount recorded on {int((disc > 0).sum())} of {n} invoices"
    line.unscored_reason = "margin norm not supplied in norms.yaml"
    p.lines.append(line)

    # SHIELD is Mahindra's extended warranty product — one line, not two.
    # AMC is not a Mahindra scheme and is therefore not audited.
    p.lines.append(vap_line("6.2", "SHIELD (extended warranty) sold", "Shield Scheme Reg ID",
                            "Shield Scheme Amount", "ew_penetration_pct", p.weight * 0.24))
    p.lines.append(vap_line("6.3", "RSA policies sold", "RSA Scheme Reg ID",
                            "RSA Scheme Amount", "rsa_penetration_pct", p.weight * 0.20))

    line = Line("6.4", p.key, "SHIELD and RSA offer discipline",
                "Sample of non-buying customers: was the product offered, quoted, "
                "and the decline reason recorded",
                "100% offered and recorded", weight=p.weight * 0.08,
                source=data.src("F6"))
    line.unscored_reason = ("no decline-reason field exists in the retail extract; "
                            "score from the physical audit sample instead")
    p.lines.append(line)

    line = Line("6.5", p.key, "Finance penetration",
                "Retails funded through dealer-arranged finance",
                f"≥{cfg.norm('finance_penetration_pct')}%", weight=p.weight * 0.16,
                source=data.src("F6", "Finance Arrange By", "Financier"))
    if inv is not None and "Finance Arrange By" in inv.columns:
        dealer = int((inv["Finance Arrange By"].astype(str).str.strip().str.lower() == "dealer").sum())
        v = pct(dealer, n)
        named = pct(inv["Financier"].notna().sum(), n) if "Financier" in inv.columns else None
        line.actual_text = (f"Dealer-arranged on {dealer} of {n} = {v}%"
                            + (f"; financier named on {named}%" if named is not None else ""))
        line.achievement = achievement(v, cfg.norm("finance_penetration_pct"))
        line.remark = f"norm {cfg.norm('finance_penetration_pct')}%"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    line = Line("6.6", p.key, "Insurance penetration",
                "In-house insurance against total retails",
                f"≥{cfg.norm('insurance_penetration_pct')}%", weight=p.weight * 0.12,
                source=data.src("F6", "Insurance Company Name", "Insurance Amount"))
    if inv is not None and "Insurance Company Name" in inv.columns:
        names = inv["Insurance Company Name"].astype(str).str.strip().str.upper()
        generic = int((names.isin(["OTHERS", "OTHER", "NAN", ""])).sum())
        amt_blank = int(inv["Insurance Amount"].isna().sum()) if "Insurance Amount" in inv.columns else n
        line.actual_text = (f"Insurer recorded as a generic value on {generic} of {n} "
                            f"({pct(generic, n)}%); insurance amount blank on {amt_blank} of {n}")
        if generic >= n * 0.9:
            line.achievement = 0.0
            line.remark = "in-house share cannot be identified from the insurer master"
        else:
            line.achievement = pct(n - generic, n)
            line.remark = f"norm {cfg.norm('insurance_penetration_pct')}%"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    line = Line("6.7", p.key, "Exchange penetration",
                "Exchange-linked retails against total retails",
                f"≥{cfg.norm('exchange_penetration_pct')}%", weight=p.weight * 0.06,
                source=data.src("F6", "Exchange Indicator"))
    if inv is not None and "Exchange Indicator" in inv.columns:
        yes = int((inv["Exchange Indicator"].astype(str).str.upper() == "YES").sum())
        v = pct(yes, n)
        line.actual_text = f"Exchange on {yes} of {n} = {v}%"
        line.achievement = achievement(v, cfg.norm("exchange_penetration_pct"))
        line.remark = f"norm {cfg.norm('exchange_penetration_pct')}%"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)
    return p


# =============================================================================
# G. Customer experience
# =============================================================================
def build_pillar_g(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("G_experience", "G. Customer experience health", cfg.weights["G_experience"])
    rt = data.get("F6")
    inv = rt[rt["_invoiced"]] if rt is not None else None
    n = len(inv) if inv is not None else 0
    branches = set(cfg.branches)

    p.lines.append(Line("7.1", p.key, "NVD / SSI top-box score",
                        "OEM customer feedback score for the period",
                        "≥ OEM target", weight=p.weight * 0.36,
                        unscored_reason="NVD/SSI score report not supplied; only "
                                        "complaint extracts available"))

    line = Line("7.2", p.key, "Complaints per 1000",
                "Complaints at enquiry, test drive and delivery stage",
                "Reduction against the prior period", weight=p.weight * 0.27,
                source="Complaint extracts F7–F10")
    counts = {}
    for ref, label, denom in (("F7", "enquiry", len(data.get("F3")) if data.get("F3") is not None else 0),
                              ("F8", "test drive", int(data.get("F5")["_completed"].sum())
                               if data.get("F5") is not None else 0),
                              ("F9", "delivery", n)):
        df = data.get(ref)
        if df is None or not denom:
            continue
        own = int(df["_branch"].isin(branches).sum())
        counts[label] = (own, round(own / denom * 1000, 2))
    if counts:
        line.actual_text = "; ".join(f"{k} {v[0]} complaints = {v[1]} per 1000"
                                     for k, v in counts.items())
        line.unscored_reason = "prior period not supplied, so reduction cannot be scored"
    else:
        line.unscored_reason = "complaint extracts not supplied"
    p.lines.append(line)

    p.lines.append(Line("7.3", p.key, "Escalation TAT and repeat concerns",
                        "Escalation log: owner, closure within TAT, repeats",
                        "100% within TAT", weight=p.weight * 0.18,
                        unscored_reason="escalation log not supplied"))

    line = Line("7.4", p.key, "Delivery documentation",
                "Delivery note raised against every invoice",
                f"{cfg.norm('delivery_note_pct')}% of invoices", weight=p.weight * 0.10,
                source=data.src("F6", "Delivery Note Date"))
    if inv is not None and "_delnote_dt" in inv.columns:
        have = int(inv["_delnote_dt"].notna().sum())
        line.actual_text = (f"Delivery note present on {have} of {n} invoiced units "
                            f"({pct(have, n)}%)")
        line.achievement = pct(have, n)
        line.remark = f"{n - have} units with no delivery note"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    line = Line("7.5", p.key, "Delivery commitment adherence",
                "Promised delivery date recorded and honoured",
                f"Within committed date; booking to invoice ≤"
                f"{cfg.norm('booking_to_invoice_days')} days", weight=p.weight * 0.09,
                source=data.src("F6", "Initial Promised Delivery date", "Booking Date and Time"))
    if inv is not None:
        promised = int(inv["Initial Promised Delivery date"].notna().sum()) \
            if "Initial Promised Delivery date" in inv.columns else 0
        lag = (inv["_invoice_dt"] - inv["_booking_dt"]).dt.days.dropna()
        over = int((lag > cfg.norm("booking_to_invoice_days")).sum())
        line.actual_text = (f"Promised delivery date populated on {promised} of {n}. "
                            f"Booking to invoice median {lag.median():.0f} days; "
                            f"{over} ({pct(over, len(lag))}%) beyond "
                            f"{cfg.norm('booking_to_invoice_days')} days")
        line.achievement = pct(promised, n)
        line.remark = ("commitment date never recorded, so slippage cannot be measured"
                       if promised == 0 else "")
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)
    return p


# =============================================================================
# H. Systems and process compliance
# =============================================================================
def build_pillar_h(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("H_systems", "H. Systems and process compliance", cfg.weights["H_systems"])
    enq, rt, td = data.get("F3"), data.get("F6"), data.get("F5")
    inv = rt[rt["_invoiced"]] if rt is not None else None

    line = Line("8.1", p.key, "DMS data hygiene",
                "Mandatory and qualification fields populated across all extracts",
                "All mandatory fields populated", weight=p.weight * 0.40,
                source=f"{data.src('F3')}; {data.src('F5')}; {data.src('F6')}")
    checks: list[tuple[str, float]] = []
    bits: list[str] = []
    for df, cols, tag in ((enq, ["Customer Email", "Enquiry Sub Source", "Variant Description",
                                 "Customer Type"], "enquiry"),
                          (td, ["TD Vehicle Name"], "test drive"),
                          (inv, ["RSA Scheme Reg ID", "Total Accessories Amount",
                                 "Initial Promised Delivery date"], "retail")):
        for c in cols:
            b = _blank_pct(df, c)
            if b is not None:
                checks.append((c, b))
    if checks:
        worst = sorted(checks, key=lambda x: -x[1])[:5]
        bits = [f"{c} blank {b}%" for c, b in worst]
        line.actual_text = "; ".join(bits)
        line.achievement = round(100 - sum(b for _, b in checks) / len(checks), 1)
        line.remark = "qualification fields not being filled"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    line = Line("8.2", p.key, "Follow-up adherence",
                "Follow-ups completed against the follow-up calendar",
                f"No more than {cfg.norm('followup_zero_pct_max')}% of enquiries untouched",
                weight=p.weight * 0.40,
                source=data.src("F3", "Completed Followup Count", "Next Planned Followup"))
    if enq is not None and "_followups" in enq.columns:
        zero = int((enq["_followups"] == 0).sum())
        share = pct(zero, len(enq))
        nxt = _blank_pct(enq, "Next Planned Followup")
        line.actual_text = (f"{zero:,} enquiries ({share}%) with zero follow-up"
                            + (f"; {nxt}% with no next planned date" if nxt is not None else ""))
        line.achievement = achievement(share, cfg.norm("followup_zero_pct_max"),
                                       higher_is_better=False)
        line.remark = "follow-up discipline below norm" if share > cfg.norm("followup_zero_pct_max") else "within norm"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)

    p.lines.append(Line("8.3", p.key, "Closure of previous audit findings",
                        "Prior non-compliances and their corrective actions",
                        "100% closed within target date", weight=p.weight * 0.20,
                        unscored_reason="no previous audit on record"))
    return p


# =============================================================================
# I. Demo fleet traceability (data-derived; the physical check is pillar J)
# =============================================================================
def build_pillar_i(data: AuditData, cfg: Config) -> Pillar:
    p = Pillar("I_demo_fleet", "I. Demo fleet traceability", cfg.weights["I_demo_fleet"])
    td = data.get("F5")
    line = Line("9.1", p.key, "Test drive vehicle recorded",
                "Every test drive record names the vehicle used",
                "100% of test drives name a vehicle", weight=p.weight,
                source=data.src("F5", "TD Vehicle Name"))
    if td is not None and "TD Vehicle Name" in td.columns:
        by_branch = td.groupby("_branch")["TD Vehicle Name"].nunique()
        blind = sorted(by_branch[by_branch == 0].index)
        filled = pct(td["TD Vehicle Name"].notna().sum(), len(td))
        line.actual_text = (f"Vehicle named on {filled}% of test drives; "
                            f"{len(blind)} branch(es) with no identified demo vehicle"
                            + (": " + ", ".join(blind) if blind else ""))
        line.achievement = filled
        line.remark = "demo fleet untraceable at some branches" if blind else "fleet traceable"
    else:
        line.unscored_reason = NOT_SUPPLIED
    p.lines.append(line)
    return p


BUILDERS = (build_pillar_a, build_pillar_b, build_pillar_c, build_pillar_d,
            build_pillar_e, build_pillar_f, build_pillar_g, build_pillar_h,
            build_pillar_i)
