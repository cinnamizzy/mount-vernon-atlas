// Attaching an arbitrary position - a place centroid, or the visitor's GPS fix -
// to the nearest routable node.
//
// A uniform grid keeps this cheap enough to run on every GPS update rather than
// scanning every node each time.

import { distance } from './geo.js';

const CELL_DEG = 0.0015; // roughly 130-165 m at this latitude

export function buildIndex(graph) {
  const cells = new Map();
  for (const [key, coord] of graph.nodes) {
    const cx = Math.floor(coord[0] / CELL_DEG);
    const cy = Math.floor(coord[1] / CELL_DEG);
    const id = `${cx},${cy}`;
    let bucket = cells.get(id);
    if (!bucket) { bucket = []; cells.set(id, bucket); }
    bucket.push(key);
  }
  return { graph, cells };
}

/**
 * Nearest routable node to [lng, lat].
 * Returns { key, coordinate, metres } or null when the graph is empty.
 */
export function nearest(index, position) {
  const { graph, cells } = index;
  const cx = Math.floor(position[0] / CELL_DEG);
  const cy = Math.floor(position[1] / CELL_DEG);

  let best = null;
  // Widen the search ring until something is found, then one ring more so a
  // closer node just over a cell boundary is not missed.
  for (let ring = 0; ring < 40; ring += 1) {
    for (let dx = -ring; dx <= ring; dx += 1) {
      for (let dy = -ring; dy <= ring; dy += 1) {
        if (ring > 0 && Math.abs(dx) !== ring && Math.abs(dy) !== ring) continue;
        for (const key of cells.get(`${cx + dx},${cy + dy}`) || []) {
          const coordinate = graph.nodes.get(key);
          const metres = distance(position, coordinate);
          if (!best || metres < best.metres) best = { key, coordinate, metres };
        }
      }
    }
    if (best && ring > 0) break;
  }
  return best;
}
