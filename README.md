# RegulaMind AI

RegulaMind AI is a single-origin FastAPI application with a focused chat UI for evidence-grounded RBI questions and saved recent conversations. The backend also exposes report-generation APIs, but report, compliance, and settings screens are intentionally not part of the current UI. Chat answers and report jobs share one retrieval service over the configured Qdrant collection.

Accounts use server-issued JWTs in an HTTP-only cookie. Registration and login require `JWT_SECRET_KEY`; conversations, uploaded policies, and reports are scoped to the signed-in account. Records created before account ownership was added remain unassigned and are not visible to new accounts.

## Retrieval and chat integration

The non-React chat UI posts each question to `/api/chat`; the React chat UI posts to `/api/chat/stream`. Both endpoints retrieve evidence directly from the complete original question, without invoking the Query Analyzer. Each request makes one Qdrant `query_points` request using the existing embedding model and takes the top five results, as the standalone retrieval script does. If a bank policy is selected, its text is added as retrieval context. Retrieved passages are used for evidence-grounded answer generation. Chat answers cite matched regulation sections, clauses, and page references. Page references are resolved from each indexed passage's source-text offsets and the original extracted text's page markers, including for older index entries that omitted page metadata. If source text or reliable offsets are unavailable, the UI leaves the page unstated rather than guessing. Each retrieved passage displays its similarity score; scores below `0.50` are labeled low confidence. Evidence with a top similarity score below `0.50` is treated as insufficient for a reliable answer in chat. Report retrieval continues using its existing filtered candidate/reranking path.

`queryanalyser.py` and `queryanalyser_retrival.py` remain available for standalone adaptive retrieval experiments, but neither is imported by the chat API. The unused chat adapter has been removed. New chat traces record the original-question retrieval plan and actual Qdrant execution counts.

The React chat header includes a **Retrieval Logs** panel. `GET /api/retrieval/logs` returns per-request retrieval and answer-generation outcomes, including historical analyzer traces; the former `/api/query-analyzer/logs` path remains as a compatibility alias. Refreshing the panel does not submit another question. The audit endpoint is read-only and unauthenticated, like the local development API; audit records can contain user questions and retrieved excerpts. Restrict network access to the service and protect or remove the JSONL file when it is not needed. `RBI_QUERY_AUDIT_LOG` can override its path.

## Setup

Python 3.10+ is recommended. From the project root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item backend\.env.example backend\.env
Copy-Item frontend-react\.env.example frontend-react\.env
```

Backend secrets and service settings belong in `backend\.env`. The React app's public settings belong in `frontend-react\.env`; only `VITE_` variables are exposed to browser code, so never put secrets there. Set `QDRANT_URL`, `QDRANT_API_KEY`, `GROQ_API_KEY`, and `JWT_SECRET_KEY` in the backend environment. Generate a unique JWT signing key, for example with `python -c "import secrets; print(secrets.token_urlsafe(48))"`, then paste its output as the value of `JWT_SECRET_KEY`. Do not use an issued login token as the signing key. Set `JWT_COOKIE_SECURE=1` when serving over HTTPS and update `RBI_CORS_ORIGINS` to the exact frontend origins when hosting the UI separately. If a key has been exposed, revoke it and enter a replacement directly in `backend\.env`. Set `QDRANT_COLLECTION` to the collection populated by `embed_and_index.py`; it defaults to `rbi_regulations`.

Google sign-in is optional. In Google Cloud Console, create a Web OAuth client, add `http://127.0.0.1:8000/api/auth/google/callback` as an authorized redirect URI for local development, and set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and `RBI_FRONTEND_URL` in `backend\.env`. For deployment, use HTTPS callback/frontend URLs and register the exact callback URI with Google. The backend verifies the profile through Google's HTTPS OpenID Connect userinfo endpoint, requires a verified email, and links an existing account with the same verified email. Never put the Google client secret in the frontend environment file.

To split and index every numbered `data/rbi_text/rbi_<number>.txt` source in one step, configure `QDRANT_URL` and `QDRANT_API_KEY` in `backend\.env` and run:

```powershell
python ingest_rbi_texts.py
```

The command regenerates a validated chunks JSON and progress file for each source, then indexes the combined chunks into the configured Qdrant collection. Re-running it is safe: chunk IDs are deterministic, so existing matching points are updated rather than duplicated. To process only the legacy `rbi_008.txt` source manually, run `python semantic_splitter.py` and then `python embed_and_index.py`. If no Groq key is configured, chat returns cited evidence without model-generated analysis. If a configured Groq request fails, the chat reports the failure instead of silently presenting a retrieval-only response.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `QDRANT_URL` | unset | Qdrant Cloud cluster URL for the RBI vector collection. |
| `QDRANT_API_KEY` | unset | Server-side Qdrant Cloud API key. |
| `QDRANT_COLLECTION` | `rbi_regulations` | Collection populated by the RBI indexing pipeline. |
| `GROQ_API_KEY` | unset | Enables grounded chat and structured report analysis. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq chat-completion model. |
| `JWT_SECRET_KEY` | unset | Server-only signing key for JWT cookies; use a unique random value of at least 32 bytes. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | JWT session length (5–1440 minutes). |
| `JWT_COOKIE_SECURE` | `1` | Set to `1` to require HTTPS for authentication and OAuth cookies; use `0` only for local HTTP development. |
| `JWT_COOKIE_SAMESITE` | `none` over HTTPS, `lax` locally | Controls cross-site cookie behavior. Use `none` when frontend and backend have different domains. |
| `GOOGLE_CLIENT_ID` | unset | Optional server-side Google OAuth web client ID. |
| `GOOGLE_CLIENT_SECRET` | unset | Optional server-side Google OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | unset | Exact registered OAuth callback URL, ending in `/api/auth/google/callback`. |
| `RBI_FRONTEND_URL` | `http://127.0.0.1:5173` | Frontend origin to return to after Google sign-in. |
| `RBI_CORS_ORIGINS` | Local Vite origins | Comma-separated exact frontend origins allowed to send authenticated API requests. |
| `RBI_DATA_DIR` | `data` | Root for local SQLite storage and generated files. |
| `RBI_DATABASE_PATH` | `data/rbi_chat.sqlite3` | Conversations, uploaded policies, report jobs, and metadata. |
| `RBI_REPORT_DIR` | `data/reports` | Generated PDF/CSV files. |
| `RBI_POLICY_DIR` | `data/bank_policies` | Uploaded policy originals. |
| `RBI_DISABLE_LLM` | unset | Set to `1` to disable external model calls, useful for offline testing. |

The first startup may download the embedding model if it is not cached. Keep the local `data` directory private because it contains uploaded bank policies and generated reports.

## Run

```powershell
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The FastAPI service and UI share the same origin. To export policy text again, run `python extract_policy_text.py` before starting the application.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Retrieval readiness and configured provider status. |
| `POST` | `/api/auth/register` | Create an account and issue an HTTP-only JWT cookie. |
| `POST` | `/api/auth/login` | Verify credentials and issue an HTTP-only JWT cookie. |
| `GET` | `/api/auth/google` | Begin Google OAuth sign-in. |
| `GET` | `/api/auth/google/callback` | Verify the Google response and issue the HTTP-only JWT cookie. |
| `GET` | `/api/auth/me` | Return the signed-in account. |
| `POST` | `/api/auth/logout` | Clear the authentication cookie. |
| `GET` | `/api/retrieval/logs?limit=50` | Recent retrieval and answer audit traces; optionally filter with `request_id`. |
| `GET` | `/api/sources` | Indexed RBI documents. |
| `POST` | `/api/chat` | Grounded chat; accepts `conversation_id`, `message`, and optional `policy_id`. |
| `POST` | `/api/chat/stream` | Server-sent event stream for the React chat UI; streams answer tokens and then source metadata. |
| `GET` | `/api/conversations` | Search/list saved conversations. |
| `GET` | `/api/conversations/{id}` | Reopen conversation messages and citations. |
| `DELETE` | `/api/conversations/{id}` | Permanently delete a conversation and its messages; retain associated reports without a conversation link. |
| `POST` | `/api/messages/{id}/feedback` | Save `helpful` or `not_helpful` feedback. |
| `POST` | `/api/policies` | Upload a selectable-text PDF or UTF-8 `.txt` bank policy (15 MB maximum). |
| `GET` | `/api/policies` | List uploaded policy metadata. |
| `POST` | `/api/reports` | Start a report job; returns HTTP `202` and a report ID. |
| `GET` | `/api/reports/{id}` | Poll report status and metadata. |
| `GET` | `/api/reports` | Search/filter/paginate persistent report history. |
| `GET` | `/api/reports/{id}/preview` | Preview a completed report. |
| `GET` | `/api/reports/{id}/file` | Download a completed PDF or gap CSV. |
| `POST` | `/api/reports/{id}/regenerate` | Re-run the saved report request as a new job. |
| `GET` | `/api/settings` | Runtime and report capability settings. |

PDF is available for all report types. CSV is offered only for compliance gap reports. Reports use background jobs and expose real states: queued, preparing, retrieving evidence, analyzing compliance when a policy is supplied, generating document, ready, and failed. A missing LLM does not fabricate findings; generated documents identify the analysis as retrieval-only and require manual review.

## Production deployment

The repository is configured for a split deployment:

- React frontend on Vercel.
- FastAPI backend on Render.
- Qdrant Cloud vector database.
- Groq API for answer and report generation.

Deployment configuration is provided by `render.yaml` and `vercel.json`.

### Render backend

Create a Python web service from the repository root. Use:

```text
Build command: pip install -r backend/requirements.txt
Start command: python -m uvicorn backend.api:app --host 0.0.0.0 --port $PORT
Health check path: /api/health
Python runtime: 3.11.9
```

Set these Render environment variables with secret values entered directly in Render:

| Variable | Required value or purpose |
| --- | --- |
| `QDRANT_URL` | Qdrant Cloud HTTPS cluster URL. |
| `QDRANT_API_KEY` | Qdrant Cloud API key. |
| `QDRANT_COLLECTION` | Indexed collection, normally `rbi_regulations`. |
| `GROQ_API_KEY` | Groq API key. |
| `GROQ_MODEL` | Groq model name. |
| `JWT_SECRET_KEY` | Random secret of at least 32 bytes. |
| `JWT_COOKIE_SECURE` | `1`. |
| `JWT_COOKIE_SAMESITE` | `none` for a separate Vercel frontend domain. |
| `RBI_CORS_ORIGINS` | Exact Vercel frontend origin, without a trailing slash. |
| `GOOGLE_CLIENT_ID` | Google OAuth web client ID. |
| `GOOGLE_CLIENT_SECRET` | Google OAuth web client secret, backend-only. |
| `GOOGLE_REDIRECT_URI` | `https://<render-service>.onrender.com/api/auth/google/callback`. |
| `RBI_FRONTEND_URL` | Exact Vercel frontend origin, normally `https://<project>.vercel.app`. |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Optional; defaults to `15`. |
| `RBI_DISABLE_LLM` | Optional; use `0` for normal operation. |

Render disk storage is required if SQLite conversations, uploaded policies, generated reports, and audit files must persist across restarts. The free Render filesystem is ephemeral. Configure:

```text
RBI_DATA_DIR=/path/to/persistent/disk/data
RBI_DATABASE_PATH=/path/to/persistent/disk/data/rbi_chat.sqlite3
RBI_REPORT_DIR=/path/to/persistent/disk/data/reports
RBI_POLICY_DIR=/path/to/persistent/disk/data/bank_policies
```

Do not place secrets in `render.yaml`.

### Vercel frontend

Import the repository and configure:

```text
Root Directory: frontend-react
Install Command: npm ci
Build Command: npm run build
Output Directory: dist
```

Set the Vercel environment variable:

```text
VITE_API_BASE_URL=https://<render-service>.onrender.com
```

The frontend contains no backend credentials. Redeploy after changing a `VITE_` variable.

### Google OAuth production setup

Add this exact authorized redirect URI in Google Cloud Console:

```text
https://<render-service>.onrender.com/api/auth/google/callback
```

Keep the callback HTTPS and keep `GOOGLE_CLIENT_SECRET` only in Render environment settings. The frontend is redirected to the configured `RBI_FRONTEND_URL` after sign-in.

### Deployment sequence

1. Rotate any credentials that were previously stored in a workspace `.env` file.
2. Configure the Render environment variables and deploy the backend.
3. Verify `https://<render-service>.onrender.com/api/health`.
4. Add the Vercel frontend URL to Render `RBI_CORS_ORIGINS`.
5. Deploy the frontend with its Render backend URL.
6. Register the production callback URL in Google Cloud Console.
7. Update Render OAuth variables and redeploy the backend.
8. Test registration, login, Google sign-in, chat retrieval, policy upload, and report generation.

Free-tier Render instances may sleep and initial requests can be slow. Render's filesystem and SQLite data do not persist reliably without persistent disk storage.

## Corpus and limitations

The source corpus contains RBI documents currently available from the RBI index; it is not a guaranteed historical archive. Comparison reports require selecting at least two indexed documents. Scanned policy PDFs without selectable text must be OCR-processed before upload. Validate citations and applicability against official RBI publications before making compliance decisions.
