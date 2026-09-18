"""Phase 3 backend additions (Tasks_Phase3.md 3.1 / 3.3.6): expected water volume and contour lines.

All synthetic and offline — no network, no database — so they run under `pytest -m "not slow"`.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services import contours, pond_sizing, terrain
from app.services.gridref import AffineGridRef


class TestExpectedVolume:
    def test_runoff_limited_site_collects_all_the_runoff(self):
        assert pond_sizing.expected_volume_m3(capacity_m3=1000.0, runoff_m3=400.0) == 400.0

    def test_fill_limited_site_collects_only_its_capacity(self):
        assert pond_sizing.expected_volume_m3(capacity_m3=1000.0, runoff_m3=5000.0) == 1000.0

    def test_zero_capacity_collects_nothing(self):
        assert pond_sizing.expected_volume_m3(capacity_m3=0.0, runoff_m3=5000.0) == 0.0


def _zone(candidate_id: int, area_ha: float, catchment_area_m2: float, compactness: float = 0.8):
    return {
        "candidate_id": candidate_id,
        "area_ha": area_ha,
        "mean_depth_m": 2.0,
        "compactness": compactness,
        "catchment_area_m2": catchment_area_m2,
    }


class TestRankingUsesExpectedVolume:
    @pytest.fixture
    def ranked(self):
        zones = [
            _zone(1, area_ha=0.1, catchment_area_m2=10_000.0),  # runoff-limited
            _zone(2, area_ha=0.1, catchment_area_m2=5_000_000.0),  # fill-limited
            _zone(3, area_ha=0.5, catchment_area_m2=200_000.0),
            _zone(4, area_ha=0.3, catchment_area_m2=900_000.0, compactness=0.2),  # excluded
        ]
        return terrain.score_and_rank_by_water(zones, rainfall_mm=150.0)

    def test_every_zone_carries_the_field(self, ranked):
        for zone in ranked:
            assert zone["expected_volume_m3"] == pytest.approx(
                min(zone["capacity_m3"], zone["runoff_m3"])
            )

    def test_sufficiency_score_is_the_expected_volume(self, ranked):
        for zone in ranked:
            assert zone["score"] == zone["expected_volume_m3"]

    def test_eligible_sites_are_ranked_by_expected_volume(self, ranked):
        eligible = [z for z in ranked if not z["excluded"]]
        volumes = [z["expected_volume_m3"] for z in eligible]
        assert volumes == sorted(volumes, reverse=True)
        assert [z["rank"] for z in eligible] == list(range(1, len(eligible) + 1))


class TestContourLines:
    # A cone: elevation falls off linearly from a central peak, so every level is one closed ring
    # around the centre.
    SHAPE = (120, 120)
    BBOX = (81.60, 21.20, 81.62, 21.22)

    @pytest.fixture
    def cone(self):
        rows, cols = np.mgrid[0 : self.SHAPE[0], 0 : self.SHAPE[1]]
        distance = np.hypot(rows - 60, cols - 60)
        return 300.0 - distance * 0.5  # 300 m peak, 270 m at the corners' inscribed circle

    @pytest.fixture
    def gridref(self):
        return AffineGridRef(*self.BBOX, shape=self.SHAPE)

    def test_one_feature_per_level_at_the_interval(self, cone, gridref):
        result = contours.extract_contour_lines(cone, gridref, interval=5.0)
        levels = [f["properties"]["elevation"] for f in result["features"]]
        assert levels == sorted(levels)
        assert all(level % 5.0 == 0 for level in levels)
        assert min(levels) >= cone.min() and max(levels) <= cone.max()
        assert result["interval"] == 5.0

    def test_major_lines_every_fifth_interval(self, cone, gridref):
        result = contours.extract_contour_lines(cone, gridref, interval=2.0)
        for feature in result["features"]:
            level = feature["properties"]["elevation"]
            assert feature["properties"]["major"] == (level % 10.0 == 0)

    def test_lines_are_clipped_to_the_requested_bbox(self, cone, gridref):
        # Clip to the middle half; nothing may fall outside it.
        clip = (81.605, 21.205, 81.615, 21.215)
        result = contours.extract_contour_lines(cone, gridref, interval=2.0, clip_bbox=clip)
        assert result["features"]
        for feature in result["features"]:
            assert feature["geometry"]["type"] == "MultiLineString"
            for line in feature["geometry"]["coordinates"]:
                for lon, lat in line:
                    assert clip[0] - 1e-9 <= lon <= clip[2] + 1e-9
                    assert clip[1] - 1e-9 <= lat <= clip[3] + 1e-9

    def test_flat_ground_has_no_lines(self, gridref):
        flat = np.full(self.SHAPE, 250.3)
        result = contours.extract_contour_lines(flat, gridref, interval=2.0)
        assert result["features"] == []


class TestContourBandsClipping:
    SHAPE = (120, 120)
    BBOX = (81.60, 21.20, 81.62, 21.22)

    def test_bands_are_clipped_to_the_requested_bbox(self):
        rows, cols = np.mgrid[0 : self.SHAPE[0], 0 : self.SHAPE[1]]
        cone = 300.0 - np.hypot(rows - 60, cols - 60) * 0.5
        gridref = AffineGridRef(*self.BBOX, shape=self.SHAPE)
        clip = (81.605, 21.205, 81.615, 21.215)
        result = contours.extract_contour_bands(cone, gridref, interval=5.0, clip_bbox=clip)
        assert result["features"]
        for feature in result["features"]:
            geometry = feature["geometry"]
            polygons = (
                geometry["coordinates"]
                if geometry["type"] == "MultiPolygon"
                else [geometry["coordinates"]]
            )
            for polygon in polygons:
                for ring in polygon:
                    for lon, lat in ring:
                        assert clip[0] - 1e-9 <= lon <= clip[2] + 1e-9
                        assert clip[1] - 1e-9 <= lat <= clip[3] + 1e-9
