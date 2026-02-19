# Clinic Scraper Microservice

A containerized Python microservice built for **Sofindex** that scrapes Google Maps for medical clinics in Egypt, classifies them using a hybrid rule-based and LLM pipeline, extracts doctor names, normalizes Egyptian phone numbers, and outputs structured results to CSV.

**Author:** Ragab Hassan — ragabhassan716@gmail.com  
**Repository:** https://github.com/RagabHassan1/clinic-scraper-microservice/tree/main

---

## What it does

Given a search query like `"Dentist in October"` or `"Dermatologist in Maadi"`, the service:

1. Queries Google Maps via SerpApi and fetches up to 20 local results
2. Drops any result that does not have a valid Egyptian phone number
3. Classifies each result through a three-layer pipeline to determine if it is a private clinic
4. Extracts the doctor's name from the clinic name string using regex
5. Saves accepted clinics to a deduplicated CSV file

---

## Output schema

| Field | Description |
|---|---|
| `clinic_name` | Full business name as it appears on Google Maps |
| `doctor_name` | Extracted personal name of the doctor, or `None` |
| `phone_number` | Normalized to international format (`+20XXXXXXXXXX`) |
| `address` | Full address string from Google Maps |
| `maps_link` | Clickable Google Maps URL constructed from `place_id` |
| `confidence_score` | Classification confidence: `High`, `Medium`, or `Low` |

---

## Project structure

```
clinic-scraper-microservice/
├── app/
│   ├── __init__.py
│   ├── main.py          # Entry point, CLI, batch orchestration
│   ├── scraper.py       # SerpApi integration, phone filtering
│   ├── classifier.py    # Three-layer classification pipeline
│   ├── normalizer.py    # Phone normalization, doctor name extraction
│   ├── storage.py       # CSV write with deduplication
│   └── investigate.py   # Post-run analysis tool (local dev only)
├── data/
│   └── clinics.csv      # Output file (persisted via Docker volume)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env                 # Not committed — see .env.example
```

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/RagabHassan1/clinic-scraper-microservice.git
cd clinic-scraper-microservice
```

Create your `.env` file:

```bash
cp .env.example .env
# Then edit .env and add your actual API keys
```

### 2. Build the Docker image

```bash
docker compose build
```

### 3. Run

```bash
# Default query defined in docker-compose.yml
docker compose run --rm clinic-scraper

# Custom query
docker compose run --rm clinic-scraper --query "Cardiologist in Heliopolis"

# With debug logging (shows which layer handled each clinic and raw LLM responses)
docker compose run --rm clinic-scraper --query "Dentist in Maadi" --debug
```

Results are written to `./data/clinics.csv` on your host machine via the Docker volume mount.

---

## Environment variables

| Variable | Description |
|---|---|
| `SERPAPI_KEY` | SerpApi API key for Google Maps search |
| `GROQ_API_KEY` | Groq API key for LLM classification |

---

## CLI options

| Option | Default | Description |
|---|---|---|
| `--query` | required | Search query, e.g. `"Dentist in October"` |
| `--batch-size` | `5` | Clinics classified in parallel per batch |
| `--delay` | `3.0` | Seconds between batches |
| `--debug` | off | Enable DEBUG logging |

---

## Architecture

### Overview

The pipeline has four sequential stages:

```
SerpApi → Phone Filter → Three-Layer Classifier → CSV Storage
```

Each stage is intentionally narrow in responsibility. The classifier is the most complex component and is described in detail below.

---

### Stage 1 — Scraping (`scraper.py`)

The scraper calls SerpApi's Google Maps engine with the query appended by `, Egypt` to ensure geographic relevance. It uses `tenacity` to retry up to 3 times with exponential backoff on any failure.

Every result is passed through the phone normalizer immediately. Results without a valid Egyptian phone number are dropped at this stage. A phone number is the minimum viable data point for the output to be useful — a clinic record with no contact information has no value.

---

### Stage 2 — Phone normalization (`normalizer.py`)

Egyptian phone numbers appear in many formats across Google Maps listings. The normalizer handles:

- Mobile numbers: `01XXXXXXXXX` → `+201XXXXXXXXX`
- Cairo landlines: `02XXXXXXXX` → `+202XXXXXXXX`
- Already-normalized: `+201XXXXXXXXX` → returned as-is
- Embedded numbers (e.g. `"Tel: 01012345678 ext 3"`) → extracts the first valid mobile

Numbers that do not match any recognized Egyptian format return `None` and the clinic is dropped.

---

### Stage 3 — Three-layer classification (`classifier.py`)

This is the core of the system. The key design goal was to minimize LLM API calls while maintaining high classification accuracy. Every LLM call costs tokens and time — the rule-based layers exist specifically to avoid spending them on obvious cases.

#### Layer 1a — Rule-based exclusion

Before anything else, the name is checked against a keyword list of strong non-clinic signals. If any match, the result is discarded immediately — no LLM call, no further processing.

**Substring keywords (plain match):**
`hospital`, `مستشفى`, `مصحة`, `pharmacy`, `صيدلية`, `laboratory`, `معمل`, `مختبر`, `x-ray`, `imaging`, `radiology`, `medical center`, `مركز طبي`, `مركز صحي`, `company`, `شركة`, `مجمع`

**Word-boundary regex (not plain substring):**
`lab` and `scan` are handled separately with `re.compile(r'(lab|scan)(?![a-z])', re.IGNORECASE)`. Plain substring matching would cause false positives — `"Labib"` contains `"lab"` and `"DentaScan"` contains `"scan"`, but neither should be excluded. The negative lookahead `(?![a-z])` ensures the word is only matched when it is not immediately followed by another letter.

Note on `مصحة`: this Arabic word means *sanatorium* or *psychiatric facility* — distinct from a regular hospital (`مستشفى`) but equally not a private clinic. It was added after real test data revealed psychiatric sanatoriums appearing in search results.

#### Layer 1b — Rule-based acceptance

If the name was not excluded, it is checked for an explicit doctor title. If a doctor title is present and no canceller keyword is found, the clinic is accepted immediately as a Private Clinic with `confidence_score = "High"`.

**Doctor title triggers** — all known Egyptian naming conventions:

| Form | Example |
|---|---|
| `dr.` / `dr ` | `Dr. Ahmed Samy` |
| `دكتور` | `دكتور محمد علي` (masculine) |
| `دكتورة` / `دكتوره` | `دكتورة شيماء الشبراوي` (feminine — both spellings) |
| `الدكتور` / `الدكتورة` / `الدكتوره` | `عيادة الدكتورة سهام أبو حامد` (with definite article) |
| `د.` | `د. أسامة عامر` (abbreviated with dot) |
| `د/` | `د/هاله سعيد` (abbreviated with slash) |

The feminine forms `دكتورة` and `دكتوره` are two different spellings of the same word in Egyptian Arabic — `دكتورة` is the standard written form and `دكتوره` is the common colloquial spelling. Both are included because Google Maps listings use both interchangeably. The definite article forms (`الدكتور`, `الدكتورة`) appear frequently in Arabic clinic names of the pattern `عيادة الدكتورة X` (Clinic of Dr. X).

**Canceller keywords** — override the title trigger even if a doctor title is present:
`center`, `centre`, `centers`, `مركز`, `hospital`, `مستشفى`, `معمل`, `complex`, `مجمع`

The cancellers also use a word-boundary regex for `lab` and `scan` to avoid the same false-positive issue described above.

This means `"Dr. Yehia Al Taher Center Maadi"` — which has a doctor title — is correctly sent to the LLM rather than rule-accepted, because `"center"` is present. The LLM then classifies it as Medical Center.

**Why the clinic keyword was removed from rule-accept:**

An earlier version of this layer also accepted names containing `clinic`, `clinics`, or `عيادة` without a doctor title. Testing across multiple specialties revealed that names like `"Prime Clinics"`, `"MedTown Clinics"`, and `"Sallèna Wellness Clinic"` all passed this trigger, but some of them are multi-branch chains or wellness brands rather than simple private practices. Since the LLM classifies these more accurately than a keyword rule, the clinic keyword was removed from the rule-accept trigger. A doctor title is now the only basis for rule-accept.

#### Layer 2 — LLM classification

Names that pass through both rule layers without a decision are sent to Groq's `llama-3.1-8b-instant` model. These are the genuinely ambiguous cases — names with no explicit doctor title and no obvious exclusion signal, such as:

- `"Dental House"` — dental practice or chain?
- `"nine psychology"` — private therapist or training organisation?
- `"Sawa for counseling and training"` — the word "training" is a genuine ambiguity signal
- `"Dutch physiotherapy"` — private physiotherapist or clinic brand?

The LLM receives the business name only — the address is intentionally excluded. During testing, passing the address caused incorrect classifications: a doctor renting a room inside a building called "Cairo Medical Center" had their practice misclassified as a Medical Center because the LLM read the address field. The business name is what defines the category of the business, not the building it operates from.

`temperature=0` is set for fully deterministic, reproducible classifications. `response_format={"type": "json_object"}` is used to enforce valid JSON at the API level, eliminating the need for fragile JSON extraction logic.

The LLM call uses `tenacity` for retry with exponential backoff (up to 3 attempts), which handles transient Groq API failures gracefully.

---

### Token optimization

Running LLM classification on every result would quickly exceed Groq's free-tier limit of 6,000 tokens per minute, especially with parallel processing of 20 results. The three-layer architecture was built specifically to address this.

**Measured impact across 6 specialties (118 clinics):**

- Rule-accept handled approximately 60–70% of results with zero tokens
- Rule-exclude handled approximately 5–10% of results with zero tokens
- Only 20–35% of results reached the LLM

At roughly 130 tokens per LLM call, classifying all 118 clinics naively would cost ~15,300 tokens. With the rule layers, the actual token consumption was approximately 4,500–5,000 tokens across all runs — a reduction of roughly 65%.

**Batched parallel processing:**

Within the LLM layer, classification is parallel (using `asyncio.gather`) but batched. The default batch size is 5 with a 3-second delay between batches. This keeps peak token consumption within Groq's per-minute rate limit while still being meaningfully faster than sequential processing.

The `--batch-size` and `--delay` CLI arguments allow tuning for different API tier limits.

---

### Stage 4 — Doctor name extraction (`normalizer.py`)

Doctor names are extracted from clinic name strings using regex, not the LLM. This keeps the extraction free (no tokens) and deterministic.

The extraction handles all common Egyptian doctor naming patterns. Pattern order matters critically for Arabic — abbreviated forms (`د.` / `د/`) are matched before the full `دكتور` pattern because they have an unambiguous delimiter. Without this ordering, a name like `"عيادة عظام د.ابراهيم شعراوي دكتور عظام ومفاصل"` would match the trailing `"دكتور عظام"` (bones doctor) first and return nothing after cleanup, instead of correctly extracting `"ابراهيم شعراوي"` from the `"د."` match.

After capturing, trailing medical specialty words are stripped and the result is capped at 3 words. Egyptian names commonly follow the pattern first name + father's first name + family name (3 words), so the cap covers full names without bleeding into specialty descriptions like `"استشاري امراض النساء"` (consultant in gynecology).

Returns `None` if no doctor title pattern is found — clinic names without a personal doctor name simply have no `doctor_name` value.

---

### Stage 5 — Storage and deduplication (`storage.py`)

Results are appended to `data/clinics.csv`. Before writing, each new record is checked against all existing records using a composite key of `(clinic_name.lower(), phone_number)`. Records that already exist in the CSV are silently skipped.

A threading lock (`threading.Lock`) protects concurrent writes, making the storage layer safe to use with the parallel classification in Stage 3.

---

## On the confidence score

The `confidence_score` field reflects the classification layer that made the decision:

**`High`** — assigned by the rule-based layers (Layer 1a / 1b). These are deterministic decisions: a name with an explicit doctor title and no canceller is unambiguously a private clinic. A name containing `"مستشفى"` is unambiguously not. Rules do not have uncertainty, so High confidence is the correct and honest representation of the decision.

**`Medium` / `Low`** — assigned by the LLM (Layer 2). These reflect the LLM's own expressed confidence on genuinely ambiguous names. In practice, the LLM tends to return High for most names it classifies as Private Clinic because the prompt instructs it to default to Private Clinic when uncertain — meaning a Low or Medium score from the LLM is a meaningful signal that the record deserves human review.

This design is intentional. The confidence field is most useful as a flag for human review — and the cases most worth reviewing are precisely those that required LLM judgment rather than a clear rule.

---

## Running without Docker

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and fill in your API keys
cp .env.example .env

python -m app.main --query "Dentist in October"
python -m app.main --query "Dermatologist in Maadi" --debug
```

---

## Post-run investigation

`investigate.py` (inside the `app/` directory) is a local analysis tool for inspecting the saved CSV after a run:

```bash
python -m app.investigate
python -m app.investigate --query "Maadi"   # filter by keyword
python -m app.investigate --file data/clinics.csv
```

It reports total results, confidence distribution, doctor name extraction rate, regex extraction misses, and flags any saved clinics whose names contain suspicious keywords like `"center"` or `"scan"` for manual review.

---

## Dependencies

| Package | Purpose |
|---|---|
| `groq` | Async LLM client for Groq API |
| `google-search-results` | SerpApi client |
| `python-dotenv` | Environment variable loading |
| `tenacity` | Retry logic with exponential backoff |
| `asyncio` | Async parallel classification |
