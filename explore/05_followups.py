"""Targeted follow-ups on anomalies found by the profiles.

usage: python -I 05_followups.py <parcels.parquet>
"""
import sys

import duckdb

SRC = sys.argv[1]
con = duckdb.connect()
con.execute("SET enable_geoparquet_conversion = false")  # keep geometry as raw WKB BLOB
con.execute(f"""
    CREATE VIEW p AS SELECT *, octet_length(geometry) wkb_len FROM read_parquet('{SRC}')
""")


def show(title, sql):
    print(f"\n=== {title} ===")
    print(con.execute(sql).df().to_string(index=False, max_colwidth=40))


show("CO_NO = 0: sample of what's there", """
    SELECT * FROM (SELECT PARCELNO, OID_, Shape_Area, wkb_len FROM p WHERE CO_NO = 0)
    USING SAMPLE 15 ROWS
""")
show("CO_NO = 0: PARCELNO shape patterns", """
    SELECT regexp_replace(regexp_replace(PARCELNO, '[0-9]', '9', 'g'), '[A-Za-z]', 'A', 'g') pattern,
           count(*) n
    FROM p WHERE CO_NO = 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 15
""")
show("CO_NO = 0: non-numeric PARCELNO values", """
    SELECT PARCELNO, count(*) n FROM p
    WHERE CO_NO = 0 AND NOT regexp_matches(PARCELNO, '[0-9]') GROUP BY 1 ORDER BY 2 DESC LIMIT 15
""")
show("CO_NO = 0: area and size", """
    SELECT quantile_cont(Shape_Area, [0.1, 0.5, 0.9, 0.99]) area_m2_q, max(wkb_len) max_wkb,
           sum(wkb_len) / 1e6 total_wkb_mb
    FROM p WHERE CO_NO = 0
""")

show("repeated (CO_NO, PARCEL_ID): biggest groups, same or different shapes?", """
    SELECT CO_NO, PARCEL_ID, any_value(DOR_UC) uc, any_value(OWN_NAME) owner,
           count(*) n, count(DISTINCT md5(geometry)) distinct_shapes,
           round(sum(Shape_Area)) total_area_m2
    FROM p WHERE CO_NO <> 0
    GROUP BY 1, 2 HAVING count(*) > 1 ORDER BY n DESC LIMIT 12
""")
show("repeated (CO_NO, PARCEL_ID): split by same-shape vs different-shape", """
    WITH g AS (
        SELECT CO_NO, PARCEL_ID, count(*) n, count(DISTINCT md5(geometry)) shapes
        FROM p WHERE CO_NO <> 0 GROUP BY 1, 2 HAVING count(*) > 1
    )
    SELECT CASE WHEN shapes = 1 THEN 'identical shape' WHEN shapes = n THEN 'all different shapes'
                ELSE 'mixed' END kind,
           count(*) parcel_ids, sum(n) rows
    FROM g GROUP BY 1 ORDER BY 3 DESC
""")

show("biggest stacks of identical shapes", """
    SELECT md5(geometry) h, any_value(CO_NO) co, count(*) n, count(DISTINCT PARCEL_ID) ids,
           mode(DOR_UC) top_uc, any_value(PHY_ADDR1) addr, round(any_value(Shape_Area)) area_m2
    FROM p GROUP BY 1 HAVING count(*) > 1 ORDER BY n DESC LIMIT 10
""")

# MATERIALIZED sidesteps a DuckDB 2.0 late-materialization bug on ORDER BY ... LIMIT over this file.
show("heaviest geometries", """
    WITH t AS MATERIALIZED (
        SELECT CO_NO, PARCEL_ID, PARCELNO, DOR_UC, OWN_NAME, Shape_Area, wkb_len FROM p
    )
    SELECT CO_NO, PARCEL_ID, PARCELNO, DOR_UC, OWN_NAME, round(Shape_Area / 1e6, 2) area_km2,
           wkb_len // 16 approx_vertices
    FROM t ORDER BY wkb_len DESC LIMIT 12
""")

show("exact duplicates: same county, parcel id and shape (e.g. the 2,106 stack = 1,053 ids x2)", """
    WITH d AS (
        SELECT CO_NO, PARCEL_ID, md5(geometry) h, count(*) n FROM p WHERE CO_NO <> 0
        GROUP BY 1, 2, 3 HAVING count(*) > 1
    )
    SELECT CO_NO::INT co_no, count(*) dup_groups, sum(n - 1) extra_rows FROM d
    GROUP BY 1 ORDER BY 3 DESC LIMIT 10
""")

show("JV for condo units vs others (to judge aggregating stacks)", """
    SELECT DOR_UC = '004' is_condo, count(*) n, round(avg(JV)) avg_jv, round(median(JV)) med_jv
    FROM p WHERE CO_NO <> 0 GROUP BY 1
""")
