"""
One-off script: load district boundaries from LGD_Districts (India's Local Government
Directory, republished as Parquet by yashveeeeeeer/india-geodata) and insert into the
`districts` table with a computed centroid and bbox.

Switched from the original india.geojson (2011 census) source: that dataset was frozen at
27 districts for Chhattisgarh, while Chhattisgarh has since split into 33 (2020-2022
reorganizations). Using the same LGD source as seed_villages.py guarantees district_name
values match exactly between the two tables — mixing the two unrelated sources caused 13
of Chhattisgarh's district names to mismatch (7 spelling variants, 6 genuinely missing
new districts).

Requires duckdb (see scripts/requirements-scripts.txt — not a runtime app dependency):
    pip install -r scripts/requirements-scripts.txt

Usage: python -m scripts.seed_districts
"""

import asyncio
import json

import duckdb

from app.db import postgres

DISTRICTS_PARQUET_URL = (
    "https://github.com/yashveeeeeeer/india-geodata/releases/download/"
    "admin/districts/LGD_Districts.parquet"
)


def fetch_districts() -> list[tuple]:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")

    return con.execute(
        f"""
        SELECT
            stname, dtname,
            ST_AsGeoJSON(geometry) AS geometry_json,
            ST_Y(ST_Centroid(geometry)) AS centroid_lat,
            ST_X(ST_Centroid(geometry)) AS centroid_lon,
            ST_XMin(geometry) AS min_lon,
            ST_YMin(geometry) AS min_lat,
            ST_XMax(geometry) AS max_lon,
            ST_YMax(geometry) AS max_lat
        FROM read_parquet('{DISTRICTS_PARQUET_URL}')
        WHERE dtname IS NOT NULL AND geometry IS NOT NULL
        """
    ).fetchall()


async def seed() -> None:
    print("Querying LGD_Districts (remote, nationwide — 22MB, no filter needed)...")
    raw_rows = fetch_districts()
    print(f"Fetched {len(raw_rows)} districts")

    await postgres.init_schema()
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"TRUNCATE {postgres.DISTRICTS} RESTART IDENTITY")

        rows = []
        for (
            stname, dtname, geometry_json, centroid_lat, centroid_lon,
            min_lon, min_lat, max_lon, max_lat,
        ) in raw_rows:
            centroid = {"lat": centroid_lat, "lon": centroid_lon}
            bbox = [min_lon, min_lat, max_lon, max_lat]
            rows.append(
                (
                    stname.title(),  # LGD's stname is ALL CAPS; normalize for display/lookup
                    dtname,
                    geometry_json,
                    json.dumps(centroid),
                    json.dumps(bbox),
                )
            )

        await conn.executemany(
            f"""
            INSERT INTO {postgres.DISTRICTS} (state_name, district_name, geometry, centroid, bbox)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb)
            """,
            rows,
        )

        count = await conn.fetchval(f"SELECT count(*) FROM {postgres.DISTRICTS}")
        state_count = await conn.fetchval(
            f"SELECT count(DISTINCT state_name) FROM {postgres.DISTRICTS}"
        )
        print(f"Inserted {count} districts across {state_count} states")

    await postgres.close_pool()


if __name__ == "__main__":
    asyncio.run(seed())
