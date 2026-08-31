"""FastAPI app entrypoint. Wires routes; no business logic lives here."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="OrderGuard")
