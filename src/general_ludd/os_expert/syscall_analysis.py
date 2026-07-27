"""System call analysis.

Cross-platform system call tables, categorisation, and tracing tool
definitions. Covers Linux x86_64/aarch64, macOS x86_64/arm64, Windows,
Android, and iOS syscall interfaces.
"""

from __future__ import annotations

from typing import TypedDict


class SyscallEntry(TypedDict):
    platform: str
    name: str
    number: int
    category: str
    description: str


class SyscallTraceTool(TypedDict):
    platform: str
    tool_name: str
    trace_command: str
    parse_command: str
    output_format: str


PLATFORM_SYSCALLS: dict[str, list[SyscallEntry]] = {
    "linux_x86_64": [
        {
            "platform": "linux_x86_64",
            "name": "read",
            "number": 0,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "linux_x86_64",
            "name": "write",
            "number": 1,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {"platform": "linux_x86_64", "name": "open", "number": 2, "category": "fs", "description": "Open a file"},
        {
            "platform": "linux_x86_64",
            "name": "close",
            "number": 3,
            "category": "io",
            "description": "Close a file descriptor",
        },
        {"platform": "linux_x86_64", "name": "stat", "number": 4, "category": "fs", "description": "Get file status"},
        {
            "platform": "linux_x86_64",
            "name": "mmap",
            "number": 9,
            "category": "memory",
            "description": "Map memory pages",
        },
        {
            "platform": "linux_x86_64",
            "name": "fork",
            "number": 57,
            "category": "process",
            "description": "Create a child process",
        },
        {
            "platform": "linux_x86_64",
            "name": "execve",
            "number": 59,
            "category": "process",
            "description": "Execute a program",
        },
        {
            "platform": "linux_x86_64",
            "name": "exit",
            "number": 60,
            "category": "process",
            "description": "Exit the calling process",
        },
        {
            "platform": "linux_x86_64",
            "name": "socket",
            "number": 41,
            "category": "network",
            "description": "Create a socket",
        },
        {
            "platform": "linux_x86_64",
            "name": "connect",
            "number": 42,
            "category": "network",
            "description": "Initiate a connection on a socket",
        },
        {
            "platform": "linux_x86_64",
            "name": "bind",
            "number": 49,
            "category": "network",
            "description": "Bind a name to a socket",
        },
        {
            "platform": "linux_x86_64",
            "name": "listen",
            "number": 50,
            "category": "network",
            "description": "Listen for connections on a socket",
        },
        {
            "platform": "linux_x86_64",
            "name": "accept",
            "number": 43,
            "category": "network",
            "description": "Accept a connection on a socket",
        },
        {
            "platform": "linux_x86_64",
            "name": "clone",
            "number": 56,
            "category": "process",
            "description": "Create a child thread/process",
        },
        {
            "platform": "linux_x86_64",
            "name": "ptrace",
            "number": 101,
            "category": "debug",
            "description": "Process trace",
        },
        {
            "platform": "linux_x86_64",
            "name": "kill",
            "number": 62,
            "category": "signal",
            "description": "Send a signal to a process",
        },
    ],
    "linux_aarch64": [
        {
            "platform": "linux_aarch64",
            "name": "read",
            "number": 63,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "linux_aarch64",
            "name": "write",
            "number": 64,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {
            "platform": "linux_aarch64",
            "name": "openat",
            "number": 56,
            "category": "fs",
            "description": "Open a file relative to dirfd",
        },
        {
            "platform": "linux_aarch64",
            "name": "close",
            "number": 57,
            "category": "io",
            "description": "Close a file descriptor",
        },
        {
            "platform": "linux_aarch64",
            "name": "mmap",
            "number": 222,
            "category": "memory",
            "description": "Map memory pages",
        },
        {
            "platform": "linux_aarch64",
            "name": "clone",
            "number": 220,
            "category": "process",
            "description": "Create a child thread/process",
        },
        {
            "platform": "linux_aarch64",
            "name": "execve",
            "number": 221,
            "category": "process",
            "description": "Execute a program",
        },
    ],
    "macos_x86_64": [
        {
            "platform": "macos_x86_64",
            "name": "read",
            "number": 0x2000003,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "macos_x86_64",
            "name": "write",
            "number": 0x2000004,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {
            "platform": "macos_x86_64",
            "name": "open",
            "number": 0x2000005,
            "category": "fs",
            "description": "Open a file",
        },
        {
            "platform": "macos_x86_64",
            "name": "close",
            "number": 0x2000006,
            "category": "io",
            "description": "Close a file descriptor",
        },
        {
            "platform": "macos_x86_64",
            "name": "mmap",
            "number": 0x20000C5,
            "category": "memory",
            "description": "Map memory pages",
        },
        {
            "platform": "macos_x86_64",
            "name": "fork",
            "number": 0x2000002,
            "category": "process",
            "description": "Create a child process",
        },
        {
            "platform": "macos_x86_64",
            "name": "execve",
            "number": 0x200003B,
            "category": "process",
            "description": "Execute a program",
        },
        {
            "platform": "macos_x86_64",
            "name": "exit",
            "number": 0x2000001,
            "category": "process",
            "description": "Exit the calling process",
        },
    ],
    "macos_arm64": [
        {
            "platform": "macos_arm64",
            "name": "read",
            "number": 3,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "macos_arm64",
            "name": "write",
            "number": 4,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {"platform": "macos_arm64", "name": "open", "number": 5, "category": "fs", "description": "Open a file"},
        {
            "platform": "macos_arm64",
            "name": "close",
            "number": 6,
            "category": "io",
            "description": "Close a file descriptor",
        },
        {
            "platform": "macos_arm64",
            "name": "mmap",
            "number": 197,
            "category": "memory",
            "description": "Map memory pages",
        },
        {
            "platform": "macos_arm64",
            "name": "fork",
            "number": 2,
            "category": "process",
            "description": "Create a child process",
        },
        {
            "platform": "macos_arm64",
            "name": "execve",
            "number": 59,
            "category": "process",
            "description": "Execute a program",
        },
    ],
    "windows": [
        {
            "platform": "windows",
            "name": "NtCreateFile",
            "number": 0x0055,
            "category": "fs",
            "description": "Create or open a file",
        },
        {
            "platform": "windows",
            "name": "NtReadFile",
            "number": 0x006F,
            "category": "io",
            "description": "Read from a file",
        },
        {
            "platform": "windows",
            "name": "NtWriteFile",
            "number": 0x0070,
            "category": "io",
            "description": "Write to a file",
        },
        {"platform": "windows", "name": "NtClose", "number": 0x000F, "category": "io", "description": "Close a handle"},
        {
            "platform": "windows",
            "name": "NtAllocateVirtualMemory",
            "number": 0x0018,
            "category": "memory",
            "description": "Allocate virtual memory",
        },
        {
            "platform": "windows",
            "name": "NtCreateProcess",
            "number": 0x00C8,
            "category": "process",
            "description": "Create a new process",
        },
        {
            "platform": "windows",
            "name": "NtCreateThread",
            "number": 0x00C9,
            "category": "process",
            "description": "Create a new thread",
        },
    ],
    "android": [
        {
            "platform": "android",
            "name": "read",
            "number": 63,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "android",
            "name": "write",
            "number": 64,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {
            "platform": "android",
            "name": "openat",
            "number": 56,
            "category": "fs",
            "description": "Open a file relative to dirfd",
        },
        {
            "platform": "android",
            "name": "close",
            "number": 57,
            "category": "io",
            "description": "Close a file descriptor",
        },
        {
            "platform": "android",
            "name": "clone",
            "number": 220,
            "category": "process",
            "description": "Create a child thread/process",
        },
    ],
    "ios": [
        {
            "platform": "ios",
            "name": "read",
            "number": 3,
            "category": "io",
            "description": "Read from a file descriptor",
        },
        {
            "platform": "ios",
            "name": "write",
            "number": 4,
            "category": "io",
            "description": "Write to a file descriptor",
        },
        {"platform": "ios", "name": "open", "number": 5, "category": "fs", "description": "Open a file"},
        {"platform": "ios", "name": "close", "number": 6, "category": "io", "description": "Close a file descriptor"},
        {"platform": "ios", "name": "mmap", "number": 197, "category": "memory", "description": "Map memory pages"},
        {
            "platform": "ios",
            "name": "fork",
            "number": 2,
            "category": "process",
            "description": "Create a child process",
        },
    ],
}


SYSCALL_TRACE_TOOLS: list[SyscallTraceTool] = [
    {
        "platform": "linux",
        "tool_name": "strace",
        "trace_command": "strace -f -e trace=all -o trace.log COMMAND",
        "parse_command": "strace -c COMMAND",
        "output_format": "text",
    },
    {
        "platform": "linux",
        "tool_name": "perf",
        "trace_command": "perf trace -o perf.log COMMAND",
        "parse_command": "perf script",
        "output_format": "text",
    },
    {
        "platform": "linux",
        "tool_name": "bpftrace",
        "trace_command": "bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm, args->id] = count(); }'",
        "parse_command": "bpftrace -e '...' 2>&1",
        "output_format": "text",
    },
    {
        "platform": "macos",
        "tool_name": "dtruss",
        "trace_command": "sudo dtruss -f COMMAND",
        "parse_command": "sudo dtruss -c COMMAND",
        "output_format": "text",
    },
    {
        "platform": "macos",
        "tool_name": "dtrace",
        "trace_command": "sudo dtrace -n 'syscall:::entry { @[execname, probefunc] = count(); }'",
        "parse_command": "sudo dtrace -n '...' 2>&1",
        "output_format": "text",
    },
    {
        "platform": "windows",
        "tool_name": "Process Monitor",
        "trace_command": "procmon.exe /BackingFile trace.pml /AcceptEula /Quiet",
        "parse_command": "procmon.exe /OpenLog trace.pml /SaveAs trace.csv",
        "output_format": "csv",
    },
    {
        "platform": "android",
        "tool_name": "strace",
        "trace_command": "adb shell strace -f COMMAND",
        "parse_command": "adb shell strace -c COMMAND",
        "output_format": "text",
    },
    {
        "platform": "ios",
        "tool_name": "oslog",
        "trace_command": "idevicesyslog --pattern syscall",
        "parse_command": "idevicediagnostics restart && idevicesyslog",
        "output_format": "text",
    },
]
