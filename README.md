# Guidely - Internal Knowledge Assistant

A source-cited Q&A workspace for internal policies, FAQs, and team guides. Add or edit documents, retrieve ranked passages, ask questions, and inspect the supporting excerpts.

## Sample dataset

Five original text files in `backend/data/sample-docs/` cover time off, security incidents, customer onboarding, support escalation, and remote work. A 15-question retrieval review set is in `backend/data/evaluation-queries.md`. Upload accepts UTF-8 `.txt` and `.md` files.

## Run locally

Requirements: Python 3.10+ and Node.js 18+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
# Set ADMIN_EMAILS and a private ADMIN_SIGNUP_KEY in backend/.env before creating the admin account.
uvicorn backend.main:app --reload
```

Sign in with an email address and password, or create an account. Passwords must be at least eight characters and are stored as salted PBKDF2 hashes. Emails listed in `ADMIN_EMAILS` receive administrator access; other accounts are readers. Guest sign-in can be disabled with `ALLOW_GUEST_LOGIN=0`.

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. API docs: http://localhost:8000/docs. Without an API key, the backend uses deterministic local feature-hash vectors and a lexical overlap reranker. For actual OpenAI embeddings and generated answers, set `OPENAI_API_KEY`, optionally `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`) and `OPENAI_CHAT_MODEL` (default `gpt-4o-mini`) in the environment before starting FastAPI. Re-index documents if changing between local and hosted embedding modes. Never commit API keys. Set `REQUIRE_OPENAI_API=1` if you want missing-key/model errors returned as HTTP 503 instead of using the offline answer fallback. The SQLite index defaults to `backend/data/guidely.sqlite3`.

## Pipeline

1. FastAPI validates uploaded UTF-8 text and Markdown and stores content and metadata in SQLite.
2. Paragraph-aware chunking makes passages of about 1,100 characters. With an OpenAI key, each passage gets an OpenAI embedding; without a key, a normalized deterministic feature-hash vector is used. Identical document content/category is skipped during re-index and counted as a cache hit.
3. A question is embedded with the selected provider and compared by cosine similarity; local mode also reranks by normalized lexical overlap. Results can be filtered by category.
4. With an OpenAI key, retrieved passages and recent conversation turns go to the chat model, which is instructed to answer only from those excerpts and cite them. Without a key, the best matching source passage is returned. Every response includes document, section, excerpt, and score.
5. Query latency and retrieved passage IDs are persisted. Upload/search failures and cache hit/new embedding counts appear in `/metrics`. The browser retains follow-up conversation history.

## Authentication and roles

Accounts use email and password with rate-limited login and registration. Passwords are stored as salted PBKDF2 hashes. Sessions use random server-side tokens stored as hashes and an HttpOnly cookie. Set `ADMIN_EMAILS` and `ADMIN_SIGNUP_KEY` before creating an administrator account; the setup key prevents someone else from claiming an admin email before its owner. Other email accounts are readers. Guest sessions are temporary reader access. Only administrators can upload/edit/delete/re-index documents or export logs.

## Endpoints

- `GET /api/auth/config`, `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/guest`, `GET /api/auth/me`, `POST /api/auth/logout`
- `GET /health`, authenticated `GET /metrics`
- `GET /api/documents`, `POST /api/documents` (multipart file and category)
- `GET /api/documents/{id}/content`, `PUT /api/documents/{id}` (multipart replacement), `DELETE /api/documents/{id}`
- `POST /api/reindex` (bundled sample set)
- `POST /api/ask` with `{ "question": "...", "top_k": 5, "category": null, "history": [] }`
- `GET /api/export-queries` (CSV)

## Testing & Metrics

| Check | Target | Current result |
|---|---:|---|
| Retrieval@3 on 15 sample questions | >=80% | 15/15 (100%) expected source file in top three using local fallback. See `backend/data/evaluation-queries.md`; this is an automated file-match check, not a human relevance review.
| Answer reference coverage | >=90% | Smoke query returned source citations; broader manual review remains outstanding.
| Warm-cache latency | median <3s; p95 <5s | Median 3.2 ms and p95 9.1 ms across six local API smoke requests; small sample, not a production benchmark.
| Embedding cache effectiveness | 100% unchanged docs | Re-indexing the five unchanged samples added five cache hits; `/metrics` records cumulative `embedding_cache_hits` and `new_embeddings`.
| Failure handling | Clear status and UI error | Empty query 400, no relevant passage 404, bad upload 400/415, generic search failure 500; errors are counted by type. Missing API key selects local mode.
| Source precision (10 manual checks) | >=80% | Not yet human-reviewed. Source text is shown beside each response.
| Indexing throughput (5 files) | completes without errors | Five-file sample initialization succeeded; throughput timing not collected.

The smoke check passed `/health` (200), five-document indexing, vacation retrieval with `time-off-policy.txt` first, empty query (400), no-results (404), corrupt UTF-8 upload (400), document create/edit/delete, and the edit content endpoint (200). The production Vite build completed. Human answer/source review and a representative latency distribution remain to be collected.

## Scope

Email/password sign-in, guest sessions, reader/admin roles, categories, document upload/edit/remove, sample re-index, follow-up history, CSV query export, health/metrics, and responsive reader/admin views are included. Guests and readers can ask questions; only configured admins can change documents or export query logs. This starter does not parse PDF/Word files. For production, review data retention and operational monitoring before adding confidential documents.


## Security review

| Review | Result |
|---|---|
| Frontend dependency advisories (`npm audit`) | 0 known advisories at review time.
| Backend dependency advisories (`pip-audit`) | No known vulnerabilities reported for the declared requirements at review time.
| Python environment consistency (`pip check`) | No broken requirements.
| API authorization | Unauthenticated search blocked; guest/reader writes denied; configured admin writes allowed.
| Abuse/input checks | Per-IP login and registration throttles, salted PBKDF2 password hashes, 2 MiB document upload cap, bounded question/history inputs.
| Browser protections | HttpOnly session cookie, configurable Secure flag, allowed-Origin check on state-changing API requests, basic security headers; API docs disabled in production.

Known deployment limits: authenticated readers share access to the whole single-workspace library; there is no per-document ACL or tenant isolation. SQLite content and query logs are not encrypted at rest and have no retention cleanup. Review data retention and hosting controls before adding confidential documents. Dependency scanners report known published advisories only and do not prove the application is vulnerability-free.
