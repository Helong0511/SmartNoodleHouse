/** 将支付宝/浏览器等原生英文错误转成用户可见中文 */

function localizeNativeErrMsg(raw) {
  const m = String(raw || '').trim()
  if (!m) return ''

  if (/timeout of \d+ms exceeded|timeout|TIMED_OUT|-1001|超时/i.test(m)) {
    return '请求超时，请检查网络或稍后重试'
  }
  if (/CONNECTION_REFUSED|ECONNREFUSED|connect fail|无法连接|ERR_CONNECTION|Cannot connect|-1004/i.test(m)) {
    return '无法连接服务器，请确认后端已启动'
  }
  if (/CONNECTION_RESET|ERR_CONNECTION_RESET|reset by peer/i.test(m)) {
    return '连接被重置，请确认后端未崩溃'
  }
  if (/域名白名单|无权访问域名|error:\s*81|错误码\s*81|域名合法性/i.test(m)) {
    return (
      '未放行请求域名：支付宝开发者工具 → 右上角「详情」→「域名信息」→ 勾选' +
      '「忽略 httpRequest 域名合法性检查」（仅模拟器/预览/真机调试生效；正式版须配置 HTTPS 白名单）'
    )
  }
  if (/request:fail\s*abort|ERR_CANCELED|aborted/i.test(m)) {
    return '请求已取消'
  }
  if (/Network Error|ERR_NETWORK|failed to fetch|load failed/i.test(m)) {
    return '网络连接失败，请检查服务是否已启动'
  }

  let stripped = m
    .replace(/^request:fail\s*/i, '')
    .replace(/^getPhoneNumber:fail\s*/i, '')
    .replace(/^chooseImage:fail\s*/i, '')
    .replace(/^tradePay:fail\s*/i, '')
    .replace(/^uploadFile:fail\s*/i, '')
    .replace(/^getAuthCode:fail\s*/i, '')
    .trim()

  if (!stripped || /^fail$/i.test(stripped)) return '网络请求失败'
  if (/^(cancel|deny|user cancel|user deny|11)$/i.test(stripped)) return '已取消'
  if (/cancel|deny/i.test(stripped) && !/[\u4e00-\u9fff]/.test(stripped)) return '已取消'

  if (/timeout/i.test(stripped)) return '请求超时，请检查网络或稍后重试'

  // 纯英文兜底，避免把 SDK 英文原文抛给用户
  if (/^[A-Za-z0-9][\w\s.,:;'"()[\]/-]*$/.test(stripped) && !/[\u4e00-\u9fff]/.test(stripped)) {
    return '操作失败，请稍后重试'
  }
  return stripped
}

module.exports = {
  localizeNativeErrMsg
}
