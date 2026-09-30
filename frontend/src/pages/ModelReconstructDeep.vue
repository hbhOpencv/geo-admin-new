<template>
  <div>
    <div class="page-head">
      <div>
        <h2>算法 C / 深度学习（模型 {{ modelId }}）</h2>
        <p>Kriging 引导 MLP：以算法 B 的概率场为特征，用真实钻孔分层和高置信体素伪标签训练岩性预测模型。</p>
      </div>
      <div class="head-actions">
        <el-button @click="loadLatest">加载最新结果</el-button>
        <el-button @click="refreshJob" :disabled="!jobId">刷新状态</el-button>
        <el-button type="primary" @click="start" :loading="starting">开始训练预测</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      style="margin: 12px 0;"
      title="请明确选择一次已完成的算法 B。算法 C 将严格读取该任务的概率场、最终体素和岩性颜色表；相同岩性在 B、C 中保持同色。"
    />

    <el-card>
      <template #header>
        <span>训练与预测参数</span>
      </template>
      <el-form :model="params" label-width="180px">
        <div class="grid2">
          <el-form-item label="来源算法 B" class="source-select">
            <el-select v-model="params.source_b_job_id" placeholder="请选择要对比的算法 B 任务" style="width: 100%;">
              <el-option v-for="item in sourceJobs" :key="item.id" :label="sourceLabel(item)" :value="item.id" />
            </el-select>
            <div class="hint">算法 C 将锁定此任务，不会自动改用其他算法 B 结果。</div>
          </el-form-item>
          <el-form-item label="epochs">
            <el-input-number v-model="params.epochs" :min="5" :max="300" :step="5" />
          </el-form-item>
          <el-form-item label="learning_rate">
            <el-input-number v-model="params.lr" :min="0.0001" :max="0.02" :step="0.0005" :precision="4" />
          </el-form-item>
          <el-form-item label="hidden1">
            <el-input-number v-model="params.hidden1" :min="16" :max="256" :step="16" />
          </el-form-item>
          <el-form-item label="hidden2">
            <el-input-number v-model="params.hidden2" :min="8" :max="256" :step="8" />
          </el-form-item>
          <el-form-item label="伪标签置信阈值">
            <el-input-number v-model="params.pseudo_threshold" :min="0.5" :max="0.98" :step="0.02" :precision="2" />
          </el-form-item>
          <el-form-item label="最大伪标签样本">
            <el-input-number v-model="params.max_pseudo_samples" :min="1000" :max="300000" :step="5000" />
          </el-form-item>
          <el-form-item label="真实样本权重">
            <el-input-number v-model="params.real_weight" :min="1" :max="20" :step="1" />
          </el-form-item>
          <el-form-item label="伪标签权重">
            <el-input-number v-model="params.pseudo_weight" :min="0.1" :max="5" :step="0.1" :precision="1" />
          </el-form-item>
          <el-form-item label="预览 PLY 点数">
            <el-input-number v-model="params.max_points_ply" :min="50000" :max="1200000" :step="50000" />
          </el-form-item>
        </div>
      </el-form>
    </el-card>

    <div style="height: 12px;"></div>

    <el-card v-if="jobId">
      <template #header>
        <div class="card-head">
          <span>运行状态</span>
          <div class="job-chip">
            <span>Job:</span>
            <el-tag>{{ jobId }}</el-tag>
            <el-tag :type="statusType(job?.status)">{{ job?.status || "unknown" }}</el-tag>
          </div>
        </div>
      </template>

      <el-alert v-if="job?.error" type="error" show-icon :closable="false" :title="job.error" style="margin-bottom:12px;" />

      <div v-if="sourceBJobId" class="source-line">
        <span>来源算法 B Job</span>
        <el-tag type="success">{{ sourceBJobId }}</el-tag>
        <el-tag type="info">岩性颜色继承自该任务</el-tag>
      </div>

      <div class="progress-meta">
        <span class="label">训练预测进度</span>
        <span class="hint">{{ job?.progress ?? 0 }}%</span>
      </div>
      <el-progress :percentage="job?.progress ?? 0" :stroke-width="16" />
    </el-card>

    <div style="height: 12px;"></div>

    <div v-if="job?.status === 'running' || job?.status === 'queued'" class="result-stack">
      <el-card>
        <el-empty description="算法 C 正在训练预测，完成后会显示指标、训练曲线和三维点云。" />
      </el-card>
    </div>

    <div v-if="hasResultSummary" class="result-stack">
      <el-card>
        <template #header>
          <span>1. 深度学习模型摘要</span>
        </template>
        <div class="metric-grid">
          <div class="metric">
            <div class="metric-value">{{ summary.real_samples || 0 }}</div>
            <div class="metric-label">真实钻孔样本</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ summary.pseudo_samples || 0 }}</div>
            <div class="metric-label">高置信伪标签</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ summary.n_classes || 0 }}</div>
            <div class="metric-label">岩性类别数</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ summary.feature_dim || 0 }}</div>
            <div class="metric-label">输入特征维度</div>
          </div>
        </div>

        <div class="metric-grid">
          <div class="metric">
            <div class="metric-value">{{ pct(summary.real_apparent_accuracy) }}</div>
            <div class="metric-label">钻孔点表观准确率</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ pct(summary.final_val_acc) }}</div>
            <div class="metric-label">训练内验证准确率</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ num(summary.final_val_loss) }}</div>
            <div class="metric-label">验证 loss</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ pct(summary.mean_prediction_confidence) }}</div>
            <div class="metric-label">平均预测置信度</div>
          </div>
        </div>
        <div v-if="summary.source_b_agreement_rate != null" class="metric-grid">
          <div class="metric">
            <div class="metric-value">{{ pct(summary.source_b_agreement_rate) }}</div>
            <div class="metric-label">与来源算法 B 体素一致率</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ count(summary.source_b_changed_voxels) }}</div>
            <div class="metric-label">算法 C 改变体素数</div>
          </div>
          <div class="metric">
            <div class="metric-value">{{ count(summary.source_b_compared_voxels) }}</div>
            <div class="metric-label">参与比较体素数</div>
          </div>
          <div class="metric">
            <div class="metric-value metric-text">同岩性同色</div>
            <div class="metric-label">颜色表继承自来源算法 B</div>
          </div>
        </div>
      </el-card>

      <el-card>
        <template #header>
          <span>2. 训练曲线</span>
        </template>
        <div class="image-row">
          <img v-if="imageUrls['deep_loss_curve.png']" :src="imageUrls['deep_loss_curve.png']" alt="deep learning training curve" />
          <el-empty v-else description="暂无训练曲线" />
        </div>
      </el-card>

      <el-card>
        <template #header>
          <div class="card-head">
            <span>3. 深度学习三维预测结果</span>
            <el-select v-if="plyOutputs.length > 1" v-model="selectedPly" size="small" style="width: 260px;">
              <el-option v-for="name in plyOutputs" :key="name" :label="name" :value="name" />
            </el-select>
          </div>
        </template>
        <el-empty v-if="!plyOutputs.length" description="暂无 PLY" />
        <PlyViewer
          v-else
          :jobId="jobId"
          :file="selectedPly || plyOutputs[0]"
          :colorMapFile="colorMapFile || undefined"
          :downloadPrefix="`/api/deep/jobs/${jobId}/download`"
          mode="points"
          :pointSize="2"
        />
      </el-card>

      <el-card>
        <template #header>
          <span>输出文件</span>
        </template>
        <el-empty v-if="!outputs.length" description="暂无输出文件" />
        <el-table v-else :data="outputs.map((name) => ({ name }))" size="small">
          <el-table-column prop="name" label="文件" />
          <el-table-column label="下载" width="120">
            <template #default="scope">
              <el-button link type="primary" @click="downloadFile(scope.row.name)">下载</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import PlyViewer from "./PlyViewer.vue";

const props = defineProps<{ modelId: string }>();
const storageKey = `reconstruct-deep:${props.modelId}`;

type DeepJob = {
  id: string;
  model_id: number;
  status: string;
  progress?: number;
  error?: string | null;
  params?: Record<string, any>;
  summary?: Record<string, any>;
  outputs: string[];
  log_tail?: string;
};

type SourceBJob = {
  id: string;
  created_at?: string;
  finished_at?: string;
  voxel_size?: number | null;
  fault_thickness?: number | null;
};

const params = reactive({
  source_b_job_id: "",
  epochs: 80,
  hidden1: 96,
  hidden2: 64,
  lr: 0.001,
  pseudo_threshold: 0.8,
  max_pseudo_samples: 50000,
  real_weight: 5,
  pseudo_weight: 1,
  max_points_ply: 600000,
});

const starting = ref(false);
const jobId = ref("");
const job = ref<DeepJob | null>(null);
const selectedPly = ref("");
const imageUrls = reactive<Record<string, string>>({});
const sourceJobs = ref<SourceBJob[]>([]);
let timer: any = null;

const outputs = computed(() => job.value?.outputs || []);
const summary = computed(() => job.value?.summary || null);
const hasResultSummary = computed(() => job.value?.status === "success" && !!summary.value && Object.keys(summary.value).length > 0);
const sourceBJobId = computed(() => String(summary.value?.source_b_job_id || job.value?.params?.source_b_job_id || ""));
const plyOutputs = computed(() => outputs.value.filter((name) => name.toLowerCase().endsWith(".ply")));
const colorMapFile = computed(() => outputs.value.find((name) => name.toLowerCase().endsWith("lithology_color_map.json")) || "");

watch(
  plyOutputs,
  (list) => {
    if (!list.length) {
      selectedPly.value = "";
      return;
    }
    if (!selectedPly.value || !list.includes(selectedPly.value)) {
      selectedPly.value = list.find((name) => name.toLowerCase().includes("deep_result")) || list[0];
    }
  },
  { immediate: true }
);

function persist() {
  sessionStorage.setItem(storageKey, JSON.stringify({ jobId: jobId.value, params: { ...params } }));
}

function restore() {
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return;
    const saved = JSON.parse(raw);
    Object.assign(params, saved?.params || {});
    jobId.value = saved?.jobId || "";
  } catch {}
}

function statusType(status?: string) {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "running") return "warning";
  return "info";
}

function pct(v: any) {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "-";
}

function num(v: any) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(4) : "-";
}

function count(v: any) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n).toLocaleString() : "-";
}

function sourceLabel(item: SourceBJob) {
  const shortId = item.id.slice(0, 8);
  const time = item.finished_at ? new Date(item.finished_at).toLocaleString() : "";
  const voxel = item.voxel_size ? `｜${item.voxel_size}m` : "";
  return `${shortId}${voxel}${time ? `｜完成于 ${time}` : ""}`;
}

async function loadSources() {
  const res = await http.get(`/api/models/${props.modelId}/reconstruct/deep/sources`);
  sourceJobs.value = res.data?.jobs || [];
  if (!sourceJobs.value.some((item) => item.id === params.source_b_job_id)) {
    params.source_b_job_id = sourceJobs.value[0]?.id || "";
  }
}

function clearImageUrls() {
  for (const [name, url] of Object.entries(imageUrls)) {
    URL.revokeObjectURL(url);
    delete imageUrls[name];
  }
}

async function loadImage(name: string) {
  if (!jobId.value || !outputs.value.includes(name) || imageUrls[name]) return;
  const res = await http.get(`/api/deep/jobs/${jobId.value}/download`, {
    params: { file: name },
    responseType: "blob",
  });
  imageUrls[name] = URL.createObjectURL(res.data);
}

async function loadResultImages() {
  await loadImage("deep_loss_curve.png").catch(() => undefined);
}

async function start() {
  if (!params.source_b_job_id) {
    ElMessage.error("请先选择一个已完成的算法 B 任务");
    return;
  }
  starting.value = true;
  try {
    const res = await http.post(`/api/models/${props.modelId}/reconstruct/deep/start`, null, {
      params,
      timeout: 0,
    });
    clearImageUrls();
    jobId.value = res.data.job_id;
    persist();
    ElMessage.success("已启动算法 C 深度学习任务");
    await refreshJob();
    startPolling();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "启动失败");
  } finally {
    starting.value = false;
  }
}

async function loadLatest() {
  try {
    const res = await http.get(`/api/models/${props.modelId}/reconstruct/deep/latest`);
    const latest = res.data?.job;
    if (!latest?.id) {
      ElMessage.info("暂无算法 C 结果");
      return;
    }
    clearImageUrls();
    jobId.value = latest.id;
    job.value = latest;
    persist();
    if (job.value?.status === "success") await loadResultImages();
    ElMessage.success("已加载最新算法 C 结果");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "加载失败");
  }
}

async function refreshJob() {
  if (!jobId.value) return;
  try {
    const res = await http.get(`/api/deep/jobs/${jobId.value}`);
    job.value = res.data;
    persist();
    if (job.value?.status === "success") await loadResultImages();
    if (job.value?.status === "success" || job.value?.status === "failed") stopPolling();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "获取状态失败");
    stopPolling();
  }
}

function startPolling() {
  stopPolling();
  timer = setInterval(refreshJob, 2500);
}

function stopPolling() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

async function downloadFile(name: string) {
  if (!jobId.value) return;
  try {
    const res = await http.get(`/api/deep/jobs/${jobId.value}/download`, {
      params: { file: name },
      responseType: "blob",
    });
    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "下载失败");
  }
}

onMounted(async () => {
  restore();
  await loadSources().catch((e: any) => {
    ElMessage.error(e?.response?.data?.detail || e?.message || "加载算法 B 来源失败");
  });
  if (!jobId.value) return;
  await refreshJob();
  if (job.value?.status === "queued" || job.value?.status === "running") startPolling();
});

onBeforeUnmount(stopPolling);
onBeforeUnmount(clearImageUrls);
</script>

<style scoped>
.page-head,
.card-head,
.job-chip,
.progress-meta,
.source-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.page-head h2 {
  margin: 0;
}

.page-head p,
.hint {
  margin: 6px 0 0;
  color: #71829d;
  font-size: 13px;
}

.head-actions,
.job-chip,
.source-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-line {
  justify-content: flex-start;
  margin-bottom: 12px;
  color: #52657f;
  font-size: 13px;
}

.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 18px;
}

.source-select {
  grid-column: 1 / -1;
}

.result-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.metric {
  border: 1px solid #e7edf6;
  border-radius: 8px;
  padding: 12px;
  background: #f8fbff;
}

.metric-value {
  color: #1d4ed8;
  font-weight: 800;
  font-size: 22px;
}

.metric-text {
  font-size: 17px;
}

.metric-label,
.label {
  color: #263a57;
  font-weight: 700;
}

.image-row img {
  max-width: 760px;
  width: 100%;
  border: 1px solid #e7edf6;
  border-radius: 8px;
  background: #fff;
}

@media (max-width: 1200px) {
  .grid2,
  .metric-grid {
    grid-template-columns: 1fr;
  }
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
