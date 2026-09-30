<template>
  <div class="workspace">
    <GeochemWorkflowNav
      :workflow="workflow"
      current="reconstruct"
      :selected-combination="selectedClue?.members?.join(' + ') || ''"
      :selected-element="selectedElement"
    />
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <section v-if="!workflow" class="guidance">
      <h2>三维验证必须来自前两步的线索</h2>
      <p>请先运行变化规律，再选择一个元素或元素组合。系统不会在进入本页时随意重建 Rb 或 Cu。</p>
      <el-button type="primary" @click="goVariation">从变化规律开始</el-button>
    </section>

    <template v-else>
      <section class="headline">
        <div>
          <span>算法三 · 化学元素三维重建</span>
          <h2>{{ selectedElement || "单元素" }} 的地质约束空间验证</h2>
          <p>读取算法 B 的 15 m 地质体、断层和岩性体素域作为空间约束；按已确认岩性归并与当前元素样点几何进行三轴椭球 IDW，直接生成覆盖非断层地质体的完整相对浓度场。</p>
        </div>
        <el-tag type="warning">找矿参考，不是矿体认定</el-tag>
      </section>

      <section class="provenance">
        <div><b>{{ selectedClue?.members?.join(" + ") || "变化规律全元素数据" }}</b><span>数据来源</span></div>
        <div><b>{{ selectedElement || "—" }}</b><span>本次仅重建一个元素</span></div>
        <div><b>15 × 15 × 15 m</b><span>算法 B 约束体素尺寸</span></div>
        <div><b>{{ workflow.data_scope.project_drillhole_count }}</b><span>场景项目钻孔</span></div>
        <div><b>{{ sceneManifest?.layers?.assay_intervals?.hole_count ?? '—' }}</b><span>当前元素有效化验孔</span></div>
      </section>

      <section v-if="workflow.reconstruction_element_options?.length" class="element-switcher">
        <div class="switcher-copy">
          <span>三维元素切换</span>
          <h3>全部 {{ workflow.reconstruction_element_options.length }} 种元素均已列出</h3>
          <p>每次只计算一个元素，并使用该元素自己的化验数据、背景值和品位阈值；不同元素的浓度不会混成一个颜色场。没有优先异常孔段的元素仍可计算，但会明确标记为非优先线索。</p>
        </div>
        <div class="switcher-actions">
          <div v-if="selectedClue?.members?.length" class="member-buttons">
            <b>当前线索成员</b>
            <el-button
              v-for="element in selectedClue.members || []"
              :key="element"
              :type="element === selectedElement ? 'success' : 'primary'"
              :plain="element !== selectedElement"
              :disabled="element === selectedElement || starting"
              @click="start(element)"
            >{{ element === selectedElement ? `${element}（当前）` : `重建 ${element}` }}</el-button>
          </div>
          <div class="all-element-picker">
            <b>全部有效元素</b>
            <el-select v-model="independentElement" filterable placeholder="选择任意元素" style="width: 180px">
              <el-option
                v-for="option in workflow.reconstruction_element_options || []"
                :key="option.element"
                :label="option.available ? option.element : `${option.element}（无有效背景统计）`"
                :value="option.element"
                :disabled="!option.available"
              >
                <span>{{ option.element }}</span>
                <small v-if="!option.available">{{ option.reason }}</small>
              </el-option>
            </el-select>
            <el-button
              type="primary"
              :loading="starting"
              :disabled="!independentElement || independentElement === selectedElement"
              @click="startIndependent"
            >独立三维验证</el-button>
            <small>{{ workflow.reconstruction_elements?.length || 0 }} 种可重建；其余元素已置灰并说明原因</small>
          </div>
        </div>
      </section>

      <section v-if="workflow.stage === 'reconstruct_pending'" class="guidance">
        <h3>从全部有效元素中选择一个进行三维重建</h3>
        <p>多个元素不会混在一个颜色场中。一次重建一个元素，完成后可以继续切换其他元素对照。</p>
        <div class="element-actions">
          <el-button
            v-for="element in selectedClue?.members || []"
            :key="element"
            type="primary"
            :loading="starting"
            @click="start(element)"
          >重建 {{ element }}</el-button>
        </div>
      </section>

      <section v-else-if="workflow.stage === 'reconstruction_running'" class="running-card">
        <h3>后端正在进行真实三维计算</h3>
        <el-progress :percentage="Math.max(5, workflow.progress)" :stroke-width="14" />
        <ol>
          <li>读取最新成功算法 B 的 15 m 岩性体素域，不修改算法 B 结果；</li>
          <li>用当前元素实际有值的化验钻孔数据进行自适应三轴椭球对数 IDW；</li>
          <li>相近岩性组内优先插值；没有同组样点时才作明确标记的低可信回填，保证非断层地质体不留空洞；</li>
          <li>装配数据库实际读取的地质体、断层、项目钻孔、化验段和完整单元素浓度场。</li>
        </ol>
      </section>

      <section v-else-if="workflow.stage === 'reconstruction_failed'" class="guidance failure">
        <h3>三维任务失败</h3>
        <p>{{ workflow.reconstruction_error || "三维任务未生成结果，请检查任务日志。" }}</p>
        <p>失败不会被隐藏为“空白成功”，历史失败记录也不会被覆盖。</p>
        <el-button v-if="selectedElement" type="primary" :loading="starting" @click="retryReconstruction">
          使用原元素重新运行
        </el-button>
      </section>

      <section v-else-if="sceneLoading" class="scene-loading">
        <el-skeleton :rows="6" animated />
        <p>正在装配地质体、断层、钻孔与化学元素场景，请稍候…</p>
      </section>

      <template v-else-if="sceneManifest">
        <section class="validation" :class="{ limited: workflow.stage === 'ready_with_limits' }">
          <div>
            <span>{{ workflow.stage === "ready" ? "验证通过" : "验证受限" }}</span>
            <h3>{{ validationSentence }}</h3>
            <p>{{ workflow.stage === "ready" ? "插值趋势体可以作为后续地质复核参考。" : "仍显示六级着色实测孔段和探索性插值体，但明确标记验证受限，不能据此认定矿体。" }}</p>
          </div>
          <div class="validation-numbers">
            <b>{{ validationItems.length }}</b><span>验证方案（原值/对数）</span>
            <b>{{ validationStatusLabel }}</b><span>综合状态</span>
          </div>
        </section>

        <GeochemSceneViewer :manifest="sceneManifest" :job-id="workflow.reconstruct_job_id!" />

        <section class="scene-explanation">
          <article><b>地质体 STL</b><p>限定体素计算和结果显示的空间外壳，避免出现悬空的“乱点云”。</p></article>
          <article><b>断层约束</b><p>读取算法 B 的断层编码和断层距离，断层带不参与化学异常体填色。</p></article>
          <article><b>项目钻孔</b><p>钻孔数量和化验孔数量均从当前任务数据动态读取；无化验的钻孔仅作空间参照。</p></article>
          <article><b>公共六色与两类语义</b><p>所有元素共用同一套六色锚点。默认连续场按本元素 P05–P98 显示内部相对变化，不等同于矿产品位；六级专业图例仍按本元素自己的背景值、统计阈值、边界品位和最低工业品位解释。</p></article>
          <article><b>可靠性分级</b><p>颜色回答“值处于什么等级”；椭球内支持与跨域外推分别记录，低支持结果不会升级为可信矿体。</p></article>
          <article><b>留一孔验证</b><p>轮流拿掉一孔再预测该孔，是判断空间延伸能否作为参考的最低验证门槛。</p></article>
        </section>
      </template>

      <section v-else class="guidance">
        <h3>没有可装配的三维场景</h3>
        <p>当前工作流尚未选择相关性组合中的单个元素，或三维产物尚未生成。</p>
        <el-button type="primary" @click="goCorrelation">返回相关性线索</el-button>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { http } from "../api/http";
import { useGeochemWorkflow } from "../composables/useGeochemWorkflow";
import GeochemSceneViewer from "./components/geochem-workflow/GeochemSceneViewer.vue";
import GeochemWorkflowNav from "./components/geochem-workflow/GeochemWorkflowNav.vue";

const props = defineProps<{ modelId: string }>();
const route = useRoute();
const router = useRouter();
const { workflow, error, loadLatest, refresh } = useGeochemWorkflow(props.modelId);
const selectedClue = ref<any>(null);
const sceneManifest = ref<any>(null);
const sceneLoading = ref(false);
const validationItems = ref<any[]>([]);
const validationStatus = ref("待验证");
const starting = ref(false);
const independentElement = ref("");
const selectedElement = computed(() => String(route.query.element || sceneManifest.value?.element || ""));
const validationStatusLabel = computed(() => validationStatus.value === "passed" ? "通过" : "受限");
const validationSentence = computed(() =>
  workflow.value?.stage === "ready"
    ? `${selectedElement.value} 已完成留一孔验证，场景按验证结果开放趋势体。`
    : `${selectedElement.value} 的空间插值证据不足，系统没有把它包装成确定矿体。`,
);

async function loadClue() {
  const clueId = String(route.query.clue || workflow.value?.selected_clue_id || "");
  if (!clueId || !workflow.value) {
    selectedClue.value = null;
    return;
  }
  try {
    selectedClue.value = (await http.get(`/api/geochem-workflows/${workflow.value.id}/clues/${clueId}`)).data;
  } catch {
    selectedClue.value = null;
  }
}

async function loadScene() {
  if (!workflow.value?.reconstruct_job_id || !["ready", "ready_with_limits"].includes(workflow.value.stage)) return;
  sceneLoading.value = true;
  try {
    const [scene, validation] = await Promise.all([
      http.get(`/api/geochem-workflows/${workflow.value.id}/reconstructions/${workflow.value.reconstruct_job_id}/scene`),
      http.get(`/api/geochem-workflows/${workflow.value.id}/reconstructions/${workflow.value.reconstruct_job_id}/validation`),
    ]);
    sceneManifest.value = scene.data;
    validationItems.value = validation.data.items || [];
    validationStatus.value = validation.data.validation_status || "受限";
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "读取三维场景失败");
  } finally {
    sceneLoading.value = false;
  }
}

async function start(element: string) {
  if (!workflow.value || !selectedClue.value) return;
  starting.value = true;
  try {
    const response = await http.post(
      `/api/geochem-workflows/${workflow.value.id}/clues/${selectedClue.value.id}/actions/run-reconstruction`,
      { element },
    );
    await router.replace({ query: { ...route.query, element, job: response.data.job_id } });
    await refresh();
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "启动三维验证失败");
  } finally {
    starting.value = false;
  }
}

async function startIndependent() {
  if (!workflow.value || !independentElement.value) return;
  starting.value = true;
  try {
    const response = await http.post(
      `/api/geochem-workflows/${workflow.value.id}/actions/run-element-reconstruction`,
      { element: independentElement.value },
    );
    const nextQuery: Record<string, any> = {
      ...route.query,
      element: independentElement.value,
      job: response.data.job_id,
    };
    if (response.data.source_clue_id) nextQuery.clue = response.data.source_clue_id;
    else delete nextQuery.clue;
    await router.replace({ query: nextQuery });
    sceneManifest.value = null;
    await refresh();
    await loadClue();
  } catch (reason: any) {
    ElMessage.error(reason?.response?.data?.detail || "启动独立元素三维验证失败");
  } finally {
    starting.value = false;
  }
}

async function retryReconstruction() {
  if (!selectedElement.value) return;
  if (selectedClue.value?.members?.includes(selectedElement.value)) {
    await start(selectedElement.value);
    return;
  }
  independentElement.value = selectedElement.value;
  await startIndependent();
}

function goVariation() { router.push(`/models/${props.modelId}/geochem-mining/variation`); }
function goCorrelation() { router.push(`/models/${props.modelId}/geochem-mining/correlation`); }

watch(() => `${workflow.value?.stage || ""}|${workflow.value?.reconstruct_job_id || ""}|${workflow.value?.selected_clue_id || ""}`, async () => {
  await loadClue();
  await loadScene();
});
onMounted(async () => {
  await loadLatest();
});
</script>

<style scoped>
.workspace{display:flex;flex-direction:column;gap:14px}.headline{display:flex;align-items:flex-start;justify-content:space-between;padding:22px;border:1px solid #dfe7f0;border-radius:14px;background:#fff}.headline span{color:#2470d5;font-size:13px;font-weight:700}.headline h2{margin:6px 0;color:#1b3b60}.headline p{max-width:980px;margin:0;color:#6d7e91;line-height:1.65}.provenance{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid #dfe7f0;border-radius:12px;background:#fff}.provenance div{padding:14px 16px;border-right:1px solid #edf1f5}.provenance div:last-child{border-right:0}.provenance b,.provenance span{display:block}.provenance b{color:#185ca5}.provenance span{margin-top:4px;color:#8491a2;font-size:12px}
.guidance,.running-card{padding:40px;border:1px solid #dfe7f0;border-radius:14px;background:#fff;text-align:center}.guidance h2,.guidance h3,.running-card h3{color:#1c416a}.guidance p,.running-card li{color:#718196;line-height:1.7}.element-actions{display:flex;justify-content:center;gap:10px}.running-card ol{max-width:760px;margin:22px auto 0;text-align:left}.failure{border-color:#fecaca;background:#fff7f7}.validation{display:flex;align-items:center;justify-content:space-between;padding:18px 22px;border-left:5px solid #20a273;border-radius:12px;background:#f0fbf7}.validation.limited{border-left-color:#e9972e;background:#fff8ed}.validation span{color:#258364;font-size:12px;font-weight:700}.validation.limited span{color:#b66e19}.validation h3{margin:5px 0;color:#244565}.validation p{margin:0;color:#6f8194}.validation-numbers{display:grid;grid-template-columns:auto auto;gap:3px 14px;min-width:180px}.validation-numbers b{color:#1d5f9e;font-size:20px}.validation-numbers span{color:#8492a3}
.element-switcher{display:grid;grid-template-columns:minmax(320px,.9fr) minmax(520px,1.1fr);gap:24px;padding:18px 22px;border:1px solid #cfe0f1;border-radius:14px;background:linear-gradient(120deg,#f5faff,#f8fcfb)}.switcher-copy>span{color:#1f70c4;font-size:12px;font-weight:800}.switcher-copy h3{margin:5px 0;color:#1d446b}.switcher-copy p{margin:0;color:#667b91;font-size:13px;line-height:1.55}.switcher-actions{display:flex;flex-direction:column;justify-content:center;gap:12px}.member-buttons,.all-element-picker{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.member-buttons b,.all-element-picker b{width:92px;color:#395a79;font-size:12px}.all-element-picker small{color:#7d8d9f}
.scene-loading{padding:28px 18px;border:1px solid #dfe7f0;border-radius:14px;background:#fff}.scene-loading p{margin:12px 0 0;color:#64748b;text-align:center}
.scene-explanation{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.scene-explanation article{padding:16px;border:1px solid #dfe7f0;border-radius:10px;background:#fff}.scene-explanation b{color:#244969}.scene-explanation p{margin:6px 0 0;color:#718397;font-size:13px;line-height:1.55}@media(max-width:1050px){.provenance{grid-template-columns:repeat(2,1fr)}.scene-explanation{grid-template-columns:1fr}.validation{align-items:flex-start;gap:16px;flex-direction:column}.element-switcher{grid-template-columns:1fr}}
</style>
