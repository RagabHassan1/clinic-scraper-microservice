import csv
import os
import logging
import threading
from typing import List, Dict

logger = logging.getLogger(__name__)

# Thread lock for safe concurrent writes
_lock = threading.Lock()


class CSVStorage:

    def __init__(self, filepath: str = "data/clinics.csv"):

        self.filepath = filepath

        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Ensure file exists with header
        if not os.path.exists(filepath):

            with open(filepath, "w", newline="", encoding="utf-8") as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "clinic_name",
                        "doctor_name",
                        "phone_number",
                        "address",
                        "maps_link",
                        "confidence_score"
                    ]
                )

                writer.writeheader()

            logger.info(f"Created CSV file: {filepath}")


    # Save multiple clinics
    def save_clinics(self, clinics: List[Dict]):

        if not clinics:
            logger.warning("No clinics to save")
            return

        with _lock:

            existing = self._load_existing_keys()

            new_rows = []

            for clinic in clinics:

                key = self._make_key(clinic)

                if key in existing:
                    continue

                new_rows.append(clinic)
                existing.add(key)


            if not new_rows:
                logger.info("No new clinics to write")
                return


            with open(
                self.filepath,
                "a",
                newline="",
                encoding="utf-8"
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "clinic_name",
                        "doctor_name",
                        "phone_number",
                        "address",
                        "maps_link",
                        "confidence_score"
                    ]
                )

                writer.writerows(new_rows)


            logger.info(f"Saved {len(new_rows)} new clinics")


    # Load all clinics
    def load_all(self) -> List[Dict]:

        with open(
            self.filepath,
            "r",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            return list(reader)


    # Deduplication key
    def _make_key(self, clinic: Dict):

        return (
            clinic.get("clinic_name", "").lower().strip(),
            clinic.get("phone_number", "").strip()
        )


    # Load existing keys
    def _load_existing_keys(self):

        keys = set()

        if not os.path.exists(self.filepath):
            return keys

        with open(
            self.filepath,
            "r",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                keys.add(
                    (
                        row.get("clinic_name", "").lower().strip(),
                        row.get("phone_number", "").strip()
                    )
                )

        return keys