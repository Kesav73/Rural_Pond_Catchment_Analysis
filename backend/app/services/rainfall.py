import json
from datetime import date, timedelta

import httpx

from app.db import postgres

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DEFAULT_YEARS = 10

# Open-Meteo's archive (ERA5-based) lags real time by several days; asking for "up to today"
# returns trailing nulls. Back off far enough to land on settled data.
ARCHIVE_LAG_DAYS = 10


async def fetch_rainfall(lat: float, lon: float, years: int = DEFAULT_YEARS) -> dict:
    """Historical daily precipitation for a point, reduced to the two figures sizing needs.

    - annual_mean_mm: the display/baseline figure
    - max_single_day_mm: the DESIGN STORM, which is what pond sizing must use. Sizing against
      the annual total is a recorded failure mode — it produced a 1061 m x 1061 m pond.
    """
    # Round the cache key: rainfall varies negligibly over ~1km, so neighbouring candidates in
    # the same village share one upstream call instead of one each.
    cache_key = f"rainfall:{round(lat, 2)}:{round(lon, 2)}:{years}"
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.RAINFALL_CACHE} WHERE cache_key = $1", cache_key
        )
        if row is not None:
            return json.loads(row["data"])

    end = date.today() - timedelta(days=ARCHIVE_LAG_DAYS)
    start = end.replace(year=end.year - years)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(ARCHIVE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "annual_mean_mm": None,
            "max_single_day_mm": None,
        }

    daily = payload.get("daily", {})
    dates = daily.get("time", []) or []
    values = daily.get("precipitation_sum", []) or []

    # Nulls are real in this series (missing days), so filter rather than treating them as zero,
    # which would silently drag the annual mean down.
    pairs = [(d, v) for d, v in zip(dates, values) if v is not None]
    if not pairs:
        return {
            "available": False,
            "error": "no precipitation data returned for this point",
            "annual_mean_mm": None,
            "max_single_day_mm": None,
        }

    totals_by_year: dict[str, float] = {}
    for day, value in pairs:
        totals_by_year.setdefault(day[:4], 0.0)
        totals_by_year[day[:4]] += value

    # Only whole years should feed the annual mean — the first/last year of the window are
    # partial and would understate it.
    complete_years = {
        year: total
        for year, total in totals_by_year.items()
        if sum(1 for d, _ in pairs if d[:4] == year) >= 365
    }
    used = complete_years or totals_by_year

    max_day, max_value = max(pairs, key=lambda pair: pair[1])

    result = {
        "available": True,
        "error": None,
        "annual_mean_mm": sum(used.values()) / len(used),
        "max_single_day_mm": max_value,
        "max_single_day_date": max_day,
        "years_used": sorted(used.keys()),
        "days_total": len(dates),
        "days_with_data": len(pairs),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {postgres.RAINFALL_CACHE} (cache_key, data)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            json.dumps(result),
        )
    return result
