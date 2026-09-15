// Builds a walking graph for one profile from layers/paths.geojson.
//
// Nodes are joined by EXACT shared coordinates. Two ways that merely pass close
// to each other are not connected. That is deliberate: it means a disconnected
// pair of stops produces an honest "no connected path" result instead of a
// straight line across a lawn or through a wall.
//
// Profile membership comes from each feature's `profiles` array, stamped by
// pipeline/classify.py. This module never re-decides what a profile may use, so
// the step-free profile cannot silently regain steps.

import { distance, nodeKey, lines } from './geo.js';

export function buildGraph(paths, profile, precision) {
  const adjacency = new Map(); // key -> [[neighbourKey, metres], ...]
  const nodes = new Map(); // key -> [lng, lat]
  let edges = 0;
  let usedFeatures = 0;
  const excludedClasses = new Set();

  const link = (a, b) => {
    const ka = nodeKey(a, precision);
    const kb = nodeKey(b, precision);
    if (ka === kb) return;
    if (!nodes.has(ka)) { nodes.set(ka, a); adjacency.set(ka, []); }
    if (!nodes.has(kb)) { nodes.set(kb, b); adjacency.set(kb, []); }
    const metres = distance(a, b);
    adjacency.get(ka).push([kb, metres]);
    adjacency.get(kb).push([ka, metres]);
    edges += 1;
  };

  for (const feature of paths.features) {
    const p = feature.properties;
    if (!p.routable) continue;
    if (!Array.isArray(p.profiles) || !p.profiles.includes(profile)) {
      excludedClasses.add(p.class);
      continue;
    }
    usedFeatures += 1;
    for (const line of lines(feature.geometry)) {
      for (let i = 1; i < line.length; i += 1) link(line[i - 1], line[i]);
    }
  }

  return {
    profile,
    adjacency,
    nodes,
    stats: {
      features: usedFeatures,
      nodes: nodes.size,
      edges,
      excludedClasses: [...excludedClasses].sort(),
    },
  };
}

/**
 * Connected components, so the UI can tell a genuinely unreachable stop from a
 * routing failure. Returns key -> component id.
 */
export function components(graph) {
  const seen = new Map();
  let id = 0;
  for (const start of graph.nodes.keys()) {
    if (seen.has(start)) continue;
    const stack = [start];
    seen.set(start, id);
    while (stack.length) {
      const key = stack.pop();
      for (const [next] of graph.adjacency.get(key) || []) {
        if (!seen.has(next)) { seen.set(next, id); stack.push(next); }
      }
    }
    id += 1;
  }
  return { componentOf: seen, count: id };
}
