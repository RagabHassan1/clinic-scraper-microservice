import argparse
import asyncio
import logging
from typing import List, Optional

from app.scraper import search_clinics
from app.classifier import classify_clinic
from app.storage import CSVStorage

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
)

logger = logging.getLogger(__name__)


async def classify_in_batches(
    clinics: List[dict],
    batch_size: int,
    delay: float
) -> List[Optional[dict]]:
    """
    Classify clinics in parallel within fixed-size batches, with a configurable
    delay between batches to stay within Groq's free-tier rate limits.
    """
    all_results = []
    total_batches = (len(clinics) + batch_size - 1) // batch_size

    for i, batch_start in enumerate(range(0, len(clinics), batch_size)):
        batch = clinics[batch_start : batch_start + batch_size]
        batch_num = i + 1

        logger.info(
            f"Classifying batch {batch_num}/{total_batches} "
            f"({len(batch)} clinics in parallel)..."
        )

        tasks = [classify_clinic(clinic) for clinic in batch]
        batch_results = await asyncio.gather(*tasks)
        all_results.extend(batch_results)

        is_last_batch = (batch_num == total_batches)
        if not is_last_batch:
            logger.info(f"Batch {batch_num} done. Waiting {delay}s before next batch...")
            await asyncio.sleep(delay)

    return all_results


async def run(query: str, batch_size: int, delay: float):
    logger.info(f"Starting search for: {query}")

    raw_clinics = search_clinics(query)

    if not raw_clinics:
        logger.warning("No clinics found. Exiting.")
        return

    logger.info(
        f"Fetched {len(raw_clinics)} raw results (already filtered: "
        f"clinics without a valid phone number were dropped by scraper). "
        f"Classifying in batches of {batch_size} with {delay}s delay..."
    )

    results = await classify_in_batches(
        clinics=raw_clinics,
        batch_size=batch_size,
        delay=delay
    )

    filtered_clinics = [r for r in results if r is not None]
    discarded = len(raw_clinics) - len(filtered_clinics)

    logger.info(
        f"Classification complete — "
        f"kept: {len(filtered_clinics)}, "
        f"discarded: {discarded}"
    )

    if filtered_clinics:
        print("\nSample results (first 3):")
        for clinic in filtered_clinics[:3]:
            print(clinic)

    storage = CSVStorage()
    storage.save_clinics(filtered_clinics)


def main():
    parser = argparse.ArgumentParser(description="Clinic Scraper Microservice")

    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help='Search query, e.g. "Dentist in Zayed"'
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of clinics to classify in parallel per batch. Default: 5."
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between batches. Increase if scraping more pages."
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable DEBUG logging. Shows raw LLM responses and which layer "
            "handled each clinic (RULE-ACCEPT / RULE-EXCLUDE / LLM)."
        )
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger().setLevel(log_level)

    # Groq's underlying HTTP client is noisy at DEBUG level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    asyncio.run(run(
        query=args.query,
        batch_size=args.batch_size,
        delay=args.delay
    ))


if __name__ == "__main__":
    main()