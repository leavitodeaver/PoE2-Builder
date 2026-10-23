"use strict";

const CATEGORIES = [
  ["skills", "Skills"], ["supports", "Supports"], ["passives", "Passives"], ["uniques", "Uniques"],
  ["item_classes", "Item classes"], ["augments", "Augments"], ["mechanics", "Mechanics"],
];
const FLOORS = ["life", "energy_shield", "mana", "block", "evade", "spare_spirit"];

let options = null;
let currentId = null;
let choices = emptyChoices();

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const LABELS = {
  armour_evasion: "Armour + evasion", armour_energy_shield: "Armour + energy shield",
  evasion_energy_shield: "Evasion + energy shield", hybrid: "Armour + evasion + energy shield",
  weapon: "Weapon (dual wield)", boss: "Boss DPS", mapping: "Mapping (clear)",
};

function pretty(value) {
  if (LABELS[value]) return LABELS[value];
  const words = String(value).replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function emptyChoices() {
  const make = () => Object.fromEntries(CATEGORIES.map(([key]) => [key, []]));
  return { require: make(), forbid: make() };
}

async function api(path, init = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...init,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const message = Array.isArray(detail) ? detail.map((d) => `${d.field}: ${d.message}`).join("; ") : detail || response.statusText;
    throw new Error(message);
  }
  return body;
}

// Setup ------------------------------------------------------------------------------------

function fillSelect(select, values, anyLabel) {
  select.replaceChildren();
  if (anyLabel) select.append(el("option", { value: "" }, anyLabel));
  for (const value of values) select.append(el("option", { value }, pretty(value)));
}

function buildDatalists() {
  const holder = $("datalists");
  for (const [key, list] of Object.entries(options.lists)) {
    holder.append(el("datalist", { id: `list-${key}` }, list.map((name) => el("option", { value: name }))));
  }
}

function renderAscendancies(selected = []) {
  const container = $("ascendancies");
  container.replaceChildren();
  const className = $("class_name").value;
  const classes = className ? { [className]: options.classes[className] } : options.classes;
  for (const [name, ascendancies] of Object.entries(classes)) {
    const group = el("div", { class: "class-group" });
    if (!className) group.append(el("span", { class: "class-name" }, name));
    for (const ascendancy of ascendancies) {
      group.append(el("label", {}, el("input", { type: "checkbox", value: ascendancy, checked: selected.includes(ascendancy) }), ascendancy));
    }
    container.append(group);
  }
}

function renderChoices() {
  const body = $("choices");
  body.replaceChildren();
  for (const [key, label] of CATEGORIES) {
    const row = el("tr", {}, el("td", {}, label));
    for (const mode of ["require", "forbid"]) {
      const cell = el("div", { class: "chip-cell" });
      for (const name of choices[mode][key]) {
        cell.append(el("span", { class: `chip ${mode}` }, name,
          el("button", { type: "button", "aria-label": `Remove ${name}`, onclick: () => {
            choices[mode][key] = choices[mode][key].filter((n) => n !== name);
            renderChoices();
          } }, "×")));
      }
      const input = el("input", { list: `list-${key}`, placeholder: mode === "require" ? "Add requirement" : "Add ban",
        "aria-label": `${mode === "require" ? "Require" : "Ban"} ${label.toLowerCase()}` });
      const add = () => {
        const name = input.value.trim();
        if (name && !choices[mode][key].includes(name)) {
          choices[mode][key].push(name);
          renderChoices();
          body.querySelector(`input[aria-label="${input.getAttribute("aria-label")}"]`).focus();
        }
      };
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); add(); } });
      input.addEventListener("change", () => { if (options.lists[key].includes(input.value)) add(); });
      cell.append(input);
      row.append(el("td", {}, cell));
    }
    body.append(row);
  }
}

function renderDamageInputs() {
  const make = (prefix) => options.enums.damage.map((type) =>
    el("label", {}, pretty(type), el("input", { id: `${prefix}-${type}`, type: "number", placeholder: "None" })));
  $("resists").replaceChildren(...make("resist"));
  $("max-hit").replaceChildren(...make("maxhit"));
}

// Form <-> request ---------------------------------------------------------------------------

const num = (id) => ($(id).value === "" ? null : Number($(id).value));
const text = (id) => ($(id).value.trim() === "" ? null : $(id).value.trim());

function readForm() {
  const floors = { resists: {}, max_hit: {} };
  for (const key of FLOORS) floors[key] = num(`floor-${key}`);
  for (const type of options.enums.damage) {
    if (num(`resist-${type}`) != null) floors.resists[type] = num(`resist-${type}`);
    if (num(`maxhit-${type}`) != null) floors.max_hit[type] = num(`maxhit-${type}`);
  }
  return {
    name: $("name").value.trim() || "Untitled request",
    notes: $("notes").value,
    league: text("league"),
    hardcore: $("hardcore").checked,
    level: num("level") ?? 95,
    class_name: text("class_name"),
    ascendancies: [...$("ascendancies").querySelectorAll("input:checked")].map((box) => box.value),
    main_skill: text("main_skill"),
    max_buttons: num("max_buttons"),
    require: choices.require,
    forbid: choices.forbid,
    weapon_type: text("weapon_type"),
    weapon_unique: text("weapon_unique"),
    offhand: $("offhand").value,
    weapon_swap: $("weapon_swap").value,
    defence: $("defence").value,
    floors,
    budget: { divines: num("divines"), gear_tier: $("gear_tier").value, ssf: $("ssf").checked },
    objective: $("objective").value,
    dps_gate: num("dps_gate"),
    allow_exploits: $("allow_exploits").checked,
    seed_code: text("seed_code"),
    effort_minutes: num("effort_minutes") ?? 10,
  };
}

function writeForm(request) {
  const set = (id, value) => { $(id).value = value ?? ""; };
  set("name", request.name);
  set("notes", request.notes);
  set("league", request.league);
  $("hardcore").checked = request.hardcore;
  set("level", request.level);
  set("class_name", request.class_name);
  renderAscendancies(request.ascendancies);
  set("main_skill", request.main_skill);
  set("max_buttons", request.max_buttons);
  choices = emptyChoices();
  for (const mode of ["require", "forbid"]) {
    for (const [key] of CATEGORIES) choices[mode][key] = [...(request[mode]?.[key] ?? [])];
  }
  renderChoices();
  set("weapon_type", request.weapon_type);
  set("weapon_unique", request.weapon_unique);
  set("offhand", request.offhand);
  set("weapon_swap", request.weapon_swap);
  set("defence", request.defence);
  for (const key of FLOORS) set(`floor-${key}`, request.floors[key]);
  for (const type of options.enums.damage) {
    set(`resist-${type}`, request.floors.resists[type]);
    set(`maxhit-${type}`, request.floors.max_hit[type]);
  }
  set("divines", request.budget.divines);
  set("gear_tier", request.budget.gear_tier);
  $("ssf").checked = request.budget.ssf;
  set("objective", request.objective);
  set("dps_gate", request.dps_gate);
  $("allow_exploits").checked = request.allow_exploits;
  set("seed_code", request.seed_code);
  set("effort_minutes", request.effort_minutes);
  showIssues(null);
}

function showIssues(issues, savedMessage) {
  const list = $("issues");
  if (issues === null) { list.hidden = true; return; }
  list.replaceChildren();
  if (savedMessage) list.append(el("li", { class: "ok" }, savedMessage));
  if (!issues.length) list.append(el("li", { class: "ok" }, "Every name and setting checks out."));
  for (const issue of issues) list.append(el("li", { class: issue.level }, `${issue.field}: ${issue.message}`));
  list.hidden = false;
}

// Saved requests -----------------------------------------------------------------------------

async function refreshSaved() {
  const saved = await api("/api/requests");
  const list = $("saved-list");
  list.replaceChildren();
  if (!saved.length) list.append(el("li", { class: "empty" }, "Nothing saved yet."));
  for (const entry of saved) {
    list.append(el("li", { class: entry.id === currentId ? "active" : "" },
      el("button", { type: "button", class: "open", onclick: () => openRequest(entry.id) }, entry.name),
      el("button", { type: "button", class: "icon", "aria-label": `Delete ${entry.name}`, onclick: () => deleteRequest(entry) }, "×")));
  }
}

async function openRequest(id) {
  writeForm(await api(`/api/requests/${id}`));
  currentId = id;
  refreshSaved();
}

async function deleteRequest(entry) {
  if (!confirm(`Delete "${entry.name}"?`)) return;
  await api(`/api/requests/${entry.id}`, { method: "DELETE" });
  if (entry.id === currentId) newRequest();
  refreshSaved();
}

function newRequest() {
  currentId = null;
  writeForm(structuredClone(options.defaults));
  refreshSaved();
}

async function save() {
  try {
    const result = await api("/api/requests", { method: "POST", body: { id: currentId, request: readForm() } });
    currentId = result.id;
    showIssues(result.issues, "Saved.");
    refreshSaved();
  } catch (error) {
    showIssues([{ level: "error", field: "request", message: error.message }]);
  }
}

async function validate() {
  try {
    showIssues((await api("/api/validate", { method: "POST", body: readForm() })).issues);
  } catch (error) {
    showIssues([{ level: "error", field: "request", message: error.message }]);
  }
}

// Build check ------------------------------------------------------------------------------

function formatNumber(value) {
  if (value === "inf") return "∞";
  if (typeof value !== "number") return "–";
  return Math.abs(value) >= 1000 ? Math.round(value).toLocaleString() : String(Math.round(value * 10) / 10);
}

function statTiles(stats, dps) {
  const tiles = [
    ["DPS (PoB)", dps], ["Life", stats.Life], ["Energy shield", stats.EnergyShield], ["Mana", stats.Mana],
    ["Phys max hit", stats.PhysicalMaximumHitTaken], ["Fire/Cold/Light res", null], ["Chaos res", stats.ChaosResist],
    ["Spare spirit", stats.SpiritUnreserved],
  ];
  return el("div", { class: "stats" }, tiles.map(([label, value]) => el("div", { class: "stat" },
    el("div", { class: "k" }, label),
    el("div", { class: "v" }, label.startsWith("Fire/")
      ? `${formatNumber(stats.FireResist)} / ${formatNumber(stats.ColdResist)} / ${formatNumber(stats.LightningResist)}`
      : formatNumber(value)))));
}

function checksTable(checks) {
  const mark = (ok) => (ok === null ? ["pending", "–"] : ok ? ["ok", "✓"] : ["bad", "✗"]);
  return [el("h3", {}, "Requested vs delivered"), el("div", { class: "table-wrap" }, el("table", { class: "checks" },
    el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "Requirement"), el("th", {}, "Requested"), el("th", {}, "Delivered"))),
    el("tbody", {}, checks.map((check) => {
      const [cls, symbol] = mark(check.ok);
      return el("tr", {}, el("td", { class: `status ${cls}` }, symbol), el("td", {}, check.label,
        check.note ? el("div", { class: "note" }, check.note) : null), el("td", {}, check.requested), el("td", {}, check.delivered));
    }))))];
}

function simpleTable(headers, rows) {
  const isNum = (h) => typeof h === "object" && h.num;
  return el("div", { class: "table-wrap" }, el("table", { class: "checks" },
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { class: isNum(h) ? "num" : "" }, h.label ?? h)))),
    el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell, i) => el("td", { class: isNum(headers[i]) ? "num" : "" }, cell)))))));
}

async function inspect() {
  const output = $("inspect-result");
  const code = $("inspect-code").value.trim();
  if (!code) return;
  output.replaceChildren(el("p", { class: "hint" }, "Calculating… (the first build takes a few seconds while PoB starts)"));
  let result;
  try {
    result = await api("/api/inspect", { method: "POST", body: { code, request: readForm() } });
  } catch (error) {
    output.replaceChildren(el("p", { class: "error-text" }, error.message));
    return;
  }
  const s = result.summary;
  const stats = result.stats;
  const dps = Math.max(stats.FullDPS || 0, stats.CombinedDPS || 0, stats["Minion.CombinedDPS"] || 0);
  const parts = [
    el("p", { class: "result-summary" },
      el("strong", {}, `${s.ascendClassName || s.className}, level ${s.level}`),
      ` · main skill ${result.mainSkill || "none"} · ${result.buttons.length} button${result.buttons.length === 1 ? "" : "s"}`
      + ` (${result.buttons.join(", ") || "none"}) · ${s.allocated} passives + ${s.ascendancyAllocated} ascendancy`),
    statTiles(stats, dps),
  ];
  if (result.warnings.length) parts.push(el("p", { class: "warnings" }, "PoB warnings: " + result.warnings.join("; ")));
  if (result.issues?.some((issue) => issue.level === "error")) {
    parts.push(el("p", { class: "error-text" }, "The request has errors (see Check request), so some rows may be missing."));
  }
  if (result.checks) parts.push(...checksTable(result.checks));
  output.replaceChildren(...parts);
}

// Optimizer --------------------------------------------------------------------------------

let runPoll = null;

function logLine(event) {
  const t = `${Math.round(event.t)}s`;
  let text;
  if (event.kind === "best") {
    text = `Best so far: ${event.skeleton}, ${formatNumber(event.dps)} DPS`
      + (event.deficits.length ? ` (short: ${event.deficits.join(", ")})` : "");
  } else if (event.kind === "probe") {
    text = `Probe: ${event.skeleton}, ${formatNumber(event.dps)} DPS`;
  } else if (event.kind === "skeletons") {
    text = `${event.count} skeleton${event.count === 1 ? "" : "s"} to search`
      + (event.problems.length ? `; dropped: ${event.problems.join("; ")}` : "");
  } else {
    text = event.message ?? event.kind;
  }
  return el("li", {}, el("span", { class: "t" }, t), text);
}

function showRun(run, events) {
  $("run-progress").hidden = false;
  const log = $("run-log");
  for (const event of events) {
    if (event.kind === "progress") {
      $("run-status").textContent = `${formatNumber(event.evals_per_second)} evaluations/s across ${event.tasks} searches · `
        + `${formatNumber(event.evals)} so far · leading: ${event.leader} at ${formatNumber(event.leader_dps)} DPS (${event.phase})`;
      continue;
    }
    log.append(logLine(event));
    log.scrollTop = log.scrollHeight;
  }
  const elapsed = Date.now() / 1000 - run.started;
  $("run-bar").style.width = `${Math.min(100, (100 * elapsed) / Math.max(run.seconds, 1))}%`;
}

async function pollRun(runId, since = 0) {
  clearTimeout(runPoll);
  let run;
  try {
    run = await api(`/api/runs/${runId}?since=${since}`);
  } catch (error) {
    $("run-status").textContent = `Lost the run: ${error.message}`;
    return;
  }
  showRun(run, run.events);
  if (run.status === "running") {
    $("run-cancel").hidden = false;
    $("optimize").disabled = true;
    runPoll = setTimeout(() => pollRun(runId, run.next), 1000);
    return;
  }
  $("run-cancel").hidden = true;
  $("optimize").disabled = false;
  $("run-bar").style.width = "100%";
  if (run.status === "error") {
    $("run-result").replaceChildren(el("p", { class: "error-text" }, "The run failed:"), el("pre", {}, run.error));
  } else if (run.result) {
    renderResult(run.result);
  }
}

async function optimize() {
  const request = readForm();
  try {
    const run = await api("/api/runs", { method: "POST", body: { request, id: currentId } });
    $("run-log").replaceChildren();
    $("run-status").textContent = "Starting…";
    $("run-result").replaceChildren();
    pollRun(run.id);
  } catch (error) {
    showIssues([{ level: "error", field: "optimize", message: error.message }]);
  }
}

async function cancelRun() {
  const run = await api("/api/runs/current");
  if (run.id) await api(`/api/runs/${run.id}/cancel`, { method: "POST" });
  $("run-status").textContent = "Stopping: finishing the best build found so far…";
}

function formatPrice(price) {
  if (price == null) return "–";
  return price < 1 ? price.toFixed(3) : price.toFixed(1);
}

function renderResult(result) {
  const parts = [];
  if (result.feasible) {
    parts.push(el("div", { class: "verdict ok" }, "Every requirement is met."));
  } else {
    const heading = result.status === "invalid" ? "The request has errors:" : "Infeasible: no build met every requirement.";
    parts.push(el("div", { class: "verdict bad" }, heading, el("ul", {},
      (result.reasons || []).map((r) => el("li", {}, r)),
      (result.relaxations || []).map((r) => el("li", {}, `Cheapest relaxation: ${r}`)))));
  }
  if (result.summary) {
    const s = result.summary;
    parts.push(el("p", { class: "result-summary" }, el("strong", {}, `${s.ascendClassName}, level ${s.level}`),
      ` · ${result.label} · ${s.allocated} passives + ${s.ascendancyAllocated} ascendancy`
      + ` · Full DPS ${formatNumber(result.dps.full)} (main skill ${formatNumber(result.dps.main)})`));
    parts.push(statTiles(result.stats, result.dps.full));
    parts.push(...checksTable(result.checks));
    const copy = el("button", {
      type: "button",
      onclick: async (event) => {
        await navigator.clipboard.writeText(result.code);
        event.target.textContent = "Copied";
      },
    }, "Copy");
    const badge = result.verified
      ? el("span", { class: "badge" }, "verified: reloads to the same numbers")
      : el("span", { class: "badge bad" }, "did not reload identically");
    parts.push(el("div", { class: "result-section" }, el("h3", {}, "PoB code", badge),
      el("div", { class: "code-row" }, el("textarea", { rows: 3, readonly: true }, result.code), copy)));
    const gear = Object.entries(result.gear).sort();
    parts.push(el("div", { class: "result-section" }, el("h3", {}, `Gear · ${result.spent.toFixed(2)} divine`),
      simpleTable(["Slot", "Item", "Base", { label: "Price (div)", num: true }, "Confidence"],
        gear.map(([slot, g]) => [slot, g.unique ? g.name : `${g.name} (normal)`, g.base, formatPrice(g.price), g.confidence]))));
    parts.push(el("div", { class: "result-section" }, el("h3", {}, "Skills"),
      el("ul", { class: "gem-groups" }, result.groups.filter((g) => g.enabled).map((g) => {
        const names = g.gems.map((x) => x.name).join(" + ");
        return el("li", {}, g.main ? el("strong", {}, names) : names,
          g.fromItem ? " (from an item)" : "", g.fullDPS && !g.main ? " (counted in Full DPS)" : "");
      }))));
    if (result.conditions.length) {
      parts.push(el("div", { class: "result-section" }, el("h3", {}, "Conditions turned on (the build sustains them)"),
        simpleTable(["Condition", "Evidence"], result.conditions.map((c) => [
          c.label + (c.value === true ? "" : ` = ${formatNumber(c.value)}`), c.evidence]))));
    }
    const quests = Object.entries(result.quests || {}).filter(([, v]) => v !== "None");
    if (quests.length) {
      parts.push(el("div", { class: "result-section" }, el("h3", {}, "Campaign reward choices"),
        simpleTable(["Quest", "Choice"], quests.map(([q, v]) => [q, v.replaceAll("\n\t", "; ")]))));
    }
  }
  if (result.probes?.length) {
    parts.push(el("div", { class: "result-section" }, el("h3", {}, "Skeletons searched"),
      simpleTable(["Skeleton", { label: "DPS", num: true }, "Short of"],
        result.probes.map((p) => [p.skeleton, formatNumber(p.dps), p.deficits.join(", ") || "–"]))));
  }
  for (const note of result.notes || []) parts.push(el("p", { class: "hint" }, note));
  parts.push(el("p", { class: "hint" }, `${formatNumber(result.evaluations)} PoB evaluations in ${result.seconds}s `
    + `(${formatNumber(result.evals_per_second)}/s on ${result.workers} workers)`
    + (result.saved_as ? ` · saved as ${result.saved_as}` : "")));
  $("run-result").replaceChildren(...parts);
}

async function attachToRun() {
  const run = await api("/api/runs/current").catch(() => ({}));
  if (run.id) {
    pollRun(run.id);
    return;
  }
  // No run since the page server started: show the last saved result, if any
  const saved = await api("/api/results").catch(() => []);
  if (saved.length) {
    const result = await api(`/api/results/${encodeURIComponent(saved[0].name)}`).catch(() => null);
    if (result) {
      $("run-hint").textContent = `Showing the last saved result (${saved[0].name}). Optimize runs a new search.`;
      renderResult(result);
    }
  }
}

// Prices -----------------------------------------------------------------------------------

function describePrices(status) {
  const minutes = Math.round((Date.now() / 1000 - status.fetched_at) / 60);
  const age = minutes < 1 ? "just now" : minutes < 120 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
  return `prices: ${status.league}, ${status.exchange + status.uniques} items, updated ${age}`;
}

async function loadPrices(refresh = false) {
  const league = $("league").value || "";
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  try {
    const status = refresh
      ? await api(`/api/prices/refresh${query}`, { method: "POST" })
      : await api(`/api/prices${query}`);
    $("price-status").textContent = describePrices(status);
  } catch (error) {
    $("price-status").textContent = `prices: unavailable (${error.message})`;
  }
}

// Start ------------------------------------------------------------------------------------

async function start() {
  options = await api("/api/options");
  const pob = options.data.pob;
  $("data-version").textContent = `PoB ${pob.branch} ${pob.commit.slice(0, 7)} (${pob.commit_date})`;
  buildDatalists();
  fillSelect($("class_name"), Object.keys(options.classes), "Any class");
  fillSelect($("league"), options.leagues, `Current (${options.default_league ?? "unknown"})`);
  fillSelect($("weapon_type"), options.weapon_types, "Any");
  for (const [id, values] of [["offhand", options.enums.offhand], ["weapon_swap", options.enums.weapon_swap],
    ["defence", options.enums.defence], ["gear_tier", options.enums.gear_tier], ["objective", options.enums.objective]]) {
    fillSelect($(id), values);
  }
  renderDamageInputs();
  $("class_name").addEventListener("change", () => renderAscendancies(readForm().ascendancies));
  $("league").addEventListener("change", () => loadPrices());
  $("new-request").addEventListener("click", newRequest);
  $("save").addEventListener("click", save);
  $("validate").addEventListener("click", validate);
  $("inspect").addEventListener("click", inspect);
  $("optimize").addEventListener("click", optimize);
  $("run-cancel").addEventListener("click", cancelRun);
  $("refresh-prices").addEventListener("click", () => loadPrices(true));
  newRequest();
  loadPrices();
  attachToRun();
}

start().catch((error) => {
  document.querySelector("main").prepend(el("p", { class: "card error-text" }, `Couldn't start: ${error.message}`));
});
