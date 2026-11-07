// The mechanics catalog page: every mechanic PoB doesn't simulate (builder/ledger/mechanics.py), searchable and filterable.
let catalog = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(typeof child === "string" || typeof child === "number" ? String(child) : child);
  }
  return node;
}

function pobOf(m) { return m.pob || m.pob_computed; }

function matches(m, q) {
  if (!q) return true;
  const hay = [m.name, ...(m.names || []), m.category, m.game || "", m.handling || "", (m.plan && m.plan.how) || "",
               ...(m.gems || []), ...(m.all_lines || []).map((l) => l.line)].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

function card(m) {
  const ln = m.lines;
  const plan = m.plan ? `${m.plan.approach}: ${m.plan.how}` + (m.plan.sustain ? ` On while: ${m.plan.sustain}.` : "") : "to plan";
  const facts = [`priority ${m.priority}`, `impact ${m.impact ?? "?"}`, `effort ${m.effort ?? "?"}`,
                 `${ln.not_counted} lines not counted of ${ln.linked + ln.counted_by_pob_elsewhere}`,
                 `${m.gem_count} gems`, `what-ifs: ${m.what_if ?? "?"}`, m.origin].join(" · ");
  const details = el("details", {}, el("summary", {}, `Lines not counted (${ln.not_counted})`),
    el("ul", { class: "lines" }, (m.all_lines || []).map((l) => el("li", {}, `${l.line}  (${l.source}; ${l.category})`))));
  return el("div", { class: "mech" },
    el("h3", {}, m.name, el("span", { class: `badge pob-${pobOf(m)}` }, `PoB: ${pobOf(m)}`),
       m.pob && m.pob !== m.pob_computed ? el("span", { class: "badge" }, `lines say: ${m.pob_computed}`) : null),
    el("div", { class: "facts" }, facts),
    el("ul", {},
      m.game ? el("li", {}, el("b", {}, "Game: "), m.game, m.game_source ? ` (${m.game_source})` : "") : null,
      m.pob_check ? el("li", {}, el("b", {}, "PoB (checked): "), m.pob_check) : null,
      m.handling ? el("li", {}, el("b", {}, "Now: "), m.handling) : null,
      el("li", {}, el("b", {}, "Plan: "), plan),
      m.guide_note ? el("li", {}, el("b", {}, "Guide note: "), m.guide_note) : null,
      m.configs && m.configs.length ? el("li", {}, el("b", {}, "PoB options: "), m.configs.map((c) => `${c.var} (${c.optimizer})`).join(", ")) : null,
      m.gems && m.gems.length ? el("li", {}, el("b", {}, "Gems: "), m.gems.slice(0, 15).join(", ")) : null),
    ln.not_counted ? details : null);
}

function render() {
  const q = document.getElementById("q").value.trim();
  const pob = document.getElementById("pob").value;
  const cat = document.getElementById("cat").value;
  const prio = Number(document.getElementById("prio").value) || 0;
  const planned = document.getElementById("planned").checked;
  const shown = catalog.mechanics.filter((m) => (!pob || pobOf(m) === pob) && (!cat || m.category === cat)
    && (m.priority || 0) >= prio && (!planned || m.origin === "catalog") && matches(m, q));
  const list = document.getElementById("list");
  list.replaceChildren(...shown.slice(0, 300).map(card));
  document.getElementById("shown").textContent = `${shown.length} shown` + (shown.length > 300 ? " (first 300)" : "");
}

async function load() {
  const response = await fetch("/api/mechanics");
  catalog = await response.json();
  const s = catalog.summary;
  document.getElementById("summary").textContent = `${s.mechanics} mechanics (${s.reviewed} reviewed with a plan). `
    + `PoB: ${Object.entries(s.by_pob).map(([k, v]) => `${k} ${v}`).join(", ")}. Sources: poe2db's keyword glossary `
    + `(${s.keywords}), capitalised terms in every line and gem description, PoB's ${s.config_options} Configuration options `
    + `(${s.config_options_on} turned on by the optimizer), systemic rules.`;
  const cats = [...new Set(catalog.mechanics.map((m) => m.category))].sort();
  document.getElementById("cat").append(...cats.map((c) => el("option", {}, c)));
  for (const id of ["q", "pob", "cat", "prio", "planned"]) {
    document.getElementById(id).addEventListener("input", render);
  }
  render();
}

load();
