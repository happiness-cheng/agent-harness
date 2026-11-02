"""Windows 自有进程容器：挂起创建后纳入 Job，退出与输出管道无关。"""
import ctypes
from ctypes import wintypes
import time


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _BasicLimits),
        ('IoInfo', ctypes.c_ulonglong * 6),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ('TotalUserTime', ctypes.c_longlong),
        ('TotalKernelTime', ctypes.c_longlong),
        ('ThisPeriodTotalUserTime', ctypes.c_longlong),
        ('ThisPeriodTotalKernelTime', ctypes.c_longlong),
        ('TotalPageFaultCount', wintypes.DWORD),
        ('TotalProcesses', wintypes.DWORD),
        ('ActiveProcesses', wintypes.DWORD),
        ('TotalTerminatedProcesses', wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
        ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
        ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
    ]


def _kernel_api():
    # 明确指针宽度，避免 64 位 HANDLE 被默认 ctypes int 截断。
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'OpenThread': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'IsProcessInJob': ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        'ResumeThread': ([wintypes.HANDLE], wintypes.DWORD),
        'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        'Thread32First': ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        'Thread32Next': ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = arguments
        function.restype = result
    return api


def _checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class WindowsJob:
    """仅持有本次 Job 的内核句柄；不按进程名或过期 PID 杀进程。"""

    def __init__(self):
        self.api = _kernel_api()
        self.handle = _checked(self.api.CreateJobObjectW(None, None))
        self.drained = False
        try:
            limits = _ExtendedLimits()
            # 不设置 BREAKAWAY_OK，后代默认留在同一个生命周期容器。
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            _checked(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    def assign(self, pid):
        # 此时进程仍为 CREATE_SUSPENDED，尚不能生成未经登记的后代。
        process = _checked(self.api.OpenProcess(0x0101, False, pid))  # SET_QUOTA | TERMINATE
        try:
            _checked(self.api.AssignProcessToJobObject(self.handle, process))
        finally:
            self.api.CloseHandle(process)

    def resume(self, pid):
        # Popen 关闭了创建时的线程句柄；枚举仍挂起的自有进程主线程。
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)  # SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = []
            more = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == pid:
                    found.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                more = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
            if len(found) != 1:
                raise RuntimeError('无法唯一确认挂起进程的主线程，不开始执行')
            thread = _checked(self.api.OpenThread(0x0002, False, found[0]))
            try:
                previous = self.api.ResumeThread(thread)
                if previous == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if previous != 1:
                    raise RuntimeError('主线程挂起计数异常，不继续执行')
            finally:
                self.api.CloseHandle(thread)
        finally:
            self.api.CloseHandle(snapshot)

    def stop(self, timeout=10):
        if self.drained:
            return
        if self.handle is None:
            raise RuntimeError('Job 已关闭但未确认所有进程退出，结果未知')
        deadline = time.monotonic()+timeout
        handles = []
        try:
            self._prevent_new_processes()
            handles = self._process_handles(deadline)
            _checked(self.api.TerminateJobObject(self.handle, 1))
            # TerminateProcess/Job 只发起异步终止；活动计数不能代替句柄终态。
            for handle in handles:
                milliseconds = max(0, int((deadline-time.monotonic())*1000))
                status = self.api.WaitForSingleObject(handle, milliseconds)
                if status == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if status != 0:
                    raise RuntimeError('本次后代进程尚未完成退出，结果未知')
            while True:
                info = _Accounting()
                _checked(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
                if info.ActiveProcesses == 0:
                    self.drained = True
                    self.close()
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError('无法确认本次 Job 全部进程退出，结果未知')
                time.sleep(.01)
        except BaseException:
            # 查询/等待失败也要发起关闭，不能依赖调用方何时回收 Popen。
            # 未完成退出确认，drained 仍为 False，上层必须保存 UNKNOWN。
            self.close()
            raise
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)

    def _prevent_new_processes(self):
        # 仅停止阶段关门：已有进程不受缩限终止，但任一存活成员再建后代会越限。
        # 当成员数已为零时也没有继续创建后代的执行者。
        limits = _ExtendedLimits()
        _checked(self.api.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits), None))
        limits.BasicLimitInformation.LimitFlags |= 0x00000008  # ACTIVE_PROCESS
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        _checked(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def _process_handles(self, deadline):
        """只保留属于本 Job 的同步句柄，绝不向枚举得到的 PID 发终止。"""
        capacity = 64
        while True:
            class ProcessIds(ctypes.Structure):
                _fields_ = [('assigned', wintypes.DWORD), ('listed', wintypes.DWORD),
                            ('ids', ctypes.c_size_t * capacity)]
            info = ProcessIds()
            if self.api.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                break
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise RuntimeError('无法在期限内读取本次 Job 进程，结果未知')
            capacity = max(capacity*2, info.assigned)
        handles = []
        try:
            for pid in info.ids[:info.listed]:
                handle = self.api.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:  # 枚举后已退出，且 PID 已不存在。
                        continue
                    raise ctypes.WinError(error)
                member = wintypes.BOOL()
                try:
                    _checked(self.api.IsProcessInJob(handle, self.handle, ctypes.byref(member)))
                    if member.value:
                        handles.append(handle)
                        handle = None
                finally:
                    if handle is not None:
                        self.api.CloseHandle(handle)
            return handles
        except BaseException:
            for handle in handles:
                self.api.CloseHandle(handle)
            raise

    def close(self):
        if self.handle is not None:
            _checked(self.api.CloseHandle(self.handle))
            self.handle = None

    def __del__(self):
        # 正常路径显式 stop；异常销毁仍以 KILL_ON_JOB_CLOSE 防止孤儿进程。
        if getattr(self, 'handle', None) is not None:
            self.api.CloseHandle(self.handle)

