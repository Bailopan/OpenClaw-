from __future__ import annotations

import os

from fastapi import FastAPI, Request
import uvicorn

from .main import run

app = FastAPI(title="Supplier Radar")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "supplier-radar"}


@app.post("/")
async def timer_handler(_: Request) -> dict:
    result = await run()
    return result["summary"]


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=False)
