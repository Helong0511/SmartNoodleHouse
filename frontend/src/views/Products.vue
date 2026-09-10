<template>
  <div v-loading="loading">
    <el-form inline class="toolbar">
      <el-form-item v-if="auth.isSuperAdmin">
        <el-select
          v-model="storeFilter"
          placeholder="全部门店"
          clearable
          style="width:160px"
          @change="onStoreFilterChange"
        >
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-tag v-else-if="boundStoreName" type="info" style="margin-right:12px">门店：{{ boundStoreName }}</el-tag>
      <el-form-item>
        <el-input v-model="keyword" placeholder="搜索商品名" clearable @keyup.enter="reload" />
      </el-form-item>
      <el-form-item>
        <el-select v-model="statusFilter" placeholder="状态" style="width:120px" @change="reload">
          <el-option label="全部" value="ALL" />
          <el-option label="在售" value="ON_SALE" />
          <el-option label="下架" value="OFF_SALE" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-select v-model="categoryFilter" placeholder="分类" clearable style="width:140px" @change="reload">
          <el-option v-for="c in filterCategories" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-button type="primary" @click="openForm()">新增商品</el-button>
      <el-button
        v-if="auth.isSuperAdmin"
        type="success"
        plain
        :disabled="!selectedIds.length"
        @click="openCopy"
      >复制到门店</el-button>
      <el-button @click="load">刷新</el-button>
    </el-form>

    <el-table :data="products" border row-key="id" @selection-change="onSelectionChange">
      <el-table-column v-if="auth.isSuperAdmin" type="selection" width="48" />
      <el-table-column prop="product_no" label="编号" min-width="150" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" width="160" show-overflow-tooltip />
      <el-table-column v-if="auth.isSuperAdmin" prop="store_name" label="门店" min-width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ row.store_name || (row.store_id ? `门店#${row.store_id}` : '—') }}</template>
      </el-table-column>
      <el-table-column label="图片" width="72">
        <template #default="{ row }">
          <el-image
            v-if="row.main_image"
            :src="mediaUrl(row.main_image)"
            fit="cover"
            class="thumb"
            :preview-src-list="[mediaUrl(row.main_image)]"
          />
          <span v-else class="no-img">无</span>
        </template>
      </el-table-column>
      <el-table-column label="分类" width="120">
        <template #default="{ row }">{{ categoryName(row.category_id) }}</template>
      </el-table-column>
      <el-table-column prop="price" label="价格" width="100" />
      <el-table-column prop="box_price" label="餐盒费" width="90" />
      <el-table-column prop="stock" label="库存" width="100" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-select
            :model-value="row.status"
            size="small"
            class="status-select"
            @change="(v: string) => changeStatus(row, v)"
          >
            <el-option label="在售" value="ON_SALE" />
            <el-option label="下架" value="OFF_SALE" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column prop="sales_count" label="销量" width="100" />
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top:16px"
      @current-change="load"
    />

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑商品' : '新增商品'" width="640px" destroy-on-close>
      <el-form :model="form" label-width="100px">
        <el-form-item v-if="!form.id && auth.isSuperAdmin" label="门店">
          <el-select
            v-model="form.store_id"
            clearable
            placeholder="不选=总部前缀 0000（编号全店统一风格）"
            style="width:100%"
            @change="onFormStoreChange"
          >
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <div class="hint">不选门店：编号为 0000-分类-商品；商品归属所选分类所在门店，可用「复制到门店」铺开</div>
        </el-form-item>
        <el-form-item v-else-if="form.id || !auth.isSuperAdmin" label="门店">
          <span>{{ form.store_name || boundStoreName || (form.store_id ? `门店#${form.store_id}` : '—') }}</span>
        </el-form-item>
        <el-form-item label="分类" required>
          <el-select
            v-model="form.category_id"
            style="width:100%"
            placeholder="请先选择分类"
            filterable
            @change="onFormCategoryChange"
          >
            <el-option
              v-for="c in formCategories"
              :key="c.id"
              :label="categoryOptionLabel(c)"
              :value="c.id"
            />
          </el-select>
          <p v-if="!formCategories.length" class="hint warn">暂无分类，请先到「分类管理」创建</p>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如 牛肉面" @input="onProductNameInput" />
        </el-form-item>
        <el-form-item label="商品编号" required>
          <el-input
            v-model="form.product_no"
            :disabled="!!form.id"
            :placeholder="productNoPlaceholder"
            @input="codeTouched = true"
          />
          <div class="hint">{{ productNoTip }}</div>
        </el-form-item>
        <el-form-item label="预览图">
          <div class="img-row">
            <el-image
              v-if="form.main_image"
              :src="mediaUrl(form.main_image)"
              fit="cover"
              class="preview"
              :preview-src-list="[mediaUrl(form.main_image)]"
            />
            <div v-else class="preview empty">暂无图片</div>
            <div class="img-actions">
              <el-upload
                :show-file-list="false"
                accept="image/jpeg,image/png,image/webp,image/gif"
                :http-request="uploadImage"
                :disabled="!form.id || uploading"
              >
                <el-button type="primary" plain :loading="uploading" :disabled="!form.id">
                  {{ form.id ? '上传图片' : '请先保存商品' }}
                </el-button>
              </el-upload>
              <el-button v-if="form.main_image" link type="danger" @click="clearImage">清除</el-button>
              <p class="hint">建议 1:1、1200×1200（最低 800×800），小程序按屏幕自适应</p>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="须先点">
          <div class="require-box">
            <div class="require-row">
              <span class="require-label">分类</span>
              <el-select
                v-model="form.require_category_ids"
                multiple
                clearable
                filterable
                style="flex:1"
                placeholder="如：面（满足任一即可）"
                @change="onRequireCategoriesChange"
              >
                <el-option
                  v-for="c in requireCategoryOptions"
                  :key="c.id"
                  :label="c.name"
                  :value="c.id"
                />
              </el-select>
            </div>
            <div class="require-row">
              <span class="require-label">商品</span>
              <el-select
                v-model="form.require_product_ids"
                multiple
                clearable
                filterable
                style="flex:1"
                :placeholder="form.require_category_ids.length ? '仅显示已选分类下的商品' : '指定商品（可先选分类缩小范围）'"
              >
                <el-option-group
                  v-for="g in requireProductGroups"
                  :key="g.id"
                  :label="g.name"
                >
                  <el-option
                    v-for="p in g.products"
                    :key="p.id"
                    :label="p.name"
                    :value="p.id"
                    :disabled="p.id === form.id"
                  />
                </el-option-group>
              </el-select>
            </div>
            <div class="require-actions">
              <el-button link type="primary" @click="applyCategoryRequireDefault">应用分类默认</el-button>
              <span v-if="form.require_inherited" class="hint">当前继承分类默认，保存后将固定到本商品</span>
            </div>
            <p class="hint">选了分类后，下方商品只显示这些分类里的品；勾选项之间为「或」</p>
          </div>
        </el-form-item>
        <el-form-item label="价格" required>
          <el-input-number v-model="form.price" :min="0.01" :precision="2" :step="1" placeholder="必填" />
        </el-form-item>
        <el-form-item label="原价">
          <el-input-number v-model="form.original_price" :min="0" :precision="2" />
        </el-form-item>
        <el-form-item label="餐盒费">
          <el-input-number v-model="form.box_price" :min="0" :precision="2" :step="0.5" />
          <span class="hint">顾客选「打包」时按件数加收</span>
        </el-form-item>
        <el-form-item label="库存">
          <el-input-number v-model="form.stock" :min="0" />
          <span class="hint">999 表示不限库存</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio label="ON_SALE">在售</el-radio>
            <el-radio label="OFF_SALE">下架</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="规格组">
          <el-select v-model="form.option_group_ids" multiple style="width:100%" placeholder="选择关联规格组">
            <el-option
              v-for="g in bindableOptionGroups"
              :key="g.id"
              :label="g.store_id == null ? `${g.name}（总部）` : g.name"
              :value="g.id"
            />
          </el-select>
          <p class="hint">仅可选总部共享或本店规格组</p>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" />
        </el-form-item>
        <el-form-item label="标签">
          <el-checkbox v-model="form.is_recommended">推荐</el-checkbox>
          <el-checkbox v-model="form.is_hot">热销</el-checkbox>
          <el-checkbox v-model="form.no_member_discount">特价（不参与会员折扣/优惠券/代金券）</el-checkbox>
          <el-checkbox v-model="form.no_cooking">免烹饪（卤味等现成品，KDS 可直出餐）</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <CopyToStoreDialog
      v-model="copyVisible"
      :stores="stores"
      :exclude-store-id="copySourceStoreId"
      :selected-count="selectedIds.length"
      :product-ids="selectedIds"
      show-product-conflict
      @done="load"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import CopyToStoreDialog from '@/components/CopyToStoreDialog.vue'
import { storePrefixFromNo, suggestCodeFromName } from '@/utils/bizCode'

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const products = ref<any[]>([])
const allStoreProducts = ref<any[]>([])
const categories = ref<any[]>([])
const stores = ref<any[]>([])
const optionGroups = ref<any[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('ALL')
const categoryFilter = ref<number | undefined>()
const storeFilter = ref<number | undefined>()
const dialogVisible = ref(false)
const copyVisible = ref(false)
const selectedRows = ref<any[]>([])
const codeTouched = ref(false)

const selectedIds = computed(() => selectedRows.value.map((r) => r.id))
const copySourceStoreId = computed(() => {
  const storesSet = new Set(selectedRows.value.map((r) => Number(r.store_id)))
  return storesSet.size === 1 ? [...storesSet][0] : null
})

function onSelectionChange(rows: any[]) {
  selectedRows.value = rows
}

function openCopy() {
  if (!selectedIds.value.length) {
    ElMessage.warning('请先勾选商品')
    return
  }
  const storesSet = new Set(selectedRows.value.map((r) => Number(r.store_id)))
  if (storesSet.size > 1) {
    ElMessage.warning('请选择同一门店的商品再复制')
    return
  }
  copyVisible.value = true
}

function formStorePrefix() {
  if (!form.store_id && auth.isSuperAdmin) return '0000'
  const sid = form.store_id || auth.user?.store_id
  const s = stores.value.find((x) => Number(x.id) === Number(sid))
  return storePrefixFromNo(s?.store_no)
}

const productNoPlaceholder = computed(() => {
  const p = formStorePrefix()
  return `如 ${p}-Mian-NiuRouMian`
})
const productNoTip = computed(() =>
  form.id
    ? '编号创建后不可改'
    : `规则：${formStorePrefix()}-分类拼音-商品拼音；先选分类再填名称会自动建议。`,
)

function categoryOptionLabel(c: any) {
  if (form.store_id || !auth.isSuperAdmin) return c.name
  const sn = c.store_name || stores.value.find((s) => Number(s.id) === Number(c.store_id))?.name
  return sn ? `${c.name}（${sn}）` : c.name
}

function refreshProductCodeSuggest() {
  if (form.id || codeTouched.value) return
  if (!form.name?.trim() || !form.category_id) return
  const hq = auth.isSuperAdmin && !form.store_id
  suggestCodeFromName(
    {
      name: form.name,
      kind: 'product',
      store_id: form.store_id || auth.user?.store_id || undefined,
      category_id: form.category_id,
      hq,
    },
    (code) => {
      if (!codeTouched.value) form.product_no = code
    },
  )
}

function onProductNameInput() {
  refreshProductCodeSuggest()
}

const form = reactive({
  id: 0,
  store_id: undefined as number | undefined,
  store_name: '',
  product_no: '',
  name: '',
  category_id: undefined as number | undefined,
  price: undefined as number | undefined,
  original_price: undefined as number | undefined,
  box_price: 0,
  stock: 999,
  status: 'ON_SALE',
  description: '',
  main_image: '' as string,
  sort_order: 0,
  is_recommended: false,
  is_hot: false,
  no_member_discount: false,
  no_cooking: false,
  option_group_ids: [] as number[],
  require_category_ids: [] as number[],
  require_product_ids: [] as number[],
  require_inherited: false,
})

const boundStoreName = computed(() => {
  const sid = auth.user?.store_id
  if (!sid) return ''
  return stores.value.find((s) => s.id === sid)?.name || `门店#${sid}`
})

const effectiveListStoreId = computed(() => {
  if (auth.isSuperAdmin) return storeFilter.value
  return auth.user?.store_id || undefined
})

const filterCategories = computed(() => {
  const sid = effectiveListStoreId.value
  if (!sid) return categories.value
  return categories.value.filter((c) => c.store_id === sid)
})

const formCategories = computed(() => {
  const sid = form.store_id || (!auth.isSuperAdmin ? auth.user?.store_id : undefined)
  if (!sid) {
    // 超管未选门店：可选全部分类（选项带门店名）
    return categories.value
  }
  return categories.value.filter((c) => Number(c.store_id) === Number(sid))
})

/** 绑规格：总部共享 ∪ 表单门店私有（未选门店时用分类所属门店） */
const bindableOptionGroups = computed(() => {
  const sid =
    form.store_id ||
    categories.value.find((c) => Number(c.id) === Number(form.category_id))?.store_id
  if (!sid) {
    return (optionGroups.value || []).filter((g) => g.store_id == null)
  }
  return (optionGroups.value || []).filter(
    (g) => g.store_id == null || Number(g.store_id) === Number(sid),
  )
})

const requireCategoryOptions = computed(() => formCategories.value.filter((c) => c.status === 1 || c.id))

const requireProductGroups = computed(() => {
  const selectedCatIds = form.require_category_ids || []
  const sid = form.store_id
  const catList = selectedCatIds.length
    ? formCategories.value.filter((c) => selectedCatIds.includes(c.id))
    : formCategories.value
  const allowed = new Set(catList.map((c) => c.id))
  const list = (allStoreProducts.value || []).filter(
    (p) => (!sid || p.store_id === sid) && allowed.has(p.category_id),
  )
  return catList
    .map((c) => ({
      id: c.id,
      name: c.name,
      products: list.filter((p) => p.category_id === c.id),
    }))
    .filter((g) => g.products.length > 0)
})

function mediaUrl(path: string) {
  if (!path) return ''
  if (/^https?:\/\//i.test(path)) return path
  return path.startsWith('/') ? path : `/${path}`
}

function categoryName(id: number) {
  const c = categories.value.find((x) => x.id === id)
  return c?.name || id
}

function pruneRequireProducts() {
  const selectedCatIds = form.require_category_ids || []
  if (!selectedCatIds.length) return
  const allowedCatIds = new Set(selectedCatIds)
  const sid = form.store_id
  const allowedProductIds = new Set(
    (allStoreProducts.value || [])
      .filter((p) => (!sid || p.store_id === sid) && allowedCatIds.has(p.category_id))
      .map((p) => p.id),
  )
  form.require_product_ids = (form.require_product_ids || []).filter((id) => allowedProductIds.has(id))
}

function onRequireCategoriesChange() {
  form.require_inherited = false
  pruneRequireProducts()
}

function applyCategoryRequireDefault() {
  const cat = categories.value.find((c) => c.id === form.category_id)
  form.require_category_ids = [...(cat?.require_category_ids || [])]
  form.require_product_ids = []
  form.require_inherited = false
  pruneRequireProducts()
}

function onFormCategoryChange() {
  if (!form.id) applyCategoryRequireDefault()
  if (!form.id) {
    codeTouched.value = false
    refreshProductCodeSuggest()
  }
}

async function loadAllStoreProducts(storeId?: number) {
  const sid = storeId || form.store_id || effectiveListStoreId.value
  if (!sid) {
    allStoreProducts.value = []
    return
  }
  try {
    const all: any[] = []
    let page = 1
    let totalPages = 1
    do {
      const res: any = await api.get('/products', {
        params: { page, page_size: 200, status: 'ALL', store_id: sid },
      })
      const data = res?.data || {}
      all.push(...(data.items || []))
      totalPages = Math.max(1, Number(data.total_pages || 1))
      page += 1
    } while (page <= totalPages && page <= 20)
    allStoreProducts.value = all
  } catch {
    allStoreProducts.value = []
  }
}

async function loadStores() {
  try {
    stores.value = (await api.get('/settings/stores/options')).data.items || []
  } catch {
    stores.value = []
  }
}

async function loadMeta() {
  const sid = effectiveListStoreId.value
  categories.value = (
    await api.get('/categories', {
      params: { all: true, ...(sid ? { store_id: sid } : {}) },
    })
  ).data
  optionGroups.value = (
    await api.get('/options/groups', {
      params: sid ? { store_id: sid } : {},
    })
  ).data
}

function reload() {
  page.value = 1
  load()
}

async function onStoreFilterChange() {
  categoryFilter.value = undefined
  await loadMeta()
  reload()
}

async function onFormStoreChange() {
  // 换店后若当前分类不属于可选范围则重置
  if (!formCategories.value.some((c) => c.id === form.category_id)) {
    form.category_id = formCategories.value[0]?.id
  }
  // 换店后去掉不可绑的他店规格组
  const ok = new Set(bindableOptionGroups.value.map((g) => g.id))
  form.option_group_ids = (form.option_group_ids || []).filter((id: number) => ok.has(id))
  // 超管全店筛选时列表可能不含本店组：按表单门店补拉
  if (auth.isSuperAdmin && !effectiveListStoreId.value && form.store_id) {
    try {
      const rows = (
        await api.get('/options/groups', { params: { store_id: form.store_id } })
      ).data || []
      const byId = new Map((optionGroups.value || []).map((g: any) => [g.id, g]))
      for (const g of rows) byId.set(g.id, g)
      optionGroups.value = Array.from(byId.values())
    } catch {
      /* ignore */
    }
  }
  codeTouched.value = false
  refreshProductCodeSuggest()
}

async function load() {
  loading.value = true
  try {
    const res = await api.get('/products', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        keyword: keyword.value || undefined,
        status: statusFilter.value,
        category_id: categoryFilter.value,
        store_id: effectiveListStoreId.value,
      },
    })
    products.value = res.data.items
    total.value = res.data.total
    selectedRows.value = []
  } catch (e: any) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  codeTouched.value = false
  // 超管新建：默认不选门店 → 0000 前缀；若列表筛了门店则带上
  const defaultStore = auth.isSuperAdmin
    ? storeFilter.value || undefined
    : auth.user?.store_id || stores.value[0]?.id
  Object.assign(form, {
    id: 0,
    store_id: defaultStore,
    store_name: defaultStore
      ? stores.value.find((s) => s.id === defaultStore)?.name || ''
      : '',
    product_no: '',
    name: '',
    category_id: undefined,
    price: undefined,
    original_price: undefined,
    box_price: 0,
    stock: 999,
    status: 'ON_SALE',
    description: '',
    main_image: '',
    sort_order: 0,
    is_recommended: false,
    is_hot: false,
    no_member_discount: false,
    no_cooking: false,
    option_group_ids: [],
    require_category_ids: [],
    require_product_ids: [],
    require_inherited: false,
  })
  form.category_id = formCategories.value[0]?.id
  applyCategoryRequireDefault()
}

async function openForm(row?: any) {
  resetForm()
  codeTouched.value = !!row
  if (row) {
    const detail = (await api.get(`/products/${row.id}`)).data
    const inherited = !!detail.require_inherited
    Object.assign(form, {
      id: detail.id,
      store_id: detail.store_id,
      store_name: detail.store_name || '',
      product_no: detail.product_no,
      name: detail.name,
      category_id: detail.category_id,
      price: parseFloat(detail.price),
      original_price: detail.original_price ? parseFloat(detail.original_price) : undefined,
      box_price: detail.box_price != null ? parseFloat(detail.box_price) : 0,
      stock: detail.stock ?? 999,
      status: detail.status,
      description: detail.description || '',
      main_image: detail.main_image || '',
      sort_order: detail.sort_order ?? 0,
      is_recommended: detail.is_recommended ?? false,
      is_hot: detail.is_hot ?? false,
      no_member_discount: detail.no_member_discount ?? false,
      no_cooking: detail.no_cooking ?? false,
      option_group_ids: detail.option_group_ids || [],
      require_category_ids: inherited
        ? [...(detail.require_category_ids || [])]
        : [...(detail.require_category_ids_stored ?? detail.require_category_ids ?? [])],
      require_product_ids: inherited
        ? [...(detail.require_product_ids || [])]
        : [...(detail.require_product_ids_stored ?? detail.require_product_ids ?? [])],
      require_inherited: inherited,
    })
  }
  await loadAllStoreProducts(form.store_id)
  pruneRequireProducts()
  dialogVisible.value = true
}

async function uploadImage(option: { file: File }) {
  if (!form.id) {
    ElMessage.warning('请先保存商品再上传图片')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', option.file)
    const res = await api.post(`/products/${form.id}/image`, fd)
    form.main_image = res.data.main_image || res.data.url || ''
    ElMessage.success('图片已上传')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function clearImage() {
  if (!form.id) return
  try {
    await api.put(`/products/${form.id}`, { main_image: null })
    form.main_image = ''
    ElMessage.success('已清除主图')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '清除失败')
  }
}

async function save() {
  const productNo = form.product_no?.trim()
  if (!productNo || !form.name?.trim()) {
    ElMessage.warning('请填写商品编号和名称')
    return
  }
  if (!form.id && !auth.isSuperAdmin && !form.store_id) {
    ElMessage.warning('请选择门店')
    return
  }
  if (!form.category_id) {
    ElMessage.warning('请选择分类（请先在「分类管理」中创建分类）')
    return
  }
  if (form.price == null || form.price <= 0) {
    ElMessage.warning('请填写大于 0 的价格')
    return
  }
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      category_id: form.category_id,
      product_no: productNo,
      name: form.name.trim(),
      price: form.price,
      box_price: form.box_price ?? 0,
      stock: form.stock,
      status: form.status,
      description: form.description?.trim() || null,
      sort_order: form.sort_order,
      is_recommended: form.is_recommended,
      is_hot: form.is_hot,
      no_member_discount: form.no_member_discount,
      no_cooking: form.no_cooking,
      option_group_ids: form.option_group_ids,
      require_category_ids: form.require_category_ids || [],
      require_product_ids: form.require_product_ids || [],
    }
    if (form.original_price != null && form.original_price > 0) {
      payload.original_price = form.original_price
    }
    if (form.id) {
      await api.put(`/products/${form.id}`, payload)
      ElMessage.success('已更新')
      dialogVisible.value = false
      load()
    } else {
      payload.store_id = form.store_id || null
      const res = await api.post('/products', payload)
      form.id = res.data.id
      form.store_id = res.data.store_id || form.store_id
      if (res.data.product_no) form.product_no = res.data.product_no
      ElMessage.success('已创建，可继续上传预览图')
      statusFilter.value = form.status
      page.value = 1
      load()
    }
  } catch (e: any) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function changeStatus(row: any, status: string) {
  if (status === row.status) return
  try {
    await api.put(`/products/${row.id}`, { status })
    ElMessage.success(status === 'ON_SALE' ? '已上架' : '已下架')
    load()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}

async function remove(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除商品「${row.name}」（${row.product_no}）？删除后不可恢复。`,
      '确认删除',
      { type: 'warning' },
    )
    await api.delete(`/products/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(e.message || '删除失败')
    }
  }
}

onMounted(async () => {
  await loadStores()
  if (!auth.isSuperAdmin && auth.user?.store_id) {
    storeFilter.value = auth.user.store_id
  }
  await loadMeta()
  await load()
})
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.thumb { width: 48px; height: 48px; border-radius: 6px; }
.no-img { color: #c0c4cc; font-size: 12px; }
.status-select { width: 88px; }
.img-row { display: flex; gap: 12px; align-items: flex-start; }
.preview { width: 96px; height: 96px; border-radius: 8px; }
.preview.empty {
  display: flex; align-items: center; justify-content: center;
  background: #f5f7fa; color: #c0c4cc; font-size: 12px;
}
.img-actions { flex: 1; }
.hint { margin: 4px 0 0; font-size: 12px; color: #909399; }
.hint.warn { color: #e6a23c; }
.require-box { width: 100%; }
.require-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}
.require-label {
  width: 36px;
  line-height: 32px;
  color: #606266;
  flex-shrink: 0;
}
.require-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
</style>
