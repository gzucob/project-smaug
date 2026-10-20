/** Generate download metadata only from a complete, published installer release. */
import { writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { githubURL, installerName, repository } from '../src/downloads.ts';

export function createManifest(release) {
  if (release.draft !== false || release.prerelease !== false || !release.published_at ||
      !/^\d{4}\.\d{2}\.\d{2}-[a-f0-9]{12}$/.test(release.tag_name)) {
    throw new Error('A published stable installer release is required');
  }
  const assets = {};
  const prefix = `${githubURL}/releases/download/${release.tag_name}/`;
  for (const edition of ['full', 'backend']) {
    for (const platform of ['windows', 'macos', 'linux']) {
      for (const architecture of ['amd64', 'arm64']) {
        const name = installerName(platform, architecture, edition);
        for (const filename of [name, `${name}.sha256`]) {
          const matches = release.assets?.filter((asset) => asset.name === filename) ?? [];
          const asset = matches[0];
          if (matches.length !== 1 || asset.state !== 'uploaded' || !(asset.size > 0) ||
              asset.browser_download_url !== prefix + filename) {
            throw new Error(`Missing or invalid installer asset: ${filename}`);
          }
        }
        assets[name] = prefix + name;
      }
    }
  }
  return { version: release.tag_name, assets };
}

async function main() {
  const headers = { Accept: 'application/vnd.github+json' };
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers, signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(`Release lookup failed: HTTP ${response.status}`);
  const manifest = createManifest(await response.json());
  await writeFile(new URL('../public/release.json', import.meta.url), JSON.stringify(manifest, null, 2) + '\n');
  console.log(`Prepared ${Object.keys(manifest.assets).length} installers for ${manifest.version}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fileURLToPath(pathToFileURL(process.argv[1]))) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
