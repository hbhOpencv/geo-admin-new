<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">地质体列表</h2>
        <div class="page-subtitle">模型 {{ modelId }} 的地质体属性、岩土名称与工程参数。</div>
      </div>
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索地层代号 / 岩土名称 / 元素ID..."
          clearable
          style="width: 320px"
          @keyup.enter="load"
        />
        <el-button v-if="isAdmin" :loading="importing" @click="triggerImport">导入CSV</el-button>
        <el-button v-if="isAdmin" type="primary" @click="openCreate">新增地质体</el-button>
        <input ref="fileInput" type="file" accept=".csv,text/csv" class="file-input" @change="handleImport" />
      </div>
    </div>

    <div class="content-card">
      <div class="section-caption">地质体列表</div>

      <el-table :data="rows" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="元素ID" label="元素ID" width="120" />
        <el-table-column prop="地层代号" label="地层代号" width="140" />
        <el-table-column prop="岩土名称" label="岩土名称" width="220" show-overflow-tooltip />
        <el-table-column prop="体积" label="体积" min-width="180" show-overflow-tooltip />
        <el-table-column prop="表面积" label="表面积" min-width="180" show-overflow-tooltip />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :width="isAdmin ? 260 : 90" label="操作" fixed="right">
          <template #default="{ row }">
            <div class="page-table-actions">
              <el-button size="small" @click="openDetail(row)">详情</el-button>
              <template v-if="isAdmin">
                <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
              </template>
            </div>
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

    <el-drawer v-model="detailVisible" title="地质体详情" size="50%">
      <el-table :data="detailKvs" border style="width: 100%">
        <el-table-column prop="field" label="字段" width="220" />
        <el-table-column prop="value" label="值" show-overflow-tooltip />
      </el-table>
    </el-drawer>

    <el-dialog v-if="isAdmin" v-model="dlgVisible" :title="dlgTitle" width="760px">
      <div class="dialog-section">
        <div class="dialog-section-title">基础信息</div>
        <el-form :model="edit" label-width="120px">
          <div class="compact-form-grid">
            <el-form-item label="元素ID">
              <el-input v-model="edit.元素ID" />
            </el-form-item>
            <el-form-item label="地层代号">
              <el-input v-model="edit.地层代号" />
            </el-form-item>
            <el-form-item label="岩土名称">
              <el-input v-model="edit.岩土名称" />
            </el-form-item>
            <el-form-item label="层">
              <el-input v-model="edit.层" />
            </el-form-item>
            <el-form-item label="体积">
              <el-input v-model="edit.体积" />
            </el-form-item>
            <el-form-item label="表面积">
              <el-input v-model="edit.表面积" />
            </el-form-item>
          </div>
        </el-form>
      </div>

      <div class="dialog-section">
        <div class="dialog-section-title">范围</div>
        <el-form :model="edit" label-width="120px">
          <div class="compact-form-grid">
            <el-form-item label="范围下限">
              <el-input v-model="edit.范围下限" />
            </el-form-item>
            <el-form-item label="范围上限">
              <el-input v-model="edit.范围上限" />
            </el-form-item>
          </div>
        </el-form>
      </div>

      <div class="dialog-section">
        <div class="dialog-section-title">物性 / 力学</div>
        <el-form :model="edit" label-width="150px">
          <div class="compact-form-grid">
            <el-form-item label="潮湿程度">
              <el-input v-model="edit.潮湿程度" />
            </el-form-item>
            <el-form-item label="单轴饱和抗压强度">
              <el-input v-model="edit.单轴饱和抗压强度" />
            </el-form-item>
            <el-form-item label="密实状态">
              <el-input v-model="edit.密实状态" />
            </el-form-item>
            <el-form-item label="塑性状态">
              <el-input v-model="edit.塑性状态" />
            </el-form-item>
            <el-form-item label="天然密度">
              <el-input v-model="edit.天然密度" />
            </el-form-item>
            <el-form-item label="风化程度">
              <el-input v-model="edit.风化程度" />
            </el-form-item>
            <el-form-item label="工程等级">
              <el-input v-model="edit.工程等级" />
            </el-form-item>
            <el-form-item label="基本承载力">
              <el-input v-model="edit.基本承载力" />
            </el-form-item>
            <el-form-item label="基地摩擦系数">
              <el-input v-model="edit.基地摩擦系数" />
            </el-form-item>
            <el-form-item label="内摩擦角">
              <el-input v-model="edit.内摩擦角" />
            </el-form-item>
            <el-form-item label="凝聚力">
              <el-input v-model="edit.凝聚力" />
            </el-form-item>
          </div>
        </el-form>
      </div>

      <div class="dialog-section">
        <div class="dialog-section-title">边坡 / 其他</div>
        <el-form :model="edit" label-width="190px">
          <el-form-item label="临时挖方边坡率">
            <el-input v-model="edit.临时挖方边坡率" />
          </el-form-item>
          <el-form-item label="永久挖方边坡率">
            <el-input v-model="edit.永久挖方边坡率" />
          </el-form-item>
          <el-form-item label="时代成因">
            <el-input v-model="edit.时代成因" />
          </el-form-item>
          <el-form-item label="填料类别">
            <el-input v-model="edit.填料类别" />
          </el-form-item>
          <el-form-item label="钻孔灌注桩桩周极限摩阻力">
            <el-input v-model="edit.钻孔灌注桩桩周极限摩阻力" />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dlgVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import type { Geobody, Page } from "../api/types";
import { useCurrentUser } from "../utils/session";

type DetailKV = { field: string; value: string };

const props = defineProps<{ modelId: string }>();
const modelId = Number(props.modelId);

const { currentUser } = useCurrentUser();
const isAdmin = computed(() => !!currentUser.value?.is_admin);

const keyword = ref("");
const rows = ref<Geobody[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const importing = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

const detailVisible = ref(false);
const detailRow = ref<Geobody | null>(null);

const dlgVisible = ref(false);
const dlgTitle = ref("新增地质体");
const saving = ref(false);

const EMPTY_EDIT: Record<string, any> = {
  id: null,
  model_id: modelId,
  key: "",
  层: "",
  体积: "",
  表面积: "",
  元素ID: "",
  范围下限: "",
  范围上限: "",
  潮湿程度: "",
  单轴饱和抗压强度: "",
  地层代号: "",
  风化程度: "",
  工程等级: "",
  基本承载力: "",
  基地摩擦系数: "",
  临时挖方边坡率: "",
  密实状态: "",
  内摩擦角: "",
  凝聚力: "",
  时代成因: "",
  塑性状态: "",
  天然密度: "",
  填料类别: "",
  岩土名称: "",
  永久挖方边坡率: "",
  钻孔灌注桩桩周极限摩阻力: "",
};

const edit = reactive<Record<string, any>>({ ...EMPTY_EDIT });

function fmt(value: string) {
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

const fieldOrder = [
  "id",
  "model_id",
  "地层代号",
  "岩土名称",
  "元素ID",
  "层",
  "体积",
  "表面积",
  "范围下限",
  "范围上限",
  "潮湿程度",
  "单轴饱和抗压强度",
  "风化程度",
  "工程等级",
  "基本承载力",
  "基地摩擦系数",
  "临时挖方边坡率",
  "永久挖方边坡率",
  "密实状态",
  "塑性状态",
  "天然密度",
  "内摩擦角",
  "凝聚力",
  "时代成因",
  "填料类别",
  "钻孔灌注桩桩周极限摩阻力",
  "key",
  "created_at",
];

const detailKvs = computed<DetailKV[]>(() => {
  const row = detailRow.value as Record<string, any> | null;
  if (!row) return [];

  const used = new Set<string>();
  const out: DetailKV[] = [];

  function pushKV(key: string) {
    if (used.has(key)) return;
    used.add(key);
    const value = row[key];
    if (value === undefined || value === null || value === "") return;
    out.push({
      field: key,
      value: key === "created_at" ? fmt(String(value)) : String(value),
    });
  }

  fieldOrder.forEach(pushKV);
  Object.keys(row).forEach(pushKV);
  return out;
});

async function load() {
  const res = await http.get<Page<Geobody>>(`/api/models/${modelId}/geobodies`, {
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
    form.append("geobody_csv", file);
    const res = await http.post(`/api/models/${modelId}/geobodies/import`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    ElMessage.success(`导入成功：${res.data.imported} 个地质体`);
    page.value = 1;
    await load();
  } finally {
    importing.value = false;
    input.value = "";
  }
}

function openDetail(row: Geobody) {
  detailRow.value = row;
  detailVisible.value = true;
}

function openCreate() {
  dlgTitle.value = "新增地质体";
  Object.assign(edit, { ...EMPTY_EDIT, id: null, model_id: modelId });
  dlgVisible.value = true;
}

function openEdit(row: Geobody) {
  dlgTitle.value = "编辑地质体";
  Object.assign(edit, { ...EMPTY_EDIT, ...row });
  dlgVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    if (edit.id == null) {
      await http.post("/api/geobodies", edit);
      ElMessage.success("已新增地质体");
    } else {
      const payload: Record<string, any> = { ...edit };
      delete payload.id;
      delete payload.model_id;
      delete payload.created_at;
      await http.put(`/api/geobodies/${edit.id}`, payload);
      ElMessage.success("已保存修改");
    }
    dlgVisible.value = false;
    await load();
  } catch (error: any) {
    const detail = error?.response?.data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: any) => item?.msg || JSON.stringify(item)).join("; ")
          : "保存失败";
    ElMessage.error(message);
  } finally {
    saving.value = false;
  }
}

async function remove(row: Geobody) {
  if (!confirm(`确定删除地质体 ID=${row.id} 吗？`)) return;
  try {
    await http.delete(`/api/geobodies/${row.id}`);
    ElMessage.success("已删除");
    await load();
  } catch {
    ElMessage.error("删除失败");
  }
}

load();
</script>

<style scoped>
.file-input {
  display: none;
}
</style>
