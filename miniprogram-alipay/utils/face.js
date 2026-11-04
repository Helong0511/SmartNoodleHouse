/** 人脸迎宾授权：活体挑战 + 前置摄像头实拍 + consent-base64 */

const api = require('./api')
const auth = require('./auth')
const { appendDevApiHint, resolveApiBase } = require('../config/env')
const clientConfig = require('./clientConfig')

function friendlyNetError(err) {
  const raw = String((err && err.message) || err || '')
  if (/NSURLErrorDomain.*-1004|-1004|无法连接|Cannot connect|ECONNREFUSED|ERR_CONNECTION/i.test(raw)) {
    return appendDevApiHint(
      '无法连接服务器，请确认后端已启动且真机请用局域网 IP（勿用 127.0.0.1）',
      resolveApiBase()
    )
  }
  if (/timeout|超时|TIMED_OUT|-1001/i.test(raw)) {
    return appendDevApiHint('上传超时，请检查网络后重试', resolveApiBase())
  }
  return appendDevApiHint(raw || '人脸授权失败', resolveApiBase())
}

function confirmAction(label) {
  return new Promise((resolve) => {
    my.confirm({
      title: '人脸活体验证',
      content: `${label || '请按提示完成动作'}，完成后点击「去拍照」使用前置摄像头采集。`,
      confirmButtonText: '去拍照',
      cancelButtonText: '取消',
      success: (r) => resolve(!!r.confirm),
    })
  })
}

function takeFrontCameraBase64() {
  return new Promise((resolve, reject) => {
    my.chooseImage({
      count: 1,
      sourceType: ['camera'],
      success: (res) => {
        const path = (res.apFilePaths && res.apFilePaths[0]) || (res.tempFilePaths && res.tempFilePaths[0])
        if (!path) {
          reject(new Error('未采集到人脸照片'))
          return
        }
        const fs = my.getFileSystemManager ? my.getFileSystemManager() : null
        if (!fs || !fs.readFile) {
          reject(new Error('当前支付宝基础库不支持读取照片，请升级后重试'))
          return
        }
        fs.readFile({
          filePath: path,
          encoding: 'base64',
          success: (r) => resolve(r.data),
          fail: (e) => {
            const { localizeNativeErrMsg } = require('./errMsg')
            const raw = (e && e.errorMessage) || '读取照片失败'
            reject(new Error(localizeNativeErrMsg(raw) || raw))
          },
        })
      },
      fail: (e) => {
        const { localizeNativeErrMsg } = require('./errMsg')
        const msg = (e && (e.errorMessage || e.error)) || ''
        if (/cancel|取消|11/i.test(String(msg))) reject(new Error('已取消拍照'))
        else reject(new Error(localizeNativeErrMsg(msg) || msg || '打开摄像头失败'))
      },
    })
  })
}

/**
 * 开启人脸迎宾：challenge → 引导动作 → 前置摄像头 → 提交授权
 */
async function enrollFaceWelcome() {
  await clientConfig.loadClientConfig()
  if (!clientConfig.isFaceWelcomeEnabled()) {
    throw new Error('人脸迎宾功能未开启')
  }
  await auth.ensureLogin()
  let challenge
  try {
    challenge = await api.get('/miniprogram/face/liveness/challenge')
  } catch (e) {
    throw new Error(friendlyNetError(e))
  }
  const mode = (challenge && challenge.mode) || 'challenge'
  let token = ''
  if (mode === 'challenge') {
    token = (challenge && challenge.token) || ''
    if (!token) throw new Error('活体挑战获取失败，请稍后重试')
    const ok = await confirmAction(challenge.action_label || challenge.action)
    if (!ok) throw new Error('已取消人脸授权')
  } else {
    const ok = await new Promise((resolve) => {
      my.confirm({
        title: '开启人脸迎宾',
        content: '将使用前置摄像头采集人脸，仅用于到店迎宾，可随时关闭。',
        confirmButtonText: '去拍照',
        cancelButtonText: '取消',
        success: (r) => resolve(!!r.confirm),
      })
    })
    if (!ok) throw new Error('已取消人脸授权')
  }

  my.showLoading({ content: '采集中' })
  let b64
  try {
    b64 = await takeFrontCameraBase64()
  } finally {
    my.hideLoading()
  }
  if (!b64 || String(b64).length < 800) {
    throw new Error('人脸图像无效，请正对摄像头重试')
  }

  my.showLoading({ content: '校验中' })
  try {
    return await api.post('/miniprogram/face/consent-base64', {
      image_base64: b64,
      liveness_token: token,
      action_completed: true,
      claimed_action: challenge && challenge.action,
    })
  } catch (e) {
    throw new Error(friendlyNetError(e))
  } finally {
    my.hideLoading()
  }
}

async function revokeFaceWelcome() {
  await auth.ensureLogin()
  return api.post('/miniprogram/face/revoke', {})
}

async function loadFaceStatus() {
  await auth.ensureLogin()
  return api.get('/miniprogram/customers/me/face-status')
}

module.exports = {
  enrollFaceWelcome,
  revokeFaceWelcome,
  loadFaceStatus,
}
