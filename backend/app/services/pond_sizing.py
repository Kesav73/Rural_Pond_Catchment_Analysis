"""Pond sizing — Rational Method runoff, capacity, and capture (FR6 + FR7, Tasks.md Phase 6).

Restructured by the 2026-08-29 plan change: `runoff_volume_m3` is no longer a display-only figure
computed after the top-N were chosen. It is the number the ranking is *based on* ("how much water
actually drains here"), so it runs for every surviving candidate, before selection.
"""

# --- Runoff coefficient -------------------------------------------------------------------------
#
# CITED (Tasks.md 6.1 / 9.3), replacing the previous uncited C ~= 0.30 placeholder.
#
# Barlow's Tables — Barlow, first Chief Engineer of the Hydro-Electric Survey of India (1915),
# derived from small catchments (~130 km2) in Uttar Pradesh; the standard Indian runoff-coefficient
# reference, reproduced in K. Subramanya, "Engineering Hydrology". Values are percentages, indexed
# by catchment class and monsoon season:
#
#   Class  Catchment description                        Season I  Season II  Season III
#   A      Flat, cultivated, absorbent soil                  7%       10%        15%
#   B      Flat, partly cultivated, stiff soil              12%       15%        18%
#   C      Average catchment                                16%       20%        32%
#   D      Hills and plains with little cultivation         28%       35%        60%
#   E      Very hilly, steep, no cultivation                36%       45%        81%
#
#   Season I:   light rain, no heavy downpour
#   Season II:  average/varying rainfall, no continuous downpour
#   Season III: continuous downpours
#
# This project's default is **Class B, Season III = 0.18**:
#   - Class B ("flat, partly cultivated, stiff soil") matches rural Chhattisgarh — the target
#     region is agricultural plains, and WorldCover on the Bhilai test bbox measured 39.7%
#     cropland as the dominant class.
#   - Season III because the design storm is the **maximum single-day rainfall** (6.2), i.e. a
#     continuous-downpour event, not an average day.
BARLOW_RUNOFF_COEFFICIENTS = {
    "A": {"description": "flat, cultivated, absorbent soil", "I": 0.07, "II": 0.10, "III": 0.15},
    "B": {"description": "flat, partly cultivated, stiff soil", "I": 0.12, "II": 0.15, "III": 0.18},
    "C": {"description": "average catchment", "I": 0.16, "II": 0.20, "III": 0.32},
    "D": {"description": "hills and plains, little cultivation", "I": 0.28, "II": 0.35, "III": 0.60},
    "E": {"description": "very hilly, steep, no cultivation", "I": 0.36, "II": 0.45, "III": 0.81},
}

DEFAULT_CATCHMENT_CLASS = "B"
DEFAULT_SEASON = "III"
RUNOFF_COEFFICIENT = BARLOW_RUNOFF_COEFFICIENTS[DEFAULT_CATCHMENT_CLASS][DEFAULT_SEASON]

RUNOFF_COEFFICIENT_SOURCE = (
    "Barlow's Tables (Hydro-Electric Survey of India, 1915; reproduced in Subramanya, "
    "'Engineering Hydrology') — class B 'flat, partly cultivated, stiff soil', season III "
    "'continuous downpours'"
)

# --- Pond depth ---------------------------------------------------------------------------------
#
# CITED (Tasks.md 6.1 / 9.3). Indian farm-pond practice under MGNREGA uses square ponds of 15x15 m,
# 20x20 m or 22x22 m (top dimensions) at a depth of 3 m with 1.5:1 side slopes. Depth is the stable
# figure across those variants, so it is what this project fixes; the *area* is not assumed, it
# comes from the detected depression polygon (a deliberate correction from the project's first
# failed exploration pass, which tried to solve for pond dimensions and produced a 1061 m pond).
POND_DEPTH_M = 3.0
POND_DEPTH_SOURCE = (
    "MGNREGA farm-pond practice — standard 15x15/20x20/22x22 m ponds at 3 m depth, 1.5:1 slopes"
)

# Effective-storage factor. A real excavated pond is trapezoidal (1.5:1 side slopes), not a prism,
# so area x depth overstates true capacity. Kept explicit and conservative rather than hidden.
# NOTE: this one is judgement, not a citation — flagged as such in the API response.
STORAGE_EFFICIENCY = 0.70


def runoff_volume_m3(
    catchment_area_m2: float,
    rainfall_mm: float,
    runoff_coefficient: float = RUNOFF_COEFFICIENT,
) -> float:
    """Rational Method: V = A x P x C.

    `rainfall_mm` must be the **maximum single-day** rainfall, not an annual total. Using the
    annual basis is a recorded failure mode of this project: it sized a pond at 1061 m x 1061 m.
    """
    if catchment_area_m2 <= 0 or rainfall_mm <= 0:
        return 0.0
    return catchment_area_m2 * (rainfall_mm / 1000.0) * runoff_coefficient


def pond_capacity_m3(pond_area_m2: float, depth_m: float = POND_DEPTH_M) -> float:
    """Storage the pond can actually hold, derated for trapezoidal side slopes."""
    if pond_area_m2 <= 0 or depth_m <= 0:
        return 0.0
    return pond_area_m2 * depth_m * STORAGE_EFFICIENCY


def capture_fraction(capacity_m3: float, runoff_m3: float) -> float:
    """Share of one design storm's runoff the pond can retain. 1.0 means it fills and overflows."""
    if runoff_m3 <= 0:
        return 0.0
    return min(1.0, capacity_m3 / runoff_m3)


def fill_ratio(runoff_m3: float, capacity_m3: float) -> float:
    """How many times over one design storm fills the pond. <1 means it cannot fill."""
    if capacity_m3 <= 0:
        return 0.0
    return runoff_m3 / capacity_m3


def constants_provenance() -> dict:
    """What is cited vs. what is judgement — surfaced in API responses so a placeholder is never
    presented as authoritative (Tasks.md 6.1 / 9.3)."""
    return {
        "runoff_coefficient": {
            "value": RUNOFF_COEFFICIENT,
            "cited": True,
            "source": RUNOFF_COEFFICIENT_SOURCE,
            "catchment_class": DEFAULT_CATCHMENT_CLASS,
            "season": DEFAULT_SEASON,
        },
        "pond_depth_m": {"value": POND_DEPTH_M, "cited": True, "source": POND_DEPTH_SOURCE},
        "storage_efficiency": {
            "value": STORAGE_EFFICIENCY,
            "cited": False,
            "source": "engineering judgement for 1.5:1 trapezoidal side slopes — not a citation",
        },
    }
