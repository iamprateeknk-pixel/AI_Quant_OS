"""Owned trusted service processes; lifetime control, never an execution sandbox."""

import os
import signal
import subprocess
from pathlib import Path


class _WindowsJob:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            (
                "SetInformationJobObject",
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen) -> None:
        import ctypes

        # Popen retains this exact process HANDLE; opening a numeric PID could race reuse.
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("thread_id", wintypes.DWORD),
                ("process_id", wintypes.DWORD),
                ("base_priority", wintypes.LONG),
                ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        for name, arguments, result in (
            ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            ("Thread32First", [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            ("Thread32Next", [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL),
            ("OpenThread", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("GetProcessIdOfThread", [wintypes.HANDLE], wintypes.DWORD),
            ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            available = self.api.Thread32First(snapshot, ctypes.byref(entry))
            matches = []
            while available:
                if entry.process_id == process.pid:
                    matches.append(entry.thread_id)
                entry.size = ctypes.sizeof(entry)
                available = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            if len(matches) != 1 or process.poll() is not None:
                raise RuntimeError("Owned suspended initial thread identity mismatch")
            thread = self.api.OpenThread(0x0802, False, matches[0])
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if self.api.GetProcessIdOfThread(thread) != process.pid:
                    raise RuntimeError("Owned thread process identity mismatch")
                if self.api.ResumeThread(thread) != 1:
                    raise RuntimeError("Owned initial thread suspend count mismatch")
            finally:
                if not self.api.CloseHandle(thread):
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if not self.api.CloseHandle(snapshot):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                import ctypes

                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


class OwnedProcess:
    def __init__(self, command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
        self._job = _WindowsJob() if os.name == "nt" else None
        self._closed = False
        try:
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                start_new_session=os.name != "nt",
                # CREATE_SUSPENDED closes the spawn-before-job-assignment fork race.
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0,
            )
            if self._job:
                self._job.assign(self.process)
                self._job.resume(self.process)
        except BaseException:
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait(timeout=5)
                self.process.stdin.close()
            if self._job:
                self._job.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if self._job:
            self._job.close()
        else:
            # Only the new session/group this object created, never discovered services.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if self._job:
                self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=5)
        if not self._job:
            # A direct child can exit while a descendant ignores SIGTERM.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._closed = True
