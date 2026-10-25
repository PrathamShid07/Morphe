# MORPHE — Setup guide

How to install, configure and run MORPHE from a fresh clone. Commands are given for **macOS/Linux** (bash/zsh) and **Windows** (PowerShell).
They were verified on macOS (Apple silicon). The Windows commands are written from the repository's structure and **have not been executed on Windows**; if one fails, the macOS/Linux line shows the intent.

All commands assume you are in the **repository root** (the folder that contains `backend/`, `frontend/`, `data/` and `docs/`) unless a `cd` says otherwise.

## 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.12** (tested: 3.12.14) | other 3.x versions are untested |
| Node.js | **22.13 or newer** (tested: 24.14) | Vite accepts Node 20.19+, but Vitest 5 needs 22.12+ and ESLint 10 needs 22.13+ on the 22 line |
| npm | any bundled with that Node (tested: 11) | the lockfile is `frontend/package-lock.json`, so use npm, not yarn/pnpm |
| MongoDB Community | 7 or 8 (tested: 8.0.12) | must listen on `localhost:27017` |
| Disk / network | about 1 GB for PyTorch and the 90 MB embedding model | internet is needed **once** to download the model (and BEIR datasets, if you run the benchmark) |

Not needed: Docker, any API key, or any paid service. Embeddings are computed locally by `sentence-transformers/all-MiniLM-L6-v2`.

**Qdrant** needs no installation in the default mode. It runs *inside* the API process and stores its data in `data/qdrant/` (`QDRANT_MODE=local`); this means the API must run with a **single worker**.
A separate Qdrant server (`QDRANT_MODE=server`) is optional and only used if you want to repeat the performance analysis (section 8).

## 2. Backend: create and activate the virtual environment

The virtual environment lives at **`.venv/` in the repository root** (the helper scripts, such as `backend/scripts/reset_demo.sh`, expect it there).

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
```

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
```

If PowerShell refuses to run the activation script, allow it for the current window only: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

**Which dependency file?** `backend/requirements.txt` is the install source (minimum versions). `backend/requirements.lock.txt` pins the exact top-level versions the project was tested with
(it is not a full freeze of transitive packages); for the tested set use `pip install -r backend/requirements.lock.txt` instead. After the 2026-10-25 benchmark work the lock file and the installed environment matched exactly and `pip check` reported no conflicts.

**Confirm the environment is active.** Your prompt normally starts with `(.venv)`, and these should point inside the repository's `.venv`:

```bash
which python pip            # macOS/Linux  -> .../Morphe/.venv/bin/python and .../Morphe/.venv/bin/pip
python --version            # Python 3.12.x
```

```powershell
Get-Command python, pip     # Windows      -> ...\Morphe\.venv\Scripts\python.exe
```

### Leaving and re-entering the environment

```bash
deactivate                   # leave the virtual environment (macOS/Linux and Windows)
source .venv/bin/activate    # macOS/Linux: re-enter it later, from the repository root
```

```powershell
.\.venv\Scripts\Activate.ps1 # Windows: re-enter it later, from the repository root
```

You do not have to activate the environment: every command below can instead call `.venv/bin/python` (Windows: `.venv\Scripts\python.exe`) directly, which is what the README does.

The virtual environment is **local to your machine**. It is large (about 1.4 GB here), it is excluded by `.gitignore`, and it must never be committed. If it breaks, delete the `.venv` folder and recreate it with the commands above; nothing else depends on its contents.

## 3. Configuration (`.env`)

```bash
cp backend/.env.example backend/.env          # Windows: Copy-Item backend\.env.example backend\.env
```

**This file is optional.** Every setting has a working default, so MORPHE starts without it (verified). Copy it only when you want to change something. Nothing in it is a secret by default, and no API key is required.

| Setting group | Controls | Change it when |
|---|---|---|
| `MONGODB_URI`, `MONGODB_DATABASE` | where metadata, events and versions are stored (default `mongodb://localhost:27017`, database `morphe`) | MongoDB is elsewhere or requires authentication |
| `QDRANT_MODE`, `QDRANT_LOCAL_PATH`, `QDRANT_URL` | `local` (default, folder `data/qdrant`), `memory` (tests), or `server` | you run a Qdrant server |
| `EMBEDDING_*` | model and dimension (`384` must match the model **and** the Qdrant collections) | almost never |
| `CORS_ORIGINS`, `APP_ENV`, `ALLOW_ADMIN_ENDPOINTS` | browser origins; `production` disables the demo/admin endpoints | deploying anywhere but your laptop |
| retrieval, compression, adaptation, evaluation groups | thresholds for routing, the token budget, structural changes and acceptance | you are tuning behaviour |

**Secrets:** if your MongoDB needs credentials, put the full URI only in `backend/.env`. That file is git-ignored; never paste credentials into `SETUP.md`, the README or any committed file.
`HF_TOKEN` (a Hugging Face token) is optional and only silences a rate-limit warning during the first model download.

## 4. MongoDB

Use an installed MongoDB service, or run a project-local copy that keeps its data in `data/mongo/`:

```bash
scripts/dev_mongo.sh        # macOS/Linux; uses `mongod` from PATH, else ./.tools/mongodb/bin/mongod. Leave this terminal open.
```

```powershell
mongod --dbpath data\mongo --bind_ip 127.0.0.1 --port 27017     # Windows (create data\mongo first)
```

To get `mongod` without installing it system-wide, download the Community build for your OS from mongodb.com/try/download/community and unpack it to `.tools/mongodb` (git-ignored).

Confirm it is running (with the virtual environment active):

```bash
python -c "import pymongo; print(pymongo.MongoClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=3000).server_info()['version'])"
```

It prints a version number (for example `8.0.12`) when MongoDB is reachable, or raises `ServerSelectionTimeoutError` when it is not.

## 5. Frontend dependencies

```bash
cd frontend
npm ci            # installs exactly what package-lock.json records (use `npm install` if you intend to change dependencies)
cd ..
```

## 6. Run the application

Use three terminals (the backend and frontend run side by side):

```bash
# terminal 1 — MongoDB (skip if it already runs as a service)
scripts/dev_mongo.sh

# terminal 2 — API (ONE worker only: the local Qdrant folder can be opened by a single process)
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
#   Windows, environment active:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# terminal 3 — dashboard
cd frontend
npm run dev
```

| URL | What |
|---|---|
| http://localhost:5173 | the dashboard (proxies `/api` and `/health` to port 8000) |
| http://127.0.0.1:8000/docs | interactive OpenAPI documentation of the FastAPI backend |
| http://127.0.0.1:8000/health | readiness: reports MongoDB, Qdrant and embedding-model status with the reason when something is wrong |

The first start loads the embedding model (about 10 s after the one-time download).

### Demo data (deterministic)

```bash
.venv/bin/python backend/scripts/demo.py full     # reset -> seed -> merge -> mixed -> three 50-query windows -> evaluation
.venv/bin/python backend/scripts/demo.py seed     # only load the 60 sample memories + a baseline version
.venv/bin/python backend/scripts/demo.py reset    # remove ONLY MORPHE's own Qdrant collections and MongoDB database
```

These scripts call the running API, so start MongoDB and the API first (section 6). (Windows: `.venv\Scripts\python.exe backend\scripts\demo.py full`.) `backend/scripts/reset_demo.sh --offline` resets with the server stopped. The Adaptation and Compression pages have buttons for the same scenarios.
`reset` deletes MORPHE's demo data, so use it deliberately.

## 7. Tests and checks

```bash
# backend (needs NO MongoDB, Docker or model download: fake embedder, mongomock, in-memory Qdrant)
cd backend && ../.venv/bin/python -m pytest

# frontend
cd frontend && npm test && npm run lint && npm run typecheck

# end-to-end (drives the REAL running stack; needs MongoDB + API on :8000; resets and re-seeds the demo data)
cd frontend && npx playwright install chromium     # once
cd frontend && npm run e2e
```

Verify that the frozen BEIR baselines in the repository are untouched (standard library only, read-only):

```bash
python3 scripts/verify_baseline_hashes.py          # exits 0 when every committed baseline file matches docs/baselines/beir_v1_v2_freeze.json
```

## 8. BEIR benchmark and diagnostics

Requires MongoDB (section 4). The first run of a dataset needs internet (`--download`, a 3–4 MB zip each) and embeds the corpus (15–40 s on Apple MPS; slower on CPU); datasets and embedding caches go to `data/beir/` (git-ignored).

```bash
cd backend
../.venv/bin/python -m app.evaluation.run_beir --datasets scifact --methods global_dense,fixed_graph,adaptive_graph --query-limit 20 --download   # quick smoke run (marked non-official)
../.venv/bin/python -m app.evaluation.run_beir --datasets scifact,nfcorpus,arguana --methods bm25,global_dense,fixed_graph,adaptive_graph --split test --download
../.venv/bin/python -m app.evaluation.report_beir --latest

# diagnostics added after the first benchmark (analyses, not benchmark runs; output goes to backend/artifacts/beir_phase2/)
../.venv/bin/python -m app.evaluation.run_parity --datasets scifact,nfcorpus,arguana --run-id my_parity_01
../.venv/bin/python -m app.evaluation.routing_diagnostics --datasets scifact,nfcorpus,arguana --run-id my_diag_01

# retrieval strategies (progressive expansion, soft graph routing): tune on validation -> freeze -> evaluate; shown on Benchmarks -> Retrieval strategies
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id my_strat_01 --phase tune
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id my_strat_01 --phase evaluate
```

A full three-dataset run is slow (the strategy comparison takes about 3 hours) (the recorded v2 run took about 105 minutes on an Apple-silicon laptop). **Give every run a new `--run-id`** and never run two processes with the same id at once. New output is written under `backend/artifacts/` and is git-ignored; the frozen baseline results already in the repository are read-only evidence and should not be regenerated over.
The Qdrant performance analysis (`python -m app.evaluation.qdrant_perf --server`) additionally needs a Qdrant **server** (official release binary, version 1.20.0) started on port 6343 with its own storage folder; see `docs/BEIR_PERFORMANCE_ANALYSIS.md` for the exact configuration used. The binary is not part of the repository. Example start (macOS/Linux; run it from a scratch directory, because Qdrant writes a `.qdrant-initialized` marker file into its working directory):

```bash
QDRANT__SERVICE__HTTP_PORT=6343 QDRANT__SERVICE__GRPC_PORT=6344 QDRANT__SERVICE__HOST=127.0.0.1 \
QDRANT__STORAGE__STORAGE_PATH="$PWD/data/qdrant_perf_server/storage" QDRANT__TELEMETRY_DISABLED=true  /path/to/qdrant
```

## 9. Troubleshooting

| Symptom | Cause and safe fix |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell window, then activate again |
| `source: no such file` / prompt has no `(.venv)` | you are not in the repository root, or `.venv` does not exist yet; run section 2 from the root |
| `ModuleNotFoundError` (fastapi, qdrant_client, beir …) | the environment is not active, or dependencies are missing: activate it, then `pip install -r backend/requirements.txt` |
| `npm: command not found`, or Vitest/Vite fail on start | install Node 22.13 or newer; then `cd frontend && npm ci` |
| `/health` returns 503 `mongodb_unavailable` | MongoDB is not running at `MONGODB_URI`: start it (section 4) or fix the URI in `backend/.env` |
| `qdrant_unavailable … already accessed by another instance` | another process holds `data/qdrant`. Stop the other API/worker (run exactly one). Do not delete the folder unless you intend to reset MORPHE's data |
| `Address already in use` (port 8000, 5173, 27017) | something else is using the port. Find it (`lsof -i :8000`; Windows: `netstat -ano \| findstr 8000`) and stop it, or start on another port (`--port 8001`; for Vite set `VITE_PROXY_TARGET`) |
| settings seem ignored | the file must be `backend/.env` (not the repository root); restart the API after editing it |
| `DatasetNotAvailable` for a BEIR dataset | it is not cached in `data/beir/`: add `--download` (needs internet) |
| first start fails downloading the embedding model | needs internet once (Hugging Face); retry, optionally set `HF_TOKEN`. MORPHE never silently substitutes another model |
| `dimension_mismatch` | `EMBEDDING_DIMENSION` differs from the model or existing collections: restore the value, or deliberately reset the data (`demo.py reset`) |
| a benchmark run aborts with "INVALID RUN" or consistency errors | two runs probably shared a run id; rerun with a fresh `--run-id` (each run now also gets a unique temporary database) |

Never use `rm -rf data` as a "fix": `data/` also contains the sample documents and evaluation labels that the application needs. The disposable parts are `data/qdrant/`, `data/mongo/` and `data/beir/`, and removing them deletes that local state.
