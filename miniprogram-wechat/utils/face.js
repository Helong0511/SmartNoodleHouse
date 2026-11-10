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

const FACE_CONSENT_VERSION = 'face-welcome-2026-11-v1'

function confirmAction(label) {
  return new Promise((resolve) => {
    wx.showModal({
      title: '人脸信息单独授权',
      content: `人脸信息属于敏感个人信息。选择同意后，将采集前置摄像头照片并提取人脸特征，经网络传输后加密保存在系统服务器，仅用于本店到店迎宾识别，不用于广告营销。拒绝不影响点餐、支付等服务；可随时在个人中心撤回，撤回后删除人脸特征。保存期限为授权有效期间。请先完成动作：${label || '按提示完成动作'}。`,
      confirmText: '同意并去拍照',
      cancelText: '不同意',
      success: (r) => resolve(!!r.confirm),
    })
  })
}

function takeFrontCameraBase64() {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['camera'],
      camera: 'front',
      sizeType: ['compressed'],
      success: (res) => {
        const file = (res.tempFiles && res.tempFiles[0]) || {}
        const path = file.tempFilePath
        if (!path) {
          reject(new Error('未采集到人脸照片'))
          return
        }
        const fs = wx.getFileSystemManager()
        fs.readFile({
          filePath: path,
          encoding: 'base64',
          success: (r) => resolve(r.data),
          fail: (e) => {
            const { localizeNativeErrMsg } = require('./errMsg')
            const raw = (e && e.errMsg) || '读取照片失败'
            reject(new Error(localizeNativeErrMsg(raw) || raw))
          },
        })
      },
      fail: (e) => {
        const { localizeNativeErrMsg } = require('./errMsg')
        const msg = (e && e.errMsg) || ''
        if (/cancel|取消/i.test(msg)) reject(new Error('已取消拍照'))
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
  let actionCompleted = false
  if (mode === 'challenge') {
    token = (challenge && challenge.token) || ''
    if (!token) throw new Error('活体挑战获取失败，请稍后重试')
    const ok = await confirmAction(challenge.action_label || challenge.action)
    if (!ok) throw new Error('已取消人脸授权')
    actionCompleted = ok
  } else {
    // Development-only no-challenge mode still requires the same separate face-data consent.
    const ok = await confirmAction('无需动作挑战，请确认后继续')
    if (!ok) throw new Error('已取消人脸授权')
    actionCompleted = ok
  }

  wx.showLoading({ title: '采集中', mask: true })
  let b64
  try {
    b64 = await takeFrontCameraBase64()
  } finally {
    wx.hideLoading()
  }
  if (!b64 || String(b64).length < 800) {
    throw new Error('人脸图像无效，请正对摄像头重试')
  }

  wx.showLoading({ title: '校验中', mask: true })
  try {
    return await api.post('/miniprogram/face/consent-base64', {
      image_base64: b64,
      face_consent_accepted: true,
      face_consent_version: FACE_CONSENT_VERSION,
      liveness_token: token,
      action_completed: actionCompleted,
      claimed_action: challenge && challenge.action,
    })
  } catch (e) {
    throw new Error(friendlyNetError(e))
  } finally {
    wx.hideLoading()
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
