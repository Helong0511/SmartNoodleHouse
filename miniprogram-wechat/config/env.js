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

/**
 * 微信隐私接口开关（getPhoneNumber / chooseAvatar 等）
 * 须在公众平台 → 设置 → 服务内容声明 → 用户隐私保护指引 中声明对应信息类型后改为 true
 * 未配置时保持 false，避免 errno:112 渲染层报错
 */
const ENABLE_WX_PRIVACY_APIS = false

module.exports = {
  resolveApiBase,
  enableWxPhoneBind: ENABLE_WX_PRIVACY_APIS,
  enableChooseAvatar: ENABLE_WX_PRIVACY_APIS
}
