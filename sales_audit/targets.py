"""Sales targets by location, manager and model.

The target file is a monthly plan: one row per location, manager and model,
with enquiry, test drive, booking and retail figures. Two things have to be
reconciled before it can be compared with the DMS extracts.

**Names.** The plan calls an outlet GNR; the DMS calls it GREAT_NAG_ROAD.
Neither is wrong, so the mapping lives in norms.yaml under `target_locations`
rather than being guessed here. A plan location with no mapping is reported
rather than silently dropped, because a missing mapping quietly deflates the
target and makes a branch look better than it is.

**Period.** The plan is monthly, but the extracts are not all the same length
— the enquiry book routinely covers three months while retails cover one. So
each funnel stage is scaled by the months its own extract actually spans,
not by one shared figure. Comparing a three-month enquiry count against a
one-month enquiry target is the single easiest way to make a healthy branch
look like it is failing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Column names as they appear in the cleaned target file.
COL_SEGMENT = "segment"
COL_MONTH = "month"
COL_LOCATION = "location"
COL_MANAGER = "manager"
COL_MODEL = "model"

# Target column -> the funnel stage it governs.
STAGES = {
    "enq": "enquiry",
    "test_drive": "test_drive",
    "booking": "booking",
    "retail": "retail",
}
REQUIRED_COLUMNS = (COL_LOCATION, COL_MANAGER, "enq", "test_drive",
                    "booking", "retail")


@dataclass
class TargetRow:
    segment: str
    month: str
    location: str
    branch: str          # location mapped to the DMS branch name, or ""
    manager: str
    model: str
    enq: float
    test_drive: float
    booking: float
    retail: float


@dataclass
class TargetBook:
    """Every target row, with the lookups the audit needs."""

    rows: list[TargetRow] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    filename: str = ""

    # -- totals -----------------------------------------------------------
    def total(self, stage: str, branch: str | None = None,
              manager: str | None = None) -> float:
        """Monthly target for one stage, optionally narrowed."""
        col = {v: k for k, v in STAGES.items()}[stage]
        out = 0.0
        for r in self.rows:
            # When the plan is month-stamped and an audit month is set, only
            # that month's rows count.
            if self.period_key and r.month and not r.month.startswith(self.period_key):
                continue
            # A plan location with no DMS branch contributes no actuals, so
            # counting its target would make the network look short against a
            # number nothing could ever have been recorded towards. These are
            # listed as unmapped in the report instead.
            if not r.branch:
                continue
            if branch and (r.branch or "").upper() != branch.upper():
                continue
            if manager and r.manager.upper() != manager.upper():
                continue
            out += getattr(r, col) or 0.0
        return round(out, 2)

    # Set by the audit to the month being examined, so a plan covering
    # several months contributes only the relevant one.
    period_key: str | None = None

    def _months_counted(self) -> int:
        if self.period_key:
            hit = [m for m in self.months if m.startswith(self.period_key)]
            if hit:
                return len(hit)
        return max(len(self.months), 1)

    def monthly(self, branch: str | None = None,
                manager: str | None = None) -> dict[str, float]:
        """All four stage targets for the audit month."""
        n = self._months_counted()
        return {stage: round(self.total(stage, branch, manager) / n, 2)
                for stage in STAGES.values()}

    def branches(self) -> list[str]:
        return sorted({r.branch for r in self.rows if r.branch})

    def managers(self, branch: str | None = None) -> list[str]:
        return sorted({r.manager for r in self.rows
                       if r.manager
                       and (not branch or (r.branch or "").upper() == branch.upper())})

    def by_manager(self, branch: str | None = None) -> pd.DataFrame:
        """Manager-wise monthly target, for the workbook."""
        recs = []
        n = max(len(self.months), 1)
        for m in self.managers(branch):
            row = {"Manager": m}
            locs = sorted({r.location for r in self.rows if r.manager == m
                           and (not branch
                                or (r.branch or "").upper() == branch.upper())})
            row["Location(s)"] = ", ".join(locs)
            for stage in STAGES.values():
                row[stage] = round(self.total(stage, branch, m) / n, 1)
            recs.append(row)
        return pd.DataFrame(recs)

    def by_branch(self) -> pd.DataFrame:
        recs = []
        n = max(len(self.months), 1)
        for b in self.branches():
            row = {"Branch": b}
            for stage in STAGES.values():
                row[stage] = round(self.total(stage, b) / n, 1)
            recs.append(row)
        return pd.DataFrame(recs)

    @property
    def empty(self) -> bool:
        return not self.rows


def _tokens(name: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in str(name).lower()).split()
            if t]


def merge_dms_variants(actuals: dict) -> tuple[dict, dict]:
    """Collapse DMS spellings of the same person.

    The DMS carries both "AVINASH SONWANE" and "AVINASH DHRJA SONWANE" - the
    same manager with and without a middle name. Left alone these look like
    two people, split that manager's actuals in half, and make every plan
    name ambiguous. Two names are treated as one person when one's token set
    is contained in the other's; the shorter spelling becomes canonical.

    Returns (merged actuals, {variant: canonical}).
    """
    names = sorted(actuals, key=lambda n: (len(_tokens(n)), n))
    canonical: dict[str, str] = {}
    for n in names:
        tn = set(_tokens(n))
        hit = None
        for c in canonical.values():
            tc = set(_tokens(c))
            if tn and tc and (tn <= tc or tc <= tn):
                hit = c
                break
        canonical[n] = hit or n

    merged: dict = {}
    for n, c in canonical.items():
        bucket = merged.setdefault(c, {})
        for stage, v in (actuals.get(n) or {}).items():
            bucket[stage] = bucket.get(stage, 0) + v
    return merged, canonical


def match_manager(plan_name: str, dms_names: list[str],
                  overrides: dict[str, str] | None = None) -> tuple[str | None, str]:
    """Match a plan manager to a DMS sales manager.

    The plan writes "Jitu" and "P. Sangole"; the DMS writes "JITENDRA SHAHU"
    and "PRASHANT SANGOLE". Matching on a shared three-character prefix per
    token handles both, but only when exactly one DMS name matches - an
    ambiguous match is left unmatched and reported, because silently
    attributing one manager's retails to another is worse than a gap in the
    table.

    Returns (dms_name or None, reason).
    """
    overrides = {str(k).strip().upper(): str(v).strip()
                 for k, v in (overrides or {}).items()}
    if plan_name.strip().upper() in overrides:
        return overrides[plan_name.strip().upper()], "mapped in norms.yaml"

    pt = _tokens(plan_name)
    if not pt:
        return None, "no name"
    hits = []
    for dms in dms_names:
        dt = _tokens(dms)
        if not dt:
            continue
        if all(any(a.startswith(b[:3]) or b.startswith(a[:3])
                   for b in dt) for a in pt):
            hits.append(dms)
    if len(hits) == 1:
        return hits[0], "matched on name"
    if len(hits) > 1:
        return None, "ambiguous: " + ", ".join(hits)
    return None, "no DMS manager with this name"


def _mapping(cfg) -> dict[str, str]:
    """Plan location -> DMS branch, upper-cased for comparison."""
    raw = (cfg.raw.get("target_locations") or {})
    return {str(k).strip().upper(): str(v).strip() for k, v in raw.items()}


def load_targets(path, cfg) -> TargetBook:
    """Read every sheet of the target file that carries the expected columns."""
    path = Path(path)
    book = TargetBook(filename=path.name)
    mapping = _mapping(cfg)
    branches_upper = {b.strip().upper(): b for b in cfg.branches}
    unmapped: set[str] = set()

    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet, header=0)
        df.columns = [str(c).strip().lower() for c in df.columns]
        if not all(c in df.columns for c in REQUIRED_COLUMNS):
            continue
        for _, r in df.iterrows():
            loc = str(r.get(COL_LOCATION, "")).strip()
            if not loc or loc.lower() == "nan":
                continue
            key = loc.upper()
            # An explicit mapping wins; otherwise a plan location that already
            # matches a DMS branch name is taken as-is.
            branch = mapping.get(key, branches_upper.get(key, ""))
            if not branch:
                unmapped.add(loc)
            book.rows.append(TargetRow(
                segment=str(r.get(COL_SEGMENT, "") or sheet).strip(),
                month=str(r.get(COL_MONTH, "")).strip(),
                location=loc, branch=branch,
                manager=str(r.get(COL_MANAGER, "")).strip(),
                model=str(r.get(COL_MODEL, "")).strip(),
                enq=_num(r.get("enq")), test_drive=_num(r.get("test_drive")),
                booking=_num(r.get("booking")), retail=_num(r.get("retail")),
            ))

    book.months = sorted({r.month for r in book.rows if r.month and r.month != "nan"})
    book.unmapped = sorted(unmapped)
    if book.unmapped:
        log.warning("Target locations with no branch mapping (targets ignored "
                    "for these): %s", ", ".join(book.unmapped))
    return book


def _num(v) -> float:
    try:
        f = float(v)
        return 0.0 if pd.isna(f) else f
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Period scaling
# ---------------------------------------------------------------------------
# A month holding fewer than this share of the records is treated as a
# straggler rather than as part of the period. Retail extracts routinely carry
# a handful of back-dated invoices from months earlier; taking min-to-max at
# face value stretched one August extract to 6.27 months and multiplied its
# target sixfold.
MONTH_SHARE_FLOOR = 0.02


def months_covered(df: pd.DataFrame | None, date_col: str) -> float | None:
    """How many months an extract really spans, as a fraction.

    29 days of August is 0.94 of a month, not 1; three whole months is 3.0.
    A fraction rather than a count of distinct months matters because a
    month-to-date pull would otherwise be judged against a whole month.

    Months holding a negligible share of the records are excluded first. The
    question being answered is "what period does this extract represent",
    and three back-dated invoices do not make an extract six months long.
    """
    if df is None or date_col not in df.columns:
        return None
    s = df[date_col].dropna()
    if s.empty:
        return None

    counts = s.dt.to_period("M").value_counts()
    keep = counts[counts >= max(1, len(s) * MONTH_SHARE_FLOOR)].index
    if len(keep):
        s = s[s.dt.to_period("M").isin(keep)]
    if s.empty:
        return None

    start, end = s.min(), s.max()
    days = (end.normalize() - start.normalize()).days + 1
    # Average month length over the span, so a February-heavy period is not
    # measured against a 31-day month.
    dim = (((start.days_in_month + end.days_in_month) / 2)
           if start != end else start.days_in_month)
    return round(days / dim, 3)


def stage_target(book: TargetBook | None, stage: str, months: float | None,
                 branch: str | None = None) -> tuple[float | None, str]:
    """Monthly target for a stage, scaled to the extract's own period.

    Returns (target, note). A note is returned whenever the figure was scaled,
    so the report can say so on the line rather than presenting a number whose
    basis is invisible.
    """
    if book is None or book.empty:
        return None, ""
    monthly = book.monthly(branch)[stage]
    if not monthly:
        return None, ("no target rows for this branch" if branch
                      else "no target rows in the file")
    if not months:
        return monthly, ""
    if abs(months - 1.0) < 0.05:
        return monthly, ""
    scaled = round(monthly * months, 1)
    return scaled, (f"monthly target {monthly:,.0f} × {months:.2f} months "
                    f"covered = {scaled:,.0f}")
