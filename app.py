"""
app.py

Main FastAPI application entrypoint. Brings together all four tasks:
  1. MCP Server (Task 1)
  2. Gateway (Task 2)
  3. Streaming Guardrail (Task 3)
  4. Rate Limited Router (Task 4)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from databaseinit.db import dispose_engine, init_db
from databaseinit.seed_data import run_seed
from task2_gateway.authorization.proxy import router as gateway_router
from task3_streaming_guardrail.streamhandler import router as stream_router
from task4_rate_limit_router.failover import router as router_router
from utils.logging_utils import setup_logging

# Setup logging
setup_logging()

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown."""
    logger.info("Starting MCP Gateway Suite...")
    
    # Initialize database
    await init_db()
    
    # Seed mock data
    try:
        await run_seed()
    except Exception as e:
        logger.warning("Seeding data failed: %s", e)
    
    logger.info("All services initialized successfully")
    yield
    
    # Cleanup
    logger.info("Shutting down MCP Gateway Suite...")
    await dispose_engine()
    logger.info("Cleanup complete")


# Create FastAPI application
app = FastAPI(
    title="MCP Gateway Suite",
    description="Multi-task MCP Gateway with Auth, Streaming Guardrail, and Rate Limiting",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(gateway_router)
app.include_router(stream_router)
app.include_router(router_router)


@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "MCP Gateway Suite",
        "version": "1.0.0",
        "endpoints": {
            "gateway": "/gateway/mcp",
            "streaming": "/stream/completion",
            "router": "/router/mcp",
            "status": "/router/status",
        },
        "documentation": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )