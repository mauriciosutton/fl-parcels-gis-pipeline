import json

import pyarrow.parquet as pq
import pyogrio
import pytest
import shapely

from flparcels.config import FLORIDA_BOUNDS
from flparcels.transform import FEATURES, LOOKUP, REPORT, street_prefix, transform


def read_features(build):
    meta, table = pyogrio.read_arrow(build / FEATURES)
    rows = table.to_pylist()
    for r in rows:
        r["geom"] = shapely.from_wkb(r.pop(meta["geometry_name"] or "wkb_geometry"))
    return meta, rows


@pytest.fixture
def statewide(staged, tmp_path):
    build = tmp_path / "build"
    report = transform(staged, build)
    return build, report


def test_counts(statewide):
    build, report = statewide
    assert report["input_rows"] == 15
    assert report["duplicates_removed"] == 1
    assert report["unmatched_removed"] == 1  # the condo's unmatched copy
    assert report["records"] == 13
    assert report["features"] == report["features_written"] == 9
    assert report["stacked_features"] == 3 and report["records_in_stacks"] == 7
    assert json.loads((build / REPORT).read_text())["features"] == 9


def test_condo_stack_collapses_to_one_feature(statewide):
    build, _ = statewide
    _, rows = read_features(build)
    condo = next(r for r in rows if r["units"] == 3)
    assert condo["parcel_id"] is None and condo["state_parcel_id"] is None
    assert condo["just_value"] == 300_000
    assert condo["res_units"] == 3
    assert condo["site_addr"] == "100 MAIN ST"
    assert condo["land_use"] == "Condominium" and condo["land_use_group"] == "Residential"
    assert condo["county"] == "Alachua" and condo["matched"]

    lookup = pq.read_table(build / LOOKUP).to_pylist()
    units = [r for r in lookup if r["fid"] == condo["fid"]]
    assert sorted(u["owner_name"] for u in units) == ["OWNER 1", "OWNER 2", "OWNER 3"]

    pairs = {r["site_addr"] for r in rows if r["units"] == 2}
    assert pairs == {None, "ROYAL MANOR CIR"}


def test_singles_keep_their_attributes_and_text_is_cleaned(statewide):
    build, _ = statewide
    _, rows = read_features(build)
    house = next(r for r in rows if r["parcel_id"] == "H-1")
    assert house["units"] == 1
    assert house["site_addr"] == "500 OAK AVE"
    assert (house["sale_price"], house["sale_year"], house["year_built"]) == (250_000, 2025, 1990)
    assert house["land_value"] is None  # source 0 means missing

    split = [r for r in rows if r["parcel_id"] == "S-1"]
    assert len(split) == 2 and all(r["state_parcel_id"] == "C11-S1" for r in split)


def test_unmatched_polygons(statewide):
    build, _ = statewide
    _, rows = read_features(build)
    unmatched = {r["source_parcel_no"]: r for r in rows if not r["matched"]}
    assert set(unmatched) == {"Road", "Water"}
    assert unmatched["Road"]["county"] is None and unmatched["Road"]["dor_uc"] is None


def test_feature_ids_and_geometry(statewide):
    build, _ = statewide
    meta, rows = read_features(build)
    assert sorted(r["fid"] for r in rows) == list(range(1, 10))
    assert "4326" in meta["crs"]
    w, s, e, n = FLORIDA_BOUNDS
    for r in rows:
        assert r["geom"].geom_type == "MultiPolygon" and r["geom"].is_valid
        x0, y0, x1, y1 = r["geom"].bounds
        assert w < x0 < x1 < e and s < y0 < y1 < n

    lookup_fids = {r["fid"] for r in pq.read_table(build / LOOKUP, columns=["fid"]).to_pylist()}
    assert lookup_fids <= {r["fid"] for r in rows}


def test_county_pilot_keeps_unmatched_inside_extent_only(staged, tmp_path):
    build = tmp_path / "co11"
    report = transform(staged, build, county=11)
    _, rows = read_features(build)
    assert report["features_written"] == 7
    assert {r["county"] for r in rows} == {"Alachua", None}
    assert {r["source_parcel_no"] for r in rows if not r["matched"]} == {"Road"}


def test_unknown_county_rejected(staged, tmp_path):
    with pytest.raises(ValueError):
        transform(staged, tmp_path / "x", county=99)


@pytest.mark.parametrize(("a", "b", "expected"), [
    ("100 MAIN ST 1", "100 MAIN ST 10", "100 MAIN ST"),
    ("6241 POINTE REGAL CIR 203", "6241 POINTE REGAL CIR 210", "6241 POINTE REGAL CIR"),
    ("100 MAIN ST", "100 MAIN ST", "100 MAIN ST"),
    ("100 MAIN ST", "100 MAINE ST", None),
    ("100 MAIN ST", "200 MAIN ST", None),
    (None, "100 MAIN ST", None),
])
def test_street_prefix(a, b, expected):
    assert street_prefix(a, b) == expected
