"use strict";

// Loaded after app.js. Adds, by wrapping app.js's own functions (so app.js itself is untouched):
//   - the passive tree beside the request form, kept in step with the Passives chips (tree.js);
//   - live edits: while a run is going, changes to the form reach the run a moment after you stop typing;
//   - a summary of the run's best build so far, under the progress;
//   - a read-only tree of the passives a result's build allocated.

function extend(name, wrapper) {
  const original = globalThis[name];
  if (typeof original !== "function") {
    console.error(`live.js: app.js has no ${name}() to extend`);
    return;
  }
  globalThis[name] = (...args) => wrapper(original, ...args);
}

const lowerSet = (names) => new Set((names || []).map((name) => name.toLowerCase()));

// The request tree ---------------------------------------------------------------------------------

let requestTree = null;
let treeInfo = null; // the tree data once loaded: which passives need which ascendancy or class
let loadingForm = false; // writeForm is filling the form: that isn't an edit
let lastClass = "";
let treeSignature = ""; // the class and ascendancy ticks the tree last followed
let noteTimer = null;
loadTreeData().then((data) => { treeInfo = data; }).catch(() => { /* the tree panel says so */ });

function passiveMarks() {
  return { require: lowerSet(choices.require.passives), forbid: lowerSet(choices.forbid.passives) };
}

// Class, ascendancies and passives must agree ---------------------------------------------------------

const classOf = (ascendancy) => Object.entries(options.classes).find(([, names]) => names.includes(ascendancy))?.[0];
const tickedAscendancies = () => [...$("ascendancies").querySelectorAll("input:checked")].map((box) => box.value);
const listOf = (names) => (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0]);

function setTicks(names) {
  for (const box of $("ascendancies").querySelectorAll("input")) box.checked = names.includes(box.value);
}

/** All the ascendancies on the form, grouped by class, whatever the class is: ticking one picks its class. */
globalThis.renderAscendancies = (selected = []) => {
  const container = $("ascendancies");
  container.replaceChildren();
  for (const [name, ascendancies] of Object.entries(options.classes)) {
    const group = el("div", { class: "class-group", "data-class": name }, el("span", { class: "class-name" }, name));
    for (const ascendancy of ascendancies) {
      group.append(el("label", {}, el("input", { type: "checkbox", value: ascendancy, checked: selected.includes(ascendancy) }), ascendancy));
    }
    container.append(group);
  }
  markClassGroup();
};

function markClassGroup() {
  const className = $("class_name").value;
  for (const group of $("ascendancies").querySelectorAll(".class-group")) group.classList.toggle("current", group.dataset.class === className);
}

/** What the form allows now: the classes, and the ascendancies (those sharing a start node, like Lich and Abyssal Lich, count as one). */
function selection() {
  const className = $("class_name").value;
  const ticked = tickedAscendancies();
  const classes = className ? [className] : ticked.length ? [...new Set(ticked.map(classOf))] : Object.keys(options.classes);
  const names = ticked.length ? ticked : className ? options.classes[className] : Object.values(options.classes).flat();
  const all = treeInfo?.ascendancies || [];
  const starts = new Map(all.map((a) => [a.name, a.start]));
  const ascendancies = new Set(names);
  for (const name of names) {
    for (const other of all) if (other.start === starts.get(name)) ascendancies.add(other.name);
  }
  return { className, ticked, classes: new Set(classes), ascendancies };
}

function whyInvalid(name, choice) {
  const rule = treeInfo.rules.get(name.toLowerCase());
  if (!rule) return null;
  if (rule.ascendancies && !rule.ascendancies.some((a) => choice.ascendancies.has(a))) {
    return `a ${[...new Set(rule.ascendancies)].join(" or ")} passive, and that ascendancy isn't allowed now`;
  }
  if (rule.classes && !rule.classes.some((c) => choice.classes.has(c))) {
    return `only ${listOf(rule.classes)} has it, and the class is ${listOf([...choice.classes])} now`;
  }
  return null;
}

/** Take required and banned passives out of the lists when the class or ascendancies no longer allow them. */
function dropInvalidPassives() {
  if (!treeInfo || !options) return [];
  const choice = selection();
  const removed = [];
  for (const mode of ["require", "forbid"]) {
    choices[mode].passives = choices[mode].passives.filter((name) => {
      const why = whyInvalid(name, choice);
      if (why) removed.push(`${name} (${mode === "require" ? "required" : "banned"}: ${why})`);
      return !why;
    });
  }
  return removed;
}

/** After the class or ascendancies changed: drop what no longer fits, and say what happened. */
function settle(notes) {
  const removed = dropInvalidPassives();
  if (removed.length) {
    renderChoices();
    notes.push(`Removed ${removed.join("; ")}.`);
  }
  markClassGroup();
  syncTreeSelection();
  showSelectionNote(notes.join(" "));
}

function showSelectionNote(text) {
  const note = $("selection-note");
  note.textContent = text;
  note.hidden = !text;
  clearTimeout(noteTimer);
  if (text) noteTimer = setTimeout(() => { note.hidden = true; }, 25000);
}

function classChanged() {
  const className = $("class_name").value;
  const notes = [];
  if (className) {
    const ticked = tickedAscendancies();
    const keep = ticked.filter((a) => options.classes[className].includes(a));
    if (keep.length !== ticked.length) {
      setTicks(keep);
      notes.push(`Unticked ${listOf(ticked.filter((a) => !keep.includes(a)))}: not ${className} ascendancies.`);
    }
  }
  settle(notes);
}

function ascendancyChanged() {
  const ticked = tickedAscendancies();
  const notes = [];
  if (ticked.length) {
    const classes = [...new Set(ticked.map(classOf))];
    const target = classes.length === 1 ? classes[0] : "";
    if ($("class_name").value !== target) {
      $("class_name").value = target;
      notes.push(target ? `Class set to ${target}, to match ${listOf(ticked)}.`
        : `${listOf(ticked)} belong to different classes (${listOf(classes)}), so the class is now Any.`);
    }
  }
  settle(notes);
}

/** Of the classes that have a version of a node, the one to switch to: one that starts where the current class does, else Any. */
function pickClass(classes, choice) {
  if (classes.length === 1) return classes[0];
  const here = treeInfo?.classes.get(choice.className);
  return classes.find((c) => treeInfo.classes.get(c) === here) || "";
}

/** Requiring or banning a version of a node that only some classes have: make the class one of them. */
function ensureClass(classes, name) {
  const choice = selection();
  const notes = [];
  if (classes.some((c) => choice.classes.has(c))) {
    if (!choice.className && !choice.ticked.length && classes.length === 1) {
      $("class_name").value = classes[0];
      notes.push(`Class set to ${classes[0]}: ${name} is the version of that node only ${classes[0]} gets.`);
    }
    return notes;
  }
  const target = pickClass(classes, choice);
  const ticked = tickedAscendancies();
  const keep = ticked.filter((a) => (target ? options.classes[target].includes(a) : classes.includes(classOf(a))));
  $("class_name").value = target;
  setTicks(keep);
  const others = Object.keys(options.classes).filter((c) => !classes.includes(c));
  notes.push(`Class ${target ? `changed to ${target}` : "set to Any"}`
    + (keep.length !== ticked.length ? `, unticked ${listOf(ticked.filter((a) => !keep.includes(a)))}` : "")
    + `: ${name} is the version of that node ${classes.length === 1 ? `only ${classes[0]} gets` : `every class but ${listOf(others)} has`}.`);
  return notes;
}

/** A click on the tree: toggle the passive in the Require or Ban list (the chips), never in both. */
function markPassive(name, mode, info = {}) {
  const same = (n) => n.toLowerCase() === name.toLowerCase();
  const other = mode === "require" ? "forbid" : "require";
  const present = choices[mode].passives.some(same);
  const notes = [];
  if (present) {
    choices[mode].passives = choices[mode].passives.filter((n) => !same(n));
  } else {
    choices[mode].passives = [...choices[mode].passives, name];
    choices[other].passives = choices[other].passives.filter((n) => !same(n));
    if (info.classes) notes.push(...ensureClass(info.classes, name));
  }
  renderChoices();
  const done = present ? `Cleared ${name}.` : mode === "require" ? `Required: ${name}.` : `Banned: ${name}.`;
  settle(notes);
  requestTree.flash([done, ...notes].join(" "));
}

function treeHelp() {
  const item = (...parts) => el("li", {}, ...parts);
  return el("ul", { class: "tree-help" },
    item("Scroll to zoom, drag to move, or search for a passive by name or effect (Enter jumps to the next match)."),
    item(el("span", { class: "req" }, "Left click"), " a notable or keystone to require it, ",
      el("span", { class: "ban" }, "right click"), " to ban it; click again to clear. On a touch screen use the Tap switch."),
    item("It edits the Passives lists under Requirements and bans, and follows them. Small passives share names, so only notables and keystones (ascendancy ones too) can be picked."),
    item("Nodes marked ⇄ are a different passive for some classes (Vile Wounds is Eagle Eye for a Huntress). Choose which class's version to show above; requiring or banning one sets the class to match."));
}

/** Class and ascendancy ticks on the form decide which ascendancy the tree's second view offers. */
function syncTreeSelection(focus = false) {
  if (!requestTree || !options) return;
  const className = $("class_name").value;
  const ticked = tickedAscendancies();
  const signature = `${className}|${ticked.join(",")}`;
  if (signature === treeSignature) return; // not a change of class or ascendancy: leave the views as the user has them
  treeSignature = signature;
  const names = className ? options.classes[className] || [] : Object.values(options.classes).flat();
  const current = requestTree.asc;
  const shown = ticked.includes(current) ? current : ticked[0] || (names.includes(current) ? current : "");
  requestTree.setAscendancyChoices(names, shown);
  requestTree.setClass(className, focus && className !== lastClass);
  lastClass = className;
}

function onFormEdited(event) {
  if (event?.type === "change") {
    const target = event.target;
    if (target.id === "class_name") {
      classChanged();
      syncTreeSelection(true);
    } else if (target.closest?.("#ascendancies")) {
      ascendancyChanged();
    }
  }
  syncTreeSelection();
  liveEdited();
}

extend("renderChoices", (original) => {
  original();
  requestTree?.refresh();
  if (!loadingForm) liveEdited();
});

extend("writeForm", (original, request) => {
  loadingForm = true;
  try {
    original(request);
  } finally {
    loadingForm = false;
  }
  lastClass = "";
  treeSignature = "";
  showSelectionNote("");
  markClassGroup();
  syncTreeSelection(true);
  if (liveRun && !adoptingRun) detachLive();
});

// Live edits ---------------------------------------------------------------------------------------

let liveRun = null; // {id, applied, tried, linked, busy}: the run the form is linked to
let liveTimer = null;
let adoptingRun = false;
const LIVE_DELAY_MS = 1500;
const snapshot = () => JSON.stringify(readForm());

const FIELD_NAMES = {
  require: "requirements", forbid: "bans", floors: "defence floors", main_skill: "main skill", class_name: "class",
  effort_minutes: "search time", dps_gate: "minimum DPS", seed_code: "PoB code", max_buttons: "max buttons",
  allow_exploits: "known exploits", weapon_unique: "weapon", weapon_type: "weapon type", weapon_swap: "weapon swap",
};

function showNotice(text) {
  const notice = $("run-notice");
  notice.textContent = text || "";
  notice.hidden = !text;
}

function liveEdited() {
  if (!liveRun || !liveRun.linked) return;
  clearTimeout(liveTimer);
  liveTimer = setTimeout(applyLive, LIVE_DELAY_MS);
}

async function applyLive() {
  const run = liveRun;
  if (!run || !run.linked || run.busy) return;
  const now = snapshot();
  if (now === run.applied) { // back to what the run has: an earlier error no longer applies
    if (run.showingError) { showIssues(null); run.showingError = false; }
    return;
  }
  run.busy = true;
  run.tried = now;
  try {
    const result = await api(`/api/runs/${run.id}/request`, { method: "POST", body: JSON.parse(now) });
    run.applied = now;
    if (run.showingError) { showIssues(null); run.showingError = false; }
    if (result.restarting) {
      showNotice("Request changed: finishing the search step in progress, then continuing from the best build so far "
        + "with the new settings…");
    } else if (result.extended) {
      showNotice("Search time raised: the search carries on from its best build when the current one ends, for the time added.");
    }
  } catch (error) {
    // A request with errors restarts nothing; the run goes on with the last request that was good
    run.showingError = true;
    showIssues([{ level: "error", field: "not applied to the running search", message: error.message }]);
  } finally {
    run.busy = false;
    if (liveRun === run && run.linked && snapshot() !== run.tried) liveEdited();
  }
}

function startLive(runId) {
  clearTimeout(liveTimer);
  liveRun = { id: runId, applied: snapshot(), tried: snapshot(), linked: true, busy: false, showingError: false };
  showNotice("");
  $("run-synopsis").dataset.key = "";
}

function detachLive() {
  liveRun.linked = false;
  clearTimeout(liveTimer);
  showNotice("The form now shows a different request than the running search, so edits no longer change it. Stop the run "
    + "(it keeps its best build) and press Optimize to run this one.");
}

function endLive() {
  liveRun = null;
  clearTimeout(liveTimer);
  showNotice("");
  $("run-synopsis").hidden = true;
}

/** After a page reload during a run: the form shows the request the run is on. */
async function adoptRunningRequest() {
  const run = await api("/api/runs/current").catch(() => ({}));
  if (!run.id || run.status !== "running") return;
  adoptingRun = true;
  try {
    writeForm(run.request);
    currentId = run.request_id;
    refreshSaved();
  } finally {
    adoptingRun = false;
  }
}

extend("attachToRun", async (original) => {
  await adoptRunningRequest();
  return original();
});

extend("pollRun", (original, runId, since = 0) => {
  if (since === 0 && liveRun?.id !== runId) startLive(runId);
  return original(runId, since);
});

extend("showRun", (original, run, events) => {
  original(run, events);
  for (const event of events) {
    if (event.kind === "restart") {
      const fields = (event.changed || []).map((key) => FIELD_NAMES[key] || key.replaceAll("_", " "));
      showNotice(event.message + (fields.length ? ` (changed: ${fields.join(", ")})` : "") + ".");
    }
  }
  if (run.status === "running") renderSynopsis(run.synopsis, run);
  else endLive();
});

// The best build so far -----------------------------------------------------------------------------

function renderSynopsis(s, run) {
  const box = $("run-synopsis");
  box.hidden = false;
  const key = JSON.stringify([s, run.request?.objective]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  if (!s) {
    box.replaceChildren(el("div", { class: "synopsis-head" }, el("h3", {}, "Best pick so far")),
      el("p", { class: "hint" }, "Appears when the first search round ends."));
    return;
  }
  const n = (value) => (typeof value === "number" ? formatNumber(value) : "–");
  const stats = s.stats || {};
  const rows = [];
  const row = (label, value, cls = "") => value ? rows.push(el("dt", {}, label), el("dd", { class: cls }, value)) : null;
  const build = [s.mainSkill || s.skeleton, s.ascendClassName || s.className, s.level ? `level ${s.level}` : null].filter(Boolean).join(" · ");
  row("Build", build, "big");
  const clear = run.request?.objective !== "boss" && typeof s.clear === "number" ? ` · clear ~${formatNumber(s.clear)} (approximate)` : "";
  row("DPS", `${n(s.dps)}${clear}`, "big");
  if (s.stats) {
    row("Defences", [`Life ${n(stats.Life)}`, `Energy shield ${n(stats.EnergyShield)}`, `Mana ${n(stats.Mana)}`,
      stats.Ward ? `Ward ${n(stats.Ward)}` : null, `Spare spirit ${n(stats.SpiritUnreserved)}`].filter(Boolean).join(" · "));
    row("Resistances", `Fire ${n(stats.FireResist)} · Cold ${n(stats.ColdResist)} · Lightning ${n(stats.LightningResist)} · Chaos ${n(stats.ChaosResist)}`);
    row("Supports", s.supports?.length ? s.supports.join(", ") : "none");
    row("Key items", keyItems(s.items));
    row("Passives", `${s.allocated} + ${s.ascendancyAllocated} ascendancy`);
  } else if (s.error) {
    row("Details", `PoB couldn't read this build's numbers (${s.error})`);
  }
  if (s.deficits?.length) row("Still short of", s.deficits.join(", "), "short");
  const when = Number.isFinite(s.t) ? `found at ${Math.round(s.t)}s` : "";
  box.replaceChildren(
    el("div", { class: "synopsis-head" }, el("h3", {}, "Best pick so far"),
      el("span", { class: "hint" }, `${when} · changes when a search round ends; the line above is the live leader`)),
    el("dl", {}, rows));
}

/** Weapon, then the other uniques, then how many rares. */
function keyItems(items) {
  const gear = (items || []).filter((i) => !/^(Flask|Charm)/.test(i.slot));
  if (!gear.length) return "none";
  const unique = (i) => /unique/i.test(i.rarity || "");
  const weapons = gear.filter((i) => /^Weapon [12]$/.test(i.slot));
  const parts = weapons.map((i) => (unique(i) ? i.name : `${i.base} (rare)`));
  const others = gear.filter((i) => unique(i) && !weapons.includes(i)).map((i) => `${i.name} (${i.slot})`);
  const rares = gear.filter((i) => !unique(i) && !weapons.includes(i) && /rare/i.test(i.rarity || "")).length;
  return [...parts, ...others, rares ? `${rares} rare item${rares === 1 ? "" : "s"}` : null].filter(Boolean).join(" · ") || "none";
}

// A result's tree -------------------------------------------------------------------------------------

let resultTree = null;

extend("renderResult", (original, result) => {
  resultTree?.destroy();
  resultTree = null;
  original(result);
  if (result.code && result.summary) addResultTree(result);
});

async function addResultTree(result) {
  const summary = result.summary;
  const root = el("div", { class: "result-tree" });
  const section = el("details", { class: "result-section result-tree-section", open: true },
    el("summary", {}, `Passive tree · ${summary.allocated} passives + ${summary.ascendancyAllocated} ascendancy`), root);
  const anchor = $("run-result").querySelector(".code-row")?.closest(".result-section");
  if (anchor) anchor.after(section); else $("run-result").append(section);
  const request = result.request || {};
  const tree = new PassiveTree(root, {
    interactive: false,
    marks: () => ({ require: lowerSet(request.require?.passives), forbid: lowerSet(request.forbid?.passives) }),
    help: el("ul", { class: "tree-help" },
      el("li", {}, "Orange: the passives this build allocates. Scroll to zoom, drag to move, hover for what a passive does."),
      el("li", {}, el("span", { class: "req" }, "Green"), " rings are passives you required, ", el("span", { class: "ban" }, "red"), " ones you banned."),
      el("li", {}, "Gold circles: a socketed jewel's radius; gold rings mark the passives in it, dashed ones the passives taken without a connection through it. A dashed circle is a radius the custom model enlarges (Baryanic Leylines*).")),
  });
  resultTree = tree;
  tree.setClass(summary.className);
  tree.setNote("Reading the build's passives…");
  try {
    const allocation = await api("/api/tree/allocated", { method: "POST", body: { code: result.code } });
    if (resultTree !== tree) return; // another result was drawn meanwhile
    tree.setAllocated(allocation.ids, allocation.radiusJewels || []);
    tree.setAscendancy(allocation.ascendClassName || summary.ascendClassName);
    tree.setNote("");
  } catch (error) {
    if (resultTree === tree) tree.setNote(`Couldn't read the build's passives: ${error.message}`);
  }
}

// Start -----------------------------------------------------------------------------------------------

requestTree = new PassiveTree($("request-tree"), {
  interactive: true, ascendancySelect: true, marks: passiveMarks, onMark: markPassive, help: treeHelp(),
});
$("request-form").addEventListener("input", onFormEdited);
$("request-form").addEventListener("change", onFormEdited);
