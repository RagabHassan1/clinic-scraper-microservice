import argparse
import logging
import time


from app.scraper import search_clinics
from app.classifier import classify_clinic

from app.storage import CSVStorage

# -------------------------------------------------
# Logging Setup
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Clinic Scraper Microservice - SerpApi Version"
    )

    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help='Search query (e.g., "Dentist in Zayed")'
    )

    args = parser.parse_args()

    logger.info(f"Starting search for: {args.query}")

    raw_clinics = search_clinics(args.query)

    filtered_clinics = []

    for clinic in raw_clinics:
        classified = classify_clinic(clinic)
        if classified:
            filtered_clinics.append(classified)
        time.sleep(1)

    logger.info(f"Final private clinics: {len(filtered_clinics)}")
    print(filtered_clinics[:3])


    storage = CSVStorage()

    storage.save_clinics(filtered_clinics)




if __name__ == "__main__":
    main()
