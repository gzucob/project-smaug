export const repository = 'gzucob/project-smaug';
export const githubURL = `https://github.com/${repository}`;
export type Platform = 'windows' | 'macos' | 'linux';
export type Architecture = 'amd64' | 'arm64';
export type Edition = 'full' | 'backend';
export type PublishedRelease = {
  version: string | null;
  assets: Record<string, string>;
};

export function parsePublishedRelease(value: unknown): PublishedRelease | null {
  if (!value || typeof value !== 'object' || !('version' in value) || !('assets' in value)) return null;
  if (value.version !== null && (typeof value.version !== 'string' || !/^\d{4}\.\d{2}\.\d{2}-[a-f0-9]{12}$/.test(value.version))) return null;
  if (!value.assets || typeof value.assets !== 'object' || Array.isArray(value.assets)) return null;
  const assets: Record<string, string> = {};
  for (const [name, url] of Object.entries(value.assets)) {
    if (typeof url !== 'string') return null;
    assets[name] = url;
  }
  return { version: value.version, assets };
}

export function installerName(platform: Platform, architecture: Architecture, edition: Edition): string {
  const prefix = edition === 'full' ? 'smaug-installer' : 'smaug-backend-installer';
  if (platform === 'windows') return `${prefix}${architecture === 'arm64' ? '-arm64' : ''}.exe`;
  return `${prefix}-${platform}-${architecture}.${platform === 'macos' ? 'dmg' : 'deb'}`;
}

export function detectPlatform(userAgent: string): Platform | '' {
  if (/Android|iPhone|iPad|iPod/i.test(userAgent)) return '';
  if (/Windows/i.test(userAgent)) return 'windows';
  if (/Macintosh|Mac OS X/i.test(userAgent)) return 'macos';
  if (/Linux/i.test(userAgent)) return 'linux';
  return '';
}

export function downloadURL(release: PublishedRelease | null, filename: string): string | null {
  if (!release?.version || !/^\d{4}\.\d{2}\.\d{2}-[a-f0-9]{12}$/.test(release.version)) return null;
  const expected = `${githubURL}/releases/download/${release.version}/${filename}`;
  return release.assets[filename] === expected ? expected : null;
}

export function dockerURL(platform: Platform | ''): string {
  const paths = { windows: 'windows-install/', macos: 'mac-install/', linux: 'linux/' };
  return `https://docs.docker.com/desktop/setup/install/${platform ? paths[platform] : ''}`;
}
