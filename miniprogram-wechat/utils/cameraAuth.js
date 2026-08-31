/** 人脸迎宾页：隐私协议 + 摄像头授权（真机/iOS 必需） */

const CAMERA_DENIED_HINT =
  '未授予摄像头权限。请依次检查：\n' +
  '1. 微信公众平台 → 设置 → 用户隐私保护指引 → 勾选「摄像头」并发布\n' +
  '2. iPhone：设置 → 微信 → 摄像头 → 允许\n' +
  '3. 返回本页重新点「打开摄像头采集」'

function getAppCameraAuth() {
  return new Promise((resolve) => {
    if (wx.getAppAuthorizeSetting) {
      try {
        const s = wx.getAppAuthorizeSetting()
        resolve(s.cameraAuthorized || 'not determined')
        return
      } catch (_) {}
    }
    wx.getSetting({
      success: (res) => {
        const v = res.authSetting && res.authSetting['scope.camera']
        if (v === true) resolve('authorized')
        else if (v === false) resolve('denied')
        else resolve('not determined')
      },
      fail: () => resolve('not determined')
    })
  })
}

function requirePrivacyAuthorize() {
  return new Promise((resolve, reject) => {
    if (!wx.requirePrivacyAuthorize) {
      resolve()
      return
    }
    wx.requirePrivacyAuthorize({
      success: () => resolve(),
      fail: () => reject(new Error('需同意《用户隐私保护指引》后才能使用摄像头'))
    })
  })
}

function openCameraSettings() {
  return new Promise((resolve, reject) => {
    const finish = async () => {
      const status = await getAppCameraAuth()
      if (status === 'authorized') resolve()
      else reject(new Error(CAMERA_DENIED_HINT))
    }

    if (wx.openAppAuthorizeSetting) {
      wx.openAppAuthorizeSetting({
        success: () => finish(),
        fail: () => {
          wx.openSetting({
            success: () => finish(),
            fail: () => reject(new Error('无法打开设置页'))
          })
        }
      })
      return
    }

    wx.openSetting({
      success: () => finish(),
      fail: () => reject(new Error('无法打开设置页'))
    })
  })
}

/** 用户曾拒绝或设置页无摄像头项时引导 */
function promptCameraPermission() {
  return new Promise((resolve, reject) => {
    wx.showModal({
      title: '需要摄像头权限',
      content:
        '人脸迎宾需前置摄像头实时采集。\n\n' +
        '若设置页没有「摄像头」开关，请先在微信公众平台 → 用户隐私保护指引 中勾选「摄像头」并发布，再重试。',
      confirmText: '去设置',
      cancelText: '取消',
      success: (res) => {
        if (!res.confirm) {
          reject(new Error(CAMERA_DENIED_HINT))
          return
        }
        openCameraSettings().then(resolve).catch(reject)
      }
    })
  })
}

/**
 * 打开 camera 组件前：仅校验隐私协议。
 * 不在此阶段 wx.authorize —— iOS 须先挂载 <camera> 才会弹出系统授权；
 * 过早 authorize + openSetting 会出现设置页只有「照片」没有「摄像头」。
 */
async function ensureCameraReady() {
  await requirePrivacyAuthorize()
  const status = await getAppCameraAuth()
  if (status === 'denied') {
    await promptCameraPermission()
  }
}

/** camera 组件 binderror 或 takePhoto 失败时调用 */
async function handleCameraError(errMsg) {
  const msg = String(errMsg || '')
  const authRelated = /auth|deny|permission|authorize|权限|未授权/i.test(msg)
  if (authRelated) {
    try {
      await promptCameraPermission()
      return true
    } catch (_) {
      wx.showModal({ title: '无法打开摄像头', content: CAMERA_DENIED_HINT, showCancel: false })
      return false
    }
  }
  wx.showModal({
    title: '摄像头不可用',
    content: msg || '请检查权限与光线后重试',
    showCancel: false
  })
  return false
}

module.exports = {
  ensureCameraReady,
  handleCameraError,
  getAppCameraAuth,
  CAMERA_DENIED_HINT
}
