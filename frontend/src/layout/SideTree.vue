<template>
  <div class="side-tree">
    <div class="tree-toolbar">
      <el-input v-model="keyword" placeholder="搜索模型..." clearable @input="reload" />
      <el-button class="all-btn" @click="go('/models')">查看全部</el-button>
    </div>

    <div class="tree-scroll">
      <el-tree
        :data="nodes"
        node-key="id"
        :expand-on-click-node="false"
        highlight-current
        @node-click="onClick"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { http } from "../api/http";
import type { GeologicalModel, Page } from "../api/types";

const router = useRouter();
const keyword = ref("");
const nodes = ref<any[]>([]);

function go(path: string) {
  router.push(path);
}

async function reload() {
  const res = await http.get<Page<GeologicalModel>>("/api/models", {
    params: { keyword: keyword.value || undefined, page: 1, page_size: 200 },
  });
  const models = res.data.items;

  nodes.value = models.map((model) => ({
    id: `m-${model.id}`,
    label: model.name,
    type: "model",
    modelId: model.id,
    children: [
      { id: `m-${model.id}-b`, label: "钻孔信息", type: "boreholes", modelId: model.id },
      { id: `m-${model.id}-chem`, label: "化学钻孔", type: "chemical_boreholes", modelId: model.id },
      { id: `m-${model.id}-g`, label: "地质体信息", type: "geobodies", modelId: model.id },
      {
        id: `m-${model.id}-r`,
        label: "三维重建",
        type: "reconstruct",
        modelId: model.id,
        children: [
          { id: `m-${model.id}-ra`, label: "算法 A / CDT", type: "reconstruct_a", modelId: model.id },
          { id: `m-${model.id}-rb`, label: "算法 B / 体素化", type: "reconstruct_b", modelId: model.id },
          { id: `m-${model.id}-rdeep`, label: "算法 C / MLP", type: "reconstruct_deep", modelId: model.id },
        ],
      },
      {
        id: `m-${model.id}-mining`,
        label: "化学元素找矿分析",
        type: "geochem_mining",
        modelId: model.id,
        children: [
          { id: `m-${model.id}-mining-expert`, label: "专家公式建模对比", type: "geochem_expert", modelId: model.id },
          { id: `m-${model.id}-mining-v`, label: "1. 化学元素变化规律", type: "geochem_mining_variation", modelId: model.id },
          { id: `m-${model.id}-mining-c`, label: "2. 化学元素相关性", type: "geochem_mining_correlation", modelId: model.id },
          { id: `m-${model.id}-mining-3d`, label: "3. 化学元素三维重建", type: "reconstruct_geochem", modelId: model.id },
        ],
      },
    ],
  }));
}

function onClick(node: any) {
  if (node.type === "model" || node.type === "boreholes") go(`/models/${node.modelId}/boreholes`);
  if (node.type === "chemical_boreholes") go(`/models/${node.modelId}/chemical-boreholes`);
  if (node.type === "geochem_rules") go(`/models/${node.modelId}/geochem-workspace/rules`);
  if (node.type === "geochem_expert") go(`/models/${node.modelId}/geochem-workspace/expert`);
  if (node.type === "geochem_mining_variation") go(`/models/${node.modelId}/geochem-mining/variation`);
  if (node.type === "geochem_mining_correlation") go(`/models/${node.modelId}/geochem-mining/correlation`);
  if (node.type === "geobodies") go(`/models/${node.modelId}/geobodies`);
  if (node.type === "reconstruct_a") go(`/models/${node.modelId}/reconstruct/a`);
  if (node.type === "reconstruct_b") go(`/models/${node.modelId}/reconstruct/b`);
  if (node.type === "reconstruct_deep") go(`/models/${node.modelId}/reconstruct/deep`);
  if (node.type === "reconstruct_geochem") go(`/models/${node.modelId}/reconstruct/geochem`);
  if (node.type === "geochem_evidence") go(`/models/${node.modelId}/geochem-workspace/evidence`);
}

onMounted(reload);
</script>

<style scoped>
.side-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.tree-toolbar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 10px;
}

.all-btn {
  width: 100%;
  margin: 0;
}

.tree-scroll {
  flex: 1;
  overflow: auto;
  padding-right: 4px;
}

:deep(.el-tree) {
  background: transparent;
  color: #32455f;
}

:deep(.el-tree-node__content) {
  height: 34px;
  border-radius: 6px;
  margin: 3px 0;
  padding-right: 6px;
}

:deep(.el-tree-node__content:hover) {
  background: #f2f7ff;
}

:deep(.el-tree-node:focus > .el-tree-node__content),
:deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: #eaf3ff;
}

:deep(.el-tree-node__label) {
  color: #32455f;
  font-weight: 600;
}

:deep(.el-tree-node.is-current > .el-tree-node__content .el-tree-node__label) {
  color: #2b77ea;
}

:deep(.el-tree-node__expand-icon) {
  color: #8ca0bf;
}
</style>
