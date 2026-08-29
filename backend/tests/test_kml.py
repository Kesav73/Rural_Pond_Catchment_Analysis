"""Parser tests (Tasks_Phase2.md 2.2.4).

Split deliberately:
  - `TestSample`     asserts against the provided file's measured values. Sample-specific numbers
                     are legitimate *here* and nowhere in `app/services/`.
  - everything else  uses synthetic files that differ from the sample in every dimension the parser
                     could have been accidentally tuned to. These are what demonstrate generality.
"""

from __future__ import annotations

import os

import pytest

from app.services import kml
from tests import fixtures

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "contours_1m.kml")


@pytest.fixture(scope="module")
def sample_bytes():
    if not os.path.exists(SAMPLE):
        pytest.skip("sample contour map not present")
    with open(SAMPLE, "rb") as handle:
        return handle.read()


class TestSample:
    """Measured properties of the provided map. Test-only values by design."""

    def test_counts_and_levels(self, sample_bytes):
        result = kml.parse_contours(sample_bytes)
        assert len(result.lines) == 1355
        assert len(result.levels) == 32
        assert (min(result.levels), max(result.levels)) == (267.0, 298.0)
        assert result.contour_interval() == 1.0

    def test_elevation_comes_from_placemark_name(self, sample_bytes):
        assert kml.parse_contours(sample_bytes).elevation_source == "placemark_name"

    def test_label_points_are_excluded(self, sample_bytes):
        """The file holds 160,473 vertices; 1,355 belong to label Points and 5 to the boundary."""
        result = kml.parse_contours(sample_bytes)
        assert result.vertex_count == 160_473 - 1355 - 5

    def test_boundary_polygon_found(self, sample_bytes):
        result = kml.parse_contours(sample_bytes)
        assert result.boundary is not None and len(result.boundary) == 5

    def test_non_numeric_names_do_not_break_parsing(self, sample_bytes):
        """The file contains placemarks named 'land' and 'sources'."""
        result = kml.parse_contours(sample_bytes)
        assert result.skipped_no_elevation == 0
        assert result.skipped_degenerate == 0


class TestGeneralisation:
    """Layouts the sample never exercises — a parser written for it would fail these."""

    @pytest.mark.parametrize("where", ["name", "z", "extended", "description"])
    def test_elevation_read_from_every_supported_location(self, where):
        contours = [
            (10.0, fixtures.ring(77.5, 12.9, 0.004)),
            (20.0, fixtures.ring(77.5, 12.9, 0.003)),
            (30.0, fixtures.ring(77.5, 12.9, 0.002)),
        ]
        result = kml.parse_contours(fixtures.build_kml(contours, elevation_in=where))
        assert result.levels == [10.0, 20.0, 30.0]
        assert len(result.lines) == 3

    def test_parses_without_a_namespace(self):
        contours = [(5.0, fixtures.ring(0.5, 0.5, 0.003)), (10.0, fixtures.ring(0.5, 0.5, 0.002))]
        result = kml.parse_contours(fixtures.build_kml(contours, namespace=False))
        assert result.levels == [5.0, 10.0]

    @pytest.mark.parametrize("root", ["kml", "Document", "Folder"])
    def test_parses_any_document_root(self, root):
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        result = kml.parse_contours(fixtures.build_kml(contours, root_tag=root))
        assert len(result.lines) == 2

    def test_parses_kmz_archive(self):
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        result = kml.parse_contours(fixtures.build_kmz(contours))
        assert result.levels == [1.0, 2.0]

    def test_boundary_absent_is_not_an_error(self):
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        result = kml.parse_contours(fixtures.build_kml(contours, boundary=None))
        assert result.boundary is None

    def test_largest_polygon_wins_regardless_of_name(self):
        """Boundary is chosen by area, never by the sample's name 'land'."""
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        small = [(10, 10), (10.001, 10), (10.001, 10.001), (10, 10.001), (10, 10)]
        big = [(9, 9), (11, 9), (11, 11), (9, 11), (9, 9)]
        doc = fixtures.build_kml(contours, boundary=small).decode()
        extra = (
            "<Placemark><name>anything</name><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            + " ".join(f"{a},{b}" for a, b in big)
            + "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
        doc = doc.replace("</Folder>", extra + "</Folder>")
        result = kml.parse_contours(doc.encode())
        assert result.boundary is not None
        assert max(p[0] for p in result.boundary) == 11  # picked the big one

    def test_point_placemarks_are_ignored_by_geometry_type(self):
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        labels = [(1.0, (10.0, 10.0)), (2.0, (10.001, 10.001))]
        result = kml.parse_contours(fixtures.build_kml(contours, extra_points=labels))
        assert len(result.lines) == 2

    def test_zero_filled_z_column_is_not_mistaken_for_elevation(self):
        """`lon,lat,0` filler must not flatten the map — the name should win instead."""
        contours = [(7.0, fixtures.ring(10, 10, 0.003)), (9.0, fixtures.ring(10, 10, 0.002))]
        doc = fixtures.build_kml(contours, elevation_in="name").decode()
        doc = doc.replace(",10.0 ", ",10.0,0 ")  # sprinkle zero Z values
        result = kml.parse_contours(doc.encode())
        assert result.levels == [7.0, 9.0]

    @pytest.mark.parametrize("interval,base", [(0.5, 3.0), (5.0, 100.0), (2.0, -10.0)])
    def test_interval_is_measured_not_assumed(self, interval, base):
        """Including a range crossing zero — nothing may assume the sample's 1 m / 267-298 m."""
        contours = [
            (base + i * interval, fixtures.ring(-58.4, -34.6, 0.004 - 0.001 * i)) for i in range(3)
        ]
        result = kml.parse_contours(fixtures.build_kml(contours))
        assert result.contour_interval() == pytest.approx(interval)
        assert min(result.levels) == pytest.approx(base)

    def test_southern_hemisphere_negative_coordinates(self):
        result = kml.parse_contours(fixtures.build_kml(fixtures.gaussian_bowl_contours()))
        min_lon, min_lat, max_lon, max_lat = result.bbox()
        assert max_lon < 0 and max_lat < 0


class TestRejects:
    """Bad input must raise KMLParseError (mapped to 4xx), never crash or return nonsense."""

    @pytest.mark.parametrize(
        "data,reason",
        [
            (b"", "empty file"),
            (b"<kml><unclosed>", "malformed XML"),
            (b"<kml><Document></Document></kml>", "no placemarks"),
            (b"PK\x03\x04garbage", "corrupt zip"),
        ],
    )
    def test_rejects_unusable_input(self, data, reason):
        with pytest.raises(kml.KMLParseError):
            kml.parse_contours(data)

    def test_rejects_points_only(self):
        doc = fixtures.build_kml([], extra_points=[(1.0, (10.0, 10.0))])
        with pytest.raises(kml.KMLParseError, match="no LineString"):
            kml.parse_contours(doc)

    def test_rejects_single_elevation_level(self):
        """One level means no relief — analysing it would produce a meaningless flat surface."""
        contours = [(5.0, fixtures.ring(10, 10, 0.003)), (5.0, fixtures.ring(10, 10, 0.002))]
        with pytest.raises(kml.KMLParseError, match="one elevation level"):
            kml.parse_contours(fixtures.build_kml(contours))

    def test_rejects_when_no_elevation_resolvable(self):
        contours = [(1.0, fixtures.ring(10, 10, 0.003)), (2.0, fixtures.ring(10, 10, 0.002))]
        doc = fixtures.build_kml(contours).decode().replace("<name>1.0</name>", "<name>a</name>")
        doc = doc.replace("<name>2.0</name>", "<name>b</name>")
        with pytest.raises(kml.KMLParseError):
            kml.parse_contours(doc.encode())
