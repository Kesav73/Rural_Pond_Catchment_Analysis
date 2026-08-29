"""Endpoint tests for POST /api/analyzeContour (Tasks_Phase2.md 2.4.3-2.4.6).

Split the same way as the parser tests:
  - `TestSample`         exercises the provided map; sample-specific numbers are legitimate here.
  - `TestGeneralisation` runs whole different contour maps through the same route. These are the
                         real evidence for the "code extensibility" criterion — a pipeline tuned to
                         the sample would fail them.
  - `TestErrors`         every bad input must return 4xx with a message, never a 500.

Network-dependent tests are marked `slow`: the route fetches satellite water cover and rainfall.
Run everything with `pytest`, or skip the slow ones with `pytest -m "not slow"`.
"""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests import fixtures

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "contours_1m.kml")

# A single TestClient context for the whole session. Entering the context runs the app lifespan
# once and keeps one event loop alive for every request. Without it each request gets a fresh loop
# while the asyncpg pool stays cached against the first one, so the second request onwards dies with
# "Event loop is closed" — which is exactly what happened before this was fixed.
@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


def post(client, data: bytes, filename: str = "contours.kml", **params):
    return client.post(
        "/api/analyzeContour",
        files={"file": (filename, io.BytesIO(data), "application/vnd.google-earth.kml+xml")},
        params=params,
    )


@pytest.fixture(scope="module")
def sample_bytes():
    if not os.path.exists(SAMPLE):
        pytest.skip("sample contour map not present")
    with open(SAMPLE, "rb") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def sample_response(client, sample_bytes):
    response = post(client, sample_bytes, filename="contours_1m.kml")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.slow
class TestSample:
    def test_returns_the_two_graded_essentials(self, sample_response):
        """A pond location and its catchment area — the whole requirement."""
        site = sample_response["pond_site"]
        catchment = sample_response["catchment"]
        assert site["geometry"]["type"] == "Polygon"
        assert len(site["geometry"]["coordinates"][0]) >= 4
        assert site["area_ha"] > 0
        assert catchment["area_ha"] > 0
        assert "pour_point" in catchment

    def test_pond_sits_inside_the_map_extent(self, sample_response):
        min_lon, min_lat, max_lon, max_lat = sample_response["source"]["bbox"]
        centroid = sample_response["pond_site"]["centroid"]
        assert min_lon <= centroid["lon"] <= max_lon
        assert min_lat <= centroid["lat"] <= max_lat

    def test_source_metadata_reports_what_was_parsed(self, sample_response):
        source = sample_response["source"]
        assert source["contour_lines"] == 1355
        assert source["contour_interval_m"] == 1.0
        assert source["elevation_field_used"] == "placemark_name"
        assert source["boundary_polygon_found"] is True

    def test_depth_threshold_follows_the_contour_interval(self, sample_response):
        """min_depth must be derived, not a literal inherited from the AWS pipeline."""
        source = sample_response["source"]
        assert source["min_depth_m_used"] == pytest.approx(source["contour_interval_m"])

    def test_boundary_polygon_from_the_file_was_applied(self, sample_response):
        assert sample_response["screening"]["boundary_applied"] == "boundary polygon from file"

    def test_water_screening_actually_excluded_sites(self, sample_response):
        """The sample contains a large water corridor, so the screen must reject something."""
        screening = sample_response["screening"]
        assert screening["worldcover_available"] is True
        assert screening["excluded_water"] > 0

    def test_water_screening_rejected_sites_for_the_stated_reason(self, sample_response):
        """Every water rejection must name the water source that caused it.

        The independent check — re-fetching the mask and confirming no *returned* site overlaps it —
        runs as a standalone script (`scripts/verify_water_exclusion.py`) rather than here, because
        driving an async DB-backed service from inside the TestClient's event loop closes the loop
        the app is using and breaks every subsequent test.
        """
        rejected = sample_response["screening"]["rejected"]
        water_rejections = [r for r in rejected if "water" in (r["reason"] or "")]
        assert water_rejections, "expected at least one site rejected as existing water"
        for rejection in water_rejections:
            assert "existing water body" in rejection["reason"]

    def test_assumptions_distinguish_cited_from_judgement(self, sample_response):
        assumptions = sample_response["assumptions"]
        assert assumptions["runoff_coefficient"]["cited"] is True
        assert assumptions["storage_efficiency"]["cited"] is False


@pytest.mark.slow
class TestGeneralisation:
    """Different maps entirely. A pipeline written for the sample fails these."""

    def test_finds_the_known_bowl_in_a_synthetic_map(self, client):
        """The fixture is an analytic depression, so the right answer is known in advance."""
        contours = fixtures.gaussian_bowl_contours()
        boundary = fixtures.bowl_boundary()
        response = post(client, fixtures.build_kml(contours, boundary=boundary))
        assert response.status_code == 200, response.text
        body = response.json()
        centroid = body["pond_site"]["centroid"]
        # The detected pond must land on the analytic bowl's centre.
        assert centroid["lon"] == pytest.approx(fixtures.SYNTHETIC_LON, abs=0.005)
        assert centroid["lat"] == pytest.approx(fixtures.SYNTHETIC_LAT, abs=0.005)

    def test_southern_hemisphere_negative_coordinates(self, client):
        response = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours()))
        assert response.status_code == 200, response.text
        assert response.json()["pond_site"]["centroid"]["lat"] < 0

    @pytest.mark.parametrize("where", ["name", "z", "extended", "description"])
    def test_every_elevation_storage_convention(self, client, where):
        contours = fixtures.gaussian_bowl_contours()
        response = post(client, fixtures.build_kml(contours, elevation_in=where))
        assert response.status_code == 200, response.text
        assert response.json()["source"]["elevation_field_used"] is not None

    def test_kmz_upload(self, client):
        response = post(client, fixtures.build_kmz(fixtures.gaussian_bowl_contours()), filename="c.kmz")
        assert response.status_code == 200, response.text

    def test_interval_drives_the_depth_threshold(self, client):
        """A 2 m-interval map must use a 2 m threshold — not the sample's 1 m."""
        contours = fixtures.gaussian_bowl_contours(interval=2.0)
        response = post(client, fixtures.build_kml(contours))
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["contour_interval_m"] == pytest.approx(2.0)
        assert source["min_depth_m_used"] == pytest.approx(2.0)

    def test_works_without_a_boundary_polygon(self, client):
        response = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours(), boundary=None))
        assert response.status_code == 200, response.text
        assert response.json()["screening"]["boundary_applied"] == "none"

    def test_resolution_is_derived_from_contour_spacing(self, client):
        """Two maps of different physical size must get different grid resolutions."""
        small = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours(extent_deg=0.01)))
        large = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours(extent_deg=0.05)))
        assert small.status_code == 200 and large.status_code == 200
        assert (
            small.json()["source"]["grid_resolution_m"]
            < large.json()["source"]["grid_resolution_m"]
        )

    def test_explicit_resolution_override_is_honoured(self, client):
        response = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours()), resolution_m=8.0)
        assert response.status_code == 200, response.text
        assert response.json()["source"]["grid_resolution_m"] == pytest.approx(8.0, rel=0.15)


class TestErrors:
    """Bad input returns 4xx with a message. A 500 here would be a bug."""

    @pytest.mark.parametrize(
        "data,filename",
        [
            (b"", "empty.kml"),
            (b"<kml><unclosed>", "broken.kml"),
            (b"<kml><Document></Document></kml>", "noplacemarks.kml"),
            (b"PK\x03\x04not-a-zip", "corrupt.kmz"),
            (b"just some text", "notkml.txt"),
        ],
    )
    def test_unusable_input_returns_4xx(self, client, data, filename):
        response = post(client, data, filename=filename)
        assert 400 <= response.status_code < 500, response.status_code
        assert "detail" in response.json()

    def test_single_elevation_level_rejected(self, client):
        contours = [(5.0, fixtures.ring(10, 10, 0.003)), (5.0, fixtures.ring(10, 10, 0.002))]
        response = post(client, fixtures.build_kml(contours))
        assert response.status_code == 400
        assert "one elevation level" in response.json()["detail"]

    def test_points_only_rejected(self, client):
        response = post(client, fixtures.build_kml([], extra_points=[(1.0, (10.0, 10.0))]))
        assert response.status_code == 400

    def test_negative_min_depth_rejected(self, client):
        response = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours()), min_depth=-1)
        assert response.status_code == 400

    @pytest.mark.slow
    def test_impossible_min_depth_returns_422_not_500(self, client):
        """Nothing that deep exists — an honest 'no site found', not a crash."""
        response = post(client, fixtures.build_kml(fixtures.gaussian_bowl_contours()), min_depth=10_000)
        assert response.status_code == 422
        assert "no depression" in response.json()["detail"]
