<template>
  <div class="wrap">
    <el-card class="card">
      <div class="badge">Create Account</div>
      <div class="h1">注册账号</div>
      <div class="subtitle">创建后即可使用登录页进入系统</div>

      <el-form :model="form" label-width="90px" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="form.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="form.confirmPassword" type="password" show-password />
        </el-form-item>

        <el-button type="primary" style="width: 100%" @click="submit" :loading="loading">
          注册
        </el-button>
      </el-form>

      <div class="tip">
        已有账号？
        <el-link type="primary" @click="goLogin">去登录</el-link>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { http } from "../api/http";

const router = useRouter();
const loading = ref(false);

const form = reactive({
  username: "",
  password: "",
  confirmPassword: "",
});

function goLogin() {
  router.push("/login");
}

async function submit() {
  const username = form.username.trim();
  if (username.length < 3) {
    ElMessage.error("用户名至少 3 位");
    return;
  }
  if (form.password.length < 6) {
    ElMessage.error("密码至少 6 位");
    return;
  }
  if (form.password !== form.confirmPassword) {
    ElMessage.error("两次输入的密码不一致");
    return;
  }

  loading.value = true;
  try {
    await http.post("/api/auth/register", {
      username,
      password: form.password,
    });
    ElMessage.success("注册成功，请登录");
    router.push("/login");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || "注册失败");
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.wrap {
  height: 100vh;
  display: grid;
  place-items: center;
  background: linear-gradient(150deg, #e7f2ff 0%, #f9fcff 58%, #fff2df 100%);
}
.card {
  width: 460px;
  border-radius: 20px;
  border: 1px solid #e2eaf3;
  box-shadow: 0 26px 50px -35px rgba(25, 59, 91, 0.65);
}
.badge {
  display: inline-block;
  margin-bottom: 10px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: #286f80;
  background: #e2f1f5;
}
.h1 {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 4px;
}
.subtitle {
  font-size: 13px;
  color: #607389;
  margin-bottom: 14px;
}
.tip {
  margin-top: 12px;
  font-size: 12px;
  opacity: 0.8;
  line-height: 1.7;
}
</style>
