/*
 * Florida Parcels web map.
 *
 * MapLibre GL JS draws the parcel vector tiles straight from a single PMTiles archive through the
 * `pmtiles` protocol plugin (HTTP Range requests, no tile server). Mapbox GL JS can't do this: it has
 * no custom-protocol hook. Mapbox is used only for the optional basemap, as raster tiles from its
 * Static Tiles API, which Mapbox documents for use in third-party map libraries; its terms then
 * require the Mapbox logo and "© Mapbox © OpenStreetMap / Improve this map" attribution, shown below.
 *
 * Everything deployment-specific (token, tile URL, start view) comes from window.CONFIG, see
 * config.example.js. The tile schema is documented in the repository README ("Tile schema").
 */

// Keep this version in sync with the stylesheet link in index.html.
import * as maplibregl from 'https://cdn.jsdelivr.net/npm/maplibre-gl@6.11.2/dist/maplibre-gl.mjs';

const PARCELS = 'parcels';  // source id, and the MVT layer name inside the archive
const BASEMAP = 'basemap';  // source and layer id of the Mapbox raster basemap
const FALLBACK_MIN_ZOOM = 12;  // the archive's own minzoom replaces this once its header is read
const FALLBACK_VIEW = { center: [-81.6, 28.1], zoom: 6.5 };  // all of Florida
const DEFAULT_OPACITY = 0.55;

// DOR land use groups (src/flparcels/codes.py). Colours follow the usual planning conventions
// (residential yellow, commercial red, industrial purple, ...) using the Okabe-Ito palette, which
// stays distinguishable with common colour vision deficiencies; the detail panel also names the use.
const LAND_USE = [
  ['Residential', '#F0E442'],
  ['Commercial', '#D55E00'],
  ['Industrial', '#CC79A7'],
  ['Agricultural', '#009E73'],
  ['Institutional', '#0072B2'],
  ['Governmental', '#56B4E9'],
  ['Miscellaneous', '#E69F00'],
  ['Centrally Assessed', '#7A4A1E'],
  ['Non-Agricultural Acreage', '#BDB76B'],
];
const LAND_USE_COLOR = Object.fromEntries(LAND_USE);
const NO_RECORD = { label: 'No tax roll record', color: '#9AA0A6' };

// Unmatched polygons (roads, water, right-of-way) carry matched=false and no land use.
const FILL_COLOR = [
  'case',
  ['==', ['get', 'matched'], false], NO_RECORD.color,
  ['match', ['get', 'land_use_group'], ...LAND_USE.flat(), NO_RECORD.color],
];

const link = (href, text) => `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
const MAPBOX_CREDITS = `${link('https://www.mapbox.com/about/maps/', '© Mapbox')} ${link('https://www.openstreetmap.org/copyright', '© OpenStreetMap')}`;
// Mapbox's terms ask for this link in the lower right corner, which is where the attribution control sits.
const MAPBOX_FEEDBACK = `<strong>${link('https://apps.mapbox.com/feedback/', 'Improve this map')}</strong>`;

// Mapbox-owned styles that the Static Tiles API can render (it does not support Mapbox Standard).
const BASEMAPS = {
  satellite: { label: 'Satellite', style: 'mapbox/satellite-streets-v12', dark: true, credits: link('https://www.maxar.com/', '© Maxar') },
  streets: { label: 'Streets', style: 'mapbox/streets-v12' },
  light: { label: 'Light', style: 'mapbox/light-v11' },
  none: { label: 'None' },
};

const $ = (id) => document.getElementById(id);
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

const state = {
  map: null,
  token: null,          // validated Mapbox public token, or null for no basemap
  basemap: 'none',
  opacity: DEFAULT_OPACITY,
  minZoom: FALLBACK_MIN_ZOOM,
  hasParcels: false,
  hoverId: null,
  selectedId: null,
};

main();

async function main() {
  setupChrome();
  const { config, source } = await loadConfig();

  if (source === 'example') {
    notify('config', 'No `config.js` found, so the defaults from `config.example.js` are in use. Copy it to `config.js` to set your own token and tile URL.');
  } else if (!source) {
    notify('config', 'No configuration found. Copy `config.example.js` to `config.js` and set `PMTILES_URL`.', 'error');
  }

  state.token = checkToken(config.MAPBOX_TOKEN);
  buildBasemapToggle();

  let map;
  try {
    map = state.map = new maplibregl.Map({
      container: 'map',
      // An empty style with a plain background; the basemap and parcels are added on load.
      style: {
        version: 8,
        sources: {},
        layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e8eaed' } }],
      },
      ...initialView(config.INITIAL_VIEW),
      minZoom: 4,
      maxZoom: 20,  // the parcel archive stops at z16; MapLibre overzooms its tiles beyond that
      hash: true,   // #zoom/lat/lng in the URL, so views can be shared
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
    });
  } catch (err) {
    // MapLibre 6 requires WebGL2 and throws from the constructor without it.
    notify('fatal', `The map can't start in this browser (${err.message}). It needs WebGL2.`, 'error');
    return;
  }
  map.touchZoomRotate.disableRotation();

  // Bottom positions stack upwards, so the logo goes in first to sit at the very bottom.
  state.logo = new MapboxLogoControl();
  map.addControl(state.logo, 'bottom-left');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left');
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');

  map.on('error', onMapError);

  map.on('load', async () => {
    // The basemap goes in first; parcel layers are then appended above it.
    setBasemap(Object.hasOwn(BASEMAPS, config.DEFAULT_BASEMAP ?? '') ? config.DEFAULT_BASEMAP : 'satellite');
    const url = typeof config.PMTILES_URL === 'string' ? config.PMTILES_URL.trim() : '';
    if (!url) {
      if (source) notify('pmtiles-url', '`PMTILES_URL` is not set in the config, so there are no parcels to show.', 'error');
    } else if (!window.pmtiles) {
      notify('pmtiles-lib', 'The PMTiles library failed to load from the CDN, so parcels can\'t be shown.', 'error');
    } else {
      await addParcels(new URL(url, window.location.href).href);
    }
    updateZoomHint();
  });

  map.on('zoom', updateZoomHint);
  // A hover belongs to the pointer position; once the map moves under it, drop it.
  map.on('movestart', () => {
    setHover(null);
    hideTip();
  });
}

/* ---------- configuration ---------- */

async function loadConfig() {
  if (window.CONFIG) return { config: window.CONFIG, source: 'config' };
  // config.js is gitignored, so a fresh clone or a plain static deploy won't have it.
  try {
    await loadScript('config.example.js');
  } catch {
    // reported by the caller
  }
  return window.CONFIG ? { config: window.CONFIG, source: 'example' } : { config: {}, source: null };
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}

function checkToken(raw) {
  const token = typeof raw === 'string' ? raw.trim() : '';
  if (!token) {
    notify('token', 'No Mapbox token is configured, so parcels are drawn on a plain background. Add a public `pk.` token as `MAPBOX_TOKEN` in `config.js` for a basemap.');
    return null;
  }
  if (token.startsWith('sk.')) {
    notify('token', '`MAPBOX_TOKEN` is a secret (`sk.`) token and was not used. Secret tokens must never be put in a web page: revoke it in your Mapbox account and use a public `pk.` token.', 'error');
    return null;
  }
  if (!token.startsWith('pk.')) {
    notify('token', '`MAPBOX_TOKEN` doesn\'t look like a Mapbox public token (`pk.…`), so the basemap is off.', 'warn');
    return null;
  }
  return token;
}

function initialView(view) {
  const center = view?.center;
  const valid = Array.isArray(center) && center.length === 2 && center.every(Number.isFinite) && Number.isFinite(view.zoom);
  return valid ? { center, zoom: view.zoom } : FALLBACK_VIEW;
}

/* ---------- parcels ---------- */

async function addParcels(url) {
  const map = state.map;
  // One archive instance shared by the protocol and the metadata check below, so the header and
  // directory bytes are fetched once.
  const archive = new window.pmtiles.PMTiles(url);
  const protocol = new window.pmtiles.Protocol();
  protocol.add(archive);
  maplibregl.addProtocol('pmtiles', protocol.tile);

  // fid is meant to be the MVT feature id. When tippecanoe can't use it as one (it skips a
  // stringified number), fid stays a plain attribute instead; promote it so feature-state still has
  // an id to key on. The archive's layer metadata says which case this is.
  let promoteId = null;
  try {
    const metadata = await archive.getMetadata();
    const fields = metadata?.vector_layers?.find((layer) => layer.id === PARCELS)?.fields ?? {};
    if ('fid' in fields) promoteId = { [PARCELS]: 'fid' };
  } catch {
    // An unreachable archive is reported by the source's own error event below.
  }

  // The protocol answers with TileJSON built from the archive header (bounds, minzoom 12, maxzoom 16).
  map.addSource(PARCELS, {
    type: 'vector',
    url: `pmtiles://${url}`,
    attribution: 'Parcels: Florida Department of Revenue',
    ...(promoteId && { promoteId }),
  });

  const from = { source: PARCELS, 'source-layer': PARCELS };
  const flag = (name) => ['boolean', ['feature-state', name], false];

  map.addLayer({
    id: 'parcels-fill', type: 'fill', ...from,
    paint: { 'fill-color': FILL_COLOR, 'fill-opacity': fillOpacity(state.opacity) },
  });
  // Outlines fade in from z14, where parcels are big enough on screen for edges to help.
  map.addLayer({
    id: 'parcels-outline', type: 'line', ...from, minzoom: 13.5,
    paint: {
      'line-color': 'rgba(29, 36, 51, 0.45)',
      'line-width': ['interpolate', ['linear'], ['zoom'], 14, 0.4, 16, 0.9, 19, 1.6],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 13.5, 0, 14.5, 1],
    },
  });
  map.addLayer({
    id: 'parcels-hover', type: 'line', ...from,
    paint: { 'line-color': '#1d2433', 'line-width': 2, 'line-opacity': ['case', flag('hover'), 1, 0] },
  });
  // A dark casing under a white core reads as "selected" on every basemap and fill colour.
  map.addLayer({
    id: 'parcels-selected-casing', type: 'line', ...from,
    paint: { 'line-color': '#000000', 'line-width': 6, 'line-opacity': ['case', flag('selected'), 0.55, 0] },
  });
  map.addLayer({
    id: 'parcels-selected', type: 'line', ...from,
    paint: { 'line-color': '#ffffff', 'line-width': 2.5, 'line-opacity': ['case', flag('selected'), 1, 0] },
  });
  state.hasParcels = true;
  applyEdgeContrast();

  map.on('sourcedata', (e) => {
    if (e.sourceId !== PARCELS || e.sourceDataType !== 'metadata') return;
    const minzoom = map.getSource(PARCELS)?.minzoom;
    if (Number.isFinite(minzoom)) {
      state.minZoom = minzoom;
      updateZoomHint();
    }
  });

  const hoverCapable = window.matchMedia('(hover: hover)').matches;
  map.on('mousemove', 'parcels-fill', (e) => {
    const feature = pick(e.features);
    setHover(feature.id);
    map.getCanvas().style.cursor = 'pointer';
    if (hoverCapable) showTip(e.point, tipText(feature.properties));
  });
  map.on('mouseleave', 'parcels-fill', () => {
    setHover(null);
    map.getCanvas().style.cursor = '';
    hideTip();
  });
  map.on('click', (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: ['parcels-fill'] });
    if (hits.length) select(pick(hits));
    else clearSelection();
  });
}

function fillOpacity(base) {
  const lift = Math.min(1, base + 0.2);
  return ['case', ['boolean', ['feature-state', 'hover'], false], lift, ['boolean', ['feature-state', 'selected'], false], lift, base];
}

// Parcels can nest (a unit footprint inside its common-area parcel); prefer the smallest one
// under the pointer so the inner parcels stay reachable.
function pick(features) {
  const size = (f) => num(f.properties.acres) ?? Infinity;
  return features.reduce((best, f) => (size(f) < size(best) ? f : best));
}

// Numeric attributes can arrive as numbers or as numeric strings, depending on how tippecanoe typed
// the column, so read them through this.
function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function setFeatureState(id, values) {
  // The archive stores fid as the MVT feature id (tippecanoe --use-attribute-for-id=fid).
  if (id === undefined || id === null) return;
  state.map.setFeatureState({ source: PARCELS, sourceLayer: PARCELS, id }, values);
}

function setHover(id) {
  if (id === state.hoverId) return;
  setFeatureState(state.hoverId, { hover: false });
  state.hoverId = id ?? null;
  setFeatureState(state.hoverId, { hover: true });
}

function select(feature) {
  if (feature.id !== state.selectedId) {
    setFeatureState(state.selectedId, { selected: false });
    state.selectedId = feature.id ?? null;
    setFeatureState(state.selectedId, { selected: true });
  }
  renderDetails(feature.properties, feature.id ?? feature.properties.fid);
}

function clearSelection() {
  setFeatureState(state.selectedId, { selected: false });
  state.selectedId = null;
  $('details').hidden = true;
  document.body.classList.remove('has-details');
}

/* ---------- basemap ---------- */

function buildBasemapToggle() {
  const group = $('basemap-toggle');
  for (const [key, basemap] of Object.entries(BASEMAPS)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = basemap.label;
    button.dataset.basemap = key;
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => setBasemap(key));
    group.append(button);
  }
  updateBasemapButtons();
}

function updateBasemapButtons() {
  for (const button of $('basemap-toggle').children) {
    const needsToken = Boolean(BASEMAPS[button.dataset.basemap].style);
    button.disabled = needsToken && !state.token;
    button.title = button.disabled ? 'Needs a Mapbox token in config.js' : '';
    button.setAttribute('aria-pressed', String(button.dataset.basemap === state.basemap));
  }
}

// Static Tiles API raster tiles. 512 px is Mapbox's default size (256 px tiles take four times the
// billed requests for the same area) and matches MapLibre's tile grid; @2x serves high-DPI screens.
function mapboxTileUrl(style, token) {
  const scale = window.devicePixelRatio > 1 ? '@2x' : '';
  return `https://api.mapbox.com/styles/v1/${style}/tiles/512/{z}/{x}/{y}${scale}?access_token=${encodeURIComponent(token)}`;
}

function setBasemap(key) {
  const map = state.map;
  if (!Object.hasOwn(BASEMAPS, key) || (BASEMAPS[key].style && !state.token)) key = 'none';
  const basemap = BASEMAPS[key];

  // Swap the source rather than hiding layers, so the attribution always matches what is on screen.
  if (map.getLayer(BASEMAP)) map.removeLayer(BASEMAP);
  if (map.getSource(BASEMAP)) map.removeSource(BASEMAP);
  if (basemap.style) {
    map.addSource(BASEMAP, {
      type: 'raster',
      tiles: [mapboxTileUrl(basemap.style, state.token)],
      tileSize: 512,
      maxzoom: 22,
      attribution: [MAPBOX_CREDITS, basemap.credits, MAPBOX_FEEDBACK].filter(Boolean).join(' '),
    });
    map.addLayer({ id: BASEMAP, type: 'raster', source: BASEMAP }, map.getLayer('parcels-fill') ? 'parcels-fill' : undefined);
  }
  state.logo.setVisible(Boolean(basemap.style));
  state.basemap = key;
  applyEdgeContrast();
  updateBasemapButtons();
}

// Parcel edges need to contrast with the basemap: light on imagery, dark elsewhere.
function applyEdgeContrast() {
  if (!state.hasParcels) return;
  const dark = Boolean(BASEMAPS[state.basemap].dark);
  state.map.setPaintProperty('parcels-outline', 'line-color', dark ? 'rgba(255, 255, 255, 0.7)' : 'rgba(29, 36, 51, 0.45)');
  state.map.setPaintProperty('parcels-hover', 'line-color', dark ? '#ffffff' : '#1d2433');
}

// The required Mapbox logo, shown only while a Mapbox basemap is on the map.
class MapboxLogoControl {
  onAdd() {
    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl mapbox-logo';
    this.container.hidden = true;
    const anchor = document.createElement('a');
    anchor.href = 'https://www.mapbox.com/';
    anchor.target = '_blank';
    anchor.rel = 'noopener';
    const img = document.createElement('img');
    img.src = 'mapbox-logo.svg';
    img.alt = 'Mapbox';
    img.width = 81;
    img.height = 20;
    anchor.append(img);
    this.container.append(anchor);
    return this.container;
  }

  onRemove() {
    this.container.remove();
  }

  setVisible(visible) {
    this.container.hidden = !visible;
  }
}

/* ---------- errors ---------- */

function onMapError(e) {
  const error = e.error ?? {};
  const message = redact(error.message ?? String(error));
  if (e.sourceId === BASEMAP) {
    // 401: bad token; 403: URL restrictions (or a privacy extension stripping the Referer header).
    if (error.status === 401 || error.status === 403) setTimeout(() => rejectToken(error.status));
    return;  // an occasional failed basemap tile isn't worth a notice
  }
  if (e.sourceId === PARCELS) {
    notify('parcels', `Parcel tiles couldn't be loaded (${message}). The PMTiles file must be publicly readable, send CORS headers for this site and support HTTP Range requests.`, 'error');
    return;
  }
  console.error('Map error:', message);
}

function rejectToken(status) {
  if (!state.token) return;
  state.token = null;
  setBasemap('none');
  notify('token', status === 403
    ? 'Mapbox refused the token for this site (HTTP 403), so the basemap is off. If the token has URL restrictions, add this site\'s address to them.'
    : 'Mapbox rejected the token (HTTP 401), so the basemap is off. Check `MAPBOX_TOKEN` in `config.js`.', 'warn');
}

// Error messages can contain tile URLs; keep the token out of the page and the console.
function redact(text) {
  return text.replace(/access_token=[^&\s]+/g, 'access_token=…');
}

/* ---------- page chrome ---------- */

function setupChrome() {
  // Legend
  for (const [label, color] of [...LAND_USE, [NO_RECORD.label, NO_RECORD.color]]) {
    const item = document.createElement('li');
    item.append(swatch(color), label);
    $('legend').append(item);
  }

  // Fill opacity
  const slider = $('opacity');
  slider.value = String(Math.round(state.opacity * 100));
  const showOpacity = () => { $('opacity-value').textContent = `${slider.value}%`; };
  showOpacity();
  slider.addEventListener('input', () => {
    state.opacity = Number(slider.value) / 100;
    showOpacity();
    if (state.map?.getLayer('parcels-fill')) state.map.setPaintProperty('parcels-fill', 'fill-opacity', fillOpacity(state.opacity));
  });

  // The controls card starts collapsed on small screens.
  const small = window.matchMedia('(max-width: 759px)');
  setControlsOpen(!small.matches);
  small.addEventListener('change', (e) => setControlsOpen(!e.matches));
  $('controls-toggle').addEventListener('click', () => setControlsOpen($('controls').classList.contains('is-collapsed')));
  // On small screens the notices sit just below the controls card, whatever its current height.
  new ResizeObserver(() => {
    document.documentElement.style.setProperty('--controls-bottom', `${Math.ceil($('controls').getBoundingClientRect().bottom)}px`);
  }).observe($('controls'));

  $('zoom-hint-btn').addEventListener('click', () => state.map?.easeTo({ zoom: state.minZoom + 0.5 }));
  $('details-close').addEventListener('click', clearSelection);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('details').hidden) clearSelection();
  });

  // "Improve this map" should open Mapbox's feedback tool at the current view.
  document.addEventListener('click', (e) => {
    const anchor = e.target instanceof Element && e.target.closest('a[href^="https://apps.mapbox.com/feedback/"]');
    if (!anchor || !state.map) return;
    const { lng, lat } = state.map.getCenter();
    anchor.href = `https://apps.mapbox.com/feedback/#/${lng.toFixed(5)}/${lat.toFixed(5)}/${state.map.getZoom().toFixed(2)}`;
  }, true);
}

function setControlsOpen(open) {
  $('controls').classList.toggle('is-collapsed', !open);
  $('controls-toggle').setAttribute('aria-expanded', String(open));
}

function updateZoomHint() {
  const show = state.hasParcels && state.map.getZoom() < state.minZoom;
  $('zoom-hint').hidden = !show;
  $('zoom-hint-text').textContent = `Parcels appear from zoom ${state.minZoom}`;
}

function swatch(color) {
  const span = document.createElement('span');
  span.className = 'swatch';
  span.style.background = color;
  return span;
}

// Notices are keyed by id, so a repeated problem updates one notice instead of stacking copies.
// `backticks` in the text become <code>; everything is inserted as text, never as HTML.
function notify(id, text, level = 'info') {
  let notice = $('notices').querySelector(`[data-id="${id}"]`);
  if (!notice) {
    notice = document.createElement('div');
    notice.className = 'notice';
    notice.dataset.id = id;
    notice.append(document.createElement('p'), closeButton('Dismiss', () => notice.remove()));
    $('notices').append(notice);
  }
  notice.dataset.level = level;
  const p = notice.querySelector('p');
  p.replaceChildren(...text.split('`').map((part, i) => {
    if (i % 2 === 0) return document.createTextNode(part);
    const code = document.createElement('code');
    code.textContent = part;
    return code;
  }));
}

function closeButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'icon-btn icon-btn--sm';
  button.setAttribute('aria-label', label);
  button.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  button.addEventListener('click', onClick);
  return button;
}

/* ---------- hover tooltip ---------- */

function tipText(p) {
  if (p.matched === false) return p.source_parcel_no ? `${p.source_parcel_no} (no tax roll record)` : NO_RECORD.label;
  return p.site_addr || p.land_use || p.parcel_id || 'Parcel';
}

function showTip(point, text) {
  const tip = $('hover-tip');
  tip.textContent = text;
  tip.hidden = false;
  // Flip to the left of the pointer near the right edge.
  const flip = point.x > window.innerWidth - tip.offsetWidth - 24;
  const x = flip ? point.x - tip.offsetWidth - 12 : point.x + 14;
  tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(point.y + 14)}px)`;
}

function hideTip() {
  $('hover-tip').hidden = true;
}

/* ---------- details panel ---------- */

function renderDetails(p, fid) {
  const matched = p.matched !== false;
  const units = Math.max(1, num(p.units) ?? 1);
  const stack = units > 1;

  $('details-swatch').style.background = matched ? (LAND_USE_COLOR[p.land_use_group] ?? NO_RECORD.color) : NO_RECORD.color;
  $('details-group').textContent = matched ? (p.land_use_group ?? 'Unknown land use') : NO_RECORD.label;
  let title = p.site_addr;
  if (!title && !matched) title = p.source_parcel_no ?? 'Unmatched polygon';
  if (!title && stack) title = `${p.land_use ?? 'Unit'} stack, ${int.format(units)} records`;
  if (!title) title = p.parcel_id ? `Parcel ${p.parcel_id}` : 'No situs address';
  $('details-title').textContent = title;
  const zip = ['FL', p.site_zip].filter(Boolean).join(' ');
  $('details-sub').textContent = p.site_city || p.site_zip
    ? [p.site_city, zip].filter(Boolean).join(', ')
    : (p.county ? `${p.county} County` : '');

  const area = num(p.acres);
  const acres = area !== null ? `${area.toLocaleString('en-US', { maximumFractionDigits: area < 10 ? 3 : 1 })} acres` : null;
  const money = (v) => (num(v) !== null ? usd.format(num(v)) : null);
  const count = (v, unit = '') => (num(v) !== null ? `${int.format(num(v))}${unit}` : null);
  let groups;
  if (matched) {
    const sale = money(p.sale_price) ? `${money(p.sale_price)}${p.sale_year ? ` (${p.sale_year})` : ''}` : 'None recorded';
    groups = [
      [
        ['Land use', p.land_use ? `${p.land_use}${p.dor_uc ? ` (${p.dor_uc})` : ''}` : p.dor_uc],
        ['Parcel ID', p.parcel_id],
        ['State parcel ID', p.state_parcel_id],
        ['County', p.county],
      ],
      [
        [stack ? 'Just value, total' : 'Just (market) value', money(p.just_value)],
        [stack ? 'Land value, total' : 'Land value', money(p.land_value)],
        // Sales are only in the roll for 2025-2026, and only for single parcels.
        stack ? null : ['Last sale, 2025–26', sale],
      ],
      [
        ['Area', acres],
        [stack ? 'Year built, earliest' : 'Year built', p.year_built],
        [stack ? 'Living area, total' : 'Living area', count(p.living_sqft, ' sq ft')],
        ['Residential units', count(p.res_units)],
        stack ? ['Records on footprint', count(units)] : null,
      ],
    ];
  } else {
    groups = [[['Source label', p.source_parcel_no], ['County', p.county], ['Area', acres]]];
  }
  groups.push([['Feature ID', fid != null ? String(fid) : null, 'is-muted']]);

  const body = $('details-body');
  body.replaceChildren();
  for (const rows of groups) {
    const dl = document.createElement('dl');
    dl.className = 'details__list';
    for (const row of rows) {
      if (!row || row[1] == null || row[1] === '') continue;
      const [label, value, cls] = row;
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = value;
      if (cls) dd.className = cls;
      dl.append(dt, dd);
    }
    if (dl.childElementCount) body.append(dl);
  }

  const note = $('details-note');
  note.hidden = !(stack || !matched);
  note.textContent = stack
    ? `A stack of ${int.format(units)} condo, co-op or mobile home records shares this footprint, so values are totals across them.`
    : 'No tax roll record matches this polygon: usually a road, water body, right-of-way or an unjoined parcel.';

  $('details').hidden = false;
  document.body.classList.add('has-details');
  if (window.matchMedia('(max-width: 759px)').matches) setControlsOpen(false);
}
