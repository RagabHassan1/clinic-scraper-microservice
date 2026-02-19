import os
import logging
from typing import List, Dict

from app.normalizer import normalize_phone


from dotenv import load_dotenv
from serpapi import GoogleSearch
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


# -------------------------------------------------
# Logging Configuration
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# -------------------------------------------------
# Load Environment Variables
# -------------------------------------------------
load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")

if not SERPAPI_KEY:
    raise ValueError("SERPAPI_KEY not found in environment variables.")


# -------------------------------------------------
# Retry Decorator for API Calls
# -------------------------------------------------
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def _call_serpapi(query: str) -> dict:
    """
    Internal function that calls SerpApi.
    Wrapped with retry logic.
    """
    logger.info(f"Calling SerpApi for query: {query}")

    params = {
        "engine": "google_maps",
        "q": f"{query}, Egypt",
        "api_key": SERPAPI_KEY,
        "gl": "eg",
        "hl": "en"
    }


    search = GoogleSearch(params)
    results = search.get_dict()

    return results


# -------------------------------------------------
# Public Scraper Function
# -------------------------------------------------
def search_clinics(query: str) -> List[Dict]:
    """
    Searches Google Maps for clinics based on query.
    Returns a list of structured clinic dictionaries.
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

        # Drop clinics without valid phone — log at DEBUG so --debug flag exposes them
        if not normalized_phone:
            logger.debug(
                f"DROPPED (no valid phone): '{place.get('title')}' "
                f"| raw phone: '{raw_phone}'"
            )
            continue

        # SerpApi's google_maps engine does not return a direct "link" field.
        # However it always returns a "place_id" (e.g. "ChIJkXKOZZhZwokR...").
        # We construct the standard Google Maps URL from it.
        # This URL opens directly to the place's Maps page when clicked.
        place_id = place.get("place_id")
        if place_id:
            maps_link = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        else:
            maps_link = None

        clinic = {
            "clinic_name": place.get("title"),
            "phone_number": normalized_phone,
            "address": place.get("address"),
            "maps_link": maps_link
        }

        clinics.append(clinic)


    logger.info(f"Fetched {len(clinics)} results.")

    return clinics