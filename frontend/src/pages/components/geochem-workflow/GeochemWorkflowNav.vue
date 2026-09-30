<template>
  <section class="workflow-hub">
    <div class="rules-entry">
      <span>背景值方法：</span>
      <router-link :to="expertPath">专家公式校准模块（3σ剔除 + X + 2σ）</router-link>
    </div>
    <div class="hub-head">
      <div class="hub-copy">
        <span class="product-label">化学元素找矿参考工作台</span>
        <h1>{{ currentTitle }}</h1>
        <p>{{ currentQuestion }}</p>
        <div v-if="workflow" class="snapshot-tags">
          <span>数据快照 {{ workflow.dataset_hash.slice(0, 10) }}</span>
          <span>{{ workflow.data_scope.element_count }} 种元素全量参与</span>
          <span>{{ workflow.data_scope.chemical_drillhole_count }} 个化验钻孔</span>
          <span>规则状态：{{ ruleStatusLabel }}</span>
        </div>
        <div v-else class="snapshot-tags">
          <span>先冻结数据与规则，再主动运行算法</span>
        </div>
      </div>
      <el-progress
        v-if="workflow"
        :percentage="workflow.progress"
        type="circle"
        :width="82"
        :stroke-width="8"
        color="#56d7b0"
        :status="workflow.status === 'failed' ? 'exception' : undefined"
      />
    </div>
    <div class="pipeline">
      <div v-for="step in steps" :key="step.key" class="step" :class="stepClass(step)">
        <span class="dot">{{ step.number }}</span>
        <span class="copy">
          <b>{{ step.label }}</b>
          <small>{{ step.note }}</small>
        </span>
        <span v-if="step.done" class="state">完成</span>
        <span v-else-if="step.running" class="state running">计算中</span>
        <span v-else-if="step.key === current" class="state current">当前</span>
      </div>
    </div>
    <div v-if="workflow" class="result-chain">
      <div>
        <b>输入</b>
        <span>{{ workflow.data_scope.element_count }} 元素 · {{ workflow.data_scope.chemical_drillhole_count }} 化验孔 · {{ workflow.data_scope.assay_count }} 区间</span>
      </div>
      <i>→</i>
      <div>
        <b>算法一输出</b>
        <span>{{ workflow.clue_counts.variation }} 条“元素—孔—深度”线索</span>
      </div>
      <i>→</i>
      <div>
        <b>算法二输出</b>
        <span>{{ workflow.clue_counts.correlation }} 个组合{{ selectedCombination ? `；当前 ${selectedCombination}` : "" }}</span>
      </div>
      <i>→</i>
      <div>
        <b>算法三输出</b>
        <span>{{ selectedElement ? `当前验证 ${selectedElement}` : "从组合中选择单元素作空间验证" }}</span>
      </div>
    </div>
    <div class="delivery-boundary">
      <b>系统交付边界</b>
      <span>输出为优先核查线索、空间延伸证据与找矿概率参考，不代表已经发现或圈定矿体。</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import type { GeochemWorkflow } from "../../../composables/useGeochemWorkflow";

const props = defineProps<{
  workflow: GeochemWorkflow | null;
  current: "rules" | "variation" | "correlation" | "reconstruct";
  selectedCombination?: string;
  selectedElement?: string;
}>();
const route = useRoute();
const expertPath = computed(() => `/models/${route.params.modelId}/geochem-workspace/expert`);

const stages = [
  "variation_pending", "variation_running", "variation_failed", "clue_selection",
  "correlation_running", "correlation_failed", "reconstruct_pending",
  "reconstruction_running", "reconstruction_failed", "ready", "ready_with_limits",
];

const stageIndex = computed(() => stages.indexOf(props.workflow?.stage || "variation_pending"));
const currentTitle = computed(() => ({
  rules: "准备阶段：统一数据解释规则与计算口径",
  variation: "第一步：识别值得优先核查的元素与孔段",
  correlation: "第二步：识别共同异常的多元素组合",
  reconstruct: "第三步：把候选线索放回地质空间验证",
}[props.current]));
const currentQuestion = computed(() => ({
  rules: "冻结背景计算、异常分级与专业品位来源，保证三个算法使用同一口径。",
  variation: "回答“什么元素、哪个钻孔、哪段深度更值得优先核查”。",
  correlation: "回答“哪些元素在相同或相邻空间内稳定共同异常”。",
  reconstruct: "回答“选定元素的异常趋势在地质体、断层和钻孔约束下如何延伸”。",
}[props.current]));
const ruleStatusLabel = computed(() => ({
  confirmed: "已确认",
  draft: "待项目确认",
}[props.workflow?.rule_set.status || "draft"] || props.workflow?.rule_set.status || "待项目确认"));
const steps = computed(() => [
  {
    key: "quality", number: 1, label: "数据与规则", note: props.workflow
      ? `${props.workflow.data_scope.element_count} 元素 / ${props.workflow.data_scope.chemical_drillhole_count} 化验孔`
      : "冻结本次计算口径",
    done: !!props.workflow, running: false,
  },
  {
    key: "variation", number: 2, label: "变化规律", note: "回答什么元素、哪个孔段异常",
    done: stageIndex.value >= 3, running: props.workflow?.stage === "variation_running",
  },
  {
    key: "correlation", number: 3, label: "元素相关性", note: "回答哪些元素共同异常",
    done: stageIndex.value >= 6, running: props.workflow?.stage === "correlation_running",
  },
  {
    key: "reconstruct", number: 4, label: "单元素三维验证", note: "回答线索在地质空间如何延伸",
    done: ["ready", "ready_with_limits"].includes(props.workflow?.stage || ""),
    running: props.workflow?.stage === "reconstruction_running",
  },
  {
    key: "evidence", number: 5, label: "证据包", note: "来源、限制、产物可追溯",
    done: ["ready", "ready_with_limits"].includes(props.workflow?.stage || ""),
    running: false,
  },
]);

function stepClass(step: any) {
  return {
    active: step.key === props.current,
    done: step.done,
    running: step.running,
  };
}
</script>

<style scoped>
.workflow-hub{border:1px solid #cfddeb;border-radius:18px;background:#fff;box-shadow:0 12px 34px rgba(25,63,102,.08);overflow:hidden}
.rules-entry{display:flex;align-items:center;gap:8px;padding:10px 18px;background:#fff8e8;border-bottom:1px solid #f0dfb2;color:#76591f;font-size:13px}.rules-entry a{color:#1769b0;font-weight:700;text-decoration:none}.rules-entry a:hover{text-decoration:underline}
.hub-head{display:grid;grid-template-columns:minmax(0,1fr) 92px;align-items:center;gap:30px;padding:24px 28px;color:#fff;background:linear-gradient(125deg,#102f52 0%,#155c91 58%,#16887e 100%)}
.product-label{display:block;margin-bottom:8px;color:#8fe5cc;font-size:12px;font-weight:800;letter-spacing:.12em}
.hub-head h1{margin:0;font-size:25px;line-height:1.25}.hub-head p{margin:8px 0 0;color:#d6e8f5;line-height:1.6}
.snapshot-tags{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}.snapshot-tags span{padding:5px 9px;border:1px solid rgba(255,255,255,.22);border-radius:999px;background:rgba(255,255,255,.09);color:#e7f5fb;font-size:11px}
.pipeline{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;padding:16px 18px 12px;background:#f6f9fc}
.step{position:relative;display:flex;align-items:center;min-height:70px;padding:11px;border:1px solid #dbe5ef;border-radius:11px;background:#fff;color:#6f8196}
.step.active{border-color:#2d78d1;background:#edf6ff;color:#134f8d;box-shadow:0 5px 16px rgba(28,103,176,.12)}.step.done{border-color:#b9e2d5}.step.done .dot{background:#1f9d70}.step.running .dot{background:#2d78e6}
.dot{display:grid;flex:0 0 30px;width:30px;height:30px;margin-right:9px;place-items:center;border-radius:9px;background:#a8b6c8;color:#fff;font-weight:800}
.copy b,.copy small{display:block}.copy b{font-size:13px}.copy small{margin-top:4px;font-size:11px;line-height:1.35}
.state{position:absolute;right:7px;top:6px;color:#19835e;font-size:10px;font-weight:700}.state.running,.state.current{color:#216cc4}
.result-chain{display:grid;grid-template-columns:minmax(0,1fr) 26px minmax(0,1fr) 26px minmax(0,1fr) 26px minmax(0,1fr);align-items:stretch;gap:5px;padding:5px 18px 14px;background:#f6f9fc}.result-chain div{min-width:0;padding:11px 12px;border-radius:9px;background:#eaf2f9}.result-chain b,.result-chain span{display:block}.result-chain b{color:#1a578e;font-size:12px}.result-chain span{margin-top:4px;color:#597087;font-size:11px;line-height:1.45}.result-chain i{align-self:center;text-align:center;color:#2f78c8;font-style:normal;font-weight:800}
.delivery-boundary{display:flex;gap:12px;padding:11px 18px;border-top:1px solid #e3eaf1;background:#fff9ee;color:#725a31;font-size:12px}.delivery-boundary b{flex:0 0 auto;color:#a16412}
@media(max-width:1100px){.pipeline{grid-template-columns:1fr 1fr}.hub-head{grid-template-columns:1fr}.result-chain{grid-template-columns:1fr}.result-chain i{transform:rotate(90deg)}}
</style>
