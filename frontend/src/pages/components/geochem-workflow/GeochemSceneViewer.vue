<template>
  <div class="scene-shell">
    <div class="scene-toolbar">
      <label v-for="item in layerControls" :key="item.key">
        <el-switch v-model="item.visible" @change="toggleLayer(item)" />
        <span class="swatch" :style="{ background: item.color }"></span>
        {{ item.label }}
      </label>
      <label class="opacity">
        地质体透明度
        <el-slider v-model="geobodyOpacity" :min="5" :max="80" @input="applyOpacity" />
      </label>
      <el-button-group class="view-presets">
        <el-button @click="setCameraView('front')">正视</el-button>
        <el-button @click="setCameraView('side')">侧视</el-button>
        <el-button @click="setCameraView('top')">俯视</el-button>
      </el-button-group>
      <el-button @click="fitCamera">复位视角</el-button>
    </div>
    <div ref="host" class="viewer">
      <div v-if="loading" class="loading">正在装配地质体、断层、钻孔与元素场景…</div>
      <div v-if="error" class="scene-error">{{ error }}</div>
      <div v-if="hoverInfo.visible" class="hover-card" :style="{ left: `${hoverInfo.x}px`, top: `${hoverInfo.y}px` }">
        <div class="hover-title">{{ manifest.element }} 元素浓度场</div>
        <div>X: {{ hoverInfo.position[0] }}</div>
        <div>Y: {{ hoverInfo.position[1] }}</div>
        <div>Z: {{ hoverInfo.position[2] }}</div>
      </div>
      <div class="orientation">Z ↑<br />拖动旋转 · 滚轮缩放</div>
      <div class="coordinate-note">X：东向 · Y：北向 · Z：高程</div>
    </div>
    <div class="legend">
      <b>{{ isStatisticalAnomaly ? `${manifest.element} 统计异常体专业分级` : `${manifest.element} 内部相对浓度连续色带（公共六色锚点）` }}</b>
      <span v-for="item in manifest.legend" :key="item.level">
        <i :style="{ background: item.color }"></i>{{ displayLevel(item.level) }}
        <small>{{ item.measured_interval_count ?? 0 }}</small>
      </span>
      <em v-if="isStatisticalAnomaly">
        当前仅显示 ≥ {{ formatPpm(manifest.layers?.element_layer?.threshold_ppm) }} ppm 的体素，
        共 {{ Number(manifest.layers?.element_layer?.voxel_count || 0).toLocaleString("zh-CN") }} 个；切换“完整浓度场（参考）”可查看未裁剪结果。
      </em>
      <em v-else>
        当前连续显示范围：P05 {{ formatPpm(manifest.layers?.element_layer?.display_low_ppm) }} –
        P98 {{ formatPpm(manifest.layers?.element_layer?.display_high_ppm) }} ppm；色带位置表示本元素内部相对变化，不等同于矿产品位等级。
        六级名称及数字保留统一业务语义与实测区间统计；可靠性另行记录。
      </em>
      <p v-if="manifest.background_definition" class="background-note">
        <b>背景值：</b>{{ manifest.background_definition.value_ppm ?? "未获得" }} ppm。
        {{ manifest.background_definition.meaning }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { http } from "../../../api/http";

const props = defineProps<{ manifest: any; jobId: string }>();
const host = ref<HTMLDivElement | null>(null);
const loading = ref(true);
const error = ref("");
const hoverInfo = reactive({
  visible: false,
  x: 0,
  y: 0,
  position: ["-", "-", "-"] as [string, string, string],
});
const geobodyOpacity = ref(Math.round(Number(props.manifest.layers?.geobody?.opacity || 0.22) * 100));
const isStatisticalAnomaly = computed(() => props.manifest.layers?.element_layer?.display_mode === "statistical_anomaly");
const layerControls = reactive([
  { key: "geobody", label: "地质体", color: "#7AA6C2", visible: true },
  { key: "fault", label: "断层", color: "#F59E0B", visible: true },
  { key: "drillholes", label: "项目钻孔", color: "#E2E8F0", visible: true },
  { key: "assay_intervals", label: "当前元素有效化验孔", color: "#10B981", visible: true },
  { key: "element_layer", label: "完整浓度场", color: "#FDD0C4", visible: true },
  { key: "statistical_anomaly_layer", label: "统计异常体", color: "#FC8A6A", visible: false },
  { key: "field_difference_layer", label: "专家模型 − 原模型差值场", color: "#F87171", visible: true },
]);

let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let renderer: THREE.WebGLRenderer;
let controls: OrbitControls;
let root: THREE.Group;
let animation = 0;
let resizeObserver: ResizeObserver | null = null;
const objects = new Map<string, THREE.Object3D>();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let origin = new THREE.Vector3(...(props.manifest.coordinate_origin || [0, 0, 0]));

function displayLevel(level: any) {
  const text = String(level ?? "");
  return ({ "明显正异常": "明显异常", "最低边界品位": "边界品位", "工业品位": "最低工业品位" } as Record<string, string>)[text] || text;
}

function formatPpm(value: any) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "暂无";
  return number >= 1000 ? number.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) : number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function syncControlLabels() {
  const drillholes = layerControls.find((item) => item.key === "drillholes");
  const assays = layerControls.find((item) => item.key === "assay_intervals");
  const element = layerControls.find((item) => item.key === "element_layer");
  const anomaly = layerControls.find((item) => item.key === "statistical_anomaly_layer");
  const difference = layerControls.find((item) => item.key === "field_difference_layer");
  if (drillholes) drillholes.label = `${Number(props.manifest.layers?.drillholes?.project_count || 0)} 个项目钻孔`;
  if (assays) assays.label = `${Number(props.manifest.layers?.assay_intervals?.hole_count || 0)} 个当前元素有效化验孔`;
  if (element) {
    element.label = props.manifest.layers?.element_layer?.label || `${props.manifest.element || "当前元素"} 完整浓度场`;
    element.visible = props.manifest.layers?.element_layer?.visible !== false;
  }
  if (anomaly) {
    anomaly.label = props.manifest.layers?.statistical_anomaly_layer?.label || "统计异常体";
    anomaly.visible = props.manifest.layers?.statistical_anomaly_layer?.visible === true;
  }
  if (difference) {
    difference.label = props.manifest.layers?.field_difference_layer?.label || "专家模型 − 原模型差值场";
    difference.visible = props.manifest.layers?.field_difference_layer?.visible !== false;
  }
}

function assetUrl(file: string) {
  return `${props.manifest.asset_base_url}${String(file).split("/").map(encodeURIComponent).join("/")}`;
}

async function arrayBuffer(file: string) {
  const response = await http.get(assetUrl(file), { responseType: "arraybuffer" });
  return response.data;
}
async function jsonAsset(file: string) {
  const response = await http.get(assetUrl(file));
  return response.data;
}

function materialOpacity(material: THREE.Material, value: number) {
  const target: any = material;
  target.opacity = value;
  target.transparent = value < 1;
  target.depthWrite = value >= 0.7;
  target.needsUpdate = true;
}

async function loadStl(key: string, descriptor: any) {
  if (!descriptor?.file) return;
  const geometry = new STLLoader().parse(await arrayBuffer(descriptor.file));
  geometry.translate(-origin.x, -origin.y, -origin.z);
  geometry.computeVertexNormals();
  const material = new THREE.MeshStandardMaterial({
    color: descriptor.color,
    opacity: descriptor.opacity,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    roughness: 0.88,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = key;
  root.add(mesh);
  objects.set(key, mesh);
}

async function loadDrillholes(descriptor: any) {
  if (!descriptor?.file) return;
  const payload = await jsonAsset(descriptor.file);
  const group = new THREE.Group();
  group.name = "drillholes";
  for (const hole of payload.drillholes || []) {
    const points = (hole.points || []).map((point: number[]) =>
      new THREE.Vector3(point[0] - origin.x, point[1] - origin.y, point[2] - origin.z),
    );
    if (points.length < 2) continue;
    const curve = new THREE.CatmullRomCurve3(points);
    const geometry = new THREE.TubeGeometry(
      curve,
      Math.max(8, Math.min(64, points.length * 4)),
      hole.has_chemical_assays ? 5.5 : 3.2,
      6,
      false,
    );
    const material = new THREE.MeshStandardMaterial({
      color: hole.has_chemical_assays ? 0x49b6ff : 0xd7dee8,
      transparent: true,
      opacity: hole.has_chemical_assays ? 1 : 0.62,
      roughness: 0.75,
    });
    const line = new THREE.Mesh(geometry, material);
    line.userData = { holeId: hole.hole_id, hasChemicalAssays: hole.has_chemical_assays };
    group.add(line);
  }
  root.add(group);
  objects.set("drillholes", group);
}

async function loadAssays(descriptor: any) {
  if (!descriptor?.file) return;
  const payload = await jsonAsset(descriptor.file);
  const group = new THREE.Group();
  group.name = "assay_intervals";
  for (const interval of payload.intervals || []) {
    if (![interval.x, interval.y, interval.z].every((value) => Number.isFinite(Number(value)))) continue;
    const start = new THREE.Vector3(
      Number(interval.x_from ?? interval.x) - origin.x,
      Number(interval.y_from ?? interval.y) - origin.y,
      Number(interval.z_from ?? interval.z) - origin.z,
    );
    const end = new THREE.Vector3(
      Number(interval.x_to ?? interval.x) - origin.x,
      Number(interval.y_to ?? interval.y) - origin.y,
      Number(interval.z_to ?? interval.z) - origin.z,
    );
    const direction = end.clone().sub(start);
    const length = Math.max(direction.length(), 0.5);
    const geometry = new THREE.CylinderGeometry(10, 10, length, 7);
    const material = new THREE.MeshBasicMaterial({
      color: interval.display_color || "#94A3B8",
      depthTest: false,
      depthWrite: false,
    });
    const segment = new THREE.Mesh(geometry, material);
    segment.position.copy(start).add(end).multiplyScalar(0.5);
    if (direction.lengthSq() > 0) {
      segment.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        direction.normalize(),
      );
    }
    segment.renderOrder = 20;
    segment.userData = interval;
    group.add(segment);
  }
  removeObject("assay_intervals");
  root.add(group);
  objects.set("assay_intervals", group);
}

async function loadElementLayer(key: string, descriptor: any) {
  if (!descriptor?.file) return;
  const geometry = new PLYLoader().parse(await arrayBuffer(descriptor.file));
  geometry.translate(-origin.x, -origin.y, -origin.z);
  const hasFaces = !!geometry.index;
  const material = hasFaces
    ? new THREE.MeshStandardMaterial({ vertexColors: true, transparent: false, opacity: 1, side: THREE.DoubleSide })
    : new THREE.PointsMaterial({ vertexColors: true, size: 2.35, sizeAttenuation: false, transparent: false, opacity: 1 });
  const object = hasFaces ? new THREE.Mesh(geometry, material) : new THREE.Points(geometry, material);
  object.name = key;
  object.visible = !!descriptor.visible;
  removeObject(key);
  root.add(object);
  objects.set(key, object);
}

function applyVisibility() {
  for (const control of layerControls) {
    const object = objects.get(control.key);
    if (object) object.visible = control.visible;
  }
}

function hideHover() {
  hoverInfo.visible = false;
}

function formatCoordinate(value: number) {
  return Number.isFinite(value) ? value.toFixed(2) : "-";
}

function onPointerMove(event: PointerEvent) {
  const elementLayer = objects.get("element_layer");
  if (!renderer || !camera || !elementLayer?.visible) {
    hideHover();
    return;
  }
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  raycaster.params.Points.threshold = 8;
  const hit = raycaster.intersectObject(elementLayer, true)[0];
  if (!hit) {
    hideHover();
    return;
  }
  const coordinate = hit.point.clone().add(origin);
  hoverInfo.visible = true;
  hoverInfo.x = Math.min(event.clientX + 16, window.innerWidth - 180);
  hoverInfo.y = Math.min(event.clientY + 16, window.innerHeight - 110);
  hoverInfo.position = [
    formatCoordinate(coordinate.x),
    formatCoordinate(coordinate.y),
    formatCoordinate(coordinate.z),
  ];
}

function removeObject(key: string) {
  const object: any = objects.get(key);
  if (!object) return;
  root.remove(object);
  object.traverse?.((child: any) => {
    child.geometry?.dispose?.();
    if (Array.isArray(child.material)) child.material.forEach((item: any) => item.dispose?.());
    else child.material?.dispose?.();
  });
  objects.delete(key);
}

async function toggleLayer(control: { key: string; visible: boolean }) {
  if (control.key === "field_difference_layer" && control.visible) {
    const fullField = layerControls.find((item) => item.key === "element_layer");
    if (fullField) fullField.visible = false;
  }
  if (control.key === "element_layer" && control.visible) {
    const difference = layerControls.find((item) => item.key === "field_difference_layer");
    if (difference) difference.visible = false;
  }
  if (control.key.startsWith("element_") && control.visible && !objects.has(control.key)) {
    loading.value = true;
    error.value = "";
    try {
      const descriptor = props.manifest.layers?.[control.key];
      await loadElementLayer(control.key, descriptor);
    } catch (reason: any) {
      control.visible = false;
      error.value = reason?.response?.data?.detail || reason?.message || "插值趋势体加载失败";
    } finally {
      loading.value = false;
    }
  }
  applyVisibility();
}
function applyOpacity() {
  const object = objects.get("geobody") as THREE.Mesh | undefined;
  if (!object) return;
  const materials = Array.isArray(object.material) ? object.material : [object.material];
  materials.forEach((material) => materialOpacity(material, geobodyOpacity.value / 100));
}

function fitCamera() {
  if (!root || !camera || !controls) return;
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const max = Math.max(size.x, size.y, size.z, 100);
  // The tunnel is long in X and narrow in Y.  A near-side elevation makes
  // dipping/irregular geochemical bodies visible instead of flattening them
  // into a misleading plan-view strip.
  camera.position.set(center.x, center.y - max * 1.18, center.z + max * 0.24);
  camera.near = Math.max(0.1, max / 5000);
  camera.far = max * 30;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

function setCameraView(view: "front" | "side" | "top") {
  if (!root || !camera || !controls) return;
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.y, size.z, 100);
  if (view === "top") camera.position.set(center.x, center.y, center.z + span * 1.35);
  else if (view === "side") {
    const crossSpan = Math.max(size.y, size.z, 100);
    camera.position.set(center.x + crossSpan * 2.3, center.y, center.z + crossSpan * 0.12);
  }
  else camera.position.set(center.x, center.y - span * 1.18, center.z + span * 0.24);
  camera.up.set(0, 0, 1);
  camera.near = Math.max(0.1, span / 5000);
  camera.far = span * 30;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

async function buildScene() {
  if (!host.value) return;
  loading.value = true;
  error.value = "";
  try {
    origin = new THREE.Vector3(...(props.manifest.coordinate_origin || [0, 0, 0]));
    syncControlLabels();
    scene = new THREE.Scene();
    scene.background = new THREE.Color("#111D30");
    camera = new THREE.PerspectiveCamera(42, host.value.clientWidth / host.value.clientHeight, 0.1, 100000);
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(host.value.clientWidth, host.value.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.value.appendChild(renderer.domElement);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerleave", hideHover);
    root = new THREE.Group();
    scene.add(root);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x1b2940, 1.6));
    const light = new THREE.DirectionalLight(0xffffff, 1.25);
    light.position.set(1, -1, 2);
    scene.add(light);

    const layers = props.manifest.layers || {};
    await Promise.all([
      loadStl("geobody", layers.geobody),
      loadStl("fault", layers.fault),
      loadDrillholes(layers.drillholes),
      loadAssays(layers.assay_intervals),
      layers.element_layer?.visible ? loadElementLayer("element_layer", layers.element_layer) : Promise.resolve(),
      layers.statistical_anomaly_layer?.file ? loadElementLayer("statistical_anomaly_layer", layers.statistical_anomaly_layer) : Promise.resolve(),
      layers.field_difference_layer?.file ? loadElementLayer("field_difference_layer", layers.field_difference_layer) : Promise.resolve(),
    ]);
    applyVisibility();
    applyOpacity();
    fitCamera();
    resizeObserver = new ResizeObserver(() => {
      if (!host.value) return;
      camera.aspect = host.value.clientWidth / host.value.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(host.value.clientWidth, host.value.clientHeight);
    });
    resizeObserver.observe(host.value);
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      animation = requestAnimationFrame(render);
    };
    render();
  } catch (reason: any) {
    error.value = reason?.response?.data?.detail || reason?.message || "三维场景装配失败";
  } finally {
    loading.value = false;
  }
}

function dispose() {
  cancelAnimationFrame(animation);
  resizeObserver?.disconnect();
  if (root) {
    root.traverse((object: any) => {
      object.geometry?.dispose?.();
      if (Array.isArray(object.material)) object.material.forEach((item: any) => item.dispose?.());
      else object.material?.dispose?.();
    });
  }
  controls?.dispose();
  if (renderer?.domElement) {
    renderer.domElement.removeEventListener("pointermove", onPointerMove);
    renderer.domElement.removeEventListener("pointerleave", hideHover);
  }
  renderer?.dispose();
  renderer?.domElement?.remove();
}

watch(() => [props.jobId, props.manifest?.element, props.manifest?.layers?.element_layer?.file, props.manifest?.layers?.statistical_anomaly_layer?.file].join("|"), async () => {
  if (!root) return;
  error.value = "";
  syncControlLabels();
  try {
    await Promise.all([
      loadElementLayer("element_layer", props.manifest.layers?.element_layer),
      props.manifest.layers?.statistical_anomaly_layer?.file
        ? loadElementLayer("statistical_anomaly_layer", props.manifest.layers.statistical_anomaly_layer)
        : Promise.resolve(),
      props.manifest.layers?.field_difference_layer?.file
        ? loadElementLayer("field_difference_layer", props.manifest.layers.field_difference_layer)
        : Promise.resolve(),
      loadAssays(props.manifest.layers?.assay_intervals),
    ]);
    applyVisibility();
  } catch (reason: any) {
    error.value = reason?.response?.data?.detail || reason?.message || "新元素浓度场加载失败，已保留原场景";
  }
}, { deep: false });
onMounted(buildScene);
onBeforeUnmount(dispose);
</script>

<style scoped>
.scene-shell{overflow:hidden;border:1px solid #dbe5ef;border-radius:14px;background:#fff}.scene-toolbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid #e5ebf2}.scene-toolbar label{display:flex;align-items:center;gap:7px;color:#50657d;font-size:12px}.swatch{width:9px;height:9px;border-radius:50%}.opacity{margin-left:auto}.opacity :deep(.el-slider){width:105px}.viewer{position:relative;height:640px;overflow:hidden}.loading,.scene-error{position:absolute;z-index:2;inset:0;display:grid;place-items:center;background:rgba(17,29,48,.82);color:#dce8f7}.scene-error{color:#fecaca}.hover-card{position:fixed;z-index:20;min-width:140px;padding:10px 12px;border-radius:10px;background:rgba(8,15,28,.88);color:#f4f8ff;font-size:12px;line-height:1.5;pointer-events:none;box-shadow:0 12px 24px rgba(0,0,0,.18)}.hover-title{margin-bottom:6px;font-size:13px;font-weight:700}.orientation,.coordinate-note{position:absolute;z-index:1;right:14px;bottom:12px;padding:8px 10px;border-radius:7px;background:rgba(7,16,29,.62);color:#cbd8e8;font-size:11px;line-height:1.5}.coordinate-note{right:auto;left:14px}.legend{display:flex;align-items:center;gap:13px;flex-wrap:wrap;padding:11px 14px;color:#52667d;font-size:12px}.legend b{color:#294866}.legend span{display:flex;align-items:center;gap:5px}.legend i{width:18px;height:8px;border-radius:3px}.legend small{min-width:22px;color:#9a4b45;font-weight:700}.legend em{margin-left:auto;color:#8190a2;font-style:normal}.background-note{flex-basis:100%;margin:0;padding:9px 11px;border-radius:8px;background:#f8fafc;color:#64748b;line-height:1.6}.background-note b{color:#294866}@media(max-width:900px){.viewer{height:480px}.opacity{margin-left:0}}
</style>
