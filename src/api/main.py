"""FastAPI application — Yamaha Inventory Optimisation API v1."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api import deps as _deps
from src.api.routers import (
    bikes, catalog, classification, eda, forecast, inventory, overview, parts, policy, rl, sku,
)

_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"

app = FastAPI(
    title="Yamaha Inventory Optimisation API",
    description=(
        "REST API for the 14-stage spare-parts inventory pipeline. "
        "Exposes classification, demand forecasting, stock tracking, "
        "ROL/ROQ policy, RL-optimised order quantities, and per-SKU detail."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PREFIX = "/api/v1"
# Stages 1-8
app.include_router(bikes.router,           prefix=_PREFIX)
app.include_router(parts.router,           prefix=_PREFIX)
app.include_router(eda.router,             prefix=_PREFIX)
app.include_router(catalog.router,         prefix=_PREFIX)
# Stages 9-14
app.include_router(overview.router,        prefix=_PREFIX)
app.include_router(classification.router,  prefix=_PREFIX)
app.include_router(forecast.router,        prefix=_PREFIX)
app.include_router(inventory.router,       prefix=_PREFIX)
app.include_router(policy.router,          prefix=_PREFIX)
app.include_router(rl.router,              prefix=_PREFIX)
app.include_router(sku.router,             prefix=_PREFIX)


@app.get("/health", tags=["Meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/cache/clear", tags=["Meta"])
def clear_cache() -> dict[str, str]:
    """Clear all in-memory parquet caches so regenerated data is reloaded on next request."""
    _deps._load.cache_clear()
    _deps._load_orders_enriched.cache_clear()
    _deps._load_sales_enriched.cache_clear()
    return {"status": "cleared"}


# Serve built React frontend — must be registered AFTER all API routes
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(_DIST / "favicon.svg")

    @app.get("/icons.svg", include_in_schema=False)
    def icons() -> FileResponse:
        return FileResponse(_DIST / "icons.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """Return index.html for any non-API route so React Router handles navigation."""
        return FileResponse(
            _DIST / "index.html",
            headers={"Cache-Control": "no-store"},
        )
