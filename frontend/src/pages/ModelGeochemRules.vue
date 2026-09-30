<template>
  <div class="page">
    <GeochemWorkflowNav :model-id="modelId" current="rules" />

    <header class="page-head">
      <div>
        <h2>化学元素规则与解释口径</h2>
        <p>模型 {{ modelId }} · 所有后续任务必须绑定一个规则集版本。</p>
      </div>
      <div class="actions">
        <el-select v-model="selectedId" placeholder="规则版本" @change="loadSelected">
          <el-option
            v-for="item in versions"
            :key="item.id"
            :label="`v${item.version} · ${item.name} · ${statusLabel(item.status)}`"
            :value="item.id"
          />
        </el-select>
        <el-button @click="startNewVersion">复制为新版本</el-button>
        <el-button type="primary" :loading="saving" @click="saveVersion">保存草案</el-button>
      </div>
    </header>

    <el-alert
      :type="form.status === 'confirmed' ? 'success' : 'warning'"
      show-icon
      :closable="false"
      :title="form.status === 'confirmed'
        ? '该版本已确认并锁定，后续修改请创建新版本。'
        : '当前为草案：边界品位已录入；最低工业品位和相对背景倍数仍待项目确认。'"
    />

    <el-card>
      <template #header>
        <div class="card-head">
          <span>项目级统一口径</span>
          <el-tag :type="form.status === 'confirmed' ? 'success' : 'warning'">
            {{ statusLabel(form.status) }}
          </el-tag>
        </div>
      </template>
      <el-form label-width="160px" class="rule-form">
        <el-form-item label="规则名称">
          <el-input v-model="form.name" :disabled="locked" />
        </el-form-item>
        <el-form-item label="背景方法">
          <el-select v-model="form.background_method" :disabled="locked">
            <el-option label="专家公式：3σ剔除 + X + 2σ" value="expert_2sigma" />
            <el-option label="对数域 Median + MAD（推荐）" value="log_mad" />
            <el-option label="原值均值 + 3σ 迭代（仅敏感性对照）" value="iterative_upper" />
          </el-select>
        </el-form-item>
        <el-form-item label="背景范围">
          <el-radio-group v-model="form.background_scope" :disabled="locked">
            <el-radio value="model">全模型冻结背景</el-radio>
            <el-radio value="selection">当前筛选背景（探索用）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="相对背景分级切点">
          <div class="cutoffs">
            <el-input-number
              v-for="(_, index) in form.relative_ratio_cutoffs"
              :key="index"
              v-model="form.relative_ratio_cutoffs[index]"
              :min="1.01"
              :step="0.25"
              :precision="2"
              :disabled="locked"
            />
          </div>
          <div class="hint">三个切点依次为 1.25、1.5、2 倍背景；达到统计阈值后改用 T/2T/4T 分带。</div>
        </el-form-item>
        <el-form-item label="候选区指示概率">
          <el-slider
            v-model="form.support_probability_cutoff"
            :min="0.1"
            :max="0.9"
            :step="0.05"
            show-input
            :disabled="locked"
          />
        </el-form-item>
        <el-form-item label="正式背景最小正值数">
          <el-input-number v-model="form.minimum_positive_count" :min="3" :disabled="locked" />
        </el-form-item>
        <el-form-item label="异常段合并与长度">
          <div class="cutoffs">
            <span>最大间隔</span>
            <el-input-number v-model="form.merge_gap_m" :min="0" :step="0.5" :disabled="locked" />
            <span>米；最小连续长度</span>
            <el-input-number v-model="form.minimum_segment_length_m" :min="0" :step="0.5" :disabled="locked" />
            <span>米</span>
          </div>
        </el-form-item>
        <el-form-item label="规则来源文档">
          <el-input v-model="form.source_document" :disabled="locked" />
        </el-form-item>
        <el-form-item label="版本说明">
          <el-input v-model="form.notes" type="textarea" :rows="3" :disabled="locked" />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card>
      <template #header>
        <div class="card-head">
          <span>元素级专业品位</span>
          <span class="hint">最低工业品位未提供时保持“待确认”，系统不会自动推定。</span>
        </div>
      </template>
      <el-table :data="form.element_rules" max-height="520" stripe>
        <el-table-column prop="element" label="元素" width="80" fixed />
        <el-table-column label="元素角色" width="125">
          <template #default="{ row }">
            <el-select v-model="row.role" :disabled="locked">
              <el-option label="目标元素" value="target" />
              <el-option label="伴生元素" value="companion" />
              <el-option label="指示元素" value="indicator" />
              <el-option label="背景对照" value="background" />
              <el-option label="其他/待确认" value="other" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="unit" label="单位" width="70" />
        <el-table-column label="检出限" min-width="140">
          <template #default="{ row }">
            <el-input-number v-model="row.detection_limit" :min="0.000001" clearable :disabled="locked" />
          </template>
        </el-table-column>
        <el-table-column label="检出限状态" width="120">
          <template #default="{ row }">
            <el-select v-model="row.detection_limit_status" :disabled="locked">
              <el-option label="已确认" value="confirmed" />
              <el-option label="待确认" value="pending" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="边界品位" min-width="150">
          <template #default="{ row }">
            <el-input-number v-model="row.boundary_grade_ppm" :min="0.000001" :disabled="locked" />
          </template>
        </el-table-column>
        <el-table-column label="边界状态" width="120">
          <template #default="{ row }">
            <el-select v-model="row.boundary_status" :disabled="locked">
              <el-option label="已确认" value="confirmed" />
              <el-option label="待确认" value="pending" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="最低工业品位" min-width="150">
          <template #default="{ row }">
            <el-input-number v-model="row.industrial_grade_ppm" :min="0.000001" clearable :disabled="locked" />
          </template>
        </el-table-column>
        <el-table-column label="工业状态" width="120">
          <template #default="{ row }">
            <el-select v-model="row.industrial_status" :disabled="locked">
              <el-option label="已确认" value="confirmed" />
              <el-option label="待确认" value="pending" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="industrial_source" label="来源/备注" min-width="220">
          <template #default="{ row }">
            <el-input v-model="row.industrial_source" :disabled="locked" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="form.id && form.status !== 'confirmed'">
      <template #header>版本确认</template>
      <el-checkbox v-model="acknowledgePending">
        我确认当前仍为待定的元素项已完成项目审核，并允许将该版本标记为“已确认”
      </el-checkbox>
      <div class="confirm-row">
        <el-button type="success" :disabled="!acknowledgePending" @click="confirmCurrent">
          标记为已确认版本
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import GeochemWorkflowNav from "./components/geochem-workflow/GeochemWorkflowNav.vue";

const props = defineProps<{ modelId: string }>();
const modelId = Number(props.modelId);
const versions = ref<any[]>([]);
const selectedId = ref("");
const saving = ref(false);
const acknowledgePending = ref(false);
const form = reactive<any>({
  id: "",
  name: "",
  status: "draft",
  background_method: "log_mad",
  background_scope: "model",
  relative_ratio_cutoffs: [1.25, 1.5, 2],
  support_probability_cutoff: 0.5,
  minimum_positive_count: 30,
  merge_gap_m: 0.5,
  minimum_segment_length_m: 0,
  source_document: "",
  notes: "",
  element_rules: [],
});
const locked = computed(() => form.status === "confirmed");
function statusLabel(status?: string) {
  return status === "confirmed" ? "已确认" : "待项目确认";
}

function assignRule(value: any) {
  Object.assign(form, JSON.parse(JSON.stringify(value)));
  acknowledgePending.value = false;
}

async function reload() {
  const [list, current] = await Promise.all([
    http.get(`/api/models/${modelId}/geochem-rules`),
    http.get(`/api/models/${modelId}/geochem-rules/current`),
  ]);
  versions.value = list.data.items || [];
  selectedId.value = current.data.id;
  assignRule(current.data);
}

async function loadSelected() {
  if (!selectedId.value) return;
  assignRule((await http.get(`/api/geochem-rules/${selectedId.value}`)).data);
}

function startNewVersion() {
  form.id = "";
  form.status = "draft";
  form.name = `${form.name.replace(/\s*v\d+$/i, "")} 新版本`;
  acknowledgePending.value = false;
}

async function saveVersion() {
  if (locked.value) return ElMessage.warning("已确认版本不可直接修改，请先复制为新版本。");
  saving.value = true;
  try {
    const payload = {
      name: form.name,
      background_method: form.background_method,
      background_scope: form.background_scope,
      relative_ratio_cutoffs: form.relative_ratio_cutoffs,
      support_probability_cutoff: form.support_probability_cutoff,
      minimum_positive_count: form.minimum_positive_count,
      merge_gap_m: form.merge_gap_m,
      minimum_segment_length_m: form.minimum_segment_length_m,
      source_document: form.source_document,
      notes: form.notes,
      element_rules: form.element_rules,
    };
    const saved = (await http.post(`/api/models/${modelId}/geochem-rules`, payload)).data;
    ElMessage.success(`已保存规则集 v${saved.version}`);
    await reload();
    selectedId.value = saved.id;
    await loadSelected();
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function confirmCurrent() {
  try {
    const confirmed = (
      await http.post(`/api/geochem-rules/${form.id}/confirm`, {
        acknowledge_pending: acknowledgePending.value,
      })
    ).data;
    assignRule(confirmed);
    await reload();
    ElMessage.success("该规则版本已标记为已确认");
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || "确认失败");
  }
}

onMounted(reload);
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.page-head, .card-head, .actions, .confirm-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.page-head h2 { margin: 0; }
.page-head p, .hint { color: #71829d; font-size: 13px; }
.actions :deep(.el-select) { width: 330px; }
.rule-form { max-width: 900px; }
.cutoffs { display: flex; flex-wrap: wrap; gap: 8px; }
.confirm-row { justify-content: flex-end; margin-top: 14px; }
</style>
