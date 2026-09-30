<template>
  <div class="result-empty">
    <strong>当前没有可展示结果</strong>
    <p>{{ description }}</p>
    <el-button v-if="showLoadLatest" type="primary" plain @click="emit('loadLatest')">加载最新完整结果</el-button>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ description:string; showLoadLatest?:boolean }>(), { showLoadLatest:true });
const emit = defineEmits<{ loadLatest: [] }>();
</script>

<style scoped>
.result-empty{min-height:150px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:24px;text-align:center;color:#66788f;background:#fafbfd;border:1px dashed #dce4ef}
.result-empty strong{font-size:15px;color:#344760}.result-empty p{max-width:720px;margin:0;line-height:1.7;font-size:13px}
</style>
