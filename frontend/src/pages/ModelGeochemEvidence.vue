<template>
  <div class="page">
    <GeochemWorkflowNav :model-id="modelId" current="evidence" />

    <header class="page-head">
      <div>
        <h2>证据链与验收</h2>
        <p>模型 {{ modelId }} · 核对规则、变化任务、相关任务和三维任务是否来自同一条分析链。</p>
      </div>
      <el-button type="primary" :loading="loading" @click="load">刷新证据</el-button>
    </header>

    <el-alert
      :type="chainStatus.ok ? 'success' : 'warning'"
      show-icon
      :closable="false"
      :title="chainStatus.text"
    />

    <section class="chain-grid">
      <el-card v-for="item in chainCards" :key="item.title" shadow="never">
        <template #header>{{ item.title }}</template>
        <strong>{{ item.id || "暂无成功任务" }}</strong>
        <p>{{ item.detail }}</p>
        <el-tag :type="item.status === 'success' ? 'success' : 'info'">{{ item.status || "missing" }}</el-tag>
      </el-card>
    </section>

    <el-card>
      <template #header>相关性候选组合（证据优先）</template>
      <el-empty v-if="!candidateCards.length" description="暂无绑定变化任务的候选组合证据" />
      <el-table v-else :data="candidateCards" max-height="420" stripe>
        <el-table-column prop="evidence_tier" label="等级" width="70">
          <template #default="{ row }">
            <el-tag :type="row.evidence_tier === 'A' ? 'success' : row.evidence_tier === 'B' ? 'warning' : 'info'">
              {{ row.evidence_tier }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="元素对" min-width="120">
          <template #default="{ row }">{{ row.target_element }} - {{ row.candidate_element }}</template>
        </el-table-column>
        <el-table-column prop="co_anomaly_count" label="共异常" width="90" />
        <el-table-column label="Lift" width="90"><template #default="{ row }">{{ fmt(row.lift) }}</template></el-table-column>
        <el-table-column label="log-Pearson" width="120"><template #default="{ row }">{{ fmt(row.pearson_log_r) }}</template></el-table-column>
        <el-table-column label="Spearman" width="110"><template #default="{ row }">{{ fmt(row.spearman_r) }}</template></el-table-column>
        <el-table-column prop="same_hole_event_count" label="同孔事件" width="100" />
        <el-table-column prop="cross_hole_proximity_count" label="跨孔近邻" width="100" />
        <el-table-column prop="evidence_reason" label="证据说明" min-width="300" />
      </el-table>
    </el-card>

    <div class="two-column">
      <el-card>
        <template #header>三维留一孔验证</template>
        <el-empty v-if="!validation.length" description="暂无留一孔验证结果" />
        <el-table v-else :data="validation" stripe>
          <el-table-column prop="transform" label="插值尺度" />
          <el-table-column prop="validation_quality" label="质量">
            <template #default="{ row }">
              <el-tag :type="row.validation_quality === 'high' ? 'success' : row.validation_quality === 'medium' ? 'warning' : 'danger'">
                {{ row.validation_quality }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="覆盖率"><template #default="{ row }">{{ percent(row.coverage) }}</template></el-table-column>
          <el-table-column label="RMSE"><template #default="{ row }">{{ fmt(row.rmse) }}</template></el-table-column>
          <el-table-column label="log-MAE"><template #default="{ row }">{{ fmt(row.log_mae) }}</template></el-table-column>
          <el-table-column label="Spearman"><template #default="{ row }">{{ fmt(row.spearman_r) }}</template></el-table-column>
          <el-table-column label="中位相对误差"><template #default="{ row }">{{ percent(row.median_absolute_relative_error) }}</template></el-table-column>
        </el-table>
      </el-card>

    </div>

    <el-card>
      <template #header>交付前检查</template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="规则状态">{{ currentRule?.status || "-" }}</el-descriptions-item>
        <el-descriptions-item label="规则版本">v{{ currentRule?.version || "-" }}</el-descriptions-item>
        <el-descriptions-item label="变化证据拆分">
          统计 {{ variation?.summary?.variation?.statistical_anomaly_count ?? "-" }} /
          边界 {{ variation?.summary?.variation?.boundary_grade_count ?? "-" }} /
          工业 {{ variation?.summary?.variation?.industrial_grade_count ?? "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="正式相关候选">{{ correlation?.summary?.correlation?.formal_candidate_count ?? 0 }}</el-descriptions-item>
        <el-descriptions-item label="原始点独立输出">{{ reconstruct?.summary?.raw_sample_fidelity?.raw_points_are_separate_output ? "是" : "否" }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { http } from "../api/http";
import GeochemWorkflowNav from "./components/geochem-workflow/GeochemWorkflowNav.vue";

const props = defineProps<{ modelId: string }>();
const modelId = Number(props.modelId);
const loading = ref(false);
const currentRule = ref<any>(null);
const variation = ref<any>(null);
const correlation = ref<any>(null);
const reconstruct = ref<any>(null);
const validation = ref<any[]>([]);

const candidateCards = computed(() => correlation.value?.summary?.correlation?.candidate_cards || []);
const chainCards = computed(() => [
  { title: "1 规则集", id: currentRule.value?.id, status: currentRule.value ? "success" : "", detail: currentRule.value ? `v${currentRule.value.version} · ${currentRule.value.status}` : "未建立规则" },
  { title: "2 变化规律", id: variation.value?.id, status: variation.value?.status, detail: variation.value ? `规则 ${variation.value.rule_set_id || "-"}` : "无任务" },
  { title: "3 相关与共异常", id: correlation.value?.id, status: correlation.value?.status, detail: correlation.value ? `绑定变化任务 ${correlation.value.source_variation_job_id || "未绑定"}` : "无任务" },
  { title: "4 三维重建", id: reconstruct.value?.id, status: reconstruct.value?.status, detail: reconstruct.value ? `绑定变化任务 ${reconstruct.value.source_variation_job_id || "未绑定"}` : "无任务" },
]);
const chainStatus = computed(() => {
  const ruleId = currentRule.value?.id;
  const variationId = variation.value?.id;
  const sameRule = ruleId && variation.value?.rule_set_id === ruleId
    && correlation.value?.rule_set_id === ruleId
    && reconstruct.value?.rule_set_id === ruleId;
  const bound = variationId
    && correlation.value?.source_variation_job_id === variationId
    && reconstruct.value?.source_variation_job_id === variationId;
  if (sameRule && bound) return { ok: true, text: "证据链完整：三个算法绑定同一规则集，相关性和三维重建绑定同一变化规律任务。" };
  return { ok: false, text: "证据链尚未完全闭合：请核对规则集版本，并让相关性和三维重建显式绑定同一个变化规律任务。" };
});

function fmt(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "-";
}
function percent(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "-";
}

async function load() {
  loading.value = true;
  try {
    const [rule, miningHistory, reconstructionLatest] = await Promise.all([
      http.get(`/api/models/${modelId}/geochem-rules/current`),
      http.get(`/api/models/${modelId}/geochem-mining/jobs`, { params: { page: 1, page_size: 100 } }),
      http.get(`/api/models/${modelId}/geochem/latest`),
    ]);
    currentRule.value = rule.data;
    const miningJobs = miningHistory.data.items || [];
    variation.value = miningJobs.find((item: any) => ["variation", "both"].includes(item.algorithm_mode)) || null;
    correlation.value = miningJobs.find((item: any) => ["correlation", "both"].includes(item.algorithm_mode)) || null;
    reconstruct.value = reconstructionLatest.data.job;
    if (reconstruct.value?.status === "success") {
      const [validationResult] = await Promise.all([
        http.get(`/api/geochem/jobs/${reconstruct.value.id}/validation`),
      ]);
      validation.value = validationResult.data.items || [];
    } else {
      validation.value = [];
    }
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.page-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-head h2 { margin: 0; }
.page-head p, .chain-grid p { color: #71829d; font-size: 13px; }
.chain-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.chain-grid strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.two-column { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 1100px) {
  .chain-grid, .two-column { grid-template-columns: 1fr; }
}
</style>
