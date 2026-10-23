"""Spot-check a transform build: biggest stacks, attribute fill rates, sizes.

usage: python 06_check_build.py <build_dir>
"""
import sys
from pathlib import Path

import duckdb
import pyogrio

build = Path(sys.argv[1])
meta, table = pyogrio.read_arrow(build / "features.fgb")
geom = meta["geometry_name"] or "wkb_geometry"
features = table.drop_columns([geom])

con = duckdb.connect()
con.register("f", features)
con.execute(f"CREATE VIEW lookup AS SELECT * FROM read_parquet('{(build / 'lookup.parquet').as_posix()}')")

print("crs:", meta["crs"], "| geometry:", meta["geometry_type"])
fgb_mb = (build / "features.fgb").stat().st_size / 1e6
print(f"features.fgb {fgb_mb:,.0f} MB, {features.num_rows:,} features, "
      f"{fgb_mb * 1e6 / features.num_rows:,.0f} bytes/feature")
print(f"lookup.parquet {(build / 'lookup.parquet').stat().st_size / 1e6:,.0f} MB")

print("\nbiggest stacks:")
print(con.execute("""
    SELECT fid, units, county, land_use, site_addr, site_city, just_value, res_units, round(acres, 2) acres
    FROM f ORDER BY units DESC LIMIT 8
""").df().to_string(index=False))

print("\nsample singles:")
print(con.execute("""
    SELECT fid, parcel_id, land_use, site_addr, just_value, year_built, sale_price, sale_year, acres
    FROM f WHERE units = 1 AND matched USING SAMPLE 6 ROWS (reservoir, 7)
""").df().to_string(index=False))

print("\nunmatched labels (top):")
print(con.execute("""
    SELECT source_parcel_no, count(*) n FROM f WHERE NOT matched GROUP BY 1 ORDER BY 2 DESC LIMIT 6
""").df().to_string(index=False))

print("\nattribute fill rate (% non-null):")
cols = [c for c in features.column_names]
fill = con.execute("SELECT " + ", ".join(f'round(100.0 * count("{c}") / count(*), 1) "{c}"' for c in cols)
                   + " FROM f").df().T
print(fill.to_string(header=False))

print("\nlookup: one stack's units")
print(con.execute("""
    SELECT fid, state_parcel_id, site_addr, owner_name, just_value, homestead
    FROM lookup WHERE fid = (SELECT fid FROM f ORDER BY units DESC LIMIT 1) LIMIT 5
""").df().to_string(index=False))
print("lookup rows:", con.execute("SELECT count(*) FROM lookup").fetchone()[0])
