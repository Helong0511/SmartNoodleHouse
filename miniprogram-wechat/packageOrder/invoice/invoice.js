const api = require('../../utils/api')
const auth = require('../../utils/auth')
const clientConfig = require('../../utils/clientConfig')
const { formatTimeLiteral } = require('../../utils/date')
const { normalizeTaxNo, taxNoErrorMessage } = require('../../utils/taxNo')

const TITLE_TYPE_TEXT = { PERSON: '个人', COMPANY: '单位' }
const STATUS_TEXT = { PENDING: '开票中', ISSUED: '已开票', REJECTED: '已驳回' }
const STATUS_CLASS = { PENDING: 'pending', ISSUED: 'issued', REJECTED: 'rejected' }
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const LAST_INVOICE_KEY = 'snh_last_invoice_profile'

function decorateInvoice(it) {
  if (!it || typeof it !== 'object') return it
  const status = String(it.status || '').toUpperCase()
  return {
    ...it,
    status,
    titleTypeText: TITLE_TYPE_TEXT[String(it.title_type || '').toUpperCase()] || '个人',
    statusText: STATUS_TEXT[status] || '未知状态',
    statusClass: STATUS_CLASS[status] || 'pending',
    createdText: formatTimeLiteral(it.created_at),
    issuedText: formatTimeLiteral(it.issued_at)
  }
}

function normalizeProfile(src) {
  if (!src || typeof src !== 'object') return null
  const titleType = String(src.title_type || src.titleType || '').toUpperCase()
  const title = String(src.title || '').trim()
  const taxNo = String(src.tax_no != null ? src.tax_no : (src.taxNo || '')).trim()
  const email = String(src.email || '').trim()
  if (!title && !email) return null
  return {
    titleType: (titleType === 'COMPANY' || titleType === 'PERSON') ? titleType : 'PERSON',
    title,
    taxNo,
    email
  }
}

Page({
  data: {
    orderNo: '',
    featureEnabled: true,
    featureChecked: false,
    canSubmit: true,
    existingStatus: '',
    existingStatusText: '',
    existingRejectReason: '',
    titleType: 'PERSON',
    title: '',
    taxNo: '',
    email: '',
    autoFilled: false,
    submitting: false,
    listLoading: true,
    list: [],
    page: 1,
    pageSize: 20,
    hasMore: false,
    loadingMore: false
  },

  onLoad(query) {
    this.orderId = query.order_id || ''
    let orderNo = query.order_no || ''
    try {
      orderNo = decodeURIComponent(orderNo)
    } catch (_) { }
    this.setData({ orderNo })
    try {
      this._applyDefaultsFrom(wx.getStorageSync(LAST_INVOICE_KEY))
    } catch (_) { }
  },

  onShow() {
    this._bootstrap()
  },

  async _bootstrap() {
    if (this._booting) return
    this._booting = true
    try {
      await auth.ensureLogin()
      await this._checkFeature()
      await this._loadOrderInvoice()
      if (!this._listLoaded) {
        this._listLoaded = true
        await this.loadList(true)
      }
    } finally {
      this._booting = false
    }
  },

  async _checkFeature() {
    try {
      const cfg = await clientConfig.loadClientConfig(true)
      const on = !!(cfg && cfg.invoice_enabled)
      this.setData({ featureEnabled: on, featureChecked: true })
      return on
    } catch (_) {
      this.setData({ featureEnabled: false, featureChecked: true })
      return false
    }
  },

  async _loadOrderInvoice() {
    if (!this.orderId) {
      this.setData({ canSubmit: true, existingStatus: '' })
      return
    }
    try {
      const data = await api.get('/miniprogram/invoices', {
        order_id: Number(this.orderId),
        page: 1,
        page_size: 1
      })
      const item = ((data && data.items) || [])[0]
      if (!item) {
        this.setData({
          canSubmit: true,
          existingStatus: '',
          existingStatusText: '',
          existingRejectReason: ''
        })
        return
      }
      const decorated = decorateInvoice(item)
      const st = decorated.status
      const canSubmit = st === 'REJECTED'
      this.setData({
        canSubmit,
        existingStatus: st,
        existingStatusText: decorated.statusText,
        existingRejectReason: decorated.reject_reason || ''
      })
      if (!this._userEdited) {
        this._applyDefaultsFrom(item)
      }
    } catch (_) {
      this.setData({ canSubmit: true })
    }
  },

  _applyDefaultsFrom(src) {
    if (this._userEdited) return false
    const profile = normalizeProfile(src)
    if (!profile) return false
    this._defaultsApplied = true
    this.setData({
      titleType: profile.titleType,
      title: profile.title,
      taxNo: profile.taxNo,
      email: profile.email,
      autoFilled: true
    })
    return true
  },

  _markEdited() {
    this._userEdited = true
    if (this.data.autoFilled) this.setData({ autoFilled: false })
  },

  _saveLastProfile(profile) {
    const normalized = normalizeProfile(profile)
    if (!normalized) return
    try {
      wx.setStorageSync(LAST_INVOICE_KEY, {
        title_type: normalized.titleType,
        title: normalized.title,
        tax_no: normalized.taxNo,
        email: normalized.email
      })
    } catch (_) { }
  },

  switchTitleType(e) {
    const type = String(e.currentTarget.dataset.type || '').toUpperCase()
    if (type !== 'PERSON' && type !== 'COMPANY') return
    this._markEdited()
    this.setData({ titleType: type })
  },

  onTitleInput(e) {
    this._markEdited()
    this.setData({ title: e.detail.value })
  },

  onTaxNoInput(e) {
    this._markEdited()
    this.setData({ taxNo: e.detail.value })
  },

  onEmailInput(e) {
    this._markEdited()
    this.setData({ email: e.detail.value })
  },

  validateForm() {
    if (!this.data.featureEnabled) return '电子发票功能未开启'
    if (!this.data.canSubmit) return '该订单已申请过发票'
    if (!this.orderId) return '缺少订单信息，请从已支付订单进入'
    const title = this.data.title.trim()
    const taxNo = normalizeTaxNo(this.data.taxNo)
    const email = this.data.email.trim()
    if (!title) return '请填写发票抬头'
    if (title.length > 200) return '抬头不能超过 200 字'
    if (this.data.titleType === 'COMPANY') {
      const taxErr = taxNoErrorMessage(taxNo)
      if (taxErr) return taxErr
    }
    if (!email) return '请填写接收邮箱'
    if (!EMAIL_RE.test(email)) return '邮箱格式不正确'
    return ''
  },

  async submitInvoice() {
    if (this.data.submitting) return
    const err = this.validateForm()
    if (err) {
      wx.showToast({ title: err, icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const titleType = this.data.titleType
      const title = this.data.title.trim()
      const taxNo = normalizeTaxNo(this.data.taxNo)
      const email = this.data.email.trim()
      if (taxNo !== this.data.taxNo) this.setData({ taxNo })
      const body = {
        order_id: Number(this.orderId),
        title_type: titleType,
        title,
        email
      }
      if (titleType === 'COMPANY') body.tax_no = taxNo
      await api.post('/miniprogram/invoices', body)
      this._saveLastProfile({ title_type: titleType, title, tax_no: taxNo, email })
      this._defaultsApplied = true
      this._userEdited = false
      this.setData({
        autoFilled: true,
        canSubmit: false,
        existingStatus: 'PENDING',
        existingStatusText: '开票中',
        existingRejectReason: ''
      })
      wx.showToast({ title: '开票申请已提交', icon: 'success' })
      this._listLoaded = false
      await this.loadList(true)
      this._listLoaded = true
    } catch (e) {
      wx.showToast({ title: (e && e.message) || '提交失败，请稍后重试', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  },

  async loadList(reset) {
    if (this._loadingList) return
    if (!reset && !this.data.hasMore) return
    this._loadingList = true
    const page = reset ? 1 : this.data.page + 1
    if (reset) this.setData({ listLoading: true, page: 1, hasMore: false })
    else this.setData({ loadingMore: true })
    try {
      await auth.ensureLogin()
      const data = await api.get('/miniprogram/invoices', {
        page,
        page_size: this.data.pageSize
      })
      const items = ((data && data.items) || []).map(decorateInvoice)
      const totalPages = Number((data && data.total_pages) || 0)
      this.setData({
        list: reset ? items : this.data.list.concat(items),
        page,
        hasMore: items.length >= this.data.pageSize && (!totalPages || page < totalPages),
        listLoading: false,
        loadingMore: false
      })
      if (reset && items.length && !this.orderId) {
        this._applyDefaultsFrom(items[0])
        this._saveLastProfile(items[0])
      }
    } catch (e) {
      this.setData({ listLoading: false, loadingMore: false })
      wx.showToast({ title: (e && e.message) || '发票列表加载失败', icon: 'none' })
    } finally {
      this._loadingList = false
    }
  },

  onReachBottom() {
    this.loadList(false)
  }
})
