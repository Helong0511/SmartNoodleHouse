/** 人脸迎宾页：摄像头授权（真机必需） */

const CAMERA_DENIED_HINT =
  '未授予摄像头权限。请在手机系统设置中允许本小程序/支付宝使用摄像头，然后返回重试。'

function getAppCameraAuth() {
  return new Promise((resolve) => {
    my.getSetting({
      success: (res) => {
        const auth = (res && res.authSetting) || {}
        if (auth.camera === true || auth['scope.camera'] === true) resolve('authorized')
        else if (auth.camera === false || auth['scope.camera'] === false) resolve('denied')
        else resolve('not determined')
      },
      fail: () => resolve('not determined')
    })
  })
}

function openCameraSettings() {
  return new Promise((resolve, reject) => {
    my.openSetting({
      success: async () => {
        const status = await getAppCameraAuth()
        if (status === 'authorized') resolve()
        else reject(new Error(CAMERA_DENIED_HINT))
      },
      fail: () => reject(new Error('无法打开设置页'))
    })
  })
}

function promptCameraPermission() {
  return new Promise((resolve, reject) => {
    my.confirm({
      title: '需要摄像头权限',
      content: '人脸迎宾需前置摄像头实时采集，请在设置中允许「摄像头」。',
      confirmButtonText: '去设置',
      cancelButtonText: '取消',
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

/** 仅检查是否曾被明确拒绝；未决定时直接挂载 camera 由系统弹窗 */
async function ensureCameraReady() {
  const status = await getAppCameraAuth()
  if (status === 'denied') {
    await promptCameraPermission()
  }
}

async function handleCameraError(errMsg) {
  const msg = String(errMsg || '')
  const authRelated = /auth|deny|permission|authorize|权限|未授权/i.test(msg)
  if (authRelated) {
    try {
      await promptCameraPermission()
      return true
    } catch (_) {
      my.alert({ title: '无法打开摄像头', content: CAMERA_DENIED_HINT })
      return false
    }
  }
  my.alert({ title: '摄像头不可用', content: msg || '请检查权限与光线后重试' })
  return false
}

module.exports = {
  ensureCameraReady,
  handleCameraError,
  getAppCameraAuth,
  CAMERA_DENIED_HINT
}
