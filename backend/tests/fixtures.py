"""Synthetic contour-map builders for the parser and pipeline tests (Tasks_Phase2.md 2.2.4/2.4.6).

These exist to prove the implementation is not written for the provided sample. Every one of them
differs from it in ways that would break a parser tuned to that file: a different elevation store,
a different contour interval and range, no namespace, no boundary polygon, a zipped container, and
a different hemisphere.

They also give the pipeline a surface whose correct answer is known in advance, which the sample
cannot: `gaussian_bowl_contours` builds a single analytic depression at a known centre, so a test
can assert the pipeline actually finds *that* spot rather than merely returning something.
"""

from __future__ import annotations

import io
import math
import zipfile

KML_NS = "http://www.opengis.net/kml/2.2"

# Where the synthetic maps are placed on Earth. This matters more than it looks: the pipeline runs a
# real satellite water check on whatever coordinates it is given, so a fixture sitting on water gets
# legitimately rejected and the test fails for the wrong reason. The first attempt used
# (-58.4, -34.6) — the Rio de la Plata estuary at Buenos Aires — which WorldCover reports as 28.45%
# water, so every synthetic site was correctly excluded as an existing water body.
#
# The Atacama is chosen because it is measured at 0.00% water while still being southern-hemisphere
# with a negative longitude, which is what the generalisation tests need (the sample is northern,
# positive-longitude Chhattisgarh). Measured alternatives: central Australia and the Kalahari are
# also 0.00%; inland Cordoba is 5.56%, which is too close for comfort.
SYNTHETIC_LON = -69.00
SYNTHETIC_LAT = -24.00

# Total elevation fall across the synthetic map, in metres. Held constant so the tilt scales with
# the map's extent rather than being fixed in metres-per-degree.
TOTAL_FALL_M = 8.0


def _coord_text(points, z=None):
    if z is None:
        return " ".join(f"{lon},{lat}" for lon, lat in points)
    return " ".join(f"{lon},{lat},{z}" for lon, lat in points)


def build_kml(
    contours,
    elevation_in="name",
    namespace=True,
    boundary=None,
    root_tag="Folder",
    extra_points=(),
):
    """Assemble a KML document from `contours` = [(elevation, [(lon,lat), ...]), ...].

    `elevation_in` selects where the elevation is recorded — "name", "z", "extended", or
    "description" — which is exactly the axis the parser must not assume.
    """
    ns = f' xmlns="{KML_NS}"' if namespace else ""
    parts = [f"<{root_tag}{ns}>", "<name>synthetic</name>"]

    for elevation, points in contours:
        name = f"<name>{elevation}</name>" if elevation_in == "name" else "<name>contour</name>"
        extended = ""
        if elevation_in == "extended":
            extended = (
                "<ExtendedData><SchemaData>"
                f'<SimpleData name="elevation">{elevation}</SimpleData>'
                "</SchemaData></ExtendedData>"
            )
        description = (
            f"<description>Contour at {elevation} m</description>"
            if elevation_in == "description"
            else ""
        )
        coords = _coord_text(points, z=elevation if elevation_in == "z" else None)
        parts.append(
            f"<Placemark>{name}{description}{extended}"
            f"<LineString><coordinates>{coords}</coordinates></LineString></Placemark>"
        )

    # Label points: the sample carries one per contour. They must be ignored by geometry type.
    for elevation, (lon, lat) in extra_points:
        parts.append(
            f"<Placemark><name>{elevation}</name>"
            f"<Point><coordinates>{lon},{lat}</coordinates></Point></Placemark>"
        )

    if boundary:
        parts.append(
            "<Placemark><name>boundary</name><Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{_coord_text(boundary)}</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )

    parts.append(f"</{root_tag}>")
    return "".join(parts).encode("utf-8")


def build_kmz(*args, **kwargs) -> bytes:
    """Same document, wrapped in a zip — which is all a KMZ is."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc.kml", build_kml(*args, **kwargs))
    return buffer.getvalue()


def ring(center_lon, center_lat, radius_deg, n=48):
    """A closed circle of lon/lat points."""
    return [
        (
            center_lon + radius_deg * math.cos(2 * math.pi * i / n),
            center_lat + radius_deg * math.sin(2 * math.pi * i / n),
        )
        for i in range(n + 1)
    ]


def gaussian_bowl_contours(
    center_lon=SYNTHETIC_LON,
    center_lat=SYNTHETIC_LAT,
    base=40.0,
    depth=14.0,
    interval=2.0,
    extent_deg=0.02,
    slope=None,
):
    """Contours of a known analytic surface: one bowl set into a tilted plane.

    Surface: `base + slope * (lat - center_lat) - depth * exp(-(r/sigma)^2)`

    **What this fixture is and is not for.** It pins down *pond location*: the analytic bowl centre is
    known exactly, so a test can assert the pipeline finds that spot (measured: within 0.0012 deg).
    It is **not** a good test of catchment delineation, and no test asserts catchment on it. A
    perfectly smooth bowl fills to a perfectly flat floor, and D8 cannot route across a flat surface
    — every cell picks the same arbitrary neighbour — so flow accumulation inside the pond is
    meaningless and self-overlap comes out near 4%. That is the documented flat-terrain weakness in
    its most pathological form, not a pipeline defect: the real sample yields 82% self-overlap and a
    43x catchment ratio. Catchment correctness is verified there instead.

    The tilt still matters for realism, and so does its size relative to the bowl. A bare Gaussian
    bowl fills the whole map, leaving no upslope terrain at all. A tilt that is too gentle has a
    similar problem — measured with depth=12 m against a 5 m fall, the pond covered
    115 ha and its catchment only 44 ha.

    Sizing the bowl against the tilt takes some care, because two effects eat into the depression:
      1. On a slope, the pond spills at its lowest rim point, so the *closed* depth is roughly
         `depth - slope * sigma` rather than `depth` — here 14 - 400*0.0067 ~= 11.3 m.
      2. TIN interpolation flattens the innermost contour's interior, costing up to one further
         interval (2 m).
    A 5 m bowl on this tilt left ~2.3 m closed depth, which fell below the derived 2 m threshold and
    produced "no depression found". 14 m leaves ~9 m of measurable depression with plenty of upslope
    ground draining into it.

    The bowl must also be steep enough to close on the slope at all: its maximum gradient is roughly
    `depth / sigma`, which has to exceed `slope`. Here that is 2100 vs 400.

    Contours are traced by marching squares over the analytic surface rather than solved in closed
    form, because the tilt breaks the circular symmetry that made a closed form possible.

    Defaults sit in the southern hemisphere at a negative longitude with a 2 m interval over a 40 m
    base — deliberately unlike the sample's 1 m interval over 267-298 m in Chhattisgarh. See
    SYNTHETIC_LON/LAT for why the exact spot is not arbitrary.
    """
    import numpy as np

    # The tilt must scale with the extent, or the fixture stops being the same shape at different
    # sizes. The bowl's width sigma is proportional to extent_deg, so a fixed slope means the fall
    # across the bowl (slope * sigma) grows with extent and eats into the closed depression. Measured
    # with a fixed slope=400: at extent 0.02 the closed depression was ~11 m deep, but at extent 0.05
    # only ~3.7 m — and after TIN flattening cost one 2 m interval it fell under the derived depth
    # threshold entirely, so the endpoint correctly answered "no depression found". Holding the total
    # fall constant keeps the geometry similar at every scale.
    if slope is None:
        slope = TOTAL_FALL_M / extent_deg

    n = 220
    sigma = extent_deg / 3.0
    lons = np.linspace(center_lon - extent_deg, center_lon + extent_deg, n)
    lats = np.linspace(center_lat - extent_deg, center_lat + extent_deg, n)
    lon_mesh, lat_mesh = np.meshgrid(lons, lats)
    r2 = (lon_mesh - center_lon) ** 2 + (lat_mesh - center_lat) ** 2
    surface = base + slope * (lat_mesh - center_lat) - depth * np.exp(-r2 / sigma**2)

    lo = math.ceil(surface.min() / interval) * interval
    hi = math.floor(surface.max() / interval) * interval

    contours = []
    level = lo
    while level <= hi + 1e-9:
        for path in _marching_squares(surface, lons, lats, level):
            if len(path) >= 2:
                contours.append((round(level, 3), path))
        level += interval
    return contours


def _marching_squares(surface, lons, lats, level):
    """Trace `level` through the grid as unordered short segments.

    Segments are enough: the parser and the interpolator both consume vertices, and neither cares
    whether a contour arrives as one long ordered path or many small pieces.
    """
    paths = []
    rows, cols = surface.shape
    for i in range(rows - 1):
        for j in range(cols - 1):
            cell = (surface[i, j], surface[i, j + 1], surface[i + 1, j + 1], surface[i + 1, j])
            if min(cell) > level or max(cell) < level:
                continue
            corners = (
                (lons[j], lats[i]),
                (lons[j + 1], lats[i]),
                (lons[j + 1], lats[i + 1]),
                (lons[j], lats[i + 1]),
            )
            crossings = []
            for k in range(4):
                a, b = cell[k], cell[(k + 1) % 4]
                if (a - level) * (b - level) < 0:
                    t = (level - a) / (b - a)
                    (x1, y1), (x2, y2) = corners[k], corners[(k + 1) % 4]
                    crossings.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
            if len(crossings) >= 2:
                paths.append([crossings[0], crossings[1]])
    return paths


def bowl_boundary(center_lon=SYNTHETIC_LON, center_lat=SYNTHETIC_LAT, extent_deg=0.02):
    half = extent_deg
    return [
        (center_lon - half, center_lat - half),
        (center_lon + half, center_lat - half),
        (center_lon + half, center_lat + half),
        (center_lon - half, center_lat + half),
        (center_lon - half, center_lat - half),
    ]
