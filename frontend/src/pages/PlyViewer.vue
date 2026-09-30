<template>
  <div ref="wrap" class="viewer-wrap">
    <div ref="viewerEl" class="viewer"></div>
    <div v-if="hoverInfo.visible" class="hover-card" :style="{ left: `${hoverInfo.x}px`, top: `${hoverInfo.y}px` }">
      <div class="hover-title">{{ hoverInfo.label }}</div>
      <div v-if="hoverInfo.description" class="hover-desc">{{ hoverInfo.description }}</div>
      <div class="hover-coord">X: {{ hoverInfo.position[0] }}</div>
      <div class="hover-coord">Y: {{ hoverInfo.position[1] }}</div>
      <div class="hover-coord">Z: {{ hoverInfo.position[2] }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { http } from "../api/http";

const props = defineProps<{
  jobId: string;
  file: string;
  mode?: "mesh" | "points" | "auto";
  pointSize?: number;
  fixedPointSize?: boolean;
  background?: string;
  colorMapFile?: string;
  downloadPrefix?: string;
  scalarLabel?: string;
  scalarDescription?: string;
  overlayFiles?: string[];
  overlayPointSize?: number;
  pointOpacity?: number;
  hiddenColors?: string[];
  cameraPreset?: "front" | "oblique";
  showReferenceFrame?: boolean;
  verticalExaggeration?: number;
}>();

const wrap = ref<HTMLDivElement | null>(null);
const viewerEl = ref<HTMLDivElement | null>(null);

const hoverInfo = reactive({
  visible: false,
  x: 0,
  y: 0,
  label: "",
  description: "",
  position: ["-", "-", "-"] as [string, string, string],
});

let renderer: THREE.WebGLRenderer | null = null;
let scene: THREE.Scene | null = null;
let camera: THREE.PerspectiveCamera | null = null;
let controls: OrbitControls | null = null;
let obj: THREE.Object3D | null = null;
let raf = 0;
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let geometryCenter = new THREE.Vector3();
let activeVerticalExaggeration = 1;
let colorEntries: Array<{ r: number; g: number; b: number; label: string }> = [];
const tempColor = new THREE.Color();

function downloadUrl() {
  return props.downloadPrefix || `/api/reconstruct/jobs/${props.jobId}/download`;
}

function cleanLabel(raw: unknown) {
  let label = String(raw ?? "").trim();
  const quotePairs: Array<[string, string]> = [
    ['"', '"'],
    ["'", "'"],
    ["“", "”"],
    ["‘", "’"],
  ];
  let changed = true;
  while (changed && label.length >= 2) {
    changed = false;
    for (const [left, right] of quotePairs) {
      if (label.startsWith(left) && label.endsWith(right)) {
        label = label.slice(1, -1).trim();
        changed = true;
      }
    }
  }
  return label;
}

function fmt(num: number) {
  return Number.isFinite(num) ? num.toFixed(2) : "-";
}

function maxFiniteDimension(size: THREE.Vector3) {
  const value = Math.max(size.x, size.y, size.z);
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function hideHover() {
  hoverInfo.visible = false;
}

function disposeObject(o: THREE.Object3D) {
  o.traverse((node: any) => {
    if (node.geometry) node.geometry.dispose?.();
    if (node.material) {
      if (Array.isArray(node.material)) node.material.forEach((m: any) => m.dispose?.());
      else node.material.dispose?.();
    }
  });
}

function resize() {
  if (!viewerEl.value || !renderer || !camera) return;
  const w = viewerEl.value.clientWidth;
  const h = viewerEl.value.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function animate() {
  raf = requestAnimationFrame(animate);
  controls?.update();
  renderer?.render(scene!, camera!);
}

function guessIsMesh(geom: THREE.BufferGeometry) {
  const pos = geom.getAttribute("position");
  if (!pos) return false;
  if (geom.index && geom.index.count >= 3) return true;
  if (pos.count % 3 === 0 && pos.count >= 300) return true;
  return false;
}

async function loadColorMap() {
  colorEntries = [];
  if (!props.jobId || !props.colorMapFile) return;

  try {
    const res = await http.get(downloadUrl(), {
      params: { file: props.colorMapFile },
      responseType: "text",
      transformResponse: [(data) => data],
    });
    const rows = JSON.parse(res.data);
    if (!Array.isArray(rows)) return;
    for (const row of rows) {
      const r = Number(row?.R);
      const g = Number(row?.G);
      const b = Number(row?.B);
      const labelKey = Object.keys(row || {}).find((key) => !["R", "G", "B", "A", "HEX"].includes(key));
      const label = cleanLabel(
        row?.["\u5ca9\u6027\u540d\u79f0"] ||
          row?.["label"] ||
          row?.["name"] ||
          (labelKey ? row?.[labelKey] : "") ||
          ""
      );
      if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b) || !label) continue;
      colorEntries.push({ r, g, b, label });
    }
  } catch {
    colorEntries = [];
  }

  if (colorEntries.length > 0) return;

  const csvFile = props.colorMapFile.toLowerCase().endsWith(".json")
    ? props.colorMapFile.replace(/\.json$/i, ".csv")
    : props.colorMapFile;

  try {
    const res = await http.get(downloadUrl(), {
      params: { file: csvFile },
      responseType: "text",
      transformResponse: [(data) => data],
    });
    const text = String(res.data || "").trim();
    if (!text) return;

    const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) return;
    const headers = lines[0].split(",").map((x) => x.trim());
    const labelIdx = headers.findIndex((header) => !["R", "G", "B", "A", "HEX"].includes(header));
    const rIdx = headers.indexOf("R");
    const gIdx = headers.indexOf("G");
    const bIdx = headers.indexOf("B");
    if (labelIdx < 0 || rIdx < 0 || gIdx < 0 || bIdx < 0) return;

    for (const line of lines.slice(1)) {
      const cols = line.split(",").map((x) => x.trim());
      const label = cleanLabel(cols[labelIdx] || "");
      const r = Number(cols[rIdx]);
      const g = Number(cols[gIdx]);
      const b = Number(cols[bIdx]);
      if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b) || !label) continue;
      colorEntries.push({ r, g, b, label });
    }
  } catch {
    colorEntries = [];
  }
}

function getVertexColor(attr: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, index: number) {
  let r = attr.getX(index);
  let g = attr.getY(index);
  let b = attr.getZ(index);

  if (attr.normalized) {
    r *= 255;
    g *= 255;
    b *= 255;
  } else if (Math.max(r, g, b) <= 1) {
    tempColor.setRGB(r, g, b);
    tempColor.convertLinearToSRGB();
    r = tempColor.r * 255;
    g = tempColor.g * 255;
    b = tempColor.b * 255;
  }

  return { r, g, b };
}

function nearestLabel(rgb: { r: number; g: number; b: number }) {
  if (!colorEntries.length) return props.scalarLabel ? `元素：${props.scalarLabel}` : "\u8272\u8868\u672a\u52a0\u8f7d";

  let bestLabel = "\u672a\u77e5\u5ca9\u6027";
  let bestDist = Number.POSITIVE_INFINITY;

  for (const entry of colorEntries) {
    const dr = entry.r - rgb.r;
    const dg = entry.g - rgb.g;
    const db = entry.b - rgb.b;
    const dist = dr * dr + dg * dg + db * db;
    if (dist < bestDist) {
      bestDist = dist;
      bestLabel = entry.label;
    }
  }

  return bestLabel;
}

function majorityLabel(colors: Array<{ r: number; g: number; b: number }>) {
  const votes = new Map<string, number>();
  for (const rgb of colors) {
    const label = nearestLabel(rgb);
    votes.set(label, (votes.get(label) || 0) + 1);
  }

  let bestLabel = "\u672a\u77e5\u5ca9\u6027";
  let bestCount = -1;
  for (const [label, count] of votes.entries()) {
    if (count > bestCount) {
      bestLabel = label;
      bestCount = count;
    }
  }
  return bestLabel;
}

function nearestVertexLabel(
  hitPoint: THREE.Vector3,
  vertices: Array<{ pos: THREE.Vector3; color: { r: number; g: number; b: number } }>
) {
  let bestLabel = "\u672a\u77e5\u5ca9\u6027";
  let bestDist = Number.POSITIVE_INFINITY;

  for (const vertex of vertices) {
    const dist = hitPoint.distanceToSquared(vertex.pos);
    if (dist < bestDist) {
      bestDist = dist;
      bestLabel = nearestLabel(vertex.color);
    }
  }

  return bestLabel;
}

function getLabelFromIntersection(hit: THREE.Intersection<THREE.Object3D>) {
  const target = hit.object as THREE.Points | THREE.Mesh;
  const geom = target.geometry as THREE.BufferGeometry;
  const colorAttr = geom.getAttribute("color");
  if (!colorAttr) return "\u672a\u77e5\u5ca9\u6027";

  let rgb = { r: 200, g: 200, b: 200 };

  if (target instanceof THREE.Points && hit.index != null) {
    rgb = getVertexColor(colorAttr, hit.index);
    return nearestLabel(rgb);
  }

  if (target instanceof THREE.Mesh) {
    const face = hit.face;
    if (face) {
      const c1 = getVertexColor(colorAttr, face.a);
      const c2 = getVertexColor(colorAttr, face.b);
      const c3 = getVertexColor(colorAttr, face.c);
      const posAttr = geom.getAttribute("position");
      if (posAttr) {
        const p1 = new THREE.Vector3(posAttr.getX(face.a), posAttr.getY(face.a), posAttr.getZ(face.a));
        const p2 = new THREE.Vector3(posAttr.getX(face.b), posAttr.getY(face.b), posAttr.getZ(face.b));
        const p3 = new THREE.Vector3(posAttr.getX(face.c), posAttr.getY(face.c), posAttr.getZ(face.c));
        return nearestVertexLabel(hit.point, [
          { pos: p1, color: c1 },
          { pos: p2, color: c2 },
          { pos: p3, color: c3 },
        ]);
      }
      return majorityLabel([c1, c2, c3]);
    }
  }

  return nearestLabel(rgb);
}

function onPointerMove(event: PointerEvent) {
  if (!viewerEl.value || !camera || !obj) return;
  const rect = viewerEl.value.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

  raycaster.setFromCamera(pointer, camera);
  raycaster.params.Points.threshold = 8;
  const hits = raycaster
    .intersectObject(obj, true)
    .filter((hit) => !hit.object.userData.nonInteractive);

  if (!hits.length) {
    hideHover();
    return;
  }

  const hit = hits[0];
  const world = hit.point.clone();
  world.z /= activeVerticalExaggeration;
  world.add(geometryCenter);
  hoverInfo.visible = true;
  hoverInfo.x = Math.min(event.clientX + 16, window.innerWidth - 180);
  hoverInfo.y = Math.min(event.clientY + 16, window.innerHeight - 110);
  hoverInfo.label = getLabelFromIntersection(hit);
  hoverInfo.description = props.scalarDescription || "";
  hoverInfo.position = [fmt(world.x), fmt(world.y), fmt(world.z)];
}

function filterHiddenPointColors(geometry: THREE.BufferGeometry) {
  const hidden = new Set(
    (props.hiddenColors || [])
      .map((value) => String(value).replace("#", "").trim().toUpperCase())
      .filter(Boolean)
  );
  const colorAttr = geometry.getAttribute("color");
  const positionAttr = geometry.getAttribute("position");
  if (!hidden.size || !colorAttr || !positionAttr) return geometry;

  const kept: number[] = [];
  for (let index = 0; index < positionAttr.count; index += 1) {
    let r = colorAttr.getX(index);
    let g = colorAttr.getY(index);
    let b = colorAttr.getZ(index);
    if (Math.max(r, g, b) <= 1.0001) {
      tempColor.setRGB(r, g, b);
      tempColor.convertLinearToSRGB();
      r = tempColor.r * 255;
      g = tempColor.g * 255;
      b = tempColor.b * 255;
    }
    const hex = [r, g, b]
      .map((value) => Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase();
    if (!hidden.has(hex)) kept.push(index);
  }
  if (kept.length === positionAttr.count) return geometry;

  const filtered = new THREE.BufferGeometry();
  for (const [name, attribute] of Object.entries(geometry.attributes)) {
    const source = attribute as THREE.BufferAttribute;
    const ArrayType = (source.array as any).constructor;
    const target = new ArrayType(kept.length * source.itemSize);
    kept.forEach((sourceIndex, targetIndex) => {
      for (let component = 0; component < source.itemSize; component += 1) {
        target[targetIndex * source.itemSize + component] =
          source.array[sourceIndex * source.itemSize + component];
      }
    });
    filtered.setAttribute(name, new THREE.BufferAttribute(target, source.itemSize, source.normalized));
  }
  geometry.dispose();
  return filtered;
}

async function loadPLY() {
  if (!scene || !camera || !props.jobId || !props.file) return;
  await loadColorMap();

  if (obj) {
    scene.remove(obj);
    disposeObject(obj);
    obj = null;
  }

  const res = await http.get(downloadUrl(), {
    params: { file: props.file },
    responseType: "arraybuffer",
  });

  const loader = new PLYLoader();
  let geom = loader.parse(res.data);
  geom = filterHiddenPointColors(geom);
  geom.computeBoundingBox?.();
  const box = geom.boundingBox!;
  const size = new THREE.Vector3();
  box.getSize(size);
  geometryCenter = new THREE.Vector3();
  box.getCenter(geometryCenter);
  geom.translate(-geometryCenter.x, -geometryCenter.y, -geometryCenter.z);

  const hasColor = !!geom.getAttribute("color");
  const mode = props.mode ?? "auto";
  const shouldMesh = mode === "mesh" ? true : mode === "points" ? false : guessIsMesh(geom);

  let primaryObject: THREE.Object3D;
  if (shouldMesh) {
    geom.computeVertexNormals?.();
    const mat = new THREE.MeshStandardMaterial({
      vertexColors: hasColor,
      roughness: 0.95,
      metalness: 0,
      side: THREE.DoubleSide,
      flatShading: true,
    });
    primaryObject = new THREE.Mesh(geom, mat);
  } else {
    const mat = new THREE.PointsMaterial({
      vertexColors: hasColor,
      size: props.pointSize ?? 2,
      sizeAttenuation: !props.fixedPointSize,
      transparent: (props.pointOpacity ?? 1) < 1,
      opacity: props.pointOpacity ?? 1,
      depthWrite: (props.pointOpacity ?? 1) >= 1,
    });
    primaryObject = new THREE.Points(geom, mat);
  }

  const group = new THREE.Group();
  group.add(primaryObject);
  const overlayResults = await Promise.all(
    (props.overlayFiles || []).filter(Boolean).map(async (file) => {
      const overlayResponse = await http.get(downloadUrl(), {
        params: { file },
        responseType: "arraybuffer",
      });
      let overlayGeometry = loader.parse(overlayResponse.data);
      overlayGeometry = filterHiddenPointColors(overlayGeometry);
      overlayGeometry.translate(-geometryCenter.x, -geometryCenter.y, -geometryCenter.z);
      const overlayMaterial = new THREE.PointsMaterial({
        vertexColors: !!overlayGeometry.getAttribute("color"),
        size: props.overlayPointSize ?? 6,
        sizeAttenuation: false,
        depthTest: true,
      });
      return new THREE.Points(overlayGeometry, overlayMaterial);
    })
  );
  overlayResults.forEach((overlay) => group.add(overlay));
  activeVerticalExaggeration = Math.max(1, Number(props.verticalExaggeration) || 1);
  group.scale.z = activeVerticalExaggeration;
  if (props.showReferenceFrame && maxFiniteDimension(size) > 0) {
    const maxDim = maxFiniteDimension(size);
    const centeredBox = box.clone();
    centeredBox.translate(geometryCenter.clone().multiplyScalar(-1));
    const boxHelper = new THREE.Box3Helper(centeredBox, 0x6b7d99);
    boxHelper.userData.nonInteractive = true;
    group.add(boxHelper);

    const axes = new THREE.AxesHelper(maxDim * 0.16);
    axes.userData.nonInteractive = true;
    group.add(axes);

    const grid = new THREE.GridHelper(maxDim, 10, 0x50627f, 0x34435e);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -size.z / 2;
    grid.userData.nonInteractive = true;
    group.add(grid);
  }
  obj = group;
  scene.add(obj);

  const displaySize = size.clone();
  displaySize.z *= activeVerticalExaggeration;
  const maxDim = maxFiniteDimension(displaySize);
  if (props.cameraPreset === "oblique") {
    camera.position.set(maxDim * 1.2, -maxDim * 1.2, maxDim * 0.9);
  } else {
    camera.position.set(0, 0, maxDim * 1.8);
  }
  camera.near = Math.max(maxDim / 2000, 0.01);
  camera.far = maxDim * 20;
  camera.updateProjectionMatrix();
  controls?.target.set(0, 0, 0);
  controls?.update();
}

onMounted(() => {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(props.background ?? "#2b2f4a");

  const w = viewerEl.value!.clientWidth;
  const h = viewerEl.value!.clientHeight;
  camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100000);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(w, h);
  // @ts-ignore
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  viewerEl.value!.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.65));
  const dir1 = new THREE.DirectionalLight(0xffffff, 0.9);
  dir1.position.set(1, 1, 1);
  scene.add(dir1);
  const dir2 = new THREE.DirectionalLight(0xffffff, 0.5);
  dir2.position.set(-1, -0.5, 0.2);
  scene.add(dir2);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  renderer.domElement.addEventListener("pointermove", onPointerMove);
  renderer.domElement.addEventListener("pointerleave", hideHover);
  window.addEventListener("resize", resize);
  animate();
  loadPLY().catch(console.error);
});

watch(
  () => [
    props.jobId,
    props.file,
    props.mode,
    props.pointSize,
    props.fixedPointSize,
    props.background,
    props.colorMapFile,
    props.scalarLabel,
    (props.overlayFiles || []).join("|"),
    props.overlayPointSize,
    props.pointOpacity,
    (props.hiddenColors || []).join("|"),
    props.cameraPreset,
    props.showReferenceFrame,
    props.verticalExaggeration,
  ],
  () => loadPLY().catch(console.error)
);

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.removeEventListener("resize", resize);
  if (renderer?.domElement) {
    renderer.domElement.removeEventListener("pointermove", onPointerMove);
    renderer.domElement.removeEventListener("pointerleave", hideHover);
  }
  if (obj && scene) {
    scene.remove(obj);
    disposeObject(obj);
    obj = null;
  }
  controls?.dispose();
  renderer?.dispose();
  if (renderer?.domElement && viewerEl.value) viewerEl.value.removeChild(renderer.domElement);
});
</script>

<style scoped>
.viewer-wrap {
  position: relative;
}

.viewer {
  width: 100%;
  height: 520px;
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
}

.hover-card {
  position: fixed;
  z-index: 20;
  min-width: 140px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(8, 15, 28, 0.88);
  color: #f4f8ff;
  font-size: 12px;
  pointer-events: none;
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.18);
}

.hover-title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 6px;
}

.hover-desc {
  opacity: 0.88;
  line-height: 1.5;
  margin-bottom: 4px;
}

.hover-coord {
  opacity: 0.9;
  line-height: 1.5;
}
</style>
