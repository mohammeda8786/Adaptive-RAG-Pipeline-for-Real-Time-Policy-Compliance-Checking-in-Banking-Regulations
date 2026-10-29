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
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `QDRANT_URL`, `QDRANT_API_KEY`, `GROQ_API_KEY`, and `JWT_SECRET_KEY` in `.env` on the server only. Generate a unique JWT signing key, for example with `python -c "import secrets; print(secrets.token_urlsafe(48))"`, then paste its output as the value of `JWT_SECRET_KEY`. Do not use an issued login token as the signing key, and never add secrets to browser code or client-side environment variables. Set `JWT_COOKIE_SECURE=1` when serving over HTTPS and update `RBI_CORS_ORIGINS` to the exact frontend origins when hosting the UI separately. If a key has been exposed, revoke it and enter a replacement directly in `.env`. Set `QDRANT_COLLECTION` to the collection populated by `embed_and_index.py`; it defaults to `rbi_regulations`.

To split and index every numbered `data/rbi_text/rbi_<number>.txt` source in one step, configure `QDRANT_URL` and `QDRANT_API_KEY` in `.env` and run:

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
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT session length (5–1440 minutes). |
| `JWT_COOKIE_SECURE` | `0` | Set to `1` to require HTTPS for the authentication cookie. |
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

## Corpus and limitations

The source corpus contains RBI documents currently available from the RBI index; it is not a guaranteed historical archive. Comparison reports require selecting at least two indexed documents. Scanned policy PDFs without selectable text must be OCR-processed before upload. Validate citations and applicability against official RBI publications before making compliance decisions.
