# AnythingLLM Mini Frontend

The Vue/Vite frontend provides a lightweight workbench for the existing
`anythingllm-mini` backend.

Current scope:

- Load and create workspaces.
- Load and create conversations inside the selected workspace.
- Read saved conversation messages.
- Send workspace chat and agent messages.
- Show basic assistant run metrics.

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
