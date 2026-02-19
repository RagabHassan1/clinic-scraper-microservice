import os
import re
import json
import logging
from typing import Dict, Optional

from dotenv import load_dotenv
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import AsyncGroq

from app.normalizer import extract_doctor_name

logger = logging.getLogger(__name__)

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in environment variables.")

client = AsyncGroq(api_key=GROQ_API_KEY)


# --- Rule-based filters ---
#
# The classifier works in three layers. Rules handle the easy cases first,
# so the LLM only sees genuinely ambiguous names. This keeps API calls low
# and classification fast also reduce the amount of tokens since i do not call the LLM for obvious cases. 
#
#   Layer 1a — hard excludes: hospitals, pharmacies, labs, imaging centers, etc.
#   Layer 1b — hard accepts:  names with an explicit doctor title and no red flags
#   Layer 2  — LLM:           everything that doesn't fit neatly into either bucket

_EXCLUDE_KEYWORDS = [
    "hospital", "مستشفى",
    "مصحة",
    "pharmacy", "صيدلية",
    "laboratory", "معمل", "مختبر",
    "x-ray", "xray", "imaging", "radiology",
    "medical center", "مركز طبي", "مركز صحي",
    "company", "شركة",
    "مجمع",
]

# "lab" and "scan" are too short for plain substring matching — "Labib" and
# "DentaScan" are both false positives. This regex matches them only when
# they're not immediately followed by another letter.
_EXCLUDE_WHOLE_WORDS = re.compile(r'(lab|scan)(?![a-z])', re.IGNORECASE)

# Rule-accept is intentionally strict: a doctor title must be present.
# Names like "Hayat Clinic" or "Prime Clinics" are real edge cases —
# some are private practices, others are chains — so the LLM handles them.
_DOCTOR_TITLES = [
    "dr.", "dr ",
    "دكتور ", "دكتورة", "دكتوره",
    "الدكتور", "الدكتورة", "الدكتوره",
    "د.", "د/",
]

# Even with a doctor title, these words suggest it's not a simple private clinic.
# "Dr. Yehia Al Taher Center" is a good example — has a title, but "center" wins.
_TITLE_CANCELLERS = [
    "center", "centre", "centers",
    "مركز",
    "hospital", "مستشفى",
    "معمل",
    "complex", "مجمع",
]

_CANCELLER_WHOLE_WORDS = re.compile(r'(lab|scan)(?![a-z])', re.IGNORECASE)


def _name_lower(name: str) -> str:
    return name.lower()


def is_obviously_not_clinic(name: str) -> bool:
    n = _name_lower(name)
    return (
        any(kw in n for kw in _EXCLUDE_KEYWORDS)
        or bool(_EXCLUDE_WHOLE_WORDS.search(n))
    )


def is_obviously_a_clinic(name: str) -> bool:
    n = _name_lower(name)
    has_canceller = (
        any(c in n for c in _TITLE_CANCELLERS)
        or bool(_CANCELLER_WHOLE_WORDS.search(n))
    )
    if has_canceller:
        return False
    return any(t in n for t in _DOCTOR_TITLES)


# --- LLM classification ---

SYSTEM_PROMPT = """You are a medical business classifier for Egypt.
Respond with a single valid JSON object only — no explanation, no markdown, no arrays."""

# We classify on name only. Passing the address caused the LLM to pick up on
# things like "Cairo Medical Center, floor 2" and misclassify the tenant clinic
# inside as a Medical Center. The business name is what matters here.
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


async def classify_clinic(clinic: Dict) -> Optional[Dict]:
    """
    Runs a clinic dict through the three-layer classification pipeline.
    Returns the enriched dict (with doctor_name and confidence_score) if
    it's a private clinic, or None if it should be discarded.
    """
    name = clinic.get("clinic_name", "")

    if is_obviously_not_clinic(name):
        logger.info(f"[RULE-EXCLUDE] {name}")
        return None

    if is_obviously_a_clinic(name):
        logger.info(f"[RULE-ACCEPT]  {name}")
        clinic["confidence_score"] = "High"
        clinic["doctor_name"]      = extract_doctor_name(name)
        return clinic

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