import asyncpg

from app.core.config import settings

_pool: asyncpg.Pool | None = None

# Table name constants — kept in one place so routers/services never hardcode strings.
DISTRICTS = "districts"
TILE_CACHE = "tile_cache"
RAINFALL_CACHE = "rainfall_cache"
OVERPASS_CACHE = "overpass_cache"
PROPOSALS = "proposals"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {DISTRICTS} (
    id SERIAL PRIMARY KEY,
    state_name TEXT NOT NULL,
    district_name TEXT NOT NULL,
    geometry JSONB NOT NULL,
    centroid JSONB NOT NULL,
    bbox JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_districts_state ON {DISTRICTS} (state_name);

CREATE TABLE IF NOT EXISTS {TILE_CACHE} (
    cache_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {RAINFALL_CACHE} (
    cache_key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {OVERPASS_CACHE} (
    cache_key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {PROPOSALS} (
    id SERIAL PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
                "and fill in your Neon/Postgres connection string."
            )
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    return _pool


async def init_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def ping() -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    return result == 1


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
