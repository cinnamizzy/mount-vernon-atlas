// Elevation profile along a route, from data/elevation.json (USGS 3DEP, 1 m).
//
// ADVISORY ONLY. Gradient here never filters the routing graph. Measured across
// this estate the bare-earth DEM gives a median gradient of 2% but a maximum of
// 53.5% on segments that are certainly not 53.5% walkways: short vertex spacing
// amplifies sub-metre elevation noise, and boardwalks, culverts and retaining
// walls read as terrain. Excluding edges above the ADA 8.33% ramp limit would
// leave only 8 of the 16 highlights on one connected network.
//
// So: report ascent and flag steep sections as unverified, and leave real
// step-free determination to an estate-supplied accessible-route inventory.

import { distance } from './geo.js';

/**
 * Ascent, descent and the steepest smoothed gradient along one path.
 * `keys` and `coordinates` come from shortestPath; `elevations` is key -> metres.
 */
export function profile(coordinates, keys, elevations, options) {
  const windowM = options?.smoothing_window_m ?? 10;
  const minSegment = options?.min_segment_m ?? 0.5;

  const points = [];
  for (let i = 0; i < coordinates.length; i += 1) {
    const height = elevations.get(keys[i]);
    if (height === undefined) continue;
    points.push({ coordinate: coordinates[i], height });
  }
  if (points.length < 2) {
    return { ascent: 0, descent: 0, steepest: 0, steepestAt: null, sampled: points.length };
  }

  let ascent = 0;
  let descent = 0;
  for (let i = 1; i < points.length; i += 1) {
    const delta = points[i].height - points[i - 1].height;
    if (delta > 0) ascent += delta;
    else descent -= delta;
  }

  // Steepest gradient measured over a sliding window of at least windowM metres,
  // rather than between adjacent vertices where DEM noise dominates.
  let steepest = 0;
  let steepestAt = null;
  let tail = 0;
  let run = 0;
  for (let head = 1; head < points.length; head += 1) {
    run += distance(points[head - 1].coordinate, points[head].coordinate);
    while (run > windowM && tail < head - 1) {
      const trim = distance(points[tail].coordinate, points[tail + 1].coordinate);
      if (run - trim < windowM) break;
      run -= trim;
      tail += 1;
    }
    if (run < Math.max(windowM, minSegment)) continue;
    const gradient = Math.abs(points[head].height - points[tail].height) / run;
    if (gradient > steepest) {
      steepest = gradient;
      steepestAt = points[head].coordinate;
    }
  }

  return {
    ascent: Math.round(ascent),
    descent: Math.round(descent),
    steepest,
    steepestAt,
    sampled: points.length,
  };
}

/** Combine per-leg profiles into one route-level summary. */
export function combine(profiles) {
  let ascent = 0;
  let descent = 0;
  let steepest = 0;
  let steepestAt = null;
  for (const p of profiles) {
    if (!p) continue;
    ascent += p.ascent;
    descent += p.descent;
    if (p.steepest > steepest) {
      steepest = p.steepest;
      steepestAt = p.steepestAt;
    }
  }
  return { ascent, descent, steepest, steepestAt };
}
