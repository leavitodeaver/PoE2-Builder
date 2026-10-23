"""Minimal ctypes host for Path of Building's own LuaJIT (runtime/lua51.dll).

PoB ships a patched LuaJIT (it accepts `+=` and similar operators, which PoB's source uses),
so the engine runs on that exact DLL rather than a stock LuaJIT. Values cross the boundary as
strings only; the Lua side exchanges JSON.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

LUA_GLOBALSINDEX = -10002
LUA_TBOOLEAN = 1

_void_p = ctypes.c_void_p
_int = ctypes.c_int
_size_t = ctypes.c_size_t
_char_p = ctypes.c_char_p

_SIGNATURES = {
    "luaL_newstate": ([], _void_p),
    "luaL_openlibs": ([_void_p], None),
    "luaL_loadbuffer": ([_void_p, _char_p, _size_t, _char_p], _int),
    "lua_pcall": ([_void_p, _int, _int, _int], _int),
    "lua_gettop": ([_void_p], _int),
    "lua_settop": ([_void_p, _int], None),
    "lua_getfield": ([_void_p, _int, _char_p], None),
    "lua_pushlstring": ([_void_p, _char_p, _size_t], None),
    "lua_tolstring": ([_void_p, _int, ctypes.POINTER(_size_t)], _void_p),
    "lua_toboolean": ([_void_p, _int], _int),
    "lua_type": ([_void_p, _int], _int),
    "lua_close": ([_void_p], None),
}


class LuaError(RuntimeError):
    pass


class LuaState:
    def __init__(self, runtime_dir: Path) -> None:
        runtime_dir = runtime_dir.resolve()
        # PoB's native Lua modules (lua-utf8.dll etc.) resolve their dependencies from here
        self._dll_dir = os.add_dll_directory(str(runtime_dir))
        lua = ctypes.CDLL(str(runtime_dir / "lua51.dll"))
        for name, (argtypes, restype) in _SIGNATURES.items():
            fn = getattr(lua, name)
            fn.argtypes = argtypes
            fn.restype = restype
        self._lua = lua
        self.L = lua.luaL_newstate()
        if not self.L:
            raise LuaError("luaL_newstate failed")
        lua.luaL_openlibs(self.L)
        # Error handler used by every protected call; looked up by name so no stack shuffling
        # (lua_insert) is needed
        self._load(b"__traceback = debug.traceback", b"=init")
        self._raw_call(0)

    def run(self, code: str, chunk_name: str, *args: str | bytes) -> list[bytes]:
        """Load and run a chunk; `args` arrive as `...`. Returns the chunk's results as bytes."""
        base = self._push_handler()
        self._load(code.encode("utf-8"), ("@" + chunk_name).encode("utf-8"), base)
        return self._call_with_handler(base, args)

    def call(self, function: str, *args: str | bytes) -> list[bytes]:
        """Call a global function with string arguments. Returns its results as bytes."""
        base = self._push_handler()
        self._lua.lua_getfield(self.L, LUA_GLOBALSINDEX, function.encode("ascii"))
        return self._call_with_handler(base, args)

    def close(self) -> None:
        if self.L:
            self._lua.lua_close(self.L)
            self.L = None

    def _push_handler(self) -> int:
        base = self._lua.lua_gettop(self.L)
        self._lua.lua_getfield(self.L, LUA_GLOBALSINDEX, b"__traceback")
        return base

    def _load(self, data: bytes, name: bytes, base: int | None = None) -> None:
        if self._lua.luaL_loadbuffer(self.L, data, len(data), name) != 0:
            message = self._string_at(-1)
            self._lua.lua_settop(self.L, self._lua.lua_gettop(self.L) - 1 if base is None else base)
            raise LuaError(message.decode("utf-8", "replace"))

    def _raw_call(self, nargs: int) -> None:
        if self._lua.lua_pcall(self.L, nargs, 0, 0) != 0:
            message = self._string_at(-1)
            self._lua.lua_settop(self.L, -2)
            raise LuaError(message.decode("utf-8", "replace"))

    def _call_with_handler(self, base: int, args: tuple[str | bytes, ...]) -> list[bytes]:
        lua, L = self._lua, self.L
        for arg in args:
            data = arg.encode("utf-8") if isinstance(arg, str) else arg
            lua.lua_pushlstring(L, data, len(data))
        # Stack: [base] handler, function, args... ; LUA_MULTRET = -1
        if lua.lua_pcall(L, len(args), -1, base + 1) != 0:
            message = self._string_at(-1)
            lua.lua_settop(L, base)
            raise LuaError(message.decode("utf-8", "replace"))
        results = []
        for index in range(base + 2, lua.lua_gettop(L) + 1):
            if lua.lua_type(L, index) == LUA_TBOOLEAN:
                results.append(b"true" if lua.lua_toboolean(L, index) else b"false")
            else:
                results.append(self._string_at(index))
        lua.lua_settop(L, base)
        return results

    def _string_at(self, index: int) -> bytes:
        size = _size_t()
        ptr = self._lua.lua_tolstring(self.L, index, ctypes.byref(size))
        return ctypes.string_at(ptr, size.value) if ptr else b""
