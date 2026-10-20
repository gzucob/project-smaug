import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createManifest } from './prepare-release.mjs';
import { githubURL, installerName } from '../src/downloads.ts';

function publishedRelease() {
  const tag = '2026.10.18-aaaaaaaaaaaa';
  const assets = [];
  for (const edition of ['full', 'backend']) {
    for (const platform of ['windows', 'macos', 'linux']) {
      for (const architecture of ['amd64', 'arm64']) {
        const name = installerName(platform, architecture, edition);
        for (const filename of [name, `${name}.sha256`]) {
          assets.push({ name: filename, state: 'uploaded', size: 100, browser_download_url: `${githubURL}/releases/download/${tag}/${filename}` });
        }
      }
    }
  }
  return { tag_name: tag, draft: false, prerelease: false, published_at: '2026-10-18T00:00:00Z', assets };
}

test('site downloads come from a complete published release', () => {
  const manifest = createManifest(publishedRelease());
  assert.equal(Object.keys(manifest.assets).length, 12);
  assert.ok(manifest.assets['smaug-backend-installer-macos-arm64.dmg']);
});

test('old releases, missing checksums and wrong download origins cannot be deployed', () => {
  for (const mutate of [
    (release) => { release.draft = true; },
    (release) => { release.prerelease = true; },
    (release) => { release.published_at = null; },
    (release) => { release.assets.pop(); },
    (release) => { release.assets[0].browser_download_url = 'https://example.com/file'; },
    (release) => { release.assets[0].state = 'new'; },
    (release) => { release.assets[0].size = 0; },
    (release) => { release.assets.push(release.assets[0]); },
  ]) {
    const release = publishedRelease();
    mutate(release);
    assert.throws(() => createManifest(release));
  }
});
