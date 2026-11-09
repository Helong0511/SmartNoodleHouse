/**
 * 上传前自检：列出所有必须在发布前手动处理的项。
 * 用法：node scripts/preflight-check.js
 */
const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')
const problems = []

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8')
}

// 1) 生产 API 是否为占位符
const envSrc = read('config/env.js')
if (/PROD_API_BASE\s*=\s*['"]https:\/\/your-domain\.com/.test(envSrc)) {
  problems.push('config/env.js: PROD_API_BASE 仍是占位符 your-domain.com，正式版会全部请求失败')
}

// 2) 内部文档是否排除上传
if (fs.existsSync(path.join(root, 'config/环境说明.txt'))) {
  try {
    const pc = JSON.parse(read('mini.project.json'))
    const ignored = (pc.ignore || []).some(
      (i) => i === 'config/环境说明.txt' || i === '/config/环境说明.txt'
    )
    if (!ignored) {
      problems.push('config/环境说明.txt: 含后端启动命令，需在 mini.project.json ignore 中排除')
    }
  } catch (_) {
    problems.push('mini.project.json: 无法解析，无法确认环境说明是否已忽略')
  }
}

// 4) 精确核对正式版日志闸门，不能只靠 app.js 任意位置出现 isRelease() 误判通过
const appSrc = read('app.js')
if (!/if\s*\(\s*!isRelease\(\)\s*\)\s*remoteLogger\.install\(\)/.test(appSrc)) {
  problems.push('app.js: remoteLogger.install() 必须由 if (!isRelease()) 精确保护')
}
const loggerSrc = read('utils/logger.js')
if (!/function remoteReportingAllowed\(\)[\s\S]*?return !require\(['"]\.\.\/config\/env['"]\)\.isRelease\(\)/.test(loggerSrc)) {
  problems.push('utils/logger.js: reportApi/push 缺少独立正式版外发闸门')
}
const apiSrc = read('utils/api.js')
if (!/assertApiBaseConfigured\(base\)/.test(apiSrc)) {
  problems.push('utils/api.js: 首次真实请求前未调用 assertApiBaseConfigured')
}
if (!/const TRIAL_USE_PROD_API = true/.test(envSrc)) {
  problems.push('config/env.js: 上传体验版必须默认走 HTTPS API，禁止回退内网 HTTP')
}

if (!problems.length) {
  console.log('✅ 预检通过，可以上传')
  process.exit(0)
}

console.log('❌ 上传前有 ' + problems.length + ' 项待处理：\n')
problems.forEach((p, i) => console.log(`${i + 1}. ${p}`))
process.exit(1)

