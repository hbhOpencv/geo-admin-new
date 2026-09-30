<template><ModelGeochemMining :model-id="modelId" algorithm-type="correlation" /></template>
<script setup lang="ts">
import ModelGeochemMining from "./ModelGeochemMining.vue";
defineProps<{ modelId: string }>();
</script>
