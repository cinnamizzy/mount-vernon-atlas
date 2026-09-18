# Mount Vernon Estate Atlas — Edition 25

An interactive illustrated estate map with place cards, a walking-route planner, and optional geographic reference overlays. Edition 25 replaces the previous vector-map application with new artwork rendered from the refined Blender scene.

## Open the map online

**[Open the Mount Vernon Estate Atlas](https://cinnamizzy.github.io/mount-vernon-atlas/)**

Share this link with anyone to view the Edition 25 map in a browser. No download, Python installation, or local server is needed. The GitHub repository contains the project files; the link above opens the interactive website.

## Optional: preview locally

These steps are only for previewing a downloaded copy on your own computer.

Open the project folder and double-click `serve.cmd`, then use the address it displays. Alternatively, run this PowerShell command, replacing the folder path if you saved the project elsewhere:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m http.server 8000 --bind 127.0.0.1 --directory "C:\Users\irollo3\Downloads\mount-vernon-atlas"
```

Open http://localhost:8000/. The `--directory` option selects the folder containing `index.html`, preventing a listing of your Windows user folder. If an older server is already using port 8000, stop it with **Ctrl+C** in its terminal first.

A localhost address works only on your own computer. To share the map, use the public website link above. No build step is required.

## GitHub Pages hosting

The public website is hosted from this repository. Its Pages source is the `main` branch at `/ (root)` under **Settings → Pages**. All assets use relative paths, including the 324 Edition 25 WebP map tiles (about 5.45 MB total).

## Contents

- `index.html`, `style.css`, `app.js`: website and route planner.
- `baked/edition25/tiles/`: map artwork, zooms 14–20. Zoom 20 has additional historic-core detail; elsewhere tiles fall back to zoom 19.
- `baked/projection.json`, `baked/projection-data.js`, `projection.js`: shared map projection and versioned tile configuration.
- `estate.geojson`, `data.js`: source features and places.
- `gardens-2026.geojson`, `gardens.js`: garden geometry and route connections.
- `assets/`, `art.js`: place-card illustrations.
- `AI-INTEGRATION.md`: navigation integration API.

The editable Blender scene and rendering workspace are maintained separately from this static website export. Earlier versions of this repository remain in Git history.

## Edition 25

The new artwork includes distinct roads and paved walks, marked parking, refined building proportions and roofs, low-poly trees, orchard saplings, and landmark details. Exterior residential buildings and trees are omitted, with a muted sage setting around the estate. Reference overlays are off by default so they do not obscure the artwork.

Checked locally: place cards, close-up map tiles, and a Mansion-to-Upper-Garden test route (183 m of mapped path). This is a planning prototype, not an estate-approved navigation product. Access restrictions, entrances, closures and accessible routes need field review. Building heights and parking stall spacing are estimates.

## Sources and attribution

Geographic features: © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright). Terrain: public USGS / Mapzen data. Fairfax County 2025 aerial imagery and supplied visual references informed scene refinements. Architecture and planting include illustrative interpretation. See the in-app Map notes for details. Leaflet's license is included in LEAFLET-LICENSE.txt.