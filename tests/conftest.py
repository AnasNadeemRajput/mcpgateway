"""
testings/conftest.py

Sets up an isolated, throwaway SQLite database file for the whole test
session (NOT your dev mcpgateway.db), initializes tables, seeds mock
data, and exposes a FastAPI app wiring together the three HTTP-facing
routers (Task 2 gateway, Task 3 streaming guardrail, Task 4 router).

No app.py exists yet in the uploaded codebase, so this file builds the
equivalent wiring just for testing purposes. When you add a real app.py,
it should mount these same three routers the same way.
"""

import asyncio
import os
import tempfile

# Point at a throwaway temp file BEFORE anything imports databaseinit.db,
# since db.py reads SQLITE_DB_PATH at module import time.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["SQLITE_DB_PATH"] = _TMP_DB.name

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databaseinit.db import dispose_engine, init_db
from databaseinit.seed_data import run_seed


# @pytest.fixture(scope="session")
# def event_loop():
#     loop = asyncio.new_event_loop()
#     yield loop
#     loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    await init_db(drop_first=True)
    await run_seed()
    yield
    await dispose_engine()
    try:
        os.remove(_TMP_DB.name)
    except OSError:
        pass


@pytest.fixture(scope="session")
def app() -> FastAPI:
    from task2_gateway.authorization.proxy import router as gateway_router
    from task3_streaming_guardrail.streamhandler import router as stream_router
    from task4_rate_limit_router.failover import router as router_router

    fastapi_app = FastAPI(title="MCP Gateway Suite (test)")
    fastapi_app.include_router(gateway_router)
    fastapi_app.include_router(stream_router)
    fastapi_app.include_router(router_router)
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    return TestClient(app)


# Seeded in databaseinit/seed_data.py — reused across test files so
# nobody has to remember these strings.
ADMIN_TOKEN = "admin-token-abc123"
VIEWER_TOKEN = "viewer-token-xyz789"
EXISTING_CUSTOMER = "CUST-10001"          # pro plan, two $49.00 PAID invoices
NO_INVOICE_CUSTOMER = "CUST-10004"        # free/trialing, zero invoices
UNKNOWN_CUSTOMER = "CUST-99999"
