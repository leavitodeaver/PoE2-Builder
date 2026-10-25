"use strict";

const CATEGORIES = [
  ["skills", "Skills"], ["supports", "Supports"], ["passives", "Passives"], ["uniques", "Uniques"],
  ["item_classes", "Item classes"], ["augments", "Augments"], ["mechanics", "Mechanics"],
];
const FLOORS = ["life", "energy_shield", "mana", "ward", "block", "evade", "spare_spirit"];

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
  weapon: "Weapon (dual wield)", boss: "Boss DPS", balanced: "Balanced (boss + clear)", mapping: "Mapping (clear)",
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

function statTiles(stats, dps, clear) {
  const tiles = [
    ["DPS (PoB)", dps], ...(clear === undefined ? [] : [["Clear score (approx.)", clear]]), ["Life", stats.Life], ["Energy shield", stats.EnergyShield], ["Mana", stats.Mana],
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

// Clear speed is an estimate (PLAN.md §5.5), so it is always shown as approximate
function clearText(clear) {
  return typeof clear === "number" ? `, clear ~${formatNumber(clear)}` : "";
}

function logLine(event) {
  const t = `${Math.round(event.t)}s`;
  let text;
  if (event.kind === "best") {
    text = `Best so far: ${event.skeleton}, ${formatNumber(event.dps)} DPS${clearText(event.clear)}`
      + (event.deficits.length ? ` (short: ${event.deficits.join(", ")})` : "");
  } else if (event.kind === "probe") {
    text = `Probe: ${event.skeleton}, ${formatNumber(event.dps)} DPS${clearText(event.clear)}`;
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
    $("run-hint").textContent = "Searching. Progress streams below, and the result replaces it when the search ends.";
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
      + ` · Full DPS ${formatNumber(result.dps.full)} (main skill ${formatNumber(result.dps.main)})`
      + (result.clear ? ` · clear score ~${formatNumber(result.clear.score)} (approximate: `
        + `${formatNumber(result.clear.targets)} targets per use)` : "")
      + (result.objective ? ` · goal: ${pretty(result.objective.goal)}` : "")));
    parts.push(statTiles(result.stats, result.dps.full, result.clear?.score));
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
    if (result.shopping) {
      parts.push(shoppingSection(result.shopping));
    } else {
      const gear = Object.entries(result.gear).sort();
      parts.push(el("div", { class: "result-section" }, el("h3", {}, `Gear · ${result.spent.toFixed(2)} divine`),
        simpleTable(["Slot", "Item", "Base", { label: "Price (div)", num: true }, "Confidence"],
          gear.map(([slot, g]) => [slot,
            el("div", {}, g.unique || g.rare ? g.name : `${g.name} (normal)`,
              g.affixes?.length ? el("div", { class: "note" }, g.affixes.join(" · ")) : null,
              g.runes?.length ? el("div", { class: "note" }, `Sockets: ${g.runes.join(", ")}`) : null),
            g.base, formatPrice(g.price), g.confidence]))));
    }
    parts.push(el("div", { class: "result-section" }, el("h3", {}, "Skills"),
      el("ul", { class: "gem-groups" }, result.groups.filter((g) => g.enabled).map((g) => {
        const names = g.gems.map((x) => x.name).join(" + ");
        return el("li", {}, g.main ? el("strong", {}, names) : names,
          g.fromItem ? " (from an item)" : "", g.fullDPS && !g.main ? " (counted in Full DPS)" : "",
          g.set1 === false ? " (weapon set 2)" : "");
      }))));
    if (result.checklist) parts.push(checklistSection(result));
    if (result.assumptions?.length) {
      parts.push(el("div", { class: "result-section" }, el("h3", {}, "Assumptions * (PoB doesn't model these)"),
        simpleTable(["Assumption", { label: "Value", num: true }, "Basis", "Confidence"],
          result.assumptions.map((a) => [`${a.label} *`, formatNumber(a.value), a.basis, a.confidence]))));
    }
    if (result.blind_spots?.length) {
      parts.push(el("div", { class: "result-section" },
        el("h3", {}, `PoB ignores ${result.blind_spots.length} line${result.blind_spots.length === 1 ? "" : "s"} on this build`),
        el("p", { class: "hint" }, "Path of Building can't calculate these, so the numbers above leave them out "
          + "(they may help or hurt more than shown)."),
        simpleTable(["Where", "Line", "Correction"], result.blind_spots.map((b) => [b.source, b.line,
          !b.corrected_by ? "not modeled" : b.as.length ? `* counted as: ${b.as.join("; ")} (${b.confidence})`
            : "no effect on the DPS or defences calculated here"]))));
    }
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
      simpleTable(["Skeleton", { label: "DPS", num: true }, { label: "Clear (approx.)", num: true }, "Short of"],
        result.probes.map((p) => [p.skeleton, formatNumber(p.dps), formatNumber(p.clear), p.deficits.join(", ") || "–"]))));
  }
  for (const note of result.notes || []) parts.push(el("p", { class: "hint" }, note));
  parts.push(el("p", { class: "hint" }, `${formatNumber(result.evaluations)} PoB evaluations in ${result.seconds}s `
    + `(${formatNumber(result.evals_per_second)}/s on ${result.workers} workers)`
    + (result.saved_as ? ` · saved as ${result.saved_as}` : "")));
  $("run-result").replaceChildren(...parts);
}

// Shopping list and trade links ------------------------------------------------------------

function craftBlock(shopping, item) {
  const c = item.craft;
  const basis = item.basis === "crafting" ? "the crafting estimate (jewel market data is thin)" : "the market model (cheaper)";
  return el("div", { class: "craft" },
    el("div", {}, el("strong", {}, "Crafting it: "),
      `about ${formatPrice(c.total)} div expected (lucky ~${formatPrice(c.low)}, unlucky ~${formatPrice(c.high)})`),
    el("ul", { class: "craft-steps" },
      el("li", {}, `Buy: a jewel (${item.base}) with ${c.base.mods.join(" + ")} (any roll within the tier): `
        + `~${formatPrice(c.base.price)} div `, tradeButton(shopping, item, "base")),
      c.steps.map((step) => el("li", {}, `${step.count} × ${step.item} (${formatPrice(step.each)} each) = `
        + `${formatPrice(step.cost)} div: ${step.step}`)),
      c.roll_divines ? el("li", { class: "range" }, `Then about ${c.roll_divines.toLocaleString()} Divine Orbs to bring `
        + `every line to the modeled ${Math.round(c.roll * 100)}% roll (not in the total; far fewer if lower rolls will do)`) : null),
    el("div", { class: "note" }, `Market model: ${formatPrice(item.market_estimate)} div (fitted on two-line jewels; `
      + `Trade shows what similar jewels ask). The budget uses ${basis}.`));
}

function itemCell(item, shopping) {
  const kind = { unique: "unique", rare: "rare", magic: "magic", normal: "white base", gem: "lineage gem" }[item.kind] || item.kind;
  const title = item.kind === "rare" || item.kind === "magic" ? `${pretty(item.kind)} ${item.type} · ${item.base}`
    : item.kind === "unique" ? `${item.name} · ${item.base}${item.variant ? ` (${item.variant})` : ""}`
      : item.kind === "gem" ? item.name : `${item.base} (${kind})`;
  return el("div", {}, el("strong", {}, title),
    item.kind === "unique" ? el("span", { class: "hint" }, " unique") : null,
    item.affixes?.length ? el("ul", { class: "affixes" }, item.affixes.map((a) => el("li", {},
      el("span", { class: "tier" }, a.tier ? `T${a.tier}` : a.crafted ? "craft" : "?"), a.text,
      a.range && a.range !== a.text ? el("span", { class: "range" }, `  (tier range ${a.range})`) : null,
      a.crafted ? el("span", { class: "range" }, "  · crafted")
        : !a.searchable ? el("span", { class: "range" }, "  · not searchable on trade, check by eye")
          : a.boosted ? el("span", { class: "range" }, `  · includes the crafted ${a.boosted}% boost`) : null))) : null,
    item.note ? el("div", { class: "note" }, item.note) : null,
    item.impossible ? el("div", { class: "note error-text" }, item.impossible) : null,
    item.craft && shopping ? craftBlock(shopping, item) : null);
}

function versionStatus(version) {
  // What the trade site had of a unique's chosen version (builder/report/shopping.py version_priced)
  const day = version.priced_at ? ` on ${new Date(version.priced_at * 1000).toLocaleDateString()}` : "";
  if (!version.listed) {
    const looser = version.partial_listed ? ` (${version.partial_listed.toLocaleString()} listed with ${version.partial}`
      + `${version.partial_cheapest != null ? `, asking from ${formatPrice(version.partial_cheapest)} div` : ""})` : "";
    return `no listing of this exact version${day}${looser}`;
  }
  const asking = version.price != null ? `, asking from ${formatPrice(version.cheapest)} div, typically ${formatPrice(version.price)}` : "";
  return `${version.listed.toLocaleString()} listed of this exact version${day}${asking}`;
}

function tradeButton(shopping, item, part, onVersion) {
  const status = el("span", { class: "trade-status" });
  const known = part === "item" ? item.version_price : null;
  if (known) {
    const href = !known.listed && known.partial_listed ? known.partial_url : known.url;  // the search with listings
    status.replaceChildren(`${versionStatus(known)} · `, el("a", { href, target: "_blank", rel: "noopener" }, "open"));
  }
  const button = el("button", {
    type: "button",
    onclick: async () => {
      // Open the tab now, while the click still counts as the user's (popup blockers), then point it at the search
      const tab = window.open("about:blank", "_blank");
      button.disabled = true;
      status.textContent = "searching the trade site…";
      try {
        const link = await api("/api/trade/link", { method: "POST", body: { item, part, league: shopping.league } });
        if (tab) {
          tab.opener = null;
          tab.location.href = link.url;
        }
        const found = link.total ? `${link.total.toLocaleString()} listed`
          : item.kind === "unique" ? "nothing listed"
          : "nothing listed, even loosened: likely craft-only, and its price here is only the model's estimate";
        const asking = link.asking ? ` · asking from ${formatPrice(link.asking.cheapest)} div, `
          + `typically ${formatPrice(link.asking.typical)}` : "";
        if (link.version) {
          // A unique's chosen version: only the first search is that exact version
          status.replaceChildren(`${versionStatus(link.version)}${link.cached ? " · cached" : ""} · `,
            el("a", { href: link.url, target: "_blank", rel: "noopener" }, "open"));
          if (onVersion) onVersion(link.version);
        } else {
          status.replaceChildren(`${found}${asking} · ${link.matches}${link.cached ? " · cached" : ""} · `,
            el("a", { href: link.url, target: "_blank", rel: "noopener" }, "open"));
        }
      } catch (error) {
        if (tab) tab.close();
        status.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    },
  }, "Trade");
  return el("div", {}, button, status);
}

function shoppingSection(shopping) {
  const rows = [];
  const budget = shopping.budget == null || shopping.ssf ? "" : ` of ${shopping.budget} budget`;
  const totalCell = el("td", { class: "num" });
  const totalNote = el("td", {});
  const heading = el("h3", {});
  const showTotal = () => {
    const over = !shopping.ssf && shopping.budget != null && shopping.total > shopping.budget + 1e-6;
    totalCell.className = `num${over ? " error-text" : ""}`;
    totalCell.textContent = formatPrice(shopping.total);
    totalNote.replaceChildren(`divine${budget}`, ...(over ? [el("div", { class: "note error-text" }, "over budget at today's prices")] : []));
    heading.textContent = `Shopping list · ${formatPrice(shopping.total)} divine${budget}`;
  };
  for (const item of shopping.items) {
    const priceCell = el("td", { class: "num" }, item.kind === "normal" ? "found" : formatPrice(item.price));
    const confidenceCell = el("td", {}, item.confidence || "–");
    const cell = itemCell(item, shopping);
    // A Trade click that prices a unique's chosen version replaces the feed's any-version price
    const onVersion = (version) => {
      const note = cell.querySelector(":scope > .note");
      if (!version.listed) confidenceCell.textContent = "low: this version isn't listed";
      if (!version.listed && note) note.textContent = "No listing of this exact version today. The price shown is "
        + "poe.ninja's for any version: this one may cost far more, or not be for sale.";
      if (version.price == null) return;
      if (note) note.textContent = "Priced from the trade site's listings of this exact version today"
        + (item.feed_price != null ? `; poe.ninja's ${formatPrice(item.feed_price)} div is for any version.` : ".");
      shopping.total += version.price - (item.price || 0);
      item.price = version.price;
      priceCell.textContent = formatPrice(version.price);
      confidenceCell.textContent = `trade, this version (${version.listed} listed, today)`;
      showTotal();
    };
    const trade = shopping.ssf || item.kind === "normal" ? "–" : tradeButton(shopping, item, "item", onVersion);
    rows.push(el("tr", {}, el("td", {}, item.slot), el("td", {}, cell), priceCell, confidenceCell, el("td", {}, trade)));
    for (const rune of item.runes || []) {
      rows.push(el("tr", { class: "socket" }, el("td", {}, "socket"), el("td", {}, rune.name),
        el("td", { class: "num" }, formatPrice(rune.price)), el("td", {}, rune.price == null ? "unpriced" : rune.confidence),
        el("td", {}, shopping.ssf ? "–" : tradeButton(shopping, item, `rune:${rune.name}`))));
    }
  }
  showTotal();
  rows.push(el("tr", { class: "total" }, el("td", {}, "Total"), el("td", {}, ""), totalCell, totalNote, el("td", {}, "")));
  const model = shopping.rare_prices;
  const rareNote = !model?.fitted_at
    ? "Rare prices are uncalibrated estimates (Calibrate rare prices, at the top, fits them to the trade site)."
    : `Rare prices come from the model fitted ${new Date(model.fitted_at * 1000).toLocaleDateString()} on `
      + `${model.total_samples} trade samples (typically within ×${model.error_factor ?? "?"}); slots with thin data stay estimates.`;
  return el("div", { class: "result-section" },
    heading,
    el("p", { class: "hint" }, `Uniques, runes and gems are priced from poe.ninja, which has one price per unique; `
      + "a unique whose version the optimizer chose (Megalomaniac's notables, From Nothing's keystone, a ring size) is "
      + `priced from the trade site's listings of that exact version once you click its Trade. ${rareNote} `
      + "Trade opens an official trade-site search for that line, made when you click (one search, or a few "
      + "looser ones when nothing matches). T1 is the best tier the base can roll; the search asks for at least "
      + "the modeled roll first, then any roll within each tier. The trade site shows a search only when you're "
      + "signed in to your Path of Exile account in that browser."),
    el("div", { class: "table-wrap" }, el("table", { class: "checks" },
      el("thead", {}, el("tr", {}, ["Slot", "Item and target affixes", "Price (div)", "Confidence", "Buy"].map((h, i) =>
        el("th", { class: i === 2 ? "num" : "" }, h)))),
      el("tbody", {}, rows))));
}

function checklistSection(result) {
  const items = result.checklist || [];
  const key = `checklist:${result.saved_as || result.label}`;
  let done = [];
  try { done = JSON.parse(localStorage.getItem(key) || "[]"); } catch { done = []; }
  const save = () => { try { localStorage.setItem(key, JSON.stringify(done)); } catch { /* private window */ } };
  if (!items.length) {
    return el("div", { class: "result-section" }, el("h3", {}, "Test in game"),
      el("p", { class: "hint" }, "Nothing to test: no number in this result is assumed or left out by PoB."));
  }
  return el("div", { class: "result-section" }, el("h3", {}, `Test in game · ${items.length}`),
    el("p", { class: "hint" }, "Every number marked * or not modeled above, and how to check it before you spend."),
    el("ol", { class: "checklist" }, items.map((c, i) => {
      const li = el("li", { class: done.includes(i) ? "done" : "" });
      const box = el("input", { type: "checkbox", checked: done.includes(i), onchange: () => {
        done = box.checked ? [...done, i] : done.filter((d) => d !== i);
        li.classList.toggle("done", box.checked);
        save();
      } });
      li.append(el("label", {}, box, el("span", { class: "kind" }, c.kind), el("strong", {}, c.title),
        c.where ? el("span", { class: "hint" }, ` (${c.where})`) : null),
        el("div", { class: "note" }, c.why), el("div", { class: "note" }, `How: ${c.how}`));
      return li;
    })));
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

// Rare price calibration ------------------------------------------------------------------

function describeRareModel(model) {
  if (!model.fitted_at) return "rare prices: uncalibrated estimates";
  const date = new Date(model.fitted_at * 1000).toLocaleDateString();
  const thin = model.thin?.length ? `; thin data: ${model.thin.join(", ")}` : "";
  return `rare prices: calibrated ${date} on ${model.total_samples} trade samples${thin}`;
}

async function loadCalibration() {
  const league = $("league").value || "";
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  try {
    const status = await api(`/api/prices/calibration${query}`);
    const job = status.job;
    const running = job.state === "running";
    $("calibrate-prices").hidden = running;
    $("calibrate-cancel").hidden = !running;
    $("rare-status").textContent = running
      ? `calibrating rare prices: ${job.done}/${job.total || "?"} · ${job.message}`
      : describeRareModel(status.model) + (["error", "cancelled"].includes(job.state) ? ` (last run: ${job.message})` : "");
    // Only while our own job runs: this asks the local server, never the trade site
    if (running) setTimeout(loadCalibration, 2000);
  } catch (error) {
    $("rare-status").textContent = `rare prices: unavailable (${error.message})`;
  }
}

async function calibrate() {
  const ok = window.confirm("Calibrate rare prices?\n\nThis searches the official trade site for about 24 "
    + "representative rares (one search and one listing fetch each), paced by the site's rate limits: a few "
    + "minutes. Nothing is bought. The new prices apply to the next optimizer run.");
  if (!ok) return;
  const league = $("league").value || "";
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  try {
    await api(`/api/prices/calibrate${query}`, { method: "POST" });
  } catch (error) {
    $("rare-status").textContent = `rare prices: ${error.message}`;
    return;
  }
  loadCalibration();
}

// Unique version pricing (From Nothing's keystones, Controlled Metamorphosis's rings) ------------

function describeVersions(status) {
  const uniques = status.planned.uniques;
  const priced = uniques.map((name) => [name, status.priced[name]]).filter(([, p]) => p);
  if (!priced.length) return `unique versions: ${uniques.join(" and ")} budgeted at poe.ninja's any-version price`;
  const oldest = Math.min(...priced.map(([, p]) => p.priced_at));
  const parts = priced.map(([name, p]) => `${name} ${p.versions} (${p.listed} listed)`);
  return `unique versions: ${parts.join(", ")} priced ${new Date(oldest * 1000).toLocaleDateString()}`;
}

async function loadVersionPricing() {
  const league = $("league").value || "";
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  try {
    const status = await api(`/api/prices/versions${query}`);
    const job = status.job;
    const running = job.state === "running";
    $("price-versions").hidden = running;
    $("price-versions-cancel").hidden = !running;
    $("version-status").textContent = running
      ? `pricing unique versions: ${job.done}/${job.total || "?"} · ${job.message}`
      : describeVersions(status) + (job.state && job.state !== "idle" ? ` (last run: ${job.message})` : "");
    // Only while our own job runs: this asks the local server, never the trade site
    if (running) setTimeout(loadVersionPricing, 2000);
    return status;
  } catch (error) {
    $("version-status").textContent = `unique versions: unavailable (${error.message})`;
    return null;
  }
}

async function priceVersions() {
  const status = await loadVersionPricing();
  const planned = status?.planned;
  const what = planned ? `every version of ${planned.uniques.join(" and ")} (${planned.searches} searches, one `
    + "listing fetch for each listed version)" : "every version of From Nothing and Controlled Metamorphosis";
  const minutes = planned ? Math.max(1, Math.round(planned.searches / 5.5)) : 8;
  const ok = window.confirm(`Price unique versions?\n\nThis searches the official trade site for ${what}, paced `
    + `by the site's rate limits: about ${minutes} minutes. Nothing is bought. From the next optimizer run, each `
    + "version is budgeted at its trade price, and versions nobody lists aren't offered.");
  if (!ok) return;
  const league = $("league").value || "";
  const query = league ? `?league=${encodeURIComponent(league)}` : "";
  try {
    await api(`/api/prices/versions${query}`, { method: "POST" });
  } catch (error) {
    $("version-status").textContent = `unique versions: ${error.message}`;
    return;
  }
  loadVersionPricing();
}

// CPU workers --------------------------------------------------------------------------------

function describeWorkers(s) {
  const busy = s.workers >= s.max_workers ? "nearly all" : `${s.workers} of`;
  $("workers-hint").textContent = `Each worker keeps one CPU thread busy: ${busy} your ${s.cpu_threads} threads. `
    + "Fewer workers keep the computer cooler and quieter but search less in the same time. "
    + "Workers run at low priority, so other programs come first. Changes apply to the next run.";
}

async function loadSettings() {
  const s = await api("/api/settings");
  const select = $("workers");
  select.replaceChildren();
  for (let count = 1; count <= s.max_workers; count++) {
    const note = count === s.recommended_workers ? " (recommended)" : count === s.max_workers ? " (fastest)" : "";
    select.append(el("option", { value: count }, `${count}${note}`));
  }
  select.value = s.workers;
  describeWorkers(s);
  select.addEventListener("change", async () => {
    describeWorkers(await api("/api/settings", { method: "POST", body: { workers: Number(select.value) } }));
  });
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
  $("league").addEventListener("change", () => { loadPrices(); loadCalibration(); loadVersionPricing(); });
  $("new-request").addEventListener("click", newRequest);
  $("save").addEventListener("click", save);
  $("validate").addEventListener("click", validate);
  $("inspect").addEventListener("click", inspect);
  $("optimize").addEventListener("click", optimize);
  $("run-cancel").addEventListener("click", cancelRun);
  $("refresh-prices").addEventListener("click", () => loadPrices(true));
  $("calibrate-prices").addEventListener("click", calibrate);
  $("calibrate-cancel").addEventListener("click", async () => {
    await api("/api/prices/calibration/cancel", { method: "POST" }).catch(() => null);
  });
  $("price-versions").addEventListener("click", priceVersions);
  $("price-versions-cancel").addEventListener("click", async () => {
    await api("/api/prices/versions/cancel", { method: "POST" }).catch(() => null);
  });
  newRequest();
  loadPrices();
  loadCalibration();
  loadVersionPricing();
  loadSettings();
  attachToRun();
}

start().catch((error) => {
  document.querySelector("main").prepend(el("p", { class: "card error-text" }, `Couldn't start: ${error.message}`));
});
