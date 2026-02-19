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
# Basic config — level will be overridden after args are parsed
# if --debug is passed. We set INFO as default.
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
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
        f"Fetched {len(raw_clinics)} raw results (already filtered: "
        f"clinics without a valid phone number were dropped by scraper). "
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

    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable DEBUG logging. Shows raw LLM responses, which layer "
            "handled each clinic (RULE-ACCEPT / RULE-EXCLUDE / LLM), "
            "and dropped clinics (no phone). Use this when investigating "
            "edge cases across different specialties."
        )
    )

    args = parser.parse_args()

    # Apply log level based on --debug flag
    # DEBUG → shows everything including raw LLM responses
    # INFO  → normal run, shows classification results only
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger().setLevel(log_level)

    # Also set the level on the httpx logger that Groq uses —
    # without this, DEBUG mode gets flooded with low-level HTTP logs
    # that are not useful for our investigation.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    asyncio.run(run(
        query=args.query,
        batch_size=args.batch_size,
        delay=args.delay
    ))


if __name__ == "__main__":
    main()