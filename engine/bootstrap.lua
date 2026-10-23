-- Boots Path of Building (PoE2) headless on PoB's own LuaJIT (runtime/lua51.dll).
--
-- Mirrors PoB's own src/HeadlessWrapper.lua, with the changes embedding needs: an absolute
-- script path, a real clock, zlib through FFI against PoB's bundled zlib1.dll, captured console
-- output, and startup errors raised instead of waiting on stdin. No PoB file is modified.
--
-- Two PoB layouts are supported: current dev, where the SimpleGraphic stubs live in
-- _SimpleGraphic.def.lua, and v0.23.x and earlier, where HeadlessWrapper.lua defines them
-- itself. The older layout matters for comparing against the PoB app's released version.
--
-- The caller must set the process working directory to PoB's src/ folder first, because PoB
-- loads its modules and data with relative paths.
local pobSrc = ...

arg = { }
package.path = pobSrc .. "/../runtime/lua/?.lua;" .. pobSrc .. "/../runtime/lua/?/init.lua;" .. package.path
package.cpath = pobSrc .. "/../runtime/?.dll;" .. package.cpath

-- PoB's wrappers wait on stdin after a startup failure; an embedded engine must never block
io.read = function() return nil end

-- Console output goes to a bounded buffer the Python side can read when something fails
engineLog = { }

local function installOverrides()
	function ConPrintf(fmt, ...)
		local ok, line = pcall(string.format, fmt, ...)
		engineLog[#engineLog + 1] = ok and line or tostring(fmt)
		if #engineLog > 500 then
			table.remove(engineLog, 1)
		end
	end

	local clock = os.clock
	function GetTime()
		return math.floor(clock() * 1000)
	end
	function GetScriptPath()
		return pobSrc
	end
	function GetRuntimePath()
		return pobSrc .. "/../runtime"
	end
	function GetUserPath()
		return pobSrc
	end
	function GetVirtualScreenSize()
		return 1920, 1080
	end

	-- zlib, for build codes and timeless-jewel lookup tables
	local ffi = require("ffi")
	ffi.cdef[[
	typedef struct z_stream_s {
		const uint8_t *next_in; unsigned int avail_in; unsigned long total_in;
		uint8_t *next_out; unsigned int avail_out; unsigned long total_out;
		const char *msg; void *state;
		void *zalloc; void *zfree; void *opaque;
		int data_type; unsigned long adler; unsigned long reserved;
	} z_stream;
	const char *zlibVersion(void);
	int deflateInit2_(z_stream *strm, int level, int method, int windowBits, int memLevel, int strategy, const char *version, int stream_size);
	int deflate(z_stream *strm, int flush);
	int deflateEnd(z_stream *strm);
	int inflateInit2_(z_stream *strm, int windowBits, const char *version, int stream_size);
	int inflate(z_stream *strm, int flush);
	int inflateEnd(z_stream *strm);
	]]
	local zlib = ffi.load(pobSrc .. "/../runtime/zlib1.dll")
	local Z_FINISH, Z_STREAM_END, Z_OK, Z_BUF_ERROR = 4, 1, 0, -5
	local CHUNK = 65536

	local function pump(strm, step)
		local out = { }
		local buf = ffi.new("uint8_t[?]", CHUNK)
		repeat
			strm.next_out = buf
			strm.avail_out = CHUNK
			local ret = step(strm, Z_FINISH)
			if ret ~= Z_OK and ret ~= Z_STREAM_END and ret ~= Z_BUF_ERROR then
				return nil, strm.msg ~= nil and ffi.string(strm.msg) or ("zlib error " .. ret)
			end
			out[#out + 1] = ffi.string(buf, CHUNK - strm.avail_out)
			if ret == Z_BUF_ERROR and strm.avail_in == 0 then
				return nil, "truncated input"
			end
		until ret == Z_STREAM_END
		return table.concat(out)
	end

	function Deflate(data, isGzip)
		local strm = ffi.new("z_stream")
		-- windowBits 15 = zlib header, 31 = gzip header; level 9, memLevel 8, default strategy
		if zlib.deflateInit2_(strm, 9, 8, isGzip and 31 or 15, 8, 0, zlib.zlibVersion(), ffi.sizeof(strm)) ~= Z_OK then
			return nil, "deflateInit failed"
		end
		strm.next_in = ffi.cast("const uint8_t *", data)
		strm.avail_in = #data
		local result, err = pump(strm, zlib.deflate)
		zlib.deflateEnd(strm)
		return result, err
	end

	function Inflate(data)
		local strm = ffi.new("z_stream")
		-- windowBits 47 = 15 + 32: detect zlib or gzip header automatically
		if zlib.inflateInit2_(strm, 47, zlib.zlibVersion(), ffi.sizeof(strm)) ~= Z_OK then
			return nil, "inflateInit failed"
		end
		strm.next_in = ffi.cast("const uint8_t *", data)
		strm.avail_in = #data
		local result, err = pump(strm, zlib.inflate)
		zlib.inflateEnd(strm)
		return result, err
	end
end

local function bootCurrentLayout()
	dofile("_SimpleGraphic.def.lua")
	installOverrides()

	-- From here on this matches HeadlessWrapper.lua
	__callbackTable__ = { }

	function runCallback(name, ...)
		if __callbackTable__[name] then
			return __callbackTable__[name](...)
		elseif __mainObject__ and __mainObject__[name] then
			return __mainObject__[name](__mainObject__, ...)
		end
	end

	local l_require = require
	function require(name)
		-- PoB's network library isn't needed headless
		if name == "lcurl.safe" then
			return
		end
		return l_require(name)
	end

	dofile("Launch.lua")
	__mainObject__.continuousIntegrationMode = os.getenv("CI")
	runCallback("OnInit")
	runCallback("OnFrame") -- PoB needs one frame for everything to initialise
end

local function bootLegacyLayout()
	-- The wrapper defines its stubs and runs startup itself; overrides go on afterwards
	dofile("HeadlessWrapper.lua")
	__mainObject__ = launch
	installOverrides()
end

local defFile = io.open("_SimpleGraphic.def.lua", "r")
if defFile then
	defFile:close()
	bootCurrentLayout()
else
	bootLegacyLayout()
end

if __mainObject__.promptMsg then
	error("PoB startup failed: " .. tostring(__mainObject__.promptMsg) .. "\n" .. table.concat(engineLog, "\n"))
end

-- The unique and rare item databases load a slice per frame; finish loading them now
local mainModule = __mainObject__.main
for _ = 1, 100000 do
	if not (mainModule.uniqueDB and mainModule.uniqueDB.loading) and not (mainModule.rareDB and mainModule.rareDB.loading) then
		break
	end
	runCallback("OnFrame")
end

build = __mainObject__.main.modes["BUILD"]

function newBuild()
	if GlobalCache and GlobalCache.cachedData then
		wipeGlobalCache()
	end
	__mainObject__.main:SetMode("BUILD", false, "Unnamed build")
	runCallback("OnFrame")
end

function loadBuildFromXML(xmlText, name)
	__mainObject__.main:SetMode("BUILD", false, name or "", xmlText)
	runCallback("OnFrame")
end
