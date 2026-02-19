import os
import json
import logging
import asyncio
from typing import Dict, Optional

from dotenv import load_dotenv
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import AsyncGroq

from app.normalizer import extract_doctor_name


# -------------------------------------------------
# Logging
# -------------------------------------------------
logger = logging.getLogger(__name__)

# -------------------------------------------------
# Load Environment Variables
# -------------------------------------------------
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in environment variables.")

client = AsyncGroq(api_key=GROQ_API_KEY)


# =================================================
# LAYER 1: RULE-BASED PRE-FILTER
# =================================================
# These run BEFORE any LLM call.
# Goal: handle the obvious cases for free (no tokens spent).
#
# Two lists:
#   OBVIOUS_EXCLUDE → definitely NOT a private clinic → discard immediately
#   OBVIOUS_INCLUDE → definitely IS a private clinic  → accept immediately
#   anything else   → send to LLM (the genuinely ambiguous cases)

# Strong signals that something is NOT a private clinic.
# If any of these appear in the name, we discard without calling the LLM.
_EXCLUDE_KEYWORDS = [
    # Hospitals
    "hospital", "mustashfa", "مستشفى",
    # Pharmacies
    "pharmacy", "صيدلية",
    # Labs
    "lab", "laboratory", "معمل", "مختبر",
    # Imaging / diagnostic centers (not clinics)
    "scan", "x-ray", "xray", "ray", "imaging", "radiology",
    # Clearly corporate multi-branch centers
    "medical center", "مركز طبي", "مركز صحي",
]

# Strong signals that something IS a private clinic.
# Two conditions — either is sufficient (if no canceller present):
#   1. Has a doctor title (Dr. / دكتور / د.)
#   2. Has a clinic keyword (clinic / عيادة / dental clinic)
_DOCTOR_TITLES = [
    "dr.", "dr ", "دكتور", "د.",
]

_CLINIC_KEYWORDS = [
    "dental clinic", "dental clinics", "عيادة", " clinic", "clinics"
]

# These cancel out both conditions above.
# e.g. "Dr. X Medical Center" or "Hayat Medical Center" → NOT rule-accepted
_TITLE_CANCELLERS = [
    "center", "centre", "مركز", "centers",
    "hospital", "مستشفى",
    "scan", "lab", "معمل",
]


def _name_lower(name: str) -> str:
    """Lowercased name — computed once and reused."""
    return name.lower()


def is_obviously_not_clinic(name: str) -> bool:
    """
    Returns True if the name contains a strong non-clinic signal.
    Discarded immediately without calling the LLM.

    Examples that return True:
        "REFORM DENTAL CENTER"      → has "center"
        "DentaScan"                 → has "scan"
        "Al Salam Hospital"         → has "hospital"
        "Smile Zone Dental Centers" → has "centers"
    """
    n = _name_lower(name)
    return any(keyword in n for keyword in _EXCLUDE_KEYWORDS)


def is_obviously_a_clinic(name: str) -> bool:
    """
    Returns True if the name is unambiguously a private clinic.
    Accepted immediately without calling the LLM.

    Condition: (has doctor title OR has clinic keyword) AND no canceller.

    Examples that return True:
        "Dr. Ahmed Samy Dental Clinic"   → doctor title, no canceller
        "Hisham Sholkamy dental clinic"  → clinic keyword, no canceller
        "IVORY DENTAL CLINICS"           → clinic keyword, no canceller
        "Hayat clinic - عيادة حياة"      → clinic keyword + عيادة, no canceller

    Examples that return False (go to LLM):
        "Dr. X Medical Center"           → has canceller "center"
        "Ultra Dental Care"              → no title, no clinic keyword
        "Dental Valley October"          → no title, no clinic keyword
        "Genesis Dental Crew"            → no title, no clinic keyword
    """
    n = _name_lower(name)

    # If any canceller is present — send to LLM, don't rule-accept
    has_canceller = any(c in n for c in _TITLE_CANCELLERS)
    if has_canceller:
        return False

    has_title = any(t in n for t in _DOCTOR_TITLES)
    has_clinic_kw = any(k in n for k in _CLINIC_KEYWORDS)
    return has_title or has_clinic_kw


# =================================================
# LAYER 2: LLM CLASSIFICATION (AMBIGUOUS CASES ONLY)
# =================================================

SYSTEM_PROMPT = """You are a medical business classifier for Egypt.
Respond with a single valid JSON object only — no explanation, no markdown, no arrays."""

# This prompt only receives genuinely ambiguous cases —
# names without a clear doctor title and without obvious exclusion keywords.
# Examples of what reaches here:
#   "Ultra Dental Care And Esthetics"   → no Dr., no center → ambiguous
#   "Dental House"                      → no Dr., no center → ambiguous
#   "Hayat clinic"                      → no Dr., no center → ambiguous
#   "Dr. X Medical Center"              → Dr. + center conflict → ambiguous
#
# Because only hard cases reach here, the prompt can be lean.
# We add one targeted rule for the "center" conflict case.
USER_PROMPT_TEMPLATE = """Classify this Egyptian medical business into ONE category.

Categories: Private Clinic, Hospital, Medical Center, Lab, Pharmacy

Rules:
- "center/مركز" in name → Medical Center (even if it has Dr.)
- Small named clinics without center/hospital indicators → Private Clinic
- If still ambiguous → Private Clinic

Return ONLY: {{"category": "...", "confidence": "High|Medium|Low", "reason": "..."}}

Name: {clinic_name}
Address: {address}"""


# -------------------------------------------------
# Async LLM Call with Retry
# -------------------------------------------------
async def _call_llm(clinic_name: str, address: str) -> str:
    """
    Calls Groq LLM asynchronously. Only reached for ambiguous cases.
    Retries up to 3 times with exponential backoff on any failure.
    """
    # Option 3: trim address to first 60 chars — the city/neighbourhood
    # is all the LLM needs. Postal codes and long governorate strings
    # add tokens without improving classification accuracy.
    trimmed_address = (address or "")[:60].strip()

    user_prompt = USER_PROMPT_TEMPLATE.format(
        clinic_name=clinic_name,
        address=trimmed_address
    )

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True
    ):
        with attempt:
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0,
                # Option 3: JSON mode — forces the API to return valid
                # JSON at the infrastructure level, not just via prompt.
                # Eliminates the need for {start}/{end} extraction logic
                # and removes any possibility of prose contamination.
                response_format={"type": "json_object"}
            )

    return response.choices[0].message.content


# -------------------------------------------------
# Main Classification Function (Async)
# -------------------------------------------------
async def classify_clinic(clinic: Dict) -> Optional[Dict]:
    """
    Three-layer classification pipeline for a single clinic.

    Layer 1a — Rule-based exclusion  (free, instant)
    Layer 1b — Rule-based acceptance (free, instant)
    Layer 2  — LLM classification    (only for ambiguous cases)

    Returns:
        Clinic dict with doctor_name and confidence_score if Private Clinic.
        None otherwise.
    """
    name = clinic.get("clinic_name", "")
    address = clinic.get("address", "")

    # --------------------------------------------------
    # Layer 1a: Obviously NOT a clinic → discard
    # --------------------------------------------------
    if is_obviously_not_clinic(name):
        logger.info(f"[RULE-EXCLUDE] {name}")
        return None

    # --------------------------------------------------
    # Layer 1b: Obviously IS a clinic → accept immediately
    # --------------------------------------------------
    if is_obviously_a_clinic(name):
        logger.info(f"[RULE-ACCEPT]  {name}")
        clinic["confidence_score"] = "High"
        clinic["doctor_name"] = extract_doctor_name(name)
        return clinic

    # --------------------------------------------------
    # Layer 2: Genuinely ambiguous → send to LLM
    # --------------------------------------------------
    logger.info(f"[LLM]          {name}")

    try:
        raw_response = await _call_llm(clinic_name=name, address=address)

        # Only visible when running with --debug flag
        logger.debug(f"[LLM-RAW]      '{name}':\n{raw_response}")

        if not raw_response:
            logger.warning(f"Empty LLM response for: {name}")
            return None

        # With json_object mode the response IS valid JSON — no need
        # to search for { } boundaries anymore.
        parsed = json.loads(raw_response)

        category = parsed.get("category", "")
        confidence = parsed.get("confidence", "Low")

        logger.info(f"[LLM-RESULT]   '{name}' → '{category}' ({confidence})")

        if category != "Private Clinic":
            return None

        clinic["confidence_score"] = confidence
        clinic["doctor_name"] = extract_doctor_name(name)
        return clinic

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for '{name}': {e}")
        return None
    except Exception as e:
        logger.error(f"LLM classification failed for '{name}': {e}")
        return None