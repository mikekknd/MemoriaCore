"""YouTubeBridge FastAPI route registration。"""
from __future__ import annotations

from fastapi import FastAPI

from . import (
    connectors,
    director,
    fact_cards,
    memoria,
    research,
    sessions,
    summaries,
    testing,
    topic_packs,
    ui,
)


_ROUTE_MODULES = (
    ui,
    connectors,
    sessions,
    director,
    testing,
    topic_packs,
    fact_cards,
    research,
    summaries,
    memoria,
)


def register_routes(app: FastAPI, state) -> None:
    for module in _ROUTE_MODULES:
        module.configure(state)
        app.include_router(module.router)
