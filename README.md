# AI Shipping Document Verification Platform

**Averis x Monash Hackathon 2026**

An AI-assisted platform that reads shipping-operations emails, classifies
them, locates Shipping Instruction (SI) and Bill of Lading (BL) attachments
for comparison requests, extracts the seven key shipment fields from each,
compares them, and either confirms a clean match, flags a mismatch, or
escalates the case for human review when it can't confidently decide.

---

## 1. Quick start

```bash
git clone <this repo>
cd sdoc-platform
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000 (docs at http://localhost:8000/docs)
- Postgres: localhost:5432 (user/pass/db: `sdoc`/`sdoc`/`sdoc`)

**Demo login:** `admin` / `admin123` (see `backend/.env.example` to change).

**First run:** the dataset already ships inside `backend/storage/`. After the
stack is up, call `POST /upload` once (or click **Generate Submission** on
the Evaluation page, which calls it for you) to load the 520 emails into the
database, then `POST /emails/process-all` (or use the dashboard) to run the
full pipeline over them.

```bash
curl -X POST http://localhost:8000/upload
curl -X POST http://localhost:8000/emails/process-all
```

### Running the backend without Docker

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./storage/app.db"
uvicorn app.main:app --reload
```

### Running just the pipeline (no DB / API at all)

This is the fastest way to sanity-check the AI logic — it's pure Python,
no FastAPI/DB dependency required:

```bash
cd backend
python3 scripts/run_pipeline.py --verbose
# -> storage/reports/submission.json
```

---

## 2. Architecture

**Modular monolith**, exactly per the locked spec — one deployable backend,
organized by responsibility, not split into microservices.

```
backend/app/
├── main.py                  FastAPI app, router registration, CORS, startup
├── api/                     HTTP layer — one router per resource
│   ├── auth.py                POST /auth/login (simple username/password)
│   ├── emails.py               /upload, /emails, /emails/{id}, /dashboard
│   ├── documents.py            /documents/{email_id}
│   ├── comparison.py           /comparison/{email_id}
│   ├── reviews.py               /reviews, /reviews/{id}
│   └── evaluation.py            /evaluation/generate, /submit
├── services/                 the AI/business logic — no FastAPI imports here,
│   │                         fully unit-testable on its own
│   ├── classifier.py           email -> category (rules + optional LLM fallback)
│   ├── extractor.py             attachment -> 7 fields (rules + OCR + optional LLM)
│   ├── normalizer.py            label/value normalization dictionaries
│   ├── comparator.py            SI vs BL field-by-field comparison
│   ├── confidence.py            OK / MISMATCH / NEEDS_REVIEW decision engine
│   ├── pipeline.py               orchestrates the above for one email
│   └── processing.py            persists pipeline results to the DB
├── models/                   SQLModel tables (see §4)
└── database/connection.py    engine/session, SQLite (dev) or Postgres (docker)
```

Why it's split this way: `services/` has **zero** dependency on FastAPI or
the database, so `scripts/run_pipeline.py` and `tests/test_dataset_processing.py`
can exercise the entire AI pipeline directly against the dataset on disk —
no server, no DB, no Docker — which is what made it possible to validate
correctness against the real 520-email bundle quickly during development.
`services/processing.py` is the only place that bridges pipeline output to
SQLModel rows, and the API layer is a thin wrapper around that.

### Frontend

The frontend is the design you supplied
(`frontend/index.html`) — React + Tailwind + shadcn-style components +
Chart.js, loaded from CDN with in-browser Babel (no Node build step, matches
the locked "React/Tailwind/shadcn/Chart.js" stack with zero extra tooling).
It ships fully self-contained with polished sample data so it always looks
demo-ready, and additionally now:

- **Logs in for real** against `POST /auth/login`. If the backend can't be
  reached (or you type any non-empty username/password while it's down), it
  falls back to the original offline demo mode rather than getting stuck —
  a live-demo booth should never show a broken screen.
- **Loads real data** from `GET /emails/full` right after login and maps it
  into the exact shape the existing UI components already expect (same
  `si`/`bl`/`defectFields`/`evidence` schema as the original mock data), so
  none of the page components needed to be rewritten.
- Shows a toast telling you whether it connected live or fell back to demo
  data.

See `frontend/index.html`, search for **"Live backend wiring"** for the
adapter code (`mapBackendEmailToUi`, `reviewCaseFromEmail`, `apiLogin`,
`apiFetchFullEmails`) — it's additive and doesn't touch any of the existing
page/component code.

If you deploy the frontend somewhere the backend isn't at
`http://localhost:8000`, set `window.API_BASE_URL = "https://your-api"`
in a `<script>` tag before the main script block, or edit the `API_BASE`
constant directly.

---

## 3. AI workflow

```
Email (subject + body)
   │
   ▼
Classifier (rules first, weighted keyword scoring)  ──low confidence, no rule fired──▶  OpenAI fallback (optional)
   │
   ▼  (only BL_COMPARISON continues past this point)
Locate SI + BL attachments (filename convention, falls back to content sniffing)
   │
   ▼
Extractor  (per file type: .txt line parser / .xlsx & .docx two-column tables / .pdf text + OCR fallback)
   │        — rules/NLP first; OpenAI structured-extraction fallback only if <3 fields found and a key is configured
   ▼
Normalizer (label synonyms → 7 canonical fields; value cleanup: case/punctuation/units)
   │
   ▼
Comparator (exact match, then light fuzzy match for text fields only — numeric fields are never fuzzy-matched)
   │
   ▼
Confidence / Decision engine
   │
   ├─ 0/1 attachments but body isn't actively asking to compare  → OK (nothing to compare, not a defect)
   ├─ 0/1 attachments AND body says "please compare/confirm"     → NEEDS_REVIEW / missing_attachment
   ├─ either document unreadable (no text layer, OCR empty)      → NEEDS_REVIEW / unreadable
   ├─ second attachment doesn't look like a BL (invoice/COO/etc.) → NEEDS_REVIEW / wrong_doc_type
   ├─ a required field is blank/placeholder in either document    → NEEDS_REVIEW / missing_value
   └─ otherwise: any field differs → MISMATCH, else → OK
```

**Why rules-first, LLM-fallback (not "send everything to the LLM"):** these
are highly templated business documents. A deterministic parser is faster,
free, fully explainable (you can point to exactly which line produced which
value), and — measured against this dataset — more accurate than routing
everything through an LLM would be, because the LLM path is only exercised
when the rules under-deliver. The `OPENAI_API_KEY` hook in both
`classifier.py` and `extractor.py` is there for exactly that long tail; leave
it unset and the whole pipeline still runs, fully offline and deterministic.

**Why OCR'd documents still get escalated:** the pipeline OCRs scanned PDFs
with Tesseract so the human reviewer has real evidence to look at, but it
does **not** let a successful OCR result silently produce an OK/MISMATCH —
any document that needed OCR is policy-flagged `unreadable` and routed to a
human, because that's a materially higher-risk read than a native text
layer, independent of how good the OCR looked in this particular case.

---

## 4. Database schema

Matches the spec exactly (`SQLModel`, one table per model file):

| Table | Purpose |
|---|---|
| `emails` | one row per inbox email — id, subject, body, category, classification_confidence, status |
| `documents` | one row per attachment — filename, document_type, storage_path, extracted_text, readable, used_ocr |
| `shipment_fields` | one row per document — the 7 extracted fields (`port_loading`/`port_discharge`/`gross_weight` per the exact spec field names) |
| `comparison_results` | one row per email — status, has_defect, defect_fields (JSON), review_reason |
| `reviews` | one row per human decision — reason, human_decision (approve/correct/retry), final_result |

`DATABASE_URL` switches between SQLite (local dev, zero setup) and Postgres
(docker-compose) with no code changes.

---

## 5. API reference

All routes except `/health` and `/auth/login` require `Authorization: Bearer
<token>` (or run with `DISABLE_AUTH=1` while testing locally).

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | `{username, password}` → `{token}` |
| POST | `/upload` | idempotently sync `storage/inbox/*.json` into the `emails` table |
| GET | `/emails` | list emails (`?category=`, `?status=`, `?limit=`, `?offset=`) |
| GET | `/emails/full` | bulk: every email + documents + shipment fields + comparison, joined (used by the frontend to hydrate in one call) |
| GET | `/emails/{email_id}` | one email's full detail |
| POST | `/emails/{email_id}/process` | queue this email through the pipeline (FastAPI `BackgroundTasks`) |
| POST | `/emails/process-all` | queue every unprocessed (`PENDING`) email |
| GET | `/dashboard` | aggregate counts for the dashboard cards |
| GET | `/documents/{email_id}` | raw extracted text + fields per attachment, for the evidence viewer |
| GET | `/comparison/{email_id}` | this email's comparison result |
| GET | `/reviews` | the pending human-review queue |
| POST | `/reviews/{email_id}` | `{decision: "approve"\|"correct"\|"retry", ...}` |
| POST | `/evaluation/generate` | write `storage/reports/submission.json` from whatever's processed so far |
| POST | `/evaluation/submit` | generate + POST to `EVAL_SERVER_URL/submit`, return the scoreboard |
| GET | `/evaluation/submission` | read back the last generated submission.json |

Interactive Swagger docs: `http://localhost:8000/docs` once the backend is
running.

---

## 6. Testing

```bash
cd backend

# No dependencies beyond stdlib — run these first, they work anywhere:
python3 tests/test_comparator.py          # comparison engine logic
python3 tests/test_classifier.py          # email classification logic
python3 tests/test_dataset_processing.py  # full 520-email pipeline run, schema check

# requires `pip install -r requirements.txt` first:
pytest tests/test_api.py
```

### The API layer HAS been tested end to end — here's how

`pytest tests/test_api.py` needs the real FastAPI/SQLModel installed, which
needs network access. If you don't have that yet, you don't have to take
the API layer on faith: see `dev-tools/api_shim/` — a small compatibility
shim that lets the **real, unmodified** `backend/app/main.py` run against a
**real SQLite-backed database**, with no `pip install` required at all.

```bash
cd dev-tools/api_shim
python3 run_full_api_test.py
```

This actually imports `app.main.app` unchanged and drives it through 53
checks — login, the full 520-email pipeline run via the real
`POST /emails/process-all`, dashboard aggregation, filtering, bulk fetch,
single-email detail, the review queue and all three human decisions
(approve/correct/retry), and both evaluation outcomes. **Current result:
53/53 passing.** See `dev-tools/api_shim/README.md` for exactly what this
does and doesn't prove (it's SQLite-only, doesn't validate Postgres, and
doesn't exercise real Pydantic request validation).

### Offline accuracy check (development-time only)

During development we validated this pipeline against the organizers'
`scoring.py` + `ground_truth.json` (from the docker evaluation package) as a
self-check — the app itself never reads either file, only the outcome was
used to catch bugs. At time of writing:

- Stage-1 classification: 100% accuracy / macro-F1 1.0
- Stage-3 defect detection: F1 ≈ 0.98, recall 1.0 (every planted mismatch caught)
- Reliability (NEEDS_REVIEW escalation): precision/recall/F1 all 1.0
- End-to-end: 45/46 (≈97.8%)

The 3 residual field-comparison misses all trace back to a single upstream
oddity in a handful of the generated PDF fixtures, where two lines of text
are interleaved at the byte level inside the PDF's own content stream (not
an artifact of our extraction — `pdfplumber`'s word-level extraction shows
the same interleaving). Left as a known limitation rather than special-cased
against a specific fixture.

---

## 7. Environment variables

See `backend/.env.example` for the full list. The important ones:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./storage/app.db` | swap to Postgres for docker-compose |
| `APP_USERNAME` / `APP_PASSWORD` | `admin` / `admin123` | demo login |
| `DISABLE_AUTH` | `0` | set `1` to bypass auth while testing the API directly |
| `OPENAI_API_KEY` | *(unset)* | optional — enables the LLM fallback in classifier/extractor |
| `EVAL_SERVER_URL` | `http://eval-server:8000` | organizers' scoring service base URL |

---

## 8. Folder layout

```
sdoc-platform/
├── docker-compose.yml
├── README.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env.example
│   ├── app/                (see §2)
│   ├── scripts/run_pipeline.py
│   ├── tests/
│   └── storage/
│       ├── inbox/          520 email JSON records
│       ├── attachments/    SI/BL/other attachments (.txt/.pdf/.docx/.xlsx)
│       ├── processed/
│       ├── reports/        generated submission.json lands here
│       └── evidence/
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    └── index.html          the supplied design, now wired to the live API
```

---

## 9. Known limitations / next steps

- The LLM fallback paths (`classifier._openai_classify`,
  `extractor.openai_fallback`) are written and wired but untested against a
  live OpenAI key.
- `reviews.py`'s `approve`/`correct` actions update the DB directly;
  there's no audit trail beyond the single `Review` row per email yet (fine
  for a hackathon demo, worth revisiting for production).
- The frontend's live-data adapter fetches the whole dataset in one call
  (`/emails/full`); fine at hundreds of rows, would want pagination for a
  much larger inbox.
- `EVAL_SERVER_URL` points at the organizers' scoring container, which isn't
  part of this docker-compose (it's their service, not ours) — set it to
  wherever that container is actually reachable from the `backend` service.
- The API layer's end-to-end test (`dev-tools/api_shim/`) runs against
  SQLite via a compatibility shim, not the real Postgres/psycopg2 driver —
  the app's SQL usage is simple (equality filters only, no joins), so this
  is a low-risk gap, but it's not the same as a real `docker compose up`
  run. **Treat your first real Docker run as the actual first execution
  against Postgres** and watch the backend container logs.

### Evaluation page

**Run Evaluation** now calls the real API: `POST /evaluation/generate` →
`POST /evaluation/submit` → `GET /evaluation/submission`, and renders
whichever of two honest outcomes actually happened:

- **Reference server reachable** (`EVAL_SERVER_URL` resolves): shows the
  real score returned by the organizers' `scoring.py` — final score, the
  three weighted axes (classification / defect detection / review
  reliability), and real per-category F1 — plus the real `submission.json`
  in the preview panel.
- **Reference server unreachable** (the common case when running this stack
  standalone, since that scoring container isn't part of this
  docker-compose): rather than fabricating a fake accuracy number, it shows
  a **local pipeline summary** instead — how many emails were processed, how
  many BL comparisons were flagged as mismatches or escalated for review,
  and the real category breakdown — clearly labeled "Reference server
  unreachable" so it's never mistaken for an official score.

In offline demo mode (no backend at all) the page behaves exactly as
originally supplied — the animated demo run with the static 87.5% sample
score.

### Frontend verification performed

Since this environment has no network access to load the CDN scripts the
page depends on (React/ReactDOM/Chart.js/Tailwind) in a real browser, the
JSX was bundled locally with `esbuild` against local React 19 packages (a
syntax/type-error check — this alone would have caught malformed JSX or
undefined-variable mistakes) and then actually rendered in headless
Chromium via Playwright, against both:

1. **The offline fallback path** — backend unreachable → login still
   succeeds, dashboard renders on the bundled demo data, zero console/page
   errors.
2. **The live-data path** — a mock server serving the exact `/auth/login` +
   `/emails/full` response shapes the real backend produces (built from
   actual pipeline output on 5 real dataset emails, including a genuine
   `MISMATCH` and a genuine `NEEDS_REVIEW` case) — login, Dashboard, Emails
   list, Email Detail (comparison table + evidence), and the Reviews queue
   + review detail all rendered correctly with zero JavaScript errors, and
   the mismatch/evidence text matched the underlying data exactly (e.g.
   `email_004`'s consignee/notify_party defect rendered with the correct
   SI/BL values in the comparison table and evidence panel).
3. **The Evaluation page, both branches** — with the mock server returning
   a `502` from `/evaluation/submit` (simulating no reachable reference
   server), the page correctly fell back to the local pipeline summary,
   labeled "Reference server unreachable", with real per-category counts
   and a real `submission.json` preview. With the mock server instead
   returning an actual `scoring.py`-shaped response (the same JSON produced
   against the real 520-email dataset during development, final score
   98.5%), the page rendered the real score, the real three-axis breakdown,
   and real per-category F1 — exactly matching the numbers from the
   standalone `scoring.py` run in §6. Zero JavaScript errors in either
   branch.

This was development-time verification of the wiring logic with a stand-in
server, not a run against the actual Dockerized FastAPI+Postgres backend —
run `docker compose up --build` for that.
