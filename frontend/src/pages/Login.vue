<template>
  <div class="login-shell">
    <div class="login-hero">
      <div class="hero-badge">Geo Admin</div>
      <h1>地质模型管理平台</h1>
      <p>面向工程场景的模型、钻孔、地质体与重建作业统一管理界面。</p>
      <ul class="hero-points">
        <li>统一浏览模型树与业务数据</li>
        <li>表格、分层、重建流程一体化操作</li>
        <li>延续参考系统的蓝白工业风工作台</li>
      </ul>
    </div>

    <div class="login-panel">
      <div class="panel-title">欢迎登录</div>
      <div class="panel-subtitle">请输入账号密码进入工作台</div>

      <el-form :model="form" label-position="top" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="form.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="loading" @click="submit">
          登录系统
        </el-button>
      </el-form>

      <div class="tip">
        默认账号：`admin / admin123`，可在 `backend/.env` 中修改。<br />
        还没有账号？<el-link type="primary" @click="goRegister">立即注册</el-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import { clearReconstructSessionState } from "../utils/reconstructState";
import { clearCurrentUser, loadCurrentUser } from "../utils/session";

const router = useRouter();
const loading = ref(false);

const form = reactive({
  username: "admin",
  password: "admin123",
});

function goRegister() {
  router.push("/register");
}

async function submit() {
  loading.value = true;
  try {
    const body = new URLSearchParams();
    body.append("username", form.username);
    body.append("password", form.password);

    const res = await http.post("/api/auth/login", body, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });

    clearReconstructSessionState();
    clearCurrentUser();
    localStorage.setItem("token", res.data.access_token);
    await loadCurrentUser(true);
    router.push("/models");
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) 420px;
  background:
    radial-gradient(circle at left top, rgba(77, 142, 242, 0.18), transparent 34%),
    linear-gradient(135deg, #eef5ff 0%, #f7f9fc 48%, #ffffff 100%);
}

.login-hero {
  padding: 72px 72px 56px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  background:
    linear-gradient(180deg, rgba(50, 115, 233, 0.9) 0%, rgba(41, 98, 220, 0.82) 100%),
    linear-gradient(120deg, #2e78ee, #2f67d8);
  color: #ffffff;
}

.hero-badge {
  display: inline-flex;
  align-self: flex-start;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.18);
  font-size: 12px;
  font-weight: 700;
}

.login-hero h1 {
  margin: 22px 0 12px;
  font-size: 42px;
  line-height: 1.18;
}

.login-hero p {
  max-width: 560px;
  margin: 0 0 28px;
  font-size: 16px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.82);
}

.hero-points {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
  max-width: 460px;
}

.hero-points li {
  padding: 14px 16px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.14);
  font-size: 14px;
}

.login-panel {
  align-self: center;
  margin: 24px;
  padding: 30px 28px 24px;
  border: 1px solid #e3ebf6;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 28px 60px -42px rgba(28, 66, 130, 0.55);
}

.panel-title {
  font-size: 28px;
  font-weight: 700;
  color: #253652;
}

.panel-subtitle {
  margin: 8px 0 20px;
  color: #7b8ea9;
  font-size: 13px;
}

.submit-btn {
  width: 100%;
  height: 42px;
  margin-top: 6px;
}

.tip {
  margin-top: 14px;
  color: #7386a3;
  font-size: 12px;
  line-height: 1.8;
}

@media (max-width: 1080px) {
  .login-shell {
    grid-template-columns: 1fr;
  }

  .login-hero {
    padding: 40px 24px 28px;
  }

  .login-panel {
    margin: 0 24px 24px;
  }
}
</style>
