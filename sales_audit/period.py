"""Which month is being audited.

An audit is run in the current month and examines the month before it. Run on
17 September, it audits August: a completed month, with a whole month's target
to judge against and no part-month arithmetic anywhere.

That is the difference between this module and pro-rating. Pro-rating exists
because an extract might cover ten days; resolving the period up front means
it usually does not have to. The audit date and the audit period are two
different things, and conflating them is what produced a September audit on
the 16th judged against a whole month's plan.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)

# The date column that governs each extract.
PERIOD_COLUMNS = {
    "F3": "_enq_dt",
    "F4": "_created_dt",
    "F5": "_created_dt",
    "F6": "_invoice_dt",
}


@dataclass
class AuditPeriod:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return pd.Timestamp(year=self.year, month=self.month, day=1).strftime("%B %Y")

    @property
    def first(self) -> pd.Timestamp:
        return pd.Timestamp(year=self.year, month=self.month, day=1)

    @property
    def last(self) -> pd.Timestamp:
        return self.first + pd.offsets.MonthEnd(0)

    @property
    def days(self) -> int:
        return self.last.day

    def contains(self, ts) -> bool:
        return pd.notna(ts) and self.first <= ts <= self.last.replace(
            hour=23, minute=59, second=59)


def last_completed_month(today: date | None = None) -> AuditPeriod:
    """The month before the one we are standing in."""
    today = today or date.today()
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    return AuditPeriod(y, m)


def parse_period(value: str | None, today: date | None = None) -> AuditPeriod:
    """Resolve a period from config or the command line.

    Accepts 'YYYY-MM', 'last', 'previous', 'current', or None (which means
    last completed month).
    """
    if value is None or str(value).strip() == "":
        return last_completed_month(today)
    v = str(value).strip().lower()
    if v in ("last", "previous", "last_completed_month", "last-month"):
        return last_completed_month(today)
    if v in ("current", "this", "this_month", "mtd"):
        t = today or date.today()
        return AuditPeriod(t.year, t.month)
    m = re.fullmatch(r"(\d{4})[-/ ](\d{1,2})", v)
    if m:
        return AuditPeriod(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{1,2})[-/ ](\d{4})", v)
    if m:
        return AuditPeriod(int(m.group(2)), int(m.group(1)))
    raise ValueError(f"Could not read an audit period from '{value}'. "
                     "Use YYYY-MM, or 'last'.")


def months_in(df: pd.DataFrame | None, column: str) -> dict[str, int]:
    """Row counts per month for one extract."""
    if df is None or column not in df.columns:
        return {}
    s = df[column].dropna()
    if s.empty:
        return {}
    return {str(k): int(v) for k, v in
            s.dt.to_period("M").value_counts().sort_index().items()}


def clip_to_period(data, period: AuditPeriod) -> dict[str, dict]:
    """Restrict every dated extract to the audit month, in place.

    Returns a note per extract saying what was kept and what was dropped, so
    the report can show that a retail file was three months wide and only
    August survived - rather than silently scoring whatever arrived.
    """
    notes: dict[str, dict] = {}
    for ref, col in PERIOD_COLUMNS.items():
        ds = data.ds(ref) if hasattr(data, "ds") else None
        if ds is None:
            continue
        df = ds.df
        if col not in df.columns:
            continue
        before = len(df)
        inside = df[col].apply(period.contains)
        kept = df[inside]
        dropped = before - len(kept)
        notes[ref] = {
            "file": ds.filename,
            "before": before,
            "kept": len(kept),
            "dropped": dropped,
            "months": months_in(df, col),
        }
        if len(kept) == 0 and before:
            # Do not silently empty an extract. Leave it whole, flag it loudly,
            # and let the lines that use it report against the wrong period
            # rather than against nothing at all.
            notes[ref]["empty"] = True
            log.warning("%s has no rows in %s (it covers %s) - left unfiltered",
                        ds.filename, period.label,
                        ", ".join(notes[ref]["months"]) or "no dated rows")
            continue
        ds.df = kept
        if dropped:
            log.info("%-28s %d of %d rows are in %s", ds.filename,
                     len(kept), before, period.label)
    return notes


def target_rows_for(book, period: AuditPeriod):
    """The plan rows for this month, if the plan is month-stamped."""
    if book is None or not getattr(book, "rows", None):
        return None
    months = {r.month for r in book.rows if r.month}
    if not months:
        return None
    wanted = {m for m in months if m.startswith(period.key)}
    return wanted or None
