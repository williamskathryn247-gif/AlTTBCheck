"""
matcher.py — Pure Tesseract OCR + rule-based compliance field extraction and matching.

CASE SENSITIVITY POLICY
────────────────────────
- Field DETECTION (finding text in the image):  case-insensitive  — OCR output varies
- Field COMPARISON (application vs label):       case-sensitive    — labels must match
  Exceptions (only where case is meaningless):
    • alcohol_content  — numeric comparison only (45% == 45.0%, tolerance ±0.5%)
    • net_contents     — numeric/unit comparison (750 mL == 0.75 L)
    • health_warning   — boolean present/absent (not a text comparison)
    • country_of_origin — synonym-aware but still case-normalised (USA == United States)
  All other fields (brand_name, class_type, producer_address):
    → whitespace is collapsed, leading/trailing space stripped, but CASE IS PRESERVED
    → "Blue Ridge Bourbon" ≠ "BLUE RIDGE BOURBON" → mismatch
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

MANDATORY_FIELDS = [
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer_address",
    "country_of_origin",
    "health_warning",
]

FIELD_LABELS = {
    "brand_name":        "Brand Name",
    "class_type":        "Class/Type Designation",
    "alcohol_content":   "Alcohol Content",
    "net_contents":      "Net Contents",
    "producer_address":  "Name & Address of Bottler/Producer",
    "country_of_origin": "Country of Origin",
    "health_warning":    "Government Health Warning Statement",
}

CLASS_TYPES = [
    "Straight Bourbon Whisky", "Bourbon Whisky", "Tennessee Whisky",
    "Blended Whisky", "Scotch Whisky", "Irish Whiskey", "Rye Whisky",
    "Malt Whisky", "Grain Whisky", "American Whisky",
    "Vodka", "Gin", "Rum", "Tequila", "Mezcal", "Brandy", "Cognac",
    "Cabernet Sauvignon", "Merlot", "Chardonnay", "Pinot Noir",
    "Pinot Grigio", "Sauvignon Blanc", "Riesling", "Zinfandel",
    "Red Wine", "White Wine", "Rose Wine", "Sparkling Wine", "Champagne",
    "Prosecco", "Port Wine", "Dessert Wine", "Table Wine",
    "Pale Ale", "India Pale Ale", "IPA", "Lager", "Stout", "Porter",
    "Wheat Beer", "Hefeweizen", "Pilsner", "Amber Ale", "Sour Ale",
    "Hard Cider", "Hard Seltzer", "Malt Beverage",
]

# Lower-cased for detection only
_CLASS_TYPES_LOWER = {ct.lower(): ct for ct in CLASS_TYPES}

COUNTRY_TERMS_LOWER = [
    "united states", "usa", "u.s.a", "u.s.", "product of usa",
    "product of the united states", "domestic",
    "product of france", "france", "scotland", "ireland", "canada",
    "mexico", "italy", "spain", "germany", "australia", "new zealand",
    "argentina", "chile", "portugal", "japan", "product of",
]

US_SYNONYMS = frozenset({
    "united states", "usa", "u.s.a", "u.s.",
    "product of usa", "product of the united states", "domestic",
    "product of u.s.a", "product of u.s.", "american",
})

HEALTH_WARNING_KEYWORDS = [
    "government warning",
    "surgeon general",
    "birth defects",
    "impairs your ability",
    "health problems",
    "drive a car",
    "operate machinery",
]


# ── Image preprocessing ────────────────────────────────────────────────────────

def preprocess_image(image_bytes: bytes) -> np.ndarray:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image bytes")
    # Upscale small images — Tesseract accuracy improves significantly at 300 DPI+
    h, w = img.shape[:2]
    if max(h, w) < 1800:
        scale = 1800 / max(h, w)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray     = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    clahe    = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)
    kernel   = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp    = cv2.filter2D(enhanced, -1, kernel)
    _, binarised = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binarised


def run_ocr(image_bytes: bytes) -> str:
    processed = preprocess_image(image_bytes)
    pil_img   = Image.fromarray(processed)
    text = pytesseract.image_to_string(pil_img, config=r"--oem 3 --psm 6 --dpi 300")
    logger.debug(f"OCR output ({len(text)} chars): {text[:200]!r}")
    return text


# ── Helpers ────────────────────────────────────────────────────────────────────

def _collapse_whitespace(text: str) -> str:
    """Strip and collapse internal whitespace — PRESERVES CASE."""
    return re.sub(r"\s+", " ", text).strip()

def _lower(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# ── Field extractors (detection is case-insensitive; returned value preserves original case) ──

def extract_brand_name(text: str) -> Optional[str]:
    # Handle two-line format: "Brand Name:\nValue" (application forms)
    m = re.search(r"brand(?:\s*name)?\s*[:\-]?\s*\n?([^\n]{2,80})", text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        # Skip if the captured text is itself a field label
        if (val
                and not re.match(r"(REF|APP|TTB|Class|Alcohol|Net|Name|Country)[:\s/]", val, re.IGNORECASE)
                and len(val) > 2):
            return _collapse_whitespace(val)

    # Fallback for bottle labels: first non-header, non-reference capitalised line
    skip = re.compile(
        r"^(REF[:\s]|APP-|TTB|GOVERNMENT|UNITED STATES|ALCOHOL|BOTTLED|DISTILLED|"
        r"IMPORTED|NET CONTENTS|CLASS|COUNTRY|PRODUCT OF|\d)",
        re.IGNORECASE
    )
    for line in text.splitlines():
        line = line.strip()
        if (len(line) > 3
                and len(line.split()) >= 1
                and not skip.match(line)
                and not re.match(r"(REF|APP)\s*[:\-]", line, re.IGNORECASE)
                # Must look like a brand: starts with uppercase, no colons
                and re.match(r"[A-Z]", line)
                and ":" not in line
                and len(line) <= 60):
            return line
    return None


def extract_class_type(text: str) -> Optional[str]:
    lower = _lower(text)
    # Labelled field first — most reliable
    m = re.search(r"class[/\s]?type\s*(?:designation)?\s*[:\-]?\s*(.+)", text, re.IGNORECASE)
    if m:
        val = _collapse_whitespace(m.group(1).split("\n")[0])
        # Make sure it actually matches a known type
        if any(ct.lower() in val.lower() for ct in CLASS_TYPES):
            return val

    # Search known types in full text (longest match first)
    for ct_lower, _ in sorted(_CLASS_TYPES_LOWER.items(), key=lambda x: -len(x[0])):
        if ct_lower in lower:
            m = re.search(re.escape(ct_lower), lower)
            if m:
                return _collapse_whitespace(text[m.start():m.end()])
    return None


def extract_alcohol_content(text: str) -> Optional[str]:
    """
    Extract alcohol content. Returns the matched string ONLY if the numeric
    value is plausible (1-99%). Returns None (not found) otherwise so that
    OCR misreads are not passed through as valid data.
    """
    # Priority order: % alc/vol first (most specific), then broader fallbacks.
    # Each pattern is validated before being returned.
    patterns = [
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*alc[a-z]*[/\s]?vol[a-z]*",  # 45% alc/vol, alcvol, alcivol
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:abv|alcohol by volume)",
        r"alc(?:ohol)?[\s/]?(?:by\s*vol(?:ume)?)?[.:\s]+?(\d{1,3}(?:\.\d+)?)\s*%",
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*alcohol",
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*alc",          # bare "45% alc"
        r"(\d{1,3}(?:\.\d+)?)\s*%",                 # any % (validated below)
        r"(\d{2,3})\s*proof",                          # proof as last resort
    ]
    best = None
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            nums = re.findall(r"\d+(?:\.\d+)?", m.group(0))
            if not nums:
                continue
            val = float(nums[0])
            # For proof, convert to ABV equivalent for range check
            check_val = val / 2 if "proof" in pat else val
            if 1.0 <= check_val <= 99.0:
                best = _collapse_whitespace(m.group(0))
                # Stop at first high-confidence match (contains alc or vol keyword)
                if re.search(r"alc|vol|abv", m.group(0), re.IGNORECASE):
                    break
    return best


# Lookup table: normalised OCR text → canonical unit
_UNIT_MAP = {
    # Millilitres
    "ml": "mL", "ml.": "mL", "m1": "mL",   # m1 = OCR l→1
    "milliliter": "mL", "milliliters": "mL",
    "millilitre": "mL", "millilitres": "mL",
    # Litres
    "l": "L", "1": "L",  # standalone 1 after number = OCR l
    "liter": "L", "liters": "L", "litre": "L", "litres": "L",
    # Centilitres
    "cl": "cL", "c1": "cL",
    "centiliter": "cL", "centiliters": "cL",
    "centilitre": "cL", "centilitres": "cL",
    # Fluid ounces
    "floz": "fl oz", "fl.oz": "fl oz", "fl oz": "fl oz",
    "fluidounce": "fl oz", "fluidounces": "fl oz",
    "fl.oz.": "fl oz",
}

def _normalise_volume_unit(raw: str) -> Optional[str]:
    """
    Map a raw OCR unit string to a canonical unit.
    Returns None for unrecognised/garbage units (mb, mi, mib, mg, etc.).
    """
    u = raw.strip().lower().replace(" ", "")
    return _UNIT_MAP.get(u)


def _to_ml_strict(amount: float, raw_unit: str) -> Optional[float]:
    """Convert amount + raw OCR unit to mL. Returns None for unrecognised units."""
    canon = _normalise_volume_unit(raw_unit)
    if canon is None:
        return None
    if canon == "mL":  return amount
    if canon == "L":   return amount * 1000
    if canon == "cL":  return amount * 10
    if canon == "fl oz": return amount * 29.5735
    return None


def extract_net_contents(text: str) -> Optional[str]:
    """
    Extract net contents using strict unit validation.
    Only recognised volume units (mL, L, cL, fl oz) are accepted.
    OCR misreads (mb, mi, mib, mg, etc.) are rejected → returns None.

    Handles multi-word units (fl oz), OCR variants (m1, c1), and
    common formats: 750 mL, 0.75 L, 75 cL, 25.4 fl oz.
    """
    # Candidate patterns ordered most-specific first.
    # Each captures (amount, unit) — unit validated via _normalise_volume_unit.
    candidate_patterns = [
        # fl oz / fluid ounces — must come first (two-word unit)
        r"(\d{1,4}(?:\.\d+)?)\s*(fl\.?\s*oz(?:s|\.)?)(?=[\s\n,|()\[]|$)",
        r"(\d{1,4}(?:\.\d+)?)\s*(fluid\s*ounces?)(?=[\s\n,|()\[]|$)",
        # mL / ml / ML / m1
        r"(?<!\d)(\d{1,4}(?:\.\d+)?)\s*(m[lL1])(?=[\s\n,|()\[]|$)",
        r"(?<!\d)(\d{1,4}(?:\.\d+)?)\s*(milliliter[s]?)(?=[\s\n,|()\[]|$)",
        r"(?<!\d)(\d{1,4}(?:\.\d+)?)\s*(millilitre[s]?)(?=[\s\n,|()\[]|$)",
        # cL / cl / c1
        r"(\d{1,3}(?:\.\d+)?)\s*(c[lL1])(?=[\s\n,|()\[]|$)",
        r"(\d{1,3}(?:\.\d+)?)\s*(centiliter[s]?)(?=[\s\n,|()\[]|$)",
        r"(\d{1,3}(?:\.\d+)?)\s*(centilitre[s]?)(?=[\s\n,|()\[]|$)",
        # L / l / litre  (after number, standalone)
        r"(\d{1,2}(?:\.\d+)?)\s*(liters?|litres?|[lL])(?=[\s\n,|()\[]|$)",
        # Net contents labelled field
        r"[Nn]et\s*[Cc]ontents?\s*[:\-]?\s*(\d{1,4}(?:\.\d+)?)\s*(\S+)",
    ]

    for pat in candidate_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        try:
            amount = float(m.group(1))
        except ValueError:
            continue
        unit_raw = m.group(2).strip()
        # Normalise and validate the unit
        canon = _normalise_volume_unit(unit_raw)
        if canon is None:
            continue
        ml = _to_ml_strict(amount, unit_raw)
        if ml is None:
            continue
        # Plausible range: 25 mL (mini) to 30,000 mL (keg)
        if not (25 <= ml <= 30000):
            continue
        return f"{m.group(1)} {canon}"

    return None


def extract_producer_address(text: str) -> Optional[str]:
    # Labelled field patterns (application forms)
    labelled = [
        r"(?:name\s*(?:&|and)\s*address\s*of\s*bottler[^:\n]*)[:\-]?\s*\n?([^\n]{10,200})",
        r"(?:bottler|producer|importer|brewer)\s*[:\-]\s*\n?([^\n]{10,200})",
    ]
    for pat in labelled:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = _collapse_whitespace(m.group(1))
            # Strip accidental field-label prefix (e.g. "Name & Address of Bottler/Producer:, ")
            val = re.sub(r"^[Nn]ame\s*(?:&|and)\s*[Aa]ddress[^,]*,\s*", "", val)
            if len(val) > 8:
                return val[:250]

    # "Bottled by:" on bottle labels — grab the NEXT 2 lines (name + address)
    m = re.search(
        r"(?:bottled|distilled|produced|imported|manufactured)\s+(?:by|for)\s*[:\-]?\s*\n?"
        r"([^\n]{3,80})\n([^\n]{3,80})",
        text, re.IGNORECASE
    )
    if m:
        name = _collapse_whitespace(m.group(1))
        addr = _collapse_whitespace(m.group(2))
        if len(name) > 3 and len(addr) > 3:
            return f"{name}, {addr}"

    # Single-line "Bottled by: Name, Address"
    m = re.search(
        r"(?:bottled|distilled|produced|imported|manufactured)\s+(?:by|for)\s*[:\-]?\s*(.{10,200}?)(?:\n|$)",
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        val = _collapse_whitespace(m.group(1))
        if len(val) > 8:
            return val[:250]

    # US address pattern — walk back one line to grab the company name too
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"[A-Z]{2}\s*\d{5}", line):
            if i > 0:
                prev = lines[i-1].strip()
                curr = line.strip()
                if len(prev) > 3 and not re.search(r"(bottled|distilled|produced|imported|by|for)", prev, re.IGNORECASE):
                    return _collapse_whitespace(f"{prev}, {curr}")
            m2 = re.search(r"([A-Za-z].{5,60},\s*[A-Z]{{2}}\s*\d{{5}})", line)
            if m2:
                return _collapse_whitespace(m2.group(1))
    return None


def extract_country_of_origin(text: str) -> Optional[str]:
    lower = _lower(text)

    # Prefer labelled field — most reliable
    m = re.search(r"country\s+of\s+origin\s*[:\-]?\s*\n?(.{3,60}?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        val = _collapse_whitespace(m.group(1))
        if len(val) >= 3:
            return val

    # "Product of X" — capture the FULL phrase including the country name
    m = re.search(r"(product\s+of\s+[A-Za-z][A-Za-z ]{1,30}?)(?:[\n,|()\.\[]|$)", text, re.IGNORECASE)
    if m:
        val = _collapse_whitespace(m.group(1))
        if len(val) > 10:
            return val

    # Search known country terms — longest first, word-boundary aware
    for term in sorted(COUNTRY_TERMS_LOWER, key=len, reverse=True):
        if term == "product of":  # skip bare partial — caught above
            continue
        if term not in lower:
            continue
        pattern = r"(?:^|[\s\n,|()])(" + re.escape(term) + r")(?:[\s\n,|()|\.]|$)"
        m = re.search(pattern, lower)
        if m:
            start, end = m.start(1), m.end(1)
            return _collapse_whitespace(text[start:end])

    return None


def extract_health_warning(text: str) -> bool:
    lower = _lower(text)
    hits = sum(1 for kw in HEALTH_WARNING_KEYWORDS if kw in lower)
    return hits >= 2


def extract_all_fields(text: str) -> Dict:
    return {
        "brand_name":        extract_brand_name(text),
        "class_type":        extract_class_type(text),
        "alcohol_content":   extract_alcohol_content(text),
        "net_contents":      extract_net_contents(text),
        "producer_address":  extract_producer_address(text),
        "country_of_origin": extract_country_of_origin(text),
        "health_warning":    "present" if extract_health_warning(text) else "absent",
    }


# ── Case-sensitive field comparison ───────────────────────────────────────────

def _strip_ws(v: str) -> str:
    """Collapse internal whitespace, preserve case."""
    return re.sub(r"\s+", " ", v).strip()

def _pct_value(s: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    return float(m.group(1)) if m else None

def _to_ml(s: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|milliliter|l\b|liter|litre)", s, re.IGNORECASE)
    if not m:
        return None
    val  = float(m.group(1))
    unit = m.group(2).lower()
    return val * 1000 if unit.startswith("l") else val


def compare_field(field: str, app_val: Optional[str], label_val: Optional[str]) -> str:
    """
    Compare extracted values for one field.
    Returns: 'match' | 'mismatch' | 'missing_application' | 'missing_label' | 'missing_both'

    Case handling per field:
      brand_name, class_type, producer_address  → CASE-SENSITIVE (whitespace-normalised only)
      alcohol_content                            → numeric ±0.5% tolerance (case irrelevant)
      net_contents                               → numeric mL equivalence  (case irrelevant)
      country_of_origin                          → synonym-aware, lowercase fold only
      health_warning                             → boolean present/absent
    """
    app_absent   = app_val   is None or _strip_ws(str(app_val)).lower() in ("", "absent", "none")
    label_absent = label_val is None or _strip_ws(str(label_val)).lower() in ("", "absent", "none")

    if app_absent and label_absent:  return "missing_both"
    if app_absent:                   return "missing_application"
    if label_absent:                 return "missing_label"

    a = _strip_ws(str(app_val))
    b = _strip_ws(str(label_val))

    # ── health_warning ─────────────────────────────────────────────────────────
    if field == "health_warning":
        al, bl = a.lower(), b.lower()
        if al == bl == "present":  return "match"
        if al == "present":        return "missing_label"
        if bl == "present":        return "missing_application"
        return "mismatch"

    # ── alcohol_content: numeric comparison, ±0.5% tolerance ──────────────────
    if field == "alcohol_content":
        pct_a, pct_b = _pct_value(a), _pct_value(b)
        if pct_a is not None and pct_b is not None:
            return "match" if abs(pct_a - pct_b) <= 0.5 else "mismatch"
        # Fall through to case-sensitive string compare if no % found
        return "match" if a == b else "mismatch"

    # ── net_contents: volume equivalence ──────────────────────────────────────
    if field == "net_contents":
        ml_a, ml_b = _to_ml(a), _to_ml(b)
        if ml_a is not None and ml_b is not None:
            return "match" if abs(ml_a - ml_b) < 5 else "mismatch"
        return "match" if a == b else "mismatch"

    # ── country_of_origin: synonym-aware, lowercase fold ──────────────────────
    if field == "country_of_origin":
        al, bl = a.lower(), b.lower()

        def _country_core(s: str) -> str:
            """Strip 'Product of', 'Imported from' prefixes and normalise."""
            s = re.sub(r"^(product\s+of|imported\s+from|made\s+in)\s+", "", s.strip(), flags=re.IGNORECASE)
            return s.strip().lower()

        core_a = _country_core(al)
        core_b = _country_core(bl)

        # Both resolve to USA synonyms → match
        if any(t in al for t in US_SYNONYMS) and any(t in bl for t in US_SYNONYMS):
            return "match"

        # Both resolve to same country core (e.g. "Mexico" == "Product of Mexico")
        if core_a and core_b and core_a == core_b:
            return "match"

        # One contains the other (e.g. "Mexico" ⊂ "Product of Mexico")
        if core_a and core_b and (core_a in core_b or core_b in core_a):
            return "match"

        return "match" if al == bl else "mismatch"

    # ── brand_name, class_type, producer_address: CASE-SENSITIVE ──────────────
    return "match" if a == b else "mismatch"


def build_discrepancies(fields_status: Dict, app_fields: Dict, label_fields: Dict) -> List[str]:
    out = []
    for field, status in fields_status.items():
        label = FIELD_LABELS[field]
        if status == "match":
            continue
        elif status == "mismatch":
            out.append(
                f"{label}: application has '{app_fields.get(field)}' "
                f"but label has '{label_fields.get(field)}' "
                f"[case-sensitive mismatch]"
            )
        elif status == "missing_application":
            out.append(f"{label}: missing from the application form")
        elif status == "missing_label":
            out.append(f"{label}: missing from the bottle label")
        elif status == "missing_both":
            out.append(f"{label}: not found in either document")
    return out


# ── Public interface ───────────────────────────────────────────────────────────

def compare_pair(app_bytes: bytes, label_bytes: bytes, pair_index: int) -> Dict:
    try:
        app_text   = run_ocr(app_bytes)
        label_text = run_ocr(label_bytes)
    except Exception as e:
        return _error_result(pair_index, f"OCR failed: {e}")

    app_fields   = extract_all_fields(app_text)
    label_fields = extract_all_fields(label_text)

    fields_status = {
        field: compare_field(field, app_fields[field], label_fields[field])
        for field in MANDATORY_FIELDS
    }

    bad           = [s for s in fields_status.values() if s != "match"]
    overall       = "match" if not bad else "mismatch"
    matched_count = sum(1 for s in fields_status.values() if s == "match")
    confidence    = round(matched_count / len(MANDATORY_FIELDS), 2)
    discrepancies = build_discrepancies(fields_status, app_fields, label_fields)

    return {
        "pair_index":         pair_index,
        "overall_status":     overall,
        "confidence_score":   confidence,
        "application_fields": app_fields,
        "label_fields":       label_fields,
        "fields":             fields_status,
        "discrepancies":      discrepancies,
        "notes":              f"{matched_count}/{len(MANDATORY_FIELDS)} fields matched.",
    }


def _error_result(pair_index: int, message: str) -> Dict:
    return {
        "pair_index":         pair_index,
        "overall_status":     "error",
        "confidence_score":   0.0,
        "application_fields": {f: None for f in MANDATORY_FIELDS},
        "label_fields":       {f: None for f in MANDATORY_FIELDS},
        "fields":             {f: "missing_both" for f in MANDATORY_FIELDS},
        "discrepancies":      [message],
        "notes":              "Processing error — manual review required.",
    }


def process_batch(pairs: List[Dict], progress_callback=None) -> List[Dict]:
    results = []
    for pair in pairs:
        idx = pair["pair_index"]
        try:
            result = compare_pair(pair["app_bytes"], pair["label_bytes"], idx)
        except Exception as e:
            logger.error(f"Unhandled error for pair {idx}: {e}")
            result = _error_result(idx, str(e))
        results.append(result)
        if progress_callback:
            progress_callback(idx, result)
    return results

# Alias
extract_fields_with_claude = compare_pair
