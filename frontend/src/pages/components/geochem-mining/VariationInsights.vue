<template>
  <div class="variation-insights" v-loading="loading">
    <section class="answer-banner">
      <div>
        <span class="kicker">变化规律自动解读</span>
        <h3>{{ automaticConclusion }}</h3>
        <p>先比较全部元素，再自动展开重点元素在 7 个化验钻孔中的深度变化；无需反复手工选择元素和钻孔。</p>
      </div>
      <div class="answer-stats">
        <span><b>{{ elementSummaries.length }}</b>参与比较元素</span>
        <span><b>{{ allClues.length }}</b>正异常孔段</span>
        <span><b>{{ selectedProfiles.length }}</b>已对齐钻孔</span>
      </div>
    </section>

    <section class="element-overview panel">
      <header class="section-head">
        <div>
          <span class="step">01 · 先回答“什么元素值得看”</span>
          <h3>全部元素异常强度与空间支持</h3>
          <p>卡片按元素角色、专业品位证据、异常强度和支持钻孔数综合排序，不再把 2,000 多条孔段直接堆给用户。</p>
        </div>
        <el-select v-model="selectedElement" filterable placeholder="查看任意元素" style="width: 180px">
          <el-option
            v-for="item in elementSummaries"
            :key="item.element"
            :label="`${item.element} · ${item.segmentCount} 段`"
            :value="item.element"
          />
        </el-select>
      </header>

      <div class="element-strip">
        <button
          v-for="item in visibleElementCards"
          :key="item.element"
          class="element-card"
          :class="{ active: selectedElement === item.element }"
          type="button"
          @click="selectedElement = item.element"
        >
          <span class="element-symbol">{{ item.element }}</span>
          <span class="role" :class="item.role">{{ roleText(item.role) }}</span>
          <b>{{ item.segmentCount }} 个异常段</b>
          <small>{{ item.holeCount }}/{{ holeCount }} 孔出现</small>
          <small>{{ ratioExplanation(item.maxValue, item.backgroundValue, item.maxRatio) }}</small>
          <span class="strength-track"><i :style="{ width: `${item.strength}%` }" /></span>
        </button>
      </div>
      <aside class="background-explainer">
        <b>背景值是什么</b>
        <span>{{ backgroundDefinition }}</span>
      </aside>
      <div class="legend-row">
        <span><i class="swatch background" />背景范围</span>
        <span><i class="swatch enriched" />相对富集</span>
        <span><i class="swatch positive" />明显正异常</span>
        <span><i class="swatch strong" />强正异常</span>
        <span><i class="swatch boundary" />边界品位</span>
        <span><i class="swatch industrial" />最低工业品位</span>
      </div>
    </section>

    <section class="detail-grid">
      <article class="panel borehole-panel">
        <header class="section-head compact">
          <div>
            <span class="step">02 · 再回答“去哪个孔、哪个深度”</span>
            <h3>{{ selectedElement }} 的七孔纵向异常带</h3>
            <p>所有钻孔共用同一深度轴；有颜色表示该深度存在真实化验记录，透明位置不是“元素为零”。</p>
          </div>
        </header>

        <div v-if="selectedProfiles.length" class="borehole-bands">
          <div class="depth-axis">
            <span v-for="tick in depthTicks" :key="tick" :style="{ top: `${(tick / maxDepth) * 100}%` }">{{ tick }} m</span>
          </div>
          <button
            v-for="profile in selectedProfiles"
            :key="profile.holeId"
            type="button"
            class="borehole-column"
            :class="{ active: selectedHole === profile.holeId }"
            @click="selectedHole = profile.holeId"
          >
            <b>{{ shortHole(profile.holeId) }}</b>
            <span class="hole-track">
              <i
                v-for="point in anomalyPoints(profile.points)"
                :key="point.assay_id"
                class="interval"
                :title="pointEvidenceText(point)"
                :style="intervalStyle(point)"
              />
            </span>
            <small>{{ anomalyPoints(profile.points).length }} 段</small>
          </button>
        </div>
        <el-empty v-else description="当前元素没有可读取的钻孔剖面" :image-size="70" />
      </article>

      <article class="panel profile-panel">
        <header class="section-head compact">
          <div>
            <span class="step">03 · 查看真实浓度如何随深度变化</span>
            <h3>{{ shortHole(selectedHole) }} 浓度—深度曲线</h3>
            <p>曲线、背景值、统计阈值和品位阈值来自同一次后端任务。</p>
          </div>
        </header>
        <DrillholeProfileChart
          v-if="selectedProfilePoints.length"
          :points="selectedProfilePoints"
          :element="selectedElement"
        />
        <el-empty v-else description="请选择有数据的钻孔" :image-size="70" />
        <div class="profile-summary">
          <span><b>{{ selectedProfilePoints.length }}</b>化验区间</span>
          <span><b>{{ selectedAnomalyCount }}</b>正异常区间</span>
          <span><b>{{ selectedBoundaryCount }}</b>达到边界品位</span>
        </div>
      </article>
    </section>

    <section class="panel evidence-list">
      <header class="section-head compact">
        <div>
          <span class="step">04 · 把规律落实为可操作线索</span>
          <h3>{{ selectedElement }} 最值得复核的异常孔段</h3>
          <p>点击证据可查看阈值来源和原始孔段；点击相关性分析会把当前元素作为主线索，但后端仍搜索全部合格元素。</p>
        </div>
      </header>
      <div class="segment-list">
        <article v-for="clue in selectedElementClues.slice(0, 8)" :key="clue.id">
          <span class="level-dot" :style="{ background: levelColor(clue.highest_level) }" />
          <div>
            <b>{{ firstHole(clue) }} · {{ depthRange(clue) }}</b>
            <small>{{ levelText(clue.highest_level) }} · {{ clueRatioExplanation(clue) }}</small>
            <small>{{ clueGradeText(clue) }}</small>
          </div>
          <span class="support">{{ clue.hole_ids?.length || 1 }} 孔支持</span>
          <el-button @click="$emit('open-evidence', clue)">查看证据</el-button>
          <el-button type="primary" @click="$emit('analyze-correlation', clue)">查找相关元素</el-button>
        </article>
      </div>
      <el-empty v-if="!selectedElementClues.length" description="该元素没有进入优先异常线索" :image-size="70" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { http } from "../../../api/http";
import DrillholeProfileChart from "./DrillholeProfileChart.vue";

const props = defineProps<{ workflow: any }>();
defineEmits<{
  (event: "open-evidence", clue: any): void;
  (event: "analyze-correlation", clue: any): void;
}>();

const loading = ref(false);
const allClues = ref<any[]>([]);
const enrichmentRows = ref<any[]>([]);
const profiles = ref<Record<string, any[]>>({});
const selectedElement = ref("");
const selectedHole = ref("");

const holeIds = computed(() => Array.from(new Set(
  enrichmentRows.value.map((row) => String(row.hole_id || "")).filter(Boolean),
)).sort());
const holeCount = computed(() => props.workflow?.data_scope?.chemical_drillhole_count || holeIds.value.length);
const selectedProfiles = computed(() => holeIds.value.map((holeId) => ({
  holeId,
  points: profiles.value[holeId] || [],
})));
const selectedProfilePoints = computed(() => profiles.value[selectedHole.value] || []);
const selectedElementClues = computed(() => allClues.value.filter((item) => item.main_element === selectedElement.value));
const maxDepth = computed(() => Math.max(
  1,
  ...selectedProfiles.value.flatMap((profile) => profile.points.map((point) => Number(point.to_depth) || 0)),
));
const depthTicks = computed(() => {
  const step = Math.max(100, Math.ceil(maxDepth.value / 5 / 100) * 100);
  return Array.from({ length: Math.floor(maxDepth.value / step) + 1 }, (_, index) => index * step);
});
const selectedAnomalyCount = computed(() => anomalyPoints(selectedProfilePoints.value).length);
const selectedBoundaryCount = computed(() => selectedProfilePoints.value.filter((point) => point.meets_boundary_grade).length);

const elementSummaries = computed(() => {
  const byElement = new Map<string, any>();
  for (const row of enrichmentRows.value) {
    const element = String(row.element || "");
    if (!element) continue;
    const current = byElement.get(element) || {
      element,
      holes: new Set<string>(),
      maxRatio: 0,
      role: "other",
      segmentCount: 0,
      boundaryCount: 0,
      maxValue: null,
      backgroundValue: null,
    };
    current.holes.add(String(row.hole_id || ""));
    const rowRatio = Number(row.relative_background_ratio);
    if (Number.isFinite(rowRatio) && rowRatio >= current.maxRatio) {
      current.maxRatio = rowRatio;
      current.maxValue = finiteOrNull(row.group_mean_positive);
      current.backgroundValue = finiteOrNull(row.project_background_mean);
    }
    byElement.set(element, current);
  }
  for (const clue of allClues.value) {
    const element = String(clue.main_element || "");
    const current = byElement.get(element) || {
      element,
      holes: new Set<string>(),
      maxRatio: 0,
      role: "other",
      segmentCount: 0,
      boundaryCount: 0,
      maxValue: null,
      backgroundValue: null,
    };
    current.segmentCount += 1;
    const clueRatio = Number(clue.max_background_ratio);
    if (Number.isFinite(clueRatio) && clueRatio >= current.maxRatio) {
      const peak = finiteOrNull(clue.evidence?.max_value);
      current.maxRatio = clueRatio;
      current.maxValue = peak;
      current.backgroundValue = peak != null && clueRatio > 0 ? peak / clueRatio : null;
    }
    current.role = clue.professional_evidence?.element_role || current.role;
    for (const hole of clue.hole_ids || []) current.holes.add(String(hole));
    if (String(clue.highest_level || "").includes("boundary")) current.boundaryCount += 1;
    byElement.set(element, current);
  }
  const items = Array.from(byElement.values()).map((item) => ({
    ...item,
    holeCount: Array.from(item.holes).filter(Boolean).length,
  }));
  const maxRatio = Math.max(1, ...items.map((item) => Math.log10(1 + item.maxRatio)));
  return items.map((item) => ({
    ...item,
    strength: Math.max(5, Math.min(100, (Math.log10(1 + item.maxRatio) / maxRatio) * 100)),
  })).sort((left, right) => {
    const roleScore: Record<string, number> = { metal: 3, indicator: 2, other: 1 };
    return (right.boundaryCount - left.boundaryCount)
      || ((roleScore[right.role] || 0) - (roleScore[left.role] || 0))
      || (right.holeCount - left.holeCount)
      || (right.maxRatio - left.maxRatio);
  });
});
const visibleElementCards = computed(() => {
  const selected = elementSummaries.value.find((item) => item.element === selectedElement.value);
  const leaders = elementSummaries.value.slice(0, 11);
  return selected && !leaders.some((item) => item.element === selected.element)
    ? [...leaders.slice(0, 10), selected]
    : leaders;
});
const automaticConclusion = computed(() => {
  const item = elementSummaries.value.find((entry) => entry.element === selectedElement.value);
  if (!item) return "正在整理全部元素的空间变化证据。";
  const strongest = selectedElementClues.value[0];
  return `${item.element} 在 ${item.holeCount} 个化验钻孔出现 ${item.segmentCount} 个异常孔段`
    + (strongest ? `，当前优先复核 ${firstHole(strongest)} 的 ${depthRange(strongest)}。` : "。");
});
const backgroundDefinition = computed(() => {
  const method = props.workflow?.rule_set?.background_method === "log_mad"
    ? "对数尺度中位数与 MAD 稳健统计"
    : "当前规则指定的统计方法";
  const scope = props.workflow?.rule_set?.background_scope === "model" ? "本项目全部合格化验数据" : "当前统计范围内的合格化验数据";
  return `背景值是用${scope}，按${method}计算出的该元素基准水平，用来比较孔段相对通常水平的富集程度；它不是边界品位、不是最低工业品位，也不表示元素含量为零。`;
});

async function fetchAllClues() {
  const result: any[] = [];
  let page = 1;
  let total = 1;
  while (result.length < total) {
    const response = await http.get(`/api/geochem-workflows/${props.workflow.id}/clues`, {
      params: { kind: "variation", page, page_size: 1000 },
    });
    result.push(...(response.data.items || []));
    total = Number(response.data.total || 0);
    if (!(response.data.items || []).length) break;
    page += 1;
  }
  allClues.value = result;
}

async function loadOverview() {
  if (!props.workflow?.variation_job_id) return;
  loading.value = true;
  try {
    const [enrichment] = await Promise.all([
      http.get(`/api/geochem-mining/jobs/${props.workflow.variation_job_id}/variation/enrichment`, {
        params: { group: "hole" },
      }),
      fetchAllClues(),
    ]);
    enrichmentRows.value = enrichment.data.items || [];
    selectedElement.value = selectedElement.value || elementSummaries.value[0]?.element || "";
  } finally {
    loading.value = false;
  }
}

async function loadProfiles() {
  if (!selectedElement.value || !props.workflow?.variation_job_id) return;
  loading.value = true;
  try {
    const entries = await Promise.all(holeIds.value.map(async (holeId) => {
      const response = await http.get(
        `/api/geochem-mining/jobs/${props.workflow.variation_job_id}/variation/profile`,
        { params: { element: selectedElement.value, hole_id: holeId } },
      );
      return [holeId, response.data.items || []] as const;
    }));
    profiles.value = Object.fromEntries(entries);
    const firstWithData = entries.find(([, points]) => points.length)?.[0] || holeIds.value[0] || "";
    if (!profiles.value[selectedHole.value]?.length) selectedHole.value = firstWithData;
  } finally {
    loading.value = false;
  }
}

watch(() => props.workflow?.variation_job_id, () => void loadOverview(), { immediate: true });
watch(selectedElement, () => void loadProfiles());

function anomalyPoints(points: any[]) {
  return points.filter((point) => {
    const rank = Number(point.mining_level_rank);
    return Number.isFinite(rank) ? rank >= 2 : Boolean(point.meets_statistical_threshold || point.meets_boundary_grade);
  });
}
function intervalStyle(point: any) {
  const top = ((Number(point.from_depth) || 0) / maxDepth.value) * 100;
  const height = Math.max(0.7, (((Number(point.to_depth) || 0) - (Number(point.from_depth) || 0)) / maxDepth.value) * 100);
  return {
    top: `${top}%`,
    height: `${height}%`,
    background: point.display_color_hex || levelColor(point.mining_level),
  };
}
function firstHole(clue: any) { return clue?.hole_ids?.[0] || "未知钻孔"; }
function depthRange(clue: any) {
  if (clue?.from_depth == null || clue?.to_depth == null) return "深度待复核";
  return `${format(clue.from_depth)}–${format(clue.to_depth)} m`;
}
function shortHole(holeId: string) { return String(holeId || "—").replace("DZ-德达-深-", ""); }
function format(value: any) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed >= 100 ? parsed.toFixed(1) : parsed.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}
function finiteOrNull(value: any) {
  if (value == null || value === "" || typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function ratioExplanation(value: any, background: any, ratio: any) {
  const measured = finiteOrNull(value);
  const baseline = finiteOrNull(background);
  const multiple = finiteOrNull(ratio);
  if (measured == null || baseline == null || baseline <= 0 || multiple == null) {
    return "背景值缺失，不能计算背景倍数";
  }
  return `峰值 ${format(measured)} ppm ÷ 背景值 ${format(baseline)} ppm = ${format(multiple)} 倍`;
}
function clueRatioExplanation(clue: any) {
  const peak = finiteOrNull(clue?.evidence?.max_value);
  const ratio = finiteOrNull(clue?.max_background_ratio);
  const background = peak != null && ratio != null && ratio > 0 ? peak / ratio : null;
  return ratioExplanation(peak, background, ratio);
}
function clueGradeText(clue: any) {
  const evidence = clue?.professional_evidence || {};
  const boundary = evidence.meets_boundary_grade ? "达到边界品位" : "未达到边界品位";
  if (finiteOrNull(evidence.industrial_grade_ppm) == null) return `${boundary}；最低工业品位数据缺失，暂不判断`;
  return `${boundary}；${evidence.meets_industrial_grade ? "达到" : "未达到"}最低工业品位`;
}
function pointEvidenceText(point: any) {
  return `${point.from_depth}–${point.to_depth} m · ${ratioExplanation(point.value, point.background_mean, point.value_to_background_ratio)} · ${displayLevel(point.mining_level)}`;
}
function roleText(role: string) {
  return ({ metal: "金属", indicator: "指示", other: "其他" } as Record<string, string>)[role] || "待确认";
}
function levelText(level: string) {
  const normalized = String(level || "异常线索")
    .replaceAll("_", " ")
    .replace("boundary_grade", "边界品位")
    .replace("industrial_grade", "最低工业品位");
  return displayLevel(normalized);
}
function displayLevel(level: any) {
  const text = String(level ?? "");
  return ({ "最低边界品位": "边界品位", "工业品位": "最低工业品位" } as Record<string, string>)[text] || text;
}
function levelColor(level: string) {
  const text = String(level || "");
  if (text.includes("industrial") || text.includes("工业")) return "#7A0177";
  if (text.includes("boundary") || text.includes("边界")) return "#A50F15";
  if (text.includes("strong") || text.includes("强")) return "#EF3B2C";
  if (text.includes("moderate") || text.includes("明显")) return "#FC8A6A";
  return "#FDD0C4";
}
</script>

<style scoped>
.variation-insights{display:flex;flex-direction:column;gap:16px}.panel{padding:22px;border:1px solid #dce7f2;border-radius:18px;background:#fff;box-shadow:0 10px 32px rgba(28,67,111,.06)}.answer-banner{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:24px 28px;border-radius:18px;background:linear-gradient(135deg,#123d6b,#236bc5);color:#fff;box-shadow:0 14px 34px rgba(26,91,166,.22)}.answer-banner h3{max-width:900px;margin:6px 0;font-size:23px;line-height:1.45}.answer-banner p{margin:0;color:#d9e9fb}.kicker,.step{font-size:12px;font-weight:800;letter-spacing:.06em}.kicker{color:#9bcaff}.answer-stats{display:flex;gap:10px}.answer-stats span{min-width:105px;padding:13px;border:1px solid rgba(255,255,255,.2);border-radius:12px;background:rgba(255,255,255,.09);text-align:center}.answer-stats b{display:block;font-size:24px}.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.section-head.compact{margin-bottom:14px}.section-head h3{margin:5px 0;color:#173a61;font-size:21px}.section-head p{max-width:860px;margin:0;color:#718298;line-height:1.6}.step{color:#2470d5}.element-strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:18px}.element-card{display:flex;min-height:150px;padding:13px;flex-direction:column;align-items:flex-start;border:1px solid #e0e8f2;border-radius:13px;background:#fbfdff;text-align:left;cursor:pointer;transition:.2s}.element-card:hover,.element-card.active{border-color:#3b83e7;background:#f1f7ff;box-shadow:0 8px 20px rgba(45,111,195,.13);transform:translateY(-2px)}.element-symbol{color:#144e87;font-size:25px;font-weight:900}.role{margin:-25px 0 11px auto;padding:3px 7px;border-radius:99px;background:#eef2f6;color:#6e7d90;font-size:10px}.role.metal{background:#fff0ed;color:#d95336}.role.indicator{background:#fff7df;color:#b37800}.element-card b{color:#244766}.element-card small{margin-top:5px;color:#8190a2;line-height:1.4}.strength-track{width:100%;height:5px;margin-top:auto;border-radius:99px;background:#edf1f6;overflow:hidden}.strength-track i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#ffb58a,#ef3b2c)}.background-explainer{display:flex;gap:10px;margin-top:16px;padding:12px 14px;border:1px solid #d8e7f5;border-radius:10px;background:#f5faff;color:#5f748b;font-size:12px;line-height:1.6}.background-explainer b{flex:none;color:#1d5f9e}.legend-row{display:flex;gap:17px;flex-wrap:wrap;margin-top:16px;color:#65778c;font-size:12px}.legend-row span{display:flex;align-items:center;gap:6px}.swatch{width:18px;height:8px;border-radius:3px}.background{background:#fff7f3;border:1px solid #eaded9}.enriched{background:#fdd0c4}.positive{background:#fc8a6a}.strong{background:#ef3b2c}.boundary{background:#a50f15}.industrial{background:#7a0177}.detail-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(420px,.8fr);gap:16px}.borehole-bands{display:grid;grid-template-columns:56px repeat(7,1fr);height:430px;margin-top:18px}.depth-axis{position:relative;border-right:1px solid #dbe5ef}.depth-axis span{position:absolute;right:8px;color:#90a0b2;font-size:10px;transform:translateY(-50%)}.borehole-column{display:flex;padding:0 8px;flex-direction:column;align-items:center;border:0;background:transparent;cursor:pointer}.borehole-column b{margin-bottom:8px;color:#42617e}.borehole-column small{margin-top:7px;color:#8796a7}.borehole-column.active b{color:#1865c8}.hole-track{position:relative;width:34px;flex:1;border-radius:8px;background:linear-gradient(90deg,#f0f4f8,#e7edf4);box-shadow:inset 0 0 0 1px #e0e7ef}.borehole-column.active .hole-track{box-shadow:0 0 0 3px #d8e9ff,inset 0 0 0 1px #3c82dd}.interval{position:absolute;left:2px;right:2px;min-height:4px;border-radius:4px}.profile-panel{min-width:0}.profile-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}.profile-summary span{padding:11px;border-radius:10px;background:#f3f7fb;color:#728399;font-size:11px;text-align:center}.profile-summary b{display:block;color:#1762b5;font-size:20px}.segment-list{display:flex;flex-direction:column;gap:8px}.segment-list article{display:grid;grid-template-columns:10px minmax(300px,1fr) 100px 90px 130px;gap:12px;align-items:center;padding:11px 13px;border:1px solid #e4ebf3;border-radius:11px;background:#fbfdff}.segment-list small{display:block;margin-top:3px;color:#7c8b9d}.level-dot{width:9px;height:36px;border-radius:6px}.support{color:#526e8b;font-size:12px}@media(max-width:1500px){.element-strip{grid-template-columns:repeat(4,1fr)}}@media(max-width:1100px){.answer-banner,.section-head{flex-direction:column}.answer-stats{width:100%}.answer-stats span{flex:1}.detail-grid{grid-template-columns:1fr}.element-strip{grid-template-columns:repeat(3,1fr)}}
</style>
