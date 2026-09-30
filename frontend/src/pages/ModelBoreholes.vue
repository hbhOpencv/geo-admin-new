<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">钻孔管理</h2>
        <div class="page-subtitle">模型 {{ modelId }} 的钻孔编号、坐标与孔深信息。</div>
      </div>
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索钻孔编号..."
          clearable
          style="width: 260px"
          @keyup.enter="load"
        />
        <el-button v-if="isAdmin" :loading="importing" @click="triggerImport">导入CSV</el-button>
        <el-button v-if="isAdmin" type="primary" @click="openCreate">新增</el-button>
        <input ref="fileInput" type="file" accept=".csv,text/csv" class="file-input" @change="handleImport" />
      </div>
    </div>

    <div class="content-card">
      <div class="section-caption">钻孔列表</div>

      <el-table :data="rows" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="Borehole" label="钻孔编号" min-width="180" />
        <el-table-column label="实际孔深(m)" width="120">
          <template #default="{ row }">{{ row.Holedepth ?? "" }}</template>
        </el-table-column>
        <el-table-column label="X 坐标" width="140">
          <template #default="{ row }">{{ row.x ?? row.Easting ?? "" }}</template>
        </el-table-column>
        <el-table-column label="Y 坐标" width="140">
          <template #default="{ row }">{{ row.y ?? row.Northing ?? "" }}</template>
        </el-table-column>
        <el-table-column label="Z 坐标" width="140">
          <template #default="{ row }">{{ row.z ?? row.Elevation ?? "" }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :width="isAdmin ? 260 : 90" label="操作" fixed="right">
          <template #default="{ row }">
            <div class="page-table-actions">
              <el-button size="small" @click="goSections(row.id)">分层</el-button>
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

    <el-dialog v-if="isAdmin" v-model="dlgVisible" :title="dlgTitle" width="700px">
      <div class="dialog-section">
        <div class="dialog-section-title">基本信息</div>
        <el-form :model="edit" label-width="100px">
          <div class="compact-form-grid">
            <el-form-item label="钻孔编号">
              <el-input v-model="edit.Borehole" />
            </el-form-item>
            <div />

            <el-form-item label="X 坐标">
              <el-input-number v-model="edit.Easting" style="width: 100%" :precision="6" />
            </el-form-item>
            <el-form-item label="Y 坐标">
              <el-input-number v-model="edit.Northing" style="width: 100%" :precision="6" />
            </el-form-item>

            <el-form-item label="Z 坐标">
              <el-input-number v-model="edit.Elevation" style="width: 100%" :precision="6" />
            </el-form-item>
            <el-form-item label="孔深">
              <el-input-number v-model="edit.Holedepth" style="width: 100%" :precision="3" />
            </el-form-item>
          </div>
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
import { useRouter } from "vue-router";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import type { Borehole, Page } from "../api/types";
import { useCurrentUser } from "../utils/session";

const props = defineProps<{ modelId: string }>();
const modelId = Number(props.modelId);

const router = useRouter();
const { currentUser } = useCurrentUser();
const isAdmin = computed(() => !!currentUser.value?.is_admin);
const keyword = ref("");
const rows = ref<Borehole[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const importing = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

const dlgVisible = ref(false);
const dlgTitle = ref("新增钻孔");
const saving = ref(false);

const edit = reactive<any>({
  id: null,
  model_id: modelId,
  Borehole: "",
  Easting: null,
  Northing: null,
  Elevation: null,
  Holedepth: null,
  鍘熺偣: "",
});

function fmt(value: string) {
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

function goSections(id: number) {
  router.push(`/boreholes/${id}/sections`);
}

async function load() {
  const res = await http.get<Page<Borehole>>(`/api/models/${modelId}/boreholes`, {
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
    form.append("borehole_csv", file);
    const res = await http.post(`/api/models/${modelId}/boreholes/import`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    ElMessage.success(`导入成功：${res.data.boreholes} 个钻孔，${res.data.sections} 条分层`);
    page.value = 1;
    await load();
  } finally {
    importing.value = false;
    input.value = "";
  }
}

function openCreate() {
  dlgTitle.value = "新增钻孔";
  Object.assign(edit, {
    id: null,
    model_id: modelId,
    Borehole: "",
    Easting: null,
    Northing: null,
    Elevation: null,
    Holedepth: null,
    鍘熺偣: "",
  });
  dlgVisible.value = true;
}

function openEdit(row: Borehole) {
  dlgTitle.value = "编辑钻孔";
  Object.assign(edit, row);
  dlgVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    if (!edit.Borehole) return;
    if (edit.id == null) {
      await http.post("/api/boreholes", edit);
    } else {
      await http.put(`/api/boreholes/${edit.id}`, {
        Borehole: edit.Borehole,
        Easting: edit.Easting,
        Northing: edit.Northing,
        Elevation: edit.Elevation,
        Holedepth: edit.Holedepth,
        鍘熺偣: edit["鍘熺偣"],
      });
    }
    dlgVisible.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

async function remove(row: Borehole) {
  if (!confirm(`确定删除钻孔“${row.Borehole}”吗？其分层数据也会一起删除。`)) return;
  await http.delete(`/api/boreholes/${row.id}`);
  await load();
}

load();
</script>

<style scoped>
.file-input {
  display: none;
}
</style>
