package main

import (
	"os"
	"os/exec"
	"syscall"
	"unsafe"
)

func hideWindow(cmd *exec.Cmd) {
	if os.Getenv("SMAUG_GUI_CHILD") == "1" || guiBuild == "true" {
		if cmd.SysProcAttr == nil {
			cmd.SysProcAttr = &syscall.SysProcAttr{}
		}
		cmd.SysProcAttr.HideWindow = true
	}
}

func showGUIError(message string) {
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("Smaug")
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
}
