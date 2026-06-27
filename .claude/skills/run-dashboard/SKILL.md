---
description: Start the Yamaha inventory dashboard (FastAPI backend + Streamlit/React frontend). Checks the venv, starts the API server, then opens the frontend dev server.
---

## Steps

1. Verify `.venv` is active; if not, activate `.venv\Scripts\Activate.ps1` (Windows).
2. Check `data/interim/` exists — warn if no processed data has been generated yet (stages 1-8 must have run).
3. Start the FastAPI backend:
   ```bash
   uvicorn src.api.main:app --host 0.0.0.0 --port 8080 --reload
   ```
4. In a second terminal, serve the React frontend:
   ```bash
   cd frontend
   npm run dev
   ```
   Or serve the built static files if the frontend is already built.
5. Smoke-test the API: `GET http://localhost:8080/health` — expect `{"status":"ok"}`.
6. Report the URLs to the user.

## Notes

- The API must be running before the frontend loads data.
- If port 8080 is in use, suggest `--port 8081`.
- Do NOT use `streamlit run` — this project uses a FastAPI + React architecture.
- Raw data in `data/raw/` is immutable — the dashboard reads from `data/interim/` and `data/outputs/`.
