"""Profile every attribute column of the staged parcels parquet with DuckDB.

usage: python -I 03_profile_attrs.py <parcels.parquet> <out_dir>
"""
import sys
from pathlib import Path

import duckdb

SRC, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
con.execute(f"CREATE VIEW p AS SELECT * EXCLUDE geometry FROM read_parquet('{SRC}')")
cols = con.execute("DESCRIBE p").fetchall()  # (name, type, ...)
total = con.execute("SELECT count(*) FROM p").fetchone()[0]
print(f"rows: {total:,}\n")

# One scan: fill rate, cardinality, range and average stored length per column.
exprs = []
for name, typ, *_ in cols:
    q = f'"{name}"'
    is_str = typ == "VARCHAR"
    blank = f"({q} IS NULL OR trim({q}) = '')" if is_str else f"({q} IS NULL OR {q} = 0)"
    exprs += [
        f"count(*) FILTER (WHERE {blank}) AS \"{name}__blank\"",
        f"approx_count_distinct({q}) AS \"{name}__distinct\"",
        f"min({q})::VARCHAR AS \"{name}__min\"",
        f"max({q})::VARCHAR AS \"{name}__max\"",
        f"avg(length({q}::VARCHAR)) AS \"{name}__avglen\"",
    ]
row = con.execute(f"SELECT {', '.join(exprs)} FROM p").fetchone()
vals = iter(row)
rows = []
for name, typ, *_ in cols:
    blank, distinct, mn, mx, avglen = (next(vals) for _ in range(5))
    rows.append((name, typ, 100 * blank / total, distinct, mn, mx, avglen or 0))

prof = con.execute(
    "SELECT * FROM (VALUES " + ",".join("(?,?,?,?,?,?,?)" for _ in rows) + ") "
    "t(col, type, pct_blank_or_zero, approx_distinct, min, max, avg_len)",
    [v for r in rows for v in r],
).df()
prof.to_csv(OUT / "column_profile.csv", index=False)

with __import__("pandas").option_context("display.max_rows", 200, "display.width", 250,
                                         "display.max_colwidth", 28):
    prof["pct_blank_or_zero"] = prof["pct_blank_or_zero"].round(1)
    prof["avg_len"] = prof["avg_len"].round(1)
    print(prof.to_string(index=False))

# Top values for low-cardinality columns.
print("\n=== top values (columns with <= 120 distinct) ===")
with open(OUT / "top_values.txt", "w", encoding="utf-8") as fh:
    for name, typ, _, distinct, *_ in rows:
        if distinct > 120:
            continue
        top = con.execute(
            f'SELECT "{name}" v, count(*) n FROM p GROUP BY 1 ORDER BY 2 DESC LIMIT 12'
        ).fetchall()
        line = f"{name}: " + ", ".join(f"{v!r}={n:,}" for v, n in top)
        print(line)
        fh.write(line + "\n")

print("\n=== key uniqueness ===")
for label, sql in {
    "distinct PARCEL_ID": "SELECT count(DISTINCT PARCEL_ID) FROM p",
    "distinct (CO_NO, PARCEL_ID)": "SELECT count(*) FROM (SELECT DISTINCT CO_NO, PARCEL_ID FROM p)",
    "distinct STATE_PAR_": "SELECT count(DISTINCT STATE_PAR_) FROM p",
    "distinct (CO_NO, PARCELNO)": "SELECT count(*) FROM (SELECT DISTINCT CO_NO, PARCELNO FROM p)",
    "PARCELNO <> PARCEL_ID": "SELECT count(*) FROM p WHERE PARCELNO IS DISTINCT FROM PARCEL_ID",
    "PARCEL_ID blank": "SELECT count(*) FROM p WHERE PARCEL_ID IS NULL OR trim(PARCEL_ID) = ''",
    "PARCELNO blank": "SELECT count(*) FROM p WHERE PARCELNO IS NULL OR trim(PARCELNO) = ''",
    "OID_ distinct": "SELECT count(DISTINCT OID_) FROM p",
}.items():
    print(f"  {label:<30} {con.execute(sql).fetchone()[0]:>12,}")

print("\n  sample PARCEL_ID values repeated within a county:")
print(con.execute("""
    SELECT CO_NO, PARCEL_ID, count(*) n FROM p
    GROUP BY ALL HAVING count(*) > 1 ORDER BY n DESC LIMIT 10
""").df().to_string(index=False))
print("\n  rows sharing a (CO_NO, PARCEL_ID):",
      f"{con.execute('SELECT coalesce(sum(n), 0) FROM (SELECT count(*) n FROM p GROUP BY CO_NO, PARCEL_ID HAVING n > 1)').fetchone()[0]:,}")

print("\n=== per county ===")
county = con.execute("""
    SELECT CO_NO::INT co_no, count(*) parcels,
           string_agg(DISTINCT ASMNT_YR::INT::VARCHAR, ',') asmnt_yr,
           round(sum(JV) / 1e9, 1) jv_billion,
           round(100.0 * count(*) FILTER (WHERE trim(PHY_ADDR1) = '' OR PHY_ADDR1 IS NULL) / count(*), 1) pct_no_addr,
           round(100.0 * count(*) FILTER (WHERE trim(OWN_NAME) = '' OR OWN_NAME IS NULL) / count(*), 1) pct_no_owner,
           round(100.0 * count(*) FILTER (WHERE trim(DOR_UC) = '' OR DOR_UC IS NULL) / count(*), 1) pct_no_uc
    FROM p GROUP BY 1 ORDER BY 1
""").df()
county.to_csv(OUT / "per_county.csv", index=False)
print(county.to_string(index=False))

print("\n=== DOR_UC (land use) ===")
uc = con.execute("""
    SELECT DOR_UC, count(*) n, round(100.0 * count(*) / sum(count(*)) OVER (), 2) pct
    FROM p GROUP BY 1 ORDER BY 1
""").df()
uc.to_csv(OUT / "dor_uc.csv", index=False)
print(uc.to_string(index=False))
