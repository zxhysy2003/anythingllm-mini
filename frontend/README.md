# AnythingLLM Mini Frontend

Phase 0 provides the Vue/Vite shell for the future `anythingllm-mini` frontend.

## Development

Start the backend from `backend/`:

```bash
conda run -n anythingllm-mini python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the frontend from `frontend/`:

```bash
npm install
npm run dev
```

The Vite dev server proxies `/health` and `/workspaces` to FastAPI on
`http://127.0.0.1:8000`, so local development does not require CORS or Nginx.
