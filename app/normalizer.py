import re
from typing import Optional


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
