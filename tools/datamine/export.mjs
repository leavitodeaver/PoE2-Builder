// Read-only extractor for the PoE2 game files (run by `python -m builder.data.gamefiles`).
// Usage: node export.mjs <request.json>
//   request: {"game": "<install dir>", "schema": "<schema.min.json>", "out": "<dir>",
//             "tables": ["SkillGems", ...], "files": ["Metadata/...", ...], "list": ["Metadata/StatDescriptions", ...]}
// Opens game files with fs.readFile only (pathofexile-dat's SteamBundleLoader); never writes to the game folder.
// Tables: every column the community dat-schema names for PoE2, all rows, English (Data/Balance). Columns the library
// can't read (8-bit, 64-bit) are sized and skipped, and listed in the table's report. A schema whose row width differs
// from the file's is reported, not trusted.
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const lib = path.join(path.dirname(fileURLToPath(import.meta.url)), 'node_modules', 'pathofexile-dat', 'dist');
const load = (rel) => import(pathToFileURL(path.join(lib, rel)).href);
const { SteamBundleLoader, CachingBundleLoader } = await load('cli/bundle-loaders.js');
const { FileLoader, decompressSliceInBundle, decompressedBundleSize, readIndexBundle, getDirContent } = await load('bundles.js');
const { readDatFile } = await load('dat/dat-file.js');
const { readColumn } = await load('dat/reader.js');
const { getHeaderLength } = await load('dat/header.js');

const request = JSON.parse(await fs.readFile(process.argv[2], 'utf-8'));
const POE2 = 2;
const bundles = new CachingBundleLoader(new SteamBundleLoader(request.game));
const loader = await FileLoader.create(bundles);
await fs.mkdir(path.join(request.out, 'tables'), { recursive: true });
await fs.mkdir(path.join(request.out, 'files'), { recursive: true });
const report = { tables: {}, files: {}, lists: {} };

// Directory listings (to find stat description files by name)
if (request.list?.length) {
  const indexBin = await bundles.fetchFile('_.index.bin');
  const indexBundle = new Uint8Array(decompressedBundleSize(indexBin));
  decompressSliceInBundle(indexBin, 0, indexBundle);
  const index = readIndexBundle(indexBundle);
  const reps = new Uint8Array(decompressedBundleSize(index.pathRepsBundle));
  decompressSliceInBundle(index.pathRepsBundle, 0, reps);
  for (const dir of request.list) {
    const walk = (d) => {
      const content = getDirContent(d, reps, index.dirsInfo);
      return [...content.files, ...content.dirs.flatMap(walk)];
    };
    report.lists[dir] = walk(dir);
  }
}

const SIZES = { u8: 1, i8: 1, u64: 8, i64: 8, f64: 8 };
function headers(name, dat, schema) {
  const found = schema.tables.filter(t => t.name === name);
  const table = found.find(t => t.validFor & POE2) ?? found.at(0);
  if (!table) throw new Error(`no schema for ${name}`);
  const out = [], skipped = [];
  let offset = 0;
  table.columns.forEach((column, i) => {
    const columnName = column.name || `_unnamed${i}`;
    const t = column.type;
    const type = {
      array: column.array, interval: column.interval,
      integer: t === 'u16' ? { unsigned: true, size: 2 } : t === 'u32' ? { unsigned: true, size: 4 }
        : t === 'i16' ? { unsigned: false, size: 2 } : t === 'i32' || t === 'enumrow' ? { unsigned: false, size: 4 } : undefined,
      decimal: t === 'f32' ? { size: 4 } : undefined,
      string: t === 'string' ? {} : undefined,
      boolean: t === 'bool' ? {} : undefined,
      key: (t === 'row' || t === 'foreignrow') ? { foreign: t === 'foreignrow' } : undefined,
    };
    if (!column.array && SIZES[t]) {
      skipped.push(columnName);
      offset += SIZES[t] * (column.interval ? 2 : 1);
      return;
    }
    const header = { name: columnName, offset, type, references: column.references?.table ?? null };
    out.push(header);
    offset += getHeaderLength(header, dat);
  });
  return { headers: out, skipped, width: offset };
}

const schema = JSON.parse(await fs.readFile(request.schema, 'utf-8'));
for (const name of request.tables ?? []) {
  const bytes = await loader.tryGetFileContents(`Data/Balance/${name}.datc64`) ?? await loader.tryGetFileContents(`Data/${name}.datc64`);
  if (!bytes) { report.tables[name] = { error: 'not in the game files' }; continue; }
  const dat = readDatFile('.datc64', bytes);
  const { headers: hs, skipped, width } = headers(name, dat, schema);
  const entry = { rows: dat.rowCount, rowLength: dat.rowLength, schemaWidth: width, skipped };
  if (width > dat.rowLength && dat.rowCount > 0) {
    entry.error = `schema is wider (${width}) than the file's rows (${dat.rowLength}): not exported`;
    report.tables[name] = entry;
    continue;
  }
  if (width < dat.rowLength) entry.warning = `file rows have ${dat.rowLength - width} bytes the schema doesn't name`;
  const columns = hs.map(h => ({ name: h.name, data: readColumn(h, dat) }));
  const rows = Array.from({ length: dat.rowCount }, (_, i) =>
    Object.fromEntries([['_index', i], ...columns.map(c => [c.name, c.data[i]])]));
  await fs.writeFile(path.join(request.out, 'tables', `${name}.json`), JSON.stringify(rows));
  entry.references = Object.fromEntries(hs.filter(h => h.references).map(h => [h.name, h.references]));
  report.tables[name] = entry;
}

for (const file of request.files ?? []) {
  const bytes = await loader.tryGetFileContents(file);
  if (!bytes) { report.files[file] = { error: 'not in the game files' }; continue; }
  const utf16 = bytes[0] === 0xff && bytes[1] === 0xfe;
  const text = utf16 ? new TextDecoder('utf-16le').decode(bytes.subarray(2)) : null;
  const target = path.join(request.out, 'files', file);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, text ?? bytes);
  report.files[file] = { bytes: bytes.byteLength, text: utf16 };
}
await fs.writeFile(path.join(request.out, 'export-report.json'), JSON.stringify(report, null, 1));
console.log(`exported ${Object.keys(report.tables).length} tables, ${Object.keys(report.files).length} files`);
