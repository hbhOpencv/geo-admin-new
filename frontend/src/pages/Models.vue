<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">模型管理</h2>
        <div class="page-subtitle">统一维护地质模型，并从模型快速进入钻孔、地质体与重建流程。</div>
      </div>
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索模型名称..."
          clearable
          style="width: 260px"
          @keyup.enter="load"
        />
        <el-button v-if="isAdmin" type="primary" @click="openCreate">新增模型</el-button>
      </div>
    </div>

    <div class="content-card">
      <div class="section-caption">模型列表</div>

      <el-table :data="rows" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="模型名称" min-width="220" />
        <el-table-column prop="description" label="描述" min-width="260" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :width="isAdmin ? 320 : 180" label="操作" fixed="right">
          <template #default="{ row }">
            <div class="page-table-actions">
              <el-button size="small" @click="goBoreholes(row.id)">钻孔</el-button>
              <el-button size="small" @click="goGeobodies(row.id)">地质体</el-button>
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

    <el-dialog v-if="isAdmin" v-model="dlgVisible" :title="dlgTitle" width="620px">
      <div class="dialog-section">
        <div class="dialog-section-title">基本信息</div>
        <el-form :model="edit" label-width="90px">
          <el-form-item label="名称">
            <el-input v-model="edit.name" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="edit.description" type="textarea" :rows="4" />
          </el-form-item>
        </el-form>
      </div>

      <div v-if="edit.id == null" class="dialog-section">
        <div class="dialog-section-title">CSV 导入</div>
        <el-form label-width="170px">
          <el-form-item label="钻孔 CSV（可选）">
            <el-upload
              :auto-upload="false"
              :limit="1"
              :on-change="onBoreholeFileChange"
              :on-remove="() => (boreholeFile = null)"
              accept=".csv"
            >
              <el-button>选择钻孔 CSV</el-button>
            </el-upload>
          </el-form-item>

          <el-form-item label="地质体 CSV（可选）">
            <el-upload
              :auto-upload="false"
              :limit="1"
              :on-change="onGeobodyFileChange"
              :on-remove="() => (geobodyFile = null)"
              accept=".csv"
            >
              <el-button>选择地质体 CSV</el-button>
            </el-upload>
          </el-form-item>


          <el-form-item label="化学钻孔 CSV（可选）">
            <el-upload
              :auto-upload="false"
              :limit="1"
              :on-change="onChemicalFileChange"
              :on-remove="() => (chemicalFile = null)"
              accept=".csv"
            >
              <el-button>选择化学钻孔 CSV</el-button>
            </el-upload>
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
import { useRouter } from "vue-router";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import type { GeologicalModel, Page } from "../api/types";
import { useCurrentUser } from "../utils/session";

const router = useRouter();
const { currentUser } = useCurrentUser();
const isAdmin = computed(() => !!currentUser.value?.is_admin);

const keyword = ref("");
const rows = ref<GeologicalModel[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);

const dlgVisible = ref(false);
const dlgTitle = ref("新建模型");
const saving = ref(false);

const edit = reactive<any>({
  id: null,
  name: "",
  description: "",
});

let boreholeFile: File | null = null;
let geobodyFile: File | null = null;
let chemicalFile: File | null = null;

function onBoreholeFileChange(file: any) {
  boreholeFile = file?.raw ?? null;
}

function onGeobodyFileChange(file: any) {
  geobodyFile = file?.raw ?? null;
}

function onChemicalFileChange(file: any) {
  chemicalFile = file?.raw ?? null;
}

function fmt(value: string) {
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

function goBoreholes(id: number) {
  router.push(`/models/${id}/boreholes`);
}

function goGeobodies(id: number) {
  router.push(`/models/${id}/geobodies`);
}

async function load() {
  const res = await http.get<Page<GeologicalModel>>("/api/models", {
    params: { keyword: keyword.value || undefined, page: page.value, page_size: pageSize.value },
  });
  rows.value = res.data.items;
  total.value = res.data.total;
}

function openCreate() {
  dlgTitle.value = "新建模型";
  edit.id = null;
  edit.name = "";
  edit.description = "";
  boreholeFile = null;
  geobodyFile = null;
  chemicalFile = null;
  dlgVisible.value = true;
}

function openEdit(row: GeologicalModel) {
  dlgTitle.value = "编辑模型";
  edit.id = row.id;
  edit.name = row.name;
  edit.description = row.description ?? "";
  dlgVisible.value = true;
}

async function save() {
  saving.value = true;
  try {
    if (!edit.name) return;

    if (edit.id == null) {
      const hasAny = !!boreholeFile || !!geobodyFile;
      const hasBoth = !!boreholeFile && !!geobodyFile;

      if (hasAny && !hasBoth) {
        ElMessage.error("导入时需要同时选择：钻孔 CSV + 地质体 CSV");
        return;
      }

      let createdModelId: number | null = null;
      if (hasBoth) {
        const formData = new FormData();
        formData.append("name", edit.name);
        formData.append("description", edit.description ?? "");
        formData.append("borehole_csv", boreholeFile as File);
        formData.append("geobody_csv", geobodyFile as File);
        const res = await http.post("/api/models/import", formData);
        createdModelId = res.data.id;
      } else {
        const res = await http.post("/api/models", { name: edit.name, description: edit.description });
        createdModelId = res.data.id;
      }

      if (chemicalFile && createdModelId != null) {
        const chemicalForm = new FormData();
        chemicalForm.append("file", chemicalFile);
        const res = await http.post(`/api/models/${createdModelId}/chemical-boreholes/import`, chemicalForm, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        ElMessage.success(`化学钻孔导入成功：${res.data.hole_count} 个钻孔，${res.data.row_count} 条样品`);
      }
    } else {
      await http.put(`/api/models/${edit.id}`, { name: edit.name, description: edit.description });
    }

    dlgVisible.value = false;
    await load();
  } finally {
    saving.value = false;
  }
}

async function remove(row: GeologicalModel) {
  if (!confirm(`确定删除模型“${row.name}”吗？这会联动删除钻孔和地质体数据。`)) return;
  await http.delete(`/api/models/${row.id}`);
  await load();
}

load();
</script>
