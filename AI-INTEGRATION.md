# Mount Vernon Atlas navigation integration

The deployed map exposes `window.mountVernonMap` for the host audio-tour app.

```js
const context = window.mountVernonMap.getNavigationContext();
// Send context to the app's route-planning service or model.

window.mountVernonMap.setAccessProfile('visitor'); // or 'accessible'
window.mountVernonMap.locate();                    // asks for phone GPS permission
window.mountVernonMap.applyAiRoute(routeGeoJSON);  // draws returned LineStrings
```

The navigation context includes the current GPS fix, stable place IDs, garden access notes, source-derived wall/fence geometries, and the count of routable path segments. The planner should return a GeoJSON `FeatureCollection` containing `LineString` or `MultiLineString` route features. The map draws those as a separate AI route layer, leaving the user's manually composed route intact.

Access behavior is intentionally conservative:

- Routes are based on mapped paths, not free-roam polygons.
- The Lower Garden is treated as a walled enclosure with small entrances only; the Fruit Garden and Nursery is treated as a fenced enclosure. Exact gate locations remain unverified.
- Garden stops carry path-only notes and boundary warnings.
- GPS is used locally in the browser and is not uploaded by this static map.
- Wall, fence, hedge, gate, closure, and step-free status remain source-derived or unvalidated until the estate supplies an approved access inventory.

The returned GeoJSON from `exportRoute()` includes the access profile, GPS fix (when available), barrier visibility, stop access notes, and planning-preview warnings so the host app can preserve that context with an audio tour.

## Garden detail (Edition 15)

`gardens-2026.geojson` (also loaded as `gardens.js`) holds the walks, walls, fences, hedges, planting compartments, gates and enclosure outlines for the Upper Garden, Lower Garden, Fruit Garden & Nursery and Pioneer Farm. Every feature carries `kind`, `garden`, `garden_id` (the OpenStreetMap place id used elsewhere in the map), `source`, `method`, `confidence` and `surveyed: false`.

- `kind: walk` features carry `width_m`, `surface` (`gravel` or `grass`) and `routable`. Routable walks are pre-noded into `routing_segments`; `connectors` join the garden network to the estate network at gates.
- `kind: fence` features carry `fence_type` (`post_rail` or `worm`); `kind: wall` features carry `material: brick`.
- `superseded_ways` and `superseded_barriers` list the OpenStreetMap ids that the garden detail replaces. The map no longer draws or routes on them.
- `getNavigationContext().barriers` now includes these fences and walls with `source: "aerial-2026"`, and `getNavigationContext().garden_detail.gates` lists the gate positions.

The baked ground tiles inside the four enclosures were re-rendered from the same GeoJSON, so the drawing, the interactive layer and the routing graph agree.
