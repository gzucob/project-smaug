package main

import (
	"context"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

//go:embed ui/*
var guiFiles embed.FS

const guiLogLimit = 256 * 1024

type guiSnapshot struct {
	Busy          bool   `json:"busy"`
	Action        string `json:"action"`
	Message       string `json:"message"`
	Error         string `json:"error"`
	Log           string `json:"log"`
	Installed     bool   `json:"installed"`
	Prepared      bool   `json:"prepared"`
	Running       bool   `json:"running"`
	Edition       string `json:"edition"`
	Version       string `json:"version"`
	Latest        string `json:"latest"`
	Docker        bool   `json:"docker"`
	DockerMessage string `json:"docker_message"`
	URL           string `json:"url"`
	OS            string `json:"os"`
}

type guiServer struct {
	mu                              sync.Mutex
	root, executable, origin, token string
	state                           guiSnapshot
	touched                         time.Time
	close                           chan struct{}
	execute                         func([]string, io.Writer) error
	open                            func(string) error
	inspect                         func()
}

func guiArguments(args []string) (string, bool) {
	home, err := os.UserConfigDir()
	if err != nil {
		return "", false
	}
	root := filepath.Join(home, "smaug")
	if len(args) >= 2 && args[0] == "--dir" {
		root, args = args[1], args[2:]
	}
	return root, len(args) == 0 || (len(args) == 1 && args[0] == "gui")
}

func runGUI(root string) error {
	// Desktop apps do not inherit shell profiles. Include Docker's install
	// locations so rechecking also works after installing it with this UI open.
	for _, value := range desktopEnvironment(os.Environ()) {
		if strings.HasPrefix(value, "PATH=") {
			os.Setenv("PATH", strings.TrimPrefix(value, "PATH="))
		}
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return err
	}
	unlock, err := lockOperation(filepath.Join(root, "gui.guard"))
	if err != nil {
		return reopenGUI(root)
	}
	defer unlock()
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer listener.Close()
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return err
	}
	g := &guiServer{root: root, executable: executable, origin: "http://" + listener.Addr().String(), token: hex.EncodeToString(secret), close: make(chan struct{}, 1), touched: time.Now(), open: openBrowser}
	g.state.Edition = defaultEdition
	g.state.OS = runtime.GOOS
	g.refreshInstallation()
	g.execute = g.executeCommand
	g.inspect = g.probe
	server := &http.Server{Handler: g, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	url := g.origin + "/" + g.token + "/"
	session := filepath.Join(root, "gui-session.json")
	if err := writeJSON(session, map[string]string{"url": url}); err != nil {
		server.Close()
		return err
	}
	defer os.Remove(session)
	if err := g.open(url); err != nil {
		server.Close()
		return fmt.Errorf("não foi possível abrir o navegador: %w", err)
	}
	g.scheduleProbe()
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	defer server.Close()
	for {
		select {
		case err := <-done:
			if errors.Is(err, http.ErrServerClosed) {
				return nil
			}
			return err
		case <-g.close:
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			server.Shutdown(ctx)
			return nil
		case <-ticker.C:
			g.mu.Lock()
			idle := !g.state.Busy && time.Since(g.touched) > 5*time.Minute
			g.mu.Unlock()
			if idle {
				return nil
			}
		}
	}
}

func reopenGUI(root string) error {
	client := http.Client{Timeout: time.Second}
	for attempt := 0; attempt < 10; attempt++ {
		var session struct {
			URL string `json:"url"`
		}
		if readJSON(filepath.Join(root, "gui-session.json"), &session) == nil && regexp.MustCompile(`^http://127\.0\.0\.1:[0-9]+/[a-f0-9]{64}/$`).MatchString(session.URL) {
			response, err := client.Get(session.URL + "status")
			if err == nil {
				response.Body.Close()
				if response.StatusCode == http.StatusOK {
					return openBrowser(session.URL)
				}
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errors.New("o gerenciador do Smaug está iniciando; aguarde e abra o atalho novamente")
}

func (g *guiServer) refreshInstallation() {
	var state State
	var config Config
	if readJSON(filepath.Join(g.root, "current.json"), &state) == nil && readJSON(filepath.Join(g.root, "config.json"), &config) == nil && releasePattern.MatchString(state.Version) && validateConfig(config) == nil {
		g.state.Installed = true
		g.state.Edition = state.Edition
		g.state.Version = state.Version
		var prepared State
		g.state.Prepared = readJSON(filepath.Join(g.root, "prepared.json"), &prepared) == nil && prepared.Version == state.Version && !exists(filepath.Join(g.root, "preparing.json")) && !exists(filepath.Join(g.root, "pending.json"))
		port, path := config.WebPort, ""
		if state.Edition == "backend" {
			port, path = config.APIPort, "/docs"
		}
		g.state.URL = fmt.Sprintf("http://localhost:%d%s", port, path)
	}
}

func (g *guiServer) probe() {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	dockerErr := dockerReady(ctx)
	running := false
	g.mu.Lock()
	g.refreshInstallation()
	prepared := g.state.Prepared
	g.mu.Unlock()
	if prepared && dockerErr == nil {
		var config Config
		if readJSON(filepath.Join(g.root, "config.json"), &config) == nil {
			client := http.Client{Timeout: 2 * time.Second}
			response, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/ready", config.APIPort))
			if err == nil {
				var ready struct {
					Ready bool   `json:"ready"`
					Data  string `json:"data"`
				}
				running = response.StatusCode == http.StatusOK && json.NewDecoder(response.Body).Decode(&ready) == nil && ready.Ready && ready.Data == "prepared"
				response.Body.Close()
			}
		}
	}
	g.mu.Lock()
	g.state.Docker = dockerErr == nil
	g.state.Running = running
	if dockerErr != nil {
		g.state.DockerMessage = "Instale ou abra o Docker Desktop e aguarde sua inicialização. Depois, clique em Verificar novamente."
	} else {
		g.state.DockerMessage = "Docker pronto para usar."
	}
	g.mu.Unlock()
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(releaseAPI)
	if err == nil {
		defer response.Body.Close()
		var latest struct {
			Version string `json:"tag_name"`
		}
		if response.StatusCode == 200 && json.NewDecoder(response.Body).Decode(&latest) == nil && releasePattern.MatchString(latest.Version) {
			g.mu.Lock()
			g.state.Latest = latest.Version
			g.mu.Unlock()
		}
	}
}

func (g *guiServer) scheduleProbe() {
	if g.inspect != nil {
		go g.inspect()
	}
}

func dockerReady(ctx context.Context) error {
	for _, args := range [][]string{{"version", "--format", "{{.Server.Version}}"}, {"compose", "version", "--short"}} {
		cmd := exec.CommandContext(ctx, "docker", args...)
		hideWindow(cmd)
		if err := cmd.Run(); err != nil {
			return err
		}
	}
	return nil
}

func (g *guiServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Host != strings.TrimPrefix(g.origin, "http://") {
		http.Error(w, "invalid host", http.StatusForbidden)
		return
	}
	prefix := "/" + g.token + "/"
	if !strings.HasPrefix(r.URL.Path, prefix) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
	path := strings.TrimPrefix(r.URL.Path, prefix)
	if r.Method == http.MethodGet {
		switch path {
		case "", "index.html", "style.css", "app.js", "icon.svg":
			if path == "" {
				path = "index.html"
			}
			content, err := guiFiles.ReadFile("ui/" + path)
			if err != nil {
				http.NotFound(w, r)
				return
			}
			kind := map[string]string{"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "icon.svg": "image/svg+xml"}
			w.Header().Set("Content-Type", kind[path])
			w.Write(content)
		case "status":
			g.mu.Lock()
			defer g.mu.Unlock()
			g.touched = time.Now()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(g.state)
		default:
			http.NotFound(w, r)
		}
		return
	}
	if r.Method != http.MethodPost || path != "action" {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("Origin") != g.origin || r.Header.Get("Content-Type") != "application/json" {
		http.Error(w, "invalid origin", http.StatusForbidden)
		return
	}
	var request struct {
		Action  string `json:"action"`
		Edition string `json:"edition"`
		Client  string `json:"client"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil {
		http.Error(w, "invalid request", 400)
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.touched = time.Now()
	if g.state.Busy {
		http.Error(w, "Aguarde a operação em andamento.", http.StatusConflict)
		return
	}
	args, err := g.actionArguments(request.Action, request.Edition, request.Client)
	if err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	switch request.Action {
	case "close":
		g.close <- struct{}{}
		w.WriteHeader(http.StatusAccepted)
		return
	case "open":
		if err := g.open(g.state.URL); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		w.WriteHeader(202)
		return
	case "docker":
		if err := launchDockerDesktop(); err != nil {
			http.Error(w, "Não foi possível abrir o Docker Desktop. Abra-o pelo menu do sistema ou instale-o pelo link abaixo.", 500)
			return
		}
		g.scheduleProbe()
		w.WriteHeader(202)
		return
	case "probe":
		g.scheduleProbe()
		w.WriteHeader(202)
		return
	}
	g.state.Busy = true
	g.state.Action = request.Action
	g.state.Error = ""
	g.state.Log = ""
	g.state.Message = guiActionMessage(request.Action)
	go g.perform(args, request.Action)
	w.WriteHeader(http.StatusAccepted)
}

func (g *guiServer) actionArguments(action, edition, client string) ([]string, error) {
	switch action {
	case "install":
		if !validEdition(edition) {
			return nil, errors.New("Escolha Completo ou Backend.")
		}
		return []string{"installer", "--edition", edition}, nil
	case "upgrade", "start", "stop":
		if !g.state.Installed {
			return nil, errors.New("Instale o Smaug primeiro.")
		}
		return []string{action}, nil
	case "mcp":
		if !g.state.Prepared {
			return nil, errors.New("Conclua a instalação primeiro.")
		}
		if client != "codex" && client != "claude" {
			return nil, errors.New("Cliente MCP inválido.")
		}
		if runtime.GOOS == "linux" && client == "claude" {
			return nil, errors.New("O registro do Claude Desktop requer Windows ou macOS.")
		}
		return []string{"mcp", "register", "--client", client}, nil
	case "open":
		if !g.state.Running || g.state.URL == "" {
			return nil, errors.New("Inicie o Smaug antes de abrir a aplicação.")
		}
		return nil, nil
	case "probe", "docker", "close":
		return nil, nil
	default:
		return nil, errors.New("Ação inválida.")
	}
}

func guiActionMessage(action string) string {
	switch action {
	case "install":
		return "Instalando o Smaug e preparando os dados da CVM e da B3…"
	case "upgrade":
		return "Atualizando o Smaug e preparando os dados…"
	case "start":
		return "Iniciando o Smaug…"
	case "stop":
		return "Parando os serviços…"
	case "mcp":
		return "Conectando o MCP…"
	}
	return "Aguarde…"
}

func (g *guiServer) perform(args []string, action string) {
	err := g.execute(args, guiLogWriter{g})
	g.mu.Lock()
	g.state.Busy = false
	g.refreshInstallation()
	if err != nil {
		g.state.Error = "Não foi possível concluir. Confira os detalhes abaixo. Você pode tentar novamente; os dados preparados são preservados."
		g.state.Log += "\n" + err.Error()
		g.state.Message = "A operação precisa de atenção."
	} else {
		switch action {
		case "install", "upgrade", "start":
			g.state.Message = "Smaug pronto para usar."
			g.state.Running = g.state.Prepared
		case "stop":
			g.state.Message = "Serviços parados."
			g.state.Running = false
		case "mcp":
			g.state.Message = "MCP conectado. Reinicie o cliente para utilizá-lo."
		}
	}
	g.mu.Unlock()
	g.scheduleProbe()
}

type guiLogWriter struct{ g *guiServer }

func (w guiLogWriter) Write(content []byte) (int, error) {
	w.g.mu.Lock()
	defer w.g.mu.Unlock()
	w.g.state.Log += string(content)
	if len(w.g.state.Log) > guiLogLimit {
		w.g.state.Log = w.g.state.Log[len(w.g.state.Log)-guiLogLimit:]
	}
	return len(content), nil
}

func (g *guiServer) executeCommand(args []string, output io.Writer) error {
	if err := os.MkdirAll(g.root, 0700); err != nil {
		return err
	}
	file, err := os.OpenFile(filepath.Join(g.root, "installer-ui.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return err
	}
	defer file.Close()
	fmt.Fprintln(file, "\n", time.Now().UTC().Format(time.RFC3339), strings.Join(args, " "))
	executable := g.executable
	var state State
	// A freshly downloaded installer must use its new installation logic, even
	// when the installed manager predates graphical setup and the installer alias.
	if args[0] != "installer" && readJSON(filepath.Join(g.root, "current.json"), &state) == nil && releasePattern.MatchString(state.Version) {
		candidate := filepath.Join(g.root, "releases", state.Version, binaryName())
		if exists(candidate) {
			executable = candidate
		}
	}
	cmd := backgroundCommand(executable, append([]string{"--dir", g.root}, args...)...)
	cmd.Env = append(os.Environ(), "SMAUG_GUI_CHILD=1")
	// Docker Desktop exposes its CLI outside the shell PATH on some macOS installs.
	cmd.Env = desktopEnvironment(cmd.Env)
	writer := io.MultiWriter(file, output)
	cmd.Stdout = writer
	cmd.Stderr = writer
	return cmd.Run()
}

func desktopEnvironment(env []string) []string {
	var extra []string
	switch runtime.GOOS {
	case "darwin":
		extra = []string{"/usr/local/bin", "/opt/homebrew/bin", "/Applications/Docker.app/Contents/Resources/bin"}
	case "windows":
		for _, base := range []string{os.Getenv("ProgramFiles"), os.Getenv("LOCALAPPDATA")} {
			if base == "" {
				continue
			}
			if base == os.Getenv("ProgramFiles") {
				extra = append(extra, filepath.Join(base, "Docker", "Docker", "resources", "bin"))
			} else {
				extra = append(extra, filepath.Join(base, "Programs", "DockerDesktop", "resources", "bin"))
			}
		}
	default:
		extra = []string{"/usr/local/bin", "/usr/bin", "/bin"}
		if home, err := os.UserHomeDir(); err == nil {
			extra = append(extra, filepath.Join(home, ".docker", "bin"))
		}
	}
	separator := string(os.PathListSeparator)
	for i, value := range env {
		if len(value) >= 5 && strings.EqualFold(value[:5], "PATH=") {
			value = "PATH=" + value[5:]
			for _, path := range extra {
				if !strings.Contains(separator+strings.TrimPrefix(value, "PATH=")+separator, separator+path+separator) {
					value += separator + path
				}
			}
			env[i] = value
			return env
		}
	}
	return append(env, "PATH="+strings.Join(extra, separator))
}
