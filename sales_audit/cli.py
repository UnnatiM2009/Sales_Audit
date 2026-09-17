"""Command line entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from . import history
from .audit import run_audit
from .config import Config
from .loaders import (branches_present, filter_branches, filter_segment,
                      load_all, segments_present)
from . import physical
from .physical import write_template
from .report_docx import write_report
from .report_xlsx import write_findings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sales-audit",
        description="Generate a Sales Process Audit from DMS extracts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python run_audit.py                                  run with defaults
  python run_audit.py --input data/aug --output out    explicit folders
  python run_audit.py --make-sheet                     blank Physical Audit Sheet
  python run_audit.py --make-sheet --branch "AMRAVATI" blank sheet, that branch only
  python run_audit.py --config config/norms.yaml       alternate norms
""")
    p.add_argument("--input", "-i", default="input",
                   help="folder containing the DMS extracts (default: input)")
    p.add_argument("--output", "-o", default="output",
                   help="folder for the generated reports (default: output)")
    p.add_argument("--config", "-c", default="config/norms.yaml",
                   help="norms and weightage file (default: config/norms.yaml)")
    p.add_argument("--physical", default=None,
                   help="completed Physical Audit Sheet "
                        "(default: <input>/Physical_Audit_Sheet.xlsx)")
    p.add_argument("--make-sheet", "--make-checklist", dest="make_sheet",
                   action="store_true",
                   help="write a blank branch-wise Physical Audit Sheet and exit")
    p.add_argument("--prefix", default="Sales_Process_Audit",
                   help="output filename prefix")
    p.add_argument("--branch", action="append", default=None, metavar="NAME",
                   help="audit one branch only; repeat for several. "
                        "Use --list-branches to see the names.")
    p.add_argument("--per-branch", action="store_true",
                   help="also produce a separate report and workbook for every branch, "
                        "each saved in its own subfolder")
    p.add_argument("--list-branches", action="store_true",
                   help="print the branches found in the extracts and exit")
    p.add_argument("--label", default="",
                   help="tag this run in the history, e.g. 'week 36' or 'Aug MTD'")
    p.add_argument("--archive", action="store_true",
                   help="write reports into a dated subfolder so daily runs do not "
                        "overwrite each other")
    p.add_argument("--history", action="store_true",
                   help="print the trend of previous runs and exit")
    p.add_argument("--month", default=None, metavar="YYYY-MM",
                   help="the month to audit. Defaults to the last completed "
                        "month, so an audit run in September examines August.")
    p.add_argument("--segment", default=None, metavar="NAME",
                   help="audit one vehicle segment only: Personal, BEV, "
                        "Commercial or LMM")
    p.add_argument("--list-segments", action="store_true",
                   help="print the segments found in the extracts and exit")
    p.add_argument("--no-mobile", action="store_true",
                   help="ignore audits captured on a phone; score pillar J only "
                        "from a sheet in the input folder")
    p.add_argument("--no-history", action="store_true",
                   help="do not record this run in history.json")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s")
    log = logging.getLogger("sales-audit")

    try:
        cfg = Config.load(args.config)
    except FileNotFoundError:
        log.error("Config not found: %s", args.config)
        return 2
    except ValueError as e:
        log.error("%s", e)
        return 2

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.history:
        runs = history.load(out_dir)
        if not runs:
            log.info("No previous runs recorded in %s", out_dir / history.HISTORY_FILE)
            return 0
        _print_table(log, history.TABLE_HEADERS, history.as_table(runs))
        spark = history.sparkline(runs)
        if spark:
            log.info("Index trend: %s", spark)
        return 0

    if args.make_sheet:
        # --branch narrows the sheet to those outlets. A sheet with eleven tabs
        # is awkward on a single visit and invites filling in the wrong one.
        wanted = args.branch or cfg.branches
        unknown = [b for b in wanted if b not in cfg.branches]
        if unknown:
            log.error("Not a branch in %s: %s", args.config, ", ".join(unknown))
            log.error("Branches configured: %s", ", ".join(cfg.branches))
            return 2
        suffix = "_" + "_".join(_slug(b) for b in wanted) if args.branch else ""
        target = out_dir / f"Physical_Audit_Sheet_BLANK{suffix}.xlsx"
        write_template(target, wanted)
        log.info("Blank Physical Audit Sheet written: %s", target)
        log.info("Complete one tab per branch during the showroom walk, then save it")
        log.info("as 'Physical_Audit_Sheet.xlsx' in the input folder: %s",
                 Path(args.input).resolve())
        log.info("Re-run the audit and pillar J will be scored.")
        return 0

    try:
        data = load_all(args.input, cfg)
    except FileNotFoundError as e:
        log.error("%s", e)
        return 2

    if args.list_segments:
        from .segments import unclassified
        found = segments_present(data)
        log.info("Enquiries by segment:")
        for k, v in sorted(found.items(), key=lambda kv: -kv[1]):
            log.info("  %-12s %5d", k, v)
        stray = unclassified(data.get("F3"))
        if stray:
            log.warning("In no segment list:")
            for k, v in sorted(stray.items(), key=lambda kv: -kv[1]):
                log.warning("  %-24s %4d", k, v)
        return 0

    if args.list_branches:
        found = branches_present(data)
        log.info("Branches found in the extracts (%d):", len(found))
        for b in found:
            in_scope = "" if b in cfg.branches else "   [not in norms.yaml branch list]"
            log.info("  %s%s", b, in_scope)
        return 0

    physical_path = (Path(args.physical) if args.physical
                     else _find_sheet(Path(args.input)))
    physical_arg = physical_path if physical_path.exists() else None

    # No sheet in the input folder? Fall back to whatever was captured on a
    # phone. The walk has already been done; asking the auditor to export it
    # and copy the file across is a step that earns nothing.
    if physical_arg is None and not args.no_mobile:
        store_dir = Path(os.environ.get("AUDIT_PHYSICAL_DIR",
                                        out_dir / "physical"))
        try:
            from . import mobile_store
            if store_dir.is_dir() and mobile_store.has_any(store_dir):
                built = out_dir / "Physical_Audit_Sheet_from_phone.xlsx"
                used = mobile_store.build_sheet(
                    store_dir, built, cfg.branches,
                    only=args.branch[0] if args.branch else None)
                if used:
                    physical_arg = built
                    log.info("Physical audit taken from the phone captures: %s",
                             ", ".join(sorted(used)))
        except Exception as e:  # never let this stop the audit
            log.warning("Phone captures could not be read (%s)", e)

    scoped = filter_branches(data, args.branch) if args.branch else data
    if args.segment:
        seg = next((s for s in ("Personal", "BEV", "Commercial", "LMM")
                    if s.lower() == args.segment.strip().lower()), None)
        if seg is None:
            log.error("Unknown segment '%s'. Use Personal, BEV, Commercial or LMM.",
                      args.segment)
            return 2
        scoped = filter_segment(scoped, seg)
        if scoped.get("F3") is None or scoped.get("F3").empty:
            log.error("No %s records in these extracts.", seg)
            return 2
    if args.branch:
        found = branches_present(data)
        unknown = [b for b in args.branch if b not in found]
        if unknown:
            log.warning("Branch(es) not found in the extracts: %s", ", ".join(unknown))
        if not branches_present(scoped):
            log.error("No records remain after filtering to: %s", ", ".join(args.branch))
            return 2
    # A single-branch run scores that branch's showroom, not the network's.
    single = args.branch[0] if args.branch and len(args.branch) == 1 else None
    result, detail = run_audit(scoped, cfg, physical_arg, branch_scope=single,
                               audit_period=args.month)

    # Daily runs would overwrite each other; --archive keeps them side by side.
    report_dir = out_dir
    if args.archive:
        stamp = result.context.get("period_end") or datetime.now().strftime("%Y-%m-%d")
        report_dir = out_dir / stamp
        report_dir.mkdir(parents=True, exist_ok=True)

    suffix = ""
    if args.segment:
        suffix += "_" + _slug(args.segment)
    if args.branch:
        suffix = "_" + "_".join(_slug(b) for b in args.branch)
        report_dir = report_dir / _slug(args.branch[0]) if len(args.branch) == 1 else report_dir

    report_dir.mkdir(parents=True, exist_ok=True)
    docx_path = report_dir / f"{args.prefix}{suffix}.docx"
    xlsx_path = report_dir / f"{args.prefix}{suffix}_Findings.xlsx"
    write_report(docx_path, result, data, cfg)
    write_findings(xlsx_path, result, data, cfg, detail)

    # --- one report per branch, each saved separately ------------------------
    branch_results = []
    if args.per_branch:
        for b in branches_present(data):
            bdata = filter_branches(data, [b])
            try:
                bres, bdet = run_audit(bdata, cfg, physical_arg, branch_scope=b,
                                       audit_period=args.month)
            except Exception as e:  # a thin branch should not stop the batch
                log.warning("  %-24s skipped (%s)", b, e)
                continue
            bdir = (out_dir / "branches" / _slug(b))
            bdir.mkdir(parents=True, exist_ok=True)
            write_report(bdir / f"{args.prefix}.docx", bres, bdata, cfg)
            write_findings(bdir / f"{args.prefix}_Findings.xlsx", bres, bdata, cfg, bdet)
            branch_results.append((b, bres))
            log.info("  %-24s index %5.1f%%   %s", b, bres.index or 0,
                     bres.band(cfg.grading)["band"])

    # --- history and movement against the previous run -----------------------
    move = {}
    if not args.no_history:
        lbl = args.label
        if args.branch:
            lbl = (lbl + " · " if lbl else "") + "/".join(args.branch)
        row = history.record(result, cfg, result.context, lbl)
        runs = history.append(out_dir, row)
        move = history.movement(row, history.previous(runs, row))

    band = result.band(cfg.grading)
    log.info("-" * 62)
    delta = move.get("index_delta")
    arrow = ""
    if delta is not None:
        arrow = f"   {'+' if delta > 0 else ''}{delta:.1f} vs previous run"
    if args.segment:
        log.info("Segment             : %s", args.segment)
    per = getattr(scoped, "period", None)
    if per is not None:
        log.info("Audit month         : %s   (audited on %s)", per.label,
                 datetime.now().strftime("%d-%b-%Y"))
    log.info("Data covers         : %s (%s day(s))", result.context.get("period", "—"),
             result.context.get("days_covered") or "?")
    log.info("Sales Process Index : %.1f%%  (%s)%s", result.index or 0, band["band"], arrow)
    log.info("Scored              : %.1f of %g weightage points",
             result.scorable_weight, result.total_weight)
    log.info("Corrective actions  : %d", len(result.actions))
    if result.context.get("period_note"):
        log.warning("%s", result.context["period_note"])
    if move.get("pillars"):
        moved = sorted(move["pillars"].items(), key=lambda kv: kv[1])
        worst, best = moved[0], moved[-1]
        if worst[1] < 0:
            log.info("Biggest fall        : %s %+.1f pts", worst[0], worst[1])
        if best[1] > 0:
            log.info("Biggest gain        : %s %+.1f pts", best[0], best[1])
    if physical_arg is None:
        log.warning("Physical Audit Sheet not supplied and no phone captures "
                    "found — pillar J unscored.")
        log.warning("  Generate a blank one:  python run_audit.py --make-sheet")
        log.warning("  Then save it as 'Physical_Audit_Sheet.xlsx' in: %s",
                    Path(args.input).resolve())
    if args.branch:
        log.info("Branch scope        : %s", ", ".join(args.branch))
    if branch_results:
        log.info("Per-branch reports  : %s", out_dir / "branches")
    log.info("Report              : %s", docx_path)
    log.info("Findings workbook   : %s", xlsx_path)
    if not args.no_history:
        log.info("History             : %s  (--history to see the trend)",
                 out_dir / history.HISTORY_FILE)
    log.info("-" * 62)
    return 0


def _find_sheet(input_dir: Path) -> Path:
    """Locate the completed Physical Audit Sheet(s) in the input folder.

    One sheet is used as it is. Several — one per branch, which is what
    auditing branch by branch on a phone produces — are merged into a single
    workbook first, so nobody has to paste tabs together in Excel.
    """
    found = physical.find_sheets(input_dir)
    if not found:
        return input_dir / "Physical_Audit_Sheet.xlsx"      # reported as missing
    if len(found) == 1:
        return found[0]
    # Merge into a temp file rather than back into input/, so the next run does
    # not find a file it wrote itself sitting among the extracts.
    import tempfile
    merged = Path(tempfile.mkdtemp(prefix="sales_audit_")) / "Physical_Audit_Sheet.xlsx"
    branches = physical.merge_sheets(found, merged)
    if not branches:
        return found[0]
    logging.getLogger("sales-audit").info(
        "Merged %d Physical Audit Sheets: %s", len(found), ", ".join(branches))
    return merged


def _slug(name: str) -> str:
    """Filesystem-safe branch name for folders and filenames."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(name).strip()]
    return "".join(keep).strip("_") or "branch"


def _print_table(log, headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    log.info("%s", line)
    log.info("%s", "-" * len(line))
    for r in rows:
        log.info("%s", "  ".join(c.ljust(w) for c, w in zip(r, widths)))


if __name__ == "__main__":
    sys.exit(main())
