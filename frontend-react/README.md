# RegulaMind AI - React frontend

This folder contains the React/Vite chat interface for RegulaMind AI. The frontend collects questions, displays retrieved RBI evidence and answers, and manages saved conversations. The FastAPI backend handles persistence, Qdrant retrieval, and optional Groq-generated answers.

For the full backend, ingestion, and environment configuration guide, see the [project README](../README.md).

## How the application works

1. When the React app opens, it checks `GET /api/health` for retrieval readiness and loads recent conversations with `GET /api/conversations?limit=30`.
2. When a user submits a question, the app sends `POST /api/chat/stream` with the question and the current conversation ID, if any.
3. The FastAPI backend retrieves passages from Qdrant using the complete original question (and selected bank-policy text, when supplied). It then generates a grounded answer from the retrieved RBI passages.
4. The backend streams status, answer tokens, and a final event containing the answer and its source passages. The React app displays the answer and citations as they arrive. The **Retrieval Logs** button in the chat header opens a read-only panel with per-request searches, actual retrieval outcomes, and returned passage details.
5. The backend saves the conversation and messages in SQLite. The frontend can reload a saved conversation, start a new one, delete a conversation, and submit feedback on an answer.

Qdrant and Groq credentials belong in the backend environment only; do not put secrets in the React app or Vite environment variables. If Groq is not configured, the backend can return a retrieval-based response using the matched sources.

The retrieval logs endpoint is unauthenticated and the JSONL audit file includes user questions and retrieved excerpts. Keep the backend network-restricted and remove the audit file when these diagnostics are no longer required. The standalone `queryanalyser.py` and `queryanalyser_retrival.py` scripts are separate from chat requests.

## Run locally

Start and configure the FastAPI backend first, following the project-level [setup instructions](../README.md). The backend must be available at `http://localhost:8000` by default.

In a second PowerShell window, from this folder:

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite, usually <http://localhost:5173>.

The frontend defaults to `http://localhost:8000` for its API. To use a backend at a different URL, set `VITE_API_BASE_URL` before starting Vite, for example:

```powershell
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev
```

The FastAPI backend currently allows cross-origin requests for the development frontend. Review and restrict its CORS configuration before deploying the services.

## Main API calls used by this frontend

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Show whether the backend retrieval service is ready. |
| `GET` | `/api/conversations?limit=30` | Load recent saved conversations. |
| `GET` | `/api/conversations/{id}` | Load a conversation and its messages and citations. |
| `POST` | `/api/chat/stream` | Submit a question and receive a streamed answer with source evidence. |
| `DELETE` | `/api/conversations/{id}` | Delete a conversation. |
| `POST` | `/api/messages/{id}/feedback` | Record helpful/not-helpful feedback. |

The React app currently provides the chat and conversation UI. Report generation and policy-upload endpoints exist in the backend but are not exposed as screens in this frontend.

## Development commands

```powershell
npm run dev
npm run lint
npm run build
npm run preview
```
