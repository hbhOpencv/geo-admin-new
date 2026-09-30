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
      title="????? STL?model.stl"
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
          <div class="label">断层 STL</div>
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="(f)=>onPickFile('fault', f)"
            :on-remove="()=>onRemoveFile('fault')"
          >
            <el-button>选择 STL</el-button>
          </el-upload>
          <div class="hint" v-if="faultFileName">已选：{{ faultFileName }}</div>
        </div>

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

      <el-form :model="params" label-width="140px">
        <div class="grid2">
          <el-form-item label="domain_simplify">
            <el-input-number v-model="params.domain_simplify" :step="1" />
          </el-form-item>

          <el-form-item label="domain_buffer">
            <el-input-number v-model="params.domain_buffer" :step="10" />
          </el-form-item>

          <el-form-item label="fault_simplify">
            <el-input-number v-model="params.fault_simplify" :step="0.5" />
          </el-form-item>

          <el-form-item label="cdt_q">
            <el-input-number v-model="params.cdt_q" :step="0.5" />
          </el-form-item>

          <el-form-item label="clip">
            <el-select v-model="params.clip" style="width: 180px;">
              <el-option label="sample" value="sample" />
              <el-option label="centroid" value="centroid" />
              <el-option label="none" value="none" />
            </el-select>
          </el-form-item>

          <el-form-item label="clip_threshold">
            <el-input-number v-model="params.clip_threshold" :step="0.05" :min="0" :max="1" />
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
          mode="mesh"
        />
      </div>

      <div style="height:12px;"></div>

      <div>
        <div class="label">输出文件</div>
        <el-empty v-if="!downloadOutputs.length" description="暂无可下载文件" />
        <el-table v-else :data="downloadOutputs.map((x)=>({name:x}))" size="small">
          <el-table-column prop="name" label="文件" />
          <el-table-column label="下载" width="120">
            <template #default="scope">
              <el-button link type="primary" @click="downloadFile(scope.row.name)">下载</el-button>
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
const storageKey = `reconstruct-a:${props.modelId}`;

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
  snap_tol: 0.1,
  model_face_step: 5,
  domain_simplify: 70,
  domain_buffer: 100,
  fault_step: 2,
  fault_simplify: 2.0,
  cdt_q: 5.0,
  triangle_extra: "",
  tri_max_show: 1500,
  clip: "sample",
  clip_threshold: 0.7,
  z_min: null as number | null,
});

const jobId = ref<string>("");
const job = ref<Job | null>(null);

let timer: any = null;

const faultFile = ref<File | null>(null);
const modelFile = ref<File | null>(null);
const savedFaultFileName = ref("");
const savedModelFileName = ref("");

const faultFileName = computed(() => faultFile.value?.name || savedFaultFileName.value || "");
const modelFileName = computed(() => modelFile.value?.name || savedModelFileName.value || "");

const plyOutputs = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.filter((x) => x.toLowerCase().endsWith(".ply"));
});

const colorMapFile = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.find((x) => x.toLowerCase().endsWith("lithology_color_map.json")) || "";
});

const downloadOutputs = computed(() => {
  const outs = job.value?.outputs || [];
  return outs.filter((x) => !x.toLowerCase().endsWith(".png"));
});


function onPickFile(which: "fault" | "model", f: any) {
  const file = f?.raw as File;
  if (!file) return;
  if (which === "fault") {
    faultFile.value = file;
    savedFaultFileName.value = file.name;
  }
  if (which === "model") {
    modelFile.value = file;
    savedModelFileName.value = file.name;
  }
  persistState();
}

function onRemoveFile(which: "fault" | "model") {
  if (which === "fault") {
    faultFile.value = null;
    savedFaultFileName.value = "";
  }
  if (which === "model") {
    modelFile.value = null;
    savedModelFileName.value = "";
  }
  persistState();
}

function persistState() {
  try {
    sessionStorage.setItem(
      storageKey,
      JSON.stringify({
        jobId: jobId.value,
        params: { ...params },
        faultFileName: faultFileName.value,
        modelFileName: modelFileName.value,
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
    savedFaultFileName.value = String(saved?.faultFileName || "");
    savedModelFileName.value = String(saved?.modelFileName || "");
    if (saved?.jobId) {
      jobId.value = String(saved.jobId);
    }
  } catch {}
}

function statusType(status?: string) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "running") return "warning";
  return "info";
}
// 下载任意文件（png/ply/stl 都行）
async function downloadFile(file: string) {
  if (!jobId.value) return;
  try {
    const res = await http.get(`/api/reconstruct/jobs/${jobId.value}/download`, {
      params: { file },
      responseType: "blob",
    });

    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = file; // 用原文件名
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "下载失败");
  }
}


async function start() {
  if (!faultFile.value || !modelFile.value) {
    ElMessage.error("请先选择断层 STL 和模型 STL。");
    return;
  }
  starting.value = true;
  try {
    const fd = new FormData();
    fd.append("fault_stl", faultFile.value);
    fd.append("model_stl", modelFile.value);

    // params
    Object.entries(params).forEach(([k, v]) => {
      if (v === null || v === undefined) return;
      fd.append(k, String(v));
    });

    const res = await http.post(`/api/models/${props.modelId}/reconstruct/start`, fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 0, // allow long start (actual run is async)
    });

    jobId.value = res.data.job_id;
    persistState();
    ElMessage.success("已启动重建任务");
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
  faultFile.value = null;
  modelFile.value = null;
  savedFaultFileName.value = "";
  savedModelFileName.value = "";
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
