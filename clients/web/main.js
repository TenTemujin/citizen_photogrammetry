// Minimal chunk-streaming viewer: flat ground, extruded OSM footprints, no tokens needed.
// Serve from the repo root: python -m http.server 8000, then open /clients/web/
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const CHUNKS_URL = "../../chunks";
const MESH_LEVEL = new URLSearchParams(location.search).get("mesh"); // ?mesh=16 shows the reality mesh (meshes/L16) instead of OSM extrusions
const LOAD_RADIUS = 3; // chunks around the camera kept loaded; unload beyond +1
const SPEED = 12; // m/s, hold Shift for x4
const M_PER_DEG_LAT = 111320;

const hud = document.getElementById("hud");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fb6c9);
scene.fog = new THREE.Fog(0x9fb6c9, 150, 500);
scene.add(new THREE.HemisphereLight(0xffffff, 0x556655, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(100, 200, 50);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.5, 1000);
camera.position.set(0, 1.7, 0);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
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
const meshTiles = [];
let meshAttribution = "";
if (MESH_LEVEL) {
  const base = `../../meshes/L${MESH_LEVEL}`;
  const mi = await (await fetch(`${base}/index.json`, { cache: "no-store" })).json();
  meshAttribution = mi.attribution;
  scene.remove(ground);
  const loader = new GLTFLoader();
  await Promise.all(mi.tiles.map(async (t) => {
    const gltf = await loader.loadAsync(`${base}/${t.file}`);
    scene.add(gltf.scene);
    meshTiles.push({ root: gltf.scene, t });
  }));
}

const down = new THREE.Vector3(0, -1, 0);
const ray = new THREE.Raycaster();
// height of the mesh surface under (x, z); null where no tile covers the point
function groundY(x, z) {
  const roots = meshTiles
    .filter(({ t }) => x >= t.east[0] && x <= t.east[1] && -z >= t.north[0] && -z <= t.north[1])
    .map((m) => m.root);
  ray.set(new THREE.Vector3(x, 500, z), down);
  const hit = ray.intersectObjects(roots, true)[0];
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
  if (!MESH_LEVEL) updateChunks();
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
