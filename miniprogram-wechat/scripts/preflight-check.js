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

// 2) urlCheck 是否为 false
;[['project.config.json', '项目配置'], ['project.private.config.json', '本地私有配置']].forEach(([f, label]) => {
  try {
    const j = JSON.parse(read(f))
    if (j.setting && j.setting.urlCheck === false) {
      problems.push(`${f}（${label}）: setting.urlCheck = false，上传前必须改为 true`)
    }
  } catch (_) {}
})

// 3) 内部文档是否仍在包内
if (fs.existsSync(path.join(root, 'config/环境说明.txt'))) {
  const pc = JSON.parse(read('project.config.json'))
  const ignored = ((pc.packOptions || {}).ignore || []).some(
    (i) => i.value === 'config/环境说明.txt'
  )
  if (!ignored) {
    problems.push('config/环境说明.txt: 含后端启动命令与内网 IP，需移出上传包（见 project.config.json packOptions.ignore）')
  }
}

// 4) 远程日志是否仅开发版启用
const appSrc = read('app.js')
if (/remoteLogger\.install\(\)/.test(appSrc) && !/isRelease\(\)/.test(appSrc)) {
  problems.push('app.js: remoteLogger.install() 未加环境闸门，正式版仍会上报全量日志')
}

if (!problems.length) {
  console.log('✅ 预检通过，可以上传')
  process.exit(0)
}

console.log('❌ 上传前有 ' + problems.length + ' 项待处理：\n')
problems.forEach((p, i) => console.log(`${i + 1}. ${p}`))
process.exit(1)

