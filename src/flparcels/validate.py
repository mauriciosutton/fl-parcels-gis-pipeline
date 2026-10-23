"""Step 5: sanity-check the PMTiles archive before it ships to the tile server."""
import json
from collections import defaultdict
from pathlib import Path

from pmtiles.reader import MmapSource, Reader, deserialize_directory, tileid_to_zxy
from pmtiles.tile import TileType

from .config import FLORIDA_BOUNDS, MAX_TILE_BYTES, MAX_ZOOM, MIN_ZOOM, TILE_LAYER

# Attributes every real build has somewhere; sparse ones (sale_*, source_parcel_no) may be absent in a pilot.
CORE_FIELDS = {"matched", "units", "county", "dor_uc", "land_use", "land_use_group", "just_value", "acres"}


class ValidationError(Exception):
    def __init__(self, report: dict):
        super().__init__("; ".join(report["problems"]))
        self.report = report


def _tile_sizes(get_bytes, header, offset: int, length: int, zooms: dict) -> None:
    """Accumulate [tiles, biggest, total] stored bytes per zoom, walking directories without reading tile data."""
    for entry in deserialize_directory(get_bytes(offset, length)):
        if entry.run_length == 0:  # points at a leaf directory
            _tile_sizes(get_bytes, header, header["leaf_directory_offset"] + entry.offset, entry.length, zooms)
            continue
        for i in range(entry.run_length):
            z = tileid_to_zxy(entry.tile_id + i)[0]
            stat = zooms[z]
            stat[0] += 1
            stat[1] = max(stat[1], entry.length)
            stat[2] += entry.length


def validate(pmtiles: Path, expected_features: int | None = None) -> dict:
    with open(pmtiles, "rb") as f:
        get_bytes = MmapSource(f)
        reader = Reader(get_bytes)
        header = reader.header()
        info = reader.metadata()
        sizes = defaultdict(lambda: [0, 0, 0])
        _tile_sizes(get_bytes, header, header["root_offset"], header["root_length"], sizes)
    zooms = [(z, *sizes[z]) for z in sorted(sizes)]

    problems, warnings = [], []
    if header["tile_type"] != TileType.MVT:
        problems.append(f"tile type is {header['tile_type'].name}, expected MVT")

    minz, maxz = header["min_zoom"], header["max_zoom"]
    if minz != MIN_ZOOM or maxz < MAX_ZOOM:
        problems.append(f"zoom range {minz}-{maxz}, expected {MIN_ZOOM}-{MAX_ZOOM} or deeper")
    missing = sorted(set(range(minz, maxz + 1)) - {z for z, *_ in zooms})
    if missing:
        problems.append(f"no tiles at zooms {missing}")
    # tile sizes are as stored (gzip), which is what tippecanoe's 500 KB limit applies to
    oversize = {z: biggest for z, _, biggest, _ in zooms if biggest > MAX_TILE_BYTES}
    if oversize:
        problems.append(f"tiles over {MAX_TILE_BYTES:,} bytes at zooms {oversize}")

    bounds = [header[k] / 1e7 for k in ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7")]
    w, s, e, n = FLORIDA_BOUNDS
    if not (w <= bounds[0] < bounds[2] <= e and s <= bounds[1] < bounds[3] <= n):
        problems.append(f"bounds {bounds} fall outside Florida")

    # tippecanoe stores the layer description (vector_layers, tilestats) in the archive metadata; older
    # tooling nests it as a JSON string under "json"
    if isinstance(info.get("json"), str):
        info = {**info, **json.loads(info["json"])}
    layers = {layer["id"]: layer for layer in info.get("vector_layers", [])}
    if TILE_LAYER not in layers:
        problems.append(f"layer {TILE_LAYER!r} missing; found {sorted(layers)}")
    elif missing_fields := CORE_FIELDS - set(layers[TILE_LAYER].get("fields", {})):
        problems.append(f"layer {TILE_LAYER!r} lacks fields {sorted(missing_fields)}")

    stats = {layer.get("layer"): layer for layer in info.get("tilestats", {}).get("layers", [])}
    counted = stats.get(TILE_LAYER, {}).get("count")
    if expected_features is not None and counted is not None and counted != expected_features:
        warnings.append(f"tilestats counts {counted:,} features, transform wrote {expected_features:,}")

    report = {
        "path": str(pmtiles),
        "size_gb": round(pmtiles.stat().st_size / 1e9, 3),
        "minzoom": minz,
        "maxzoom": maxz,
        "bounds": bounds,
        "tilestats_features": counted,
        "zooms": [
            {"zoom": z, "tiles": count, "max_kb": round(biggest / 1e3, 1), "total_mb": round(total / 1e6, 1)}
            for z, count, biggest, total in zooms
        ],
        "warnings": warnings,
        "problems": problems,
    }
    if problems:
        raise ValidationError(report)
    return report
