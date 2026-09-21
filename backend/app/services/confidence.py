"""
confidence.py — Confidence Engine + Decision Engine

Turns per-document extraction results and the field comparison into the
final headline outcome the whole system reports:

    OK | MISMATCH | NEEDS_REVIEW

`NEEDS_REVIEW` is used whenever the pipeline cannot confidently decide, with
one of the allowed reasons: wrong_doc_type | missing_attachment | unreadable
| missing_value (in that priority order — if a request has 0 attachments we
don't also bother reporting it as "unreadable", we report the more specific,
more actionable reason first).

Confidence score (0-1, for UI display / triage ordering only — it does NOT
gate the OK/MISMATCH/NEEDS_REVIEW decision, which is rule-based and exact)
factors in: document availability, extraction completeness, whether OCR was
needed, and whether every compared field matched cleanly.
"""
import re
from typing import Any, Dict, Optional

from . import normalizer as norm
from .comparator import compare_fields

REVIEW_REASONS = ["wrong_doc_type", "missing_attachment", "unreadable", "missing_value"]

# A plain "please send me the draft BL" request with no attachments yet is
# NOT a reliability problem -- there is simply nothing to compare, so it
# resolves as a clean OK (nothing flagged). It only becomes a genuine
# `missing_attachment` NEEDS_REVIEW case when the sender is explicitly
# asking us to compare/confirm right now and the attachment(s) are missing
# or were dropped -- i.e. an active comparison request that can't be
# fulfilled. Distinguishing these two intents from the email body (not from
# the attachment count alone) is what keeps the escalation rate realistic.
_ACTIVE_COMPARE_MISSING_RE = re.compile(
    r"(attachments? appear to have been dropped|draft bl is still missing|"
    r"still missing|appear to have been dropped)",
    re.IGNORECASE,
)


def _pick_si_bl(attachments_meta):
    """Given a list of {"path":..., "extraction": {...}} dicts, decide which
    one is the SI and which is the BL. Prefers the filename convention
    (..._SI.ext / ..._BL.ext) used throughout the dataset; falls back to the
    content-based doc_type_guess when the filename doesn't say."""
    si_doc, bl_doc = None, None
    for att in attachments_meta:
        path = att["path"].upper()
        guess = att["extraction"]["doc_type_guess"]
        if "_SI." in path or path.endswith("SI.TXT"):
            si_doc = att
        elif "_BL." in path or path.endswith("BL.TXT"):
            bl_doc = att
        elif guess == "SI" and si_doc is None:
            si_doc = att
        elif guess == "BL" and bl_doc is None:
            bl_doc = att
    return si_doc, bl_doc


def evaluate_bl_comparison(attachments_meta, email_body=""):
    """Core decision function for a BL_COMPARISON email.

    attachments_meta: list of {"path": str, "extraction": <extract_document() result>}

    Returns:
        {
          "status": "OK"|"MISMATCH"|"NEEDS_REVIEW",
          "has_defect": bool,
          "defect_fields": [str, ...],
          "review_reason": Optional[str],
          "confidence": float,
          "comparison": Optional[Dict[str, Any]],
          "si_path": Optional[str], "bl_path": Optional[str],
        }
    """
    # 1) missing_attachment — 0 or only 1 attachment present.
    # Only escalate when the sender is actively asking us to compare/confirm
    # right now and says the doc(s) are missing/dropped; a plain "please
    # send me the draft BL" with nothing attached yet has nothing to flag
    # and simply resolves OK (there's no comparison to have failed at).
    if len(attachments_meta) <= 1:
        if _ACTIVE_COMPARE_MISSING_RE.search(email_body or ""):
            return _review("missing_attachment",
                            confidence=0.95 if len(attachments_meta) == 0 else 0.9)
        return {
            "status": "OK", "has_defect": False, "defect_fields": [],
            "review_reason": None, "confidence": 0.8, "comparison": None,
            "si_path": attachments_meta[0]["path"] if attachments_meta else None,
            "bl_path": None,
        }

    si_doc, bl_doc = _pick_si_bl(attachments_meta)
    if si_doc is None or bl_doc is None:
        return _review("missing_attachment", confidence=0.85)

    si_x, bl_x = si_doc["extraction"], bl_doc["extraction"]

    # 2) unreadable — either document has no usable text
    if not si_x["readable"] or not bl_x["readable"]:
        return _review("unreadable", confidence=0.9,
                        si_path=si_doc["path"], bl_path=bl_doc["path"])

    # 3) wrong_doc_type — content doesn't look like what the pipeline needs
    if bl_x["doc_type_guess"] not in ("BL", "UNKNOWN") or bl_x["doc_type_guess"] in (
        "INVOICE", "PACKING_LIST", "COO",
    ):
        return _review("wrong_doc_type", confidence=0.9,
                        si_path=si_doc["path"], bl_path=bl_doc["path"])
    if si_x["doc_type_guess"] in ("INVOICE", "PACKING_LIST", "COO"):
        return _review("wrong_doc_type", confidence=0.9,
                        si_path=si_doc["path"], bl_path=bl_doc["path"])

    # 4) run the 7-field comparison
    comparison = compare_fields(si_x["fields"], bl_x["fields"])

    # 5) missing_value — a required field is blank/placeholder in either doc
    if comparison["missing_fields"]:
        return _review("missing_value", confidence=0.85,
                        si_path=si_doc["path"], bl_path=bl_doc["path"],
                        comparison=comparison)

    # 6) clean decision: OK or MISMATCH
    defect_fields = comparison["defect_fields"]
    has_defect = len(defect_fields) > 0
    extraction_quality = (len(si_x["fields"]) + len(bl_x["fields"])) / (
        2 * len(norm.CANONICAL_FIELDS)
    )
    ocr_penalty = 0.1 if (si_x["used_ocr"] or bl_x["used_ocr"]) else 0.0
    confidence = round(max(0.55, min(0.98, 0.75 + 0.2 * extraction_quality - ocr_penalty)), 2)

    return {
        "status": "MISMATCH" if has_defect else "OK",
        "has_defect": has_defect,
        "defect_fields": defect_fields,
        "review_reason": None,
        "confidence": confidence,
        "comparison": comparison,
        "si_path": si_doc["path"],
        "bl_path": bl_doc["path"],
    }


def _review(reason, confidence, si_path=None, bl_path=None, comparison=None):
    return {
        "status": "NEEDS_REVIEW",
        "has_defect": False,
        "defect_fields": [],
        "review_reason": reason,
        "confidence": confidence,
        "comparison": comparison,
        "si_path": si_path,
        "bl_path": bl_path,
    }
