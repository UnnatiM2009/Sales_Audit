"""Configuration loading and the registry of expected input files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# -----------------------------------------------------------------------------
# Input file registry
# -----------------------------------------------------------------------------
# Each entry maps a short reference (used throughout the audit for traceability)
# to the filename pattern the loader looks for. `required` files stop the run if
# missing; optional ones simply leave their lines unscored.


@dataclass(frozen=True)
class InputSpec:
    ref: str
    pattern: str          # expected filename stem, matched loosely (see loaders)
    label: str
    required: bool
    scores: str           # human-readable note on what it feeds
    signature: tuple[str, ...] = ()   # columns that must be present to accept a match
    aliases: tuple[str, ...] = ()     # other filenames that are also accepted
    # True for files this app writes with a branch or code appended, e.g.
    # Physical_Audit_Sheet_NAGPUR_KAMPTHEE_ROAD.xlsx. Without this the
    # downloaded file is refused on upload, which is the worst kind of bug:
    # the app rejecting its own output.
    allow_suffix: bool = False

    @staticmethod
    def _norm(name: str) -> str:
        import re
        return re.sub(r"[\s_\-]+", " ", name.lower().rsplit(".", 1)[0]).strip()

    @property
    def target(self) -> str:
        """Pattern normalised for matching: lowercase, separators unified."""
        return self._norm(self.pattern)

    @property
    def targets(self) -> tuple[str, ...]:
        """Every accepted filename, longest first so the most specific wins."""
        names = (self.pattern,) + self.aliases
        return tuple(sorted({self._norm(n) for n in names}, key=len, reverse=True))


# Order does not matter: the loader claims files most-specific-first so that
# 'Test_Drive_Concerns.xlsx' can never be mistaken for the test drive extract.
# `signature` columns are verified before a match is accepted, so a wrongly
# named file is rejected with a warning rather than silently scored.
INPUT_FILES: tuple[InputSpec, ...] = (
    InputSpec("F3", "enquiry.xlsx", "Enquiry extract", True,
              "Pillars A, B, H",
              ("Enquiry Number", "Enquiry Date", "Stage")),
    InputSpec("F4", "booking.xlsx", "Booking extract", True,
              "Lines 2.2, 2.3, 3.2",
              ("Booking Number", "Booking Stage")),
    InputSpec("F5", "test_drive.xlsx", "Test drive extract", True,
              "Lines 2.1, 2.2, 9.1",
              ("Test Drive Number", "Stage")),
    InputSpec("F6", "retails.xlsx", "Retail invoice extract", True,
              "Pillars C, D, E, F, G",
              ("Invoice Number", "Invoice Status")),
    # The DMS exports these with singular names, and "Test Dive" is how the
    # portal spells it. Accepting both is cheaper than asking an auditor to
    # rename a file every month, and the column signature still guards against
    # the wrong file being bound.
    InputSpec("F7", "enquiry_concerns.xlsx", "Enquiry-stage complaints", False,
              "Line 7.2", ("Enquiry ID", "Rating"),
              aliases=("enquiry_concern.xlsx", "enquiry concern.xlsx",
                       "ad_enquiry.xlsx")),
    InputSpec("F8", "test_drive_concerns.xlsx", "Test-drive complaints", False,
              "Line 7.2", ("Test Drive Name", "Rating"),
              aliases=("test_drive_concern.xlsx", "test_dive_concern.xlsx",
                       "test_dive_concerns.xlsx", "test dive concern.xlsx",
                       "ad_test_drive.xlsx")),
    InputSpec("F9", "new_vehicle_delivery_experience.xlsx",
              "Delivery-experience complaints", False, "Line 7.2",
              ("VDN Number", "Response")),
    InputSpec("F10", "new_vehicle_delivery_experience_30_days.xlsx",
              "30-day post-delivery feedback", False, "Line 7.2",
              ("VDN Number", "Ratings")),
    InputSpec("F13", "ad_lost_enquiry.xlsx", "Lost-enquiry survey", False,
              "Lines 1.5, 7.2", ("Enquiry ID", "StageName"),
              aliases=("lost_enquiry.xlsx", "ad lost enquiry.xlsx",
                       "lost_enquiry_survey.xlsx", "ad_lost_enquiry_concern.xlsx")),
    InputSpec("F14", "booking_cancellation_concern.xlsx",
              "Booking cancellation survey", False, "Lines 2.3, 7.2",
              ("DMS Case No.", "Booking Cancellation Response"),
              aliases=("booking_cancellation.xlsx", "booking_cancellation_concerns.xlsx",
                       "booking cancellation concern.xlsx",
                       "ad_booking_cancellation.xlsx")),
    InputSpec("F12", "sales_target.xlsx", "Sales target by location and manager",
              False, "Lines 1.1, 3.1, 3.5, 3.6",
              ("location", "manager", "enq", "test_drive", "booking", "retail"),
              aliases=("sales_target_-_clean.xlsx", "sales target.xlsx",
                       "sales_target_clean.xlsx", "sales targets.xlsx",
                       "sales_targets.xlsx", "target.xlsx"),
              allow_suffix=True),
    InputSpec("F11", "physical_audit_sheet.xlsx", "Physical Audit Sheet (completed)",
              False, "Pillar J", (),
              aliases=("physical_audit.xlsx", "physical_check_sheet.xlsx",
                       "physical audit sheet.xlsx"),
              allow_suffix=True),
)

# Files the audit needs but which usually are not in the DMS export. Named
# explicitly so the report can state why a line is unscored rather than blank.
MISSING_DATA_NOTES: dict[str, str] = {
    "enquiry_target": "Enquiry target by month and branch",
    "retail_target": "Retail target by month and branch",
    "market_share_norm": "VAHAN registration data for the territory",
    "sanctioned_sc": "Sanctioned manpower roster",
    "stock_statement": "Stock statement with ageing",
    "nvd_score": "NVD / SSI score report (only complaint extracts supplied)",
    "escalation_log": "Escalation log with TAT",
    "footfall_register": "Gate entry / showroom footfall register",
    "rsa_report": "RSA policy issue report (policy number against VIN)",
    "ew_report": "Extended warranty policy issue report with plan mix",
}


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        cfg = cls(raw=raw, path=path)
        cfg.validate()
        return cfg

    # -- convenience accessors -------------------------------------------------
    @property
    def dealership(self) -> dict[str, Any]:
        return self.raw["dealership"]

    @property
    def branches(self) -> list[str]:
        return list(self.raw["dealership"]["branches"])

    @property
    def norms(self) -> dict[str, Any]:
        return self.raw["norms"]

    @property
    def targets(self) -> dict[str, Any]:
        return self.raw["targets"]

    @property
    def weights(self) -> dict[str, int]:
        return self.raw["weights"]

    @property
    def grading(self) -> list[dict[str, Any]]:
        return self.raw["grading"]

    @property
    def rag(self) -> dict[str, int]:
        return self.raw["rag"]

    @property
    def owners(self) -> dict[str, str]:
        """Corrective-action owner per pillar, with sensible fallbacks."""
        defaults = {
            "A_enquiry": "Sales CRM / Sales Manager",
            "B_conversion": "Branch Sales Managers",
            "C_volume": "Sales Manager",
            "D_manpower": "Sales Manager / HR",
            "E_stock": "Stock Controller",
            "F_profitability": "Sales GM / Sales Manager",
            "G_experience": "Sales Manager",
            "H_systems": "Sales CRM",
            "I_demo_fleet": "Branch Sales Managers",
            "J_physical": "Branch Sales Managers",
            "default": "Sales Manager",
        }
        defaults.update(self.raw.get("owners") or {})
        return defaults

    @property
    def signoff(self) -> list[str]:
        """Roles listed in the sign-off table of the report."""
        return list(self.raw.get("signoff")
                    or ["Auditor", "Sales Manager", "Sales GM", "OEM Area Sales Manager"])

    @property
    def period(self) -> dict[str, Any]:
        return self.raw.get("period", {
            "prorate_volume_norms": True,
            "min_days_to_score_volume": 7,
            "short_period_ageing_days": 15,
        })

    def norm(self, key: str) -> Any:
        return self.norms.get(key)

    def target(self, key: str) -> Any:
        return self.targets.get(key)

    def validate(self) -> None:
        total = sum(self.weights.values())
        if total != 100:
            raise ValueError(
                f"Pillar weights in {self.path} total {total}, expected 100. "
                "Adjust the 'weights' block."
            )
        if not self.branches:
            raise ValueError("At least one branch must be listed under dealership.branches.")
