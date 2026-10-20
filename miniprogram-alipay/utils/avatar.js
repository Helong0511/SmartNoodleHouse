/** 会员头像：本地临时路径需上传；支付宝 chooseAvatar 常返回 CDN 地址，须服务端转存 */

const api = require('./api')
const auth = require('./auth')
const media = require('./media')

function isPersistableAvatarUrl(url) {
  const s = String(url || '').trim()
  if (!s) return false
  if (s.startsWith('/uploads/')) return true
  if (/^https?:\/\//i.test(s) && s.includes('/uploads/')) return true
  return false
}

/** uploadFile 可接受的本地路径（支付宝 https://resource/、微信 wxfile/tmp 等） */
function isLocalUploadPath(url) {
  const s = String(url || '').trim()
  if (!s) return false
  if (/^https:\/\/resource\//i.test(s)) return true
  if (/^(wxfile|apfile|file|content):\/\//i.test(s)) return true
  if (/^http:\/\/tmp\//i.test(s)) return true
  if (/^http:\/\/127\.0\.0\.1(?::\d+)?\//i.test(s)) return true
  if (!/^https?:\/\//i.test(s) && !s.startsWith('data:')) return true
  return false
}

function isRemoteHttpUrl(url) {
  const s = String(url || '').trim()
  if (!/^https?:\/\//i.test(s)) return false
  if (isPersistableAvatarUrl(s) || isLocalUploadPath(s)) return false
  return true
}

function needsUpload(url) {
  const s = String(url || '').trim()
  if (!s) return false
  return !isPersistableAvatarUrl(s)
}

function resolveDisplayUrl(url) {
  const s = String(url || '').trim()
  if (!s) return ''
  if (needsUpload(s)) return s
  return media.resolveUrl(s)
}

function parseUploadBody(raw) {
  if (raw == null) return {}
  if (typeof raw === 'object') return raw
  try {
    return JSON.parse(String(raw))
  } catch (_) {
    return { message: String(raw) }
  }
}

function formatUploadError(err, fallback) {
  const msg = String((err && err.message) || err || '')
  if (/filePath|invalid path|无效/i.test(msg)) {
    return '头像转存失败，请改用「从相册选择头像」'
  }
  return msg || fallback || '头像上传失败，注册时将重试'
}

/**
 * 将本地临时头像上传到服务端，返回持久 /uploads/avatars/... 路径
 */
async function uploadLocalAvatar(filePath) {
  const path = String(filePath || '').trim()
  if (!path) throw new Error('未选择头像文件')
  await auth.ensureLogin()
  const app = auth.getAppSafe()
  const base = (app && app.globalData && app.globalData.apiBase) || ''
  if (!base) throw new Error('未配置 apiBase')
  const token = (app && app.globalData && app.globalData.customerToken)
    || auth.readStorage('customer_token')
    || ''
  if (!token) throw new Error('请先登录后再上传头像')

  return new Promise((resolve, reject) => {
    my.uploadFile({
      url: `${base}/miniprogram/member/avatar`,
      filePath: path,
      fileName: 'file',
      name: 'file',
      header: {
        Authorization: `Bearer ${token}`
      },
      success(res) {
        const status = res.statusCode != null ? res.statusCode : res.status
        const body = parseUploadBody(res.data)
        const msg = body.message || body.detail || `上传失败(${status || '?'})`
        if ((status && status >= 400) || (body.code !== 0 && body.code !== undefined)) {
          reject(Object.assign({ message: String(msg) }, { code: body.code, status }))
          return
        }
        const data = body.data || body
        const avatarUrl = (data && data.avatar_url) || ''
        if (!avatarUrl) {
          reject({ message: '上传成功但未返回头像地址' })
          return
        }
        resolve(avatarUrl)
      },
      fail(err) {
        const { localizeNativeErrMsg } = require('./errMsg')
        const raw = (err && (err.errorMessage || err.errMsg || err.message)) || '头像上传失败'
        reject({
          message: localizeNativeErrMsg(raw) || raw
        })
      }
    })
  })
}

async function importRemoteAvatar(sourceUrl) {
  const url = String(sourceUrl || '').trim()
  if (!url) throw new Error('未选择头像文件')
  await auth.ensureLogin()
  const data = await api.post('/miniprogram/member/avatar-from-url', { source_url: url })
  const avatarUrl = (data && data.avatar_url) || ''
  if (!avatarUrl) throw new Error('导入头像未返回地址')
  return avatarUrl
}

function persistablePath(url) {
  const s = String(url || '')
  if (s.includes('/uploads/')) {
    return s.slice(s.indexOf('/uploads/')).split('?')[0]
  }
  return s
}

/** 已是持久地址则原样返回；CDN 网络图走服务端转存；本地临时路径则 uploadFile */
async function ensureRemoteAvatar(url) {
  const s = String(url || '').trim()
  if (!s) return ''
  if (!needsUpload(s)) return persistablePath(s)
  if (isRemoteHttpUrl(s)) return importRemoteAvatar(s)
  try {
    return await uploadLocalAvatar(s)
  } catch (e) {
    const msg = String((e && e.message) || '')
    if (/filePath|invalid|无效/i.test(msg) && /^https?:\/\//i.test(s) && !isPersistableAvatarUrl(s)) {
      return importRemoteAvatar(s)
    }
    throw e
  }
}

module.exports = {
  isPersistableAvatarUrl,
  isLocalUploadPath,
  isRemoteHttpUrl,
  needsUpload,
  resolveDisplayUrl,
  formatUploadError,
  uploadLocalAvatar,
  importRemoteAvatar,
  ensureRemoteAvatar
}
