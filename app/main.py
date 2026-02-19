import argparse
import asyncio
import logging
from typing import List, Optional

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


# -------------------------------------------------
# Batched Parallel Classification
# -------------------------------------------------
async def classify_in_batches(
    clinics: List[dict],
    batch_size: int,
    delay: float
) -> List[Optional[dict]]:
    """
    Classifies clinics in parallel within fixed-size batches,
    with a pause between each batch to respect Groq's rate limit.

    Parameters:
        clinics:    Full list of clinic dicts from the scraper.
        batch_size: How many to classify in parallel at once.
                    Rule of thumb for Groq free tier:
                      ~20 results  → batch_size=5
                      ~40 results  → batch_size=5, delay=5.0
                      ~60+ results → batch_size=5, delay=8.0
                    Or pass via --batch-size CLI arg at runtime.
        delay:      Seconds to wait between batches.
                    Passed via --delay CLI arg at runtime.

    Returns:
        Flat list of results (clinic dict or None), in original order.
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

        # Run this batch in parallel
        tasks = [classify_clinic(clinic) for clinic in batch]
        batch_results = await asyncio.gather(*tasks)
        all_results.extend(batch_results)

        # Pause between batches — skip after the last one
        is_last_batch = (batch_num == total_batches)
        if not is_last_batch:
            logger.info(
                f"Batch {batch_num} done. "
                f"Waiting {delay}s before next batch..."
            )
            # Use asyncio.sleep — NOT time.sleep.
            # time.sleep() freezes the entire event loop.
            # asyncio.sleep() only pauses this coroutine.
            await asyncio.sleep(delay)

    return all_results


# -------------------------------------------------
# Async Main Pipeline
# -------------------------------------------------
async def run(query: str, batch_size: int, delay: float):
    """
    Full pipeline:
      1. Scrape Google Maps via SerpApi
      2. Classify in batches (parallel within each batch)
      3. Save results to CSV
    """

    logger.info(f"Starting search for: {query}")

    # Step 1: Scrape
    raw_clinics = search_clinics(query)

    if not raw_clinics:
        logger.warning("No clinics found. Exiting.")
        return

    logger.info(
        f"Fetched {len(raw_clinics)} raw results. "
        f"Classifying in batches of {batch_size} with {delay}s delay..."
    )

    # Step 2: Classify in batches
    results = await classify_in_batches(
        clinics=raw_clinics,
        batch_size=batch_size,
        delay=delay
    )

    # Step 3: Filter Nones
    filtered_clinics = [r for r in results if r is not None]

    logger.info(f"Final private clinics found: {len(filtered_clinics)}")

    if filtered_clinics:
        print("\nSample results (first 3):")
        for clinic in filtered_clinics[:3]:
            print(clinic)

    # Step 4: Save
    storage = CSVStorage()
    storage.save_clinics(filtered_clinics)


# -------------------------------------------------
# Entry Point
# -------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Clinic Scraper Microservice"
    )

    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help='Search query e.g. "Dentist in Zayed"'
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help=(
            "Number of clinics to classify in parallel per batch. "
            "Default: 5 (safe for Groq free tier). "
            "Increase if you have a paid plan."
        )
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help=(
            "Seconds to wait between batches. "
            "Default: 3.0. Increase if you scrape more pages."
        )
    )

    args = parser.parse_args()

    asyncio.run(run(
        query=args.query,
        batch_size=args.batch_size,
        delay=args.delay
    ))


if __name__ == "__main__":
    main()