// Minimal chunk-streaming viewer: flat ground, extruded OSM footprints, no tokens needed.
// Serve from the repo root: python -m http.server 8000, then open /clients/web/
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { Sky } from "three/addons/objects/Sky.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const CHUNKS_URL = "../../chunks";
const MESH_LEVEL = new URLSearchParams(location.search).get("mesh"); // ?mesh=16 shows the reality mesh (meshes/L16) instead of OSM extrusions
const FINE_LEVEL = new URLSearchParams(location.search).get("lod"); // ?mesh=16&lod=18,20 swaps in meshes/L18, then L20, nearer the camera
const MESH_DIR = new URLSearchParams(location.search).get("raw") === "1" ? "../../meshes" : "../../meshes_novg"; // novg: canopy faces removed (pipeline/mesh_to_glb.py --strip-veg), trees planted by addTrees
const SHOW_TREES = new URLSearchParams(location.search).get("trees") === "1"; // OSM tree markers (see addProps)
const ALBEDO = +(new URLSearchParams(location.search).get("albedo") ?? 1.0);
const LIFT = +(new URLSearchParams(location.search).get("lift") ?? 0.55); // <1 raises dark texels more than bright ones
const LOD_NEAR = [150, 60, 25]; // m: camera distance below which a tile of level k is swapped for its children (?lod=18,20,21 ...)
let lodTick = null;
const LOAD_RADIUS = 3; // chunks around the camera kept loaded; unload beyond +1
const SPEED = 12; // m/s, hold Shift for x4
const M_PER_DEG_LAT = 111320;

const hud = document.getElementById("hud");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fb6c9);
scene.fog = new THREE.Fog(0x9fb6c9, 150, 500);
scene.add(new THREE.HemisphereLight(0xffffff, 0x556655, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
// Sun at a fixed afternoon position (elevation / azimuth in degrees). Shadows follow the camera so one 2048 map covers +-SHADOW_R m.
const SUN_ELEV = 35;
const SUN_AZ = 200;
const SHADOW_R = 120;
const sunDir = new THREE.Vector3().setFromSphericalCoords(1, THREE.MathUtils.degToRad(90 - SUN_ELEV), THREE.MathUtils.degToRad(SUN_AZ));
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -SHADOW_R, right: SHADOW_R, top: SHADOW_R, bottom: -SHADOW_R, near: 1, far: 600 });
sun.shadow.bias = -0.0005;
sun.shadow.normalBias = 0.5;
scene.add(sun, sun.target);

const sky = new Sky();
sky.scale.setScalar(5000);
sky.material.uniforms.sunPosition.value.copy(sunDir);
Object.assign(sky.material.uniforms.turbidity, { value: 4 });
Object.assign(sky.material.uniforms.rayleigh, { value: 1.5 });
scene.add(sky);

const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.5, 1000);
camera.position.set(0, 1.7, 0);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = +(new URLSearchParams(location.search).get("exp") ?? 1.2); // aerial textures are dark (baked shadows)
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.appendChild(renderer.domElement);

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(4000, 4000).rotateX(-Math.PI / 2),
  new THREE.MeshLambertMaterial({ color: 0x8a8b84 }) // mid urban grey: between street (dark) and path (light)
);
scene.add(ground);

// no-store: a stale cached index silently hides regenerated chunks
const meta = await (await fetch(`${CHUNKS_URL}/index.json`, { cache: "no-store" })).json();
const { south, west } = meta.origin;
const size = meta.size_m;
const available = new Set(meta.chunks.map((c) => c.file));
const mPerDegLon = M_PER_DEG_LAT * Math.cos((south * Math.PI) / 180);

// chunk-local meters: x = east, z = -north, y = up
const project = ([lon, lat]) => [(lon - west) * mPerDegLon, (lat - south) * M_PER_DEG_LAT];

// Reality mesh tiles from pipeline/mesh_to_glb.py: already in the local frame, heights in the source datum.
// trimesh exports metallic=1 by default: without an environment map that renders walls black
function prepareMesh(o) {
  if (!o.isMesh) return;
  o.material.metalness = 0;
  o.material.roughness = 1;
  o.material.color.setScalar(ALBEDO);
  // aerial textures carry baked shadows and sit far below real albedo (asphalt ~ 0.1-0.2): lift the dark end of the curve
  o.material.onBeforeCompile = (sh) => {
    sh.fragmentShader = sh.fragmentShader.replace("#include <map_fragment>", `#include <map_fragment>
  diffuseColor.rgb = pow(diffuseColor.rgb, vec3(${LIFT.toFixed(3)}));`);
  };
  o.castShadow = true;
  o.receiveShadow = true;
}
const meshTiles = [];
let streets = null; // DEM + overlay of pipeline/streets.py: clean OSM street surfaces draped over the mesh ground
let meshAttribution = "";
if (MESH_LEVEL) {
  const base = `${MESH_DIR}/L${MESH_LEVEL}`;
  const mi = await (await fetch(`${base}/index.json`, { cache: "no-store" })).json();
  meshAttribution = mi.attribution;
  scene.remove(ground);
  const loader = new GLTFLoader();
  await Promise.all(mi.tiles.map(async (t) => {
    const gltf = await loader.loadAsync(`${base}/${t.file}`);
    gltf.scene.traverse(prepareMesh);
    scene.add(gltf.scene);
    meshTiles.push({ root: gltf.scene, t, depth: 0, kids: null, open: false, loading: false });
  }));
  if (FINE_LEVEL) await setupLod();
  await addStreets();
}

// OSM trees / lamps / benches / bins (pipeline/props.py) as one InstancedMesh per kind, placed on the mesh ground.
function lowestY(x, z) {
  ray.set(new THREE.Vector3(x, 500, z), down);
  const hits = ray.intersectObjects(meshTiles.filter(({ t }) => x >= t.east[0] && x <= t.east[1] && -z >= t.north[0] && -z <= t.north[1]).map((m) => m.root), true);
  // last hit = lowest surface (under tree canopies); `clear` = height of the stuff above it
  if (!hits.length) return streets ? { y: demAt(x, z), clear: 0 } : null; // holes cut for streets
  return { y: hits[hits.length - 1].point.y, clear: hits[0].point.y - hits[hits.length - 1].point.y };
}

function part(geo, color, y) {
  const g = geo.index ? geo.toNonIndexed() : geo; // mergeGeometries needs all parts to agree on indexing
  g.translate(0, y, 0);
  const c = new THREE.Color(color);
  g.setAttribute("color", new THREE.Float32BufferAttribute(Array.from({ length: g.attributes.position.count }, () => [c.r, c.g, c.b]).flat(), 3));
  return g;
}

const PROP_GEO = {
  tree: () => mergeGeometries([part(new THREE.CylinderGeometry(0.15, 0.25, 3, 6), 0x5a4632, 1.5), part(new THREE.IcosahedronGeometry(2.4, 1), 0x4f7a3a, 5)]),
  lamp: () => mergeGeometries([part(new THREE.CylinderGeometry(0.06, 0.08, 5, 6), 0x2b2d30, 2.5), part(new THREE.SphereGeometry(0.25, 8, 6), 0xfff3c4, 5.1)]),
  bench: () => mergeGeometries([part(new THREE.BoxGeometry(1.6, 0.08, 0.45), 0x7a5a3a, 0.45), part(new THREE.BoxGeometry(1.6, 0.4, 0.06), 0x7a5a3a, 0.75)]),
  bin: () => part(new THREE.CylinderGeometry(0.2, 0.2, 0.9, 8), 0x3a4a3a, 0.45),
};

// Streets: a plane displaced by the DEM, textured with the OSM road / sidewalk overlay (alpha-cut elsewhere); the mesh was cut there
// by mesh_to_glb.py --streets because aerial photogrammetry makes streets lumpy and melts cars into them.
const demAt = (x, z) => {
  if (!streets) return null;
  const { x0, y0, cell, n, dem } = streets;
  const fx = Math.min(Math.max((x - x0) / cell - 0.5, 0), n - 1.001), fy = Math.min(Math.max((-z - y0) / cell - 0.5, 0), n - 1.001);
  const i = Math.floor(fx), j = Math.floor(fy), u = fx - i, v = fy - j, at = (a, b) => dem[b * n + a];
  return (at(i, j) * (1 - u) + at(i + 1, j) * u) * (1 - v) + (at(i, j + 1) * (1 - u) + at(i + 1, j + 1) * u) * v;
};

async function addStreets() {
  let meta;
  try {
    meta = await (await fetch("../../meshes/streets/streets.json", { cache: "no-store" })).json();
  } catch {
    return; // optional: run pipeline/streets.py
  }
  const dem = new Float32Array(await (await fetch("../../meshes/streets/dem.bin")).arrayBuffer());
  streets = { ...meta, dem };
  const { x0, y0, cell, n, size } = meta;
  const pos = new Float32Array(n * n * 3), uv = new Float32Array(n * n * 2);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const k = j * n + i, x = x0 + (i + 0.5) * cell, y = y0 + (j + 0.5) * cell;
    pos.set([x, dem[k] + 0.03, -y], k * 3);
    uv.set([(x - x0) / size, (y - y0) / size], k * 2);
  }
  const idx = [];
  for (let j = 0; j < n - 1; j++) for (let i = 0; i < n - 1; i++) {
    const a = j * n + i, b = a + 1, c = a + n, d = c + 1;
    idx.push(a, b, c, b, d, c); // counter-clockwise seen from above (z = -north)
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  const tex = await new THREE.TextureLoader().loadAsync("../../meshes/streets/overlay.png");
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  // tileable grain, multiplied over the overlay every 2.5 m so close-up asphalt is not a flat colour
  const gc = document.createElement("canvas");
  gc.width = gc.height = 256;
  const g2 = gc.getContext("2d"), img = g2.createImageData(256, 256);
  for (let i = 0; i < 256 * 256; i++) {
    const v = 150 + 70 * Math.random() * Math.random() + 35 * (Math.random() - 0.5);
    img.data.set([v, v, v, 255], i * 4);
  }
  g2.putImageData(img, 0, 0);
  const grain = new THREE.CanvasTexture(gc);
  grain.wrapS = grain.wrapT = THREE.RepeatWrapping;
  grain.anisotropy = 8;
  const mat = new THREE.MeshStandardMaterial({ map: tex, alphaTest: 0.5, roughness: 0.95, metalness: 0 });
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.grain = { value: grain };
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
uniform sampler2D grain;`)
      .replace("#include <map_fragment>", `#include <map_fragment>
  diffuseColor.rgb *= 0.55 + 0.9 * texture2D(grain, vMapUv * ${(size / 2.5).toFixed(1)}).r;`);
  };
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  scene.add(mesh);
  // everything the mesh does not cover (holes under cut streets, canopies, edges): neutral paving just below the overlay
  // only where the overlay is drawn: elsewhere the DEM climbs next to buildings and the plane would stand in front of the facades
  const underMat = new THREE.MeshStandardMaterial({ color: 0x9a968d, map: tex, alphaTest: 0.5, roughness: 1, metalness: 0, polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2 });
  underMat.onBeforeCompile = (sh) => {
    sh.fragmentShader = sh.fragmentShader.replace("#include <map_fragment>", "diffuseColor.a *= texture2D(map, vMapUv).a;");
  };
  const under = new THREE.Mesh(geo, underMat);
  under.position.y = -0.04;
  under.receiveShadow = true;
  scene.add(under);
}

// Trees from pipeline/mesh_to_glb.py --trees-out: the mesh canopies were cut out, each crown is rebuilt as a trunk,
// a lumpy low-poly crown and a patch of ground under it (aerial photogrammetry has no ground below canopies).
function hash3(x, y, z) {
  const h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
  return h - Math.floor(h);
}

function crownGeometry() {
  const blobs = [[0, 0, 0, 1], [0.5, -0.2, 0.25, 0.7], [-0.45, 0.1, -0.3, 0.75], [0.1, 0.35, -0.4, 0.6]];
  const parts = blobs.map(([ox, oy, oz, r]) => {
    const g = new THREE.IcosahedronGeometry(r, 1); // non-indexed: each face keeps its own vertices
    const pos = g.attributes.position;
    const col = [];
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
      const k = 0.8 + 0.4 * hash3(Math.round(x * 20), Math.round(y * 20), Math.round(z * 20)); // same point -> same bump
      pos.setXYZ(i, x * k + ox, y * k + oy, z * k + oz);
      const shade = 0.55 + 0.35 * ((y * k + oy + 1) / 2) + 0.15 * (hash3(i, ox, oz) - 0.5); // darker underneath, speckled
      col.push(0.22 * shade, 0.42 * shade, 0.13 * shade);
    }
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    return g;
  });
  return mergeGeometries(parts);
}

async function addTrees() {
  let data;
  try {
    data = await (await fetch("../../meshes/trees.json", { cache: "no-store" })).json();
  } catch {
    return;
  }
  const trees = data.trees.filter((t) => t.top - t.base >= 3.5 && t.radius >= 2); // the mesh gives many scraps next to real crowns
  const mat = (o) => new THREE.MeshStandardMaterial({ roughness: 1, metalness: 0, ...o });
  const crowns = new THREE.InstancedMesh(crownGeometry(), mat({ vertexColors: true, flatShading: true }), trees.length);
  const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.6, 1, 1, 6).translate(0, 0.5, 0), mat({ color: 0x4b3b2a }), trees.length);
  const soil = new THREE.InstancedMesh(new THREE.CircleGeometry(1, 16).rotateX(-Math.PI / 2), mat({ color: 0x57623a }), trees.length);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), c = new THREE.Color();
  trees.forEach((t, i) => {
    const h = t.top - t.base;
    const rx = t.radius * 0.9, ry = Math.max(h / 2, 1.5);
    const trunk = Math.max(t.base - t.ground, 1.2);
    q.setFromEuler(e.set(0, hash3(i, 1, 2) * 6.283, 0));
    crowns.setMatrixAt(i, m.compose(new THREE.Vector3(t.x, t.ground + trunk + ry * 0.9, t.z), q, new THREE.Vector3(rx, ry, rx)));
    crowns.setColorAt(i, c.setHSL(0.05 * (hash3(i, 3, 4) - 0.5), 1, 0.9 + 0.2 * hash3(i, 5, 6)).multiplyScalar(1));
    trunks.setMatrixAt(i, m.compose(new THREE.Vector3(t.x, t.ground, t.z), new THREE.Quaternion(), new THREE.Vector3(0.12 + rx * 0.03, trunk + ry * 0.5, 0.12 + rx * 0.03)));
    soil.setMatrixAt(i, m.compose(new THREE.Vector3(t.x, t.ground + 0.03, t.z), new THREE.Quaternion(), new THREE.Vector3(rx * 1.1, 1, rx * 1.1)));
  });
  [crowns, trunks, soil].forEach((o) => { o.castShadow = o !== soil; o.receiveShadow = true; scene.add(o); });
}

async function addProps() {
  let data;
  try {
    data = await (await fetch("../../meshes/props.json", { cache: "no-store" })).json();
  } catch {
    return; // props are optional: run pipeline/props.py to create them
  }
  const m = new THREE.Matrix4();
  for (const [kind, pts] of Object.entries(data.props)) {
    // The mesh already contains canopies, and aerial photogrammetry has no ground under them, so a
    // tree point lands on the canopy top: OSM trees are off unless ?trees=1.
    if (kind === "tree" && !SHOW_TREES) continue;
    const placed = pts
      .map((p) => [p, lowestY(p.x, p.z)])
      .filter(([, g]) => g)
      .map(([p, g]) => [p.x, g.y, p.z]);
    const mesh = new THREE.InstancedMesh(PROP_GEO[kind](), new THREE.MeshLambertMaterial({ vertexColors: true }), placed.length);
    placed.forEach(([x, y, z], i) => mesh.setMatrixAt(i, m.makeTranslation(x, y, z)));
    mesh.castShadow = true;
    scene.add(mesh);
  }
  // start next to the street lamp nearest the middle of the area (open ground) instead of inside a building
  const { x: cx, z: cz } = camera.position;
  const lamp = (data.props.lamp ?? []).reduce((best, l) => (!best || Math.hypot(l.x - cx, l.z - cz) < Math.hypot(best.x - cx, best.z - cz) ? l : best), null);
  if (lamp) camera.position.set(lamp.x + 1.5, camera.position.y, lamp.z);
}

// Multi-level LOD (?lod=18,20): tiles form a tree L16 -> L18 -> L20. A tile is swapped for the finer tiles inside it
// while the camera is within LOD_NEAR[depth] metres, and the children are freed again beyond 1.4x that distance.
async function setupLod() {
  const levels = FINE_LEVEL.split(",").map(Number);
  const loader = new GLTFLoader();
  const mid = (r) => (r[0] + r[1]) / 2;
  const indexes = await Promise.all(levels.map(async (l) =>
    (await (await fetch(`${MESH_DIR}/L${l}/index.json`, { cache: "no-store" })).json()).tiles));
  const inside = (f, t) => mid(f.east) >= t.east[0] && mid(f.east) < t.east[1] &&
                           mid(f.north) >= t.north[0] && mid(f.north) < t.north[1];

  const dist = (t) => Math.hypot(Math.max(t.east[0] - camera.position.x, 0, camera.position.x - t.east[1]),
                                 Math.max(t.north[0] + camera.position.z, 0, -camera.position.z - t.north[1]));
  const dispose = (n) => {
    n.kids?.forEach(dispose);
    if (n.depth > 0 && n.root) { // depth 0 (the L16 tiles) stay loaded
      scene.remove(n.root);
      n.root.traverse((o) => { if (o.isMesh) { o.geometry.dispose(); o.material.map?.dispose(); o.material.dispose(); } });
    }
    n.kids = null;
    n.open = false;
  };
  async function open(n) {
    n.loading = true;
    const kids = indexes[n.depth].filter((f) => inside(f, n.t)).map((t) => ({ t, depth: n.depth + 1, root: null, kids: null, open: false, loading: false }));
    if (!kids.length) {
      n.depth = Infinity; // nothing finer here: never try again
      return;
    }
    try {
      await Promise.all(kids.map(async (k) => {
        const gltf = await loader.loadAsync(`${MESH_DIR}/L${levels[n.depth]}/${k.t.file}`);
        gltf.scene.traverse(prepareMesh);
        gltf.scene.visible = false;
        scene.add(gltf.scene);
        k.root = gltf.scene;
      }));
    } catch (err) {
      console.error("LOD open failed", n.t.file, err);
      kids.forEach((k) => k.root && (scene.remove(k.root), k.root.traverse((o) => { if (o.isMesh) { o.geometry.dispose(); o.material.map?.dispose(); o.material.dispose(); } })));
      n.depth = Infinity; // do not retry every tick
      n.loading = false;
      return;
    }
    kids.forEach((k) => { k.root.visible = true; });
    n.root.visible = false;
    n.kids = kids;
    n.open = true;
    n.loading = false;
  }
  function tick(n) {
    if (n.depth >= levels.length || n.loading) return;
    const d = dist(n.t);
    if (!n.open && d < LOD_NEAR[n.depth]) open(n);
    else if (n.open && d > LOD_NEAR[n.depth] * 1.4) {
      n.kids.forEach(dispose);
      n.kids = null;
      n.open = false;
      n.root.visible = true;
    }
    n.kids?.forEach(tick);
  }
  lodTick = () => meshTiles.forEach(tick);
}

// the roots currently drawn for a tile: its own, or those of the finer tiles that replaced it
const activeRoots = (n) => (n.open ? n.kids.flatMap(activeRoots) : [n.root]);

const down = new THREE.Vector3(0, -1, 0);
const ray = new THREE.Raycaster();
// height of the mesh surface under (x, z); null where no tile covers the point
function groundY(x, z) {
  const roots = meshTiles
    .filter(({ t }) => x >= t.east[0] && x <= t.east[1] && -z >= t.north[0] && -z <= t.north[1])
    .flatMap(activeRoots);
  ray.set(new THREE.Vector3(x, 500, z), down);
  const hit = ray.intersectObjects(roots, true)[0];
  const dem = demAt(x, z);
  if (dem !== null && (!hit || hit.point.y > dem + 3)) return dem; // street holes in the mesh: the ray lands on a roof or misses
  return hit ? hit.point.y : null;
}

const known = new THREE.MeshLambertMaterial({ color: 0xd8d0c4 });
const guessed = new THREE.MeshLambertMaterial({ color: 0xa39384 }); // height from default, not OSM

// DoubleSide: the quad winding is clockwise seen from above, so it would be culled otherwise
const road = new THREE.MeshLambertMaterial({ color: 0x4a4d52, side: THREE.DoubleSide });
const path = new THREE.MeshLambertMaterial({ color: 0xc9c2b4, side: THREE.DoubleSide });

// All road quads of a chunk go into one geometry: one draw call instead of hundreds.
function buildRoads(quads, material, y) {
  const pos = [];
  for (const ring of quads) {
    const [a, b, c, d] = ring.slice(0, 4).map(project); // ring is closed, 5th point repeats the 1st
    for (const [px, py] of [a, b, c, a, c, d]) pos.push(px, y, -py);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.computeVertexNormals();
  return new THREE.Mesh(geo, material);
}

// Paths sit 3 cm above streets so overlapping quads layer predictably instead of z-fighting.
const PATH_CLASSES = new Set(["footway", "path", "steps", "pedestrian", "cycleway"]);

function buildChunk(fc) {
  const group = new THREE.Group();
  const streets = [];
  const paths = [];
  for (const f of fc.features) {
    if (f.properties.kind === "road") {
      (PATH_CLASSES.has(f.properties.highway) ? paths : streets).push(f.geometry.coordinates[0]);
      continue;
    }
    const ring = f.geometry.coordinates[0].map(project);
    const shape = new THREE.Shape(ring.map(([x, y]) => new THREE.Vector2(x, y)));
    const geo = new THREE.ExtrudeGeometry(shape, { depth: f.properties.height_m, bevelEnabled: false });
    geo.rotateX(-Math.PI / 2); // (x, y, z) -> (x, z, -y): extrusion goes up, north is -z
    group.add(new THREE.Mesh(geo, f.properties.height_known ? known : guessed));
  }
  if (streets.length) group.add(buildRoads(streets, road, 0.05));
  if (paths.length) group.add(buildRoads(paths, path, 0.08));
  return group;
}

const loaded = new Map(); // "ix_iy" -> Group, or null while the fetch is in flight

function disposeGroup(g) {
  scene.remove(g);
  g.traverse((o) => o.geometry?.dispose());
}

async function loadChunk(key) {
  loaded.set(key, null);
  try {
    const fc = await (await fetch(`${CHUNKS_URL}/${key}.geojson`)).json();
    if (!loaded.has(key)) return; // unloaded while fetching
    const g = buildChunk(fc);
    loaded.set(key, g);
    scene.add(g);
  } catch (e) {
    console.warn("chunk failed", key, e);
    loaded.delete(key);
  }
}

function updateChunks() {
  const cx = Math.floor(camera.position.x / size);
  const cy = Math.floor(-camera.position.z / size);
  for (let dx = -LOAD_RADIUS; dx <= LOAD_RADIUS; dx++) {
    for (let dy = -LOAD_RADIUS; dy <= LOAD_RADIUS; dy++) {
      const key = `${cx + dx}_${cy + dy}`;
      if (available.has(`${key}.geojson`) && !loaded.has(key)) loadChunk(key);
    }
  }
  for (const [key, g] of loaded) {
    const [ix, iy] = key.split("_").map(Number);
    if (Math.max(Math.abs(ix - cx), Math.abs(iy - cy)) > LOAD_RADIUS + 1) {
      if (g) disposeGroup(g);
      loaded.delete(key);
    }
  }
}

// Controls: click to capture the mouse, WASD to move, Space/C up/down.
const keys = new Set();
let yaw = 0;
let pitch = 0;
let eye = 1.7; // camera height above the surface under it
let floorY = 0;
addEventListener("keydown", (e) => keys.add(e.code));
addEventListener("keyup", (e) => keys.delete(e.code));
renderer.domElement.addEventListener("click", () => renderer.domElement.requestPointerLock());
addEventListener("mousemove", (e) => {
  if (document.pointerLockElement !== renderer.domElement) return;
  yaw -= e.movementX * 0.002;
  pitch = Math.max(-1.5, Math.min(1.5, pitch - e.movementY * 0.002));
});
addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// start in the middle of the chunk bounding box, looking north
const cells = meta.chunks;
if (cells.length) {
  const mx = cells.reduce((a, c) => a + c.ix, 0) / cells.length;
  const my = cells.reduce((a, c) => a + c.iy, 0) / cells.length;
  camera.position.set((mx + 0.5) * size, 1.7, -(my + 0.5) * size);
}

globalThis.viewer = { camera, scene, meshTiles }; // debugging / headless tests
// headless comparison with a real photo: place the camera at local (east, north) looking along (fwd = east, north, up)
viewer.setPose = (east, north, fwd, fovY) => {
  camera.position.set(east, camera.position.y, -north);
  yaw = Math.atan2(-fwd[0], fwd[1]);
  pitch = Math.asin(fwd[2]);
  camera.fov = fovY;
  camera.aspect = 4 / 3;
  camera.updateProjectionMatrix();
};
if (MESH_LEVEL) {
  await addTrees();
  await addProps();
} // after every const it uses (ray, down, PROP_GEO) is initialised

let lodAt = 0;
let last = performance.now();
let frames = 0;
let fpsAt = last;
let fps = 0;
renderer.setAnimationLoop((now) => {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;
  camera.rotation.set(pitch, yaw, 0, "YXZ");
  const v = SPEED * dt * (keys.has("ShiftLeft") ? 4 : 1);
  const fwd = (keys.has("KeyW") ? 1 : 0) - (keys.has("KeyS") ? 1 : 0);
  const side = (keys.has("KeyD") ? 1 : 0) - (keys.has("KeyA") ? 1 : 0);
  camera.position.x += (-Math.sin(yaw) * fwd + Math.cos(yaw) * side) * v;
  camera.position.z += (-Math.cos(yaw) * fwd - Math.sin(yaw) * side) * v;
  eye = Math.max(1.7, eye + ((keys.has("Space") ? 1 : 0) - (keys.has("KeyC") ? 1 : 0)) * v);
  if (MESH_LEVEL) floorY = groundY(camera.position.x, camera.position.z) ?? floorY;
  camera.position.y = floorY + eye;
  ground.position.set(camera.position.x, 0, camera.position.z);
  sun.target.position.copy(camera.position);
  sun.position.copy(camera.position).addScaledVector(sunDir, 300);
  sky.position.copy(camera.position);
  if (!MESH_LEVEL) updateChunks();
  else if (lodTick && now - lodAt > 300) {
    lodAt = now;
    lodTick();
  }
  renderer.render(scene, camera);
  frames++;
  if (now - fpsAt > 500) {
    fps = Math.round((frames * 1000) / (now - fpsAt));
    frames = 0;
    fpsAt = now;
  }
  const live = [...loaded.values()].filter(Boolean).length;
  hud.textContent = `fps ${fps}  chunks ${live}/${meta.chunks.length}\nclick = look, WASD move, Shift fast, Space/C up/down\n${meta.attribution}${meshAttribution ? `\n${meshAttribution}` : ""}`;
});
