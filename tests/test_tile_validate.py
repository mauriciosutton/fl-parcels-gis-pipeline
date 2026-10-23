from pathlib import Path

import pytest
from pmtiles.reader import zxy_to_tileid
from pmtiles.tile import Compression, TileType
from pmtiles.writer import write

from flparcels.codes import COUNTIES, LAND_USE, land_use_group
from flparcels.tile import tippecanoe_command
from flparcels.validate import ValidationError, validate


def test_tippecanoe_command():
    cmd = tippecanoe_command(Path("in.fgb"), Path("out.pmtiles"), Path("tmp"))
    assert cmd[0] == "tippecanoe" and cmd[-1] == "in.fgb"
    for flag in ("--minimum-zoom=12", "--maximum-zoom=16", "--use-attribute-for-id=fid", "--layer=parcels"):
        assert flag in cmd
    assert tippecanoe_command(Path("in.fgb"), Path("o"), Path("t"), extra_args=["--detect-shared-borders"])[-2] == (
        "--detect-shared-borders"
    )


def make_pmtiles(path: Path, biggest_tile: int = 1_000) -> Path:
    fields = {f: "String" for f in ("matched", "units", "county", "dor_uc", "land_use", "land_use_group",
                                     "just_value", "acres")}
    header = {
        "tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "internal_compression": Compression.GZIP,
        "min_zoom": 12, "max_zoom": 16,
        "min_lon_e7": -825_000_000, "min_lat_e7": 295_000_000, "max_lon_e7": -822_000_000, "max_lat_e7": 298_000_000,
        "center_zoom": 12, "center_lon_e7": -823_500_000, "center_lat_e7": 296_500_000,
    }
    meta = {
        "vector_layers": [{"id": "parcels", "fields": fields}],
        "tilestats": {"layers": [{"layer": "parcels", "count": 7}]},
    }
    with write(path) as w:
        for z in range(12, 17):
            # distinct bytes per zoom so the writer doesn't dedupe them into one entry
            w.write_tile(zxy_to_tileid(z, 0, 0), bytes([z]) * (biggest_tile if z == 12 else 100))
        w.finalize(header, meta)
    return path


def test_validate_passes(tmp_path):
    report = validate(make_pmtiles(tmp_path / "ok.pmtiles"), expected_features=7)
    assert [z["zoom"] for z in report["zooms"]] == [12, 13, 14, 15, 16]
    assert report["warnings"] == [] and report["problems"] == []
    assert report["bounds"] == [-82.5, 29.5, -82.2, 29.8]


def test_validate_flags_oversize_tiles(tmp_path):
    with pytest.raises(ValidationError, match="tiles over"):
        validate(make_pmtiles(tmp_path / "big.pmtiles", biggest_tile=600_000))


def test_validate_warns_on_feature_count_mismatch(tmp_path):
    report = validate(make_pmtiles(tmp_path / "ok.pmtiles"), expected_features=8)
    assert "tilestats counts 7" in report["warnings"][0]


def test_codes():
    assert len(COUNTIES) == 67 and min(COUNTIES) == 11 and max(COUNTIES) == 77
    assert len(LAND_USE) == 100
    assert land_use_group("004") == "Residential"
    assert land_use_group("048") == "Industrial"
    assert land_use_group("080") == "Governmental"
    assert land_use_group("099") == "Non-Agricultural Acreage"
