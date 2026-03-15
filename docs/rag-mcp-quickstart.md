# RAG MCP Quickstart

## 1. Install dependencies

```bash
cd E:\Github\OpenManus
uv sync
```

## 2. Start RAG MCP server

```bash
cd E:\Github\OpenManus
uv run python -m app.rag_mcp.server --transport stdio
```

Or:

```bash
uv run python run_rag_mcp_server.py --transport stdio
```

## 3. Register in BFF

1. Enable `rag` MCP server in the MCP 管理页（Postgres 模式写入 DB；非 Postgres 模式写入 `config/mcp.bff.json`）。
2. Start BFF:

```bash
uv run python -m uvicorn bff.main:app --host 0.0.0.0 --port 8000
```

3. Discover tools:

```http
POST /api/v1/mcp/servers/rag/discover
```

## 4. Tool usage shape

- `index(paths: string[], force_reindex?: boolean)`
- `search(query: string, top_k?: number, with_rerank?: boolean)`
- `stats()`

Example flow:
1. Call `index` with one or more file/folder paths.
2. Call `search` with a question.
3. Inject `hits` into the final chat prompt as context.

## Key env vars

- `RAG_VECTOR_BACKEND` (default `chroma`, supports `chroma|qdrant`)
- `RAG_CHROMA_PATH` (default `workspace/rag/chroma`)
- `RAG_VECTOR_COLLECTION` (default `openmanus_rag`)
- `RAG_QDRANT_URL` (only for `qdrant`, default `http://127.0.0.1:6333`)
- `RAG_DATABASE_URL` (PostgreSQL DSN for RAG metadata, recommended)
- `RAG_SQLITE_PATH` (default `workspace/rag/rag.sqlite3`, fallback when `RAG_DATABASE_URL` is empty)
- `RAG_HF_CACHE_DIR` (default `workspace/rag/hf-cache`, used for HF/transformers/sentence-transformers model cache)
- `RAG_EMBEDDING_PROVIDER` (`local` or `openai`)
- `RAG_EMBEDDING_MODEL` (default `BAAI/bge-m3`)
- `RAG_OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`)
- `RAG_RERANK_ENABLED` (default `true`)
- `RAG_RERANKER_MODEL` (default `BAAI/bge-reranker-v2-m3`)

Optional Qdrant mode:

```bash
RAG_VECTOR_BACKEND=qdrant
RAG_QDRANT_URL=http://127.0.0.1:6333
```
