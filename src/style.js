// Assembles a MapLibre style spec from style/tokens.json, style/manifest.json and
// style/layers/*.json at runtime. No build step: edit a JSON file, refresh the page.
//
// Two small conveniences on top of plain MapLibre style JSON:
//   "$color.path.visitor_path"       -> looked up in tokens.json
//   { "$ref": "$width_ramp.by_zoom" } -> spliced from elsewhere in the same fragment
//
// Keys beginning with "$comment" are stripped, so fragments can document themselves.

const STYLE_DIR = 'style';

function stripComments(value) {
  if (Array.isArray(value)) return value.map(stripComments);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (!k.startsWith('$comment')) out[k] = stripComments(v);
    }
    return out;
  }
  return value;
}

function lookup(root, path) {
  return path.split('.').reduce((acc, key) => (acc == null ? acc : acc[key]), root);
}

// Resolves "$a.b.c" strings and { "$ref": "$a.b" } objects.
// `tokens` is tokens.json; `local` is the fragment itself, so a fragment can
// reference its own reusable pieces.
function resolve(node, tokens, local) {
  if (typeof node === 'string' && node.startsWith('$')) {
    const value = lookup(tokens, node.slice(1));
    if (value === undefined) throw new Error(`Unknown style token: ${node}`);
    return resolve(value, tokens, local);
  }
  if (Array.isArray(node)) return node.map((n) => resolve(n, tokens, local));
  if (node && typeof node === 'object') {
    if (typeof node.$ref === 'string') {
      // $ref paths keep their leading $ because helper blocks are written as
      // "$width_ramp" in the fragment - the path matches the keys literally.
      const value = lookup(local, node.$ref);
      if (value === undefined) throw new Error(`Unknown $ref: ${node.$ref}`);
      return resolve(value, tokens, local);
    }
    const out = {};
    for (const [k, v] of Object.entries(node)) out[k] = resolve(v, tokens, local);
    return out;
  }
  return node;
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status} ${response.statusText}`);
  return response.json();
}

export async function buildStyle() {
  // Both are stripped: anything under `sources` is handed to MapLibre verbatim,
  // and a stray $comment key there becomes a source with no "type".
  const [tokens, manifest] = await Promise.all([
    loadJson(`${STYLE_DIR}/tokens.json`).then(stripComments),
    loadJson(`${STYLE_DIR}/manifest.json`).then(stripComments),
  ]);

  const order = manifest.order;
  const fragments = await Promise.all(
    order.map((name) => loadJson(`${STYLE_DIR}/layers/${name}.json`))
  );

  const layers = [];
  const meta = [];
  fragments.forEach((raw, i) => {
    // Resolve against the raw fragment so $ref can reach $-prefixed helper blocks,
    // then strip comments from the result.
    const resolved = stripComments(resolve(raw, tokens, raw));
    const name = order[i];
    meta.push({
      name,
      label: resolved.label || name,
      legend: resolved.legend || [],
    });
    for (const layer of resolved.layers || []) {
      if (layer.type === 'symbol' && !layer.layout?.['text-font']) {
        layer.layout = { ...layer.layout, 'text-font': manifest.default_text_font };
      }
      layer._fragment = name;
      layers.push(layer);
    }
  });

  const sources = {};
  for (const [name, source] of Object.entries(manifest.sources)) {
    sources[name] = { ...source, attribution: manifest.attribution };
  }

  return {
    style: {
      version: 8,
      glyphs: manifest.glyphs,
      sprite: manifest.sprite,
      sources,
      layers: [
        { id: 'background', type: 'background', paint: { 'background-color': tokens.color.background } },
        ...layers.map(({ _fragment, ...layer }) => layer),
      ],
    },
    manifest,
    tokens,
    // fragment name -> its label, legend entries and the layer ids it contributed.
    // The legend and the layer toggles are BOTH generated from this, so a legend
    // key cannot describe geometry that is not drawn - the prototype's bug.
    layerIndex: meta.map((m) => ({
      ...m,
      layerIds: layers.filter((l) => l._fragment === m.name).map((l) => l.id),
    })),
  };
}
