/**
 * 充值幂等凭证按「顾客 + 档位 + 门店」分别保存。
 * 切换档位/门店后再切回时，仍复用原待支付凭证，避免创建第二笔充值单。
 */
const STORAGE_KEY = 'recharge_idempotency_v1'
const TTL_MS = 24 * 60 * 60 * 1000
const MAX_ENTRIES = 50

function readEntries() {
  let saved
  try {
    saved = wx.getStorageSync(STORAGE_KEY)
  } catch (error) {
    throw new Error('无法读取充值幂等凭证，已阻止重复充值风险')
  }
  if (saved == null || saved === '') return {}
  if (
    saved.version === 2 &&
    saved.entries &&
    typeof saved.entries === 'object' &&
    !Array.isArray(saved.entries)
  ) {
    return { ...saved.entries }
  }
  // 兼容旧版单条缓存，迁移时保留旧 key，不让升级中的待支付单失去幂等关联。
  if (saved.signature && saved.key && Number.isFinite(Number(saved.createdAt))) {
    return {
      [String(saved.signature)]: {
        key: String(saved.key),
        createdAt: Number(saved.createdAt)
      }
    }
  }
  throw new Error('充值幂等凭证格式异常，已阻止创建新充值单')
}

function writeEntries(entries) {
  try {
    wx.setStorageSync(STORAGE_KEY, { version: 2, entries })
  } catch (error) {
    throw new Error('无法保存充值幂等凭证，已阻止重复充值风险')
  }
}

function pruneExpired(entries, now) {
  Object.keys(entries).forEach((signature) => {
    const entry = entries[signature]
    if (
      !entry ||
      typeof entry.key !== 'string' ||
      !entry.key ||
      !Number.isFinite(Number(entry.createdAt))
    ) {
      throw new Error('充值幂等凭证内容异常，已阻止创建新充值单')
    }
    if (now - Number(entry.createdAt) >= TTL_MS) delete entries[signature]
  })
}

function getRechargeIdempotencyKey(customerId, packageId, storeId) {
  const customer = Number(customerId)
  const pkg = Number(packageId)
  const store = Number(storeId)
  if (![customer, pkg, store].every((value) => Number.isSafeInteger(value) && value > 0)) {
    throw new Error('充值顾客、档位或门店信息无效，已阻止创建充值单')
  }

  const signature = `${customer}:${pkg}:${store}`
  const now = Date.now()
  const entries = readEntries()
  pruneExpired(entries, now)

  const existing = entries[signature]
  if (existing) return existing.key

  if (Object.keys(entries).length >= MAX_ENTRIES) {
    throw new Error('待处理充值凭证过多，请先完成或核对历史充值')
  }

  const key = `rc${now.toString(36)}${Math.random().toString(36).slice(2, 12)}`
  entries[signature] = { key, createdAt: now }
  writeEntries(entries)
  return key
}

function clearRechargeIdempotencyKey(key) {
  if (!key) return false
  try {
    const entries = readEntries()
    let changed = false
    Object.keys(entries).forEach((signature) => {
      if (entries[signature] && entries[signature].key === String(key)) {
        delete entries[signature]
        changed = true
      }
    })
    if (changed) writeEntries(entries)
    return changed
  } catch (_) {
    // 清理失败只会保留旧幂等键，不会生成重复充值；不得让已成功的充值显示为失败。
    return false
  }
}

module.exports = {
  getRechargeIdempotencyKey,
  clearRechargeIdempotencyKey
}
