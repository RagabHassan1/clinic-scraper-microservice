import os
import json
import logging
from typing import Dict, Optional

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import Groq


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

client = Groq(api_key=GROQ_API_KEY)


# -------------------------------------------------
# Keyword Pre-Filter (Rule-Based)
# -------------------------------------------------
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
# LLM Call with Retry
# -------------------------------------------------
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def _call_llm(prompt: str) -> str:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "You are a strict business classifier."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    return response.choices[0].message.content


# -------------------------------------------------
# Main Classification Function
# -------------------------------------------------
def classify_clinic(clinic: Dict) -> Optional[Dict]:
    """
    Returns:
        Updated clinic dict with confidence_score
        OR None if not a private clinic
    """

    name = clinic.get("clinic_name", "")

    # Step 1: Rule-based exclusion
    if is_obviously_not_clinic(name):
        return None

    # Step 2: LLM Classification Prompt
    prompt = f"""
Classify the following medical business in Egypt.

Name: {clinic.get("clinic_name")}
Address: {clinic.get("address")}

Categories:
- Private Clinic
- Hospital
- Medical Center
- Lab
- Pharmacy

Return ONLY valid JSON:
{{
  "category": "...",
  "confidence": "High|Medium|Low",
  "reason": "..."
}}


"""

    try:
        raw_response = _call_llm(prompt)

        logger.error(f"RAW LLM RESPONSE:\n{raw_response}")

        if not raw_response:
            return None

        cleaned = raw_response.strip()

        # Extract JSON part only
        start_obj = cleaned.find("{")
        end_obj = cleaned.rfind("}")

        start_arr = cleaned.find("[")
        end_arr = cleaned.rfind("]")

        json_str = None

        # Case 1: Object
        if start_obj != -1 and end_obj != -1:
            json_str = cleaned[start_obj:end_obj+1]

        # Case 2: Array
        elif start_arr != -1 and end_arr != -1:
            json_str = cleaned[start_arr:end_arr+1]

        else:
            logger.error("No JSON found")
            return None

        parsed = json.loads(json_str)

        # If list, take first item
        if isinstance(parsed, list):
            parsed = parsed[0]

        category = parsed.get("category")
        confidence = parsed.get("confidence")

        if category != "Private Clinic":
            return None

        clinic["confidence_score"] = confidence
        return clinic

    except Exception as e:
        logger.error(f"LLM classification failed: {e}")
        return None
