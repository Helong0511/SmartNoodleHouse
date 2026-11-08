#!/usr/bin/env node
/**
 * 核对前端直接依赖是否被源码、构建配置、测试或 npm scripts 使用。
 * 仅对明确的类型声明包使用 allowlist，并写出原因。
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const ROOT = process.cwd();
const packageJson = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
const sourceFiles = [];
const SKIP = new Set(["node_modules", "dist", ".git", "coverage"]);

function walk(dir) {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory() && SKIP.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(?:js|jsx|mjs|cjs|ts|tsx|vue)$/.test(entry.name)) sourceFiles.push(full);
  }
}
walk(ROOT);

const imported = new Set();
for (const file of sourceFiles) {
  const source = fs.readFileSync(file, "utf8");
  const blocks = path.extname(file) === ".vue"
    ? [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)].map(x => x[1])
    : [source];
  for (const block of blocks) {
    const ext = path.extname(file);
    const kind = ext === ".js" || ext === ".mjs" || ext === ".cjs"
      ? ts.ScriptKind.JS : ext === ".jsx" ? ts.ScriptKind.JSX : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(file, block, ts.ScriptTarget.Latest, true, kind);
    function visit(node) {
      let specifier = null;
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        specifier = node.moduleSpecifier.text;
      } else if (ts.isCallExpression(node) && node.arguments.length === 1
        && ts.isStringLiteral(node.arguments[0])
        && (node.expression.kind === ts.SyntaxKind.Identifier && node.expression.text === "require"
          || node.expression.kind === ts.SyntaxKind.ImportKeyword)) {
        specifier = node.arguments[0].text;
      }
      if (specifier && !specifier.startsWith(".") && !specifier.startsWith("/")) imported.add(specifier);
      ts.forEachChild(node, visit);
    }
    visit(sf);
  }
}

const scriptsText = Object.values(packageJson.scripts || {}).join("\n");
const allowlist = {
  "@types/qrcode": "为 qrcode 提供 TypeScript 声明；由 TS 类型解析器自动加载，不直接 import",
};
const all = { ...(packageJson.dependencies || {}), ...(packageJson.devDependencies || {}) };
const unused = [];
for (const name of Object.keys(all).sort()) {
  const found = [...imported].some(x => x === name || x.startsWith(name + "/"));
  const binary = name === "vite" ? /\bvite\b/.test(scriptsText)
    : name === "vitest" ? /\bvitest\b/.test(scriptsText)
    : name === "vue-tsc" ? /\bvue-tsc\b/.test(scriptsText)
    : name === "typescript" ? /\btsc\b/.test(scriptsText) || imported.has("typescript")
    : false;
  if (!found && !binary && !allowlist[name]) unused.push(name);
}
if (unused.length) {
  console.error("发现未引用的前端直接依赖（请确认后删除或登记用途）：");
  for (const name of unused) console.error(` - ${name}`);
  process.exit(1);
}
console.log(`前端依赖引用审计通过：${Object.keys(all).length} 个直接依赖，扫描 ${sourceFiles.length} 个源码/配置文件。`);
