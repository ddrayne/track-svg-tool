from __future__ import annotations

from typing import Iterable

from pyproj import CRS, Transformer


def _utm_crs_for_lon_lat(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    is_north = lat >= 0
    return CRS.from_dict({"proj": "utm", "zone": zone, "south": not is_north})


def project_latlon_to_utm(points: Iterable[tuple[float, float]]) -> tuple[list[tuple[float, float]], CRS]:
    pts = list(points)
    if not pts:
        return [], CRS.from_epsg(4326)
    avg_lon = sum(p[0] for p in pts) / len(pts)
    avg_lat = sum(p[1] for p in pts) / len(pts)
    utm = _utm_crs_for_lon_lat(avg_lon, avg_lat)
    transformer = Transformer.from_crs("EPSG:4326", utm, always_xy=True)
    projected = [transformer.transform(lon, lat) for lon, lat in pts]
    return projected, utm
