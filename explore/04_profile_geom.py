"""Compute per-feature geometry metrics from the staged parquet and summarize them.

usage: python -I 04_profile_geom.py <parcels.parquet> <out_dir>
Writes <out_dir>/geom_metrics.parquet (one row per feature) for follow-up queries.
"""
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

SRC, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
METRICS = OUT / "geom_metrics.parquet"

t0 = time.time()
pf = pq.ParquetFile(SRC)
writer = None
done = 0
for i in range(pf.num_row_groups):
    t = pf.read_row_group(i, columns=["CO_NO", "DOR_UC", "geometry"])
    wkb = t.column("geometry").to_numpy(zero_copy_only=False)
    geoms = shapely.from_wkb(wkb)
    is_null = pd.isna(wkb)
    wkb_len = np.array([0 if w is None else len(w) for w in wkb], dtype=np.int32)
    out = pa.table({
        "CO_NO": t.column("CO_NO"),
        "DOR_UC": t.column("DOR_UC"),
        "is_null": is_null,
        "is_empty": shapely.is_empty(geoms) & ~is_null,
        "is_valid": shapely.is_valid(geoms),
        "geom_type": shapely.get_type_id(geoms).astype(np.int8),
        "n_parts": shapely.get_num_geometries(geoms).astype(np.int32),
        "n_rings": shapely.get_num_interior_rings(shapely.get_geometry(geoms, 0)).astype(np.int32),
        "n_coords": shapely.get_num_coordinates(geoms).astype(np.int32),
        "area_m2": shapely.area(geoms),
        "wkb_len": wkb_len,
        # Hash of normalized geometry to find stacked duplicates (condos, common elements).
        "geom_hash": pd.util.hash_array(
            np.array([b"" if w is None else w for w in shapely.to_wkb(shapely.normalize(geoms))], dtype=object)
        ),
    })
    writer = writer or pq.ParquetWriter(METRICS, out.schema)
    writer.write_table(out)
    done += t.num_rows
    if i % 10 == 0:
        print(f"  {done:>12,} rows  {time.time() - t0:6.0f}s", flush=True)
writer.close()
print(f"metrics written: {done:,} rows in {time.time() - t0:.0f}s\n")

con = duckdb.connect()
con.execute(f"CREATE VIEW g AS SELECT * FROM read_parquet('{METRICS}')")
q = lambda sql: con.execute(sql).df().to_string(index=False)

print("=== geometry health ===")
print(q("""
    SELECT count(*) total,
           count(*) FILTER (WHERE is_null) null_geom,
           count(*) FILTER (WHERE is_empty) empty_geom,
           count(*) FILTER (WHERE NOT is_valid AND NOT is_null AND NOT is_empty) invalid,
           count(*) FILTER (WHERE n_parts > 1) multipart,
           count(*) FILTER (WHERE n_rings > 0) with_holes,
           count(*) FILTER (WHERE area_m2 < 1 AND NOT is_null) area_lt_1m2,
           count(*) FILTER (WHERE area_m2 > 1e7) area_gt_10km2
    FROM g
"""))
print("\ngeometry type ids (6=MultiPolygon, 3=Polygon, -1=null):")
print(q("SELECT geom_type, count(*) n FROM g GROUP BY 1 ORDER BY 2 DESC"))

print("\n=== distributions ===")
print(q("""
    SELECT 'n_coords' metric, min(n_coords) min, quantile_cont(n_coords, 0.5) p50, quantile_cont(n_coords, 0.9) p90,
           quantile_cont(n_coords, 0.99) p99, quantile_cont(n_coords, 0.9999) p9999, max(n_coords) max, sum(n_coords) total
    FROM g WHERE NOT is_null
    UNION ALL
    SELECT 'n_parts', min(n_parts), quantile_cont(n_parts, 0.5), quantile_cont(n_parts, 0.9),
           quantile_cont(n_parts, 0.99), quantile_cont(n_parts, 0.9999), max(n_parts), sum(n_parts)
    FROM g WHERE NOT is_null
    UNION ALL
    SELECT 'area_m2', min(area_m2), quantile_cont(area_m2, 0.5), quantile_cont(area_m2, 0.9),
           quantile_cont(area_m2, 0.99), quantile_cont(area_m2, 0.9999), max(area_m2), sum(area_m2)
    FROM g WHERE NOT is_null
    UNION ALL
    SELECT 'wkb_len', min(wkb_len), quantile_cont(wkb_len, 0.5), quantile_cont(wkb_len, 0.9),
           quantile_cont(wkb_len, 0.99), quantile_cont(wkb_len, 0.9999), max(wkb_len), sum(wkb_len)
    FROM g WHERE NOT is_null
"""))

print("\n=== stacked / duplicate geometries ===")
print(q("""
    WITH d AS (SELECT geom_hash, count(*) n FROM g WHERE NOT is_null AND NOT is_empty GROUP BY 1)
    SELECT count(*) FILTER (WHERE n > 1) distinct_shapes_shared,
           coalesce(sum(n) FILTER (WHERE n > 1), 0) features_on_shared_shapes,
           max(n) max_stack
    FROM d
"""))
print("\nland use of features on shared shapes (top 15):")
print(q("""
    WITH d AS (SELECT geom_hash FROM g WHERE NOT is_null AND NOT is_empty GROUP BY 1 HAVING count(*) > 1)
    SELECT DOR_UC, count(*) n FROM g SEMI JOIN d USING (geom_hash) GROUP BY 1 ORDER BY 2 DESC LIMIT 15
"""))

print("\n=== per county geometry ===")
per = con.execute("""
    SELECT CO_NO::INT co_no, count(*) n,
           count(*) FILTER (WHERE is_null OR is_empty) no_geom,
           count(*) FILTER (WHERE NOT is_valid AND NOT is_null AND NOT is_empty) invalid,
           round(avg(n_coords), 1) avg_coords, max(n_coords) max_coords,
           round(quantile_cont(area_m2, 0.5)) p50_area_m2
    FROM g GROUP BY 1 ORDER BY 1
""").df()
per.to_csv(OUT / "per_county_geom.csv", index=False)
print(per.to_string(index=False))
