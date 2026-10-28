const API = ((window.APP_CONFIG && window.APP_CONFIG.apiUrl) || "/api").replace(/\/$/, "");
const MAX_BYTES = 4 * 1024 * 1024;

const REGIONS = {
  "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)", "us-west-1": "US West (N. California)",
  "us-west-2": "US West (Oregon)", "af-south-1": "Africa (Cape Town)", "ap-east-1": "Asia Pacific (Hong Kong)",
  "ap-south-1": "Asia Pacific (Mumbai)", "ap-south-2": "Asia Pacific (Hyderabad)",
  "ap-southeast-1": "Asia Pacific (Singapore)", "ap-southeast-2": "Asia Pacific (Sydney)",
  "ap-southeast-3": "Asia Pacific (Jakarta)", "ap-southeast-4": "Asia Pacific (Melbourne)",
  "ap-southeast-5": "Asia Pacific (Malaysia)", "ap-southeast-7": "Asia Pacific (Thailand)",
  "ap-northeast-1": "Asia Pacific (Tokyo)", "ap-northeast-2": "Asia Pacific (Seoul)",
  "ap-northeast-3": "Asia Pacific (Osaka)", "ca-central-1": "Canada (Central)", "ca-west-1": "Canada West (Calgary)",
  "eu-central-1": "Europe (Frankfurt)", "eu-central-2": "Europe (Zurich)", "eu-west-1": "Europe (Ireland)",
  "eu-west-2": "Europe (London)", "eu-west-3": "Europe (Paris)", "eu-south-1": "Europe (Milan)",
  "eu-south-2": "Europe (Spain)", "eu-north-1": "Europe (Stockholm)", "il-central-1": "Israel (Tel Aviv)",
  "me-south-1": "Middle East (Bahrain)", "me-central-1": "Middle East (UAE)", "mx-central-1": "Mexico (Central)",
  "sa-east-1": "South America (Sao Paulo)",
};
const UNIT_WORDS = {
  perSecond: "per second", perMinute: "per minute", perHour: "per hour", perDay: "per day", perWeek: "per week",
  perMonth: "per month", millionPerMonth: "million per month", thousandPerMonth: "thousand per month",
  billionPerMonth: "billion per month", sec: "seconds", min: "minutes", hr: "hours", hours: "hours", ms: "ms",
  day: "days", mbps: "MBps", gbps: "Gbps",
};
const unitLabel = (u) => {
  if (UNIT_WORDS[u]) return UNIT_WORDS[u];
  const [size, freq] = String(u).split("|");
  if (freq !== undefined) return size.toUpperCase().replace("BYTES", "bytes") + (freq && freq !== "NA" ? " " + (UNIT_WORDS[freq] || freq) : "");
  return u.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
};

let items = [];
let services = [];          // [{code, name}]
const schemas = {};          // code -> {templates, regions, name}
let nextId = 1;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const setStatus = (el, html, isError) => { el.innerHTML = html ? (isError ? `<div class="error">${esc(html)}</div>` : html) : ""; };
const busy = (text) => `<span class="spin"></span><span class="muted">${esc(text)}</span>`;

async function api(method, path, body) {
  const r = await fetch(API + path, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch { /* non-JSON error page */ }
  if (r.status === 403) throw new Error("Too many requests from your network. Please wait a few minutes.");
  if (r.status === 429) throw new Error("The service is busy. Please try again in a moment.");
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status}).`);
  return data;
}

async function runJob(body, onTick) {
  const { job_id } = await api("POST", "/jobs", body);
  const t0 = Date.now();
  let misses = 0;
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    let st;
    try {
      st = await api("GET", "/jobs/" + job_id);
      misses = 0;
    } catch (e) {
      // A dropped status check should not lose a job that is still running; give up after several in a row.
      if (++misses >= 5) throw e;
      continue;
    }
    if (st.status === "done") return st.result;
    if (st.status === "error") throw new Error(st.error || "The job failed.");
    if (Date.now() - t0 > 5 * 60 * 1000) throw new Error("This is taking too long. Please try again.");
    onTick && onTick(Math.round((Date.now() - t0) / 1000));
  }
}

async function schemaFor(code) {
  if (!code) return null;
  for (let attempt = 1; !schemas[code]; attempt++) {
    try {
      schemas[code] = await api("GET", "/services/" + encodeURIComponent(code));
    } catch (e) {
      // Throttled responses reach the browser as network errors; back off and retry a few times.
      if (attempt >= 4) throw e;
      await new Promise((r) => setTimeout(r, 500 * attempt));
    }
  }
  return schemas[code];
}

// Large files reference dozens of services; fetch their forms a few at a time to stay under the API rate limit.
async function loadSchemas(codes, parallel = 6) {
  const queue = [...codes];
  await Promise.all(Array.from({ length: parallel }, async () => {
    while (queue.length) await schemaFor(queue.shift());
  }));
}

async function loadServices() {
  if (services.length) return;
  services = await api("GET", "/services");
  $("serviceList").innerHTML = services.map((s) => `<option value="${esc(s.name)}"></option>`).join("");
}

function readB64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
    fr.onerror = () => reject(new Error("Could not read the file."));
    fr.readAsDataURL(file);
  });
}

// ---------- upload ----------
async function handleFile(file) {
  if (!file) return;
  $("result").classList.add("hidden");
  if (file.size > MAX_BYTES) return setStatus($("status"), "File is larger than 4 MB.", true);
  $("drop").style.pointerEvents = "none";
  const say = (s) => setStatus($("status"), busy(`${file.name}: ${s}`));
  say("uploading…");
  try {
    loadServices().catch(() => {});
    const res = await runJob({ type: "extract", filename: file.name, content_b64: await readB64(file) },
      (sec) => say(sec < 15 ? `finding AWS services… (${sec}s)` : `filling in each service's details… (${sec}s)`));
    if (!res.items.length) {
      setStatus($("status"), "No AWS resources were found in this file.", true);
      return;
    }
    items = res.items;
    nextId = items.length + 1;
    await loadSchemas([...new Set(items.map((i) => i.service).filter(Boolean))]);
    $("estName").value = file.name.replace(/\.[^.]+$/, "");
    $("total").textContent = "";
    render();
    $("review").classList.remove("hidden");
    const free = (res.not_priced || []).map((n) => esc(n.name)).join(", ");
    setStatus($("status"), `<span class="muted">Found ${items.length} item(s) in <b>${esc(file.name)}</b>.${
      free ? ` Not priced (no AWS charge): ${free}.` : ""}</span>`);
  } catch (e) {
    setStatus($("status"), e.message, true);
  } finally {
    $("drop").style.pointerEvents = "";
  }
}

// ---------- item state ----------
function templateOf(it) {
  const s = schemas[it.service];
  if (!s) return null;
  return s.templates.find((t) => t.id === it.template) || s.templates[0];
}
function openQuestions(it) {
  return (it.questions || []).filter((q) =>
    q === "region" ? !it.region : !(it.values && q in it.values) && !(it.skipped || []).includes(q));
}
function itemState(it) {
  if (!it.service) return "unsupported";
  return openQuestions(it).length || (it.errors || []).length ? "needs_input" : "ready";
}

// Mirrors catalog.cond_ok: is a field shown given the item's other answers? Unknown inputs count as yes.
function condOk(c, values) {
  if (!c) return true;
  if (c.and) return c.and.every((x) => condOk(x, values));
  if (c.or) return c.or.some((x) => condOk(x, values));
  if (c.not) return !condOk(c.not, values);
  const v = (values[c.id] || {}).value;
  if (v === undefined || v === null || v === "") return true;
  const [a, b] = isNaN(v) || isNaN(c.value) ? [String(v).toLowerCase(), String(c.value).toLowerCase()] : [Number(v), Number(c.value)];
  return { "==": a === b, "!=": a !== b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b }[c.op];
}

// ---------- widgets ----------
function fieldHtml(it, f, isQ) {
  const v = (it.values || {})[f.id];
  const val = v ? v.value : "";
  const d = `data-id="${it.id}" data-f="${esc(f.id)}"`;
  let input;
  switch (f.kind) {
    case "dropdown":
      input = `<select ${d} data-kind="dropdown"><option value="">${"default" in f ? "Default" : "Choose…"}</option>${
        f.options.map((o) => `<option value="${esc(o.id)}"${o.id === val ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
      break;
    case "checkbox":
      input = `<label class="row" style="gap:6px"><input type="checkbox" ${d} data-kind="checkbox"${val === true ? " checked" : ""} style="width:auto"> Yes</label>`;
      break;
    case "frequency": case "size": case "duration": case "throughput": {
      const unit = (v && v.unit) || f.defaultUnit;
      const units = (f.units && f.units.length ? f.units : [unit]).filter(Boolean);
      input = `<div class="pair"><input type="number" min="0" step="any" ${d} data-kind="amount" value="${esc(val)}" placeholder="${esc(f.default ?? "")}">
        <select ${d} data-kind="unit">${units.map((u) => `<option value="${esc(u)}"${u === unit ? " selected" : ""}>${esc((f.unitLabels || {})[u] || unitLabel(u))}</option>`).join("")}</select></div>`;
      break;
    }
    case "instances": return instancesHtml(it, f, isQ);
    case "datatransfer": return transferHtml(it, f, isQ);
    case "text":
      input = `<input type="text" ${d} data-kind="text" value="${esc(val)}" placeholder="${esc(f.help || "")}">`;
      break;
    default:
      input = `<input type="number" step="any" ${d} data-kind="number" value="${esc(val)}" placeholder="${esc(f.default ?? "")}">`;
  }
  return `<label class="f${isQ ? " q" : ""}" title="${esc(f.help || "")}"><span class="lbl"><span>${esc(f.label)}</span>${skipBtn(it, f, isQ)}</span>${input}</label>`;
}

// Family columns are looked up from the instance type by the calculator, so they are not edited here.
const rowColumns = (f) => (f.columns || []).filter((c) => !c.derived);

function newRow(f) {
  const row = {};
  for (const c of rowColumns(f)) {
    if (c.count) row[c.key] = { value: "1" };
    else if (c.type === "utilization") row[c.key] = { value: { unit: "100", selectedId: "%Utilized/Month" } };
    else if (c.key === "TermType") row[c.key] = { value: "OnDemand" };
  }
  return row;
}

function instancesHtml(it, f, isQ) {
  const rows = ((it.values || {})[f.id] || {}).value || [];
  const cols = rowColumns(f);
  const cell = (r, i, c) => {
    const raw = r[c.key] ? r[c.key].value : "";
    const util = c.type === "utilization";
    const val = util ? (raw && raw.unit) || "" : raw;
    const ph = util ? "100" : c.count ? "1" : c.key === "TermType" ? "OnDemand" : c.default ? `e.g. ${c.default}` : "";
    return `<td><input data-id="${it.id}" data-f="${esc(f.id)}" data-row="${i}" data-col="${esc(c.key)}"${util ? ' data-util="1"' : ""} value="${esc(val)}" placeholder="${esc(ph)}"></td>`;
  };
  const head = cols.map((c) => `<th>${esc(c.type === "utilization" ? "Utilization %" : c.label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr>${cols.map((c) => cell(r, i, c)).join("")}<td><button class="link" data-delrow="${i}" data-id="${it.id}" data-f="${esc(f.id)}">Remove</button></td></tr>`).join("");
  return `<div class="f wide${isQ ? " q" : ""}"><span class="lbl"><span>${esc(f.label)}</span>${skipBtn(it, f, isQ)}</span>
    <table class="rows"><tr>${head}<th></th></tr>${body}</table>
    <div><button class="small" data-addrow="1" data-id="${it.id}" data-f="${esc(f.id)}">+ Add row</button></div></div>`;
}

const skipBtn = (it, f, isQ) => isQ ? `<button class="link" data-skip="${esc(f.id)}" data-id="${it.id}">Use default</button>` : "";

function transferHtml(it, f, isQ) {
  const rows = ((it.values || {})[f.id] || {}).value || [];
  const types = f.types || ["INBOUND", "OUTBOUND"];
  const body = rows.map((r, i) => `<tr><td><select data-id="${it.id}" data-f="${esc(f.id)}" data-dt="${i}" data-col="entryType">${
    types.map((t) => `<option${t === r.entryType ? " selected" : ""}>${t}</option>`).join("")}</select></td>
    <td><input type="number" min="0" step="any" data-id="${it.id}" data-f="${esc(f.id)}" data-dt="${i}" data-col="gb" value="${esc(r.value === "" ? "" : Math.round(Number(r.value) * 1024 * 100) / 100)}"></td>
    <td><button class="link" data-deldt="${i}" data-id="${it.id}" data-f="${esc(f.id)}">Remove</button></td></tr>`).join("");
  return `<div class="f wide${isQ ? " q" : ""}"><span class="lbl"><span>${esc(f.label)} (GB per month)</span>${skipBtn(it, f, isQ)}</span>
    <table class="rows">${body}</table><div><button class="small" data-adddt="1" data-id="${it.id}" data-f="${esc(f.id)}">+ Add transfer</button></div></div>`;
}

// ---------- render ----------
function render() {
  $("items").innerHTML = items.map(itemHtml).join("");
  updateFooter();
}

function itemHtml(it) {
  const state = itemState(it);
  const badge = state === "ready" ? `<span class="badge b-ok">Ready</span>`
    : state === "needs_input" ? `<span class="badge b-need">Needs input</span>` : `<span class="badge b-off">Choose a service</span>`;
  const cost = it.monthly != null ? `<span class="cost">${money(it.monthly)} / mo${it.upfront ? ` + ${money(it.upfront)} upfront` : ""}</span>` : "";
  const svcName = it.service ? (schemas[it.service] || {}).name || it.serviceName : "";
  const head = `<div class="item-head"><span class="name">${esc(it.name || "Item")}</span>
    <span class="svc">${esc(svcName)}</span>${badge}${cost}
    <button class="link" data-remove="${it.id}">Remove</button></div>
    ${it.original_text ? `<div class="src">From file: “${esc(it.original_text)}”</div>` : ""}`;
  const picker = `<label class="f wide"><span class="lbl"><span>${it.service ? "Change service" : "AWS service"}</span></span>
    <input class="svc-pick" list="serviceList" data-pick="${it.id}" placeholder="Type to search 400+ services${it.guess ? " (file said: " + esc(it.guess) + ")" : ""}"></label>`;
  if (!it.service) return `<div class="item off">${head}<div class="fields">${picker}</div></div>`;

  const s = schemas[it.service];
  const tpl = templateOf(it);
  if (!s || !tpl) return `<div class="item">${head}<div class="msg">${busy("Loading fields…")}</div></div>`;
  const qs = openQuestions(it);
  const byId = Object.fromEntries(tpl.fields.map((f) => [f.id, f]));
  const shown = new Set();
  const qFields = qs.filter((q) => byId[q]).map((q) => { shown.add(q); return fieldHtml(it, byId[q], true); });
  const regionSel = `<label class="f${qs.includes("region") ? " q" : ""}"><span class="lbl"><span>Region</span></span>
    <select data-id="${it.id}" data-kind="region"><option value="">Choose…</option>${
      (s.regions || Object.keys(REGIONS)).map((r) => `<option value="${r}"${r === it.region ? " selected" : ""}>${esc(REGIONS[r] || r)}</option>`).join("")}</select></label>`;
  const tplSel = s.templates.length > 1 ? `<label class="f"><span class="lbl"><span>Option</span></span><select data-id="${it.id}" data-kind="template">${
    s.templates.map((t) => `<option value="${esc(t.id)}"${t.id === tpl.id ? " selected" : ""}>${esc(t.title)}</option>`).join("")}</select></label>` : "";
  const vals = it.values || {};
  const visible = tpl.fields.filter((f) => condOk(f.showIf, vals));
  const filled = visible.filter((f) => !shown.has(f.id) && f.id in vals).map((f) => { shown.add(f.id); return fieldHtml(it, f, false); });
  const rest = visible.filter((f) => !shown.has(f.id));
  const more = rest.length ? `<details class="more"${it._open ? " open" : ""} data-more="${it.id}"><summary>More fields (${rest.length}) — calculator defaults apply if left empty</summary>
    <div class="fields">${rest.sort((a, b) => !!a.conditional - !!b.conditional).map((f) => fieldHtml(it, f, false)).join("")}</div></details>` : "";
  const msgs = [...(it.errors || []).map((e) => `<div class="msg err">${esc(e)}</div>`),
    ...(it.warnings || []).map((w) => `<div class="msg warn">${esc(w)}</div>`),
    it.warning ? `<div class="msg warn">${esc(it.warning)}</div>` : "",
    it.notes ? `<div class="calc">AI note: ${esc(it.notes)}</div>` : "",
    it.configSummary ? `<div class="calc">Calculator: ${esc(it.configSummary)}</div>` : ""].join("");
  return `<div class="item">${head}
    ${qs.length ? `<div class="qbox">Not in your file — please answer or use the calculator default:<div class="fields">${qFields.join("")}</div></div>` : ""}
    <div class="fields">${regionSel}${tplSel}${filled.join("")}</div>${more}${msgs}
    <details class="more"><summary>Change service</summary><div class="fields">${picker}</div></details></div>`;
}

function updateFooter() {
  const states = items.map(itemState);
  const need = states.filter((s) => s === "needs_input").length;
  const ready = states.filter((s) => s === "ready").length;
  const off = states.filter((s) => s === "unsupported").length;
  $("generate").disabled = need > 0 || ready === 0;
  $("price").disabled = ready === 0;
  $("blockers").textContent = [need ? `${need} item(s) need input` : "", off ? `${off} without a service (skipped)` : ""].filter(Boolean).join(" · ");
}

// ---------- editing ----------
const find = (id) => items.find((i) => i.id === id);
function changed(it) { delete it.monthly; delete it.upfront; delete it.configSummary; delete it.warning; it.errors = []; }

document.addEventListener("change", async (e) => {
  const el = e.target;
  const it = find(el.dataset.id || el.dataset.pick);
  if (!it) return;
  if (el.dataset.pick !== undefined) {
    const s = services.find((x) => x.name === el.value);
    if (!s) return;
    it.service = s.code; it.serviceName = s.name; it.template = null; it.values = {}; it.questions = []; it.skipped = [];
    changed(it); render();
    const sc = await schemaFor(s.code);
    if (!it.region || !sc.regions.includes(it.region)) it.questions = ["region"];
    render();
    return;
  }
  const fid = el.dataset.f;
  it.values = it.values || {};
  const kind = el.dataset.kind;
  if (kind === "region") it.region = el.value || null;
  else if (kind === "template") { it.template = el.value; it.values = {}; }
  else if (el.dataset.row !== undefined) {
    const rows = it.values[fid].value; const r = rows[+el.dataset.row]; const col = el.dataset.col;
    if (el.dataset.util) r[col] = { value: { unit: el.value || "100", selectedId: "%Utilized/Month" } };
    else if (el.value === "") delete r[col]; else r[col] = { value: el.value };
  } else if (el.dataset.dt !== undefined) {
    const r = it.values[fid].value[+el.dataset.dt];
    if (el.dataset.col === "gb") r.value = el.value === "" ? "" : String(Number(el.value) / 1024); else r.entryType = el.value;
  } else if (kind === "unit") {
    if (it.values[fid]) it.values[fid].unit = el.value;
    else el.dataset.pendingUnit = el.value;
  } else if (kind === "amount") {
    const unitSel = el.parentElement.querySelector('[data-kind="unit"]');
    if (el.value === "") delete it.values[fid]; else it.values[fid] = { value: el.value, unit: unitSel.value };
  } else if (kind === "checkbox") it.values[fid] = { value: el.checked };
  else if (el.value === "") delete it.values[fid];
  else it.values[fid] = { value: el.value };
  changed(it);
  // Re-render this card only so focus in other cards is kept.
  const open = el.closest("details.more");
  if (open) it._open = open.open;
  rerender(it);
});

document.addEventListener("click", (e) => {
  const el = e.target.closest("button");
  if (!el) return;
  const it = find(el.dataset.id || el.dataset.remove);
  if (el.dataset.remove) { items = items.filter((x) => x.id !== el.dataset.remove); render(); return; }
  if (!it) return;
  e.preventDefault();
  const fid = el.dataset.f;
  if (el.dataset.skip) { it.skipped = [...(it.skipped || []), el.dataset.skip]; }
  else if (el.dataset.addrow) {
    it.values = it.values || {};
    const rows = (it.values[fid] = it.values[fid] || { value: [] }).value;
    rows.push(newRow(templateOf(it).fields.find((x) => x.id === fid) || {}));
  } else if (el.dataset.delrow !== undefined) {
    it.values[fid].value.splice(+el.dataset.delrow, 1);
    if (!it.values[fid].value.length) delete it.values[fid];
  } else if (el.dataset.adddt) {
    it.values = it.values || {};
    (it.values[fid] = it.values[fid] || { value: [] }).value.push({ entryType: "OUTBOUND", value: "", unit: "tb_month", toRegion: "" });
  } else if (el.dataset.deldt !== undefined) {
    it.values[fid].value.splice(+el.dataset.deldt, 1);
    if (!it.values[fid].value.length) delete it.values[fid];
  } else return;
  changed(it);
  rerender(it);
});

document.addEventListener("toggle", (e) => {
  const d = e.target;
  if (d.dataset && d.dataset.more) { const it = find(d.dataset.more); if (it) it._open = d.open; }
}, true);

function rerender(it) {
  const idx = items.indexOf(it);
  const el = $("items").children[idx];
  if (el) el.outerHTML = itemHtml(it);
  updateFooter();
}

$("addItem").onclick = async () => {
  await loadServices().catch(() => {});
  items.push({ id: "n" + nextId++, name: "New item", service: null, values: {}, questions: [], skipped: [] });
  render();
};

// ---------- pricing / link ----------
async function price(save) {
  const ready = items.filter((i) => itemState(i) === "ready");
  const btns = [$("price"), $("generate")];
  btns.forEach((b) => (b.disabled = true));
  const say = (sec) => setStatus($("status2"), busy(`${save ? "Pricing in the AWS Pricing Calculator and creating your link" : "Pricing in the AWS Pricing Calculator"}… (${sec}s)`));
  say(0);
  try {
    const res = await runJob({ type: save ? "link" : "price", items: ready, name: $("estName").value }, say);
    for (const r of res.items) {
      const it = find(r.id);
      if (it) Object.assign(it, { monthly: r.monthly, upfront: r.upfront, configSummary: r.configSummary, warning: r.warning, errors: r.errors || [], warnings: r.warnings || [] });
    }
    $("total").textContent = `Total ${money(res.total.monthly)} / month` + (res.total.upfront ? ` + ${money(res.total.upfront)} upfront` : "");
    render();
    setStatus($("status2"), "");
    if (res.url) showResult(res);
  } catch (e) {
    setStatus($("status2"), e.message, true);
  } finally {
    updateFooter();
  }
}

function showResult(res) {
  const skipped = items.filter((i) => itemState(i) !== "ready");
  $("result").innerHTML = `
    <strong>Your AWS Pricing Calculator estimate</strong>
    <p class="total">${money(res.total.monthly)} / month${res.total.upfront ? ` + ${money(res.total.upfront)} upfront` : ""}</p>
    <p><a href="${esc(res.url)}" target="_blank" rel="noopener">${esc(res.url)}</a></p>
    <div class="row"><button id="copy">Copy link</button><a href="${esc(res.url)}" target="_blank" rel="noopener"><button class="primary">Open in calculator</button></a></div>
    ${skipped.length ? `<p class="muted">Not included: ${skipped.map((i) => esc(i.name)).join(", ")}.</p>` : ""}`;
  $("result").classList.remove("hidden");
  $("copy").onclick = () => navigator.clipboard.writeText(res.url).then(() => ($("copy").textContent = "Copied"));
  $("result").scrollIntoView({ behavior: "smooth" });
}

$("price").onclick = () => price(false);
$("generate").onclick = () => price(true);
$("file").onchange = (e) => handleFile(e.target.files[0]);
const drop = $("drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => handleFile(e.dataTransfer.files[0]));
