import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import instrumentation from "istanbul-lib-instrument";

const root = fileURLToPath(new URL("../../", import.meta.url));
const c8 = fileURLToPath(new URL("./node_modules/c8/bin/c8.js", import.meta.url));
const reports = mkdtempSync(join(tmpdir(), "smaug-js-coverage-"));
const suites = [
  {
    label: "Product frontend",
    cwd: join(root, "frontend"),
    sources: ["src/**", "scripts/**", "next.config.ts", "postcss.config.mjs"],
    tests: ["tests/*.test.mjs"],
  },
  {
    label: "Distribution website",
    cwd: join(root, "website"),
    sources: ["src/**", "scripts/**", "vite.config.ts"],
    tests: ["src/downloads.test.ts", "scripts/prepare-release.test.mjs"],
  },
  {
    label: "Installer interface",
    cwd: join(root, "installer"),
    sources: ["ui/**/*.js"],
    tests: ["tests/*.test.cjs"],
  },
];

let failed = false;
try {
  for (const [index, suite] of suites.entries()) {
    console.log(suite.label);
    const report = join(reports, String(index));
    const result = spawnSync(process.execPath, [
      c8, "--all", "--reporter=json", "--report-dir", report,
      "--temp-directory", join(report, "v8"),
      ...suite.sources.flatMap((glob) => ["--include", glob]),
      "--exclude", "**/*.test.*", "--exclude", "**/*.d.ts",
      process.execPath, "--test", ...suite.tests,
    ], { cwd: suite.cwd, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) {
      failed = true;
      continue;
    }
    const files = JSON.parse(readFileSync(join(report, "coverage-final.json"), "utf8"));
    if (!Object.keys(files).length) throw new Error(`No coverage for ${suite.label}`);
    for (const [path, file] of Object.entries(files)) {
      const instrumenter = instrumentation.createInstrumenter({
        parserPlugins: path.endsWith(".ts") ? ["typescript"] : ["typescript", "jsx"],
      });
      instrumenter.instrumentSync(readFileSync(path, "utf8"), path);
      const statements = Object.values(instrumenter.lastFileCoverage().statementMap);
      const executable = new Set(statements.map(({ start }) => start.line));
      const covered = new Set();
      for (const [id, { start }] of Object.entries(file.statementMap)) {
        if (executable.has(start.line) && file.s[id] > 0) {
          covered.add(start.line);
        }
      }
      if (covered.size * 10 < executable.size * 9) {
        failed = true;
        console.error(`${path}: ${covered.size}/${executable.size} executable lines`);
      }
    }
  }
} finally {
  rmSync(reports, { recursive: true, force: true });
}
process.exitCode = Number(failed);
