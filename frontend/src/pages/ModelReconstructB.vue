<template>
  <div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
      <h2 style="margin:0;">三维重建（模型 {{ modelId }}）</h2>
      <div style="display:flex; gap:8px;">
        <el-button @click="refreshJob" :disabled="!jobId">刷新状态</el-button>
        <el-button type="warning" plain @click="clearJob" :disabled="!jobId">清空本次任务</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      style="margin: 12px 0;"
      title="上传：模型 STL可使用条件模拟"
    />

    <el-card>
      <template #header>
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <span>输入文件</span>
          <el-tag v-if="jobId" :type="statusType(job?.status)">{{ job?.status || 'unknown' }}</el-tag>
        </div>
      </template>

      <div class="grid">
        <div>
          <div class="label">模型 STL</div>
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="(f)=>onPickFile('model', f)"
            :on-remove="()=>onRemoveFile('model')"
          >
            <el-button>选择 STL</el-button>
          </el-upload>
          <div class="hint" v-if="modelFileName">已选：{{ modelFileName }}</div>
        </div>

        <div>
          <div class="label">条件模拟</div>
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
            <el-switch v-model="params.use_condsim" />

          </div>

          <el-upload
            :auto-upload="false"
            :limit="1"
            :disabled="!params.use_condsim"
            :on-change="(f)=>onPickFile('fault', f)"
            :on-remove="()=>onRemoveFile('fault')"
          >
            <el-button :disabled="!params.use_condsim">选择 fault.stl</el-button>
          </el-upload>
          <div class="hint" v-if="faultFileName">已选：{{ faultFileName }}</div>
        </div>


      </div>
    </el-card>

    <div style="height: 12px;"></div>

    <el-card>
      <template #header>
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <span>参数</span>
          <el-button type="primary" @click="start" :loading="starting">开始重建</el-button>
        </div>
      </template>

      <el-form :model="params" label-width="170px">
        <div class="grid2">
          <el-form-item label="voxel_size">
            <el-input-number v-model="params.voxel_size" :step="1" :min="1" />
          </el-form-item>

          <el-form-item label="kriging_neighbors">
            <el-input-number v-model="params.n_neighbors" :step="1" :min="3" />
          </el-form-item>

          <el-form-item label="search_radius（m）">
            <el-input-number v-model="params.search_radius" :step="50" :min="1" />
          </el-form-item>

          <el-form-item label="condsim_n_sim" v-if="params.use_condsim">
            <el-input-number v-model="params.n_sim" :step="10" :min="10" />
          </el-form-item>

          <el-form-item label="fault_thickness（m）" v-if="params.use_condsim">
            <el-input-number v-model="params.fault_thickness" :step="5" :min="0" />
          </el-form-item>

          <el-form-item label="max_points_ply">
            <el-input-number v-model="params.max_points_ply" :step="100000" :min="0" />
<!--            <div class="hint">0=不下采样（可能很大）；建议先用 300000~800000 预览</div>-->
          </el-form-item>
        </div>
      </el-form>
    </el-card>

    <div style="height: 12px;"></div>

    <el-card v-if="jobId">
      <template #header>
        <div style="display:flex; align-items:center; justify-content:space-between;">
          <span>运行状态与结果</span>
          <div style="display:flex; gap:8px; align-items:center;">
            <span class="hint">Job:</span>
            <el-tag>{{ jobId }}</el-tag>
          </div>
        </div>
      </template>

      <div v-if="job?.error" style="margin-bottom: 10px;">
        <el-alert type="error" show-icon :closable="false" :title="job.error" />
      </div>

      <div class="progress-block">
        <div class="progress-meta">
          <span class="label">重建进度</span>
          <span class="hint">{{ job?.progress ?? 0 }}%</span>
        </div>
        <el-progress :percentage="job?.progress ?? 0" :stroke-width="16" />
      </div>

      <div style="height:12px;"></div>

      <div>
        <div class="label">PLY 三维预览</div>
        <el-empty v-if="!plyOutputs.length" description="暂无 PLY" />
        <PlyViewer
          v-if="plyOutputs.length"
          :jobId="jobId"
          :file="plyOutputs[0]"
          :colorMapFile="colorMapFile || undefined"
          mode="points"
        />
      </div>

      <div style="height:12px;"></div>

      <div>
        <div class="label">输出文件</div>
        <el-empty v-if="!filteredOutputs.length" description="暂无可下载文件" />
        <el-table v-else :data="filteredOutputs.map((x)=>({name:x}))" size="small">
          <el-table-column prop="name" label="文件" />
          <el-table-column label="下载" width="120">
            <template #default="scope">
              <el-link type="primary" @click.prevent="downloadFile(scope.row.name)">下载</el-link>
            </template>
          </el-table-column>
        </el-table>
      </div>

    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { http } from "../api/http";
import { ElMessage } from "element-plus";
import PlyViewer from "./PlyViewer.vue";

const props = defineProps<{ modelId: string }>();
const storageKey = `reconstruct-b:${props.modelId}`;


type Job = {
  id: string;
  model_id: number;
  status: string;
  progress?: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  outputs: string[];
  log_tail: string;
};

const starting = ref(false);

const params = reactive({
  voxel_size: 15,
  n_virtual_per_tri: 5,
  min_dist_to_real: 80,
  min_dist_to_virtual: 40,
  n_neighbors: 12,
  search_radius: 1200,
  range_a: 944,
  nugget_c0: 0.227,
  sill_c: 0.021,
  use_condsim: false,
  n_sim: 100,
  fault_thickness: 30,
  max_points_ply: 600000, // 0=不下采样
});

const jobId = ref<string>("");
const job = ref<Job | null>(null);
let timer: any = null;

const modelFile = ref<File | null>(null);
const faultFile = ref<File | null>(null);
const savedModelFileName = ref("");
const savedFaultFileName = ref("");

const modelFileName = computed(() => modelFile.value?.name || savedModelFileName.value || "");
const faultFileName = computed(() => faultFile.value?.name || savedFaultFileName.value || "");

// PLY outputs
const plyOutputs = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.filter((x) => x.toLowerCase().endsWith(".ply"));
});

const colorMapFile = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.find((x) => x.toLowerCase().endsWith("lithology_color_map.json")) || "";
});

const filteredOutputs = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.filter((x) => !x.toLowerCase().endsWith(".png"));
});

const selectedPly = ref<string>("");

function persistState() {
  try {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        jobId: jobId.value,
        params: { ...params },
        modelFileName: modelFileName.value,
        faultFileName: faultFileName.value,
      })
    );
  } catch {}
}

function restoreState() {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved?.params) {
      Object.assign(params, saved.params);
    }
    savedModelFileName.value = String(saved?.modelFileName || "");
    savedFaultFileName.value = String(saved?.faultFileName || "");
    if (saved?.jobId) {
      jobId.value = String(saved.jobId);
    }
  } catch {}
}

watch(
  plyOutputs,
  (list) => {
    if (!list.length) {
      selectedPly.value = "";
      return;
    }
    if (!selectedPly.value || !list.includes(selectedPly.value)) {
      const best = list.find((x) => x.toLowerCase().includes("result_b.ply")) || list[0];
      selectedPly.value = best;
    }
  },
  { immediate: true }
);

function revokeUrl(url?: string) {
  if (!url) return;
  try {
    URL.revokeObjectURL(url);
  } catch {}
}

function onPickFile(which: "model" | "fault", f: any) {
  const file = f?.raw as File;
  if (!file) return;
  if (which === "model") {
    modelFile.value = file;
    savedModelFileName.value = file.name;
  }
  if (which === "fault") {
    faultFile.value = file;
    savedFaultFileName.value = file.name;
  }
  persistState();
}
function onRemoveFile(which: "model" | "fault") {
  if (which === "model") {
    modelFile.value = null;
    savedModelFileName.value = "";
  }
  if (which === "fault") {
    faultFile.value = null;
    savedFaultFileName.value = "";
  }
  persistState();
}

function statusType(status?: string) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "running") return "warning";
  return "info";
}

async function fetchFileBlob(name: string) {
  const res = await http.get(`/api/reconstruct/jobs/${jobId.value}/download`, {
    params: { file: name },
    responseType: "blob",
  });
  return res.data as Blob;
}

async function downloadFile(name: string) {
  try {
    if (!jobId.value || !name) return;
    const blob = await fetchFileBlob(name);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    revokeUrl(url);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "下载失败");
  }
}

async function start() {
  if (!modelFile.value) {
    ElMessage.error("请先选择：model.stl");
    return;
  }
  if (params.use_condsim && !faultFile.value) {
    ElMessage.error("已勾选条件模拟，请同时上传 fault.stl");
    return;
  }

  starting.value = true;
  try {
    const fd = new FormData();
    fd.append("model_stl", modelFile.value);
    if (params.use_condsim && faultFile.value) {
      fd.append("fault_stl", faultFile.value);
    }
    Object.entries(params).forEach(([k, v]) => {
      if (v === null || v === undefined) return;
      fd.append(k, String(v));
    });

    const res = await http.post(`/api/models/${props.modelId}/reconstruct/b/start`, fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0,
    });

    jobId.value = res.data.job_id;
    persistState();
    ElMessage.success("已启动算法B任务");
    await refreshJob();
    startPolling();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "启动失败");
  } finally {
    starting.value = false;
  }
}

async function refreshJob() {
  if (!jobId.value) return;
  try {
    const res = await http.get(`/api/reconstruct/jobs/${jobId.value}`);
    job.value = res.data;
    persistState();
    if (job.value?.status === "success" || job.value?.status === "failed") {
      stopPolling();
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "获取状态失败");
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  timer = setInterval(refreshJob, 2000);
}

function stopPolling() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

function clearJob() {
  stopPolling();
  sessionStorage.removeItem(storageKey);
  modelFile.value = null;
  faultFile.value = null;
  savedModelFileName.value = "";
  savedFaultFileName.value = "";
  selectedPly.value = "";
  jobId.value = "";
  job.value = null;
}

watch(jobId, () => persistState());
watch(
  params,
  () => {
    persistState();
  },
  { deep: true }
);

onMounted(async () => {
  restoreState();
  if (!jobId.value) return;
  await refreshJob();
  const status = job.value?.status;
  if (status === "queued" || status === "running") {
    startPolling();
  }
});

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 14px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 18px;
}
.label {
  font-weight: 700;
  margin-bottom: 6px;
}
.hint {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 4px;
}
.progress-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
@media (max-width: 1200px) {
  .grid {
    grid-template-columns: 1fr;
  }
  .grid2 {
    grid-template-columns: 1fr;
  }
}
</style>
