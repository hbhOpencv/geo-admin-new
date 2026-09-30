<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">化学钻孔</h2>
        <div class="page-subtitle">
          独立展示化学元素/XRF 数据对应的钻孔、孔口坐标、样品数量与数据状态。
        </div>
      </div>
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索化学钻孔编号..."
          clearable
          style="width: 260px"
          @keyup.enter="load"
        />
        <el-button v-if="isAdmin" :loading="importing" @click="triggerImport">导入CSV</el-button>
        <el-button type="primary" @click="load">刷新</el-button>
        <input ref="fileInput" type="file" accept=".csv,text/csv" class="file-input" @change="handleImport" />
      </div>
    </div>

    <div class="content-card">
      <div class="section-caption">化学钻孔列表</div>

      <el-table :data="rows" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="hole_id" label="化学钻孔编号" min-width="190" />
        <el-table-column label="X 坐标" width="130">
          <template #default="{ row }">{{ fmtNumber(row.collar_x, 3) }}</template>
        </el-table-column>
        <el-table-column label="Y 坐标" width="140">
          <template #default="{ row }">{{ fmtNumber(row.collar_y, 3) }}</template>
        </el-table-column>
        <el-table-column label="Z 坐标" width="120">
          <template #default="{ row }">{{ fmtNumber(row.collar_z, 3) }}</template>
        </el-table-column>
        <el-table-column label="深度范围(m)" width="170">
          <template #default="{ row }">
            {{ fmtNumber(row.depth_min, 2) }} - {{ fmtNumber(row.depth_max, 2) }}
          </template>
        </el-table-column>
        <el-table-column prop="sample_count" label="样品数" width="100" />
        <el-table-column prop="element_count" label="元素数" width="100" />
        <el-table-column prop="unit" label="单位" width="90" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="openAssays(row)">查看元素</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          layout="prev, pager, next, sizes, total"
          :total="total"
          @current-change="load"
          @size-change="load"
        />
      </div>
    </div>

    <el-drawer v-model="assayDrawerVisible" :title="`${selectedHoleId} 各层化学元素`" size="86%">
      <div class="drawer-toolbar">
        <div class="drawer-hint">
          展示该化学钻孔每个深度区间的元素含量，默认显示常用元素，可切换更多元素列。
        </div>
        <el-select
          v-model="visibleElements"
          multiple
          collapse-tags
          collapse-tags-tooltip
          placeholder="选择展示元素"
          style="width: 360px"
        >
          <el-option v-for="element in assayElements" :key="element" :label="element" :value="element" />
        </el-select>
      </div>

      <el-table :data="assayRows" border height="calc(100vh - 260px)" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" fixed />
        <el-table-column label="起始深度(m)" width="120" fixed>
          <template #default="{ row }">{{ fmtNumber(row.from_depth, 2) }}</template>
        </el-table-column>
        <el-table-column label="终止深度(m)" width="120" fixed>
          <template #default="{ row }">{{ fmtNumber(row.to_depth, 2) }}</template>
        </el-table-column>
        <el-table-column v-for="element in visibleElements" :key="element" :prop="element" :label="element" width="105">
          <template #default="{ row }">{{ fmtElement(row[element]) }}</template>
        </el-table-column>
      </el-table>

      <div class="table-pager">
        <el-pagination
          v-model:current-page="assayPage"
          v-model:page-size="assayPageSize"
          layout="prev, pager, next, sizes, total"
          :total="assayTotal"
          :page-sizes="[20, 50, 100, 200]"
          @current-change="loadAssays"
          @size-change="loadAssays"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import type { ChemicalBorehole, Page } from "../api/types";
import { useCurrentUser } from "../utils/session";

const props = defineProps<{ modelId: string }>();
const modelId = Number(props.modelId);
const { currentUser } = useCurrentUser();
const isAdmin = computed(() => !!currentUser.value?.is_admin);

const keyword = ref("");
const rows = ref<ChemicalBorehole[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const importing = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const assayDrawerVisible = ref(false);
const selectedHoleId = ref("");
const assayRows = ref<any[]>([]);
const assayElements = ref<string[]>([]);
const visibleElements = ref<string[]>([]);
const assayPage = ref(1);
const assayPageSize = ref(50);
const assayTotal = ref(0);

function fmtNumber(value?: number | null, precision = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return Number(value).toFixed(precision);
}

function fmtElement(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

async function load() {
  const res = await http.get<Page<ChemicalBorehole>>(`/api/models/${modelId}/chemical-boreholes`, {
    params: { keyword: keyword.value || undefined, page: page.value, page_size: pageSize.value },
  });
  rows.value = res.data.items;
  total.value = res.data.total;
}


function triggerImport() {
  fileInput.value?.click();
}

async function handleImport(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  importing.value = true;
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await http.post(`/api/models/${modelId}/chemical-boreholes/import`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    ElMessage.success(`?????${res.data.hole_count} ??????${res.data.row_count} ???`);
    page.value = 1;
    visibleElements.value = [];
    assayDrawerVisible.value = false;
    await load();
  } finally {
    importing.value = false;
    input.value = "";
  }
}

async function openAssays(row: ChemicalBorehole) {
  selectedHoleId.value = row.hole_id;
  assayPage.value = 1;
  assayDrawerVisible.value = true;
  await loadAssays();
}

async function loadAssays() {
  if (!selectedHoleId.value) return;
  const res = await http.get<Page<any> & { elements: string[] }>(
    `/api/models/${modelId}/chemical-boreholes/${encodeURIComponent(selectedHoleId.value)}/assays`,
    { params: { page: assayPage.value, page_size: assayPageSize.value } }
  );
  assayRows.value = res.data.items;
  assayTotal.value = res.data.total;
  assayElements.value = res.data.elements;
  if (!visibleElements.value.length) {
    visibleElements.value = [...res.data.elements];
  }
}

load();
</script>

<style scoped>
.drawer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.drawer-hint {
  color: var(--app-muted);
  font-size: 13px;
}

.file-input {
  display: none;
}

</style>
