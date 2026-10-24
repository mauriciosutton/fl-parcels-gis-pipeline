// Copy this file to config.js (which is gitignored) and fill it in.
// index.html loads config.js; if it is missing, the map falls back to this file and says so on the page.
window.CONFIG = {
  // Mapbox *public* token (starts with "pk."), used only for the basemap raster tiles.
  // Leave it empty to run without a basemap: the parcels still render on a plain background.
  // Never put a secret ("sk.") token here; everything in this file is visible to every visitor.
  MAPBOX_TOKEN: "",

  // The parcel PMTiles archive. A relative path is resolved against the page URL.
  // The host must allow public reads, send CORS headers and support HTTP Range requests.
  // Supabase Storage public bucket example:
  //   "https://<project-ref>.supabase.co/storage/v1/object/public/<bucket>/fl_parcels/fl_parcels.pmtiles"
  PMTILES_URL: "dev-data/fl_parcels_co15.pmtiles",

  // Starting view, used when the URL has no #zoom/lat/lng hash. Parcels draw from zoom 12.
  INITIAL_VIEW: { center: [-80.62, 28.08], zoom: 14 },

  // One of "satellite", "streets", "light" or "none". Ignored without a token.
  DEFAULT_BASEMAP: "satellite",
};
