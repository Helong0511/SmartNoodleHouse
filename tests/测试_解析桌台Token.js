/**
 * 桌台扫码 token 解析（微信/支付宝 utils/scan.js 同源逻辑）
 * 运行：node tests/test_parse_table_token.js
 */
const path = require('path')
const assert = require('assert')

const ali = require(path.join(__dirname, '../miniprogram-alipay/utils/scan.js'))
const wx = require(path.join(__dirname, '../miniprogram-wechat/utils/scan.js'))

const tok = '6jO4K28UbqPozFWvtT0uqPpR5WWwD3030ir2ORwQYWs'
const samples = [
  ['http://192.168.1.110:5173/scan?token=' + tok, tok],
  ['http://192.168.1.110:5173/scan?token=' + tok + '#/', tok],
  ['http://192.168.1.110:5173/#/scan?token=' + tok, tok],
  [
    'alipays://platformapi/startapp?appId=X&page=pages/index/index&query=token%3D'
      + tok
      + '%26channel%3Dalipay',
    tok,
  ],
  [
    'weixin://dl/business/?appid=X&path=pages/index/index&query=token%3D'
      + tok
      + '%26channel%3Dwechat',
    tok,
  ],
  ['token=' + tok + '&channel=alipay', tok],
  [tok, tok],
  ['pages/index/index?token=' + tok + '&channel=alipay', tok],
  [
    'alipays://platformapi/startapp?appId=X&page=pages%2Findex%2Findex%3Ftoken%3D'
      + tok
      + '%26channel%3Dalipay',
    tok,
  ],
  ['https://example.com/scan', ''],
  ['alipays://platformapi/startapp?appId=X&page=pages/index/index', ''],
]

let failed = 0
for (const [input, expect] of samples) {
  for (const [name, mod] of [
    ['alipay', ali],
    ['wechat', wx],
  ]) {
    const got = mod.parseTableTokenFromScan(input)
    if (got !== expect) {
      failed += 1
      console.error('FAIL', name, { input: input.slice(0, 90), expect, got })
    }
  }
}

const qObj = ali.normalizeLaunchQuery({ token: tok, channel: 'alipay' })
assert.strictEqual(qObj.token, tok)
const qStr = ali.normalizeLaunchQuery('token=' + tok + '&channel=alipay')
assert.strictEqual(qStr.token, tok)
assert.strictEqual(qStr.channel, 'alipay')
const qEnc = ali.normalizeLaunchQuery(
  'token%3D' + tok + '%26channel%3Dalipay'
)
assert.strictEqual(qEnc.token, tok)

if (failed) {
  console.error('failed', failed)
  process.exit(1)
}
console.log('ok', samples.length * 2, 'parse cases + normalizeLaunchQuery')
