<template>
  <div class="compare-page">
    <header class="compare-head">
      <div>
        <span>整个地质体 · 化学元素体素插值</span>
        <h2>背景值方法三维建模对比</h2>
        <p>两套结果使用相同浓度插值；对比图默认按各自异常下限裁切统计异常体，因此空间范围会直接体现两种背景算法的差异。</p>
      </div>
      <div class="run-panel">
        <el-select v-model="element" filterable placeholder="选择元素" :disabled="running">
          <el-option v-for="item in elements" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button type="primary" size="large" :loading="starting" :disabled="!element || running" @click="startComparison">
          {{ running ? "正在插值建模" : "开始双方法建模" }}
        </el-button>
      </div>
    </header>

    <section v-if="baselineJob || expertJob" class="status-strip">
      <div><b>当前元素</b><strong>{{ element }}</strong></div>
      <div><b>现有方法</b><strong>{{ statusText(baselineJob) }}</strong><el-progress :percentage="baselineJob?.progress || 0" /></div>
      <div><b>专家公式</b><strong>{{ statusText(expertJob) }}</strong><el-progress :percentage="expertJob?.progress || 0" /></div>
    </section>

    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />

    <section v-if="baselineProfile || expertProfile" class="metrics">
      <div class="method-label"><span>对比指标</span><b>{{ element }}</b></div>
      <div><span>现有 log-MAD 背景值</span><b>{{ format(baselineProfile?.background_mean) }} ppm</b></div>
      <div><span>专家公式背景值 X</span><b>{{ format(expertProfile?.background_mean) }} ppm</b></div>
      <div><span>现有异常下限</span><b>{{ format(baselineProfile?.threshold_T_auto) }} ppm</b></div>
      <div><span>专家异常下限 X + 2σ</span><b>{{ format(expertProfile?.threshold_T_auto) }} ppm</b></div>
      <div><span>专家剔除高值</span><b>{{ expertProfile?.removed_high_count ?? "-" }} 个</b></div>
    </section>

    <section v-if="analysis" class="difference-panel">
      <div class="difference-head">
        <div><span>逐体素异常掩膜对比</span><b>{{ analysis.element }} · {{ format(analysis.voxel_size_m) }} m 体素</b></div>
        <div><span>异常体重叠率（Jaccard）</span><strong>{{ percent(analysis.jaccard) }}</strong></div>
      </div>
      <div class="difference-grid">
        <div><span>两种方法共同异常</span><b>{{ count(analysis.counts?.both) }} 个体素</b><small>{{ volume(analysis.volumes_m3?.both) }}</small></div>
        <div class="baseline-only"><span>仅 log-MAD 异常</span><b>{{ count(analysis.counts?.baseline_only) }} 个体素</b><small>{{ volume(analysis.volumes_m3?.baseline_only) }}</small></div>
        <div class="expert-only"><span>仅专家公式异常</span><b>{{ count(analysis.counts?.expert_only) }} 个体素</b><small>{{ volume(analysis.volumes_m3?.expert_only) }}</small></div>
        <div><span>有效体素</span><b>{{ count(analysis.counts?.valid) }} 个</b><small>共同异常 + 两侧差异 + 非异常</small></div>
      </div>
      <p class="difference-note">差异来自异常下限：log-MAD {{ format(analysis.thresholds?.baseline) }} ppm；专家公式 {{ format(analysis.thresholds?.expert) }} ppm。背景值定义通常水平并参与异常阈值计算；最低工业品位是两边共用的独立工程门槛，不参与制造两种统计方法的差异。</p>
      <p class="difference-note">完整场差值：平均绝对差 {{ format(analysis.field_difference?.mean_abs_ppm) }} ppm；RMSE {{ format(analysis.field_difference?.rmse_ppm) }} ppm；最大绝对差 {{ format(analysis.field_difference?.max_abs_ppm) }} ppm。正差值表示专家公式模型更高。</p>
      <p class="difference-note">插值输入：专家公式本次实际剔除 {{ expertJob?.summary?.interpolation_input?.removed_sample_count ?? 0 }} 个高值样本后重建；log-MAD 保留全部样本。</p>
    </section>

    <section v-if="baselineScene || expertScene" class="scene-grid">
      <article>
        <div class="scene-title"><span>现有方法</span><h3>log-MAD 完整地质体浓度场</h3></div>
        <GeochemSceneViewer v-if="baselineScene" :manifest="baselineScene" :job-id="baselineJob.id" />
        <div v-else class="scene-wait">等待现有方法建模完成</div>
      </article>
      <article>
        <div class="scene-title expert"><span>专家方法</span><h3>3σ 剔除 + X + 2σ 完整地质体浓度场</h3></div>
        <GeochemSceneViewer v-if="expertScene" :manifest="expertScene" :job-id="expertJob.id" />
        <div v-else class="scene-wait">等待专家公式建模完成</div>
      </article>
    </section>

    <section v-else class="empty-state">
      <b>选择一种化学元素，直接开始两套体素插值。</b>
      <p>系统自动采用最新算法 B 地质体和固定专业参数，不需要先重跑变化规律，也不要求手动设置邻居数、搜索椭球或体素尺寸。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import GeochemSceneViewer from "./components/geochem-workflow/GeochemSceneViewer.vue";

const props = defineProps<{ modelId: string }>();
const elements = ref<string[]>([]);
const element = ref("");
const starting = ref(false);
const baselineJob = ref<any>(null);
const expertJob = ref<any>(null);
const baselineScene = ref<any>(null);
const expertScene = ref<any>(null);
const analysis = ref<any>(null);
const error = ref("");
let timer: number | undefined;

const running = computed(() => [baselineJob.value, expertJob.value].some((job) => job && !["success", "failed"].includes(job.status)));
const baselineProfile = computed(() => baselineJob.value?.summary?.threshold_profiles?.[element.value]);
const expertProfile = computed(() => expertJob.value?.summary?.threshold_profiles?.[element.value]);

function format(value: any) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : "-";
}
function statusText(job: any) {
  return ({ queued: "排队中", running: "计算中", success: "已完成", failed: "失败" } as any)[job?.status] || "未启动";
}

async function loadElements() {
  const response = await http.get(`/api/models/${props.modelId}/geochem/elements`);
  elements.value = response.data.elements || [];
  if (!element.value) element.value = elements.value.includes("Cu") ? "Cu" : elements.value[0] || "";
}

async function startComparison() {
  starting.value = true;
  error.value = "";
  baselineScene.value = null;
  expertScene.value = null;
  analysis.value = null;
  try {
    const response = await http.post(`/api/models/${props.modelId}/geochem/comparison/start`, { element: element.value });
    baselineJob.value = { id: response.data.baseline_job_id, status: "queued", progress: 0 };
    expertJob.value = { id: response.data.expert_job_id, status: "queued", progress: 0 };
    ElMessage.success("两套全地质体化学元素插值任务已启动");
    beginPolling();
  } catch (reason: any) {
    error.value = reason?.response?.data?.detail || "启动对比建模失败";
  } finally {
    starting.value = false;
  }
}

async function refreshJobs() {
  if (!baselineJob.value || !expertJob.value) return;
  const [baseline, expert] = await Promise.all([
    http.get(`/api/geochem/jobs/${baselineJob.value.id}`),
    http.get(`/api/geochem/jobs/${expertJob.value.id}`),
  ]);
  baselineJob.value = baseline.data;
  expertJob.value = expert.data;
  if (baseline.data.status === "success" && !baselineScene.value) {
    baselineScene.value = (await http.get(`/api/geochem/jobs/${baseline.data.id}/scene`)).data;
  }
  if (expert.data.status === "success" && !expertScene.value) {
    expertScene.value = (await http.get(`/api/geochem/jobs/${expert.data.id}/scene`)).data;
  }
  if (baseline.data.status === "success" && expert.data.status === "success" && !analysis.value) {
    try {
      analysis.value = (await http.get(`/api/models/${props.modelId}/geochem/comparison/analysis`, {
        params: { baseline_job_id: baseline.data.id, expert_job_id: expert.data.id },
      })).data;
      if (baselineScene.value && analysis.value.field_difference?.layer) {
        baselineScene.value = {
          ...baselineScene.value,
          layers: {
            ...(baselineScene.value.layers || {}),
            field_difference_layer: {
              ...analysis.value.field_difference.layer,
              file: analysis.value.field_difference.layer.file,
              label: "专家模型 − 原模型差值场",
            },
          },
        };
      }
    } catch (reason: any) {
      error.value = reason?.response?.data?.detail || "异常体差异统计失败";
    }
  }
  const failed = [baseline.data, expert.data].find((job) => job.status === "failed");
  if (failed) error.value = failed.error || "三维插值任务失败，请查看任务日志";
  if ([baseline.data, expert.data].every((job) => ["success", "failed"].includes(job.status))) stopPolling();
}
function beginPolling() {
  stopPolling();
  refreshJobs();
  timer = window.setInterval(refreshJobs, 2500);
}
function stopPolling() {
  if (timer) window.clearInterval(timer);
  timer = undefined;
}
function count(value: any) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "-";
}
function volume(value: any) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })} m³` : "-";
}
function percent(value: any) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "-";
}

onMounted(loadElements);
onBeforeUnmount(stopPolling);
</script>

<style scoped>
.compare-page{display:grid;gap:18px}.compare-head{display:flex;align-items:end;justify-content:space-between;gap:30px;padding:24px 28px;border-radius:16px;background:#10283f;color:#fff}.compare-head span{color:#66d7b5;font-size:12px;font-weight:800}.compare-head h2{margin:7px 0 8px;font-size:27px}.compare-head p{max-width:820px;margin:0;color:#bed0df;line-height:1.65}.run-panel{display:flex;gap:10px;min-width:390px}.run-panel .el-select{flex:1}.status-strip,.metrics{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid #dce6ef;border-radius:14px;background:#fff;overflow:hidden}.status-strip>div,.metrics>div{padding:16px 18px;border-right:1px solid #e7edf3}.status-strip b,.status-strip strong,.metrics span,.metrics b{display:block}.status-strip b,.metrics span{color:#718398;font-size:12px}.status-strip strong,.metrics b{margin-top:5px;color:#143e68}.metrics{grid-template-columns:repeat(6,1fr)}.method-label{background:#edf5fa}.scene-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.scene-grid article{min-width:0}.scene-title{padding:14px 18px;background:#eaf3fa;border-radius:14px 14px 0 0}.scene-title.expert{background:#e8f8f2}.scene-title span{font-size:11px;color:#50718f}.scene-title h3{margin:4px 0 0;color:#163f65}.scene-grid :deep(.scene-shell){border-radius:0 0 14px 14px}.scene-grid :deep(.viewer){height:520px}.scene-grid :deep(.scene-toolbar){gap:9px}.scene-wait,.empty-state{display:grid;min-height:360px;place-content:center;text-align:center;border:1px dashed #b9c9d8;border-radius:0 0 14px 14px;background:#f5f8fb;color:#667c91}.empty-state{min-height:380px;border-radius:16px}.empty-state b{font-size:20px;color:#274d70}.empty-state p{max-width:720px;line-height:1.7}@media(max-width:1300px){.scene-grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(3,1fr)}.compare-head{align-items:stretch;flex-direction:column}.run-panel{min-width:0}}
.difference-panel{border:1px solid #dce6ef;border-radius:14px;background:#fff;overflow:hidden}.difference-head{display:flex;justify-content:space-between;gap:20px;padding:16px 18px;background:#f2f7fb}.difference-head span,.difference-grid span{display:block;color:#718398;font-size:12px}.difference-head b,.difference-head strong{display:block;margin-top:5px;color:#143e68;font-size:18px}.difference-grid{display:grid;grid-template-columns:repeat(4,1fr)}.difference-grid>div{padding:16px 18px;border-right:1px solid #e7edf3}.difference-grid b,.difference-grid small{display:block;margin-top:6px;color:#143e68}.difference-grid small{color:#718398}.baseline-only{background:#f3f7ff}.expert-only{background:#fff6ed}.difference-note{margin:0;padding:12px 18px;color:#64748b;font-size:12px;line-height:1.6;background:#fbfcfd}@media(max-width:900px){.difference-grid{grid-template-columns:repeat(2,1fr)}.difference-head{flex-direction:column}}
</style>
