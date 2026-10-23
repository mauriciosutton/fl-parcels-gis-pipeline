"""Stream a FileGDB layer into GeoParquet (WKB geometry, source CRS) in Arrow batches.

usage: python -I 02_to_parquet.py <gdb> <layer> <out.parquet>
"""
import json
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from pyproj import CRS

GDB, LAYER, OUT = sys.argv[1:4]
BATCH = 100_000

t0 = time.time()
with pyogrio.open_arrow(GDB, layer=LAYER, batch_size=BATCH, use_pyarrow=True) as (meta, reader):
    geom_col = meta["geometry_name"] or "wkb_geometry"
    crs = CRS.from_user_input(meta["crs"])
    print("geometry column:", geom_col, "| crs:", crs.to_string())

    schema = reader.schema
    if geom_col != "geometry":
        schema = schema.set(schema.get_field_index(geom_col), pa.field("geometry", pa.binary()))
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {"geometry": {"encoding": "WKB", "geometry_types": [], "crs": crs.to_json_dict()}},
    }
    schema = schema.with_metadata({b"geo": json.dumps(geo).encode()})

    rows = 0
    with pq.ParquetWriter(OUT, schema, compression="zstd") as writer:
        geom_idx = schema.get_field_index("geometry")
        for batch in reader:
            # Source is MultiPolygon ZM; Arrow streams can't force 2D, so drop Z/M here.
            wkb = batch.column(geom_idx).to_numpy(zero_copy_only=False)
            flat = shapely.to_wkb(shapely.force_2d(shapely.from_wkb(wkb)))
            arrays = list(batch.columns)
            arrays[geom_idx] = pa.array(flat, type=pa.binary())
            batch = pa.RecordBatch.from_arrays(arrays, schema=schema)
            writer.write_batch(batch, row_group_size=BATCH)
            rows += batch.num_rows
            if rows % 1_000_000 < BATCH:
                print(f"  {rows:>12,} rows  {time.time() - t0:6.0f}s", flush=True)

print(f"done: {rows:,} rows in {time.time() - t0:.0f}s -> {OUT}")
