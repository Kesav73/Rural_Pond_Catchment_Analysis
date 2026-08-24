import json

from fastapi import APIRouter, HTTPException

from app.db import postgres

router = APIRouter(prefix="/api", tags=["regions"])


@router.get("/states")
async def list_states():
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT DISTINCT state_name FROM {postgres.DISTRICTS} ORDER BY state_name"
        )
    return [{"name": row["state_name"]} for row in rows]


@router.get("/districts")
async def list_districts(state: str):
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT district_name, centroid, bbox FROM {postgres.DISTRICTS}
            WHERE state_name = $1 ORDER BY district_name
            """,
            state,
        )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No districts found for state '{state}'")
    return [
        {
            "name": row["district_name"],
            "centroid": json.loads(row["centroid"]),
            "bbox": json.loads(row["bbox"]),
        }
        for row in rows
    ]


@router.get("/villages")
async def list_villages(district: str):
    # Village-level data currently only exists for Chhattisgarh (see scripts/seed_villages.py).
    # An empty list is a normal, expected result for any other district, not an error.
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, village_name, gp_name, block_name, centroid, bbox
            FROM {postgres.VILLAGES}
            WHERE district_name = $1 ORDER BY village_name
            """,
            district,
        )
    return [
        {
            # Source village names are NOT unique within a district (1,451 duplicate
            # name+district pairs found in Chhattisgarh, and the source's own vil_lgd code
            # has 282 duplicates too) — our own row id is the only reliable unique key.
            "id": row["id"],
            "name": row["village_name"],
            "gp_name": row["gp_name"],
            "block_name": row["block_name"],
            "centroid": json.loads(row["centroid"]),
            "bbox": json.loads(row["bbox"]),
        }
        for row in rows
    ]
