const api = require('../../utils/api')
const auth = require('../../utils/auth')
const { formatTimeLiteral } = require('../../utils/date')

const TITLE_TYPE_TEXT = { PERSON: '个人', COMPANY: '单位' }
const STATUS_TEXT = { PENDING: '开票中', ISSUED: '已开票', REJECTED: '已驳回' }
const STATUS_CLASS = { PENDING: 'pending', ISSUED: 'issued', REJECTED: 'rejected' }
// 与后端 invoices.py 的邮箱校验保持一致
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function decorateInvoice(it) {
    if (!it || typeof it !== 'object') return it
    const status = String(it.status || '').toUpperCase()
    return {
        ...it,
        status,
        titleTypeText: TITLE_TYPE_TEXT[String(it.title_type || '').toUpperCase()] || '个人',
        statusText: STATUS_TEXT[status] || '未知状态',
        statusClass: STATUS_CLASS[status] || 'pending',
        createdText: formatTimeLiteral(it.created_at)
    }
}

Page({
    data: {
        orderNo: '',
        titleType: 'PERSON',
        title: '',
        taxNo: '',
        email: '',
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
    },

    onShow() {
        if (!this._listLoaded) {
            this._listLoaded = true
            this.loadList(true)
        }
    },

    // ---- 开票申请 ----
    switchTitleType(e) {
        const type = String(e.currentTarget.dataset.type || '').toUpperCase()
        if (type === 'PERSON' || type === 'COMPANY') this.setData({ titleType: type })
    },

    onTitleInput(e) {
        this.setData({ title: e.detail.value })
    },

    onTaxNoInput(e) {
        this.setData({ taxNo: e.detail.value })
    },

    onEmailInput(e) {
        this.setData({ email: e.detail.value })
    },

    /** 前端先行校验，规则与后端 InvoiceCreateBody 一致，返回错误文案或空串 */
    validateForm() {
        if (!this.orderId) return '缺少订单信息，请从已支付订单进入'
        const title = this.data.title.trim()
        const taxNo = this.data.taxNo.trim()
        const email = this.data.email.trim()
        if (!title) return '请填写发票抬头'
        if (title.length > 200) return '抬头不能超过 200 字'
        if (this.data.titleType === 'COMPANY') {
            if (!taxNo) return '单位抬头必须填写税号'
            if (taxNo.length > 50) return '税号不能超过 50 字'
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
            const body = {
                order_id: Number(this.orderId),
                title_type: this.data.titleType,
                title: this.data.title.trim(),
                email: this.data.email.trim()
            }
            if (this.data.titleType === 'COMPANY') {
                body.tax_no = this.data.taxNo.trim()
            }
            await api.post('/miniprogram/invoices', body)
            wx.showToast({ title: '开票申请已提交', icon: 'success' })
            this.setData({ title: '', taxNo: '', email: '' })
            this.loadList(true)
        } catch (e) {
            wx.showToast({ title: (e && e.message) || '提交失败，请稍后重试', icon: 'none' })
        } finally {
            this.setData({ submitting: false })
        }
    },

    // ---- 我的发票列表（分页） ----
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
