// Tour panel: stop list, profile choice, route summary and export.
//
// Owns no routing logic - it calls into src/routing/ and renders the result.

const $ = (id) => document.getElementById(id);

const ROUTE_SOURCE = 'route-line';
const STOP_SOURCE = 'route-stops';

export function createTour({ map, router, tokens, onStatus }) {
  let stops = [];
  let profile = router.config.default_profile;
  let startFromLocation = false;
  let userPosition = null;
  let last = null;

  map.addSource(ROUTE_SOURCE, { type: 'geojson', data: empty() });
  map.addSource(STOP_SOURCE, { type: 'geojson', data: empty() });

  map.addLayer({
    id: 'route-casing',
    type: 'line',
    source: ROUTE_SOURCE,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': tokens.color.route.casing,
      'line-width': ['interpolate', ['linear'], ['zoom'], 14, 4, 17, 9, 20, 20],
    },
  });
  map.addLayer({
    id: 'route-line',
    type: 'line',
    source: ROUTE_SOURCE,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': tokens.color.route.line,
      'line-width': ['interpolate', ['linear'], ['zoom'], 14, 2, 17, 4.5, 20, 11],
      'line-dasharray': [2.5, 1.4],
    },
  });
  map.addLayer({
    id: 'route-stop-dot',
    type: 'circle',
    source: STOP_SOURCE,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 7, 18, 12],
      'circle-color': tokens.color.route.pin,
      'circle-stroke-color': tokens.color.text.halo,
      'circle-stroke-width': 2.5,
    },
  });
  map.addLayer({
    id: 'route-stop-number',
    type: 'symbol',
    source: STOP_SOURCE,
    layout: {
      'text-field': ['to-string', ['get', 'order']],
      'text-size': 12,
      'text-font': ['Noto Sans Regular'],
      'text-allow-overlap': true,
      'text-ignore-placement': true,
    },
    paint: { 'text-color': '#ffffff' },
  });

  function empty() {
    return { type: 'FeatureCollection', features: [] };
  }

  function waypoints() {
    const list = stops.map((s) => ({ id: s.id, name: s.name, position: s.position }));
    if (startFromLocation && userPosition) {
      list.unshift({ id: 'gps', name: 'Your location', position: userPosition });
    }
    return list;
  }

  function recompute() {
    const points = waypoints();
    if (points.length < 2) {
      last = null;
      map.getSource(ROUTE_SOURCE).setData(empty());
      map.getSource(STOP_SOURCE).setData({
        type: 'FeatureCollection',
        features: points.map((w, i) => ({
          type: 'Feature',
          properties: { order: i + 1, name: w.name },
          geometry: { type: 'Point', coordinates: w.position },
        })),
      });
      render();
      return;
    }
    last = router.route(profile, points);
    map.getSource(ROUTE_SOURCE).setData(router.toLineCollection(last));
    map.getSource(STOP_SOURCE).setData({
      type: 'FeatureCollection',
      features: last.stops.map((s) => ({
        type: 'Feature',
        properties: { order: s.order, name: s.name },
        geometry: { type: 'Point', coordinates: s.position },
      })),
    });
    render();
  }

  function render() {
    const list = $('stop-list');
    list.replaceChildren();

    const points = waypoints();
    points.forEach((w, i) => {
      const li = document.createElement('li');
      const isGps = w.id === 'gps';
      const label = document.createElement('span');
      label.className = 'stop-name' + (isGps ? ' stop-gps' : '');
      label.textContent = `${i + 1}. ${w.name}`;
      li.append(label);
      if (!isGps) {
        const index = stops.findIndex((s) => s.id === w.id);
        const up = document.createElement('button');
        up.textContent = '↑';
        up.title = 'Move earlier';
        up.disabled = index <= 0;
        up.onclick = () => {
          [stops[index - 1], stops[index]] = [stops[index], stops[index - 1]];
          recompute();
        };
        const remove = document.createElement('button');
        remove.textContent = '×';
        remove.title = 'Remove';
        remove.onclick = () => {
          stops.splice(index, 1);
          recompute();
        };
        li.append(up, remove);
      }
      list.append(li);
    });

    const summary = $('route-summary');
    summary.replaceChildren();
    if (!last) {
      const p = document.createElement('p');
      p.className = 'fine';
      p.textContent = points.length
        ? 'Add another place to draw a route.'
        : 'Click a place on the map to begin.';
      summary.append(p);
    } else {
      const headline = document.createElement('p');
      headline.className = 'route-headline';
      headline.textContent = `${last.metres} m · about ${last.minutes} min`;
      summary.append(headline);

      if (last.terrain && (last.terrain.ascent || last.terrain.descent)) {
        const terrain = document.createElement('p');
        terrain.className = 'route-terrain';
        terrain.textContent =
          `↑ ${last.terrain.ascent} m ascent · ↓ ${last.terrain.descent} m descent` +
          (last.terrain.steepest
            ? ` · steepest about ${Math.round(last.terrain.steepest * 100)}%`
            : '');
        summary.append(terrain);
      }

      const pace = document.createElement('p');
      pace.className = 'fine';
      pace.textContent = `${last.profileLabel} pace, ${last.paceAssumption} m/min. Estimate ignores crowds and time spent at stops. Terrain from a public elevation model, not surveyed.`;
      summary.append(pace);
      for (const warning of last.warnings) {
        const w = document.createElement('p');
        w.className = 'route-warning';
        w.textContent = warning;
        summary.append(w);
      }
    }
    $('route-export').disabled = !last || !last.legs.some((l) => l.path);
    $('route-clear').disabled = !stops.length;
    $('stop-count').textContent = String(stops.length);
  }

  function addStop(stop) {
    if (stops.some((s) => s.id === stop.id)) {
      onStatus?.(`"${stop.name}" is already in your route.`);
      return false;
    }
    stops.push(stop);
    recompute();
    return true;
  }

  // --- controls -----------------------------------------------------------

  const profileSelect = $('route-profile');
  profileSelect.replaceChildren();
  for (const name of router.profileNames()) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = router.profileConfig(name).label;
    profileSelect.append(option);
  }
  profileSelect.value = profile;
  profileSelect.onchange = () => {
    profile = profileSelect.value;
    $('profile-note').textContent = router.profileConfig(profile).description;
    recompute();
  };
  $('profile-note').textContent = router.profileConfig(profile).description;

  const gpsToggle = $('route-from-location');
  gpsToggle.onchange = () => {
    startFromLocation = gpsToggle.checked;
    if (startFromLocation && !userPosition) {
      onStatus?.('Use the locate button to find your position first.');
    }
    recompute();
  };

  $('route-clear').onclick = () => {
    stops = [];
    recompute();
  };

  $('route-export').onclick = () => {
    if (!last) return;
    const blob = new Blob([JSON.stringify(router.toGeoJSON(last), null, 2)], {
      type: 'application/geo+json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mount-vernon-route.geojson';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  render();

  return {
    addStop,
    setUserPosition(position) {
      userPosition = position;
      gpsToggle.disabled = false;
      if (startFromLocation) recompute();
    },
    get profile() { return profile; },
    get result() { return last; },
    get stops() { return stops.slice(); },
  };
}
