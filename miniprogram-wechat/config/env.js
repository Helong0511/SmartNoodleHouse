/** 按运行环境选择 API 根地址 */
function resolveApiBase() {
  try {
    const info = wx.getAccountInfoSync()
    if (info && info.miniProgram && info.miniProgram.envVersion === 'release') {
      return 'https://your-domain.com/api/v1'
    }
  } catch (e) {
    /* 开发者工具可能无 accountInfo */
  }
  return 'http://127.0.0.1:8000/api/v1'
}

module.exports = { resolveApiBase }
