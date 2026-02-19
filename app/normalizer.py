import re
from typing import Optional


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    Normalize Egyptian phone numbers to international format (+2XXXXXXXXXX).
    Handles mobile lines (01x), Cairo landlines (02x), and Alexandria (03x).
    Returns None if the input doesn't match any recognized Egyptian format.
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

    # Some numbers come embedded in longer strings (e.g. "Tel: 01012345678 ext 3").
    # Try extracting a mobile number as a last resort.
    mobile_match = re.search(r"01[0-9]{9}", cleaned)
    if mobile_match:
        return "+2" + mobile_match.group()

    return None


# --- Doctor name extraction ---
#
# Strategy: match the doctor title, capture the words that follow,
# then strip any trailing specialty/role words to isolate the personal name.
#
# Pattern order matters for Arabic:
#   - Abbreviated forms (د. / د/) are matched first because they have an
#     unambiguous delimiter. If the full دكتور pattern ran first, a name like
#     "د.ابراهيم شعراوي دكتور عظام" could match the trailing "دكتور عظام"
#     and return nothing after cleaning.
#   - For English, the primary pattern uses a lookahead to stop before
#     specialty words. The fallback captures up to 2 words with no lookahead.

_AR_STOP_WORDS = {
    "عظام", "ومفاصل", "والمفاصل", "أسنان", "نساء", "وتوليد",
    "جلدية", "وتجميل", "نفسي", "قلب", "أطفال", "عيون",
    "استشاري", "أستاذ", "دكتور", "دكتورة", "دكتوره",
    "لأمراض", "لطب", "للطب", "لتجميل", "لزراعة",
    "وليزر", "وزراعة", "جلديه",
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
    Strip trailing specialty words from a captured Arabic name and cap at 3 words.
    Egyptian names typically follow: first + father's name + surname,
    so 3 words covers the full name without bleeding into specialty descriptions.
    """
    words = raw.strip().split()
    words = words[:3]
    while words and words[-1] in _AR_STOP_WORDS:
        words.pop()
    return " ".join(words) if words else None


_DOCTOR_PATTERNS = [
    # English — stop before specialty words
    (re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)'
        r'(?=\s+(?:' + _EN_STOP + r')|$)',
        re.IGNORECASE
    ), False),

    # English fallback — 2 words max
    (re.compile(
        r'\bDr\.?\s*([A-Za-z]+(?:\s+[A-Za-z]+){0,1})',
        re.IGNORECASE
    ), False),

    # Arabic abbreviated: د. or د/
    (re.compile(
        r'د\.\s*([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,1})',
        re.UNICODE
    ), True),

    (re.compile(
        r'د/\s*([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,1})',
        re.UNICODE
    ), True),

    # Arabic full title: دكتور / دكتورة / دكتوره / الدكتور / الدكتورة / الدكتوره
    (re.compile(
        r'(?:ال)?دكتور[ةه]?\s+([\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+){0,2})',
        re.UNICODE
    ), True),
]


def extract_doctor_name(clinic_name: Optional[str]) -> Optional[str]:
    """
    Extract a doctor's personal name from a clinic name string.
    Handles English (Dr./Dr), Arabic full titles (دكتور/دكتورة/الدكتور),
    and Arabic abbreviated forms (د./د/).
    Returns None if no doctor title is found.
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