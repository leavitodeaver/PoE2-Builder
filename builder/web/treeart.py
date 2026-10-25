"""The game's passive tree art for the web page: node icons and frames, cut from the texture sheets
that ship with Path of Building and packed into three browser images under data/.

PoB keeps its sprites in DDS texture arrays (BC1 icons, BC7 frames) compressed with zstd, one layer
per sprite. `build()` decodes the layers the tree's nodes use, resizes them, and packs them into
`frames.png`, `icons.webp` (the lit icons) and `icons-disabled.webp` (the same, dimmed, for
unallocated nodes), with `atlas.json` saying where each sprite sits. It runs once, the first time
the page asks, and again only when the tree data or this module changes.

The art belongs to Grinding Gear Games; it is for local use on this machine only and is never
committed (data/ is ignored by git).
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import threading
from pathlib import Path

from builder import POB_DIR
from builder.data import DATA_ROOT, GameData, pin

VERSION = 4  # bump when the packing changes
FILES = ("frames.png", "icons.webp", "icons-disabled.webp")
ATLAS_WIDTH = 2048
FRAME_MAX = 128  # longest side of a stored frame, in pixels
BIG_ICON, SMALL_ICON = 96, 64  # stored icon sizes: icons from the 128 px sheets and from the 64 px ones
DIM = 0.5  # how bright an unallocated node's icon is
_lock = threading.Lock()


class ArtUnavailable(RuntimeError):
    """The sprite sheets aren't there (PoB isn't fetched) or can't be decoded."""


def art_dir() -> Path:
    return DATA_ROOT / f"tree-art-{pin()['commit'][:10]}"


def sheet_dir(data: GameData) -> Path:
    return POB_DIR / "src" / "TreeData" / str(data.tree["treeVersion"])


def wanted(data: GameData) -> tuple[list[str], list[str]]:
    """(frame names, icon paths) the tree's nodes use."""
    frames: set[str] = {"AscendancyMiddle"}
    icons: set[str] = set()
    for node in data.tree["nodes"]:
        if node.get("frame"):
            frames |= {name for name in node["frame"].values() if name}
        if node.get("icon"):
            icons.add(node["icon"])
        for variant in (node.get("variants") or {}).values():
            if variant.get("icon"):
                icons.add(variant["icon"])
    return sorted(frames), sorted(icons)


def signature(data: GameData) -> str:
    frames, icons = wanted(data)
    sprites = json.dumps(data.tree.get("sprites") or {}, sort_keys=True)
    digest = hashlib.sha1(json.dumps([VERSION, frames, icons, sprites]).encode()).hexdigest()
    return digest[:16]


def status(data: GameData) -> dict | None:
    """The atlas description if it is built and current, else None."""
    path = art_dir() / "atlas.json"
    try:
        atlas = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return atlas if atlas.get("signature") == signature(data) and all((art_dir() / f).exists() for f in FILES) else None


def ensure(data: GameData) -> dict:
    """The atlas description, building the images first if they aren't there yet."""
    atlas = status(data)
    if atlas:
        return atlas
    with _lock:
        return status(data) or build(data)


# Decoding ---------------------------------------------------------------------------------------


def _mip_bytes(width: int, height: int, mips: int, dxgi: int) -> int:
    block = 8 if dxgi in (70, 71, 72) else 16  # BC1 takes 8 bytes per 4x4 block, BC7 16
    total = 0
    for _ in range(mips):
        total += max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block
        width, height = max(1, width // 2), max(1, height // 2)
    return total


class Sheet:
    """A zstd-compressed DDS texture array: layers decode one at a time."""

    def __init__(self, path: Path) -> None:
        import zstandard

        if not path.exists():
            raise ArtUnavailable(f"{path.name} isn't in PoB's tree data; run scripts/fetch_pob.py")
        self.raw = zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=1 << 30)
        if self.raw[:4] != b"DDS " or self.raw[84:88] != b"DX10":
            raise ArtUnavailable(f"{path.name} isn't a DX10 DDS texture array")
        self.height, self.width, _, _, self.mips = struct.unpack("<5I", self.raw[12:32])
        self.dxgi, _, _, self.layers, _ = struct.unpack("<5I", self.raw[128:148])
        self.stride = _mip_bytes(self.width, self.height, self.mips, self.dxgi)
        self.cache: dict[int, object] = {}

    def layer(self, index: int):
        """Layer `index` (0-based) as an RGBA image, its largest mip."""
        from PIL import Image

        if index not in self.cache:
            data = self.raw[148 + index * self.stride: 148 + (index + 1) * self.stride]
            header = bytearray(self.raw[:148])
            struct.pack_into("<I", header, 28, 1)  # one mip
            struct.pack_into("<I", header, 128 + 12, 1)  # one array element
            self.cache[index] = Image.open(io.BytesIO(bytes(header) + data)).convert("RGBA")
        return self.cache[index]


class Sheets:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.sheets: dict[str, Sheet] = {}

    def sprite(self, file: str, coords: list[int]):
        """The sprite at coords [x, y, w, h, layer] (layer counted from 1, as PoB does)."""
        if file not in self.sheets:
            self.sheets[file] = Sheet(self.directory / file)
        x, y, w, h, layer = coords
        return self.sheets[file].layer(layer - 1).crop((x, y, x + w, y + h))


# Packing ----------------------------------------------------------------------------------------


def pack(sizes: dict[str, tuple[int, int]], width: int = ATLAS_WIDTH, pad: int = 1) -> tuple[dict[str, list[int]], int]:
    """Shelf-pack sprites of the given sizes: ({name: [x, y, w, h]}, atlas height)."""
    placed: dict[str, list[int]] = {}
    x = y = shelf = 0
    for name, (w, h) in sorted(sizes.items(), key=lambda kv: (-kv[1][1], kv[0])):
        if x + w + pad > width:
            x, y, shelf = 0, y + shelf + pad, 0
        placed[name] = [x, y, w, h]
        x += w + pad
        shelf = max(shelf, h)
    return placed, y + shelf + pad


def build(data: GameData) -> dict:
    from PIL import Image

    tree = data.tree
    sprites = tree.get("sprites")
    if not sprites:
        raise ArtUnavailable("the tree dump has no sprite coordinates; run `uv run python -m builder.data dump`")
    where: dict[str, tuple[str, list[int]]] = {}  # sprite name -> (sheet file, coords)
    for file, entries in sprites.items():
        if file.startswith("skills-disabled_"):
            continue  # the greyed twins are read from their lit sheet's layer numbers
        parts = file.split("_")
        sheet_size = [int(parts[1]), int(parts[2])] if len(parts) > 3 and parts[1].isdigit() and parts[2].isdigit() else None
        for name, coords in entries.items():
            if isinstance(coords, int) and sheet_size:  # a bare number is the layer, whole
                coords = [0, 0, *sheet_size, coords]
            if isinstance(coords, list) and len(coords) == 5:
                known = where.get(name)
                if known is None or coords[2] > known[1][2]:  # the biggest sheet that has it
                    where[name] = (file, coords)
    frames, icons = wanted(data)
    sheets = Sheets(sheet_dir(data))

    # Frames: cut to their art, longest side at most FRAME_MAX. Many names (every ascendancy's own)
    # are the same layer of a sheet: one copy each
    frame_images = {}
    frame_keys = {}
    for name in frames:
        if name not in where:
            continue
        key = (where[name][0], tuple(where[name][1]))
        frame_keys[name] = key
        if key not in frame_images:
            image = sheets.sprite(*where[name])
            scale = min(1.0, FRAME_MAX / max(image.size))
            if scale < 1.0:
                image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
            frame_images[key] = image
    key_rects, frame_height = pack({repr(k): im.size for k, im in frame_images.items()})
    frame_atlas = Image.new("RGBA", (ATLAS_WIDTH, frame_height))
    for key, image in frame_images.items():
        frame_atlas.paste(image, tuple(key_rects[repr(key)][:2]))
    frame_rects = {name: key_rects[repr(key)] for name, key in frame_keys.items()}

    # Icons, lit, and dimmed for the unallocated look (PoB's own "disabled" sheets are copies of the
    # lit ones: the game darkens them when it draws)
    sizes = {}
    for path in icons:
        if path in where:
            sizes[path] = (BIG_ICON, BIG_ICON) if where[path][1][2] > SMALL_ICON else (SMALL_ICON, SMALL_ICON)
    icon_rects, icon_height = pack(sizes)
    lit = Image.new("RGBA", (ATLAS_WIDTH, icon_height))
    for path, (w, h) in sizes.items():
        image = sheets.sprite(*where[path])
        if image.size != (w, h):
            image = image.resize((w, h), Image.LANCZOS)
        lit.paste(image, tuple(icon_rects[path][:2]))
    red, green, blue, alpha = lit.split()
    dim = Image.merge("RGBA", (*(band.point(lambda v: round(v * DIM)) for band in (red, green, blue)), alpha))
    atlases = {"icons.webp": lit, "icons-disabled.webp": dim}

    target = art_dir()
    target.mkdir(parents=True, exist_ok=True)
    frame_atlas.save(target / "frames.png", optimize=True)
    for label, atlas in atlases.items():
        atlas.save(target / label, "WEBP", quality=88, method=4)
    description = {
        "signature": signature(data), "files": list(FILES), "frames": frame_rects, "icons": icon_rects,
        "missing": sorted(set(frames) - set(frame_rects)) + sorted(set(icons) - set(icon_rects)),
        "notice": "Art © Grinding Gear Games, for local use only.",
    }
    (target / "atlas.json").write_text(json.dumps(description), encoding="utf-8")
    return description


if __name__ == "__main__":
    result = ensure(GameData())
    print(f"{len(result['frames'])} frames, {len(result['icons'])} icons in {art_dir()}; missing: {len(result['missing'])}")
