'use strict';
const $ = (id) => document.getElementById(id);
let state = null;
let busyRequest = false;
let stopped = false;
let selectedEdition = false;
let actionError = '';
const dockerURLs = { windows: 'https://docs.docker.com/desktop/setup/install/windows-install/', darwin: 'https://docs.docker.com/desktop/setup/install/mac-install/', linux: 'https://docs.docker.com/desktop/setup/install/linux/' };
document.querySelectorAll('[name="edition"]').forEach((input) => input.addEventListener('change', () => { selectedEdition = true; }));
function render(next) {
  state = next;
  $('docker-title').textContent = next.docker ? 'Docker pronto' : 'Vamos preparar o ambiente';
  $('docker-message').textContent = next.docker_message || 'Verificando o Docker…';
  document.querySelector('.docker').classList.toggle('ready', next.docker);
  $('docker-download').href = dockerURLs[next.os] || dockerURLs.linux;
  if (!selectedEdition && !next.busy) document.querySelector(`[name="edition"][value="${next.edition}"]`).checked = true;
  const complete = next.installed && next.prepared;
  $('setup').hidden = complete;
  $('management').hidden = !complete;
  $('mcp').hidden = !complete;
  $('title').textContent = next.installed ? 'Gerenciar o Smaug' : 'Instalar o Smaug';
  $('install').textContent = next.installed ? 'Retomar instalação' : 'Instalar o Smaug';
  $('version').textContent = next.version;
  $('installation-state').textContent = `${next.edition === 'full' ? 'Completo' : 'Backend'} · ${next.running ? 'Em execução' : 'Serviços parados'}`;
  $('open').textContent = next.edition === 'full' ? 'Abrir o Smaug' : 'Abrir a API';
  $('open').disabled = !next.running || next.busy;
  $('start').hidden = next.running;
  $('stop').hidden = !next.running;
  $('claude').hidden = next.os === 'linux';
  const update = next.latest && next.version && next.latest !== next.version;
  $('upgrade').textContent = 'Atualizar o Smaug';
  $('update-notice').textContent = update ? `Nova versão disponível: ${next.latest}` : '';
  $('progress').hidden = !next.busy && !next.message && !next.error && !next.log && !actionError;
  $('progress-title').textContent = next.busy ? 'Preparando seu Smaug' : 'Status';
  $('working').hidden = !next.busy;
  $('activity').hidden = !next.busy;
  $('message').textContent = next.message;
  $('error').hidden = !next.error && !actionError;
  $('error').textContent = actionError || next.error;
  $('patience').hidden = !next.busy || !['install', 'upgrade'].includes(next.action);
  $('log').textContent = next.log;
  if (next.error) $('details').open = true;
  document.querySelectorAll('button').forEach((button) => {
    if (button.id === 'open') return;
    button.disabled = next.busy || busyRequest || (['install', 'start', 'upgrade', 'stop'].includes(button.dataset.action) && !next.docker) || (button.dataset.client && !next.docker);
  });
  $('editions').disabled = next.busy;
}
async function action(name, client) {
  if (busyRequest || !state || state.busy) return;
  busyRequest = true;
  actionError = '';
  render(state);
  try {
    const response = await fetch('action', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: name, edition: document.querySelector('[name="edition"]:checked').value, client: client || ''})});
    if (!response.ok) throw new Error(await response.text());
    if (name === 'close') { stopped = true; $('connection').hidden = false; $('connection').textContent = 'Gerenciador encerrado. Você pode fechar esta aba; os serviços do Smaug continuam como estavam.'; document.querySelectorAll('button').forEach((button) => {button.disabled = true;}); return; }
    await refresh();
  } catch (error) { actionError = error.message; $('error').hidden = false; $('error').textContent = error.message; $('progress').hidden = false; }
  finally { busyRequest = false; }
}
document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => action(button.dataset.action)));
document.querySelectorAll('[data-client]').forEach((button) => button.addEventListener('click', () => action('mcp', button.dataset.client)));
async function refresh() {
  const response = await fetch('status', {cache: 'no-store'});
  if (!response.ok) throw new Error('Conexão com o gerenciador indisponível.');
  render(await response.json());
  $('connection').hidden = true;
}
async function poll() {
  if (stopped) return;
  try {await refresh();} catch { $('connection').hidden = false; document.querySelectorAll('button').forEach((button) => {button.disabled = true;}); }
  if (!stopped) setTimeout(poll, 1500);
}
poll();
