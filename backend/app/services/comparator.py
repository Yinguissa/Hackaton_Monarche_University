"""
comparator.py — Comparison Engine

Compares the 7 canonical fields extracted from an SI against the same 7
fields extracted from a BL.

Pipeline (per spec): normalize -> exact comparison -> semantic comparison
when required.

  * text fields (shipper/consignee/notify_party/ports): normalized exactly
    (case, punctuation, whitespace) and, if that doesn't match, a light
    "semantic" fuzzy check (SequenceMatcher ratio) catches trivial
    formatting drift such as "ABC SHIPPING LTD" vs "ABC Shipping Ltd."
    without masking a genuine business-data mismatch (different company
    entirely) — the ratio threshold is deliberately strict.
  * numeric fields (container_count/gross_weight_kg): normalized to an int
    and compared exactly — these are business facts, not free text, so a
    real difference (planted defect) must never be smoothed over.
"""
from difflib import SequenceMatcher

from . import normalizer as norm

TEXT_FIELDS = ["shipper", "consignee", "notify_party", "port_of_loading",
               "port_of_discharge"]
NUMERIC_FIELDS = ["container_count", "gross_weight_kg"]

# Above this ratio, two text values are considered the "same" despite minor
# formatting differences (punctuation, capitalisation already stripped by
# normalize_text_value — this only mops up residual noise like extra
# corporate suffixes or transliteration spacing).
FUZZY_MATCH_THRESHOLD = 0.90


def _text_matches(a, b):
    if a == b:
        return True
    if not a or not b:
        return False
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= FUZZY_MATCH_THRESHOLD


def compare_fields(si_fields, bl_fields):
    """Compare the 7 canonical fields between an SI and a BL.

    Returns:
        {
          "defect_fields": [str, ...],
          "missing_fields": [str, ...],   # present in neither/blank in either
          "field_results": {field: {"si": norm_si, "bl": norm_bl, "match": bool}},
        }
    """
    defect_fields = []
    missing_fields = []
    field_results = {}

    for field in norm.CANONICAL_FIELDS:
        si_raw = si_fields.get(field)
        bl_raw = bl_fields.get(field)

        si_blank = norm.is_blank(si_raw)
        bl_blank = norm.is_blank(bl_raw)
        if si_blank or bl_blank:
            missing_fields.append(field)
            field_results[field] = {"si": si_raw, "bl": bl_raw, "match": None}
            continue

        si_norm = norm.normalize_value(field, si_raw)
        bl_norm = norm.normalize_value(field, bl_raw)

        if field in NUMERIC_FIELDS:
            match = si_norm is not None and bl_norm is not None and si_norm == bl_norm
        else:
            match = _text_matches(si_norm, bl_norm)

        field_results[field] = {"si": si_norm, "bl": bl_norm, "match": match}
        if not match:
            defect_fields.append(field)

    return {
        "defect_fields": defect_fields,
        "missing_fields": missing_fields,
        "field_results": field_results,
    }
