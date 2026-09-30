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
# Configure a supported SMTP sender in backend/.env before email sign-in can work.
# Set ADMIN_EMAILS to a comma-separated list of administrator email addresses.
uvicorn backend.main:app --reload --env-file backend/.env
```

Email-code sign-in requires an SMTP sender configured in `backend/.env`. If `SMTP_HOST` is empty, the app reports that no email was sent and does not create a verification code. The recipient can be an Outlook address; the sender must be configured separately. Outlook.com SMTP requires OAuth2/Modern Auth according to [Microsoft's settings](https://support.microsoft.com/en-us/outlook/pop-imap-and-smtp-settings-for-outlook-com); this app's generic SMTP integration uses username/password authentication and does not implement Microsoft's OAuth flow, so use an SMTP provider that supports this integration or add OAuth support before using an Outlook.com mailbox as the sender. Never put credentials in chat, source control, or the frontend. In production, set a unique random `AUTH_CODE_SECRET`, `GUIDELY_ENV=production` to disable guest access by default, `ALLOW_GUEST_LOGIN=1` only if you intentionally want guest readers, and `AUTH_COOKIE_SECURE=1` when serving over HTTPS. Add trusted administrator emails to `ADMIN_EMAILS`; verified accounts default to reader access.

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

Email sign-in sends a six-digit one-time code through configured SMTP. When SMTP is not configured, sign-in returns an explicit error and no code is created or shown. Codes are stored as keyed HMACs, expire after 10 minutes, are limited to one request per email per minute and five per IP per hour, and verification allows five attempts. Sessions use random server-side tokens stored as hashes and an HttpOnly cookie. The example config sets `ADMIN_EMAILS=polytechnic.main@outlook.com`. Set it to your trusted administrator addresses to grant admin access after email verification. Other email accounts are readers; guest sessions are temporary reader access. Only administrators can upload/edit/delete/re-index documents or export logs.

## Endpoints

- `GET /api/auth/config`, `POST /api/auth/request-code`, `POST /api/auth/verify-code`, `POST /api/auth/guest`, `GET /api/auth/me`, `POST /api/auth/logout`
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

Email-code sign-in, guest sessions, reader/admin roles, categories, document upload/edit/remove, sample re-index, follow-up history, CSV query export, health/metrics, and responsive reader/admin views are included. Guests and readers can ask questions; only configured admins can change documents or export query logs. This starter does not parse PDF/Word files. Configure an SMTP provider before expecting verification codes to arrive in real inboxes; local development displays the code in the app. For production, add access controls, data retention policy, deployment-specific CORS and cookie settings, data retention policy, and operational monitoring.


## Security review

| Review | Result |
|---|---|
| Frontend dependency advisories (`npm audit`) | 0 known advisories at review time.
| Backend dependency advisories (`pip-audit`) | No known vulnerabilities reported for the declared requirements at review time.
| Python environment consistency (`pip check`) | No broken requirements.
| API authorization | Unauthenticated search blocked; guest/reader writes denied; configured admin writes allowed.
| Abuse/input checks | OTP per-email and per-IP throttles, five code attempts, 10-minute expiry, HMAC code hashes in production, 2 MiB document upload cap, bounded question/history inputs.
| Browser protections | HttpOnly session cookie, configurable Secure flag, allowed-Origin check on state-changing API requests, basic security headers; API docs disabled in production.

Known deployment limits: no SMTP credentials are included, so no real email has been sent; configure a trusted sender before production. Authenticated readers share access to the whole single-workspace library; there is no per-document ACL or tenant isolation. SQLite content and query logs are not encrypted at rest and have no retention cleanup. Review data retention and hosting controls before adding confidential documents. Dependency scanners report known published advisories only and do not prove the application is vulnerability-free.
