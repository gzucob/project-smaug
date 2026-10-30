import { useEffect, useState } from 'react';
import { detectPlatform, dockerURL, downloadURL, githubURL, installerName, parsePublishedRelease } from './downloads';
import type { Architecture, Edition, Platform, PublishedRelease } from './downloads';

export function App() {
  const [platform, setPlatform] = useState<Platform | ''>(() => detectPlatform(navigator.userAgent));
  const [architecture, setArchitecture] = useState<Architecture | ''>('');
  const [edition, setEdition] = useState<Edition>('full');
  const [release, setRelease] = useState<PublishedRelease | null>(null);
  const [loading, setLoading] = useState(true);
  const filename = platform && architecture ? installerName(platform, architecture, edition) : '';
  const download = downloadURL(release, filename);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${import.meta.env.BASE_URL}release.json`, { signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error('Release unavailable'); return response.json(); })
      .then((data: unknown) => { if (!controller.signal.aborted) setRelease(parsePublishedRelease(data)); })
      .catch(() => { /* The release page remains available if metadata cannot load. */ })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  return (
    <div className="page-shell">
      <a className="skip-link" href="#download">Ir para o download</a>
      <header className="flex items-center justify-between gap-6 py-7">
        <a className="brand flex items-center gap-3" href={import.meta.env.BASE_URL} aria-label="Smaug, início">
          <img src={`${import.meta.env.BASE_URL}smaug.svg`} width="38" height="38" alt="" />
          <span>SMAUG</span>
        </a>
        <a className="quiet-link" href={githubURL}>GitHub <span aria-hidden="true">↗</span></a>
      </header>

      <main>
        <section className="hero grid items-center gap-12 lg:grid-cols-[1.1fr_1fr] lg:gap-16" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">DADOS PÚBLICOS · CVM E B3</p>
            <h1 id="page-title">Análise de ações,<br />no seu computador.</h1>
            <p className="intro">Indicadores, demonstrações financeiras e históricos em uma aplicação local.</p>
          </div>

          <section id="download" className="download-panel" aria-labelledby="download-title">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="download-title">Baixar o Smaug</h2>
              {release?.version && <span className="version">{release.version.split('-')[0]}</span>}
            </div>
            <fieldset className="edition-selector mt-5 grid grid-cols-2 gap-1">
              <legend className="sr-only">Versão do Smaug</legend>
              {(['full', 'backend'] as const).map((value) => (
                <label key={value} className="edition-option">
                  <input type="radio" name="edition" value={value} checked={edition === value} onChange={() => setEdition(value)} />
                  <span>{value === 'full' ? 'Completo' : 'Backend'}</span>
                </label>
              ))}
            </fieldset>
            <p className="edition-description">{edition === 'full' ? 'Interface web, API e MCP.' : 'API e MCP, sem interface web.'} Os mesmos dados nas duas versões.</p>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <label className="field-label">Sistema
                <select value={platform} onChange={(event) => { setPlatform(event.target.value as Platform); setArchitecture(''); }}>
                  <option value="" disabled>Escolha o sistema</option>
                  <option value="windows">Windows</option>
                  <option value="macos">macOS</option>
                  <option value="linux">Ubuntu / Debian</option>
                </select>
              </label>
              <label className="field-label">Arquitetura
                <select value={architecture} onChange={(event) => setArchitecture(event.target.value as Architecture)} disabled={!platform}>
                  <option value="" disabled>Escolha a arquitetura</option>
                  <option value="amd64">{platform === 'macos' ? 'Intel' : 'Intel / AMD (x64)'}</option>
                  <option value="arm64">{platform === 'macos' ? 'Apple Silicon (M1 ou superior)' : 'ARM64'}</option>
                </select>
              </label>
            </div>

            <div className="mt-6" aria-live="polite">
              {download ? (
                <a className="download-button flex items-center justify-between gap-4" href={download}>
                  <span>Baixar {edition === 'full' ? 'Smaug' : 'Smaug Backend'}</span><span aria-hidden="true">↓</span>
                </a>
              ) : <button className="download-button w-full" disabled>{loading ? 'Verificando versão…' : !release?.version || (platform && architecture) ? 'Instalador indisponível' : 'Escolha sistema e arquitetura'}</button>}
              <p className="download-note">{download ? `${filename} · Instalação sem terminal` : loading ? ' ' : !release?.version || (platform && architecture) ? <>Consulte os <a href={`${githubURL}/releases/latest`}>downloads no GitHub</a>.</> : 'Selecione o computador em que vai instalar.'}</p>
            </div>
          </section>
        </section>

        <section className="getting-started" aria-labelledby="start-title">
          <h2 id="start-title">Para começar</h2>
          <ol className="mt-7 grid gap-7 md:grid-cols-3 md:gap-9">
            <li><span className="step-number" aria-hidden="true">1</span><h3>Instale o Docker Desktop</h3><p><a href={dockerURL(platform)}>Baixe para o seu sistema <span aria-hidden="true">↗</span></a> e mantenha-o em execução.</p></li>
            <li><span className="step-number" aria-hidden="true">2</span><h3>Abra o instalador</h3><p>Siga a instalação. Os dados serão preparados automaticamente.</p></li>
            <li><span className="step-number" aria-hidden="true">3</span><h3>{edition === 'full' ? 'Acesse o Smaug' : 'Acesse a API'}</h3><p>Ao concluir, clique em <strong>{edition === 'full' ? 'Abrir o Smaug' : 'Abrir a API'}</strong>.</p></li>
          </ol>
        </section>
      </main>

      <footer className="flex flex-wrap items-center justify-between gap-5 py-7">
        <span>Smaug</span>
        <nav aria-label="Guias" className="flex flex-wrap gap-x-6 gap-y-3">
          <a href={`${githubURL}/blob/main/docs/installation.md`}>Instalação</a>
          <a href={`${githubURL}/blob/main/docs/mcp.md`}>Configurar MCP</a>
          <a href={`${githubURL}/blob/main/docs/development.md`}>Desenvolvimento</a>
        </nav>
      </footer>
    </div>
  );
}
