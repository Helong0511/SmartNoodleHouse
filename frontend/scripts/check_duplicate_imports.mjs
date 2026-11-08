#!/usr/bin/env node
/**
 * 检查前端和双端小程序的重复 ES import / CommonJS require。
 * 只报告同一文件、同一模块的重复引用，不自动改写业务代码。
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";

const REPO_ROOT = path.resolve(process.cwd(), "..");
const SCAN_ROOTS = ["src", "miniprogram-wechat", "miniprogram-alipay"];
const EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"]);
const failures = [];
let scanned = 0;

function collectFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist" || entry.name === "miniprogram_npm") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...collectFiles(full));
    else if (EXTENSIONS.has(path.extname(entry.name))) found.push(full);
  }
  return found;
}

function scriptBlocks(source, file) {
  if (path.extname(file) !== ".vue") return [{ text: source, kind: null }];
  const blocks = [];
  const pattern = /<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi;
  for (const match of source.matchAll(pattern)) {
    const opening = match[0].slice(0, match[0].indexOf(">"));
    const kind = /lang=["']js["']|lang=["']javascript["']/i.test(opening)
      ? ts.ScriptKind.JS : ts.ScriptKind.TS;
    blocks.push({ text: match[1], kind });
  }
  return blocks;
}

for (const rootName of SCAN_ROOTS) {
  const root = rootName === "src" ? path.join(process.cwd(), "src") : path.join(REPO_ROOT, rootName);
  for (const file of collectFiles(root)) {
    scanned += 1;
    let source;
    try { source = fs.readFileSync(file, "utf8"); }
    catch (error) {
      failures.push(`${path.relative(REPO_ROOT, file)}: 读取失败：${error.message}`);
      continue;
    }
    for (const [blockIndex, block] of scriptBlocks(source, file).entries()) {
      const extension = path.extname(file);
      const kind = block.kind || ([ ".js", ".mjs", ".cjs" ].includes(extension) ? ts.ScriptKind.JS : extension === ".jsx" ? ts.ScriptKind.JSX : ts.ScriptKind.TS);
      const sf = ts.createSourceFile(file, block.text, ts.ScriptTarget.Latest, true, kind);
      const seen = new Map();
      const scopeStack = ["module"];
      function visit(node) {
        const isFunctionScope = ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node)
          || ts.isArrowFunction(node) || ts.isMethodDeclaration(node)
          || ts.isConstructorDeclaration(node) || ts.isGetAccessorDeclaration(node)
          || ts.isSetAccessorDeclaration(node);
        const isBlockScope = ts.isBlock(node);
        if (isFunctionScope || isBlockScope) {
          const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
          scopeStack.push((isFunctionScope ? "function@" : "block@") + (pos.line + 1));
        }
        let specifier = null;
        let typeOnly = false;
        if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
          specifier = node.moduleSpecifier.text;
          typeOnly = !!node.importClause?.isTypeOnly;
        } else if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
          specifier = node.moduleSpecifier.text;
          typeOnly = !!node.isTypeOnly;
        } else if (ts.isCallExpression(node) && node.arguments.length === 1
          && ts.isStringLiteral(node.arguments[0])
          && ((node.expression.kind === ts.SyntaxKind.Identifier && node.expression.text === "require")
            || node.expression.kind === ts.SyntaxKind.ImportKeyword)) {
          specifier = "dynamic:" + node.arguments[0].text;
        }
        if (specifier !== null) {
          const key = scopeStack.join("/") + "/" + (typeOnly ? "type" : "value") + ":" + specifier;
          const previous = seen.get(key);
          if (previous !== undefined) {
            const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
            failures.push(`${path.relative(REPO_ROOT, file)}:${pos.line + 1}: 重复引用 ${specifier}（首次位于第 ${previous} 行；script 块 ${blockIndex + 1}）`);
          } else {
            const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
            seen.set(key, pos.line + 1);
          }
        }
        ts.forEachChild(node, visit);
        if (isFunctionScope || isBlockScope) scopeStack.pop();
      }
      visit(sf);
      for (const diagnostic of sf.parseDiagnostics) {
        const pos = sf.getLineAndCharacterOfPosition(diagnostic.start ?? 0);
        failures.push(`${path.relative(REPO_ROOT, file)}:${pos.line + 1}: JavaScript/TypeScript 语法解析失败`);
      }
    }
  }
}

if (failures.length) {
  console.error("重复导入检查失败：");
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}
console.log(`重复导入检查通过：已扫描 ${scanned} 个 JS/TS/Vue 文件。`);
