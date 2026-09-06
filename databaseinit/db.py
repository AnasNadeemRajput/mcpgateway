"""
databaseinit/db.py

Central database bootstrap for the MCP Gateway Suite - SQLite version.

Responsibilities:
  1. Read SQLite connection settings from environment (.env).
  2. Ensure the database file exists and is accessible.
  3. Expose an async SQLAlchemy engine + session factory for the whole app.
  4. Expose `init_db()` to create ORM tables on startup.
  5. Provide database utilities for testing and migrations.

Design notes for SQLite:
  - SQLite is file-based, so no separate database creation step needed.
  - Uses aiosqlite (async driver) for non-blocking I/O.
  - WAL mode is enabled for better concurrent read/write performance.
  - Foreign key constraints are enforced (PRAGMA foreign_keys=ON).
  - Connection pool sizing is simpler since SQLite is single-writer.
  - Great for development, testing, and moderate production loads.
  - All PRAGMA settings are optimized for performance.

Performance Optimizations:
  - WAL mode: Better concurrency for reads/writes
  - NORMAL synchronous: Good balance of safety and speed
  - 20MB cache: Faster queries
  - Memory temp store: Faster temporary tables
  - Connection pooling: Reuse connections efficiently
  - Pre-ping: Detect dropped connections
"""

import logging
import os
from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# SQLite database file path
DB_PATH = os.getenv("SQLITE_DB_PATH", "./mcpgateway.db")

# Ensure the directory exists
DB_DIR = Path(DB_PATH).parent
DB_DIR.mkdir(parents=True, exist_ok=True)

# Connection pool sizing - tuned for SQLite
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # seconds

# SQLite performance settings (can be overridden via env)
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL")
SQLITE_SYNCHRONOUS = os.getenv("SQLITE_SYNCHRONOUS", "NORMAL")
SQLITE_CACHE_SIZE = int(os.getenv("SQLITE_CACHE_SIZE", "-20000"))  # 20MB
SQLITE_TEMP_STORE = os.getenv("SQLITE_TEMP_STORE", "MEMORY")

# Logging
ECHO_SQL = os.getenv("ECHO_SQL", "false").lower() == "true"

logger.info(f"SQLite database path: {os.path.abspath(DB_PATH)}")
logger.info(f"SQLite journal mode: {SQLITE_JOURNAL_MODE}")
logger.info(f"SQLite synchronous: {SQLITE_SYNCHRONOUS}")
logger.info(f"SQLite cache size: {SQLITE_CACHE_SIZE} bytes")

# ---------------------------------------------------------------------------
# Async Engine
# ---------------------------------------------------------------------------

def _build_async_url() -> str:
    """
    Builds the SQLite async URL with proper configuration.
    
    Returns:
        str: SQLite connection URL
    """
    return f"sqlite+aiosqlite:///{DB_PATH}"


engine = create_async_engine(
    _build_async_url(),
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    pool_pre_ping=True,           # Detect dropped connections
    echo=ECHO_SQL,                # Log SQL queries if enabled
    # SQLite-specific connection arguments
    connect_args={
        "check_same_thread": False,    # Allow multi-threaded access
        "timeout": 30,                  # Connection timeout in seconds
        "isolation_level": None,  # None = autocommit mode for SQLite, # Autocommit mode for better performance
    },
)


# ---------------------------------------------------------------------------
# Session Factory
# ---------------------------------------------------------------------------

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    # NOTE: `autocommit` was intentionally removed. SQLAlchemy 2.0 dropped
    # the `autocommit` kwarg from Session/sessionmaker entirely — passing
    # it raised TypeError on every AsyncSessionLocal() call, i.e. on every
    # single request. Session behavior is already non-autocommit by
    # default in 2.0, so removing it is a no-op change in behavior, just
    # a removal of a call that was crashing everything.
)


# ---------------------------------------------------------------------------
# Base Class for Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Shared declarative base for all SQLAlchemy models.
    
    All model classes should inherit from this base.
    """
    
    def __repr__(self) -> str:
        """Human-readable representation of the model."""
        attrs = []
        for key in self.__class__.__table__.columns.keys():
            value = getattr(self, key, None)
            attrs.append(f"{key}={value!r}")
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


# ---------------------------------------------------------------------------
# Event Listeners
# ---------------------------------------------------------------------------

@event.listens_for(engine.sync_engine, "begin")
def _emit_explicit_begin(conn):
    """
    Required companion to `isolation_level=None` in connect_args above.

    Setting isolation_level=None puts pysqlite/aiosqlite into true
    autocommit mode at the driver level — every statement commits the
    instant it runs, regardless of what SQLAlchemy's session thinks the
    transaction state is. Without this listener issuing an explicit
    BEGIN when SQLAlchemy starts a logical transaction, `session.commit()`
    and `session.rollback()` become no-ops: there's no open transaction
    for them to act on. That silently breaks the atomicity trigger_refund
    depends on (refund insert + invoice update must succeed or fail
    together) and makes error rollback do nothing.

    This is the documented SQLAlchemy/pysqlite recipe for getting correct
    BEGIN/COMMIT/ROLLBACK semantics out of a driver that otherwise
    manages transactions implicitly and inconsistently.
    """
    conn.exec_driver_sql("BEGIN")


@event.listens_for(engine.sync_engine, "connect")
def setup_sqlite_connection(dbapi_connection, connection_record):
    """
    Event listener that runs when a new SQLite connection is established.
    
    This ensures all connections have the correct PRAGMA settings.
    """
    cursor = dbapi_connection.cursor()
    try:
        # Enable WAL mode for better concurrency
        cursor.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
        
        # Set synchronous mode (NORMAL is a good balance)
        cursor.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
        
        # Set cache size for performance
        cursor.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE}")
        
        # Use memory for temporary tables
        cursor.execute(f"PRAGMA temp_store={SQLITE_TEMP_STORE}")
        
        # Enable foreign key constraints
        cursor.execute("PRAGMA foreign_keys=ON")
        
        # Enable recursive triggers (useful for complex operations)
        cursor.execute("PRAGMA recursive_triggers=ON")
        
        # Set busy timeout (avoid "database is locked" errors)
        cursor.execute("PRAGMA busy_timeout=30000")  # 30 seconds
        
        # Get and log the journal mode to confirm it was set
        cursor.execute("PRAGMA journal_mode")
        result = cursor.fetchone()
        logger.debug(f"SQLite connection configured: journal_mode={result[0] if result else 'unknown'}")
        
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# FastAPI Dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields a scoped async session per request.
    
    This function:
    1. Creates a new session for each request
    2. Ensures foreign keys are enabled
    3. Commits on success, rolls back on error
    4. Closes the session after the request completes
    
    Yields:
        AsyncSession: Database session for the request
    
    Usage:
        @router.get("/customers/{id}")
        async def get_customer(db: AsyncSession = Depends(get_db)):
            customer = await db.execute(select(Customer).where(...))
            return customer
    """
    async with AsyncSessionLocal() as session:
        try:
            # Ensure foreign keys are enabled for this session
            await session.execute(text("PRAGMA foreign_keys=ON"))
            yield session
        except Exception as exc:
            logger.error(f"Database error: {exc}")
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------

async def init_db(drop_first: bool = False) -> None:
    """
    Initialize the database - creates all tables and applies optimizations.
    
    Args:
        drop_first: If True, drops all existing tables before creating.
                   Useful for testing and development.
    
    This function:
    1. Imports all models (to register them with Base)
    2. Optionally drops existing tables
    3. Creates all tables
    4. Applies SQLite PRAGMA settings
    5. Logs the database location
    """
    # Import models to register them with Base (avoids circular imports)
    from databaseinit import models  # noqa: F401
    
    async with engine.begin() as conn:
        # Drop all tables if requested (for testing)
        if drop_first:
            logger.warning("Dropping all tables (drop_first=True)")
            await conn.run_sync(Base.metadata.drop_all)
        
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
        
        # Log what was created
        table_count = len(Base.metadata.tables)
        logger.info(f"Database initialized with {table_count} tables at: {DB_PATH}")
        logger.info(f"Absolute path: {os.path.abspath(DB_PATH)}")


async def check_database_integrity() -> dict:
    """
    Check the database integrity using SQLite's PRAGMA integrity_check.
    
    Returns:
        dict: Results of the integrity check
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("PRAGMA integrity_check"))
        rows = result.fetchall()
        
        is_ok = all(row[0] == "ok" for row in rows)
        return {
            "ok": is_ok,
            "details": [row[0] for row in rows],
            "database": str(DB_PATH),
        }


async def get_database_info() -> dict:
    """
    Get information about the database.
    
    Returns:
        dict: Database information including size, tables, etc.
    """
    db_path = Path(DB_PATH)
    
    async with AsyncSessionLocal() as session:
        # Get table count
        result = await session.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        )
        table_count = result.scalar()
        
        # Get WAL mode status
        result = await session.execute(text("PRAGMA journal_mode"))
        journal_mode = result.scalar()
        
        # Get foreign keys status
        result = await session.execute(text("PRAGMA foreign_keys"))
        foreign_keys = bool(result.scalar())
        
        return {
            "path": str(db_path),
            "absolute_path": str(db_path.absolute()),
            "exists": db_path.exists(),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0,
            "table_count": table_count,
            "journal_mode": journal_mode,
            "foreign_keys": foreign_keys,
            "pool_size": DB_POOL_SIZE,
            "max_overflow": DB_MAX_OVERFLOW,
            "pool_timeout": DB_POOL_TIMEOUT,
            "pool_recycle": DB_POOL_RECYCLE,
        }


async def vacuum_database() -> None:
    """
    Run VACUUM on the database to reclaim space and optimize.
    
    VACUUM rebuilds the database file, removing fragmentation
    and freeing unused space. This can take some time on large
    databases.
    
    Note: VACUUM requires exclusive access to the database.
    """
    logger.info(f"Starting VACUUM on database: {DB_PATH}")
    async with AsyncSessionLocal() as session:
        await session.execute(text("VACUUM"))
        await session.commit()
    logger.info(f"VACUUM completed on database: {DB_PATH}")


async def dispose_engine() -> None:
    """
    Cleanly close the connection pool on application shutdown.
    
    This should be called in the FastAPI lifespan shutdown phase.
    """
    logger.info("Disposing database connection pool...")
    await engine.dispose()
    logger.info("Database connection pool disposed successfully")


# ---------------------------------------------------------------------------
# Export Public API
# ---------------------------------------------------------------------------

__all__ = [
    "Base",
    "AsyncSessionLocal",
    "engine",
    "get_db",
    "init_db",
    "dispose_engine",
    "check_database_integrity",
    "get_database_info",
    "vacuum_database",
    "DB_PATH",
]

# ---------------------------------------------------------------------------
# Module Initialization Log
# ---------------------------------------------------------------------------

logger.debug(
    f"Database module initialized with:\n"
    f"  Path: {os.path.abspath(DB_PATH)}\n"
    f"  Pool Size: {DB_POOL_SIZE}\n"
    f"  Max Overflow: {DB_MAX_OVERFLOW}\n"
    f"  Pool Timeout: {DB_POOL_TIMEOUT}s\n"
    f"  Pool Recycle: {DB_POOL_RECYCLE}s\n"
    f"  Journal Mode: {SQLITE_JOURNAL_MODE}\n"
    f"  Synchronous: {SQLITE_SYNCHRONOUS}\n"
    f"  Cache Size: {SQLITE_CACHE_SIZE}\n"
    f"  Temp Store: {SQLITE_TEMP_STORE}\n"
    f"  Echo SQL: {ECHO_SQL}"
)