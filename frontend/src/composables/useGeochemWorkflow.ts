import { computed, onBeforeUnmount, ref } from "vue";
import { http } from "../api/http";

export type WorkflowStage =
  | "variation_pending"
  | "variation_running"
  | "variation_failed"
  | "clue_selection"
  | "correlation_running"
  | "correlation_failed"
  | "reconstruct_pending"
  | "reconstruction_running"
  | "reconstruction_failed"
  | "ready"
  | "ready_with_limits";

export interface GeochemWorkflow {
  id: string;
  workflow_id: string;
  model_id: number;
  dataset_hash: string;
  algorithm_version: string;
  status: string;
  stage: WorkflowStage;
  progress: number;
  variation_job_id?: string | null;
  correlation_job_id?: string | null;
  reconstruct_job_id?: string | null;
  reconstruction_error?: string | null;
  selected_clue_id?: string | null;
  limitations: string[];
  clue_counts: { variation: number; correlation: number };
  reconstruction_elements: string[];
  reconstruction_element_options: Array<{
    element: string;
    available: boolean;
    reason?: string | null;
  }>;
  data_scope: {
    project_drillhole_count: number;
    chemical_drillhole_count: number;
    missing_chemical_drillhole_count: number;
    assay_count: number;
    element_count: number;
    elements: string[];
  };
  rule_set: {
    id: string;
    status: string;
    name?: string;
    version?: string;
    background_method?: string | null;
    background_scope?: string | null;
  };
}

export function useGeochemWorkflow(modelId: string | number) {
  const workflow = ref<GeochemWorkflow | null>(null);
  const loading = ref(false);
  const creating = ref(false);
  const error = ref("");
  let timer: number | undefined;

  const running = computed(() => workflow.value?.status === "running");

  async function loadLatest() {
    loading.value = true;
    error.value = "";
    try {
      const response = await http.get(`/api/models/${modelId}/geochem-workflows/latest`);
      workflow.value = response.data || null;
      schedule();
      return workflow.value;
    } catch (reason: any) {
      error.value = reason?.response?.data?.detail || reason?.message || "读取分析工作流失败";
      throw reason;
    } finally {
      loading.value = false;
    }
  }

  async function create() {
    creating.value = true;
    error.value = "";
    try {
      const response = await http.post(`/api/models/${modelId}/geochem-workflows`, {
        client_request_id: `ui-${modelId}-${Date.now()}`,
      });
      workflow.value = response.data;
      await http.post(`/api/geochem-workflows/${workflow.value!.id}/actions/run-quality-check`);
      await refresh();
      return workflow.value;
    } catch (reason: any) {
      error.value = reason?.response?.data?.detail || reason?.message || "创建分析工作流失败";
      throw reason;
    } finally {
      creating.value = false;
    }
  }

  async function refresh() {
    if (!workflow.value?.id) return loadLatest();
    const response = await http.get(`/api/geochem-workflows/${workflow.value.id}/status`);
    workflow.value = response.data;
    schedule();
    return workflow.value;
  }

  function schedule() {
    if (timer) window.clearTimeout(timer);
    timer = undefined;
    if (workflow.value?.status === "running") {
      timer = window.setTimeout(() => void refresh(), 1400);
    }
  }

  onBeforeUnmount(() => {
    if (timer) window.clearTimeout(timer);
  });

  return { workflow, loading, creating, error, running, loadLatest, create, refresh };
}
