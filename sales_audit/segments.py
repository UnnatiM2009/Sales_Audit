"""Which segment a vehicle belongs to.

Personal, BEV, Commercial and LMM are audited separately because they are
different businesses sharing a showroom floor: different targets, different
consultants, different norms. A Personal audit that quietly includes Jeeto
and Zor is not a Personal audit.

Classification is by longest key first. That is the whole trick, and it
matters: "Bolero Camper" is Commercial while "Bolero" is Personal, so a
shortest-first or arbitrary-order scan would file every Camper under
Personal. Sorting the keys by length before scanning makes the specific
name win over the general one, every time.
"""
from __future__ import annotations

import re

# --- Personal ---------------------------------------------------------------
PERSONAL_MODELS = [
    "ALTURAS", "ALTURAS G4", "BOLERO", "BOLERO NEO", "BOLERO NEO PLUS", "BOLERO P",
    "G4", "KUV100", "MARAZZO", "NEW SCORPIO", "NEW THAR", "NUVOSPORT", "QUANTO",
    "REXTON", "SCORPIO", "SCORPIO CLASSIC", "SCORPIO-N", "THAR", "THAR ROXX",
    "TUV300", "VERITO", "XUV 7XO", "XUV300", "XUV3XO", "XUV400", "XUV500",
    "XUV700", "XYLO", "TUV300 PLUS", "THRN", "X400", "SCRC", "X7XO", "X3XO",
    "X700", "NEO", "TH5D", "MRZO", "SCN",
    "X300", "BOL", "KUV1", "BOLP", "E2O", "NEOP", "XUV5",
    "E2OP", "LOGN", "NUSP", "TUV3", "ALTS", "REXT",
    "SCR", "SCRN", "TUVP",
]

# --- Commercial -------------------------------------------------------------
# Bolero pickup and camper variants are COMMERCIAL, not Personal. These
# multi-word keys are longer than the Personal key "BOLERO", so the
# longest-first scan matches them before "BOLERO" can claim the row.
COMMERCIAL_MODELS = [
    "BOLERO CAMPER", "BOLERO PIK UP", "BOLERO PIKUP", "BOLERO PICKUP",
    "BOLERO PICK UP", "BOLERO MAXITRUCK", "BOLERO MAXI TRUCK",
    "CAMPER", "IMPERIO", "MAXI TRUCK", "MAXX", "MAXX CITY", "MAXX HD",
    "MAXXIMO LOAD", "MXMO MINIVAN", "PICK UP", "SOFT TOP", "SUPRO LOAD",
    "SUPRO MINIVAN", "SUPRO PASSENGER", "SUPRO-MT", "SUPRO-MV", "VEERO",
    "SUORO-CV",
    "MXHD", "SUMT", "CMPR", "MAXI",
    "PUP", "SUPL", "UPP", "ESPV", "SUPP",
    "MXMO",
]

# --- LMM (last mile mobility) ----------------------------------------------
LMM_MODELS = [
    "ALFA LOAD", "ALFA PASSENGER", "ALFA PLUS", "E ALFA PLUS", "E-ALFA MINI",
    "E-ALFA MINI LOAD", "EALFA CARGO", "EALFA SUPER", "JETO", "TREO", "TRPL",
    "ZOR", "ALFL", "ZORG", "ZEO", "ALPL", "A301", "EALS", "ALFE",
    "JEETO", "UDO", "TREO PLUS", "TREO ZOR", "ZOR GRAND",
]

# --- BEV / MEL (Mahindra Electric Origin SUVs) ------------------------------
MEL_MODELS = ["BE 6", "BE6 FE", "E20 PLUS", "XEV 9E", "XEV 9S", "MBE6", "MXV9"]

SEGMENTS = ("Personal", "BEV", "Commercial", "LMM")

# Longest key first, so a specific name beats a general one.
_KEYS: list[tuple[str, str]] = sorted(
    [(m.strip().upper(), seg)
     for seg, models in (("Personal", PERSONAL_MODELS),
                         ("BEV", MEL_MODELS),
                         ("Commercial", COMMERCIAL_MODELS),
                         ("LMM", LMM_MODELS))
     for m in models if m.strip()],
    key=lambda kv: -len(kv[0]))

# Columns that might carry the model, in the order worth trying.
MODEL_COLUMNS = ("Product Family", "Model Group", "Model Description",
                 "Model Variant", "Variant Description", "Model Code",
                 "OEM Model Code")


def _norm(value) -> str:
    """Upper-case, collapse punctuation to single spaces."""
    s = str(value or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_segment_from_vehicle_model(model) -> str | None:
    """The segment for one model name, or None when nothing matches.

    Matching is on whole words so that a short key cannot fire inside a
    longer unrelated name — "SCR" must not match "SCRAP", and "NEO" must not
    match "NEON". Keys are tried longest first.
    """
    text = _norm(model)
    if not text:
        return None
    padded = f" {text} "
    for key, seg in _KEYS:
        k = _norm(key)
        if not k:
            continue
        if f" {k} " in padded:
            return seg
    return None


def segment_series(df, column: str | None = None):
    """Classify a whole frame. Returns a pandas Series of segment names."""
    import pandas as pd

    if df is None or len(df) == 0:
        return pd.Series(dtype="object")
    col = column or next((c for c in MODEL_COLUMNS if c in df.columns), None)
    if col is None:
        return pd.Series([None] * len(df), index=df.index, dtype="object")
    # One lookup per distinct model rather than per row: an enquiry book has
    # thousands of rows and a few dozen models.
    uniq = {v: get_segment_from_vehicle_model(v) for v in df[col].dropna().unique()}
    return df[col].map(uniq)


def filter_to_segment(df, segment: str | None, column: str | None = None):
    """Rows belonging to one segment. `segment` None returns the frame whole."""
    if df is None or not segment or segment.lower() in ("all", ""):
        return df
    seg = segment_series(df, column)
    if seg.empty:
        return df
    return df[seg.astype(str).str.upper() == segment.upper()]


def unclassified(df, column: str | None = None) -> dict[str, int]:
    """Model names no list claims, with how many rows each covers.

    Reported rather than swept into a default segment: a model missing from
    the lists is a gap in the lists, and silently filing it under Personal
    would corrupt the very audit the split exists to protect.
    """
    seg = segment_series(df, column)
    if seg.empty:
        return {}
    col = column or next((c for c in MODEL_COLUMNS if c in df.columns), None)
    if col is None:
        return {}
    missing = df.loc[seg.isna(), col].dropna()
    return missing.astype(str).str.strip().value_counts().to_dict()
