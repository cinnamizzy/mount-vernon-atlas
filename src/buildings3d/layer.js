// A MapLibre custom layer that draws the buildings as real 3D geometry with
// Three.js, sharing the map's WebGL context.
//
// Heights and roof forms come from pipeline/heights.py - measured from USGS 3DEP
// lidar, not invented. Buildings with no measured height are skipped rather than
// given a guess.

import {
  AmbientLight, BufferAttribute, BufferGeometry, Camera, Color, DirectionalLight,
  Matrix4, Mesh, MeshLambertMaterial, Scene, Vector3, WebGLRenderer,
} from '../../vendor/three.module.js';
import { Builder, addBuilding, toLocal } from './geometry.js';

function srgb(hex) {
  const c = new Color(hex);
  return [c.r, c.g, c.b];
}

function outerRings(geometry) {
  if (geometry.type === 'Polygon') return [geometry.coordinates];
  if (geometry.type === 'MultiPolygon') return geometry.coordinates;
  return [];
}

/**
 * @param {object} opts
 *   buildings  GeoJSON FeatureCollection with height_m / roof_form
 *   origin     {lng, lat} scene origin
 *   tokens     style tokens, for colours
 */
export function createBuildings3DLayer({ buildings, origin, tokens }) {
  const palette = {
    wall: srgb(tokens.color.building.wall),
    roof: srgb(tokens.color.building.fill),
  };
  const byClass = {
    residence: srgb(tokens.color.building.residence),
    outbuilding: srgb(tokens.color.building.outbuilding),
    civic: srgb(tokens.color.building.civic),
    memorial: srgb(tokens.color.building.memorial),
    shelter: srgb(tokens.color.building.shelter),
  };

  let built = 0;
  let skipped = 0;
  const builder = new Builder();

  for (const feature of buildings.features) {
    const props = feature.properties;
    if (!props.height_m) { skipped += 1; continue; }
    const colours = {
      wall: palette.wall,
      roof: byClass[props.class] || palette.roof,
    };
    for (const rings of outerRings(feature.geometry)) {
      const local = rings.map((ring) => ring.map(([lng, lat]) => toLocal(lng, lat, origin)));
      addBuilding(builder, local, props, colours);
    }
    built += 1;
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(builder.positions), 3));
  geometry.setAttribute('normal', new BufferAttribute(new Float32Array(builder.normals), 3));
  geometry.setAttribute('color', new BufferAttribute(new Float32Array(builder.colors), 3));

  const stats = { built, skipped, triangles: builder.triangleCount };

  return {
    id: 'buildings-3d',
    type: 'custom',
    renderingMode: '3d',
    stats,

    onAdd(map, gl) {
      this.map = map;
      this.camera = new Camera();
      this.scene = new Scene();

      // Lit from the upper left, matching the drawn assets and the washes.
      const key = new DirectionalLight(0xffffff, 2.1);
      key.position.set(-0.6, 0.8, 1.0).normalize();
      const fill = new DirectionalLight(0xffffff, 0.7);
      fill.position.set(0.7, -0.4, 0.5).normalize();
      this.scene.add(key, fill, new AmbientLight(0xffffff, 1.25));

      this.scene.add(new Mesh(geometry, new MeshLambertMaterial({
        vertexColors: true,
        // Footprints are not always wound consistently; normals are computed
        // explicitly in the builder, so render both faces and let lighting sort
        // itself out rather than dropping walls.
        side: 2, // DoubleSide
      })));

      this.renderer = new WebGLRenderer({
        canvas: map.getCanvas(),
        context: gl,
        antialias: true,
      });
      this.renderer.autoClear = false;

      // Geometry is metres from `origin`; this puts it into mercator space.
      const mercator = maplibregl.MercatorCoordinate.fromLngLat(origin, 0);
      const scale = mercator.meterInMercatorCoordinateUnits();
      this.modelMatrix = new Matrix4()
        .makeTranslation(mercator.x, mercator.y, mercator.z)
        // mercator y grows southward, so north (+y) needs the sign flipped
        .scale(new Vector3(scale, -scale, scale));
    },

    onRemove() {
      geometry.dispose();
      this.renderer?.dispose?.();
    },

    render(gl, matrixOrArgs) {
      // MapLibre 4.x calls render(gl, transform.customLayerMatrix(), {farZ,...}),
      // so the second argument IS the matrix. MapLibre 5.x passes an args object
      // carrying defaultProjectionData instead. Accept either.
      const matrix = ArrayBuffer.isView(matrixOrArgs) || Array.isArray(matrixOrArgs)
        ? matrixOrArgs
        : (matrixOrArgs?.defaultProjectionData?.mainMatrix
           || matrixOrArgs?.defaultProjectionData?.projectionMatrix);
      if (!matrix) return;

      this.camera.projectionMatrix = new Matrix4()
        .fromArray(Array.from(matrix))
        .multiply(this.modelMatrix);

      this.renderer.resetState();
      this.renderer.render(this.scene, this.camera);
    },
  };
}
