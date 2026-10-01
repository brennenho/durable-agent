"""Tool service entry point. Application routes are not implemented."""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Durable Agent Tools", version="0.1.0")


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    raise HTTPException(503, "Service implementation has not started")
