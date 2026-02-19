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

# AsyncGroq is the async version of the Groq client.
# It lets us `await` each API call, so while one call is waiting
# for a network response, other calls can run concurrently.
client = AsyncGroq(api_key=GROQ_API_KEY)


# -------------------------------------------------
# Keyword Pre-Filter (Rule-Based)
# -------------------------------------------------
# These keywords immediately disqualify a name without calling the LLM.
# This saves API calls for obvious non-clinics.
EXCLUDED_KEYWORDS = [
    "hospital", "mustashfa", "مستشفى",
    "pharmacy", "صيدلية",
    "lab", "معمل",
    "laboratory"
]


def is_obviously_not_clinic(name: str) -> bool:
    name_lower = name.lower()
    return any(keyword in name_lower for keyword in EXCLUDED_KEYWORDS)


# -------------------------------------------------
# System + User Prompt (Improved)
# -------------------------------------------------
# The system message tells the model its ONLY job is to return JSON.
SYSTEM_PROMPT = """You are a medical business classifier for Egypt.
You always respond with a single valid JSON object and nothing else — no explanation, no markdown, no arrays.
If you are uncertain, pick the single most likely category."""

# The user prompt uses few-shot examples so the model knows exactly
# what the correct format and decision logic looks like.
# f-string placeholders {clinic_name} and {address} are filled in at runtime.
USER_PROMPT_TEMPLATE = """Classify the following Egyptian medical business into exactly ONE category.

Categories:
- Private Clinic  (solo doctor office or small group practice — عيادة)
- Hospital        (large inpatient facility — مستشفى)
- Medical Center  (multi-specialty corporate center — مركز طبي)
- Lab             (diagnostic laboratory — معمل)
- Pharmacy        (drug dispensary — صيدلية)

Rules:
- If the name contains "Dr." or "دكتور" and no hospital/center indicator → Private Clinic
- If ambiguous between Private Clinic and Medical Center → choose Private Clinic
- Always pick exactly ONE category. Never return an array.
- For doctor_name: extract ONLY the person's name (e.g. "Ahmed Samy"). If no doctor name is present, use null.
- Return ONLY this JSON structure, nothing else:

{{"category": "...", "confidence": "High|Medium|Low", "reason": "...", "doctor_name": "..." or null}}

Examples:

Name: Dr. Ahmed Samy Dental Clinic
Address: 12 Tahrir St, Cairo
→ {{"category": "Private Clinic", "confidence": "High", "reason": "Name contains Dr. and clinic keyword, solo dental practice.", "doctor_name": "Ahmed Samy"}}

Name: Al Salam Hospital
Address: Ring Road, Giza
→ {{"category": "Hospital", "confidence": "High", "reason": "Name explicitly contains Hospital keyword.", "doctor_name": null}}

Name: Ultra Dental Care And Esthetics
Address: 7 Al Mehwar St, Giza
→ {{"category": "Private Clinic", "confidence": "High", "reason": "Specialized dental care, residential area typical for private clinics.", "doctor_name": null}}

Now classify:
Name: {clinic_name}
Address: {address}"""


# -------------------------------------------------
# Async LLM Call with Retry
# -------------------------------------------------
async def _call_llm(clinic_name: str, address: str) -> str:
    """
    Calls the Groq LLM asynchronously with retry logic.

    `async def` means this function can be paused with `await`.
    While it's waiting for Groq's network response, Python will
    run other classify_clinic() calls concurrently.

    Retries up to 3 times on any exception, with exponential backoff
    (waits 1s, then 2s, then 4s between retries).
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        clinic_name=clinic_name,
        address=address
    )

    # AsyncRetrying is tenacity's async-compatible retry context manager.
    # We use it instead of the @retry decorator because async functions
    # need special handling — the regular @retry decorator doesn't work
    # correctly with `async def`.
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True
    ):
        with attempt:
            # `await` here means: "send this request, then pause and
            # let other coroutines run while we wait for the response"
            response = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0
            )

    return response.choices[0].message.content


# -------------------------------------------------
# Main Classification Function (Async)
# -------------------------------------------------
async def classify_clinic(clinic: Dict) -> Optional[Dict]:
    """
    Classifies a single clinic dict asynchronously.

    `async def` allows this to be run in parallel with other
    classify_clinic() calls via asyncio.gather() in main.py.

    Returns:
        The clinic dict (with confidence_score added) if Private Clinic.
        None if not a Private Clinic or if classification failed.
    """

    name = clinic.get("clinic_name", "")
    address = clinic.get("address", "")

    # Step 1: Rule-based exclusion — no LLM call needed
    if is_obviously_not_clinic(name):
        logger.info(f"Rule-based exclusion: {name}")
        return None

    # Step 2: Call LLM (async — will run concurrently with other calls)
    try:
        # `await` pauses THIS function until _call_llm finishes,
        # but lets OTHER classify_clinic() calls run in the meantime
        raw_response = await _call_llm(clinic_name=name, address=address)

        logger.debug(f"RAW LLM RESPONSE for '{name}':\n{raw_response}")

        if not raw_response:
            logger.warning(f"Empty LLM response for: {name}")
            return None

        # Step 3: Parse JSON — extract from first { to last }
        cleaned = raw_response.strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1:
            logger.warning(f"No JSON object found in LLM response for: {name}")
            return None

        json_str = cleaned[start:end + 1]
        parsed = json.loads(json_str)

        category = parsed.get("category", "")
        confidence = parsed.get("confidence", "Low")
        # LLM-extracted doctor name (may be None if not found)
        llm_doctor_name = parsed.get("doctor_name")

        logger.info(f"Classified '{name}' as '{category}' ({confidence})")

        # Step 4: Keep only Private Clinics
        if category != "Private Clinic":
            return None

        # Step 5: Resolve doctor_name
        # Strategy: trust the LLM first. If it returned null or an empty
        # string, fall back to our regex extractor as a safety net.
        # This two-layer approach means we rarely miss a doctor name.
        if llm_doctor_name:
            doctor_name = llm_doctor_name.strip()
        else:
            # Regex fallback — extract from clinic name directly
            doctor_name = extract_doctor_name(name)
            if doctor_name:
                logger.debug(f"Regex fallback found doctor name: '{doctor_name}'")

        clinic["confidence_score"] = confidence
        clinic["doctor_name"] = doctor_name  # will be None if neither found
        return clinic

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for '{name}': {e}")
        return None
    except Exception as e:
        logger.error(f"LLM classification failed for '{name}': {e}")
        return None