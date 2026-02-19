import re
from typing import Optional, List


# -------------------------------------------------
# Egyptian Phone Normalizer
# -------------------------------------------------

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize Egyptian phone numbers to international format.

    Mobile:
        01XXXXXXXXX  -> +201XXXXXXXXX

    Landline:
        02XXXXXXXX   -> +202XXXXXXXX
        03XXXXXXXX   -> +203XXXXXXXX

    Returns:
        Normalized phone number string or None if invalid.
    """

    if not phone:
        return None

    # Remove spaces, dashes, parentheses
    cleaned = re.sub(r"[^\d+]", "", phone)

    # -------------------------------------------------
    # Case 1: Already international mobile
    # Example: +201115111171
    # -------------------------------------------------
    if re.fullmatch(r"\+201[0-9]{9}", cleaned):
        return cleaned

    # -------------------------------------------------
    # Case 2: Local mobile
    # Example: 01115111171
    # -------------------------------------------------
    if re.fullmatch(r"01[0-9]{9}", cleaned):
        return "+2" + cleaned

    # -------------------------------------------------
    # Case 3: Landline Cairo (02XXXXXXXX)
    # -------------------------------------------------
    if re.fullmatch(r"02[0-9]{8}", cleaned):
        return "+2" + cleaned

    # -------------------------------------------------
    # Case 5: Multiple numbers in one string
    # Extract first valid mobile
    # -------------------------------------------------
    mobile_match = re.search(r"01[0-9]{9}", cleaned)
    if mobile_match:
        return "+2" + mobile_match.group()

    # -------------------------------------------------
    # No valid Egyptian number found
    # -------------------------------------------------
    return None


# -------------------------------------------------
# Doctor Name Extractor (Regex-Based)
# -------------------------------------------------
# These patterns cover the most common Egyptian clinic naming conventions.
# We try them in order — most specific first.

# Each tuple is (pattern, group_index_to_extract)
# re.IGNORECASE makes it match "Dr." "dr." "DR." etc.
# re.UNICODE makes Arabic characters work correctly.

_DOCTOR_PATTERNS: List[re.Pattern] = [

    # English primary: "Dr. Ahmed Samy" or "Dr.Reham" (with or without space after dot)
    # Stops capturing before non-name words like Dental, Clinic, Center, Care, &
    # The lookahead (?=...) checks what comes AFTER the name without consuming it.
    re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)'
        r'(?=\s+(?:Dental|Dentist|Clinic|Center|Medical|Care|&|and|\||-|/)|$)',
        re.IGNORECASE
    ),

    # English fallback: grab up to 3 words after Dr (catches edge cases the primary misses)
    re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2})',
        re.IGNORECASE
    ),

    # Arabic full word: "دكتور محمد علي" — limited to 2 words (first + last name)
    re.compile(
        r'دكتور\s+([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+)?)',
        re.UNICODE
    ),

    # Arabic abbreviated: "د. أسامة عامر" — limited to 2 words
    re.compile(
        r'د\.\s*([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+)?)',
        re.UNICODE
    ),
]


def extract_doctor_name(clinic_name: Optional[str]) -> Optional[str]:
    """
    Attempts to extract a doctor's name from a clinic name string
    using regex pattern matching.

    Examples:
        "Dr. Ahmed Samy Dental Clinic"  → "Ahmed Samy"
        "911 Dental clinic - Dr shereef Azab" → "shereef Azab"
        "دكتور محمد علي للأسنان"        → "محمد علي"
        "د. أسامة عامر لتجميل الأسنان" → "أسامة عامر"
        "Ultra Dental Care"             → None

    Returns:
        Extracted name string, or None if no pattern matched.
    """
    if not clinic_name:
        return None

    for pattern in _DOCTOR_PATTERNS:
        match = pattern.search(clinic_name)
        if match:
            # group(1) is the first capture group — the name part
            # .strip() removes any leading/trailing spaces
            return match.group(1).strip()

    return None