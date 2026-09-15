// Map shell: build the style, create the map, generate the layer toggles and the
// legend from real style state, and wire up the tour panel.
//
// The legend is derived from the same fragments that produce the layers, and is
// filtered by live visibility. A legend key describing geometry that is not drawn
// is therefore impossible by construction - that was the prototype's bug.

import { buildStyle } from './style.js';
import { Router } from './routing/index.js';
import { createTour } from './tour.js';
import { createBuildings3DLayer } from './buildings3d/layer.js';

const $ = (id) => document.getElementById(id);

function stripComments(value) {
  if (Array.isArray(value)) return value.map(stripComments);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([k]) => !k.startsWith('$comment'))
        .map(([k, v]) => [k, stripComments(v)])
    );
  }
  return value;
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function swatch(entry) {
  const el = document.createElement('i');
  el.className = `swatch swatch-${entry.swatch || 'area'}`;
  if (entry.swatch === 'dot') {
    el.style.background = entry.color;
  } else if (entry.swatch === 'area') {
    el.style.background = entry.color;
    if (entry.casing) el.style.borderColor = entry.casing;
  } else {
    el.style.background = entry.color;
    if (entry.casing) el.style.boxShadow = `0 0 0 1.5px ${entry.casing}`;
    if (entry.swatch === 'line-dashed') {
      el.style.background = `repeating-linear-gradient(90deg, ${entry.color} 0 7px, transparent 7px 11px)`;
    }
    if (entry.swatch === 'line-rungs') {
      el.style.background = `repeating-linear-gradient(90deg, ${entry.color} 0 3px, transparent 3px 6px)`;
    }
  }
  return el;
}

function renderLegend(index, visible) {
  const host = $('legend-body');
  host.replaceChildren();
  let shown = 0;
  for (const fragment of index) {
    if (!visible.has(fragment.name) || !fragment.legend.length) continue;
    shown += 1;
    const group = document.createElement('div');
    group.className = 'legend-group';
    const heading = document.createElement('h4');
    heading.textContent = fragment.label;
    group.append(heading);
    for (const entry of fragment.legend) {
      const row = document.createElement('span');
      row.className = 'legend-row';
      row.append(swatch(entry), document.createTextNode(entry.label));
      group.append(row);
    }
    host.append(group);
  }
  if (!shown) {
    const empty = document.createElement('p');
    empty.className = 'fine';
    empty.textContent = 'All layers hidden.';
    host.append(empty);
  }
}

function renderToggles(map, index, visible, onChange) {
  const host = $('layer-list');
  host.replaceChildren();
  for (const fragment of index) {
    const label = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = visible.has(fragment.name);
    box.onchange = () => {
      const on = box.checked;
      if (on) visible.add(fragment.name);
      else visible.delete(fragment.name);
      for (const id of fragment.layerIds) {
        map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      }
      onChange();
    };
    label.append(box, document.createTextNode(` ${fragment.label}`));
    host.append(label);
  }
}

function status(message, isError = false) {
  const el = $('status');
  el.hidden = !message;
  el.textContent = message || '';
  el.classList.toggle('is-error', Boolean(isError));
  if (message && !isError) setTimeout(() => { if (el.textContent === message) el.hidden = true; }, 4000);
}

async function main() {
  if (typeof maplibregl === 'undefined') {
    throw new Error(
      'MapLibre did not load. Check that vendor/maplibre-gl.js is present and ' +
      'served (look for a 404 in the server log).'
    );
  }

  let built;
  try {
    built = await buildStyle();
  } catch (err) {
    throw new Error(`Style failed to assemble: ${err.message}`);
  }

  const { style, manifest, tokens, layerIndex } = built;
  const view = manifest.view;

  const [routingConfig, paths, elevation] = await Promise.all([
    loadJson('config/routing.json').then(stripComments),
    loadJson('layers/paths.geojson'),
    // Optional: the router degrades to distance-only if this is missing.
    loadJson('data/elevation.json').catch(() => null),
  ]);
  const router = new Router(paths, routingConfig, elevation);

  const map = new maplibregl.Map({
    container: 'map',
    style,
    center: view.center,
    zoom: view.zoom,
    minZoom: view.min_zoom,
    maxZoom: view.max_zoom,
    maxBounds: view.max_bounds,
    attributionControl: { compact: false },
  });

  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-left');

  const geolocate = new maplibregl.GeolocateControl({
    positionOptions: { enableHighAccuracy: true },
    trackUserLocation: true,
    showAccuracyCircle: true,
  });
  map.addControl(geolocate, 'bottom-right');

  const visible = new Set(layerIndex.map((f) => f.name));
  const refreshLegend = () => renderLegend(layerIndex, visible);

  map.on('load', () => {
    renderToggles(map, layerIndex, visible, refreshLegend);
    refreshLegend();
    status('');

    const tour = createTour({ map, router, tokens, onStatus: status });

    geolocate.on('geolocate', (e) => {
      tour.setUserPosition([e.coords.longitude, e.coords.latitude]);
      status('Location found. You can now route from where you are.');
    });

    // Must match layer ids in style/layers/places.json. check_app.py verifies this.
    const PLACE_LAYERS = ['places-highlight-dot', 'places-minor-icon'];
    map.on('click', (e) => {
      const placeHits = map.queryRenderedFeatures(e.point, { layers: PLACE_LAYERS });
      if (placeHits.length) {
        const p = placeHits[0].properties;
        const coordinates = placeHits[0].geometry.coordinates.slice();
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '250px' })
          .setLngLat(coordinates)
          .setHTML(
            `<span class="eyebrow">${p.category || 'Place'}</span>` +
            `<strong>${p.name}</strong>` +
            `<button class="popup-add">Add to route +</button>` +
            `<small>Source: OpenStreetMap · not surveyed</small>`
          )
          .addTo(map);
        popup.getElement().querySelector('.popup-add').onclick = () => {
          tour.addStop({ id: p.id, name: p.name, position: coordinates });
          popup.remove();
        };
        return;
      }
      // Anything else: inspect its classified properties. Useful while the data
      // pipeline is still being tuned.
      const hits = map.queryRenderedFeatures(e.point);
      if (!hits.length) return;
      const p = hits[0].properties;
      const rows = ['name', 'class', 'surface', 'step_free', 'routable', 'height_m', 'source', 'surveyed']
        .filter((k) => p[k] !== undefined && p[k] !== null && p[k] !== '')
        .map((k) => `<div><b>${k}</b><span>${p[k]}</span></div>`)
        .join('');
      new maplibregl.Popup({ closeButton: true, maxWidth: '260px' })
        .setLngLat(e.lngLat)
        .setHTML(`<strong>${p.name || hits[0].layer.id}</strong>${rows}`)
        .addTo(map);
    });

    map.on('mouseenter', 'places-highlight-dot', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'places-highlight-dot', () => { map.getCanvas().style.cursor = ''; });

    // --- 3D buildings ----------------------------------------------------
    // The drawn 2D blocks and the lidar geometry are two views of the same
    // footprints; only one is shown at a time.
    const FLAT_BUILDING_LAYERS = [
      'buildings-wall', 'buildings-wall-outline',
      'buildings-roof', 'buildings-roof-wash', 'buildings-roof-outline',
    ];
    let solid = null;
    let is3D = false;

    async function set3D(on) {
      if (on && !solid) {
        const buildings = await loadJson('layers/buildings.geojson');
        solid = createBuildings3DLayer({
          buildings,
          origin: { lng: view.center[0], lat: view.center[1] },
          tokens,
        });
        map.addLayer(solid);
        status(`3D: ${solid.stats.built} buildings from lidar, ` +
               `${solid.stats.triangles.toLocaleString()} triangles` +
               (solid.stats.skipped ? ` (${solid.stats.skipped} without a measured height)` : ''));
      } else if (on && solid && !map.getLayer('buildings-3d')) {
        map.addLayer(solid);
      } else if (!on && map.getLayer('buildings-3d')) {
        map.removeLayer('buildings-3d');
      }
      for (const id of FLAT_BUILDING_LAYERS) {
        if (map.getLayer(id)) {
          map.setLayoutProperty(id, 'visibility', on ? 'none' : 'visible');
        }
      }
      is3D = on;
      if (on && map.getPitch() < 5) map.easeTo({ pitch: 55, duration: 900 });
      if (!on && map.getPitch() > 5) map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
      $('toggle-3d').classList.toggle('active', on);
      $('toggle-3d').setAttribute('aria-pressed', String(on));
    }

    $('toggle-3d').onclick = () => set3D(!is3D).catch((e) => status(e.message, true));

    window.mountVernonMap = {
      map,
      router,
      tour,
      layerIndex,
      set3D,
      getRoute: () => (tour.result ? router.toGeoJSON(tour.result) : null),
    };
  });

  map.on('error', (e) => status(`Map error: ${e.error?.message || 'see console'}`, true));

  // Both panels occupy the same corner, so opening one closes the other.
  const panels = [['layers-button', 'layers'], ['tour-button', 'tour']];
  for (const [buttonId, panelId] of panels) {
    $(buttonId).onclick = () => {
      const opening = $(panelId).hidden;
      for (const [otherButton, otherPanel] of panels) {
        const show = otherPanel === panelId && opening;
        $(otherPanel).hidden = !show;
        $(otherButton).setAttribute('aria-expanded', String(show));
      }
    };
  }
}

// A silent failure here leaves a blank page and nothing in the server log, so
// surface everything on the page as well as in the console.
function fatal(err) {
  console.error(err);
  const el = $('status');
  if (!el) return;
  el.hidden = false;
  el.classList.add('is-error');
  el.replaceChildren();
  const heading = document.createElement('strong');
  heading.textContent = 'The map failed to start';
  const detail = document.createElement('span');
  detail.textContent = err?.message || String(err);
  const hint = document.createElement('small');
  hint.textContent = 'Full stack trace is in the browser console (F12).';
  el.append(heading, detail, hint);
}

window.addEventListener('error', (e) => fatal(e.error || new Error(e.message)));
window.addEventListener('unhandledrejection', (e) => fatal(e.reason));

main().catch(fatal);
