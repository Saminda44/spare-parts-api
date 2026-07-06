"""FastAPI application — Yamaha Inventory Optimisation API v1."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.api import deps as _deps
from src.api.routers import (
    bikes,
    catalog,
    classification,
    eda,
    forecast,
    inventory,
    overview,
    parts,
    policy,
    rl,
    sku,
)

_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"


def _warm_caches() -> None:
    """Pre-load parquets and pre-compute default EDA responses into cache.

    Runs in a daemon thread at startup so the server accepts requests immediately
    while warming proceeds in the background. First dashboard load will be fast
    once this completes (~5-15 s depending on parquet sizes).
    """
    try:
        logger.info("Cache warming: loading parquets …")
        _deps.get_orders_clean()
        _deps.get_sales_clean()
        _deps.get_orders_rejection_log()
        logger.info("Cache warming: parquets loaded — pre-computing EDA responses …")
        # Pre-populate EDA caches for the two most-requested parameter combinations.
        # Calling route functions directly with explicit args bypasses FastAPI Query objects.
        from src.api.routers.eda import get_orders_eda, get_sales_eda  # noqa: PLC0415

        get_orders_eda(rejection_limit=200, dealer_type="ALL", mc_category="ALL", year=0)
        get_orders_eda(rejection_limit=200, dealer_type="MC", mc_category="ALL", year=0)
        get_sales_eda(dealer_type="ALL", mc_category="ALL", year=0)
        get_sales_eda(dealer_type="MC", mc_category="ALL", year=0)
        logger.info("Cache warming complete — all default EDA responses cached.")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Cache warming failed (non-fatal): {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: RUF029
    threading.Thread(target=_warm_caches, daemon=True, name="cache-warmer").start()
    yield


app = FastAPI(
    title="Yamaha Inventory Optimisation API",
    lifespan=lifespan,
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
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PREFIX = "/api/v1"
# Stages 1-8
app.include_router(bikes.router, prefix=_PREFIX)
app.include_router(parts.router, prefix=_PREFIX)
app.include_router(eda.router, prefix=_PREFIX)
app.include_router(catalog.router, prefix=_PREFIX)
# Stages 9-14
app.include_router(overview.router, prefix=_PREFIX)
app.include_router(classification.router, prefix=_PREFIX)
app.include_router(forecast.router, prefix=_PREFIX)
app.include_router(inventory.router, prefix=_PREFIX)
app.include_router(policy.router, prefix=_PREFIX)
app.include_router(rl.router, prefix=_PREFIX)
app.include_router(sku.router, prefix=_PREFIX)


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
