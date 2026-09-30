import { readonly, ref } from "vue";
import { http } from "../api/http";
import type { User } from "../api/types";

const currentUser = ref<User | null>(null);
const loaded = ref(false);

export function useCurrentUser() {
  return {
    currentUser: readonly(currentUser),
    loaded: readonly(loaded),
  };
}

export async function loadCurrentUser(force = false) {
  if (loaded.value && !force) return currentUser.value;
  const token = localStorage.getItem("token");
  if (!token) {
    currentUser.value = null;
    loaded.value = false;
    return null;
  }

  const res = await http.get<User>("/api/auth/me");
  currentUser.value = res.data;
  loaded.value = true;
  return currentUser.value;
}

export function clearCurrentUser() {
  currentUser.value = null;
  loaded.value = false;
}
