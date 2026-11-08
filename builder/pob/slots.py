"""How many PoB engines may run, and on how many processors (POE2Q1, 6 Nov 2026).

Inside the user's shared PC queue (a run booked through tools/pc_queue.py, which sets PC_QUEUE_KIND) PoE2 has one test place, on the conditions the other
projects' managers set: at most 6 LuaJIT processes in all, on the processors the queue's job gives the run (2 and up), and about 8 GB (the queue's job
object enforces that, and the process count as a backstop).

The 6 is held here, in code, not by convention: every process that boots a PoB engine first takes one of 6 named slots (Windows mutexes in this session,
"Local\\PoE2Builder.queue.LuaJIT.<n>"; a queue proof's own set, see slot_name). A process keeps its slot for its life, however many engines it boots (they share its one LuaJIT process), and
Windows frees it when the process ends, even killed. A seventh process gets PoolFull instead of an engine. The pool sizes itself to the slots still free,
so a script that boots an engine of its own before its pool still fits in 6.

Outside the queue (the user's own runs from the web page) nothing here limits anything: the pool follows the user's setting (builder/settings.py), from
the processors this process may use.
"""

from __future__ import annotations

import ctypes
import hashlib
import os

POOL_MAX = 6


def slot_name() -> str:
    """The slots' mutex names. A queue proof or test (PC_QUEUE_DIR set: the queue's state folder isn't the real one) gets
    slots of its own, named from that folder, so it never shares the 6 with live PoE2 runs (7 Nov 2026: the proof's
    p2pool scene found them all held by a gate run). Every process of a run inherits the variable, so they agree."""
    folder = os.environ.get("PC_QUEUE_DIR")
    if not folder:
        return "Local\\PoE2Builder.queue.LuaJIT.%d"
    tag = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:12]
    return "Local\\PoE2Builder.proof." + tag + ".LuaJIT.%d"


SLOT_NAME = slot_name()
WAIT_OBJECT_0, WAIT_ABANDONED = 0x0, 0x80

_held: int | None = None  # this process's slot handle, once taken
_held_index: int | None = None  # and which slot it is (a thread may take a mutex it owns again, so free() must skip it)


class PoolFull(RuntimeError):
    pass


def in_queue() -> bool:
    """Is this process part of a run the PC queue started? (The queue sets PC_QUEUE_KIND for the command it starts, and children inherit it.)"""
    return bool(os.environ.get("PC_QUEUE_KIND"))


def available_processors() -> int:
    """The logical processors this process may run on: its affinity mask (inside the queue's job, processors 2 and up), not the machine's count."""
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
            process, system = ctypes.c_size_t(), ctypes.c_size_t()
            if kernel32.GetProcessAffinityMask(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(system)):
                return max(1, bin(process.value).count("1"))
        except (OSError, AttributeError):
            pass
    elif hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return os.cpu_count() or 1


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def _try(kernel32, index: int):
    """The slot's handle, owned now, or None if another process has it."""
    handle = kernel32.CreateMutexW(None, False, SLOT_NAME % index)
    if not handle:
        return None
    if kernel32.WaitForSingleObject(handle, 0) in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return handle
    kernel32.CloseHandle(handle)
    return None


def take() -> None:
    """Before booting a PoB engine in this process: inside the queue, own one of the POOL_MAX slots for the life of the process (once per process),
    or raise PoolFull. Call it on the thread that lives as long as the engine (the process's main thread): Windows ties a mutex to its thread."""
    global _held, _held_index
    if _held is not None or not in_queue() or os.name != "nt":
        return
    kernel32 = _kernel32()
    for index in range(POOL_MAX):
        handle = _try(kernel32, index)
        if handle:
            _held, _held_index = handle, index
            return
    raise PoolFull(f"{POOL_MAX} processes of this run already have a PoB engine: PoE2's place in the PC queue allows {POOL_MAX} LuaJIT "
                   "processes at most (builder/pob/slots.py)")


def free() -> int | None:
    """Inside the queue, how many slots no process holds now (this process's own counts as taken); None outside it."""
    if not in_queue() or os.name != "nt":
        return None
    kernel32 = _kernel32()
    count = 0
    for index in range(POOL_MAX):
        if index == _held_index:
            continue
        handle = _try(kernel32, index)
        if handle:
            count += 1
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
    return count
