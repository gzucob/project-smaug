package main

import (
	"strconv"
	"strings"
	"syscall"
)

func lockOperation(path string) (func(), error) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, err
	}
	return func() { syscall.CloseHandle(handle) }, nil
}

func processAlive(pid int) bool {
	output, err := backgroundCommand("tasklist.exe", "/FI", "PID eq "+strconv.Itoa(pid), "/FO", "CSV", "/NH").Output()
	// A failed probe must never allow a concurrent writer.
	return err != nil || strings.Contains(string(output), `","`+strconv.Itoa(pid)+`"`)
}
