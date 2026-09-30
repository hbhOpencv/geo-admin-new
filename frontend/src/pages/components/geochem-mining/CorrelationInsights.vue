<template>
  <div class="correlation-insights" v-loading="loading">
    <section class="answer-banner">
      <div>
        <span class="kicker">元素相关性自动解读</span>
        <h3>{{ automaticConclusion }}</h3>
        <p>相关性不是“相关系数排行榜”：系统同时检查共同正异常、Fisher/BH 显著性、同孔同深度事件和跨孔重复。</p>
      </div>
      <div class="answer-stats">
        <span><b>{{ allPairClues.length }}</b>全量元素对</span>
        <span><b>{{ clusterClues.length }}</b>多元素簇</span>
        <span><b>{{ targetPairs.length }}</b>{{ targetElement }} 直接关系</span>
      </div>
    </section>

    <section class="panel relation-map">
      <header class="section-head">
        <div>
          <span class="step">01 · 先看“谁与目标元素共同变化”</span>
          <h3>{{ targetElement }} 关联网络</h3>
          <p>中心元素来自上一步变化规律线索；外围元素按空间证据、共同异常和显著性排序。可切换任意元素，不限制为固定几个。</p>
        </div>
        <el-select v-model="targetElement" filterable placeholder="切换目标元素" style="width: 180px">
          <el-option v-for="element in availableElements" :key="element" :label="element" :value="element" />
        </el-select>
      </header>

      <div class="relation-grid">
        <div class="network-wrap">
          <svg viewBox="0 0 560 330" role="img" aria-label="目标元素关联网络">
            <defs>
              <linearGradient id="edgeGradient" x1="0" x2="1">
                <stop offset="0%" stop-color="#a9c9ed" />
                <stop offset="100%" stop-color="#2d78d4" />
              </linearGradient>
            </defs>
            <line
              v-for="node in networkNodes"
              :key="`edge-${node.element}`"
              x1="280"
              y1="165"
              :x2="node.x"
              :y2="node.y"
              stroke="url(#edgeGradient)"
              :stroke-width="Math.max(2, node.weight * 5)"
              :stroke-dasharray="node.crossHole > 0 ? '0' : '6 5'"
            />
            <g
              v-for="node in networkNodes"
              :key="node.element"
              class="network-node"
              @click="selectPair(node.clue)"
            >
              <circle :cx="node.x" :cy="node.y" :r="24 + node.weight * 9" :fill="tierColor(node.tier)" />
              <text :x="node.x" :y="node.y + 5" text-anchor="middle">{{ node.element }}</text>
              <text :x="node.x" :y="node.y + 48" text-anchor="middle" class="node-caption">{{ node.sameHole }} 同孔</text>
            </g>
            <circle cx="280" cy="165" r="48" fill="#155fb7" />
            <text x="280" y="172" text-anchor="middle" class="center-label">{{ targetElement }}</text>
          </svg>
          <div class="network-legend">
            <span><i class="tier-a" />A级：跨孔或高等级空间证据</span>
            <span><i class="tier-b" />B级：同孔共同异常</span>
            <span><i class="tier-c" />C级：统计关联待复核</span>
            <span>虚线表示尚无跨孔重复</span>
          </div>
        </div>

        <div class="pair-ranking">
          <h4>优先关系证据</h4>
          <button
            v-for="clue in targetPairs.slice(0, 7)"
            :key="clue.id"
            type="button"
            :class="{ active: selectedPair?.id === clue.id }"
            @click="selectPair(clue)"
          >
            <span class="pair-name">{{ clue.members.join(" + ") }}</span>
            <span class="tier" :style="{ color: tierColor(evidenceTier(clue)) }">{{ evidenceTier(clue) }}级</span>
            <small>共同异常 {{ clue.evidence?.co_anomaly_count ?? 0 }} · 同孔 {{ clue.same_hole_count }} · 跨孔 {{ clue.cross_hole_count }}</small>
            <span class="pair-metrics">Lift {{ format(clue.evidence?.lift) }} · q {{ probability(clue.evidence?.bh_q) }}</span>
          </button>
          <el-empty v-if="!targetPairs.length" description="该元素没有通过当前证据门槛的关系" :image-size="70" />
        </div>
      </div>
    </section>

    <section class="evidence-grid">
      <article class="panel matrix-panel">
        <header class="section-head compact">
          <div>
            <span class="step">02 · 看空间证据，而不是只看一个 r 值</span>
            <h3>{{ selectedPairName }} 的钻孔共同异常矩阵</h3>
            <p>颜色越深表示同一钻孔中共同异常事件越多；矩阵回答“是否在多个钻孔重复出现”。</p>
          </div>
        </header>
        <div class="hole-matrix">
          <div class="matrix-head"><span>钻孔</span><b>共同事件</b><b>空间判断</b></div>
          <div v-for="row in holeMatrix" :key="row.holeId" class="matrix-row">
            <span>{{ shortHole(row.holeId) }}</span>
            <i :style="{ background: matrixColor(row.intensity) }"><b>{{ row.count }}</b></i>
            <em>{{ row.count ? "同孔同深度支持" : "未检出重复" }}</em>
          </div>
        </div>
      </article>

      <article class="panel aligned-panel">
        <header class="section-head compact">
          <div>
            <span class="step">03 · 直接看两元素是否在同一深度重叠</span>
            <h3>{{ selectedSpatialEvent?.hole_id ? shortHole(selectedSpatialEvent.hole_id) : "选择共同异常事件" }}</h3>
            <p>两条色带位置接近或重叠，才构成同孔深度证据。</p>
          </div>
        </header>
        <div v-if="selectedSpatialEvent" class="aligned-depth">
          <div class="depth-scale">
            <span v-for="tick in spatialTicks" :key="tick">{{ tick }} m</span>
          </div>
          <div class="depth-row">
            <b>{{ selectedPair?.members?.[0] }}</b>
            <span class="depth-track">
              <i class="target" :style="spatialBarStyle(selectedSpatialEvent, 'target')" />
            </span>
          </div>
          <div class="depth-row">
            <b>{{ selectedPair?.members?.[1] }}</b>
            <span class="depth-track">
              <i class="candidate" :style="spatialBarStyle(selectedSpatialEvent, 'candidate')" />
            </span>
          </div>
          <div class="spatial-verdict">
            <b>重叠 {{ format(selectedSpatialEvent.overlap_length_m) }} m</b>
            <span>间隔 {{ format(selectedSpatialEvent.gap_m) }} m · {{ selectedSpatialEvent.same_section_geobody ? "位于同一地质分段" : "地质分段待复核" }}</span>
          </div>
        </div>
        <el-empty v-else description="当前组合没有读取到同孔深度事件" :image-size="70" />
      </article>

      <article class="panel evidence-score">
        <header class="section-head compact">
          <div>
            <span class="step">04 · 统计与空间证据合并判断</span>
            <h3>为什么它只能作为找矿参考</h3>
          </div>
        </header>
        <dl v-if="selectedPair">
          <div><dt>共同正异常</dt><dd>{{ selectedPair.evidence?.co_anomaly_count ?? 0 }}</dd></div>
          <div><dt>共同异常提升 Lift</dt><dd>{{ format(selectedPair.evidence?.lift) }}</dd></div>
          <div><dt>BH 多重校正 q</dt><dd>{{ probability(selectedPair.evidence?.bh_q) }}</dd></div>
          <div><dt>同孔事件</dt><dd>{{ selectedPair.same_hole_count }}</dd></div>
          <div><dt>跨孔重复</dt><dd>{{ selectedPair.cross_hole_count }}</dd></div>
          <div><dt>综合等级</dt><dd :style="{ color: tierColor(evidenceTier(selectedPair)) }">{{ evidenceTier(selectedPair) }} 级</dd></div>
        </dl>
        <p class="limitation">{{ selectedPair?.evidence?.evidence_reason || "相关性表示共同变化或共同异常，不直接证明成矿因果。" }}</p>
        <div v-if="selectedPair" class="evidence-actions">
          <el-button @click="$emit('open-evidence', selectedPair)">查看完整证据</el-button>
          <el-dropdown trigger="click" @command="(element) => $emit('start-3d', selectedPair, element)">
            <el-button type="primary">选择单元素做三维验证</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="element in selectedPair.members" :key="element" :command="element">
                  验证 {{ element }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </article>
    </section>

    <section class="panel cluster-section">
      <header class="section-head">
        <div>
          <span class="step">05 · 从二元证据边汇总为多元素组合</span>
          <h3>与 {{ targetElement }} 有关的 R 型多元素簇</h3>
          <p>二元关系是证据边，多元素簇才是组合解释；系统不会把不同元素的 ppm 直接相加。</p>
        </div>
        <el-button @click="openAllResults">查看全部 {{ allPairClues.length }} 个元素对</el-button>
      </header>
      <div class="cluster-cards">
        <article v-for="cluster in targetClusters.slice(0, 4)" :key="cluster.id">
          <span class="cluster-icon">{{ cluster.members.length }}</span>
          <h4>{{ cluster.members.join(" + ") }}</h4>
          <p>{{ cluster.evidence?.supporting_drillhole_count || 0 }} 个支持钻孔 · 稳定元素对 {{ percent(cluster.evidence?.stable_pair_ratio) }}</p>
          <small>{{ cluster.evidence?.confidence_level || "待空间复核" }} · 仅作为组合线索</small>
        </article>
      </div>
      <el-empty v-if="!targetClusters.length" description="当前目标元素未进入多元素聚类" :image-size="70" />
    </section>

    <el-dialog v-model="allVisible" title="全部相关性计算结果" width="88%">
      <div class="all-toolbar">
        <el-radio-group v-model="resultMode">
          <el-radio-button value="target">目标直接关系 {{ targetPairs.length }}</el-radio-button>
          <el-radio-button value="clusters">R 型聚类 {{ clusterClues.length }}</el-radio-button>
          <el-radio-button value="all">全量元素对 {{ allPairClues.length }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="searchText" clearable placeholder="搜索元素" style="width: 200px" />
      </div>
      <el-table :data="pagedResults" height="520" stripe>
        <el-table-column label="元素关系" min-width="180">
          <template #default="{ row }"><b>{{ row.members.join(" + ") }}</b></template>
        </el-table-column>
        <el-table-column label="共同异常" min-width="100">
          <template #default="{ row }">{{ row.evidence?.co_anomaly_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="Lift" min-width="90">
          <template #default="{ row }">{{ format(row.evidence?.lift) }}</template>
        </el-table-column>
        <el-table-column label="BH q" min-width="110">
          <template #default="{ row }">{{ probability(row.evidence?.bh_q) }}</template>
        </el-table-column>
        <el-table-column prop="same_hole_count" label="同孔事件" width="100" />
        <el-table-column prop="cross_hole_count" label="跨孔重复" width="100" />
        <el-table-column label="证据等级" width="100">
          <template #default="{ row }">{{ evidenceTier(row) }} 级</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="selectFromDialog(row)">看空间证据</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="resultPage"
        class="result-pagination"
        :page-size="20"
        layout="total, prev, pager, next"
        :total="filteredResults.length"
      />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { http } from "../../../api/http";

const props = defineProps<{ workflow: any }>();
defineEmits<{
  (event: "open-evidence", clue: any): void;
  (event: "start-3d", clue: any, element: string): void;
}>();

const loading = ref(false);
const allClues = ref<any[]>([]);
const spatialEvents = ref<any[]>([]);
const allHoleIds = ref<string[]>([]);
const targetElement = ref("");
const selectedPair = ref<any>(null);
const selectedSpatialEvent = ref<any>(null);
const allVisible = ref(false);
const resultMode = ref<"target" | "clusters" | "all">("target");
const resultPage = ref(1);
const searchText = ref("");

const clusterClues = computed(() => allClues.value.filter(isCluster));
const allPairClues = computed(() => allClues.value.filter((clue) => !isCluster(clue)));
const availableElements = computed(() => Array.from(new Set(
  allPairClues.value.flatMap((clue) => clue.members || []),
)).sort());
const targetPairs = computed(() => allPairClues.value
  .filter((clue) => clue.members?.includes(targetElement.value))
  .sort(compareClues));
const targetClusters = computed(() => clusterClues.value
  .filter((clue) => clue.members?.includes(targetElement.value))
  .sort(compareClues));
const networkNodes = computed(() => targetPairs.value.slice(0, 8).map((clue, index, source) => {
  const angle = (-Math.PI / 2) + (index * Math.PI * 2) / Math.max(1, source.length);
  const element = clue.members.find((member: string) => member !== targetElement.value) || clue.main_element;
  return {
    clue,
    element,
    x: 280 + Math.cos(angle) * 185,
    y: 165 + Math.sin(angle) * 118,
    weight: Math.min(1, Math.log10(1 + Number(clue.evidence?.lift || 0)) / 1.2),
    sameHole: clue.same_hole_count || 0,
    crossHole: clue.cross_hole_count || 0,
    tier: evidenceTier(clue),
  };
}));
const selectedPairName = computed(() => selectedPair.value?.members?.join(" + ") || "请选择一个元素关系");
const holeMatrix = computed(() => {
  const counts = new Map<string, number>();
  for (const item of spatialEvents.value) {
    const holeId = String(item.hole_id || "");
    counts.set(holeId, (counts.get(holeId) || 0) + 1);
  }
  const max = Math.max(1, ...counts.values());
  return allHoleIds.value.map((holeId) => ({
    holeId,
    count: counts.get(holeId) || 0,
    intensity: (counts.get(holeId) || 0) / max,
  })).sort((left, right) => right.count - left.count);
});
const spatialRange = computed(() => {
  if (!selectedSpatialEvent.value) return { min: 0, max: 1 };
  const values = [
    selectedSpatialEvent.value.target_segment_from,
    selectedSpatialEvent.value.target_segment_to,
    selectedSpatialEvent.value.candidate_segment_from,
    selectedSpatialEvent.value.candidate_segment_to,
  ].map(Number).filter(Number.isFinite);
  const min = Math.floor(Math.min(...values) / 10) * 10;
  const max = Math.ceil(Math.max(...values) / 10) * 10 || min + 10;
  return { min, max: max === min ? min + 10 : max };
});
const spatialTicks = computed(() => Array.from({ length: 5 }, (_, index) => (
  spatialRange.value.min + ((spatialRange.value.max - spatialRange.value.min) * index) / 4
).toFixed(0)));
const automaticConclusion = computed(() => {
  const first = targetPairs.value[0];
  if (!first) return `当前没有找到与 ${targetElement.value || "目标元素"} 同时满足统计和空间门槛的关系。`;
  const partner = first.members.find((member: string) => member !== targetElement.value);
  return `${targetElement.value} 与 ${partner} 为当前优先关系：共同异常 ${first.evidence?.co_anomaly_count || 0} 个，`
    + `同孔事件 ${first.same_hole_count || 0} 个，跨孔重复 ${first.cross_hole_count || 0} 个，评为 ${evidenceTier(first)} 级参考。`;
});
const baseResults = computed(() => resultMode.value === "target"
  ? targetPairs.value
  : resultMode.value === "clusters"
    ? clusterClues.value
    : allPairClues.value);
const filteredResults = computed(() => {
  const query = searchText.value.trim().toLowerCase();
  return query
    ? baseResults.value.filter((clue) => clue.members?.some((member: string) => member.toLowerCase().includes(query)))
    : baseResults.value;
});
const pagedResults = computed(() => filteredResults.value.slice((resultPage.value - 1) * 20, resultPage.value * 20));

async function fetchAllClues() {
  const result: any[] = [];
  let page = 1;
  let total = 1;
  while (result.length < total) {
    const response = await http.get(`/api/geochem-workflows/${props.workflow.id}/clues`, {
      params: { kind: "correlation", page, page_size: 1000 },
    });
    result.push(...(response.data.items || []));
    total = Number(response.data.total || 0);
    if (!(response.data.items || []).length) break;
    page += 1;
  }
  allClues.value = result;
}

async function loadResults() {
  if (!props.workflow?.correlation_job_id) return;
  loading.value = true;
  try {
    const [, enrichment] = await Promise.all([
      fetchAllClues(),
      props.workflow?.variation_job_id
        ? http.get(`/api/geochem-mining/jobs/${props.workflow.variation_job_id}/variation/enrichment`, {
          params: { group: "hole" },
        })
        : Promise.resolve({ data: { items: [] } }),
    ]);
    allHoleIds.value = Array.from(new Set(
      (enrichment.data.items || []).map((row: any) => String(row.hole_id || "")).filter(Boolean),
    )).sort();
    const source = allPairClues.value.find((clue) => clue.professional_evidence?.source_focus_element)
      ?.professional_evidence?.source_focus_element;
    targetElement.value = targetElement.value || source || (availableElements.value.includes("Cu") ? "Cu" : availableElements.value[0]) || "";
  } finally {
    loading.value = false;
  }
}

async function selectPair(clue: any) {
  selectedPair.value = clue;
  spatialEvents.value = [];
  selectedSpatialEvent.value = null;
  const [elementA, elementB] = clue.members || [];
  if (!elementA || !elementB || !props.workflow?.correlation_job_id) return;
  loading.value = true;
  try {
    const response = await http.get(
      `/api/geochem-mining/jobs/${props.workflow.correlation_job_id}/correlation/spatial`,
      { params: { scale: "same_hole", page: 1, page_size: 500, element_a: elementA, element_b: elementB } },
    );
    spatialEvents.value = response.data.items || [];
    selectedSpatialEvent.value = spatialEvents.value[0] || null;
  } finally {
    loading.value = false;
  }
}

function selectFromDialog(clue: any) {
  allVisible.value = false;
  void selectPair(clue);
}

function openAllResults() {
  resultMode.value = "all";
  searchText.value = "";
  resultPage.value = 1;
  allVisible.value = true;
}

watch(() => props.workflow?.correlation_job_id, () => void loadResults(), { immediate: true });
watch(targetElement, () => {
  resultPage.value = 1;
  if (targetPairs.value[0]) void selectPair(targetPairs.value[0]);
});
watch([resultMode, searchText], () => { resultPage.value = 1; });

function isCluster(clue: any) {
  return clue?.professional_evidence?.association_type === "r_type_multielement_cluster"
    || clue?.evidence?.association_type === "r_type_multielement_cluster";
}
function matrixColor(intensity: number) {
  return `rgba(107, 77, 192, ${0.13 + Math.max(0, Math.min(1, intensity)) * 0.87})`;
}
function evidenceTier(clue: any) {
  return clue?.evidence?.evidence_tier || clue?.professional_evidence?.evidence_tier
    || (clue?.cross_hole_count > 0 ? "A" : clue?.same_hole_count > 0 ? "B" : "C");
}
function compareClues(left: any, right: any) {
  const tierScore: Record<string, number> = { A: 3, B: 2, C: 1 };
  return (tierScore[evidenceTier(right)] - tierScore[evidenceTier(left)])
    || ((right.cross_hole_count || 0) - (left.cross_hole_count || 0))
    || ((right.same_hole_count || 0) - (left.same_hole_count || 0))
    || ((right.evidence?.lift || 0) - (left.evidence?.lift || 0));
}
function spatialBarStyle(event: any, side: "target" | "candidate") {
  const from = Number(event[`${side}_segment_from`]);
  const to = Number(event[`${side}_segment_to`]);
  const span = spatialRange.value.max - spatialRange.value.min;
  return {
    left: `${((from - spatialRange.value.min) / span) * 100}%`,
    width: `${Math.max(2, ((to - from) / span) * 100)}%`,
  };
}
function tierColor(tier: string) {
  return ({ A: "#1F9D73", B: "#F39C35", C: "#8EA0B5" } as Record<string, string>)[tier] || "#8EA0B5";
}
function shortHole(holeId: string) { return String(holeId || "—").replace("DZ-德达-深-", ""); }
function format(value: any) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed >= 100 ? parsed.toFixed(1) : parsed.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}
function probability(value: any) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toExponential(2) : "—";
}
function percent(value: any) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(0)}%` : "—";
}
</script>

<style scoped>
.correlation-insights{display:flex;flex-direction:column;gap:16px}.panel{padding:22px;border:1px solid #dce7f2;border-radius:18px;background:#fff;box-shadow:0 10px 32px rgba(28,67,111,.06)}.answer-banner{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:24px 28px;border-radius:18px;background:linear-gradient(135deg,#153c68,#315ca8 58%,#6b4dc0);color:#fff;box-shadow:0 14px 34px rgba(38,82,148,.22)}.answer-banner h3{max-width:900px;margin:6px 0;font-size:23px;line-height:1.45}.answer-banner p{margin:0;color:#dce8fa}.kicker,.step{font-size:12px;font-weight:800;letter-spacing:.06em}.kicker{color:#b9d6ff}.answer-stats{display:flex;gap:10px}.answer-stats span{min-width:112px;padding:13px;border:1px solid rgba(255,255,255,.2);border-radius:12px;background:rgba(255,255,255,.09);text-align:center}.answer-stats b{display:block;font-size:24px}.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.section-head.compact{margin-bottom:14px}.section-head h3{margin:5px 0;color:#173a61;font-size:21px}.section-head p{max-width:850px;margin:0;color:#718298;line-height:1.6}.step{color:#2470d5}.relation-grid{display:grid;grid-template-columns:minmax(520px,1.25fr) minmax(330px,.75fr);gap:20px;margin-top:12px}.network-wrap{padding:12px;border-radius:15px;background:radial-gradient(circle at center,#f7fbff,#eef4fb)}.network-node{cursor:pointer}.network-node text{fill:#fff;font-size:15px;font-weight:800;pointer-events:none}.network-node .node-caption{fill:#61758d;font-size:10px;font-weight:600}.center-label{fill:#fff;font-size:23px;font-weight:900}.network-legend{display:flex;gap:14px;flex-wrap:wrap;padding:8px 10px;color:#64768b;font-size:11px}.network-legend span{display:flex;align-items:center;gap:5px}.network-legend i{width:10px;height:10px;border-radius:50%}.tier-a{background:#1f9d73}.tier-b{background:#f39c35}.tier-c{background:#8ea0b5}.pair-ranking{display:flex;flex-direction:column;gap:8px}.pair-ranking h4{margin:0 0 4px;color:#244767}.pair-ranking button{display:grid;grid-template-columns:1fr auto;gap:4px 10px;padding:11px 13px;border:1px solid #e1e9f2;border-radius:11px;background:#fbfdff;text-align:left;cursor:pointer}.pair-ranking button:hover,.pair-ranking button.active{border-color:#4a89dc;background:#f0f7ff;box-shadow:0 6px 16px rgba(42,105,184,.1)}.pair-name{color:#234967;font-size:16px;font-weight:800}.tier{font-weight:800}.pair-ranking small{color:#77889b}.pair-metrics{color:#305f90;font-size:12px}.evidence-grid{display:grid;grid-template-columns:1fr 1fr .8fr;gap:16px}.hole-matrix{display:flex;flex-direction:column;gap:7px}.matrix-head,.matrix-row{display:grid;grid-template-columns:1fr 100px 1.2fr;gap:10px;align-items:center}.matrix-head{padding:0 10px;color:#8795a6;font-size:11px}.matrix-row{padding:8px 10px;border-radius:9px;background:#f7f9fc;color:#405f7d}.matrix-row i{display:grid;height:34px;place-items:center;border-radius:8px;background:color-mix(in srgb,#6b4dc0 calc(var(--intensity) * 100%),#edf1f6);color:#fff;font-style:normal}.matrix-row em{color:#718399;font-size:11px;font-style:normal}.aligned-depth{padding:8px}.depth-scale{display:flex;justify-content:space-between;margin:0 0 8px 64px;color:#8a99aa;font-size:10px}.depth-row{display:grid;grid-template-columns:48px 1fr;gap:12px;align-items:center;margin:15px 0}.depth-row b{color:#345576}.depth-track{position:relative;height:28px;border-radius:8px;background:repeating-linear-gradient(90deg,#eef3f8 0,#eef3f8 19%,#dfe7f0 20%)}.depth-track i{position:absolute;top:4px;height:20px;border-radius:6px}.depth-track .target{background:#2f7de1}.depth-track .candidate{background:#ed6c35}.spatial-verdict{display:flex;justify-content:space-between;gap:10px;padding:13px;border-radius:10px;background:#edf8f4;color:#3f6f62}.spatial-verdict span{font-size:12px}.evidence-score dl{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:0}.evidence-score dl div{padding:10px;border-radius:9px;background:#f4f7fb}.evidence-score dt{color:#7a899b;font-size:11px}.evidence-score dd{margin:4px 0 0;color:#205d9f;font-size:19px;font-weight:800}.limitation{padding:12px;border-left:3px solid #f1a437;border-radius:8px;background:#fff8eb;color:#8a672d;font-size:12px;line-height:1.6}.evidence-actions{display:flex;gap:8px}.cluster-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}.cluster-cards article{position:relative;padding:16px;border:1px solid #e0e8f1;border-radius:13px;background:linear-gradient(145deg,#fbfdff,#f2f6fb)}.cluster-icon{display:grid;width:34px;height:34px;place-items:center;border-radius:10px;background:#e7e0ff;color:#6041b4;font-weight:900}.cluster-cards h4{margin:12px 0 6px;color:#274766}.cluster-cards p{margin:0;color:#63788f;font-size:12px}.cluster-cards small{display:block;margin-top:9px;color:#8a97a7}.all-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.result-pagination{justify-content:flex-end;margin-top:14px}@media(max-width:1500px){.evidence-grid{grid-template-columns:1fr 1fr}.evidence-score{grid-column:1/-1}.cluster-cards{grid-template-columns:repeat(2,1fr)}}@media(max-width:1100px){.answer-banner,.section-head{flex-direction:column}.answer-stats{width:100%}.answer-stats span{flex:1}.relation-grid,.evidence-grid{grid-template-columns:1fr}.network-wrap{min-width:0}.evidence-score{grid-column:auto}.cluster-cards{grid-template-columns:1fr}}
</style>
