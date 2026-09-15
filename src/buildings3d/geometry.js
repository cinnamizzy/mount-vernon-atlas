// Turns a footprint plus its measured height into triangles: walls, and a roof
// whose form comes from the lidar.
//
// Everything is emitted into flat arrays for ONE merged BufferGeometry with
// vertex colours, so 1,150 buildings cost a single draw call. Three's core
// module has no mergeGeometries (that lives in examples/jsm), and building the
// arrays directly is both faster and gives control over normals.
//
// Local frame: x east, y north, z up, metres from the scene origin.

import { ShapeUtils } from '../../vendor/three.module.js';

/** Metres east/north of an origin. Flat-earth is fine over an estate. */
export function toLocal(lng, lat, origin) {
  const mPerDegLat = 111132.92 - 559.82 * Math.cos(2 * origin.lat * Math.PI / 180);
  const mPerDegLng = 111412.84 * Math.cos(origin.lat * Math.PI / 180);
  return [(lng - origin.lng) * mPerDegLng, (lat - origin.lat) * mPerDegLat];
}

function ringArea(ring) {
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [x0, y0] = ring[i];
    const [x1, y1] = ring[(i + 1) % ring.length];
    sum += x0 * y1 - x1 * y0;
  }
  return sum / 2;
}

/** Long axis of a footprint, from the covariance of its vertices. */
function principalAxis(ring) {
  let cx = 0;
  let cy = 0;
  for (const [x, y] of ring) { cx += x; cy += y; }
  cx /= ring.length;
  cy /= ring.length;

  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const [x, y] of ring) {
    const dx = x - cx;
    const dy = y - cy;
    sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
  }
  // Principal eigenvector of the 2x2 covariance matrix, in closed form.
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  return { cx, cy, ax: Math.cos(theta), ay: Math.sin(theta) };
}

class Builder {
  constructor() {
    this.positions = [];
    this.normals = [];
    this.colors = [];
  }

  get triangleCount() { return this.positions.length / 9; }

  tri(a, b, c, colour, flip) {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;
    if (flip && flip(nx, ny, nz)) {
      nx = -nx; ny = -ny; nz = -nz;
      [b, c] = [c, b];
    }
    for (const p of [a, b, c]) {
      this.positions.push(p[0], p[1], p[2]);
      this.normals.push(nx, ny, nz);
      this.colors.push(colour[0], colour[1], colour[2]);
    }
  }

  quad(a, b, c, d, colour, flip) {
    this.tri(a, b, c, colour, flip);
    this.tri(a, c, d, colour, flip);
  }
}

function addWalls(builder, ring, eave, colour) {
  // Outward is away from the footprint centroid.
  let cx = 0;
  let cy = 0;
  for (const [x, y] of ring) { cx += x; cy += y; }
  cx /= ring.length;
  cy /= ring.length;

  for (let i = 0; i < ring.length; i += 1) {
    const [x0, y0] = ring[i];
    const [x1, y1] = ring[(i + 1) % ring.length];
    const mx = (x0 + x1) / 2 - cx;
    const my = (y0 + y1) / 2 - cy;
    builder.quad(
      [x0, y0, 0], [x1, y1, 0], [x1, y1, eave], [x0, y0, eave],
      colour,
      (nx, ny) => nx * mx + ny * my < 0
    );
  }
}

function addFlatRoof(builder, ring, holes, height, colour) {
  const contour = ring.map(([x, y]) => ({ x, y }));
  const holeShapes = holes.map((h) => h.map(([x, y]) => ({ x, y })));
  let faces;
  try {
    faces = ShapeUtils.triangulateShape(contour, holeShapes);
  } catch {
    return;
  }
  const all = contour.concat(...holeShapes);
  for (const [i, j, k] of faces) {
    builder.tri(
      [all[i].x, all[i].y, height],
      [all[j].x, all[j].y, height],
      [all[k].x, all[k].y, height],
      colour,
      (_nx, _ny, nz) => nz < 0
    );
  }
}

/**
 * Gable over the footprint's oriented bounding box, ridge along the long axis.
 *
 * A true gable on an arbitrary polygon needs a straight skeleton; this is the
 * pictorial-map shortcut, and the slight overhang on irregular footprints reads
 * as an eave rather than as an error.
 */
function addGableRoof(builder, ring, eave, ridge, colour) {
  const { cx, cy, ax, ay } = principalAxis(ring);
  const px = -ay;
  const py = ax;

  let uMin = Infinity; let uMax = -Infinity;
  let vMin = Infinity; let vMax = -Infinity;
  for (const [x, y] of ring) {
    const dx = x - cx;
    const dy = y - cy;
    const u = dx * ax + dy * ay;
    const v = dx * px + dy * py;
    if (u < uMin) uMin = u;
    if (u > uMax) uMax = u;
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
  }
  const at = (u, v, z) => [cx + u * ax + v * px, cy + u * ay + v * py, z];
  const vMid = (vMin + vMax) / 2;

  const e1 = at(uMin, vMin, eave);
  const e2 = at(uMax, vMin, eave);
  const e3 = at(uMax, vMax, eave);
  const e4 = at(uMin, vMax, eave);
  const r1 = at(uMin, vMid, ridge);
  const r2 = at(uMax, vMid, ridge);

  const up = (_nx, _ny, nz) => nz < 0;
  builder.quad(e1, e2, r2, r1, colour, up);   // slope one way
  builder.quad(e3, e4, r1, r2, colour, up);   // and the other
  // Gable ends, shaded as wall rather than roof.
  builder.tri(e1, r1, e4, colour, null);
  builder.tri(e2, e3, r2, colour, null);
}

/**
 * Append one building.
 * `rings` are metre-space rings, outer first; `props` carries the lidar result.
 */
export function addBuilding(builder, rings, props, palette) {
  const outer = rings[0];
  if (!outer || outer.length < 3) return;

  const height = props.height_m;
  if (!height || height < 1) return;

  const form = props.roof_form || 'flat';
  const spread = props.roof_spread_m || 0;
  // height_m is the ridge. The measured spread between eave and ridge gives the
  // pitch; clamp so a noisy spread cannot swallow the walls.
  const rise = form === 'flat' ? 0 : Math.min(Math.max(spread, 0.6), height * 0.55);
  const eave = height - rise;

  // Rings are closed in GeoJSON; drop the repeated last vertex.
  const open = (r) => (r.length > 1
    && r[0][0] === r[r.length - 1][0]
    && r[0][1] === r[r.length - 1][1] ? r.slice(0, -1) : r);

  const shell = open(outer);
  const holes = rings.slice(1).map(open);

  // ShapeUtils wants a counter-clockwise contour and clockwise holes.
  const contour = ringArea(shell) < 0 ? shell.slice().reverse() : shell;

  addWalls(builder, contour, eave, palette.wall);
  for (const hole of holes) addWalls(builder, hole, eave, palette.wall);

  if (rise <= 0.6 || holes.length) {
    addFlatRoof(builder, contour, holes.map((h) => (ringArea(h) > 0 ? h.slice().reverse() : h)),
                eave, palette.roof);
  } else {
    addGableRoof(builder, contour, eave, height, palette.roof);
  }
}

export { Builder };
