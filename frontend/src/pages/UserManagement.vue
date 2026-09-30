<template>
  <div class="page-shell">
    <div class="page-header">
      <div>
        <h2 class="page-title">用户管理</h2>
        <div class="page-subtitle">查看当前账户信息、修改自己的登录密码，并在管理员模式下重置其他用户密码。</div>
      </div>
    </div>

    <div class="user-grid">
      <div class="content-card">
        <div class="section-caption">当前用户</div>
        <div class="profile-grid">
          <div class="profile-item">
            <span class="profile-label">用户名</span>
            <strong>{{ currentUser?.username || "-" }}</strong>
          </div>
          <div class="profile-item">
            <span class="profile-label">角色</span>
            <strong>{{ currentUser?.is_admin ? "管理员" : "普通用户" }}</strong>
          </div>
          <div class="profile-item">
            <span class="profile-label">状态</span>
            <strong>{{ currentUser?.is_active ? "启用" : "停用" }}</strong>
          </div>
          <div class="profile-item">
            <span class="profile-label">创建时间</span>
            <strong>{{ currentUser ? fmt(currentUser.created_at) : "-" }}</strong>
          </div>
        </div>
      </div>

      <div class="content-card">
        <div class="section-caption">修改密码</div>
        <el-form :model="passwordForm" label-width="100px">
          <el-form-item label="当前密码">
            <el-input v-model="passwordForm.current_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="passwordForm.new_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input v-model="passwordForm.confirm_password" type="password" show-password />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="changingPassword" @click="changePassword">保存新密码</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <div v-if="currentUser?.is_admin" class="content-card">
      <div class="section-caption">系统用户</div>

      <el-table :data="users" border style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="username" label="用户名" min-width="160" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">{{ row.is_admin ? "管理员" : "普通用户" }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">{{ row.is_active ? "启用" : "停用" }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <div class="page-table-actions">
              <el-button size="small" type="primary" plain @click="openResetDialog(row)">重置密码</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="resetVisible" title="重置用户密码" width="520px">
      <div class="dialog-section">
        <div class="dialog-section-title">账号信息</div>
        <div class="reset-user">{{ resetUser?.username || "-" }}</div>
      </div>

      <div class="dialog-section">
        <div class="dialog-section-title">新密码</div>
        <el-form :model="resetForm" label-width="96px">
          <el-form-item label="新密码">
            <el-input v-model="resetForm.new_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input v-model="resetForm.confirm_password" type="password" show-password />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="resettingPassword" @click="resetPassword">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { http } from "../api/http";
import type { User } from "../api/types";

const currentUser = ref<User | null>(null);
const users = ref<User[]>([]);

const passwordForm = reactive({
  current_password: "",
  new_password: "",
  confirm_password: "",
});

const resetVisible = ref(false);
const resetUser = ref<User | null>(null);
const resetForm = reactive({
  new_password: "",
  confirm_password: "",
});

const changingPassword = ref(false);
const resettingPassword = ref(false);

function fmt(value: string) {
  return dayjs(value).format("YYYY-MM-DD HH:mm:ss");
}

async function loadMe() {
  const res = await http.get<User>("/api/auth/me");
  currentUser.value = res.data;
}

async function loadUsers() {
  if (!currentUser.value?.is_admin) {
    users.value = [];
    return;
  }
  const res = await http.get<User[]>("/api/users");
  users.value = res.data;
}

async function refresh() {
  await loadMe();
  await loadUsers();
}

async function changePassword() {
  if (passwordForm.new_password.length < 6) {
    ElMessage.error("新密码至少 6 位");
    return;
  }
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    ElMessage.error("两次输入的新密码不一致");
    return;
  }

  changingPassword.value = true;
  try {
    await http.post("/api/auth/change-password", {
      current_password: passwordForm.current_password,
      new_password: passwordForm.new_password,
    });
    ElMessage.success("密码修改成功，请使用新密码重新登录");
    localStorage.removeItem("token");
    window.location.href = "/login";
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || "修改密码失败");
  } finally {
    changingPassword.value = false;
  }
}

function openResetDialog(user: User) {
  resetUser.value = user;
  resetForm.new_password = "";
  resetForm.confirm_password = "";
  resetVisible.value = true;
}

async function resetPassword() {
  if (!resetUser.value) return;
  if (resetForm.new_password.length < 6) {
    ElMessage.error("新密码至少 6 位");
    return;
  }
  if (resetForm.new_password !== resetForm.confirm_password) {
    ElMessage.error("两次输入的新密码不一致");
    return;
  }

  resettingPassword.value = true;
  try {
    await http.post(`/api/users/${resetUser.value.id}/reset-password`, {
      new_password: resetForm.new_password,
    });
    ElMessage.success(`已重置用户 ${resetUser.value.username} 的密码`);
    resetVisible.value = false;
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || "重置密码失败");
  } finally {
    resettingPassword.value = false;
  }
}

onMounted(refresh);
</script>

<style scoped>
.user-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
  gap: 14px;
}

.profile-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.profile-item {
  padding: 14px 16px;
  border: 1px solid #e7edf6;
  border-radius: 8px;
  background: #fbfcfe;
}

.profile-item strong {
  display: block;
  margin-top: 6px;
  color: #273754;
  font-size: 15px;
}

.profile-label {
  color: #8091ab;
  font-size: 12px;
}

.reset-user {
  padding: 12px 14px;
  border-radius: 8px;
  background: #f7f9fc;
  border: 1px solid #e6edf6;
  font-weight: 700;
  color: #273754;
}

@media (max-width: 1100px) {
  .user-grid,
  .profile-grid {
    grid-template-columns: 1fr;
  }
}
</style>
