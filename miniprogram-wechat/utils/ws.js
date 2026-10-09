/**
 * 微信小程序 WebSocket 客户端
 * P0-1：替代 HTTP 轮询，实现实时推送
 * P0-2：心跳保活 + 自动重连
 * P1-8：消息幂等去重
 */
var { resolveApiBase } = require('../config/env')

var HEARTBEAT_INTERVAL = 30000 // 30s，小于服务端 90s 空闲超时
var RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000]
var DEDUP_TTL = 60000 // 去重窗口 60s

function buildWsUrl(channel, params) {
    var apiBase = resolveApiBase()
    var wsBase = apiBase.replace(/^http/, 'ws')
    var parts = []
    if (params) {
        for (var k in params) {
            var v = params[k]
            if (v !== undefined && v !== null && v !== '') {
                parts.push(k + '=' + encodeURIComponent(v))
            }
        }
    }
    return wsBase + '/ws/' + channel + (parts.length ? '?' + parts.join('&') : '')
}

function getCustomerToken() {
    try { return wx.getStorageSync('customer_token') || '' } catch (e) { return '' }
}

function getStoreId() {
    try {
        var app = getApp()
        var s = app && app.globalData && (app.globalData.store || {})
        return s.id || null
    } catch (e) { return null }
}

/**
 * 创建 WebSocket 连接（带心跳 + 重连 + 去重）
 * @param {string} channel - 频道名
 * @param {Object} handlers - { onMessage(data), onStatus(status), onOpen() }
 * @param {Object} [opts] - { storeId, token }
 * @returns {Object} controller - { close(), send(data) }
 */
function connectWebSocket(channel, handlers, opts) {
    handlers = handlers || {}
    opts = opts || {}
    var token = opts.token !== undefined ? opts.token : getCustomerToken()
    var storeId = opts.storeId !== undefined ? opts.storeId : getStoreId()
    var params = {}
    if (storeId) params.store_id = storeId
    var url = buildWsUrl(channel, params)
    var protocols = token ? ['access_token', token] : undefined

    var socketTask = null
    var heartbeatTimer = null
    var reconnectTimer = null
    var reconnectAttempt = 0
    var closed = false
    var seenIds = {} // P1-8：消息幂等去重

    function clearTimers() {
        if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null }
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    }

    function startHeartbeat() {
        if (heartbeatTimer) clearInterval(heartbeatTimer)
        heartbeatTimer = setInterval(function () {
            if (socketTask) {
                try { socketTask.send({ data: JSON.stringify({ type: 'ping' }) }) } catch (e) { }
            }
        }, HEARTBEAT_INTERVAL)
    }

    function scheduleReconnect() {
        if (closed) return
        clearTimers()
        var delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)]
        // P3-13：添加抖动，避免多个客户端同时重连导致惊群
        var jitter = Math.floor(Math.random() * 500)
        reconnectAttempt++
        reconnectTimer = setTimeout(doConnect, delay + jitter)
    }

    function isDuplicate(data) {
        try {
            var id = data.event_id || data.id || (data.data && (data.data.event_id || data.data.id))
            if (!id) return false
            var now = Date.now()
            // 清理过期记录
            for (var k in seenIds) {
                if (now - seenIds[k] > DEDUP_TTL) delete seenIds[k]
            }
            if (seenIds[id]) return true
            seenIds[id] = now
            return false
        } catch (e) { return false }
    }

    function doConnect() {
        if (closed) return
        try {
            socketTask = wx.connectSocket({
                url: url,
                protocols: protocols,
                fail: function () {
                    if (handlers.onStatus) handlers.onStatus('error')
                    scheduleReconnect()
                }
            })
            if (!socketTask) {
                scheduleReconnect()
                return
            }
            socketTask.onOpen(function () {
                reconnectAttempt = 0
                startHeartbeat()
                if (handlers.onStatus) handlers.onStatus('online')
                if (handlers.onOpen) handlers.onOpen()
            })
            socketTask.onMessage(function (res) {
                var data = {}
                try { data = JSON.parse(res.data) } catch (e) { return }
                // 过滤 pong 心跳响应
                if (data.type === 'pong') return
                if (isDuplicate(data)) return
                if (handlers.onMessage) handlers.onMessage(data)
            })
            socketTask.onClose(function () {
                clearTimers()
                if (handlers.onStatus) handlers.onStatus('offline')
                if (!closed) scheduleReconnect()
            })
            socketTask.onError(function () {
                if (handlers.onStatus) handlers.onStatus('error')
            })
        } catch (e) {
            scheduleReconnect()
        }
    }

    doConnect()

    return {
        close: function () {
            closed = true
            clearTimers()
            try { if (socketTask) socketTask.close({ code: 1000 }) } catch (e) { }
        },
        send: function (data) {
            if (socketTask) {
                try { socketTask.send({ data: typeof data === 'string' ? data : JSON.stringify(data) }) } catch (e) { }
            }
        }
    }
}

/**
 * 订阅订单频道（顾客端）
 * @param {Object} handlers - { onMessage(data), onStatus(status) }
 * @returns {Object} controller - { close() }
 */
function subscribeOrders(handlers) {
    return connectWebSocket('orders', handlers)
}

module.exports = {
    connectWebSocket: connectWebSocket,
    subscribeOrders: subscribeOrders,
    buildWsUrl: buildWsUrl
}
