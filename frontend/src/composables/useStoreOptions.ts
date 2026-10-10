import { ref } from 'vue'
import api from '@/api'

/** 门店下拉选项通用结构（settings 与 kitchen 两个端点均返回 { id, name }） */
export interface StoreOption {
  id: number
  name: string
  store_no?: string
}

export interface UseStoreOptionsOptions {
  /** 门店选项来源：settings = /settings/stores/options（默认，管理端）；kitchen = /orders/kitchen/stores（POS 端） */
  source?: 'settings' | 'kitchen'
  /** 是否包含已停用门店（仅 settings 源生效，透传 include_inactive=true） */
  includeDisabled?: boolean
  /** 请求失败时是否清空已加载选项（默认 true，与多数列表页现状一致） */
  clearOnError?: boolean
  /** 请求失败时向调用方抛出异常（对应原本无 try/catch、错误向上传播的视图） */
  rethrow?: boolean
  /** 请求失败的自定义提示回调（如 ElMessage），默认静默 */
  onError?: (e: unknown) => void
}

/**
 * 「加载门店下拉选项」公共逻辑：统一请求端点、解析响应（{ items: [...] }）与错误处理。
 * loadStores 返回是否加载成功，便于调用方保持"仅成功后执行"的联动逻辑；
 * rethrow 为 true 时失败会向上抛出，返回值恒为 true。
 */
export function useStoreOptions(options: UseStoreOptionsOptions = {}) {
  const { source = 'settings', includeDisabled = false, clearOnError = true, rethrow = false, onError } = options
  const storeOptions = ref<StoreOption[]>([])
  const storeOptionsLoading = ref(false)

  async function loadStores(): Promise<boolean> {
    storeOptionsLoading.value = true
    try {
      const url = source === 'kitchen' ? '/orders/kitchen/stores' : '/settings/stores/options'
      const config = source === 'settings' && includeDisabled ? { params: { include_inactive: true } } : undefined
      const res = await api.get(url, config)
      storeOptions.value = res.data?.items || []
      return true
    } catch (e) {
      if (clearOnError) storeOptions.value = []
      onError?.(e)
      if (rethrow) throw e
      return false
    } finally {
      storeOptionsLoading.value = false
    }
  }

  return { storeOptions, storeOptionsLoading, loadStores }
}
