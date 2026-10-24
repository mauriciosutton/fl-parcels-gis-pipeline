# Web map

A static, no-build web map of the parcel tiles this repo produces. [MapLibre GL JS](https://maplibre.org/) reads
the PMTiles archive directly with the [`pmtiles`](https://github.com/protomaps/PMTiles) protocol plugin (plain
HTTP Range requests, no tile server), and an optional Mapbox basemap comes from Mapbox's
[Static Tiles API](https://docs.mapbox.com/api/maps/static-tiles/) as raster tiles. Mapbox GL JS isn't used
because it has no hook for custom protocols, so it can't read PMTiles.

| File | |
|---|---|
| `index.html`, `app.js`, `style.css` | the page |
| `config.example.js` | committed config template |
| `config.js` | your config (gitignored): Mapbox token, PMTiles URL, start view |
| `mapbox-logo.svg` | the Mapbox logo, which Mapbox requires on maps that use its tiles |
| `dev-data/` | local test tiles (gitignored) |

Libraries load from jsDelivr at pinned versions: `maplibre-gl@6.11.2` (an ES module; the stylesheet link in
`index.html` and the import in `app.js` must match) and `pmtiles@4.5.0`. There is no analytics or tracking.

## Run locally

```sh
cp web/config.example.js web/config.js        # then edit it, see below
mkdir -p web/dev-data
scp stellar-tiles:/srv/fl-parcels/out/fl_parcels_co15.pmtiles web/dev-data/   # or any build's .pmtiles
npx http-server@14.1.1 web -p 8080 --cors -c-1
```

Open <http://localhost:8080/>. The server must answer HTTP Range requests with `206 Partial Content`;
`http-server` does, Python's `python -m http.server` does not. Without `config.js` the page falls back to
`config.example.js` and says so (the browser also logs a 404 for `config.js`).

## Config

```js
window.CONFIG = {
  MAPBOX_TOKEN: "pk....",              // public token; empty = no basemap, parcels on a plain background
  PMTILES_URL: "https://.../fl_parcels.pmtiles",
  INITIAL_VIEW: { center: [-80.62, 28.08], zoom: 14 },   // used when the URL has no #zoom/lat/lng
  DEFAULT_BASEMAP: "satellite",       // satellite | streets | light | none
};
```

Everything in `config.js` is public to anyone who loads the page.

## Mapbox token

1. In the Mapbox console, open **Tokens** (<https://console.mapbox.com/account/access-tokens/>) and create a
   token with only the default public scopes; `styles:tiles` is the one the Static Tiles API needs. It starts with
   `pk.`. Never use a secret `sk.` token in a page; the map refuses one.
2. Add **URL restrictions** so the token only works from your site. Browsers send only the origin (not the path)
   as the `Referer` on cross-origin tile requests, so restrict by domain, e.g. `https://<user>.github.io` or
   `https://parcels.example.com`, not by path. Localhost is blocked unless listed, so use a second token restricted
   to `http://localhost:8080` for development. Privacy extensions that strip the `Referer` (Brave Shields,
   Ghostery) make a restricted token fail with 403; the map then turns the basemap off and says why.
3. Static Tiles requests are billed per tile beyond the monthly free tier (see Mapbox pricing). The map requests
   512 px tiles, Mapbox's default; 256 px tiles would cost four times the requests for the same area.

Mapbox's terms require its logo and "© Mapbox © OpenStreetMap" plus an "Improve this map" link in the lower right
corner (and "© Maxar" on satellite imagery). The map shows these whenever a Mapbox basemap is on; keep them.

## Pointing at a bucket or CDN

Set `PMTILES_URL` to the archive's public URL. Whatever serves it must:

- allow anonymous `GET`/`HEAD` reads of the file;
- support HTTP Range requests (respond `206` with `Content-Range`), including through any CDN in front of it;
- send CORS headers for the map's origin: `Access-Control-Allow-Origin` (your site or `*`), and allow the `Range`
  request header (`Access-Control-Allow-Headers: Range`). Exposing `ETag` (`Access-Control-Expose-Headers`) lets
  the client detect a replaced archive.

Check a URL before using it; expect `HTTP/... 206`, a `Content-Range` and an `Access-Control-Allow-Origin` line:

```sh
curl -s -D - -o /dev/null -r 0-126 -H "Origin: https://<user>.github.io" "<PMTILES_URL>"
```

**Supabase Storage.** A public bucket's objects are served at
`https://<project-ref>.supabase.co/storage/v1/object/public/<bucket>/<path>`. Don't make the bucket that
`publish` writes to public as it is: `publish` also uploads `lookup.parquet`, which holds owner names and mailing
addresses. Put the `.pmtiles` file in a separate public bucket (or behind a CDN) and keep the lookup private.
For the statewide archive (several GB), a CDN or object store with cheap egress, such as Cloudflare R2 or S3 behind
CloudFront, is worth considering; PMTiles only ever fetches the byte ranges a view needs.

If the archive can't be read (private bucket, missing CORS, no Range support), the parcels don't draw and the page
shows the error.

## Deploying

The site is plain static files; any static host works. `config.js` is gitignored, so a host that builds from the
repository (e.g. GitHub Pages) won't have it and falls back to `config.example.js`. Either generate `config.js`
in the deploy step (e.g. a Pages workflow writing it from repository variables) or upload it with the other files.

## Notes on the tiles

- Feature-state (hover and selection) is keyed on `fid`. The current archives carry `fid` as a string attribute
  rather than as the MVT feature id, so the map reads the archive metadata and promotes the attribute when it is
  there; it works unchanged once `fid` is a real feature id.
- Numeric attributes may arrive as numbers or numeric strings; the map handles both.
- Tiles exist from zoom 12 (the map shows a hint below that) to 16; beyond 16 MapLibre overzooms them.
