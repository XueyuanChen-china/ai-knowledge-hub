# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

### Backend

Run backend commands from `backend/` unless noted otherwise.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Start PostgreSQL with the repository script:

```bash
cd backend
bash scripts/start_postgres_local.sh
```

Start Elasticsearch for indexing and semantic search:

```bash
docker run -d \
  --name ai-knowledge-hub-es \
  -p 9200:9200 \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
  docker.elastic.co/elasticsearch/elasticsearch:8.14.3
```

Run the API server:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

Health check and Swagger:

```bash
curl http://127.0.0.1:8000/health
# Swagger: http://127.0.0.1:8000/docs
```

Run backend tests with stdlib `unittest`:

```bash
cd backend
source .venv/bin/activate
python -m unittest discover -s tests -p 'test_*.py'
python -m unittest tests.test_upload_service
python -m unittest tests.test_graph_workflow.GraphWorkflowTests.test_rag_question_enters_retrieve
```

Many backend tests create temporary PostgreSQL databases through `tests/postgres_test_utils.py`, so local PostgreSQL must be running. `pytest` test files exist, but `pytest` is not in `backend/requirements.txt` and was not available in the checked venv; prefer `python -m unittest ...` unless the dependency is added.

Regenerate sample indexing files when their source data changes:

```bash
cd backend
python scripts/generate_sample_index_files.py
```

### Frontend

Run frontend commands from `frontend/`.

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
npm run lint
npm run build
npm run preview
```

The Vite dev server is configured for `http://localhost:3000`. API calls use `VITE_API_BASE_URL`, defaulting to `http://127.0.0.1:8000`.

## Architecture overview

This is an enterprise knowledge-base and expert-agent platform. The main flow is:

```text
file or knowledge item
  -> text extraction
  -> structured splitting into chunks
  -> PostgreSQL persistence
  -> BGE-M3 embeddings
  -> Elasticsearch dense_vector index
  -> semantic retrieval
  -> LangGraph router / retrieval / relevance review / answer workflow
  -> frontend workbench with chat, citations, and human review resume
```

### Backend FastAPI application

`backend/app/main.py` creates the FastAPI app, configures CORS, creates SQLModel tables on startup, optionally starts the upload post-processing worker, and registers these route groups:

- `/knowledge-bases` in `app/api/knowledge_base.py` for knowledge-base CRUD.
- `/knowledge-items` in `app/api/knowledge_item.py` for manual knowledge items, chunking, indexing, and chunk inspection.
- `/documents` in `app/api/document.py` for local file upload, extraction, chunking, indexing, and document chunk listing.
- `/search/semantic` in `app/api/search.py` for Elasticsearch vector search results enriched from PostgreSQL.
- `/uploads` in `app/api/upload.py` for the newer object-storage multipart upload control plane.
- `/api/chat`, `/api/chat/stream`, `/api/review/resume`, `/api/review/resume/stream`, and conversation endpoints in `app/api/chat.py`.

Configuration is centralized in `backend/app/config.py` via `pydantic-settings` and `backend/.env`. The runtime database is PostgreSQL only; `app/db/database.py` rejects non-PostgreSQL `DATABASE_URL` values and still contains development-time column backfill helpers because there is no Alembic migration setup yet.

### Data model

`backend/app/db/models.py` is the central schema definition. The core RAG entities are:

- `KnowledgeBase`: top-level collection.
- `Document`: uploaded source file and extracted text.
- `KnowledgeItem`: manually entered or document-derived manageable knowledge record.
- `Chunk`: smallest retrievable unit; stores PostgreSQL metadata and links to an Elasticsearch `vector_id`.
- `Conversation`, `Message`, `ReviewTask`: chat history and human-review state.

The large-upload path adds `UploadTask`, `UploadPart`, `UploadProcessingJob`, and `UploadAuditLog` for multipart upload state, post-upload processing, retries, quotas, and audit events.

### Document ingestion and splitting

`app/api/document.py` handles the older local upload path for `.txt`, `.md`, `.pdf`, `.docx`, and `.xlsx`. It writes files under `backend/data/uploads`, extracts text, creates or updates a document-derived `KnowledgeItem`, regenerates chunks, and indexes them.

The splitter facade is `app/services/text_splitter.py`; the real pipeline lives under `app/services/document_splitter/`. It parses source-specific structures, normalizes them, builds sections and blocks, then assembles chunks with metadata such as headings, pages, file type, and source file. Keep using the facade imports from `text_splitter.py` unless changing internals of the splitter pipeline.

### Vector indexing and retrieval

`app/services/vector_service.py` owns Elasticsearch integration. It lazily loads the `sentence-transformers` BGE-M3 model, creates one Elasticsearch index per knowledge base using `ELASTICSEARCH_INDEX_PREFIX + knowledge_base_id`, writes `dense_vector` documents, and searches with KNN. Stable vector IDs are derived from knowledge-base/document/item/chunk identity plus content hash.

`app/services/rag_service.py` wraps vector hits into `RetrievedDocument`, formats retrieved context, builds citations, and provides a local extractive fallback answer path. The graph answer node normally goes through `app/services/llm_answer_service.py`; router decisions go through `app/services/llm_router_service.py` with rule-based fallbacks in `app/graph/nodes.py`.

### LangGraph chat workflow

The current chat API uses `app/graph/langgraph_workflow.py`, not the older basic workflow except in tests. The compiled LangGraph is:

```text
START -> router -> direct -> END
                -> retrieve -> relevance_check -> answer -> END
                                             -> human_review -> answer -> END
                                                             -> review_rejected -> END
                -> complex -> END
```

`InMemorySaver` is used for checkpoints, so interrupt/resume state is process-local. `app/api/chat.py` persists conversations and messages in PostgreSQL, streams progress as SSE, interrupts before answer generation when human review is needed, and resumes with `Command(resume={approved, human_note})`.

### Large-file upload architecture

The `/uploads` API is a separate object-storage control plane from the older `/documents` local upload. It is designed around Aliyun OSS, presigned multipart URLs, part completion tracking, abort/cleanup, actor limits, quota checks, upload audit logs, and asynchronous post-upload processing. The service boundaries are:

- `app/services/upload_service.py`: upload task lifecycle, part state, presign/complete/abort/cleanup orchestration.
- `app/services/storage/`: object storage adapter interface and Aliyun OSS implementation.
- `app/services/upload_postprocess_service.py`: enqueue and execute parse/split/index jobs after upload completion.
- `app/services/upload_worker.py`: application-local polling worker started from FastAPI startup when `UPLOAD_WORKER_ENABLED=true`.
- `docs/large-file-upload/`: roadmap and phase docs for the upload design.

OSS settings are required when the upload adapter is constructed. If working on unrelated backend code and local OSS credentials are unavailable, disable the worker or avoid endpoints that instantiate the storage adapter.

### Frontend

The frontend is React + Vite + TypeScript + Mantine. It intentionally keeps a Next-like `app/` page layout after migration to Vite:

- `frontend/app/**/page.tsx` contains route pages for dashboard, knowledge bases, documents, semantic search, and chat.
- `frontend/components/` holds reusable Mantine UI components.
- `frontend/lib/api/client.ts` is the central API client, including SSE parsing for streaming chat and review resume.
- `frontend/lib/api/types.ts` mirrors backend response shapes.
- `frontend/src/compat/next-link.tsx` and `frontend/src/compat/next-navigation.ts` are Vite aliases for `next/link` and `next/navigation`; keep these aliases in mind when imports look like Next.js.

Vite aliases `@` to `frontend/`, so imports such as `@/lib/api/client` are expected.

## Documentation map

- `README.md` contains the main project status, setup, current end-to-end flow, and sample validation questions.
- `docs/api.md` contains detailed API notes.
- `docs/day-*.md` are implementation history documents for completed milestones.
- `docs/improvements/*.md` records future improvement directions, including router upgrades, retrieval quality, deletion safeguards, and upload roadmap.
- `docs/large-file-upload/*.md` contains the current enterprise upload roadmap and phase-by-phase design notes.
