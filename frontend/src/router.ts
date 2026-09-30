import { createRouter, createWebHistory } from "vue-router";
import AdminLayout from "./layout/AdminLayout.vue";
import Login from "./pages/Login.vue";
import Register from "./pages/Register.vue";
import Models from "./pages/Models.vue";
import ModelBoreholes from "./pages/ModelBoreholes.vue";
import ModelChemicalBoreholes from "./pages/ModelChemicalBoreholes.vue";
import ModelGeobodies from "./pages/ModelGeobodies.vue";
import BoreholeSections from "./pages/BoreholeSections.vue";
import ModelReconstruct from "./pages/ModelReconstruct.vue";
import ModelReconstructB from "./pages/ModelReconstructB.vue";
import ModelReconstructDeep from "./pages/ModelReconstructDeep.vue";
import ModelGeochemReconstruct from "./pages/ModelGeochemReconstruct.vue";
import ModelElementVariation from "./pages/ModelElementVariation.vue";
import ModelElementCorrelation from "./pages/ModelElementCorrelation.vue";
import ModelGeochemRules from "./pages/ModelGeochemRules.vue";
import ModelExpertGeochem from "./pages/ModelExpertGeochem.vue";
import ModelGeochemEvidence from "./pages/ModelGeochemEvidence.vue";
import UserManagement from "./pages/UserManagement.vue";

function hasToken() {
  return !!localStorage.getItem("token");
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: Login },
    { path: "/register", component: Register },
    {
      path: "/",
      component: AdminLayout,
      children: [
        { path: "", redirect: "/models" },
        { path: "models", component: Models },
        { path: "models/:modelId/boreholes", component: ModelBoreholes, props: true },
        { path: "models/:modelId/chemical-boreholes", component: ModelChemicalBoreholes, props: true },
        { path: "models/:modelId/geochem-mining", redirect: (to) => `/models/${to.params.modelId}/geochem-mining/variation` },
        { path: "models/:modelId/geochem-workspace", redirect: (to) => `/models/${to.params.modelId}/geochem-workspace/rules` },
        { path: "models/:modelId/geochem-workspace/rules", component: ModelGeochemRules, props: true },
        { path: "models/:modelId/geochem-workspace/expert", component: ModelExpertGeochem, props: true },
        { path: "models/:modelId/geochem-workspace/evidence", component: ModelGeochemEvidence, props: true },
        {
          path: "models/:modelId/geochem-mining/variation",
          component: ModelElementVariation,
          props: true,
        },
        {
          path: "models/:modelId/geochem-mining/correlation",
          component: ModelElementCorrelation,
          props: true,
        },
        { path: "models/:modelId/geobodies", component: ModelGeobodies, props: true },
        { path: "models/:modelId/reconstruct", redirect: (to) => `/models/${to.params.modelId}/reconstruct/a` },
        { path: "models/:modelId/reconstruct/a", component: ModelReconstruct, props: true },
        { path: "models/:modelId/reconstruct/b", component: ModelReconstructB, props: true },
        { path: "models/:modelId/reconstruct/deep", component: ModelReconstructDeep, props: true },
        { path: "models/:modelId/reconstruct/geochem", component: ModelGeochemReconstruct, props: true },
        { path: "boreholes/:boreholeId/sections", component: BoreholeSections, props: true },
        { path: "users", component: UserManagement },
      ],
    },
  ],
});

router.beforeEach((to) => {
  if (to.path !== "/login" && to.path !== "/register" && !hasToken()) {
    return "/login";
  }
  if ((to.path === "/login" || to.path === "/register") && hasToken()) {
    return "/models";
  }
});

export default router;
