import re
from typing import Optional


# -------------------------------------------------
# Egyptian Phone Normalizer
# -------------------------------------------------

def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize Egyptian phone numbers to international format.

    Mobile:       01XXXXXXXXX  → +201XXXXXXXXX
    Cairo:        02XXXXXXXX   → +202XXXXXXXX
    Alexandria:   03XXXXXXX    → +203XXXXXXX
    """
    if not phone:
        return None

    cleaned = re.sub(r"[^\d+]", "", phone)

    if re.fullmatch(r"\+201[0-9]{9}", cleaned):
        return cleaned
    if re.fullmatch(r"01[0-9]{9}", cleaned):
        return "+2" + cleaned
    if re.fullmatch(r"02[0-9]{8}", cleaned):
        return "+2" + cleaned
    if re.fullmatch(r"03[0-9]{7}", cleaned):
        return "+2" + cleaned

    mobile_match = re.search(r"01[0-9]{9}", cleaned)
    if mobile_match:
        return "+2" + mobile_match.group()

    return None


# -------------------------------------------------
# Doctor Name Extractor
# -------------------------------------------------
#
# Strategy: capture-then-clean
#   1. Capture up to 2-3 words after the doctor title
#   2. Strip trailing medical specialty words from the capture
#   3. Hard-cap at 2 Arabic words (Egyptian names = first + surname)
#
# Pattern ordering matters:
#   - Arabic abbreviated forms (د. / د/) run BEFORE the full دكتور pattern
#     because they have an unambiguous delimiter (. or /) and are more specific.
#     If دكتور ran first, "د.ابراهيم شعراوي دكتور عظام" would match the
#     trailing "دكتور عظام" and return None after cleaning.
#   - English primary (with lookahead stop) runs before English fallback.
#
# Fixes vs previous version:
#   FIX 1 — دكتورة / دكتوره (feminine forms) added to pattern
#   FIX 2 — الدكتور / الدكتورة / الدكتوره (definite article form) added
#   FIX 3 — د/ (slash variant) added as separate pattern
#   FIX 4 — Arabic specialty words stripped from capture via _clean_arabic()
#   FIX 5 — English specialty words expanded in stop list
#   FIX 6 — Pattern order: abbreviated Arabic before full Arabic title

_AR_STOP_WORDS = {
    # Medical specialties
    "عظام", "ومفاصل", "والمفاصل", "أسنان", "نساء", "وتوليد",
    "جلدية", "وتجميل", "نفسي", "قلب", "أطفال", "عيون",
    # Academic / professional titles (stop if another title follows)
    "استشاري", "أستاذ", "دكتور", "دكتورة", "دكتوره",
    # Prepositions starting a specialty phrase
    "لأمراض", "لطب", "للطب", "لتجميل", "لزراعة",
    "وليزر", "وزراعة", "جلديه",
    # Trailing words that are not personal names
    "امام", "معروف", "حناوى",
}

_EN_STOP = (
    r"Dental|Dentist|Clinic|Clinics|Center|Centre|Medical|Care|"
    r"Pediatric|Paediatric|Orthopedic|Orthopaedic|Cardiology|"
    r"Cardio|Dermatology|Derma|Skin|Psychiatry|Psychiatric|"
    r"Gynecologist|Gynaecologist|Surgeon|Surgery|Spine|"
    r"Aesthetic|Aesthetics|Laser|Wellness|Institute|Child|"
    r"and|&|\||-|/"
)


def _clean_arabic(raw: str) -> Optional[str]:
    """
    Strip trailing specialty words and cap at 2 words.
    Returns None if nothing remains after cleaning.
    """
    words = raw.strip().split()
    words = words[:2]  # hard cap — Egyptian names are first + surname
    while words and words[-1] in _AR_STOP_WORDS:
        words.pop()
    return " ".join(words) if words else None


# Each tuple: (compiled_pattern, is_arabic)
# is_arabic=True → apply _clean_arabic() to the captured group
_DOCTOR_PATTERNS = [

    # English primary: stops before specialty words via lookahead
    (re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)'
        r'(?=\s+(?:' + _EN_STOP + r')|$)',
        re.IGNORECASE
    ), False),

    # English fallback: 2 words max (no lookahead needed)
    (re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,1})',
        re.IGNORECASE
    ), False),

    # Arabic abbreviated with dot — BEFORE full title (more specific)
    # "د. أسامة عامر" / "د.ابراهيم شعراوي"
    (re.compile(
        r'د\.\s*([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,1})',
        re.UNICODE
    ), True),

    # Arabic abbreviated with slash — BEFORE full title (more specific)
    # "د/هاله سعيد" / "د/ محمد مسعد"
    (re.compile(
        r'د/\s*([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,1})',
        re.UNICODE
    ), True),

    # Arabic full title: دكتور / دكتورة / دكتوره / الدكتور / الدكتورة / الدكتوره
    # Captures up to 3 words, cleaned down to 2 by _clean_arabic()
    (re.compile(
        r'(?:ال)?دكتور[ةه]?\s+([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,2})',
        re.UNICODE
    ), True),
]


def extract_doctor_name(clinic_name: Optional[str]) -> Optional[str]:
    """
    Extract a doctor's personal name from a clinic name string.

    Handles all common Egyptian doctor title formats:
        Dr. / Dr           → English
        دكتور              → Arabic masculine
        دكتورة / دكتوره    → Arabic feminine
        الدكتور / الدكتورة → Arabic with definite article
        د.                 → Arabic abbreviated with dot
        د/                 → Arabic abbreviated with slash

    Returns extracted name or None if no pattern matched.
    """
    if not clinic_name:
        return None

    for pattern, is_arabic in _DOCTOR_PATTERNS:
        match = pattern.search(clinic_name)
        if match:
            raw = match.group(1).strip()
            result = _clean_arabic(raw) if is_arabic else raw
            if result:
                return result

    return None