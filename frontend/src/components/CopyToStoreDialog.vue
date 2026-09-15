<template>
  <el-dialog
    :model-value="modelValue"
    title="复制到门店"
    width="440px"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="hint">{{ hint || defaultHint }}</p>
    <el-form label-width="100px">
      <el-form-item label="目标门店" required>
        <el-select v-model="targetStoreId" placeholder="选择门店" style="width:100%" filterable>
          <el-option
            v-for="s in targetStores"
            :key="s.id"
            :label="s.name"
            :value="s.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="showProductConflict" label="编号冲突">
        <el-radio-group v-model="onConflict">
          <el-radio value="skip">跳过</el-radio>
          <el-radio value="rename">自动改编号</el-radio>
        </el-radio-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">开始复制</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    stores: Array<{ id: number; name: string }>
    excludeStoreId?: number | null
    selectedCount: number
    categoryIds?: number[]
    optionGroupIds?: number[]
    productIds?: number[]
    showProductConflict?: boolean
    cloneSharedOptionGroups?: boolean
    hint?: string
  }>(),
  {
    categoryIds: () => [],
    optionGroupIds: () => [],
    productIds: () => [],
    showProductConflict: false,
    cloneSharedOptionGroups: false,
    excludeStoreId: null,
    hint: '',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'done', result: any): void
}>()

const loading = ref(false)
const targetStoreId = ref<number | undefined>()
const onConflict = ref<'skip' | 'rename'>('skip')

const defaultHint = computed(
  () =>
    `已选 ${props.selectedCount} 项，将在目标店新建副本（同名分类 / 同编码门店规格组会复用）。`,
)

const targetStores = computed(() => {
  const ex = props.excludeStoreId
  if (!ex) return props.stores
  return props.stores.filter((s) => Number(s.id) !== Number(ex))
})

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      targetStoreId.value = targetStores.value[0]?.id
      onConflict.value = 'skip'
    }
  },
)

async function submit() {
  if (!targetStoreId.value) {
    ElMessage.warning('请选择目标门店')
    return
  }
  if (!props.selectedCount) {
    ElMessage.warning('请先勾选要复制的项')
    return
  }
  loading.value = true
  try {
    const res = await api.post('/menu/copy-to-store', {
      target_store_id: targetStoreId.value,
      category_ids: props.categoryIds,
      option_group_ids: props.optionGroupIds,
      product_ids: props.productIds,
      on_product_conflict: onConflict.value,
      clone_shared_option_groups: props.cloneSharedOptionGroups,
    })
    const d = res.data || {}
    const bits = [
      `分类 +${d.categories?.created || 0}/复用${d.categories?.reused || 0}`,
      `规格 +${d.option_groups?.created || 0}/复用${d.option_groups?.reused || 0}` +
        (d.option_groups?.cloned_shared
          ? `（含共享克隆${d.option_groups.cloned_shared}）`
          : d.option_groups?.skipped_shared
            ? `（共享沿用${d.option_groups.skipped_shared}）`
            : ''),
      `商品 +${d.products?.created || 0}/跳过${d.products?.skipped || 0}`,
    ]
    ElMessage.success(`复制完成：${bits.join('；')}`)
    if ((d.warnings || []).length) {
      ElMessage.warning((d.warnings as string[]).slice(0, 3).join('；'))
    }
    emit('done', d)
    emit('update:modelValue', false)
  } catch (e: any) {
    ElMessage.error(e?.message || '复制失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.hint { margin: 0 0 12px; font-size: 13px; color: #606266; line-height: 1.5; }
</style>
