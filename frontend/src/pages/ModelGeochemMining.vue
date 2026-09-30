<template>
  <div class="workspace">
    <GeochemWorkflowNav
      :workflow="workflow"
      :current="algorithmType"
      :selected-combination="currentCombination"
    />

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <section v-if="!workflow" class="empty-state">
      <div class="empty-icon">⌁</div>
      <h2>建立本次找矿参考工作流</h2>
      <p>系统会冻结当前化验数据、元素规则和算法版本。进入页面不会自动计算，点击按钮后才启动真实后端任务。</p>
      <el-button type="primary" size="large" :loading="creating" @click="createWorkflow">
        建立数据与规则快照
      </el-button>
    </section>

    <template v-else>
      <section class="scope-bar">
        <div><b>{{ workflow.data_scope.project_drillhole_count }}</b><span>项目钻孔（空间场景）</span></div>
        <div><b>{{ workflow.data_scope.chemical_drillhole_count }}</b><span>化验钻孔（算法计算）</span></div>
        <div><b>{{ workflow.data_scope.element_count }}</b><span>全部参与计算元素</span></div>
        <div><b>{{ workflow.data_scope.assay_count }}</b><span>真实化验区间</span></div>
        <div><b>{{ shortHash }}</b><span>冻结数据快照</span></div>
      </section>

      <section v-if="algorithmType === 'variation' && canRunVariation" class="run-gate">
        <span class="gate-icon">1</span>
        <div>
          <h2>运行化学元素变化规律分析</h2>
          <p>后端将一次性计算全部 {{ workflow.data_scope.element_count }} 种元素、全部 {{ workflow.data_scope.chemical_drillhole_count }} 个化验钻孔和完整深度范围。</p>
        </div>
        <el-button type="primary" size="large" :loading="runningAction" @click="runVariation">开始分析</el-button>
      </section>

      <section v-else-if="algorithmType === 'variation' && workflow.stage === 'variation_running'" class="running-box">
        <h2>后端正在计算全部元素的变化规律</h2>
        <el-progress :percentage="Math.max(5, workflow.progress)" :stroke-width="13" />
        <p>正在计算背景值、统计正异常、专业品位证据以及钻孔深度连续异常段。</p>
      </section>

      <VariationInsights
        v-else-if="algorithmType === 'variation' && variationReady"
        :workflow="workflow"
        @open-evidence="openEvidence"
        @analyze-correlation="analyzeCorrelation"
      />

      <section
        v-else-if="algorithmType === 'correlation' && !workflow.selected_clue_id && !route.query.clue"
        class="run-gate warning"
      >
        <span class="gate-icon">2</span>
        <div>
          <h2>先从变化规律选择一条主线索</h2>
          <p>相关性会搜索全部合格元素，但必须知道当前要围绕哪个异常元素和孔段组织证据。</p>
        </div>
        <el-button type="primary" size="large" @click="goVariation">返回变化规律</el-button>
      </section>

      <section v-else-if="algorithmType === 'correlation' && workflow.stage === 'correlation_running'" class="running-box">
        <h2>后端正在计算全元素相关性</h2>
        <el-progress :percentage="Math.max(5, workflow.progress)" :stroke-width="13" />
        <p>正在计算全部合格元素对、R 型聚类、Fisher/BH、同孔共同异常与跨孔重复。</p>
      </section>

      <CorrelationInsights
        v-else-if="algorithmType === 'correlation' && correlationReady"
        :workflow="workflow"
        @open-evidence="openEvidence"
        @start-3d="start3d"
      />

      <section v-else class="run-gate warning">
        <span class="gate-icon">!</span>
        <div>
          <h2>当前步骤尚未形成可展示结果</h2>
          <p>请返回上一步选择主线索并启动后端任务。系统不会用演示数据填充结果。</p>
        </div>
        <el-button type="primary" @click="goVariation">返回变化规律</el-button>
      </section>
    </template>

    <el-drawer v-model="evidenceVisible" title="可追溯证据" size="600px">
      <template v-if="selectedClue">
        <div class="drawer-summary">
          <span class="element">{{ selectedClue.main_element }}</span>
          <div>
            <b>{{ selectedClue.members.join(" + ") }}</b>
            <p>
              {{ selectedClue.kind === "variation"
                ? `${firstHole(selectedClue)} · ${depthRange(selectedClue)}`
                : spatialLabel(selectedClue) }}
            </p>
          </div>
        </div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="来源任务">{{ selectedClue.source_job_id?.slice(0, 12) }}</el-descriptions-item>
          <el-descriptions-item label="证据状态">{{ selectedClue.status }}</el-descriptions-item>
          <el-descriptions-item label="同孔事件">{{ selectedClue.same_hole_count }}</el-descriptions-item>
          <el-descriptions-item label="跨孔重复">{{ selectedClue.cross_hole_count }}</el-descriptions-item>
        </el-descriptions>
        <section v-if="profilePoints.length" class="profile-section">
          <h4>自动定位的浓度—深度证据</h4>
          <DrillholeProfileChart :points="profilePoints" :element="selectedClue.main_element" />
          <p>该曲线解释异常发生在哪个深度以及是否连续，不单独构成找矿结论。</p>
        </section>
        <section v-if="selectedClue.kind === 'variation'" class="background-evidence">
          <h4>背景值与倍数说明</h4>
          <p>{{ backgroundDefinition }}</p>
          <b>{{ selectedClueRatioExplanation }}</b>
        </section>
        <section class="evidence-table">
          <h4>关键计算证据</h4>
          <dl>
            <template v-for="(value, key) in compactEvidence(selectedClue.evidence)" :key="String(key)">
              <dt>{{ evidenceLabel(String(key)) }}</dt>
              <dd>{{ displayEvidence(value) }}</dd>
            </template>
          </dl>
        </section>
        <el-alert
          v-for="item in selectedClue.limitations"
          :key="item"
          class="limit-alert"
          type="warning"
          :closable="false"
          show-icon
          :title="item"
        />
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { http } from "../api/http";
import { useGeochemWorkflow } from "../composables/useGeochemWorkflow";
import CorrelationInsights from "./components/geochem-mining/CorrelationInsights.vue";
import DrillholeProfileChart from "./components/geochem-mining/DrillholeProfileChart.vue";
import VariationInsights from "./components/geochem-mining/VariationInsights.vue";
import GeochemWorkflowNav from "./components/geochem-workflow/GeochemWorkflowNav.vue";

const props = defineProps<{ modelId: string; algorithmType: "variation" | "correlation" }>();
const route = useRoute();
const router = useRouter();
const { workflow, creating, error, loadLatest, create, refresh } = useGeochemWorkflow(props.modelId);
const runningAction = ref(false);
const activeClueId = ref("");
const evidenceVisible = ref(false);
const selectedClue = ref<any>(null);
const profilePoints = ref<any[]>([]);

const shortHash = computed(() => workflow.value?.dataset_hash?.slice(0, 8) || "—");
const variationReady = computed(() => Boolean(workflow.value?.variation_job_id)
  && !["variation_pending", "variation_running", "variation_failed"].includes(workflow.value?.stage || ""));
const correlationReady = computed(() => Boolean(workflow.value?.correlation_job_id)
  && ["reconstruct_pending", "reconstruction_running", "reconstruction_failed", "ready", "ready_with_limits"]
    .includes(workflow.value?.stage || ""));
const canRunVariation = computed(() => ["variation_pending", "variation_failed"].includes(workflow.value?.stage || ""));
const currentCombination = computed(() => route.query.members?.toString() || "");
const backgroundDefinition = computed(() => {
  const method = workflow.value?.rule_set?.background_method === "log_mad"
    ? "对数尺度中位数与 MAD 稳健统计"
    : "当前规则指定的统计方法";
  const scope = workflow.value?.rule_set?.background_scope === "model" ? "本项目全部合格化验数据" : "当前统计范围内的合格化验数据";
  return `背景值是用${scope}，按${method}计算出的该元素基准水平，用来比较孔段相对通常水平的富集程度；它不是边界品位、不是最低工业品位，也不表示元素含量为零。`;
});
const selectedClueRatioExplanation = computed(() => {
  const peak = Number(selectedClue.value?.evidence?.max_value);
  const ratio = Number(selectedClue.value?.max_background_ratio);
  const background = Number.isFinite(peak) && Number.isFinite(ratio) && ratio > 0 ? peak / ratio : Number.NaN;
  if (![peak, ratio, background].every(Number.isFinite) || background <= 0) return "背景值缺失，不能计算背景倍数。";
  return `${selectedClue.value.main_element} 峰值 ${format(peak)} ppm ÷ 背景值 ${format(background)} ppm = ${format(ratio)} 倍。`;
});

async function createWorkflow() {
  await create();
}

async function runVariation() {
  if (!workflow.value) return;
  runningAction.value = true;
  try {
    await http.post(`/api/geochem-workflows/${workflow.value.id}/actions/run-variation`);
    await refresh();
    ElMessage.success("变化规律任务已启动，正在计算全部元素");
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "启动变化规律分析失败");
  } finally {
    runningAction.value = false;
  }
}

async function analyzeCorrelation(clue: any) {
  if (!workflow.value) return;
  runningAction.value = true;
  activeClueId.value = clue.id;
  try {
    await http.post(`/api/geochem-workflows/${workflow.value.id}/clues/${clue.id}/actions/run-correlation`);
    await refresh();
    await router.push({
      path: `/models/${props.modelId}/geochem-mining/correlation`,
      query: { workflow: workflow.value.id, clue: clue.id, element: clue.main_element },
    });
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "相关性任务启动失败");
  } finally {
    runningAction.value = false;
    activeClueId.value = "";
  }
}

async function start3d(clue: any, element: string) {
  if (!workflow.value) return;
  runningAction.value = true;
  try {
    const response = await http.post(
      `/api/geochem-workflows/${workflow.value.id}/clues/${clue.id}/actions/run-reconstruction`,
      { element },
    );
    await router.push({
      path: `/models/${props.modelId}/reconstruct/geochem`,
      query: { workflow: workflow.value.id, clue: clue.id, element, job: response.data.job_id },
    });
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "三维验证任务启动失败");
  } finally {
    runningAction.value = false;
  }
}

async function openEvidence(clue: any) {
  selectedClue.value = clue;
  profilePoints.value = [];
  evidenceVisible.value = true;
  if (clue.kind !== "variation" || !workflow.value?.variation_job_id || firstHole(clue) === "未知钻孔") return;
  try {
    const response = await http.get(
      `/api/geochem-mining/jobs/${workflow.value.variation_job_id}/variation/profile`,
      { params: { element: clue.main_element, hole_id: firstHole(clue) } },
    );
    profilePoints.value = response.data.items || [];
  } catch {
    profilePoints.value = [];
  }
}

function goVariation() {
  router.push(`/models/${props.modelId}/geochem-mining/variation`);
}
function firstHole(clue: any) { return clue?.hole_ids?.[0] || "未知钻孔"; }
function depthRange(clue: any) {
  if (clue?.from_depth == null || clue?.to_depth == null) return "深度待复核";
  return `${format(clue.from_depth)}–${format(clue.to_depth)} m`;
}
function format(value: any) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed >= 100 ? parsed.toFixed(1) : parsed.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}
function spatialLabel(clue: any) {
  const grade = clue.professional_evidence?.spatial_grade || clue.highest_level;
  return ({
    cross_hole: "跨孔重复支持",
    same_hole: "同孔共同异常",
    multi_hole_overlap: "多孔共同异常分布",
    statistical_only: "仅统计关联",
  } as Record<string, string>)[grade] || "待空间复核";
}
function compactEvidence(evidence: Record<string, any> = {}) {
  const keys = [
    "value", "max_value", "background_B", "max_value_to_background_ratio", "boundary_grade_ppm",
    "industrial_grade_ppm", "co_anomaly_count", "support", "confidence",
    "lift", "odds_ratio_corrected", "fisher_p", "bh_q", "spearman_r",
    "supporting_drillhole_count", "element_count", "confidence_level",
    "mean_internal_pearson_r", "mean_internal_spearman_r", "stable_pair_ratio",
    "co_anomaly_ratio", "dominant_depth_range", "evidence_reason",
  ];
  return Object.fromEntries(keys
    .filter((key) => evidence[key] !== undefined && evidence[key] !== null)
    .map((key) => [key, evidence[key]]));
}
function evidenceLabel(key: string) {
  return ({
    value: "实测值",
    max_value: "孔段峰值（ppm）",
    background_B: "背景值 B",
    max_value_to_background_ratio: "最高背景倍数",
    boundary_grade_ppm: "边界品位",
    industrial_grade_ppm: "最低工业品位",
    co_anomaly_count: "共同异常数",
    support: "支持度",
    confidence: "置信度",
    lift: "共同异常提升 Lift",
    odds_ratio_corrected: "优势比 OR",
    fisher_p: "Fisher p",
    bh_q: "BH 校正 q",
    spearman_r: "Spearman",
    supporting_drillhole_count: "支持钻孔数",
    element_count: "组合元素数",
    confidence_level: "聚类置信度",
    mean_internal_pearson_r: "组内平均 Pearson",
    mean_internal_spearman_r: "组内平均 Spearman",
    stable_pair_ratio: "稳定元素对比例",
    co_anomaly_ratio: "共同正异常比例",
    dominant_depth_range: "主要深度范围",
    evidence_reason: "证据说明",
  } as Record<string, string>)[key] || key;
}
function displayEvidence(value: any) {
  return typeof value === "number" ? format(value) : String(value);
}

watch(
  () => workflow.value?.status,
  () => {
    if (workflow.value?.status === "running") void refresh();
  },
);
onMounted(() => void loadLatest());
</script>

<style scoped>
.workspace{display:flex;flex-direction:column;gap:16px}.empty-state{display:grid;min-height:430px;place-items:center;align-content:center;text-align:center;border:1px dashed #c9d6e5;border-radius:18px;background:#fff}.empty-state h2{margin:8px 0;color:#17385e}.empty-state p{max-width:650px;margin:0 0 22px;color:#6f8195;line-height:1.7}.empty-icon{display:grid;width:58px;height:58px;place-items:center;border-radius:50%;background:#eef5ff;color:#2872dc;font-size:32px}.scope-bar{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));overflow:hidden;border:1px solid #dce7f2;border-radius:16px;background:#fff;box-shadow:0 8px 24px rgba(31,66,107,.05)}.scope-bar div{padding:15px 18px;border-right:1px solid #edf1f5}.scope-bar div:last-child{border-right:0}.scope-bar b,.scope-bar span{display:block}.scope-bar b{color:#175ead;font-size:23px}.scope-bar span{margin-top:4px;color:#7b899b;font-size:12px}.run-gate{display:flex;min-height:190px;padding:30px;align-items:center;gap:22px;border:1px solid #dce7f2;border-radius:18px;background:linear-gradient(135deg,#fff,#f3f8ff);box-shadow:0 12px 32px rgba(30,72,120,.07)}.run-gate.warning{background:linear-gradient(135deg,#fff,#fff9ed)}.gate-icon{display:grid;width:58px;height:58px;place-items:center;border-radius:18px;background:#2675df;color:#fff;font-size:25px;font-weight:900;box-shadow:0 10px 24px rgba(38,117,223,.25)}.run-gate div{flex:1}.run-gate h2{margin:0 0 7px;color:#183b62}.run-gate p{margin:0;color:#718298;line-height:1.65}.running-box{padding:34px;border:1px solid #dce7f2;border-radius:18px;background:#fff;text-align:center}.running-box h2{color:#173c66}.running-box p{color:#76869a}.drawer-summary{display:flex;align-items:center;gap:12px;margin-bottom:16px;padding:14px;border-radius:12px;background:#f4f8fd}.drawer-summary p{margin:3px 0 0;color:#718196}.element{display:grid;min-width:46px;height:46px;padding:0 8px;place-items:center;border-radius:12px;background:#edf5ff;color:#145aa9;font-size:22px;font-weight:900}.profile-section,.evidence-table{margin-top:20px}.profile-section h4,.evidence-table h4{color:#284868}.profile-section p{color:#7a899a;font-size:12px;line-height:1.6}.evidence-table dl{display:grid;grid-template-columns:1fr 1fr;margin:0;border:1px solid #e1e8f0}.evidence-table dt,.evidence-table dd{margin:0;padding:9px 11px;border-bottom:1px solid #edf1f5}.evidence-table dt{color:#718196;background:#f7f9fc}.evidence-table dd{color:#294867;word-break:break-all}.limit-alert{margin-top:10px}@media(max-width:1000px){.scope-bar{grid-template-columns:repeat(2,1fr)}.run-gate{align-items:flex-start;flex-wrap:wrap}}
.background-evidence{margin-top:20px;padding:13px;border:1px solid #d8e7f5;border-radius:10px;background:#f5faff}.background-evidence h4{color:#284868}.background-evidence p{margin:0;color:#62778d;font-size:12px;line-height:1.65}.background-evidence b{display:block;margin-top:7px;color:#1d5f9e}
</style>
