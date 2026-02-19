import os
import logging
from typing import List, Dict

from dotenv import load_dotenv
from serpapi import GoogleSearch
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.normalizer import normalize_phone

# basicConfig is intentionally not called here — logging is configured once in main.py.
logger = logging.getLogger(__name__)

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
if not SERPAPI_KEY:
    raise ValueError("SERPAPI_KEY not found in environment variables.")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def _call_serpapi(query: str) -> dict:
    logger.info(f"Calling SerpApi for query: {query}")
    params = {
        "engine": "google_maps",
        "q": f"{query}, Egypt",
        "api_key": SERPAPI_KEY,
        "gl": "eg",
        "hl": "en"
    }
    return GoogleSearch(params).get_dict()


def search_clinics(query: str) -> List[Dict]:
    """
    Search Google Maps for clinics matching the query and return structured results.
    Clinics without a recognized Egyptian phone number are dropped here —
    a phone number is the minimum we need for the output to be useful.
    """
    try:
        results = _call_serpapi(query)
    except Exception as e:
        logger.error(f"Failed to fetch results from SerpApi: {e}")
        return []

    local_results = results.get("local_results", [])

    if not local_results:
        logger.warning("No results found.")
        return []

    clinics = []

    for place in local_results:
        raw_phone = place.get("phone")
        normalized_phone = normalize_phone(raw_phone)

        if not normalized_phone:
            logger.debug(
                f"DROPPED (no valid phone): '{place.get('title')}' "
                f"| raw phone: '{raw_phone}'"
            )
            continue

        # SerpApi returns a place_id rather than a direct Maps URL.
        # We construct the standard URL from it so the output links are clickable.
        place_id = place.get("place_id")
        maps_link = (
            f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            if place_id else None
        )

        clinics.append({
            "clinic_name": place.get("title"),
            "phone_number": normalized_phone,
            "address": place.get("address"),
            "maps_link": maps_link
        })

    logger.info(f"Fetched {len(clinics)} results.")
    return clinics