// Public routing API. One Router holds one graph per profile, built lazily.
//
// What this fixes from the prototype:
//   - the visitor's GPS fix can be a route ORIGIN, not just a dot on the map
//   - the step-free profile genuinely excludes steps, because its graph never
//     contains them (the prototype flipped a boolean and left steps routable)
//   - routes carry a walking-time estimate with a stated pace
//   - a disconnected pair of stops is reported, never bridged

import { buildGraph, components } from './graph.js';
import { buildIndex, nearest } from './snap.js';
import { shortestPath } from './search.js';
import { profile as elevationProfile, combine } from './terrain.js';
import { lineLength } from './geo.js';

export class Router {
  constructor(paths, config, elevation) {
    this.paths = paths;
    this.config = config;
    // key -> ground metres. Optional: without it the router simply omits
    // ascent and gradient from its results.
    this.elevations = new Map(Object.entries(elevation?.nodes || {}));
    this.elevationSource = elevation?.source || null;
    this.cache = new Map(); // profile -> { graph, index, components }
  }

  profileNames() {
    return Object.keys(this.config.profiles);
  }

  profileConfig(profile) {
    return this.config.profiles[profile] || this.config.profiles[this.config.default_profile];
  }

  graphFor(profile) {
    if (!this.cache.has(profile)) {
      const graph = buildGraph(this.paths, profile, this.config.coordinate_precision);
      this.cache.set(profile, {
        graph,
        index: buildIndex(graph),
        components: components(graph),
      });
    }
    return this.cache.get(profile);
  }

  /** Snap a [lng, lat] to the profile's graph, with the distance it had to move. */
  snap(profile, position) {
    const { index } = this.graphFor(profile);
    const hit = nearest(index, position);
    if (!hit) return null;
    const { warn_distance_m, max_distance_m } = this.config.snap;
    return {
      ...hit,
      warn: hit.metres > warn_distance_m,
      tooFar: hit.metres > max_distance_m,
    };
  }

  minutes(profile, metres) {
    const pace = this.profileConfig(profile).pace_m_per_min;
    const round = this.config.round_time_to_min || 1;
    return Math.max(round, Math.round(metres / pace / round) * round);
  }

  /**
   * Route through an ordered list of { id, name, position } waypoints.
   * Returns legs, totals and any warnings. Never invents a connection.
   */
  route(profile, waypoints) {
    const { graph, components: comp } = this.graphFor(profile);
    const warnings = [];
    const legs = [];
    let metres = 0;

    const snapped = waypoints.map((w, i) => {
      const hit = this.snap(profile, w.position);
      if (!hit) {
        warnings.push(`No routable path anywhere near "${w.name}".`);
      } else if (hit.tooFar) {
        warnings.push(
          `"${w.name}" is ${Math.round(hit.metres)} m from the nearest ${
            this.profileConfig(profile).label.toLowerCase()
          } path and cannot be routed to.`
        );
      } else if (hit.warn) {
        warnings.push(
          `"${w.name}" is ${Math.round(hit.metres)} m from a mapped path; the final approach is not routed.`
        );
      }
      return { ...w, order: i + 1, snap: hit };
    });

    for (let i = 1; i < snapped.length; i += 1) {
      const from = snapped[i - 1];
      const to = snapped[i];
      if (!from.snap || !to.snap || from.snap.tooFar || to.snap.tooFar) {
        legs.push({ from, to, path: null, reason: 'unsnapped' });
        continue;
      }
      const sameComponent =
        comp.componentOf.get(from.snap.key) === comp.componentOf.get(to.snap.key);
      if (!sameComponent) {
        legs.push({ from, to, path: null, reason: 'disconnected' });
        warnings.push(
          `No connected ${this.profileConfig(profile).label.toLowerCase()} path between "${from.name}" and "${to.name}".`
        );
        continue;
      }
      const path = shortestPath(graph, from.snap.key, to.snap.key);
      if (!path) {
        legs.push({ from, to, path: null, reason: 'no-path' });
        warnings.push(`Could not route between "${from.name}" and "${to.name}".`);
        continue;
      }
      metres += path.metres;
      const terrain = this.elevations.size
        ? elevationProfile(path.coordinates, path.keys, this.elevations, this.config.gradient)
        : null;
      legs.push({ from, to, path, terrain, reason: null });
    }

    const terrain = this.elevations.size ? combine(legs.map((l) => l.terrain)) : null;
    const gradientCfg = this.config.gradient;
    if (terrain && gradientCfg?.enabled && terrain.steepest > gradientCfg.warn_above) {
      warnings.push(
        `Includes a section of about ${Math.round(terrain.steepest * 100)}% gradient. ` +
        'Terrain is estimated from a public elevation model and is not surveyed.'
      );
    }

    return {
      profile,
      profileLabel: this.profileConfig(profile).label,
      stops: snapped,
      legs,
      metres: Math.round(metres),
      minutes: metres > 0 ? this.minutes(profile, metres) : 0,
      paceAssumption: this.profileConfig(profile).pace_m_per_min,
      terrain,
      elevationSource: this.elevationSource,
      connected: legs.every((l) => l.path) && legs.length > 0,
      warnings,
    };
  }

  /** GeoJSON for the host app, with provenance and every caveat attached. */
  toGeoJSON(result) {
    const features = [];
    result.stops.forEach((stop) => {
      features.push({
        type: 'Feature',
        id: stop.id,
        properties: {
          kind: 'stop',
          order: stop.order,
          name: stop.name,
          osm_id: stop.id,
          audio_url: null,
          snap_distance_m: stop.snap ? Math.round(stop.snap.metres) : null,
          path_coordinate: stop.snap ? stop.snap.coordinate : null,
        },
        geometry: { type: 'Point', coordinates: stop.position },
      });
    });
    result.legs.forEach((leg, i) => {
      if (!leg.path) return;
      features.push({
        type: 'Feature',
        properties: {
          kind: 'leg',
          segment: i + 1,
          from: leg.from.id,
          to: leg.to.id,
          length_m: Math.round(leg.path.metres),
        },
        geometry: { type: 'LineString', coordinates: leg.path.coordinates },
      });
    });
    return {
      type: 'FeatureCollection',
      name: 'Mount Vernon route',
      metadata: {
        source: 'OpenStreetMap contributors',
        license: 'ODbL-1.0',
        status: 'Unvalidated planning preview',
        profile: result.profile,
        profile_label: result.profileLabel,
        distance_m: result.metres,
        duration_min_estimate: result.minutes,
        pace_m_per_min: result.paceAssumption,
        ascent_m: result.terrain?.ascent ?? null,
        descent_m: result.terrain?.descent ?? null,
        steepest_gradient: result.terrain
          ? Math.round(result.terrain.steepest * 1000) / 1000
          : null,
        elevation_source: result.elevationSource,
        gradient_is_advisory: true,
        connected: result.connected,
        final_approaches_routed: false,
        step_free_verified: false,
        surveyed: false,
        warnings: result.warnings,
      },
      features,
    };
  }

  /** Route line as one GeoJSON source, for drawing. */
  toLineCollection(result) {
    return {
      type: 'FeatureCollection',
      features: result.legs
        .filter((l) => l.path)
        .map((l, i) => ({
          type: 'Feature',
          properties: { segment: i + 1, length_m: Math.round(l.path.metres) },
          geometry: { type: 'LineString', coordinates: l.path.coordinates },
        })),
    };
  }
}

export { lineLength };
