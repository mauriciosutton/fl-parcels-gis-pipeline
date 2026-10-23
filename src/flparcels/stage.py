"""Step 2: stream the FileGDB layer into GeoParquet (2D WKB in the source CRS, plus a bbox column)."""
import json
import logging
import time
import warnings
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from pyproj import CRS

from .config import SOURCE_LAYER

log = logging.getLogger(__name__)

BBOX_KEYS = ("xmin", "ymin", "xmax", "ymax")
BBOX_TYPE = pa.struct([(k, pa.float64()) for k in BBOX_KEYS])


def stage(gdb: Path, out: Path, layer: str = SOURCE_LAYER, batch_size: int = 100_000) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial.parquet")
    t0 = time.time()
    rows = 0
    # The source is 'Measured 3D MultiPolygon'. GDAL warns that M is unsupported (Z and M are dropped
    # below) and that a few parcels with 100+ parts are slow to assemble; both are expected.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r"Measured \(M\) geometry types")
        warnings.filterwarnings("ignore", message=r"organizePolygons\(\) received a polygon with more than 100 parts")
        with pyogrio.open_arrow(gdb, layer=layer, batch_size=batch_size, use_pyarrow=True) as (meta, reader):
            schema, geom_idx = _output_schema(reader.schema, meta)
            with pq.ParquetWriter(partial, schema, compression="zstd") as writer:
                for batch in reader:
                    writer.write_batch(_convert(batch, schema, geom_idx), row_group_size=batch_size)
                    rows += batch.num_rows
                    if rows % 1_000_000 < batch_size:
                        log.info("staged %s rows (%.0fs)", f"{rows:,}", time.time() - t0)
    partial.replace(out)
    log.info("staged %s rows in %.0fs -> %s", f"{rows:,}", time.time() - t0, out)
    return rows


def _output_schema(src: pa.Schema, meta: dict) -> tuple[pa.Schema, int]:
    geom_idx = src.get_field_index(meta["geometry_name"] or "wkb_geometry")
    schema = src.set(geom_idx, pa.field("geometry", pa.binary())).append(pa.field("bbox", BBOX_TYPE))
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": [],
                "crs": CRS.from_user_input(meta["crs"]).to_json_dict(),
                "covering": {"bbox": {k: ["bbox", k] for k in BBOX_KEYS}},
            }
        },
    }
    return schema.with_metadata({b"geo": json.dumps(geo).encode()}), geom_idx


def _convert(batch: pa.RecordBatch, schema: pa.Schema, geom_idx: int) -> pa.RecordBatch:
    # Arrow streams can't ask GDAL to force 2D, so strip Z/M with shapely.
    geoms = shapely.force_2d(shapely.from_wkb(batch.column(geom_idx).to_numpy(zero_copy_only=False)))
    bounds = shapely.bounds(geoms)
    arrays = list(batch.columns)
    arrays[geom_idx] = pa.array(shapely.to_wkb(geoms), type=pa.binary())
    arrays.append(pa.StructArray.from_arrays([pa.array(bounds[:, i]) for i in range(4)], fields=list(BBOX_TYPE)))
    return pa.RecordBatch.from_arrays(arrays, schema=schema)
