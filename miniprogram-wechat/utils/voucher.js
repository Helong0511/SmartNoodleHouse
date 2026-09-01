/** 代金券计价：一次性核销，不支持余额结转 */

/** 券/代金券展示名：优先模板名称，规则 summary 仅作兜底 */
function holdDisplayName(hold) {
  if (!hold) return ''
  return String(hold.name || hold.template_name || hold.summary || '').trim()
}

function voucherFaceValue(voucher) {
  const face = parseFloat(voucher && voucher.discount_value)
  return Number.isFinite(face) ? face : 0
}

/** 应付是否达到代金券面额（未达则不应使用，避免浪费） */
function isVoucherApplicable(payable, voucher) {
  if (!voucher || !voucher.id) return false
  const minAmt = parseFloat(voucher.min_order_amount || 0)
  if (payable < minAmt) return false
  const face = voucherFaceValue(voucher)
  if (face <= 0) return false
  return payable >= face
}

/** 计算代金券抵扣；eligiblePayable 为可参与代金券的基数（不含特价商品） */
function calcVoucherDiscount(payable, voucher, eligiblePayable) {
  const eligible = eligiblePayable != null ? eligiblePayable : payable
  if (!isVoucherApplicable(eligible, voucher)) return 0
  return Math.min(voucherFaceValue(voucher), eligible)
}

module.exports = {
  holdDisplayName,
  voucherFaceValue,
  isVoucherApplicable,
  calcVoucherDiscount
}
