/** 按运行环境选择 API 根地址 */
function resolveApiBase() {
  try {
    const info = my.getAccountInfoSync && my.getAccountInfoSync()
    if (info && info.miniProgram && info.miniProgram.envVersion === 'release') {
      return 'https://your-domain.com/api/v1'
    }
  } catch (_) {}
  return 'http://127.0.0.1:8000/api/v1'
}

module.exports = {
  resolveApiBase
}
