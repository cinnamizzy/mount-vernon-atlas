// Ground-distance helpers. Distances are real metres on the WGS84 sphere, not
// screen pixels, so route lengths mean something on the ground.

const R = 6371008.8; // IUGG mean Earth radius, metres
const rad = (deg) => (deg * Math.PI) / 180;

/** Haversine great-circle distance in metres between [lng, lat] pairs. */
export function distance(a, b) {
  const dLat = rad(b[1] - a[1]);
  const dLng = rad(a[0] - b[0]);
  const lat1 = rad(a[1]);
  const lat2 = rad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.sin(dLng / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Total length of a [lng, lat] line in metres. */
export function lineLength(coords) {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) total += distance(coords[i - 1], coords[i]);
  return total;
}

/** Stable node key. Exact string equality is what joins two ways into one graph. */
export function nodeKey(coord, precision) {
  return `${coord[0].toFixed(precision)},${coord[1].toFixed(precision)}`;
}

/** Every LineString in a feature, as arrays of [lng, lat]. */
export function lines(geometry) {
  if (geometry.type === 'LineString') return [geometry.coordinates];
  if (geometry.type === 'MultiLineString') return geometry.coordinates;
  return [];
}
