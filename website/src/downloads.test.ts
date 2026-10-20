import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { detectPlatform, downloadURL, githubURL, installerName, parsePublishedRelease } from './downloads.ts';

test('desktop downloads match the published installer names for both editions', () => {
  for (const [platform, architecture, suffix] of [
    ['windows', 'amd64', '.exe'], ['windows', 'arm64', '-arm64.exe'],
    ['macos', 'amd64', '-macos-amd64.dmg'], ['macos', 'arm64', '-macos-arm64.dmg'],
    ['linux', 'amd64', '-linux-amd64.deb'], ['linux', 'arm64', '-linux-arm64.deb'],
  ] as const) {
    assert.equal(installerName(platform, architecture, 'full'), `smaug-installer${suffix}`);
    assert.equal(installerName(platform, architecture, 'backend'), `smaug-backend-installer${suffix}`);
  }
});

test('platform suggestion does not treat phones as desktop installers', () => {
  assert.equal(detectPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)'), 'windows');
  assert.equal(detectPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X)'), 'macos');
  assert.equal(detectPlatform('Mozilla/5.0 (X11; Linux aarch64)'), 'linux');
  for (const ua of ['Linux; Android 15', 'iPhone; CPU iPhone OS like Mac OS X', 'iPad; CPU OS like Mac OS X', 'unknown']) {
    assert.equal(detectPlatform(ua), '');
  }
});

test('downloads require the exact asset in the selected published release', () => {
  const name = 'smaug-installer.exe';
  const version = '2026.10.18-aaaaaaaaaaaa';
  const url = `${githubURL}/releases/download/${version}/${name}`;
  assert.equal(downloadURL({ version, assets: { [name]: url } }, name), url);
  assert.equal(downloadURL({ version, assets: {} }, name), null);
  assert.equal(downloadURL({ version, assets: { [name]: 'https://example.com/installer.exe' } }, name), null);
  assert.equal(downloadURL({ version: null, assets: {} }, name), null);
  assert.equal(downloadURL(null, name), null);
});

test('unavailable or malformed release metadata cannot break the download page', () => {
  for (const value of [null, [], {}, { version: {}, assets: {} }, { version: 'latest', assets: {} }, { version: null, assets: [] }, { version: null, assets: { file: 123 } }]) {
    assert.equal(parsePublishedRelease(value), null);
  }
  assert.deepEqual(parsePublishedRelease({ version: null, assets: {} }), { version: null, assets: {} });
});
