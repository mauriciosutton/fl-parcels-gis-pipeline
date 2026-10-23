"""Builds tiny staged GeoParquet files shaped like the real DOR layer."""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pyproj import CRS

from flparcels.stage import BBOX_KEYS, BBOX_TYPE

TEXT_COLS = [
    "PARCELNO", "PARCEL_ID", "STATE_PAR_", "ALT_KEY", "DOR_UC", "PA_UC", "SALE_MO1", "QUAL_CD1", "VI_CD1",
    "OR_BOOK1", "OR_PAGE1", "PHY_ADDR1", "PHY_ADDR2", "PHY_CITY", "OWN_NAME", "OWN_ADDR1", "OWN_ADDR2",
    "OWN_CITY", "OWN_STATE", "S_LEGAL", "NBRHD_CD", "MKT_AR", "TAX_AUTH_C", "TWN", "RNG", "SEC",
    "CENSUS_BK", "PUBLIC_LND",
]
NUM_COLS = [
    "CO_NO", "ASMNT_YR", "JV", "AV_SD", "TV_NSD", "LND_VAL", "JV_HMSTD", "LND_SQFOOT", "ACT_YR_BLT",
    "EFF_YR_BLT", "TOT_LVG_AR", "NO_BULDNG", "NO_RES_UNT", "SALE_PRC1", "SALE_YR1", "PHY_ZIPCD",
    "OWN_ZIPCD", "SEQ_NO", "Shape_Area",
]


def square(x: float, y: float, size: float = 30.0) -> shapely.MultiPolygon:
    """A size x size square in EPSG:6439 metres."""
    return shapely.MultiPolygon([shapely.box(x, y, x + size, y + size)])


def write_staged(path, rows: list[dict]) -> None:
    """rows: dicts of source columns plus 'geom'; unspecified text is ' ' and numbers 0, as in the source."""
    geoms = [r["geom"] for r in rows]
    bounds = shapely.bounds(geoms)
    cols = {c: pa.array([r.get(c, " ") for r in rows], pa.string()) for c in TEXT_COLS}
    cols |= {c: pa.array([float(r.get(c, 0)) for r in rows], pa.float64()) for c in NUM_COLS}
    cols["Shape_Area"] = pa.array(shapely.area(geoms))
    cols["OID_"] = pa.array([i + 1 for i in range(len(rows))], pa.int32())
    cols["geometry"] = pa.array(shapely.to_wkb(geoms), pa.binary())
    cols["bbox"] = pa.StructArray.from_arrays([pa.array(bounds[:, i]) for i in range(4)], fields=list(BBOX_TYPE))
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {"geometry": {
            "encoding": "WKB", "geometry_types": [], "crs": CRS.from_epsg(6439).to_json_dict(),
            "covering": {"bbox": {k: ["bbox", k] for k in BBOX_KEYS}},
        }},
    }
    table = pa.table(cols)
    pq.write_table(table.replace_schema_metadata({b"geo": json.dumps(geo).encode()}), path)


# Near Gainesville in EPSG:6439.
X, Y = 565_000, 627_000


@pytest.fixture
def staged(tmp_path):
    def condo(n, addr):
        return dict(
            CO_NO=11, PARCEL_ID=f"C-{n}", STATE_PAR_=f"C11-{n}", PARCELNO=f"C-{n}", DOR_UC="004", JV=100_000,
            NO_RES_UNT=1, PHY_ADDR1=addr, PHY_CITY="GAINESVILLE", PHY_ZIPCD=32601, OWN_NAME=f"OWNER {n}",
            SEQ_NO=n, geom=square(X, Y),
        )

    house = dict(
        CO_NO=11, PARCEL_ID="H-1", STATE_PAR_="C11-H1", PARCELNO="H-1", DOR_UC="001", JV=200_000,
        ACT_YR_BLT=1990, SALE_PRC1=250_000, SALE_YR1=2025, SALE_MO1="04",
        PHY_ADDR1="\t500  OAK  AVE ", PHY_CITY="GAINESVILLE", SEQ_NO=10, geom=square(X + 100, Y),
    )
    rows = [
        # A three-unit condo stack on one footprint (addresses differ only by unit).
        condo(1, "100 MAIN ST 1"), condo(2, "100 MAIN ST 2"), condo(3, "100 MAIN ST 10"),
        # A two-unit stack with no common street (exercises the UDF returning NULL).
        condo(4, "1 A ST") | {"geom": square(X + 50, Y)}, condo(5, "9 B ST") | {"geom": square(X + 50, Y)},
        # A two-unit stack whose units differ only by house number.
        condo(6, "6329 ROYAL MANOR CIR") | {"geom": square(X + 70, Y)},
        condo(7, "6337 ROYAL MANOR CIR") | {"geom": square(X + 70, Y)},
        # The same house listed twice.
        house, house | {"SEQ_NO": 11},
        # Unmatched copy of the condo footprint: redundant, should go.
        dict(CO_NO=0, PARCELNO="COMMON AREA", geom=square(X, Y)),
        # Unmatched road inside the county's extent: kept as matched=false.
        dict(CO_NO=0, PARCELNO="Road", geom=square(X + 200, Y)),
        # One parcel drawn as two separate polygons.
        dict(CO_NO=11, PARCEL_ID="S-1", STATE_PAR_="C11-S1", DOR_UC="082", JV=50_000, SEQ_NO=20,
             geom=square(X + 300, Y)),
        dict(CO_NO=11, PARCEL_ID="S-1", STATE_PAR_="C11-S1", DOR_UC="082", JV=50_000, SEQ_NO=21,
             geom=square(X + 400, Y)),
        # Another county, and an unmatched polygon outside county 11.
        dict(CO_NO=12, PARCEL_ID="B-1", STATE_PAR_="C12-B1", DOR_UC="000", SEQ_NO=1, geom=square(X - 50_000, Y + 80_000)),
        dict(CO_NO=0, PARCELNO="Water", geom=square(X - 60_000, Y + 90_000)),
    ]
    path = tmp_path / "staged.parquet"
    write_staged(path, rows)
    return path
