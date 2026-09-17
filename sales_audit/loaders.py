"""Locate, read and normalise the DMS extracts.

Every loader is defensive: a missing file or a missing column degrades that
audit line to "not scored" rather than crashing the run. Column names differ
between extracts (Retails uses 'Dealer Location Name', the rest use
'Dealer Location'), so normalisation happens here and nowhere else.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import INPUT_FILES, Config, InputSpec

from . import physical

log = logging.getLogger(__name__)

# DMS timestamps arrive as '29-Aug-2026 12:36 PM'; the retail export uses a
# different, locale-dependent format, so both are attempted.
DMS_FORMAT = "%d-%b-%Y %I:%M %p"


def parse_dt(series: pd.Series) -> pd.Series:
    """Parse a DMS timestamp column, falling back to pandas inference."""
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    out = pd.to_datetime(series, format=DMS_FORMAT, errors="coerce")
    if out.isna().all():
        out = pd.to_datetime(series, errors="coerce", dayfirst=True)
    return out


def col(df: pd.DataFrame, *names: str) -> pd.Series | None:
    """Return the first column present from `names`, or None."""
    if df is None:
        return None
    for n in names:
        if n in df.columns:
            return df[n]
    return None


@dataclass
class Dataset:
    """One loaded extract plus its provenance."""

    ref: str
    label: str
    filename: str
    sheet: str
    df: pd.DataFrame

    @property
    def rows(self) -> int:
        return len(self.df)

    def source(self, *columns: str) -> str:
        """Traceability string used in every report cell."""
        base = f"{self.filename} › {self.sheet}"
        return f"{base} › {', '.join(columns)}" if columns else base


@dataclass
class AuditData:
    """All inputs for one audit run, keyed by reference."""

    datasets: dict[str, Dataset] = field(default_factory=dict)
    missing: list[InputSpec] = field(default_factory=list)
    input_dir: Path | None = None
    # Set by run_audit once the target file is read. Kept on the data object
    # so every pillar builder can reach it without changing their signature.
    targets: object | None = None
    target_branch: str | None = None
    segment: str | None = None
    period: object | None = None
    period_notes: dict | None = None

    def get(self, ref: str) -> pd.DataFrame | None:
        ds = self.datasets.get(ref)
        return ds.df if ds else None

    def ds(self, ref: str) -> Dataset | None:
        return self.datasets.get(ref)

    def src(self, ref: str, *columns: str) -> str:
        ds = self.datasets.get(ref)
        return ds.source(*columns) if ds else "Not supplied"

    def has(self, *refs: str) -> bool:
        return all(r in self.datasets for r in refs)


def _norm_stem(path: Path) -> str:
    """Filename stem normalised for matching: lowercase, separators unified.

    'Test_Drive Concerns.xlsx' and 'test-drive-concerns.xlsx' both become
    'test drive concerns', so spaces, underscores and hyphens are equivalent.
    """
    return re.sub(r"[\s_\-]+", " ", path.stem.lower()).strip()


def _matches(stem: str, target: str, allow_suffix: bool = False) -> int | None:
    """Score how well a filename stem matches a target. Lower is better.

    Only a prefix match counts, so 'ad lost enquiry' can never match 'enquiry'.
    A trailing date stamp is allowed: 'retails 29 08 2026' matches 'retails'.

    `allow_suffix` widens that to any trailing words, for the files this app
    writes with a branch name appended - Physical_Audit_Sheet_YAVATMAL.xlsx.
    It is granted per spec rather than globally, because the strictness is
    what stops AD_Lost_Enquiry.xlsx being scored as the enquiry book.
    """
    if stem == target:
        return 0
    if stem.startswith(target + " "):
        suffix = stem[len(target):].strip()
        # A date or version suffix is always fine.
        if re.fullmatch(r"[\d\s\-_./]+|v\d+|final|latest", suffix):
            return 1
        # A branch or code suffix, only where the spec expects one.
        if allow_suffix:
            return 2
    return None


def _verify(df: pd.DataFrame, spec: InputSpec) -> list[str]:
    """Return the signature columns missing from a candidate file."""
    if not spec.signature:
        return []
    cols = {str(c).strip().lower() for c in df.columns}
    return [c for c in spec.signature if c.lower() not in cols]


def _resolve(input_dir: Path) -> tuple[dict[str, tuple[Path, str]], list[str]]:
    """Bind each spec to a file. Most specific pattern claims first.

    Returns ({ref: (path, sheet)}, warnings). Sorting by target length
    descending means 'test drive concerns' claims its file before
    'test drive' gets a chance at it.
    """
    files = sorted(p for p in input_dir.glob("*.xls*") if not p.name.startswith("~$"))
    stems = {p: _norm_stem(p) for p in files}
    claimed: set[Path] = set()
    bound: dict[str, tuple[Path, str]] = {}
    warnings: list[str] = []

    for spec in sorted(INPUT_FILES, key=lambda s: -len(s.target)):
        candidates = []
        for p in files:
            if p in claimed:
                continue
            # Try every accepted name for this slot; best (lowest) rank wins.
            ranks = [r for r in (_matches(stems[p], t, spec.allow_suffix)
                                 for t in spec.targets)
                     if r is not None]
            if ranks:
                candidates.append((min(ranks), len(p.stem), p))
        candidates.sort()

        for _, _, path in candidates:
            try:
                xl = pd.ExcelFile(path)
                sheet = xl.sheet_names[0]
                head = pd.read_excel(xl, sheet, header=0, nrows=5)
            except Exception as e:  # unreadable file: report, do not crash
                warnings.append(f"{path.name} could not be read ({e})")
                continue
            missing = _verify(head, spec)
            if missing:
                warnings.append(
                    f"{path.name} looks like {spec.ref} ({spec.label}) but is missing "
                    f"expected column(s): {', '.join(missing)} — not used")
                continue
            bound[spec.ref] = (path, sheet)
            claimed.add(path)
            break

    # The Physical Audit Sheet is bound separately (it is read per branch, not
    # as a flat extract), so it is not an unmatched file and saying so only
    # teaches people to ignore this warning.
    unclaimed = [p.name for p in files
                 if p not in claimed and not physical.is_sheet_file(p.name)]
    if unclaimed:
        warnings.append("Files in the input folder that matched no expected report: "
                        + ", ".join(unclaimed))
    return bound, warnings


def load_all(input_dir: str | Path, cfg: Config) -> AuditData:
    """Load every recognised extract found in `input_dir`."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    data = AuditData(input_dir=input_dir)
    bound, warnings = _resolve(input_dir)
    for w in warnings:
        log.warning("%s", w)

    missing_required = [s for s in INPUT_FILES if s.required and s.ref not in bound]
    if missing_required:
        found = ", ".join(sorted(p.name for p, _ in bound.values())) or "none"
        raise FileNotFoundError(
            "Required input(s) missing: "
            + "; ".join(f"{s.pattern} ({s.label})" for s in missing_required)
            + f".\n         Looked in: {input_dir.resolve()}"
            + f"\n         Files matched: {found}"
            + "\n         Rename the extract to the expected filename, or point at the "
              "right folder with -i."
        )

    physical_present = bool(physical.find_sheets(input_dir))
    for spec in INPUT_FILES:
        if spec.ref not in bound:
            # The Physical Audit Sheet is bound by its own finder, which accepts
            # branch-suffixed exports and merges several. Reporting it missing
            # here when it is sitting right there would be wrong.
            if physical.is_sheet_file(spec.pattern) and physical_present:
                continue
            data.missing.append(spec)
            log.info("Optional input not found: %s (%s)", spec.pattern, spec.label)
            continue
        path, sheet = bound[spec.ref]
        df = pd.read_excel(path, sheet_name=sheet, header=0)
        df = _normalise(df, spec.ref, cfg.raw.get("booking_stages_counted"))
        data.datasets[spec.ref] = Dataset(
            ref=spec.ref, label=spec.label, filename=path.name, sheet=sheet, df=df
        )
        log.info("Loaded %-3s %-46s %6d rows  (%s)", spec.ref + ":", path.name,
                 len(df), spec.label)

    return data


def _normalise(df: pd.DataFrame, ref: str,
               booking_stages: list[str] | None = None) -> pd.DataFrame:
    """Add derived columns the metrics layer relies on."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Unified branch column across all extracts.
    branch = col(df, "Dealer Location", "Dealer Location Name", "VDN Dealer Location")
    df["_branch"] = branch.astype(str).str.strip() if branch is not None else pd.NA

    # Unified consultant column.
    sc = col(df, "Sales Consultant", "SC Name", "Sales Consultant Name")
    df["_sc"] = sc.astype(str).str.strip() if sc is not None else pd.NA

    # Unified sales-manager column, so targets can be reported manager-wise.
    # The retail extract does not carry one, so retail actuals are attributed
    # to a manager only where the DMS supplies the name.
    # The retail extract names the column "Team Lead"; the others use
    # "Sales Manager". Same role, so both feed the one normalised column.
    sm = col(df, "Sales Manager", "Sales Manager Name", "SM Name",
             "Team Lead", "Team Leader")
    df["_sm"] = sm.astype(str).str.strip() if sm is not None else pd.NA

    if ref == "F3":  # Enquiry
        df["_enq_dt"] = parse_dt(col(df, "Enquiry Date"))
        df["_assign_dt"] = parse_dt(col(df, "Enq Assign Date"))
        tds = col(df, "Test Drive Stage")
        df["_td_done"] = tds.eq("Test Drive Completed") if tds is not None else False
        fup = col(df, "Completed Followup Count")
        df["_followups"] = pd.to_numeric(fup, errors="coerce").fillna(0) if fup is not None else 0

    elif ref == "F4":  # Booking
        df["_created_dt"] = parse_dt(col(df, "Created Date"))
        # A booking counts when its stage says so. Which stages count is a
        # business decision, not a technical one, so it lives in norms.yaml
        # under booking_stages_counted. The default is "Booked" alone.
        st = col(df, "Booking Stage")
        if st is not None:
            wanted = {str(v).strip().upper() for v in (booking_stages or ["Booked"])}
            df["_booked"] = st.astype(str).str.strip().str.upper().isin(wanted)
        else:
            df["_booked"] = True

    elif ref == "F5":  # Test drive
        df["_created_dt"] = parse_dt(col(df, "TD Created Date"))
        df["_actual_start"] = parse_dt(col(df, "Actual TD Start Time"))
        df["_actual_end"] = parse_dt(col(df, "Actual TD End Time"))
        st = col(df, "Stage")
        df["_completed"] = st.eq("Test Drive Completed") if st is not None else False

    elif ref == "F6":  # Retails
        df["_invoice_dt"] = parse_dt(col(df, "Invoice Date and Time"))
        df["_booking_dt"] = parse_dt(col(df, "Booking Date and Time"))
        df["_allot_dt"] = parse_dt(col(df, "Allotment Date and Time"))
        df["_delnote_dt"] = parse_dt(col(df, "Delivery Note Date"))
        st = col(df, "Invoice Status")
        df["_invoiced"] = st.eq("Invoiced") if st is not None else True

    return df


def filter_branches(data: AuditData, branches: list[str] | None) -> AuditData:
    """Return a copy of the data restricted to the given branches.

    Used to audit one outlet on its own. Every extract carries a normalised
    `_branch` column, so the same filter applies uniformly — including the
    complaint files, where other outlets of the same group often appear.
    """
    if not branches:
        return data
    wanted = {str(b).strip().upper() for b in branches}
    out = AuditData(missing=list(data.missing), input_dir=data.input_dir,
                    targets=data.targets, period=data.period,
                    period_notes=data.period_notes,
                    # A branch audit is judged against that branch's slice of
                    # the plan, so record which one this is.
                    target_branch=(branches[0] if len(branches) == 1
                                   else data.target_branch))
    for ref, ds in data.datasets.items():
        df = ds.df
        if "_branch" in df.columns:
            mask = df["_branch"].astype(str).str.strip().str.upper().isin(wanted)
            df = df[mask].copy()
        out.datasets[ref] = Dataset(ref=ds.ref, label=ds.label, filename=ds.filename,
                                    sheet=ds.sheet, df=df)
    return out


def filter_segment(data: AuditData, segment: str | None) -> AuditData:
    """Restrict every extract to one vehicle segment.

    Personal, BEV, Commercial and LMM are separate businesses sharing a
    showroom, so they are audited separately. Rows whose model matches no
    list are dropped from a segment-scoped run and reported, never defaulted
    into a segment they might not belong to.
    """
    if not segment or str(segment).lower() in ("all", ""):
        return data
    from .segments import filter_to_segment

    out = AuditData(missing=list(data.missing), input_dir=data.input_dir,
                    targets=data.targets, period=data.period,
                    period_notes=data.period_notes, target_branch=data.target_branch,
                    segment=segment)
    for ref, ds in data.datasets.items():
        df = ds.df
        # Complaint and survey extracts carry a Product column, not a model
        # family; leave them whole rather than filtering on a guess.
        if ref in ("F3", "F4", "F5", "F6"):
            df = filter_to_segment(df, segment)
        out.datasets[ref] = Dataset(ref=ds.ref, label=ds.label,
                                    filename=ds.filename, sheet=ds.sheet, df=df)
    return out


def segments_present(data: AuditData) -> dict[str, int]:
    """Enquiry counts per segment, for the picker and the report."""
    from .segments import segment_series

    enq = data.get("F3")
    if enq is None:
        return {}
    seg = segment_series(enq)
    return seg.value_counts().to_dict() if not seg.empty else {}


def branches_present(data: AuditData) -> list[str]:
    """Branches that actually appear in the enquiry or retail extract."""
    found: set[str] = set()
    for ref in ("F3", "F6", "F4", "F5"):
        df = data.get(ref)
        if df is not None and "_branch" in df.columns:
            found |= {b for b in df["_branch"].dropna().astype(str).str.strip()
                      if b and b.lower() != "nan"}
    return sorted(found)


def audit_period(data: AuditData) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Earliest and latest enquiry date across the extracts."""
    enq = data.get("F3")
    if enq is None or "_enq_dt" not in enq:
        return None, None
    s = enq["_enq_dt"].dropna()
    return (s.min(), s.max()) if len(s) else (None, None)
