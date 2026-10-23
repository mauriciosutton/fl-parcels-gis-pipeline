# fl-parcels

Turns the Florida Department of Revenue statewide cadastral download (`Cadastral_Statewide.zip`, a FileGDB of
~11M parcel polygons joined to the 2026 tax roll) into:

- `data/out/fl_parcels.pmtiles`: vector tiles (a single-file PMTiles archive), layer `parcels`, zoom 12–16, public attributes only
- `data/build/fl_parcels/lookup.parquet`: every tax roll record with full detail (owner, values, legal
  description, sale book/page), keyed by the tile feature id `fid`, for click-through lookups

## Pipeline

| Step | Does | Output |
|---|---|---|
| `extract` | unzips the download (skips if already extracted) | `data/raw/*.gdb` |
| `stage` | FileGDB → GeoParquet, Z/M dropped, bbox column added (skips if present; `--force` to redo) | `data/staging/parcels.parquet` (EPSG:6439) |
| `transform` | cleans text, drops duplicates, collapses stacked units, reprojects | `data/build/<name>/features.fgb` (EPSG:4326), `lookup.parquet`, `transform_report.json` |
| `tile` | tippecanoe | `data/out/<name>.pmtiles` |
| `validate` | zoom range, tile sizes vs 500 KB limit, Florida bounds, layer fields | report to stdout, exit 1 on failure |
| `publish` | uploads the PMTiles and `lookup.parquet` to an S3-compatible bucket (not part of `run`) | `s3://<bucket>/<name>/<file>` |
| `run` | extract through validate, in order | |

`--county N` (DOR county number 11–77, e.g. 60 = Palm Beach) builds a single-county pilot named
`fl_parcels_coN`, using the same code path.

`publish` reads its target from the environment, like `FLPARCELS_DATA_DIR`: `FLPARCELS_PUBLISH_ENDPOINT`,
`FLPARCELS_PUBLISH_BUCKET`, `FLPARCELS_PUBLISH_ACCESS_KEY` and `FLPARCELS_PUBLISH_SECRET_KEY`, plus an optional
`FLPARCELS_PUBLISH_REGION` (default `us-east-1`). Pass them to Docker with `-e`.

For Supabase Storage, use the values from Dashboard > Storage > S3 Connection: the endpoint is
`https://<project-ref>.storage.supabase.co/storage/v1/s3`, the region is the project's region, and the keys are an
S3 access key pair (not the anon or service role key). The bucket must already exist, and the PMTiles file
is several GB, so the project's global upload size limit must be raised above it.

## Running on the VM (Docker)

Tippecanoe only builds on Linux/macOS, so the tile step runs in the Docker image, which bundles tippecanoe
2.79.0 and the Python pipeline.

```sh
docker build -t flparcels .
mkdir -p /srv/fl-parcels && cp Cadastral_Statewide.zip /srv/fl-parcels/

# pilot one county first, check the output, then go statewide
docker run --rm -v /srv/fl-parcels:/data flparcels run --zip /data/Cadastral_Statewide.zip --county 60
docker run --rm -v /srv/fl-parcels:/data flparcels run --zip /data/Cadastral_Statewide.zip
```

The tiles land in `/srv/fl-parcels/out/fl_parcels.pmtiles`. Add `--user "$(id -u):$(id -g)"` to keep
file ownership on the host. On a VM with less than ~24 GB RAM, pass `--memory-limit 8GB` (or similar) so
DuckDB spills to disk instead of running out of memory. Extra tippecanoe flags go through
`--tippecanoe-arg=...` (repeatable).

**Resources (statewide)**, measured on a 16-thread, 32 GB machine: stage 7.5 min, transform 6 min.
Disk: zip 2.6 GB, raw GDB 10.5 GB, staged Parquet 4 GB, build outputs 6.4 GB (`features.fgb` 5.9 GB,
`lookup.parquet` 0.5 GB), plus a temporary ~8 GB DuckDB database during transform. Tippecanoe's run time,
temp space and PMTiles size are not measured yet; plan for about 60 GB free in total.

## Running locally (Python steps only)

```sh
uv sync
uv run pytest
uv run python -m flparcels --data-dir data run --zip path/to/Cadastral_Statewide.zip --county 60
```

`run` stops at `tile` if tippecanoe isn't on PATH; everything before it works on Windows.

## Tile schema (layer `parcels`)

Feature id is `fid` (use it for `feature-state` and to look up `lookup.parquet`). Null attributes are omitted.

| Attribute | Meaning |
|---|---|
| `matched` | false for polygons with no tax roll record (roads, water, ROW, unjoined parcels) |
| `units` | records drawn on this footprint; >1 for collapsed condo/co-op/mobile home stacks |
| `parcel_id`, `state_parcel_id` | county parcel id and DOR statewide id (singles only) |
| `source_parcel_no` | the county's raw label on unmatched polygons ("Road", "Water", a parcel number) |
| `county` | county name |
| `dor_uc`, `land_use`, `land_use_group` | DOR land use code, short label, and category (Residential, Commercial, …) |
| `just_value`, `land_value` | market and land value (USD); totals across units for stacks |
| `acres` | footprint area from the geometry |
| `year_built`, `living_sqft`, `res_units` | building attributes; earliest year and totals for stacks |
| `sale_price`, `sale_year` | most recent recorded sale, 2025–2026 only (singles only) |
| `site_addr`, `site_city`, `site_zip` | situs address; for stacks the shared address or street, if any |

Owner names and mailing addresses are deliberately left out of the tiles (anyone who can reach a tile
server can bulk-download every tile); they live in `lookup.parquet`.

## What the transform changes, and why

Counts are from the 2026 file; each run writes its own in `transform_report.json`.

- **Exact duplicates** (same county, parcel id and shape), about 4,200, nearly all in Palm Beach, are dropped.
- **Stacked units**: about 1.17M condo/co-op/mobile home records sit on about 64k shared footprints (one
  Palm Beach outline carries 1,096 units). Each footprint becomes one feature with `units` and totals;
  the unit records stay in the lookup under that `fid`.
- **Unmatched polygons** (`CO_NO = 0`, no tax roll record, ~90k) are kept with `matched=false`, except
  ones that duplicate a matched footprint.
- **Split parcels**: about 95k parcel ids are drawn as several polygons; each polygon is its own feature
  and carries the parcel's values, so don't sum values across features of one `state_parcel_id`.
- Text is whitespace-normalised and blanks become null; numeric zeros the roll uses for "missing" become null.

## Known gaps in the source

- **Miami-Dade condos**: the polygon layer drives the join, and Miami-Dade has one polygon (and one
  record) per condo building, so most of its several hundred thousand condo units are absent. Filling them
  would need the separate DOR NAL file for county 23.
- **Sales** cover 2025–2026 only (about 13% of parcels); it is not a "last sale ever" field.
- **Situs address** is missing for 40–50% of parcels in Hamilton, Levy, Liberty and Suwannee, about 15% in
  Collier, and is near-complete in most metro counties.
- At the low zooms, tippecanoe thins features in dense metros to stay under the tile size limit
  (`--drop-densest-as-needed`); all features are kept at zoom 16.

`explore/` holds the profiling scripts behind these decisions.
