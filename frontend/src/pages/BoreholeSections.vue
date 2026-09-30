<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">分层管理</h2>
        <div class="page-subtitle">钻孔 {{ boreholeTitle }} 的分层信息。</div>
      </div>
      <div class="toolbar">
        <el-button v-if="isAdmin" type="primary" @click="openCreate">新增</el-button>
      </div>
    </div>

    <div class="content-card">
      <div class="list-caption">分层列表</div>

      <el-table :data="rows" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column label="层厚" width="100">
          <template #default="{ row }">{{ getThickness(row) }}</template>
        </el-table-column>
        <el-table-column prop="Depth" label="起始孔深" width="120" />
        <el-table-column prop="Bottom" label="终止孔深" width="120" />
        <el-table-column prop="Description" label="岩性描述" min-width="260" show-overflow-tooltip />
        <el-table-column prop="geobody_key" label="地层代号" width="120" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :width="isAdmin ? 220 : 90" label="操作" fixed="right">
          <template #default="{ row }">
            <div class="page-table-actions">
              <template v-if="isAdmin">
                <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
                <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
              </template>
              <span v-else>-</span>
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

    <el-dialog v-if="isAdmin" v-model="dlgVisible" :title="dlgTitle" width="760px">
      <div class="dialog-section">
        <div class="dialog-section-title">基本信息</div>
        <el-form :model="edit" label-width="110px">
          <div class="compact-form-grid">
            <el-form-item label="层位">
              <el-input v-model="edit[FIELD_ITEM]" placeholder="<7-17>" />
            </el-form-item>
            <el-form-item label="层厚">
              <el-input-number v-model="edit[FIELD_HEIGHT]" style="width: 100%" />
            </el-form-item>

            <el-form-item label="起始孔深">
              <el-input-number v-model="edit.Depth" style="width: 100%" />
            </el-form-item>
            <el-form-item label="终止孔深">
              <el-input-number v-model="edit.Bottom" style="width: 100%" />
            </el-form-item>

            <el-form-item label="地层代号">
              <el-input v-model="edit.geobody_key" />
            </el-form-item>
          </div>

          <el-form-item label="岩性描述">
            <el-input v-model="edit.Description" type="textarea" :rows="4" />
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
import type { Borehole, BoreholeSection, Page } from "../api/types";
import { useCurrentUser } from "../utils/session";

const FIELD_ITEM = "项";
const FIELD_HEIGHT = "高度";

const props = defineProps<{ boreholeId: string }>();
const boreholeId = Number(props.boreholeId);
const { currentUser } = useCurrentUser();
const isAdmin = computed(() => !!currentUser.value?.is_admin);

const rows = ref<BoreholeSection[]>([]);
const boreholeName = ref("");
const page = ref(1);
const pageSize = ref(50);
const total = ref(0);

const dlgVisible = ref(false);
const dlgTitle = ref("新增分层");
const saving = ref(false);

const edit = reactive<Record<string, any>>({
  id: null,
  borehole_id: boreholeId,
  [FIELD_ITEM]: "",
  [FIELD_HEIGHT]: null,
  Depth: null,
  Bottom: null,
  Description: "",
  geobody_key: "",
});

const boreholeTitle = computed(() => boreholeName.value || String(boreholeId));

function fmt(value: string) {
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

function getThickness(row: BoreholeSection) {
  const direct = row[FIELD_HEIGHT];
  if (direct !== null && direct !== undefined && direct !== "") return direct;
  if (typeof row.Bottom === "number" && typeof row.Depth === "number") {
    return Number((row.Bottom - row.Depth).toFixed(3));
  }
  return "";
}

function resetEdit() {
  Object.assign(edit, {
    id: null,
    borehole_id: boreholeId,
    [FIELD_ITEM]: "",
    [FIELD_HEIGHT]: null,
    Depth: null,
    Bottom: null,
    Description: "",
    geobody_key: "",
  });
}

async function load() {
  const res = await http.get<Page<BoreholeSection>>(`/api/boreholes/${boreholeId}/sections`, {
    params: { page: page.value, page_size: pageSize.value },
  });
  rows.value = res.data.items;
  total.value = res.data.total;
}

async function loadBorehole() {
  try {
    const res = await http.get<Borehole>(`/api/boreholes/${boreholeId}`);
    boreholeName.value = res.data.Borehole || "";
  } catch {
    boreholeName.value = "";
  }
}

function openCreate() {
  dlgTitle.value = "新增分层";
  resetEdit();
  dlgVisible.value = true;
}

function openEdit(row: BoreholeSection) {
  dlgTitle.value = "编辑分层";
  resetEdit();
  Object.assign(edit, row, {
    [FIELD_ITEM]: row[FIELD_ITEM] ?? "",
    [FIELD_HEIGHT]: row[FIELD_HEIGHT] ?? null,
  });
  dlgVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    const payload = {
      [FIELD_ITEM]: edit[FIELD_ITEM] || null,
      [FIELD_HEIGHT]: edit[FIELD_HEIGHT] ?? null,
      Depth: edit.Depth ?? null,
      Bottom: edit.Bottom ?? null,
      Description: edit.Description ?? "",
      geobody_key: edit.geobody_key ?? "",
    };

    if (edit.id == null) {
      await http.post("/api/sections", {
        borehole_id: boreholeId,
        ...payload,
      });
      ElMessage.success("分层已新增");
    } else {
      await http.put(`/api/sections/${edit.id}`, payload);
      ElMessage.success("分层已更新");
    }
    dlgVisible.value = false;
    await load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function remove(row: BoreholeSection) {
  if (!confirm(`确定删除分层 ID=${row.id} 吗？`)) return;
  try {
    await http.delete(`/api/sections/${row.id}`);
    ElMessage.success("分层已删除");
    await load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "删除失败");
  }
}

loadBorehole();
load();
</script>

<style scoped>
.page-table-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.dialog-section-title {
  margin-bottom: 14px;
  padding: 10px 12px;
  border-left: 3px solid var(--app-primary);
  background: rgba(29, 111, 130, 0.06);
  font-weight: 700;
}

.list-caption {
  margin-bottom: 14px;
  padding-left: 10px;
  border-left: 4px solid #2f73ff;
  color: #1d2f4d;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.2;
}

.compact-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}

@media (max-width: 900px) {
  .compact-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
