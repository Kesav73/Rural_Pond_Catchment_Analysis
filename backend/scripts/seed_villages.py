"""
One-off script: pull village-level boundaries for a given state (default: Chhattisgarh)
from the LGD_Villages dataset and insert into the `villages` table.

Source: India's Local Government Directory (LGD), republished as Parquet by
yashveeeeeeer/india-geodata on GitHub. Queried remotely via duckdb's httpfs — only the
matching rows are fetched over HTTP range requests, not the full ~474MB nationwide file.

Requires duckdb (see scripts/requirements-scripts.txt — not a runtime app dependency):
    pip install -r scripts/requirements-scripts.txt

Usage: python -m scripts.seed_villages [STATE_NAME]
"""

import asyncio
import json
import sys

import duckdb

from app.db import postgres

VILLAGES_PARQUET_URL = (
    "https://github.com/yashveeeeeeer/india-geodata/releases/download/"
    "admin/villages/LGD_Villages.parquet"
)


def fetch_villages(state_name: str) -> list[tuple]:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")

    rows = con.execute(
        f"""
        SELECT
            dtname, sdtname, block_name, gp_name, vilname11, vil_lgd,
            ST_AsGeoJSON(geometry) AS geometry_json,
            ST_Y(ST_Centroid(geometry)) AS centroid_lat,
            ST_X(ST_Centroid(geometry)) AS centroid_lon,
            ST_XMin(geometry) AS min_lon,
            ST_YMin(geometry) AS min_lat,
            ST_XMax(geometry) AS max_lon,
            ST_YMax(geometry) AS max_lat
        FROM read_parquet('{VILLAGES_PARQUET_URL}')
        WHERE stname = ? AND vilname11 IS NOT NULL AND trim(vilname11) != ''
            AND geometry IS NOT NULL
        """,
        [state_name],
    ).fetchall()
    return rows


async def seed(state_name: str) -> None:
    print(f"Querying LGD_Villages for '{state_name}' (remote, filtered)...")
    raw_rows = fetch_villages(state_name)
    print(f"Fetched {len(raw_rows)} villages")

    # LGD's stname is ALL CAPS ("CHHATTISGARH"); the districts table (seeded from a
    # different source) uses title case ("Chhattisgarh"). Normalize so district/village
    # lookups don't silently break on casing.
    state_name_display = state_name.title()

    await postgres.init_schema()
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM {postgres.VILLAGES} WHERE state_name = $1", state_name_display
        )

        rows = []
        for (
            dtname, sdtname, block_name, gp_name, vilname11, vil_lgd,
            geometry_json, centroid_lat, centroid_lon,
            min_lon, min_lat, max_lon, max_lat,
        ) in raw_rows:
            centroid = {"lat": centroid_lat, "lon": centroid_lon}
            bbox = [min_lon, min_lat, max_lon, max_lat]
            rows.append(
                (
                    state_name_display,
                    dtname,
                    sdtname,
                    block_name,
                    gp_name,
                    vilname11,
                    vil_lgd,
                    geometry_json,
                    json.dumps(centroid),
                    json.dumps(bbox),
                )
            )

        await conn.executemany(
            f"""
            INSERT INTO {postgres.VILLAGES}
                (state_name, district_name, subdistrict_name, block_name, gp_name,
                 village_name, vil_lgd, geometry, centroid, bbox)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb)
            """,
            rows,
        )

        count = await conn.fetchval(
            f"SELECT count(*) FROM {postgres.VILLAGES} WHERE state_name = $1", state_name_display
        )
        district_count = await conn.fetchval(
            f"SELECT count(DISTINCT district_name) FROM {postgres.VILLAGES} WHERE state_name = $1",
            state_name_display,
        )
        print(f"Inserted {count} villages across {district_count} districts in {state_name_display}")

    await postgres.close_pool()


if __name__ == "__main__":
    state = sys.argv[1] if len(sys.argv) > 1 else "CHHATTISGARH"
    asyncio.run(seed(state))
