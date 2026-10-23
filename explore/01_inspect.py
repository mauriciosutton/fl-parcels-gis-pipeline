"""Inspect the FileGDB: layers, CRS, extent, schema, and a few sample rows."""
import sys

import pyogrio

GDB = sys.argv[1]

layers = pyogrio.list_layers(GDB)
print("Layers:")
for name, geom_type in layers:
    print(f"  {name}  ({geom_type})")

for name, _ in layers:
    info = pyogrio.read_info(GDB, layer=name, force_feature_count=True, force_total_bounds=True)
    print(f"\n=== {name} ===")
    print("features:", f"{info['features']:,}")
    print("geometry:", info["geometry_type"])
    print("crs:", info["crs"])
    print("bounds:", info["total_bounds"])
    print("encoding:", info["encoding"])
    print("fid column:", info.get("fid_column"))
    print("capabilities:", info.get("capabilities"))
    print(f"\nfields ({len(info['fields'])}):")
    for f, dtype in zip(info["fields"], info["dtypes"]):
        print(f"  {f:<20} {dtype}")
    md = info.get("layer_metadata") or {}
    if md:
        print("\nlayer metadata keys:", list(md)[:20])

    df = pyogrio.read_dataframe(GDB, layer=name, max_features=5)
    with __import__("pandas").option_context("display.max_columns", None, "display.width", 250):
        print("\nsample rows (transposed):")
        print(df.drop(columns="geometry").T.to_string())
        print("\nsample geometry types:", df.geometry.geom_type.tolist())
        print("sample vertex counts:", df.geometry.count_coordinates().tolist())
