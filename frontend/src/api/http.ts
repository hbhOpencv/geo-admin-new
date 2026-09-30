import axios from "axios";
import { clearReconstructSessionState } from "../utils/reconstructState";
import { clearCurrentUser } from "../utils/session";

export const http = axios.create({
  baseURL: "",
  timeout: 30000,
});

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers = config.headers ?? {};
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err?.response?.status === 401) {
      clearReconstructSessionState();
      clearCurrentUser();
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);
