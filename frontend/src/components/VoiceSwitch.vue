<template>
  <div class="voice-toggle" :class="{ off: !modelValue }">
    <span class="voice-toggle-label">语音提示</span>
    <el-switch
      :model-value="modelValue"
      inline-prompt
      active-text="开"
      inactive-text="关"
      @update:model-value="$emit('update:modelValue', $event)"
    />
  </div>
</template>

<script setup lang="ts">
defineProps<{ modelValue: boolean }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<style scoped>
.voice-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(82, 196, 26, 0.12);
  border: 1px solid rgba(82, 196, 26, 0.35);
}
.voice-toggle.off {
  background: rgba(100, 116, 139, 0.15);
  border-color: rgba(100, 116, 139, 0.35);
}
.voice-toggle-label {
  font-size: 14px;
  font-weight: 600;
  color: #86efac;
  white-space: nowrap;
}
.voice-toggle.off .voice-toggle-label {
  color: #94a3b8;
}
</style>
