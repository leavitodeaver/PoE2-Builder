"use strict";

// Interactive passive tree, drawn on canvases with the game's own node art. Used twice: beside the
// request form (click a notable or keystone to require it, right click to ban it) and in a result
// (read-only: the nodes the build allocated). The data comes from /api/tree: the main tree and each
// ascendancy are separate layers, since the game places ascendancies far from the main tree. The art
// (frames and icons cut from Path of Building's texture sheets, Grinding Gear Games' property, for
// local use) comes from /api/tree/art; without it, and where nodes are too small to show art, nodes
// are drawn as dots.

const NODE = { NORMAL: 0, NOTABLE: 1, KEYSTONE: 2, SOCKET: 3, CLASS_START: 4, ASC_START: 5 };
const NODE_KIND = ["Small passive", "Notable", "Keystone", "Jewel socket", "Class start", "Ascendancy start"];
// Dots (and hit areas) per node type: radius in tree units where the node has no frame, and the least pixels
const NODE_SIZE = [[55, 1], [105, 2], [160, 3.5], [100, 2.5], [170, 4.5], [140, 3.5]];
const ART_MIN_PX = 6; // frames smaller than this on screen are drawn as dots
const MAIN_LAYER = "";
let treeDataPromise = null;
let treeArtPromise = null;

function loadTreeData() {
  if (!treeDataPromise) {
    treeDataPromise = fetch("/api/tree").then((response) => {
      if (!response.ok) throw new Error(`the passive tree didn't load (${response.status})`);
      return response.json();
    }).then(prepareTree).catch((error) => { treeDataPromise = null; throw error; });
  }
  return treeDataPromise;
}

/** The node art, or null when it isn't available (the page then draws dots). */
function loadTreeArt() {
  if (!treeArtPromise) {
    treeArtPromise = (async () => {
      const response = await fetch("/api/tree/art");
      if (!response.ok) return null;
      const atlas = await response.json();
      const load = (name) => new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(`${name} didn't load`));
        image.src = `/api/tree/art/${name}?v=${atlas.signature}`;
      });
      const [frames, lit, dim] = await Promise.all(atlas.files.map(load));
      return { atlas, frames, lit, dim };
    })().catch(() => null);
  }
  return treeArtPromise;
}

function prepareTree(raw) {
  const allClasses = raw.classes.map((c) => c.name);
  const look = (name, stats, icon, classes) => ({ name, stats, icon, classes, key: name.toLowerCase(), text: `${name} ${stats.join(" ")}`.toLowerCase() });
  const switchable = [];
  const nodes = raw.nodes.map(([id, x, y, type, name, stats, asc, icon, frame, iconW, frameW, variants]) => {
    const node = { id, x, y, type, asc, links: [], iconW, frameW, frame, views: null };
    // A node near a class start can be another passive for a class: `views` maps "" (every other class) and
    // each such class to the look it has for that class
    if (variants) {
      const own = variants.map((v) => v[0]);
      node.base = look(name, stats, icon, allClasses.filter((c) => !own.includes(c)));
      node.views = new Map([["", node.base], ...variants.map(([cls, vname, vstats, vicon]) => [cls, look(vname, vstats, vicon, [cls])])]);
      switchable.push(node);
    } else {
      node.base = look(name, stats, icon, null);
    }
    return node;
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const layers = new Map();
  const layerFor = (name) => {
    if (!layers.has(name)) layers.set(name, { name, nodes: [], edges: [], grid: null });
    return layers.get(name);
  };
  for (const node of nodes) layerFor(node.asc).nodes.push(node);
  for (const [a, b] of raw.edges) {
    const from = byId.get(a);
    const to = byId.get(b);
    if (!from || !to) continue;
    from.links.push(to);
    to.links.push(from);
    layerFor(from.asc).edges.push([from, to]);
  }
  for (const layer of layers.values()) layer.bounds = boundsOf(layer.nodes);
  const nameCount = new Map();
  for (const node of nodes) nameCount.set(node.base.key, (nameCount.get(node.base.key) || 0) + 1);
  // Names of the versions a class gets: found when no node shows that name for the class being viewed
  const variantNodes = new Map();
  for (const node of switchable) {
    for (const view of node.views.values()) {
      if (!variantNodes.has(view.key)) variantNodes.set(view.key, []);
      variantNodes.get(view.key).push(node);
    }
  }
  const classes = new Map(raw.classes.map((c) => [c.name, byId.get(c.start)]));
  // An ascendancy with no nodes of its own (Abyssal Lich) shows the layer of the one it shares its start with (Lich)
  const layerNames = new Map();
  for (const a of raw.ascendancies) {
    const twin = raw.ascendancies.find((b) => b.start === a.start && layers.has(b.name));
    layerNames.set(a.name, layers.has(a.name) ? a.name : twin ? twin.name : "");
  }
  const rules = new Map(Object.entries(raw.rules || {}).map(([name, rule]) => [name.toLowerCase(), rule]));
  const variantClasses = [...new Set(switchable.flatMap((n) => [...n.views.keys()].filter(Boolean)))].sort();
  return {
    nodes, byId, layers, nameCount, classes, allClasses, ascendancies: raw.ascendancies, layerNames, rules, switchable,
    variantNodes, variantClasses, icons: raw.icons, frames: raw.frames,
  };
}

function boundsOf(nodes) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const n of nodes) {
    if (n.x < x0) x0 = n.x;
    if (n.x > x1) x1 = n.x;
    if (n.y < y0) y0 = n.y;
    if (n.y > y1) y1 = n.y;
  }
  return { x0, y0, x1, y1 };
}

const targetable = (node) => node.type === NODE.NOTABLE || node.type === NODE.KEYSTONE;

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}

// One canvas: a layer's nodes with zoom, pan, hover and clicks -------------------------------------

class TreeView {
  constructor(tree, stage, label) {
    this.tree = tree;
    this.stage = stage;
    this.layer = null;
    this.scale = 0.02;
    this.tx = 0;
    this.ty = 0;
    this.width = 0;
    this.height = 0;
    this.home = null; // the nodes "Reset" fits (null: the whole layer)
    this.moved = false; // has the user zoomed or panned since the last fit?
    this.hover = null;
    this.pointers = new Map();
    this.drag = null;
    this.pinch = null;
    this.queued = false;
    this.canvas = h("canvas", { tabindex: "0", role: "img", "aria-label": label });
    this.tip = h("div", { class: "tree-tip", hidden: true });
    this.empty = h("div", { class: "tree-empty", hidden: true });
    this.toast = h("div", { class: "tree-toast", role: "status", hidden: true });
    stage.append(this.canvas, this.tip, this.empty, this.toast);
    this.ctx = this.canvas.getContext("2d");
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(stage);
    this.bind();
  }

  destroy() {
    this.observer.disconnect();
  }

  setLayer(layer, home = null) {
    this.layer = layer;
    this.home = home;
    this.hover = null;
    this.tip.hidden = true;
    if (layer && !layer.grid) layer.grid = buildGrid(layer.nodes);
    this.fit();
  }

  setEmpty(message) {
    this.empty.hidden = !message;
    this.empty.textContent = message || "";
  }

  resize() {
    const width = this.stage.clientWidth;
    const height = this.stage.clientHeight;
    if (!width || !height || (width === this.width && height === this.height)) return;
    const first = !this.width;
    const centre = first ? null : this.toWorld(this.width / 2, this.height / 2);
    this.width = width;
    this.height = height;
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.ratio = ratio;
    if (first || !this.moved) {
      this.fit();
      if (this.pendingCentre) { // asked to centre on a node before there was a size to centre in
        const [node, minScale] = this.pendingCentre;
        this.pendingCentre = null;
        this.centreOn(node, minScale);
      }
    } else {
      this.computeLimits();
      this.scale = Math.min(this.maxScale, Math.max(this.minScale, this.scale));
      this.tx = width / 2 - centre.x * this.scale;
      this.ty = height / 2 - centre.y * this.scale;
      this.requestDraw();
    }
  }

  computeLimits() {
    const b = this.layer?.bounds;
    if (!b || !this.width) return;
    const fit = Math.min(this.width / (b.x1 - b.x0 + 1), this.height / (b.y1 - b.y0 + 1));
    const main = this.layer.name === MAIN_LAYER;
    this.minScale = fit * 0.6;
    this.maxScale = Math.max(fit, Math.min(fit * (main ? 60 : 8), main ? 0.5 : 0.6));
  }

  fit() {
    if (!this.layer || !this.width) return;
    this.computeLimits();
    const nodes = this.home && this.home.length ? this.home : this.layer.nodes;
    const b = nodes === this.layer.nodes ? this.layer.bounds : boundsOf(nodes);
    const pad = 1.12;
    const w = Math.max(b.x1 - b.x0 + 440, 400) * pad; // 440: room for the nodes at the edge
    const hgt = Math.max(b.y1 - b.y0 + 440, 400) * pad;
    this.scale = Math.min(this.maxScale, Math.max(this.minScale, Math.min(this.width / w, this.height / hgt)));
    this.tx = this.width / 2 - ((b.x0 + b.x1) / 2) * this.scale;
    this.ty = this.height / 2 - ((b.y0 + b.y1) / 2) * this.scale;
    this.moved = false;
    this.requestDraw();
  }

  toWorld(px, py) {
    return { x: (px - this.tx) / this.scale, y: (py - this.ty) / this.scale };
  }

  zoomAt(factor, px = this.width / 2, py = this.height / 2) {
    if (!this.layer) return;
    const scale = Math.min(this.maxScale, Math.max(this.minScale, this.scale * factor));
    const world = this.toWorld(px, py);
    this.scale = scale;
    this.tx = px - world.x * scale;
    this.ty = py - world.y * scale;
    this.moved = true;
    this.clampPan();
    this.requestDraw();
  }

  clampPan() {
    const b = this.layer.bounds;
    const centre = this.toWorld(this.width / 2, this.height / 2);
    const mx = (b.x1 - b.x0) * 0.08, my = (b.y1 - b.y0) * 0.08;
    const x = Math.min(b.x1 + mx, Math.max(b.x0 - mx, centre.x));
    const y = Math.min(b.y1 + my, Math.max(b.y0 - my, centre.y));
    this.tx = this.width / 2 - x * this.scale;
    this.ty = this.height / 2 - y * this.scale;
  }

  centreOn(node, minScale = 0) {
    if (!this.width || !this.layer) { this.pendingCentre = [node, minScale]; return; }
    this.scale = Math.min(this.maxScale, Math.max(this.minScale, this.scale, minScale));
    this.tx = this.width / 2 - node.x * this.scale;
    this.ty = this.height / 2 - node.y * this.scale;
    this.moved = true;
    this.requestDraw();
  }

  requestDraw() {
    if (this.queued) return;
    this.queued = true;
    requestAnimationFrame(() => { this.queued = false; this.draw(); });
  }

  /** On-screen radius of a node: its frame, or at least a dot. */
  radius(node) {
    const [world, least] = NODE_SIZE[node.type];
    const size = node.frameW > 1 ? node.frameW : world * 2;
    return Math.min(90, Math.max(least, size * 0.5 * this.scale));
  }

  /** Is this node drawn with art at this zoom? */
  hasArt(node) {
    return !!this.tree.art && node.frameW > 1 && node.frameW * this.scale >= ART_MIN_PX;
  }

  // Which node is at this pixel: the nearest within its drawn size plus a margin for the pointer
  hit(px, py, coarse = false) {
    if (!this.layer) return null;
    const margin = coarse ? 10 : 3;
    const reach = (60 + margin) / this.scale;
    const here = this.toWorld(px, py);
    const grid = this.layer.grid;
    let best = null, bestScore = Infinity;
    for (let cx = Math.floor((here.x - reach) / GRID); cx <= Math.floor((here.x + reach) / GRID); cx++) {
      for (let cy = Math.floor((here.y - reach) / GRID); cy <= Math.floor((here.y + reach) / GRID); cy++) {
        for (const node of grid.get(cx * 4096 + cy) || []) {
          const distance = Math.hypot(node.x * this.scale + this.tx - px, node.y * this.scale + this.ty - py);
          const room = this.radius(node) + margin;
          if (distance > room) continue;
          // Notables and keystones win over small passives drawn under the pointer
          const score = distance / room - (targetable(node) ? 0.5 : 0);
          if (score < bestScore) { best = node; bestScore = score; }
        }
      }
    }
    return best;
  }

  // Drawing ---------------------------------------------------------------------------------

  draw() {
    if (!this.layer || !this.width) return;
    const { ctx, tree } = this;
    if (tree.scheme.matches !== tree.dark) tree.onScheme(); // the theme changed and the change event hasn't arrived yet
    const colors = tree.colors;
    const alloc = tree.allocated;
    const marks = tree.markedNodes;
    const scale = this.scale, tx = this.tx, ty = this.ty, w = this.width, hgt = this.height;
    ctx.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
    ctx.clearRect(0, 0, w, hgt);

    // Edges: plain ones, then (in a result) those between allocated nodes
    const plain = new Path2D();
    const lit = new Path2D();
    for (const [a, b] of this.layer.edges) {
      const ax = a.x * scale + tx, ay = a.y * scale + ty, bx = b.x * scale + tx, by = b.y * scale + ty;
      if ((ax < -10 && bx < -10) || (ax > w + 10 && bx > w + 10) || (ay < -10 && by < -10) || (ay > hgt + 10 && by > hgt + 10)) continue;
      const path = alloc && alloc.has(a.id) && alloc.has(b.id) ? lit : plain;
      path.moveTo(ax, ay);
      path.lineTo(bx, by);
    }
    ctx.lineCap = "round";
    ctx.lineWidth = Math.max(1, Math.min(2.4, 8 * scale));
    ctx.globalAlpha = alloc ? 0.3 : 0.5;
    ctx.strokeStyle = colors.muted;
    ctx.stroke(plain);
    if (alloc) {
      ctx.globalAlpha = 0.95;
      ctx.lineWidth = Math.max(1.6, Math.min(4, 24 * scale));
      ctx.strokeStyle = colors.accent;
      ctx.stroke(lit);
    }

    // Nodes too small for art are dots, batched by look; the rest are drawn with their frames and icons
    const batches = new Map();
    const arts = [];
    for (const node of this.layer.nodes) {
      const x = node.x * scale + tx, y = node.y * scale + ty;
      if (x < -60 || x > w + 60 || y < -60 || y > hgt + 60) continue;
      if (this.hasArt(node)) { arts.push(node, x, y); continue; }
      const on = alloc ? alloc.has(node.id) : false;
      const key = node.type * 2 + (on ? 1 : 0);
      if (!batches.has(key)) batches.set(key, []);
      batches.get(key).push(x, y, this.radius(node) * (on ? 1.15 : 1));
    }
    for (const [key, list] of batches) {
      const type = key >> 1, on = key & 1;
      const color = on ? colors.accent : [colors.muted, colors.text, colors.gold, colors.muted, colors.text, colors.gold][type];
      ctx.globalAlpha = on ? 1 : alloc ? 0.35 : type === NODE.NORMAL ? 0.7 : 0.95;
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = type === NODE.CLASS_START ? 2.5 : 1.5;
      ctx.beginPath();
      for (let i = 0; i < list.length; i += 3) {
        ctx.moveTo(list[i] + list[i + 2], list[i + 1]);
        ctx.arc(list[i], list[i + 1], list[i + 2], 0, Math.PI * 2);
      }
      if (type === NODE.SOCKET || type === NODE.ASC_START || type === NODE.CLASS_START) ctx.stroke(); else ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (arts.length) {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      for (let i = 0; i < arts.length; i += 3) {
        const node = arts[i];
        const lit_ = (alloc && alloc.has(node.id)) || marks.get(node) === "require" || node === this.hover;
        this.drawArt(node, arts[i + 1], arts[i + 2], lit_);
      }
    }

    // Socketed radius jewels: the radius (a ring for Controlled Metamorphosis), the allocated
    // passives in it, and those the tree holds only through the jewel (dashed)
    if (this.layer.name === MAIN_LAYER) {
      for (const jewel of tree.radiusJewels) {
        ctx.strokeStyle = colors.gold;
        ctx.fillStyle = colors.gold;
        ctx.lineWidth = 1.5;
        for (const centre of jewel.centreNodes) {
          const x = centre.x * scale + tx, y = centre.y * scale + ty;
          ctx.beginPath();
          ctx.arc(x, y, jewel.radius.outer * scale, 0, Math.PI * 2);
          if (jewel.radius.inner > 0) {
            ctx.moveTo(x + jewel.radius.inner * scale, y);
            ctx.arc(x, y, jewel.radius.inner * scale, 0, Math.PI * 2, true);
          }
          ctx.globalAlpha = 0.07;
          ctx.fill("evenodd");
          ctx.globalAlpha = 0.6;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        for (const node of jewel.affectedNodes) this.ring(node, colors.gold, 3, 1.5);
        ctx.setLineDash([3, 3]);
        for (const node of jewel.unconnectedNodes) this.ring(node, colors.gold, 6, 2);
        ctx.setLineDash([]);
      }
    }

    // The chosen class's starting point
    const start = tree.startNode;
    if (start && start.asc === this.layer.name) this.ring(start, colors.accent, 5, 2);

    // Search matches, then the required and banned passives
    for (const node of tree.matches) if (node.asc === this.layer.name) this.ring(node, colors.accent, 4, 2);
    const labels = [];
    for (const [node, mode] of marks) {
      if (node.asc !== this.layer.name) continue;
      const x = node.x * scale + tx, y = node.y * scale + ty;
      if (x < -60 || x > w + 60 || y < -60 || y > hgt + 60) continue;
      this.mark(node, mode, x, y);
      labels.push({ node, colour: mode === "require" ? colors.ok : colors.bad, rank: 0 });
    }
    if (this.hover) this.ring(this.hover, colors.text, 3, 2);

    // Names: marked passives always; the rest as zoom allows
    const main = this.layer.name === MAIN_LAYER;
    const showNotable = main ? scale >= 0.1 : scale >= 0.05;
    const showKeystone = main ? scale >= 0.03 : scale >= 0.05;
    const showAllocated = scale >= 0.035;
    for (const node of tree.matches) if (node.asc === this.layer.name && targetable(node)) labels.push({ node, colour: colors.text, rank: 1 });
    if (alloc && showAllocated) {
      for (const node of this.layer.nodes) if (targetable(node) && alloc.has(node.id)) labels.push({ node, colour: colors.text, rank: 2 });
    }
    for (const node of this.layer.nodes) {
      if ((node.type === NODE.KEYSTONE && showKeystone) || (node.type === NODE.NOTABLE && showNotable)) {
        labels.push({ node, colour: colors.muted, rank: node.type === NODE.KEYSTONE ? 3 : 4 });
      }
    }
    labels.sort((a, b) => a.rank - b.rank);
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.lineJoin = "round";
    const placed = [];
    for (const { node, colour } of labels) {
      const name = tree.shown(node).name;
      const x = node.x * scale + tx, y = node.y * scale + ty - this.radius(node) - 4;
      if (x < 0 || x > w || y < 0 || y > hgt + 12) continue;
      const half = ctx.measureText(name).width / 2 + 2;
      const box = [x - half, y - 13, x + half, y + 1];
      if (placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) continue;
      placed.push(box);
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = colors.surface;
      ctx.strokeText(name, x, y);
      ctx.fillStyle = colour;
      ctx.fillText(name, x, y);
    }
  }

  /** One node's icon, then its frame over it; lit when allocated, required or hovered, dimmed otherwise. */
  drawArt(node, x, y, lit) {
    const { ctx, tree } = this;
    const { art, data } = tree;
    const shown = tree.shown(node);
    const icon = art.atlas.icons[data.icons[shown.icon]];
    if (icon && node.iconW) {
      const size = node.iconW * this.scale;
      ctx.drawImage(lit ? art.lit : art.dim, icon[0], icon[1], icon[2], icon[3], x - size / 2, y - size / 2, size, size);
    }
    const frame = art.atlas.frames[(data.frames[node.frame] || [])[lit ? 0 : 2]];
    if (frame) {
      const width = node.frameW * this.scale, height = (width * frame[3]) / frame[2];
      ctx.drawImage(art.frames, frame[0], frame[1], frame[2], frame[3], x - width / 2, y - height / 2, width, height);
    }
    if (node.views && node.frameW * this.scale >= 14) this.badge(node, x, y);
  }

  /** A small mark on the nodes that are a different passive for some classes. */
  badge(node, x, y) {
    const { ctx } = this;
    const r = this.radius(node) * 0.72;
    ctx.fillStyle = this.tree.colors.accent;
    ctx.beginPath();
    ctx.arc(x + r, y - r, 5.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = this.tree.colors.surface;
    ctx.font = "700 9px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("⇄", x + r, y - r + 0.5);
  }

  /** Required: a green ring around the lit node; banned: a red ring and a cross. Dots get a filled disk. */
  mark(node, mode, x, y) {
    const { ctx } = this;
    const colour = mode === "require" ? this.tree.colors.ok : this.tree.colors.bad;
    const art = this.hasArt(node);
    const r = this.radius(node) + (art ? 1 : 3);
    if (!art) {
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.arc(x, y, r - 1, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.strokeStyle = colour;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.arc(x, y, r + 1, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(x, y, r + 1, 0, Math.PI * 2);
    ctx.stroke();
    if (mode === "forbid") {
      const k = r * 0.55;
      ctx.strokeStyle = art ? colour : this.tree.colors.surface;
      ctx.lineWidth = art ? 3 : 2;
      ctx.beginPath();
      ctx.moveTo(x - k, y - k); ctx.lineTo(x + k, y + k);
      ctx.moveTo(x + k, y - k); ctx.lineTo(x - k, y + k);
      ctx.stroke();
    }
  }

  ring(node, colour, extra, width) {
    const x = node.x * this.scale + this.tx, y = node.y * this.scale + this.ty;
    if (x < -30 || x > this.width + 30 || y < -30 || y > this.height + 30) return;
    this.ctx.strokeStyle = colour;
    this.ctx.lineWidth = width;
    this.ctx.beginPath();
    this.ctx.arc(x, y, this.radius(node) + extra, 0, Math.PI * 2);
    this.ctx.stroke();
  }

  // Pointer and keyboard ------------------------------------------------------------------------

  bind() {
    const c = this.canvas;
    c.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = c.getBoundingClientRect();
      const unit = event.deltaMode === 1 ? 0.05 : event.deltaMode === 2 ? 0.5 : 0.0016;
      this.zoomAt(Math.exp(-event.deltaY * unit), event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    c.addEventListener("pointerdown", (event) => {
      if (event.button === 2) return; // right clicks are handled by contextmenu
      c.setPointerCapture(event.pointerId);
      this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (this.pointers.size === 1) {
        this.drag = { startX: event.clientX, startY: event.clientY, tx: this.tx, ty: this.ty, moved: false, touch: event.pointerType === "touch" };
      } else if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        this.pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: this.scale };
        if (this.drag) this.drag.moved = true;
      }
    });
    c.addEventListener("pointermove", (event) => {
      const rect = c.getBoundingClientRect();
      const px = event.clientX - rect.left, py = event.clientY - rect.top;
      if (this.pointers.has(event.pointerId)) this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (this.pinch && this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        this.zoomAt((this.pinch.scale * distance) / this.pinch.distance / this.scale, (a.x + b.x) / 2 - rect.left, (a.y + b.y) / 2 - rect.top);
        this.hideTip();
        return;
      }
      if (this.drag) {
        const dx = event.clientX - this.drag.startX, dy = event.clientY - this.drag.startY;
        if (!this.drag.moved && Math.hypot(dx, dy) > 4) this.drag.moved = true;
        if (this.drag.moved) {
          this.tx = this.drag.tx + dx;
          this.ty = this.drag.ty + dy;
          this.moved = true;
          this.clampPan();
          this.hideTip();
          this.requestDraw();
          return;
        }
      }
      if (event.pointerType !== "touch") this.setHover(this.hit(px, py), px, py);
    });
    const release = (event) => {
      const drag = this.drag;
      this.pointers.delete(event.pointerId);
      if (this.pointers.size < 2) this.pinch = null;
      if (this.pointers.size === 0) this.drag = null;
      if (event.type === "pointerup" && drag && !drag.moved && event.button === 0) {
        const rect = c.getBoundingClientRect();
        const px = event.clientX - rect.left, py = event.clientY - rect.top;
        const node = this.hit(px, py, drag.touch);
        this.setHover(node, px, py);
        if (node) this.tree.clicked(node, "primary", drag.touch, this);
      }
    };
    c.addEventListener("pointerup", release);
    c.addEventListener("pointercancel", release);
    c.addEventListener("pointerleave", () => { if (!this.drag) this.setHover(null); });
    c.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const rect = c.getBoundingClientRect();
      const node = this.hit(event.clientX - rect.left, event.clientY - rect.top);
      if (node) this.tree.clicked(node, "secondary", false, this);
    });
    c.addEventListener("keydown", (event) => {
      const step = 60;
      const actions = { "+": () => this.zoomAt(1.4), "=": () => this.zoomAt(1.4), "-": () => this.zoomAt(1 / 1.4), "0": () => this.fit(),
        ArrowLeft: () => this.pan(step, 0), ArrowRight: () => this.pan(-step, 0), ArrowUp: () => this.pan(0, step), ArrowDown: () => this.pan(0, -step) };
      if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
    });
  }

  pan(dx, dy) {
    this.tx += dx;
    this.ty += dy;
    this.moved = true;
    this.clampPan();
    this.requestDraw();
  }

  setHover(node, px = 0, py = 0) {
    if (!node) { this.hideTip(); return; }
    if (node !== this.hover) {
      this.hover = node;
      this.requestDraw();
      this.tree.fillTip(this.tip, node);
    }
    this.tip.hidden = false;
    const left = Math.min(px + 14, Math.max(4, this.width - this.tip.offsetWidth - 4));
    const top = py + 16 + this.tip.offsetHeight > this.height ? Math.max(4, py - this.tip.offsetHeight - 10) : py + 16;
    this.tip.style.left = `${Math.max(4, left)}px`;
    this.tip.style.top = `${top}px`;
  }

  hideTip() {
    this.tip.hidden = true;
    if (this.hover) { this.hover = null; this.requestDraw(); }
  }
}

const GRID = 400;

function buildGrid(nodes) {
  const grid = new Map();
  for (const node of nodes) {
    const key = Math.floor(node.x / GRID) * 4096 + Math.floor(node.y / GRID);
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(node);
  }
  return grid;
}

// The panel: toolbar, the main tree, an ascendancy view, notes and legend --------------------------

class PassiveTree {
  /* options: interactive (click to require/ban), marks() -> {require: Set, forbid: Set} of lowercase names,
     onMark(name, "require" | "forbid", {classes}) (classes: the classes that have that version of the node, or null),
     ascendancySelect (offer a choice of ascendancy), help (a node shown under the toolbar) */
  constructor(root, options = {}) {
    this.options = { interactive: false, ascendancySelect: false, marks: () => ({ require: new Set(), forbid: new Set() }), ...options };
    this.root = root;
    this.data = null;
    this.art = null;
    this.allocated = null;
    this.radiusJewels = [];
    this.radiusNotes = new Map();
    this.matches = [];
    this.marked = [];
    this.markedNodes = new Map();
    this.startNode = null;
    this.asc = "";
    this.className = "";
    this.variantChoice = "follow"; // "follow" the class, "base" (every other class) or a class's name
    this.variantClass = "";
    this.byName = new Map();
    this.tapMode = "require";
    this.query = "";
    this.cursor = -1;
    this.noteTimer = null;
    this.scheme = window.matchMedia("(prefers-color-scheme: dark)");
    this.dark = this.scheme.matches;
    this.colors = this.readColors();
    this.build();
    this.onScheme = () => { this.dark = this.scheme.matches; this.colors = this.readColors(); this.redraw(); };
    this.scheme.addEventListener("change", this.onScheme);
    loadTreeData().then((data) => this.attach(data)).catch((error) => {
      this.main.setEmpty(`The passive tree couldn't load: ${error.message}`);
    });
    loadTreeArt().then((art) => { if (art) { this.art = art; this.redraw(); } });
  }

  readColors() {
    const style = getComputedStyle(document.documentElement);
    const get = (name) => style.getPropertyValue(name).trim() || "#888";
    return { text: get("--text"), muted: get("--muted"), accent: get("--accent"), ok: get("--ok"), bad: get("--bad"),
      gold: get("--pending"), surface: get("--surface-2") };
  }

  build() {
    const o = this.options;
    this.search = h("input", { type: "search", class: "tree-search", placeholder: "Find a passive by name or effect", "aria-label": "Find a passive on the tree",
      oninput: () => this.setQuery(this.search.value), onkeydown: (event) => { if (event.key === "Enter") { event.preventDefault(); this.nextMatch(); } } });
    this.count = h("span", { class: "tree-count hint", "aria-live": "polite" });
    const button = (label, title, fn) => h("button", { type: "button", title, "aria-label": title, onclick: fn }, label);
    this.modeButtons = ["require", "forbid"].map((mode) => h("button", { type: "button", "data-mode": mode, "aria-pressed": String(mode === this.tapMode),
      onclick: () => this.setTapMode(mode) }, mode === "require" ? "Tap: require" : "Tap: ban"));
    this.variantSelect = o.interactive ? h("select", { "aria-label": "Which class's version of the nodes that differ by class to show",
      onchange: () => this.setVariantChoice(this.variantSelect.value) }) : null;
    const tools = h("div", { class: "tree-tools" }, this.search,
      h("span", { class: "tree-buttons" }, button("−", "Zoom out", () => this.main.zoomAt(1 / 1.5)), button("+", "Zoom in", () => this.main.zoomAt(1.5)),
        button("Reset", "Reset the view", () => { this.main.fit(); this.ascView.fit(); })),
      o.interactive ? h("span", { class: "tree-mode", role: "group", "aria-label": "What a tap does" }, this.modeButtons) : null,
      this.variantSelect ? h("label", { class: "tree-variant" }, "Nodes that differ by class (⇄) show", this.variantSelect) : null, this.count);
    this.mainStage = h("div", { class: "tree-stage tree-stage-main" });
    this.ascStage = h("div", { class: "tree-stage tree-stage-asc" });
    this.ascSelect = o.ascendancySelect ? h("select", { "aria-label": "Ascendancy shown", onchange: () => this.setAscendancy(this.ascSelect.value) }) : null;
    this.ascTitle = h("span", { class: "tree-asc-title" }, "Ascendancy");
    this.note = h("p", { class: "tree-note", role: "status" });
    this.legend = h("div", { class: "tree-legend" });
    this.body = h("div", { class: "tree-body" },
      h("div", { class: "tree-pane tree-pane-main" }, this.mainStage),
      h("div", { class: "tree-pane tree-pane-asc" }, h("div", { class: "tree-asc-head" }, this.ascTitle, this.ascSelect), this.ascStage));
    this.root.classList.add("tree");
    this.root.replaceChildren(tools, o.help || null, this.body, this.note, this.legend);
    this.main = new TreeView(this, this.mainStage, "Passive tree");
    this.ascView = new TreeView(this, this.ascStage, "Ascendancy passives");
    this.main.setEmpty("Loading the passive tree…");
    this.ascView.setEmpty("Loading…");
    // Two columns when the panel is wide, stacked when it is narrow
    this.layoutObserver = new ResizeObserver(() => this.root.classList.toggle("wide", this.root.clientWidth >= 760));
    this.layoutObserver.observe(this.root);
    this.renderLegend();
  }

  attach(data) {
    this.data = data;
    this.main.setEmpty("");
    this.main.setLayer(data.layers.get(MAIN_LAYER));
    if (this.ascSelect) this.fillAscendancies();
    this.fillVariants();
    this.applyAscendancy();
    this.applyClass();
    this.applyAllocated();
    this.refresh();
  }

  destroy() {
    this.main.destroy();
    this.ascView.destroy();
    this.layoutObserver.disconnect();
    this.scheme.removeEventListener("change", this.onScheme);
    clearTimeout(this.noteTimer);
  }

  redraw() {
    this.main.requestDraw();
    this.ascView.requestDraw();
  }

  /** What a node looks like and is called for the class being shown (a node near a class start can differ by class). */
  shown(node) {
    return node.views ? node.views.get(this.variantClass) || node.base : node.base;
  }

  // State the page sets ---------------------------------------------------------------------------

  /** Choose which ascendancy the second view shows ("" for none). */
  setAscendancy(name) {
    this.asc = name || "";
    if (this.ascSelect) this.ascSelect.value = this.asc;
    this.applyAscendancy();
  }

  applyAscendancy() {
    if (!this.data) return;
    this.ascLayerName = this.asc ? this.data.layerNames.get(this.asc) || "" : "";
    const layer = this.ascLayerName ? this.data.layers.get(this.ascLayerName) : null;
    this.ascTitle.textContent = this.asc ? `Ascendancy: ${this.asc}` : "Ascendancy";
    this.ascStage.classList.toggle("idle", !layer);
    if (layer) {
      this.ascView.setEmpty("");
      this.ascView.setLayer(layer);
    } else {
      this.ascView.layer = null;
      this.ascView.setEmpty(this.options.interactive ? "Pick an ascendancy above to see its passives." : "No ascendancy.");
      const ctx = this.ascView.ctx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.ascView.canvas.width, this.ascView.canvas.height);
    }
    this.updateMatches();
  }

  /** The ascendancies to offer (the chosen class's, or all of them) and the one shown. */
  setAscendancyChoices(names, selected) {
    this.ascChoices = names;
    if (this.data) this.fillAscendancies();
    this.setAscendancy(selected && names.includes(selected) ? selected : "");
  }

  fillAscendancies() {
    if (!this.ascSelect) return;
    const names = this.ascChoices ?? this.data.ascendancies.map((a) => a.name);
    this.ascSelect.replaceChildren(h("option", { value: "" }, "none shown"), ...names.map((n) => h("option", { value: n }, n)));
    this.ascSelect.value = names.includes(this.asc) ? this.asc : "";
  }

  setClass(name, focus = false) {
    this.className = name || "";
    this.focusClass = focus;
    this.applyClass();
  }

  applyClass() {
    if (!this.data) return;
    this.startNode = this.className ? this.data.classes.get(this.className) || null : null;
    if (this.startNode && this.focusClass) this.main.centreOn(this.startNode, 0.07);
    this.focusClass = false;
    this.applyVariants();
  }

  // Nodes near a class start that differ by class -----------------------------------------------

  fillVariants() {
    if (!this.variantSelect) return;
    const classes = this.data.variantClasses;
    this.variantSelect.replaceChildren(h("option", { value: "follow" }, "the class chosen above"),
      ...classes.map((c) => h("option", { value: c }, `${c}'s version`)), h("option", { value: "base" }, "the other classes' version"));
    this.variantSelect.value = this.variantChoice;
  }

  setVariantChoice(choice) {
    this.variantChoice = choice;
    this.applyVariants();
  }

  /** Show each such node as the chosen class (or the other classes) gets it. */
  applyVariants() {
    if (!this.data) return;
    this.variantClass = this.variantChoice === "follow" ? this.className : this.variantChoice === "base" ? "" : this.variantChoice;
    // Names by what each node shows now, for finding the marked ones
    this.byName = new Map();
    for (const node of this.data.nodes) {
      if (!targetable(node)) continue;
      const key = this.shown(node).key;
      if (!this.byName.has(key)) this.byName.set(key, []);
      this.byName.get(key).push(node);
    }
    this.updateMatches();
    this.refresh();
  }

  /** The node ids a result's build allocated (null: no allocation to show), and its socketed radius
   * jewels ({socket, name, radius: {label, inner, outer}, centres, affected, unconnected}). */
  setAllocated(ids, radiusJewels = []) {
    this.allocatedIds = ids;
    this.radiusJewelData = radiusJewels;
    this.applyAllocated();
  }

  applyAllocated() {
    if (!this.data) return;
    this.allocated = this.allocatedIds ? new Set(this.allocatedIds) : null;
    const byId = this.data.byId;
    const known = (ids) => (ids || []).map((id) => byId.get(id)).filter(Boolean);
    this.radiusJewels = (this.radiusJewelData || []).map((jewel) => {
      const held = new Set(jewel.unconnected);
      const affected = jewel.affected.filter((id) => !held.has(id) && id !== jewel.socket);
      return { ...jewel, affected, socketNode: byId.get(jewel.socket), centreNodes: known(jewel.centres),
        affectedNodes: known(affected), unconnectedNodes: known(jewel.unconnected) };
    });
    // Node id -> what the tooltip says about the radius jewels reaching it
    this.radiusNotes = new Map();
    const note = (id, text) => this.radiusNotes.set(id, [...(this.radiusNotes.get(id) || []), text]);
    for (const jewel of this.radiusJewels) {
      note(jewel.socket, `${jewel.name}: ${jewel.radius.label} radius`);
      for (const id of jewel.affected) note(id, `In the radius of ${jewel.name}`);
      for (const id of jewel.unconnected) note(id, `Allocated without a connection, through ${jewel.name}`);
    }
    if (this.allocated) {
      const nodes = this.data.nodes.filter((n) => this.allocated.has(n.id));
      const mainNodes = nodes.filter((n) => n.asc === MAIN_LAYER);
      this.main.home = mainNodes;
      this.main.fit();
      this.legend.classList.add("has-allocation");
    }
    this.renderLegend();
    this.redraw();
  }

  /** Re-read the required and banned passives (the form changed). */
  refresh() {
    if (!this.data) return;
    const marks = this.options.marks();
    this.marked = [];
    this.markedNodes = new Map();
    for (const [mode, names] of [["require", marks.require], ["forbid", marks.forbid]]) {
      for (const name of names) {
        // Under the class being shown; a version only some class has is found whichever class that is
        const nodes = this.byName.get(name) || this.data.variantNodes.get(name) || [];
        for (const node of nodes) {
          this.marked.push({ node, mode });
          this.markedNodes.set(node, mode);
        }
      }
    }
    this.markCounts = { require: marks.require.size, forbid: marks.forbid.size };
    for (const view of [this.main, this.ascView]) if (view.hover) this.fillTip(view.tip, view.hover);
    this.renderLegend();
    this.redraw();
  }

  renderLegend() {
    const swatch = (cls, label) => h("span", { class: "tree-key" }, h("i", { class: cls }), label);
    const parts = [];
    if (this.allocatedIds) parts.push(swatch("k-alloc", `allocated (${this.allocatedIds.length})`));
    for (const jewel of this.radiusJewels) {
      const held = jewel.unconnected.length ? `, ${jewel.unconnected.length} held through it` : "";
      parts.push(swatch(jewel.unconnected.length ? "k-radius k-held" : "k-radius", `${jewel.name} radius${held}`));
    }
    const c = this.markCounts || { require: 0, forbid: 0 };
    parts.push(swatch("k-require", `required (${c.require})`), swatch("k-forbid", `banned (${c.forbid})`));
    if (this.options.interactive) parts.push(swatch("k-switch", "differs by class"));
    this.legend.replaceChildren(...parts);
  }

  // Search ----------------------------------------------------------------------------------------

  setQuery(text) {
    this.query = text.trim().toLowerCase();
    this.cursor = -1;
    this.updateMatches();
  }

  updateMatches() {
    if (!this.data) return;
    const q = this.query;
    this.matches = q.length >= 2 ? this.data.nodes.filter((n) => this.shown(n).text.includes(q)) : [];
    this.matches.sort((a, b) => (targetable(b) - targetable(a)) || ((this.shown(b).key.includes(q)) - (this.shown(a).key.includes(q))));
    if (!q) this.count.textContent = "";
    else if (q.length < 2) this.count.textContent = "type two letters";
    else if (!this.matches.length) this.count.textContent = "no match";
    else {
      const elsewhere = new Set(this.matches.filter((n) => n.asc && n.asc !== this.ascLayerName).map((n) => n.asc));
      this.count.textContent = `${this.matches.length} match${this.matches.length === 1 ? "" : "es"}`
        + (elsewhere.size ? `, some in ${[...elsewhere].slice(0, 3).join(", ")}${elsewhere.size > 3 ? "…" : ""}` : "") + " · Enter: next";
    }
    this.redraw();
  }

  nextMatch() {
    if (!this.matches.length) return;
    this.cursor = (this.cursor + 1) % this.matches.length;
    const node = this.matches[this.cursor];
    if (node.asc !== MAIN_LAYER && node.asc !== this.ascLayerName) {
      if (this.ascSelect) { this.asc = node.asc; this.fillAscendancies(); this.ascSelect.value = node.asc; this.applyAscendancy(); }
      else return;
    }
    (node.asc === MAIN_LAYER ? this.main : this.ascView).centreOn(node, node.asc === MAIN_LAYER ? 0.12 : 0);
  }

  // Clicks and tooltips ----------------------------------------------------------------------------

  setTapMode(mode) {
    this.tapMode = mode;
    for (const b of this.modeButtons) b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
  }

  clicked(node, button, touch = false, view = this.main) {
    if (!this.options.interactive) return;
    this.lastView = view;
    const look = this.shown(node);
    if (!targetable(node)) {
      const same = this.data.nameCount.get(look.key) || 1;
      const what = node.type === NODE.NORMAL
        ? `Only notables and keystones can be required or banned: small passives share names (${look.name} appears ${same} times), so a name can't pick this one.`
        : `${NODE_KIND[node.type]}s can't be required or banned here.`;
      this.flash(what, view);
      return;
    }
    // A mouse's left click requires; a tap does what the Tap switch says (a touch screen has no right click)
    const mode = button === "secondary" ? "forbid" : touch ? this.tapMode : "require";
    this.options.onMark(look.name, mode, { classes: look.classes, node });
  }

  /** A note that stays until replaced (flash fades by itself). */
  setNote(message) {
    this.note.textContent = message;
  }

  /** A short message over the view that was clicked (or the last one clicked). */
  flash(message, view = this.lastView || this.main) {
    for (const v of [this.main, this.ascView]) v.toast.hidden = true;
    view.toast.textContent = message;
    view.toast.hidden = false;
    clearTimeout(this.noteTimer);
    this.noteTimer = setTimeout(() => { view.toast.hidden = true; }, 7000);
  }

  fillTip(tip, node) {
    const marks = this.options.marks();
    const look = this.shown(node);
    const state = marks.require.has(look.key) && targetable(node) ? "Required" : marks.forbid.has(look.key) && targetable(node) ? "Banned"
      : this.allocated?.has(node.id) ? "Allocated" : this.allocated ? "Not allocated" : "";
    const lines = [h("strong", {}, look.name || NODE_KIND[node.type]), h("div", { class: "tip-kind" }, NODE_KIND[node.type] + (node.asc ? ` · ${node.asc}` : "") + (state ? ` · ${state}` : ""))];
    if (look.stats.length) lines.push(h("ul", {}, look.stats.map((s) => h("li", {}, s))));
    for (const text of this.radiusNotes.get(node.id) || []) lines.push(h("div", { class: "tip-sub" }, text));
    if (node.views) {
      // The same spot is another passive for some classes
      const versions = [...node.views].map(([cls, v]) => h("li", { class: v === look ? "on" : "" },
        h("strong", {}, cls ? `${cls}: ` : "Every other class: "), v.name, v.stats.length ? h("span", { class: "tip-stats" }, ` · ${v.stats.join("; ")}`) : null));
      lines.push(h("div", { class: "tip-sub" }, "Differs by class"), h("ul", { class: "tip-versions" }, versions));
    }
    if (this.options.interactive) {
      lines.push(h("div", { class: "tip-hint" }, targetable(node) ? "Left click: require · Right click: ban · again to clear"
        : node.type === NODE.NORMAL ? "Small passives can't be required or banned by name" : ""));
    }
    tip.replaceChildren(...lines);
  }
}
