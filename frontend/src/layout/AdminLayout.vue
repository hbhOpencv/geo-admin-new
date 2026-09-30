<template>
  <div class="admin-shell">
    <header class="topbar">
      <div class="topbar-left">
        <div class="brand-mark">G</div>
        <div class="brand-copy">
          <div class="brand-title">地质模型管理系统</div>
          <div class="brand-subtitle">Geo Admin Workspace</div>
        </div>

        <nav class="topbar-nav">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: item.active }"
            type="button"
            @click="go(item.path)"
          >
            {{ item.label }}
          </button>
        </nav>
      </div>

      <div class="topbar-right">
        <div class="topbar-meta">
          <span class="topbar-meta-label">当前模块</span>
          <strong>{{ currentNavLabel }}</strong>
        </div>
        <div class="avatar-chip">
          <span class="avatar-dot"></span>
          <span>{{ currentUser?.username || "用户" }}</span>
        </div>
        <el-button class="logout-btn" @click="logout">退出登录</el-button>
      </div>
    </header>

    <div class="workspace-head">
      <div class="crumbs">
        <span>首页</span>
        <span>/</span>
        <span>{{ currentNavLabel }}</span>
        <template v-if="currentPageLabel">
          <span>/</span>
          <span>{{ currentPageLabel }}</span>
        </template>
      </div>

      <div class="workspace-tabs">
        <button
          v-for="tab in visibleTabs"
          :key="tab.path"
          class="workspace-tab"
          :class="{ active: route.path === tab.path }"
          type="button"
          @click="go(tab.path)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <div class="workspace-body">
      <aside class="sidebar-panel">
        <div class="sidebar-panel-head">
        <div class="sidebar-icon">⌘</div>
        <div>
            <div class="sidebar-title">模型树导航</div>
            <div class="sidebar-subtitle">德达隧道模型与快捷入口</div>
        </div>
      </div>

        <div class="sidebar-panel-body">
          <SideTree />
        </div>
      </aside>

      <main class="page-panel">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import SideTree from "./SideTree.vue";
import { clearReconstructSessionState } from "../utils/reconstructState";
import { clearCurrentUser, loadCurrentUser, useCurrentUser } from "../utils/session";

const route = useRoute();
const router = useRouter();
const { currentUser } = useCurrentUser();

const NAV_CONFIG = [
  {
    key: "models",
    label: "模型管理",
    match: (path: string) =>
      path.startsWith("/models") || path.includes("/reconstruct") || /\/boreholes\/\d+\/sections/.test(path),
    tabs: computedModelTabs,
  },
  {
    key: "users",
    label: "用户管理",
    match: (path: string) => path.startsWith("/users"),
    tabs: [{ label: "用户管理", path: "/users" }],
  },
];

function extractModelId(path: string) {
  const match = path.match(/\/models\/(\d+)/);
  return match?.[1] ?? "";
}

function computedTabsForModel(path: string) {
  const modelId = extractModelId(path);
  if (!modelId) return [{ label: "模型列表", path: "/models" }];

  return [
    { label: "模型列表", path: "/models" },
    { label: "钻孔管理", path: `/models/${modelId}/boreholes` },
    { label: "化学钻孔", path: `/models/${modelId}/chemical-boreholes` },
    { label: "地质体管理", path: `/models/${modelId}/geobodies` },
  ];
}

function computedTabsForReconstruct(path: string) {
  const modelId = extractModelId(path);
  if (!modelId) return [{ label: "模型列表", path: "/models" }];

  return [
    { label: "模型列表", path: "/models" },
    { label: "算法 A", path: `/models/${modelId}/reconstruct/a` },
    { label: "算法 B", path: `/models/${modelId}/reconstruct/b` },
    { label: "算法 C", path: `/models/${modelId}/reconstruct/deep` },
    { label: "化学元素重建", path: `/models/${modelId}/reconstruct/geochem` },
  ];
}

function computedTabsForGeochemMining(path: string) {
  const modelId = extractModelId(path);
  if (!modelId) return [{ label: "模型列表", path: "/models" }];

  return [
    { label: "模型列表", path: "/models" },
    { label: "化学元素变化规律", path: `/models/${modelId}/geochem-mining/variation` },
    { label: "化学元素相关性", path: `/models/${modelId}/geochem-mining/correlation` },
    { label: "化学元素三维验证", path: `/models/${modelId}/reconstruct/geochem` },
  ];
}

function computedModelTabs(path: string) {
  if (
    /\/models\/\d+\/boreholes/.test(path) ||
    /\/models\/\d+\/chemical-boreholes/.test(path) ||
    /\/models\/\d+\/geobodies/.test(path)
  ) {
    return computedTabsForModel(path);
  }
  if (
    /\/models\/\d+\/geochem-mining\//.test(path) ||
    /\/models\/\d+\/reconstruct\/geochem$/.test(path)
  ) {
    return computedTabsForGeochemMining(path);
  }
  if (/\/models\/\d+\/reconstruct\//.test(path)) {
    return computedTabsForReconstruct(path);
  }
  return [{ label: "模型列表", path: "/models" }];
}

const currentNav = computed(() => {
  return NAV_CONFIG.find((item) => item.match(route.path)) ?? NAV_CONFIG[0];
});

const navItems = computed(() => {
  return NAV_CONFIG.map((item) => {
    const tabs = typeof item.tabs === "function" ? item.tabs(route.path) : item.tabs;
    const target = tabs[0]?.path ?? "/models";
    return {
      key: item.key,
      label: item.label,
      path: target,
      active: currentNav.value.key === item.key,
    };
  });
});

const visibleTabs = computed(() => {
  const tabs = currentNav.value.tabs;
  return typeof tabs === "function" ? tabs(route.path) : tabs;
});

const currentNavLabel = computed(() => currentNav.value.label);

const currentPageLabel = computed(() => {
  const path = route.path;
  if (path === "/models") return "列表";
  if (path.includes("/chemical-boreholes")) return "化学钻孔";
  if (path.includes("/boreholes") && path.includes("/models/")) return "钻孔列表";
  if (path.includes("/geobodies")) return "地质体列表";
  if (path.includes("/sections")) return "分层信息";
  if (path.endsWith("/reconstruct/a")) return "算法 A";
  if (path.endsWith("/reconstruct/b")) return "算法 B";
  if (path.endsWith("/reconstruct/deep")) return "算法 C";
  if (path.endsWith("/reconstruct/geochem")) return "化学元素重建";
  if (path.startsWith("/users")) return "账号设置";
  return "";
});

function go(path: string) {
  if (path && path !== route.path) {
    router.push(path);
  }
}

function logout() {
  clearReconstructSessionState();
  clearCurrentUser();
  localStorage.removeItem("token");
  window.location.href = "/login";
}

onMounted(() => {
  loadCurrentUser().catch(() => undefined);
});
</script>

<style scoped>
.admin-shell {
  min-height: 100vh;
  width: 100%;
  max-width: 100%;
  overflow-x: clip;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, #eef5ff 0, #f7f9fc 132px, #f7f9fc 100%);
}

.topbar {
  width: 100%;
  max-width: 100%;
  height: 64px;
  padding: 0 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: linear-gradient(90deg, #2c78e8 0%, #3b86f0 48%, #4c8df2 100%);
  color: #fff;
  box-shadow: 0 8px 18px -12px rgba(28, 79, 168, 0.55);
}

.topbar-left,
.topbar-right,
.brand-copy,
.topbar-meta {
  display: flex;
  align-items: center;
}

.topbar-left {
  gap: 14px;
  min-width: 0;
}

.topbar-right {
  gap: 12px;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.22);
  display: grid;
  place-items: center;
  font-weight: 700;
}

.brand-copy {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.brand-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
  line-height: 1;
}

.brand-subtitle,
.topbar-meta-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.74);
}

.topbar-nav {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 14px;
  padding-left: 14px;
  border-left: 1px solid rgba(255, 255, 255, 0.22);
}

.nav-item,
.workspace-tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  transition: all 0.18s ease;
}

.nav-item {
  padding: 8px 12px;
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.84);
  font-size: 13px;
  font-weight: 600;
}

.nav-item.active,
.nav-item:hover {
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
}

.topbar-meta {
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  min-width: 90px;
}

.avatar-chip {
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
}

.avatar-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #89f0b7;
  box-shadow: 0 0 0 3px rgba(137, 240, 183, 0.18);
}

.logout-btn {
  --el-button-text-color: #ffffff;
  --el-button-bg-color: transparent;
  --el-button-border-color: rgba(255, 255, 255, 0.28);
  --el-button-hover-bg-color: rgba(255, 255, 255, 0.14);
  --el-button-hover-text-color: #ffffff;
}

.workspace-head {
  width: 100%;
  max-width: 100%;
  padding: 12px 24px 0;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #7d8ba3;
  font-size: 12px;
  margin-bottom: 10px;
}

.workspace-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
}

.workspace-tab {
  padding: 10px 16px;
  border-radius: 10px 10px 0 0;
  background: #edf3ff;
  color: #5d6f8e;
  font-size: 13px;
  font-weight: 600;
}

.workspace-tab.active {
  background: #ffffff;
  color: #2c78e8;
  box-shadow: 0 -1px 0 #dfe8f5, 0 0 0 1px #dfe8f5;
}

.workspace-body {
  width: 100%;
  max-width: 100%;
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  gap: 16px;
  padding: 0 24px 24px;
}

.sidebar-panel,
.page-panel {
  min-height: 0;
  background: #fff;
  border: 1px solid #e4ebf5;
  box-shadow: 0 10px 24px -20px rgba(34, 67, 117, 0.35);
}

.sidebar-panel {
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-panel-head {
  padding: 18px 16px 14px;
  border-bottom: 1px solid #edf2f8;
  display: flex;
  align-items: center;
  gap: 12px;
}

.sidebar-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(180deg, #19d1ad 0%, #10b8a2 100%);
  color: #fff;
  font-size: 14px;
}

.sidebar-title {
  font-size: 16px;
  font-weight: 700;
  color: #2a3a52;
}

.sidebar-subtitle {
  margin-top: 3px;
  font-size: 12px;
  color: #8a9ab1;
}

.sidebar-panel-body {
  flex: 1;
  min-height: 0;
  padding: 12px;
}

.page-panel {
  border-radius: 14px;
  overflow: auto;
  padding: 18px 18px 20px;
}

@media (max-width: 1180px) {
  .topbar {
    height: auto;
    padding: 14px 18px;
    flex-direction: column;
    align-items: flex-start;
  }

  .topbar-left,
  .topbar-right {
    width: 100%;
    flex-wrap: wrap;
  }

  .topbar-nav {
    margin-left: 0;
    padding-left: 0;
    border-left: 0;
  }

  .workspace-head,
  .workspace-body {
    padding-left: 18px;
    padding-right: 18px;
  }

  .workspace-body {
    grid-template-columns: 1fr;
  }
}
</style>
