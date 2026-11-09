const assert = require('node:assert/strict')

const TTL_MS = 24 * 60 * 60 * 1000
const STORAGE_KEY = 'recharge_idempotency_v1'

function verifyWechat() {
  const storage = Object.create(null)
  global.wx = {
    getStorageSync(key) { return storage[key] },
    setStorageSync(key, value) { storage[key] = value }
  }
  const helper = require('../miniprogram-wechat/utils/rechargeIdempotency')
  const realNow = Date.now
  try {
    Date.now = () => 1_000_000
    const firstA = helper.getRechargeIdempotencyKey(1, 10, 3)
    const firstB = helper.getRechargeIdempotencyKey(1, 20, 3)
    assert.equal(helper.getRechargeIdempotencyKey(1, 10, 3), firstA, 'A→B→A must reuse A key')
    assert.equal(Object.keys(storage[STORAGE_KEY].entries).length, 2)
    assert.equal(helper.clearRechargeIdempotencyKey(firstA), true)
    assert.notEqual(helper.getRechargeIdempotencyKey(1, 10, 3), firstA)
    assert.equal(helper.getRechargeIdempotencyKey(1, 20, 3), firstB, 'clearing A must not clear B')

    Date.now = () => 1_000_000 + 30 * 24 * 60 * 60 * 1000
    assert.equal(helper.getRechargeIdempotencyKey(1, 10, 3), firstA, 'old pending key must not rotate based only on local age')

    const get = global.wx.getStorageSync
    global.wx.getStorageSync = () => { throw new Error('storage unavailable') }
    assert.throws(
      () => helper.getRechargeIdempotencyKey(1, 30, 3),
      /无法读取充值幂等凭证/
    )
    global.wx.getStorageSync = get
  } finally {
    Date.now = realNow
  }
}

function verifyAlipay() {
  const storage = Object.create(null)
  global.my = {
    getStorageSync({ key }) { return { data: storage[key] } },
    setStorageSync({ key, data }) { storage[key] = data }
  }
  const helper = require('../miniprogram-alipay/utils/rechargeIdempotency')
  const realNow = Date.now
  try {
    Date.now = () => 2_000_000
    const firstA = helper.getRechargeIdempotencyKey(2, 10, 4)
    const firstB = helper.getRechargeIdempotencyKey(2, 20, 4)
    assert.equal(helper.getRechargeIdempotencyKey(2, 10, 4), firstA, 'A→B→A must reuse A key')
    assert.equal(Object.keys(storage[STORAGE_KEY].entries).length, 2)
    assert.equal(helper.clearRechargeIdempotencyKey(firstA), true)
    assert.notEqual(helper.getRechargeIdempotencyKey(2, 10, 4), firstA)
    assert.equal(helper.getRechargeIdempotencyKey(2, 20, 4), firstB, 'clearing A must not clear B')

    Date.now = () => 2_000_000 + 30 * 24 * 60 * 60 * 1000
    assert.equal(helper.getRechargeIdempotencyKey(2, 10, 4), firstA, 'old pending key must not rotate based only on local age')

    const get = global.my.getStorageSync
    global.my.getStorageSync = () => { throw new Error('storage unavailable') }
    assert.throws(
      () => helper.getRechargeIdempotencyKey(2, 30, 4),
      /无法读取充值幂等凭证/
    )
    global.my.getStorageSync = get
  } finally {
    Date.now = realNow
  }
}

verifyWechat()
verifyAlipay()
console.log('WeChat/Alipay recharge idempotency cache switching, exact clear, age-independent retention and fail-closed: OK')
