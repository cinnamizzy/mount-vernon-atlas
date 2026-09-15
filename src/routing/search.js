// A* over the walking graph, with a binary heap frontier.
//
// The heuristic is straight-line ground distance to the target. Edge weights are
// the same ground distance along each segment, so the heuristic never
// overestimates and A* returns a true shortest path.

import { distance } from './geo.js';

class MinHeap {
  constructor() { this.items = []; }
  get size() { return this.items.length; }
  push(priority, value) {
    const items = this.items;
    items.push([priority, value]);
    let i = items.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (items[parent][0] <= items[i][0]) break;
      [items[parent], items[i]] = [items[i], items[parent]];
      i = parent;
    }
  }
  pop() {
    const items = this.items;
    const top = items[0];
    const last = items.pop();
    if (items.length) {
      items[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let small = i;
        if (l < items.length && items[l][0] < items[small][0]) small = l;
        if (r < items.length && items[r][0] < items[small][0]) small = r;
        if (small === i) break;
        [items[small], items[i]] = [items[i], items[small]];
        i = small;
      }
    }
    return top;
  }
}

/**
 * Shortest path between two node keys.
 * Returns { coordinates, metres, visited } or null when no route exists.
 */
export function shortestPath(graph, startKey, endKey) {
  if (!startKey || !endKey) return null;
  if (!graph.adjacency.has(startKey) || !graph.adjacency.has(endKey)) return null;
  if (startKey === endKey) {
    return { coordinates: [graph.nodes.get(startKey)], keys: [startKey], metres: 0, visited: 0 };
  }

  const target = graph.nodes.get(endKey);
  const best = new Map([[startKey, 0]]);
  const cameFrom = new Map();
  const settled = new Set();
  const frontier = new MinHeap();
  frontier.push(distance(graph.nodes.get(startKey), target), startKey);

  while (frontier.size) {
    const [, key] = frontier.pop();
    if (settled.has(key)) continue;
    settled.add(key);

    if (key === endKey) {
      const path = [key];
      while (cameFrom.has(path[0])) path.unshift(cameFrom.get(path[0]));
      return {
        coordinates: path.map((k) => graph.nodes.get(k)),
        keys: path,
        metres: best.get(endKey),
        visited: settled.size,
      };
    }

    const cost = best.get(key);
    for (const [next, weight] of graph.adjacency.get(key) || []) {
      if (settled.has(next)) continue;
      const candidate = cost + weight;
      if (candidate < (best.get(next) ?? Infinity)) {
        best.set(next, candidate);
        cameFrom.set(next, key);
        frontier.push(candidate + distance(graph.nodes.get(next), target), next);
      }
    }
  }
  return null;
}
