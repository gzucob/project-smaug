import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(frontendRoot, "src");
const configPath = path.join(frontendRoot, "tsconfig.json");
const serverApiPath = path.join(sourceRoot, "lib", "api.ts");
const favoriteApiPath = path.join(sourceRoot, "lib", "favorites-api.ts");
const revisionApiPath = path.join(sourceRoot, "lib", "data-revision-api.ts");
const approvedSameOriginRoutes = new Map([
  [path.resolve(favoriteApiPath), { kind: "prefix", path: "/api/portfolio/" }],
  [path.resolve(revisionApiPath), { kind: "exact", path: "/api/data-revision" }],
]);
const sourceExtensions = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".mts",
  ".cts",
]);

const config = ts.readConfigFile(configPath, ts.sys.readFile);
if (config.error) {
  console.error(ts.flattenDiagnosticMessageText(config.error.messageText, "\n"));
  process.exit(1);
}

const parsedConfig = ts.parseJsonConfigFileContent(config.config, ts.sys, frontendRoot);
const compilerOptions = parsedConfig.options;

function collectSourceFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectSourceFiles(entryPath));
    } else if (entry.isFile() && sourceExtensions.has(path.extname(entry.name))) {
      files.push(entryPath);
    }
  }
  return files;
}

function createSourceFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  const extension = path.extname(filePath);
  const scriptKind = extension.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, scriptKind);
}

function isUseClientFile(sourceFile) {
  for (const statement of sourceFile.statements) {
    if (!ts.isExpressionStatement(statement) || !ts.isStringLiteral(statement.expression)) {
      return false;
    }
    if (statement.expression.text === "use client") return true;
  }
  return false;
}

function moduleSpecifiers(sourceFile) {
  const specifiers = [];
  function visit(node) {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      specifiers.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === "require")) &&
      node.arguments.length === 1 &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      specifiers.push(node.arguments[0].text);
    } else if (
      ts.isImportTypeNode(node) &&
      ts.isLiteralTypeNode(node.argument) &&
      ts.isStringLiteral(node.argument.literal)
    ) {
      specifiers.push(node.argument.literal.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return specifiers;
}

function resolveLocalImport(specifier, containingFile) {
  const resolved = ts.resolveModuleName(
    specifier,
    containingFile,
    compilerOptions,
    ts.sys,
  ).resolvedModule?.resolvedFileName;
  if (!resolved) return null;

  const normalized = path.resolve(resolved);
  const relative = path.relative(frontendRoot, normalized);
  const segments = relative.split(path.sep);
  const isInsideFrontend =
    relative !== "" &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative);
  const isGeneratedOrDependency = segments.some(
    (segment) => segment === "node_modules" || segment === ".next" || segment === ".next-local",
  );
  return isInsideFrontend && !isGeneratedOrDependency && sourceExtensions.has(path.extname(normalized))
    ? normalized
    : null;
}

function isApprovedSameOriginRoute(node, sourceFile) {
  if (!ts.isCallExpression(node) || !ts.isIdentifier(node.expression) || node.expression.text !== "fetch") {
    return false;
  }
  const allowedRoute = approvedSameOriginRoutes.get(path.resolve(sourceFile.fileName));
  if (!allowedRoute) return false;
  const [url] = node.arguments;
  if (!url) return false;
  if (ts.isStringLiteral(url) || ts.isNoSubstitutionTemplateLiteral(url)) {
    return allowedRoute.kind === "exact"
      ? url.text === allowedRoute.path
      : url.text.startsWith(allowedRoute.path);
  }
  return (
    allowedRoute.kind === "prefix" &&
    ts.isTemplateExpression(url) &&
    url.head.text.startsWith(allowedRoute.path)
  );
}

function networkCall(node, sourceFile) {
  if (ts.isCallExpression(node)) {
    const expression = node.expression;
    if (ts.isIdentifier(expression) && expression.text === "fetch") {
      return isApprovedSameOriginRoute(node, sourceFile) ? null : "fetch()";
    }
    if (
      ts.isPropertyAccessExpression(expression) &&
      expression.name.text === "fetch"
    ) {
      return `${expression.getText(sourceFile)}()`;
    }
    if (ts.isIdentifier(expression) && ["axios", "ky", "got", "request", "superagent"].includes(expression.text)) {
      return `${expression.text}()`;
    }
    if (
      ts.isPropertyAccessExpression(expression) &&
      ts.isIdentifier(expression.expression) &&
      ["axios", "ky", "got", "request", "superagent"].includes(expression.expression.text) &&
      ["get", "post", "put", "patch", "delete", "request"].includes(expression.name.text)
    ) {
      return `${expression.getText(sourceFile)}()`;
    }
    if (
      ts.isPropertyAccessExpression(expression) &&
      ts.isIdentifier(expression.expression) &&
      expression.expression.text === "navigator" &&
      expression.name.text === "sendBeacon"
    ) {
      return "navigator.sendBeacon()";
    }
  }

  if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)) {
    if (["XMLHttpRequest", "WebSocket", "EventSource"].includes(node.expression.text)) {
      return `new ${node.expression.text}()`;
    }
  }
  return null;
}

function describePath(filePath) {
  return path.relative(path.dirname(frontendRoot), filePath).split(path.sep).join("/");
}

const files = collectSourceFiles(sourceRoot);
const sourceFiles = new Map(files.map((filePath) => [filePath, createSourceFile(filePath)]));
const clientRoots = [...sourceFiles.entries()]
  .filter(([, sourceFile]) => isUseClientFile(sourceFile))
  .map(([filePath]) => filePath);
const violations = new Set();

for (const root of clientRoots) {
  const pending = [root];
  const visited = new Set();

  while (pending.length > 0) {
    const filePath = pending.pop();
    if (visited.has(filePath)) continue;
    visited.add(filePath);

    if (path.resolve(filePath) === path.resolve(serverApiPath)) {
      violations.add(
        `${describePath(root)} reaches ${describePath(serverApiPath)}. Keep FastAPI reads in Server Components through src/lib/api.ts; see frontend/AGENTS.md, “Data boundary”. The favorite toggle and silent revision probe have dedicated same-origin clients.`,
      );
    }

    const sourceFile = sourceFiles.get(filePath) ?? createSourceFile(filePath);
    if (path.resolve(filePath) !== path.resolve(serverApiPath)) {
      function visit(node) {
        const network = networkCall(node, sourceFile);
        if (network) {
          violations.add(
            `${describePath(filePath)} contains ${network} in the client dependency graph. Keep FastAPI reads in Server Components through src/lib/api.ts; see frontend/AGENTS.md, “Data boundary”. Use only the dedicated same-origin clients for favorites and silent revision checks.`,
          );
        }
        ts.forEachChild(node, visit);
      }
      visit(sourceFile);
    }

    for (const specifier of moduleSpecifiers(sourceFile)) {
      const imported = resolveLocalImport(specifier, filePath);
      if (imported) pending.push(imported);
    }
  }
}

if (violations.size > 0) {
  console.error("Client data boundary violations:\n");
  for (const violation of violations) console.error(`- ${violation}`);
  process.exit(1);
}

console.log(`Client data boundary passed (${clientRoots.length} client entrypoints checked).`);
