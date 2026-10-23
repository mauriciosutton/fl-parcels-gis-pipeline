"""Step 3: clean the staged parcels, collapse stacked footprints, reproject, and write the tile inputs.

Writes to the build directory:
  features.fgb           one polygon per distinct footprint, EPSG:4326, public tile attributes only
  lookup.parquet         one row per tax roll record (full detail incl. owner), keyed by the footprint's fid
  transform_report.json  row counts at each stage

What the cleaning handles (see explore/ for the profiling behind it):
  * exact duplicates: the same county, parcel and shape listed more than once (mostly Palm Beach)
  * stacks: condo/co-op/mobile home units that each carry the whole building's outline; one feature
    per footprint with a unit count and totals, unit detail stays in the lookup
  * unmatched polygons (CO_NO = 0, no tax roll record): kept with matched=false, except ones that
    duplicate a matched footprint (e.g. a condo's master polygon)
"""
import json
import logging
import math
import shutil
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from pyproj import CRS, Transformer

from .codes import COUNTIES, LAND_USE, land_use_group
from .config import TILE_LAYER

log = logging.getLogger(__name__)

FEATURES = "features.fgb"
LOOKUP = "lookup.parquet"
REPORT = "transform_report.json"

# Source text fields pad with spaces, tabs and doubled spaces; numeric fields use 0 for "missing".
MACROS = r"""
CREATE OR REPLACE TEMP MACRO clean(s) AS nullif(trim(regexp_replace(s, '\s+', ' ', 'g')), '');
CREATE OR REPLACE TEMP MACRO pos(x) AS CASE WHEN x > 0 THEN x END;
CREATE OR REPLACE TEMP MACRO zip5(x) AS CASE WHEN x > 0 THEN lpad(x::INTEGER::VARCHAR, 5, '0') END;
CREATE OR REPLACE TEMP MACRO street_name(addr) AS regexp_replace(addr, '^\d\S*\s+', '');
"""

RECORDS = """
SELECT
    md5(geometry)                              AS ghash,
    CASE WHEN CO_NO > 0 THEN CO_NO::SMALLINT END AS co_no,
    clean(STATE_PAR_)                          AS state_parcel_id,
    clean(PARCEL_ID)                           AS parcel_id,
    clean(PARCELNO)                            AS source_parcel_no,
    clean(ALT_KEY)                             AS alt_key,
    pos(ASMNT_YR)::SMALLINT                    AS assessment_year,
    clean(DOR_UC)                              AS dor_uc,
    clean(PA_UC)                               AS pa_uc,
    pos(JV)::BIGINT                            AS just_value,
    pos(AV_SD)::BIGINT                         AS assessed_value,
    pos(TV_NSD)::BIGINT                        AS taxable_value,
    pos(LND_VAL)::BIGINT                       AS land_value,
    coalesce(JV_HMSTD, 0) > 0                  AS homestead,
    pos(LND_SQFOOT)::BIGINT                    AS land_sqft,
    pos(ACT_YR_BLT)::SMALLINT                  AS year_built,
    pos(EFF_YR_BLT)::SMALLINT                  AS eff_year_built,
    pos(TOT_LVG_AR)::BIGINT                    AS living_sqft,
    pos(NO_BULDNG)::INTEGER                    AS buildings,
    pos(NO_RES_UNT)::INTEGER                   AS res_units,
    pos(SALE_PRC1)::BIGINT                     AS sale_price,
    pos(SALE_YR1)::SMALLINT                    AS sale_year,
    pos(TRY_CAST(clean(SALE_MO1) AS TINYINT))  AS sale_month,
    clean(QUAL_CD1)                            AS sale_qual_code,
    clean(VI_CD1)                              AS sale_vacant_improved,
    clean(OR_BOOK1)                            AS sale_or_book,
    clean(OR_PAGE1)                            AS sale_or_page,
    clean(PHY_ADDR1)                           AS site_addr,
    clean(PHY_ADDR2)                           AS site_addr2,
    clean(PHY_CITY)                            AS site_city,
    zip5(PHY_ZIPCD)                            AS site_zip,
    clean(OWN_NAME)                            AS owner_name,
    clean(OWN_ADDR1)                           AS owner_addr1,
    clean(OWN_ADDR2)                           AS owner_addr2,
    clean(OWN_CITY)                            AS owner_city,
    clean(OWN_STATE)                           AS owner_state,
    zip5(OWN_ZIPCD)                            AS owner_zip,
    clean(S_LEGAL)                             AS legal_desc,
    clean(NBRHD_CD)                            AS neighborhood_code,
    clean(MKT_AR)                              AS market_area,
    clean(TAX_AUTH_C)                          AS tax_authority_code,
    clean(TWN)                                 AS twn,
    clean(RNG)                                 AS rng,
    clean(SEC)                                 AS sec,
    clean(CENSUS_BK)                           AS census_block,
    clean(PUBLIC_LND)                          AS public_land_code,
    Shape_Area                                 AS area_m2,
    geometry
FROM src
"""

# Choose the source rows to keep using only light columns (keys, hash, order), so the window and
# anti-join don't drag the wide text columns and geometry along. Keeps the first of each exact
# duplicate, and drops unmatched polygons whose shape a matched record already has.
KEEP = """
CREATE TEMP TABLE keep AS
WITH k AS (
    SELECT rn, ghash, matched FROM (
        SELECT file_row_number AS rn, md5(geometry) AS ghash, CO_NO > 0 AS matched, CO_NO AS co,
               coalesce(clean(STATE_PAR_), clean(PARCELNO)) AS pkey, SEQ_NO AS seq, OID_ AS oid
        FROM src WHERE {where}
    )
    QUALIFY row_number() OVER (PARTITION BY co, pkey, ghash ORDER BY seq, oid) = 1
)
SELECT rn FROM k WHERE matched
UNION ALL
SELECT rn FROM k u WHERE NOT u.matched AND NOT EXISTS (SELECT 1 FROM k m WHERE m.matched AND m.ghash = u.ghash)
"""

PARCELS = f"""
CREATE TABLE parcels AS
{RECORDS} WHERE file_row_number IN (SELECT rn FROM keep)
"""

# One row per distinct shape, with its unit count and a deterministic feature id.
FP_KEYS = """
CREATE TABLE fp_keys AS
SELECT
    ghash,
    count(*)::INTEGER AS units,
    row_number() OVER (
        ORDER BY min(co_no) NULLS LAST, min(coalesce(state_parcel_id, source_parcel_no)) NULLS LAST, ghash
    ) AS fid
FROM parcels
GROUP BY ghash
"""

# Single-record footprints (99%) pass straight through; only stacks are aggregated into totals.
FOOTPRINTS = """
CREATE TEMP VIEW footprints AS
SELECT
    k.fid, k.units, p.co_no, p.parcel_id, p.state_parcel_id,
    CASE WHEN p.co_no IS NULL THEN p.source_parcel_no END AS source_parcel_no,
    p.dor_uc, p.just_value, p.land_value, p.year_built, p.living_sqft, p.res_units,
    p.sale_price, p.sale_year, p.site_addr, p.site_city, p.site_zip, p.area_m2, p.geometry
FROM parcels p JOIN fp_keys k USING (ghash)
WHERE k.units = 1
UNION ALL
SELECT
    k.fid, k.units, min(p.co_no), NULL, NULL, NULL,
    mode(p.dor_uc),
    sum(p.just_value)::BIGINT,
    sum(p.land_value)::BIGINT,
    min(p.year_built),
    sum(p.living_sqft)::BIGINT,
    sum(p.res_units)::INTEGER,
    NULL, NULL,
    -- The address the units share ('100 MAIN ST'), else their one common street when house
    -- numbers differ ('ROYAL MANOR CIR'), else nothing.
    coalesce(
        street_prefix(min(p.site_addr), max(p.site_addr)),
        CASE WHEN count(DISTINCT street_name(p.site_addr)) = 1 THEN any_value(street_name(p.site_addr)) END),
    mode(p.site_city),
    mode(p.site_zip),
    any_value(p.area_m2),
    any_value(p.geometry)
FROM parcels p JOIN fp_keys k USING (ghash)
WHERE k.units > 1
GROUP BY k.fid, k.units
"""

# The public tile schema. fid becomes the MVT feature id; owner fields stay in the lookup only.
TILE_FEATURES = """
SELECT
    f.fid,
    f.co_no IS NOT NULL                     AS matched,
    f.units,
    f.parcel_id,
    f.state_parcel_id,
    f.source_parcel_no,
    c.county,
    f.dor_uc,
    l.land_use,
    l.land_use_group,
    f.just_value,
    f.land_value,
    round(f.area_m2 / 4046.8564224, 3)      AS acres,
    f.year_built,
    f.living_sqft,
    f.res_units,
    f.sale_price,
    f.sale_year,
    f.site_addr,
    f.site_city,
    f.site_zip,
    f.geometry
FROM footprints f
LEFT JOIN counties c USING (co_no)
LEFT JOIN land_use l USING (dor_uc)
"""

LOOKUP_SQL = """
SELECT f.fid, c.county, l.land_use, l.land_use_group, p.* EXCLUDE (ghash, geometry)
FROM parcels p
JOIN fp_keys f USING (ghash)
LEFT JOIN counties c ON c.co_no = p.co_no
LEFT JOIN land_use l ON l.dor_uc = p.dor_uc
ORDER BY f.fid, p.state_parcel_id
"""


def street_prefix(a: str | None, b: str | None) -> str | None:
    """Whole words shared at the start of two addresses: '100 MAIN ST 4' + '100 MAIN ST 12' -> '100 MAIN ST'.

    Called with the min and max address of a stack; their common prefix is shared by every address
    sorted between them. Returns None unless at least a house number and a street word are shared.
    """
    if a is None or b is None:
        return None
    common = []
    for x, y in zip(a.split(" "), b.split(" ")):
        if x != y:
            break
        common.append(x)
    return " ".join(common) if len(common) >= 2 else None


def reproject(wkb: np.ndarray, transformer: Transformer) -> tuple[np.ndarray, int]:
    """Reproject WKB polygons and repair any the reprojection left invalid.

    Returns (MultiPolygon WKB, number repaired). A geometry that repairs to nothing becomes None.
    """
    geoms = shapely.from_wkb(wkb)
    geoms = shapely.transform(geoms, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))
    bad = np.flatnonzero(~shapely.is_valid(geoms))
    for i in bad:
        geoms[i] = _as_multipolygon(shapely.make_valid(geoms[i], method="structure", keep_collapsed=False))
    return shapely.to_wkb(geoms), len(bad)


def _as_multipolygon(geom):
    polys = [p for p in shapely.get_parts(shapely.get_parts(geom)) if p.geom_type == "Polygon" and not p.is_empty]
    return shapely.MultiPolygon(polys) if polys else None


def transform(staged: Path, build_dir: Path, county: int | None = None, memory_limit: str | None = None) -> dict:
    t0 = time.time()
    build_dir.mkdir(parents=True, exist_ok=True)
    work_db = build_dir / "work.duckdb"
    duck_tmp = build_dir / "duckdb_tmp"
    _remove(work_db, duck_tmp)
    features_partial = build_dir / "features.partial.fgb"
    lookup_partial = build_dir / "lookup.partial.parquet"
    _remove(features_partial, lookup_partial)

    # A file-backed work database lets DuckDB page the ~10 GB of intermediate tables to disk.
    con = duckdb.connect(work_db)
    try:
        _setup(con, staged, duck_tmp, memory_limit)
        where = _where(con, county)
        log.info("de-duplicating records")
        con.execute(KEEP.format(where=where))
        log.info("cleaning attributes")
        con.execute(PARCELS)
        log.info("grouping footprints")
        con.execute(FP_KEYS)
        con.execute(FOOTPRINTS)
        report = _counts(con, where)
        log.info("writing lookup (%s records)", f"{report['records']:,}")
        con.execute(f"COPY ({LOOKUP_SQL}) TO '{_sql_path(lookup_partial)}' (FORMAT parquet, COMPRESSION zstd)")
        log.info("reprojecting and writing %s features", f"{report['features']:,}")
        report |= _write_features(con, features_partial, _source_crs(staged))
    finally:
        con.close()
        _remove(work_db, work_db.with_suffix(".duckdb.wal"), duck_tmp)

    features_partial.replace(build_dir / FEATURES)
    lookup_partial.replace(build_dir / LOOKUP)
    report.update(county=county, seconds=round(time.time() - t0))
    (build_dir / REPORT).write_text(json.dumps(report, indent=2))
    log.info("transform done in %ss: %s", report["seconds"], build_dir)
    return report


def _setup(con: duckdb.DuckDBPyConnection, staged: Path, duck_tmp: Path, memory_limit: str | None) -> None:
    con.execute("SET enable_geoparquet_conversion = false")  # keep geometry as raw WKB
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET temp_directory = '{_sql_path(duck_tmp)}'")
    if memory_limit:
        con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(MACROS)
    # "special" null handling: the UDF sees NULL inputs and may return NULL (no shared street).
    con.create_function("street_prefix", street_prefix, ["VARCHAR", "VARCHAR"], "VARCHAR", null_handling="special")
    con.execute(f"CREATE TEMP VIEW src AS SELECT * FROM read_parquet('{_sql_path(staged)}', file_row_number = true)")
    con.execute("CREATE TEMP TABLE counties (co_no SMALLINT, county VARCHAR)")
    con.executemany("INSERT INTO counties VALUES (?, ?)", list(COUNTIES.items()))
    con.execute("CREATE TEMP TABLE land_use (dor_uc VARCHAR, land_use VARCHAR, land_use_group VARCHAR)")
    con.executemany(
        "INSERT INTO land_use VALUES (?, ?, ?)",
        [(code, label, land_use_group(code)) for code, label in LAND_USE.items()],
    )


def _where(con: duckdb.DuckDBPyConnection, county: int | None) -> str:
    if county is None:
        return "TRUE"
    county = int(county)
    if county not in COUNTIES:
        raise ValueError(f"unknown county number {county}; expected 11-77")
    x0, y0, x1, y1 = con.execute(
        f"SELECT min(bbox.xmin), min(bbox.ymin), max(bbox.xmax), max(bbox.ymax) FROM src WHERE CO_NO = {county}"
    ).fetchone()
    if x0 is None:
        raise ValueError(f"no parcels for county {county}")
    # Unmatched polygons carry no county, so take the ones that fall inside the county's extent.
    return (
        f"(CO_NO = {county} OR (CO_NO = 0 AND bbox.xmin >= {x0} AND bbox.ymin >= {y0}"
        f" AND bbox.xmax <= {x1} AND bbox.ymax <= {y1}))"
    )


def _counts(con: duckdb.DuckDBPyConnection, where: str) -> dict:
    input_rows, input_unmatched = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE CO_NO = 0) FROM src WHERE {where}"
    ).fetchone()
    records, records_unmatched = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE co_no IS NULL) FROM parcels"
    ).fetchone()
    features, stacks, stacked_records = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE units > 1), coalesce(sum(units) FILTER (WHERE units > 1), 0)"
        " FROM fp_keys"
    ).fetchone()
    return {
        "input_rows": input_rows,
        "duplicates_removed": (input_rows - input_unmatched) - (records - records_unmatched),
        "unmatched_removed": input_unmatched - records_unmatched,
        "records": records,
        "records_unmatched": records_unmatched,
        "features": features,
        "stacked_features": stacks,
        "records_in_stacks": int(stacked_records),
    }


def _write_features(con: duckdb.DuckDBPyConnection, path: Path, src_crs: CRS, batch_rows: int = 50_000) -> dict:
    to_wgs84 = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    reader = con.sql(TILE_FEATURES).to_arrow_reader(batch_rows)
    geom_idx = reader.schema.get_field_index("geometry")
    schema = reader.schema.set(geom_idx, pa.field("geometry", pa.binary()))
    stats = {"features_written": 0, "geometries_repaired": 0, "geometries_dropped": 0}
    bounds = [math.inf, math.inf, -math.inf, -math.inf]

    def batches():
        for batch in reader:
            wkb, repaired = reproject(batch.column(geom_idx).to_numpy(zero_copy_only=False), to_wgs84)
            keep = np.array([w is not None for w in wkb])
            if not keep.all():
                batch, wkb = batch.filter(pa.array(keep)), wkb[keep]
            b = shapely.total_bounds(shapely.from_wkb(wkb))
            bounds[:] = [min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])]
            stats["features_written"] += len(wkb)
            stats["geometries_repaired"] += repaired
            stats["geometries_dropped"] += int((~keep).sum())
            arrays = list(batch.columns)
            arrays[geom_idx] = pa.array(wkb, type=pa.binary())
            yield pa.RecordBatch.from_arrays(arrays, schema=schema)

    pyogrio.write_arrow(
        pa.RecordBatchReader.from_batches(schema, batches()),
        path,
        layer=TILE_LAYER,
        driver="FlatGeobuf",
        geometry_name="geometry",
        geometry_type="MultiPolygon",
        crs="EPSG:4326",
        layer_options={"SPATIAL_INDEX": "NO"},  # tippecanoe reads sequentially; skip the index build
    )
    return stats | {"bounds_wgs84": [round(v, 6) for v in bounds]}


def _source_crs(staged: Path) -> CRS:
    geo = json.loads(pq.read_schema(staged).metadata[b"geo"])
    return CRS.from_json_dict(geo["columns"][geo["primary_column"]]["crs"])


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _remove(*paths: Path) -> None:
    for p in paths:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
