import os
import json
import logging
from typing import Dict, Optional

from dotenv import load_dotenv
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import AsyncGroq

from app.normalizer import extract_doctor_name


# -------------------------------------------------
# Logging
# -------------------------------------------------
logger = logging.getLogger(__name__)

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in environment variables.")

client = AsyncGroq(api_key=GROQ_API_KEY)


# =================================================
# LAYER 1: RULE-BASED PRE-FILTER
# =================================================
#
# Design principle: rules only handle cases we are 100% certain about.
# When in doubt → LLM decides.
#
# Three zones:
#   RULE-EXCLUDE  → definitely NOT a private clinic → discard, no LLM call
#   RULE-ACCEPT   → definitely IS a private clinic  → accept, no LLM call
#   LLM           → anything uncertain              → classify via Groq
#
# NARROWED SCOPE (vs previous version):
#   We removed _CLINIC_KEYWORDS from rule-accept.
#   Previously: "Hayat clinic", "IVORY DENTAL CLINICS", "Prime Clinics",
#   "MedTown Clinics" were all auto-accepted via the "clinic" keyword.
#   Problem: some of these are multi-branch chains or wellness centres
#   that should arguably be Medical Centers — the LLM handles these better.
#
#   New rule-accept condition: ONLY names with an explicit doctor title
#   (Dr. / دكتور / دكتورة / دكتوره / الدكتور / د. / د/) AND no canceller.
#   The clinic keyword alone is no longer sufficient — needs the doctor title.
#
# ADDITIONS (vs previous version):
#   _EXCLUDE_KEYWORDS: added مصحة (sanatorium), company, مجمع (complex)
#   _DOCTOR_TITLES:    added دكتورة, دكتوره, الدكتور, الدكتورة, الدكتوره, د/
#   _TITLE_CANCELLERS: no changes needed (center/مركز already present)

_EXCLUDE_KEYWORDS = [
    # Hospitals
    "hospital", "مستشفى",
    # Sanatoria / psychiatric facilities (مصحة = sanatorium — NOT a clinic)
    "مصحة",
    # Pharmacies
    "pharmacy", "صيدلية",
    # Labs
    "lab", "laboratory", "معمل", "مختبر",
    # Imaging / diagnostic
    "scan", "x-ray", "xray", "imaging", "radiology",
    # Clearly corporate entities
    "medical center", "مركز طبي", "مركز صحي",
    # Companies — not patient-facing clinics
    "company", "شركة",
    # Large complexes
    "مجمع",
]

# ALL known doctor title forms in Egyptian naming conventions.
# Includes feminine variants (دكتورة/دكتوره), definite article forms
# (الدكتور/الدكتورة), and abbreviated forms (د./د/).
# These are the ONLY basis for rule-accept — clinic keywords alone
# are not sufficient (they go to LLM instead).
_DOCTOR_TITLES = [
    # English
    "dr.", "dr ",
    # Arabic masculine
    "دكتور ",
    # Arabic feminine (FIX — were completely missing before)
    "دكتورة", "دكتوره",
    # Arabic with definite article (FIX — were completely missing before)
    "الدكتور", "الدكتورة", "الدكتوره",
    # Arabic abbreviated (FIX — د/ was missing)
    "د.", "د/",
]

# If any of these appear in a name, cancel the rule-accept
# even if a doctor title is present.
# e.g. "Dr. X Medical Center" → has title but also has "center" → LLM decides
_TITLE_CANCELLERS = [
    "center", "centre", "centers",
    "مركز",
    "hospital", "مستشفى",
    "scan", "lab", "معمل",
    "complex", "مجمع",
]


def _name_lower(name: str) -> str:
    return name.lower()


def is_obviously_not_clinic(name: str) -> bool:
    """
    Returns True if the name contains a strong non-clinic signal.
    Discarded immediately without calling the LLM.

    Catches: hospitals, pharmacies, labs, sanatoriums (مصحة),
    imaging centers, corporate medical centers, companies.
    """
    n = _name_lower(name)
    return any(keyword in n for keyword in _EXCLUDE_KEYWORDS)


def is_obviously_a_clinic(name: str) -> bool:
    """
    Returns True ONLY if the name has an explicit doctor title
    AND no cancelling keyword.

    NARROWED vs previous version:
        Old: (doctor title OR clinic keyword) AND no canceller
        New: (doctor title) AND no canceller

    Why narrowed?
        "Hayat clinic", "IVORY DENTAL CLINICS", "Prime Clinics",
        "MedTown Clinics" all had the "clinic" keyword and were
        auto-accepted — but some of these are multi-branch chains
        that the LLM would correctly classify differently.
        Removing the clinic-keyword trigger sends them to LLM,
        which is the right call for ambiguous branding names.

    What still gets rule-accepted (high confidence):
        "Dr. Ahmed Samy Dental Clinic"   → has Dr.
        "دكتورة شيماء الشبراوي"          → has دكتورة
        "عيادة الدكتورة سهام أبو حامد"   → has الدكتورة
        "دكتور عاطف خياط"               → has دكتور
        "د. أسامة عامر"                  → has د.
        "Dr. Sarah Nazmy - Psychiatrist" → has Dr.

    What now goes to LLM (was previously rule-accepted):
        "Hayat clinic"           → no doctor title → LLM
        "IVORY DENTAL CLINICS"   → no doctor title → LLM
        "Prime Clinics"          → no doctor title → LLM
        "Dental House"           → no doctor title → LLM
        "Sallèna Wellness Clinic"→ no doctor title → LLM
    """
    n = _name_lower(name)

    has_canceller = any(c in n for c in _TITLE_CANCELLERS)
    if has_canceller:
        return False

    return any(t in n for t in _DOCTOR_TITLES)


# =================================================
# LAYER 2: LLM CLASSIFICATION
# =================================================

SYSTEM_PROMPT = """You are a medical business classifier for Egypt.
Respond with a single valid JSON object only — no explanation, no markdown, no arrays."""

# IMPORTANT CHANGE: removed `address` from the prompt.
#
# Previously we sent the trimmed address to help with ambiguous cases.
# Problem found during testing: the LLM was classifying based on the
# ADDRESS field, not the business name.
#
# Example failure:
#   Name: "Genesis Dental Crew"
#   Address: "Cairo medical center, floor 2, clinic no.50"
#   → LLM returned Medical Center because it saw "center" in the ADDRESS.
#   This is wrong — a dentist renting space in a medical center building
#   is still a private clinic.
#
# Example failure:
#   Name: "Professor Ahmed El Minawi MD FACGE Gynecologist"
#   Address: "Minawi Medical Center 8 Lowaa Ahmed Ahmdy..."
#   → LLM returned Medical Center because of the ADDRESS.
#   This is clearly a named private specialist.
#
# Fix: classify on NAME ONLY. The name is what we're classifying.
# The address is irrelevant to the category of the business.
USER_PROMPT_TEMPLATE = """Classify this Egyptian medical business name into ONE category.

Categories: Private Clinic, Hospital, Medical Center, Lab, Pharmacy

Rules:
- "center/مركز" in name → Medical Center (even if Dr. is present)
- "مصحة" in name → Hospital
- Named after a person + no center/hospital keyword → Private Clinic
- Ambiguous brand/wellness/counseling names → Private Clinic
- If still unsure → Private Clinic

Return ONLY: {{"category": "...", "confidence": "High|Medium|Low", "reason": "..."}}

Name: {clinic_name}"""


async def _call_llm(clinic_name: str) -> str:
    """
    Calls Groq LLM with NAME ONLY — address removed to prevent
    the LLM from classifying based on the building rather than
    the business itself.
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(clinic_name=clinic_name)

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
                    {"role": "user",   "content": user_prompt}
                ],
                temperature=0,
                response_format={"type": "json_object"}
            )

    return response.choices[0].message.content


# =================================================
# MAIN CLASSIFICATION FUNCTION
# =================================================

async def classify_clinic(clinic: Dict) -> Optional[Dict]:
    """
    Three-layer pipeline:

    Layer 1a — RULE-EXCLUDE: strong non-clinic signals → discard instantly
    Layer 1b — RULE-ACCEPT:  explicit doctor title + no canceller → accept instantly
    Layer 2  — LLM:          everything else → Groq classification

    Returns clinic dict (with doctor_name + confidence_score) or None.
    """
    name    = clinic.get("clinic_name", "")
    address = clinic.get("address", "")

    # --------------------------------------------------
    # Layer 1a: Obviously NOT a clinic
    # --------------------------------------------------
    if is_obviously_not_clinic(name):
        logger.info(f"[RULE-EXCLUDE] {name}")
        return None

    # --------------------------------------------------
    # Layer 1b: Obviously IS a clinic (doctor title present)
    # --------------------------------------------------
    if is_obviously_a_clinic(name):
        logger.info(f"[RULE-ACCEPT]  {name}")
        clinic["confidence_score"] = "High"
        clinic["doctor_name"]      = extract_doctor_name(name)
        return clinic

    # --------------------------------------------------
    # Layer 2: LLM — name-only classification
    # --------------------------------------------------
    logger.info(f"[LLM]          {name}")

    try:
        raw_response = await _call_llm(clinic_name=name)
        logger.debug(f"[LLM-RAW]      '{name}':\n{raw_response}")

        if not raw_response:
            logger.warning(f"Empty LLM response for: {name}")
            return None

        parsed     = json.loads(raw_response)
        category   = parsed.get("category", "")
        confidence = parsed.get("confidence", "Low")

        logger.info(f"[LLM-RESULT]   '{name}' → '{category}' ({confidence})")

        if category != "Private Clinic":
            return None

        clinic["confidence_score"] = confidence
        clinic["doctor_name"]      = extract_doctor_name(name)
        return clinic

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for '{name}': {e}")
        return None
    except Exception as e:
        logger.error(f"LLM classification failed for '{name}': {e}")
        return None