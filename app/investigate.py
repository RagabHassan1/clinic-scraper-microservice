"""
investigate.py — Post-run analysis tool for the Clinic Scraper.

Usage:
    python3 investigate.py                        # reads default data/clinics.csv
    python3 investigate.py --file data/clinics.csv
    python3 investigate.py --query "Dentist"      # filter by query keyword in name

Run this after each specialty test to get a full picture of:
  - How many results were saved
  - Confidence score distribution
  - Doctor name extraction rate
  - Suspicious classifications worth reviewing manually
  - Missing data (no doctor name, no maps link)
"""

import argparse
import csv
import os
from collections import Counter
from typing import List, Dict


# -------------------------------------------------
# Load CSV
# -------------------------------------------------
def load_csv(filepath: str) -> List[Dict]:
    if not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# -------------------------------------------------
# Suspicious name patterns
# These are names that MIGHT be misclassified.
# The investigator should review these manually.
# -------------------------------------------------
_SUSPICIOUS_PATTERNS = [
    "center", "centre", "centers", "مركز",
    "hospital", "مستشفى",
    "scan", "lab", "معمل",
    "complex", "group", "network", "chain",
]

def is_suspicious(clinic_name: str) -> bool:
    n = clinic_name.lower()
    return any(p in n for p in _SUSPICIOUS_PATTERNS)


# -------------------------------------------------
# Report
# -------------------------------------------------
def run_report(rows: List[Dict], query_filter: str = None):

    # Optional filter — only look at rows matching a keyword
    if query_filter:
        rows = [
            r for r in rows
            if query_filter.lower() in r.get("clinic_name", "").lower()
            or query_filter.lower() in r.get("address", "").lower()
        ]
        print(f"\n🔍 Filtered to rows containing: '{query_filter}' ({len(rows)} rows)\n")

    if not rows:
        print("No data to analyse.")
        return

    total = len(rows)

    # --- Confidence distribution ---
    confidence_counts = Counter(r.get("confidence_score", "Unknown") for r in rows)

    # --- Doctor name extraction ---
    has_doctor   = sum(1 for r in rows if r.get("doctor_name") and r["doctor_name"].strip())
    no_doctor    = total - has_doctor

    # --- Maps link ---
    has_link     = sum(1 for r in rows if r.get("maps_link") and r["maps_link"].strip())
    no_link      = total - has_link

    # --- Phone ---
    has_phone    = sum(1 for r in rows if r.get("phone_number") and r["phone_number"].strip())

    # --- Suspicious names ---
    suspicious   = [r for r in rows if is_suspicious(r.get("clinic_name", ""))]

    # --- No doctor name despite "Dr." in clinic name ---
    # These indicate a regex extraction miss
    missed_doctor = [
        r for r in rows
        if (
            ("dr." in r.get("clinic_name", "").lower() or
             "dr " in r.get("clinic_name", "").lower() or
             "دكتور" in r.get("clinic_name", "") or
             "د." in r.get("clinic_name", ""))
            and
            (not r.get("doctor_name") or not r["doctor_name"].strip())
        )
    ]

    # -------------------------------------------------
    # Print report
    # -------------------------------------------------
    divider = "─" * 60

    print(divider)
    print("  CLINIC SCRAPER — INVESTIGATION REPORT")
    print(divider)

    print(f"\n📊 OVERVIEW")
    print(f"   Total clinics saved    : {total}")
    print(f"   With valid phone       : {has_phone} / {total}")
    print(f"   With maps link         : {has_link} / {total}")

    print(f"\n🎯 CONFIDENCE SCORES")
    for level in ["High", "Medium", "Low", "Unknown"]:
        count = confidence_counts.get(level, 0)
        bar = "█" * count
        print(f"   {level:<8}: {count:>3}  {bar}")

    print(f"\n👨‍⚕️ DOCTOR NAME EXTRACTION")
    pct = (has_doctor / total * 100) if total > 0 else 0
    print(f"   Extracted successfully : {has_doctor} / {total} ({pct:.0f}%)")
    print(f"   Not found (None)       : {no_doctor}")

    if missed_doctor:
        print(f"\n   ⚠️  REGEX MISSES — name has Dr./دكتور but doctor_name is None:")
        for r in missed_doctor:
            print(f"      • {r['clinic_name']}")

    print(f"\n⚠️  SUSPICIOUS CLASSIFICATIONS ({len(suspicious)} found)")
    print(f"   These were classified as Private Clinic but contain")
    print(f"   keywords like 'center', 'scan', 'lab' — review manually:")
    if suspicious:
        for r in suspicious:
            print(f"   • {r['clinic_name']}")
            print(f"     confidence: {r.get('confidence_score')} | phone: {r.get('phone_number')}")
    else:
        print("   ✅ None found — no suspicious names in this dataset.")

    print(f"\n📋 FULL CLINIC LIST")
    print(f"   {'#':<4} {'Clinic Name':<50} {'Doctor Name':<20} {'Conf'}")
    print(f"   {'─'*4} {'─'*50} {'─'*20} {'─'*4}")
    for i, r in enumerate(rows, 1):
        name     = r.get("clinic_name", "")[:48]
        doctor   = (r.get("doctor_name") or "—")[:18]
        conf     = r.get("confidence_score", "?")
        flag     = " ⚠️" if is_suspicious(r.get("clinic_name", "")) else ""
        print(f"   {i:<4} {name:<50} {doctor:<20} {conf}{flag}")

    print(f"\n{divider}")
    print(f"  💡 TIP: Run with --debug flag to see which layer handled")
    print(f"     each clinic (RULE-ACCEPT / RULE-EXCLUDE / LLM) and")
    print(f"     raw LLM responses during the next scrape.")
    print(divider)


# -------------------------------------------------
# Entry Point
# -------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Investigate saved clinic data after a scraper run."
    )
    parser.add_argument(
        "--file",
        type=str,
        default="data/clinics.csv",
        help="Path to the CSV file to analyse. Default: data/clinics.csv"
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Optional keyword to filter rows (e.g. 'October' or 'Maadi')"
    )
    args = parser.parse_args()

    rows = load_csv(args.file)
    if rows:
        run_report(rows, query_filter=args.query)


if __name__ == "__main__":
    main()
