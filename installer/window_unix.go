//go:build !windows

package main

import (
	"os/exec"
	"runtime"
	"strconv"
)

func hideWindow(cmd *exec.Cmd) {}

func showGUIError(message string) {
	if runtime.GOOS == "darwin" {
		backgroundCommand("/usr/bin/osascript", "-e", "display alert \"Smaug\" message "+strconv.Quote(message)).Run()
		return
	}
	if err := backgroundCommand("zenity", "--error", "--title=Smaug", "--text="+message).Run(); err != nil {
		backgroundCommand("notify-send", "Smaug", message).Run()
	}
}
