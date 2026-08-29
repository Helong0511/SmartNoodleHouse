/**
 * 环境配置
 * ─────────────────────────────────────────
 * 本地开发  Windows 11 + 支付宝开发者工具
 * 生产部署  Ubuntu 22.04.5 + HTTPS 域名
 * ─────────────────────────────────────────
 * 改 IP：只改 DEV_LAN_HOST
 * 发正式版：改 PROD_API_BASE 为真实域名
 */

/** 生产 API（Ubuntu 服务器，须 HTTPS，与小程序后台合法域名一致） */
const PROD_API_BASE = 'https://your-domain.com/api/v1'

/** 本地模拟器（Windows 开发者工具） */
const DEV_SIMULATOR_API = 'http://127.0.0.1:8000/api/v1'

/** 本地真机调试：电脑局域网 IP + USE_LAN=true + 后端 uvicorn --host 0.0.0.0 */
const DEV_LAN_HOST = '192.168.1.110'
const DEV_USE_LAN = false
const DEV_LAN_API = `http://${DEV_LAN_HOST}:8000/api/v1`

function resolveDevApiBase() {
  return DEV_USE_LAN ? DEV_LAN_API : DEV_SIMULATOR_API
}

/** 按运行环境选择 API 根地址 */
function resolveApiBase() {
  try {
    const info = my.getAccountInfoSync && my.getAccountInfoSync()
    const ver = info && info.miniProgram && info.miniProgram.envVersion
    if (ver === 'release') return PROD_API_BASE
  } catch (_) {}
  return resolveDevApiBase()
}

module.exports = {
  resolveApiBase,
  PROD_API_BASE,
  DEV_SIMULATOR_API,
  DEV_LAN_HOST,
  DEV_USE_LAN,
  DEV_LAN_API
}
