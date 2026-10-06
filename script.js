const $ = (id) => document.getElementById(id);

const PARAMS = {
  organize_files: `<input id="p_folder" placeholder="Folder path" />`,
  bulk_rename: `<input id="p_folder" placeholder="Folder path" /><input id="p_pattern" placeholder="Pattern, use {n} (e.g. doc_{n})" value="file_{n}" />`,
  clean_temp: `<input id="p_folder" placeholder="Folder path" /><input id="p_days" type="number" value="7" placeholder="Older than (days)" />`,
  write_note: `<input id="p_folder" placeholder="Folder (blank = app folder)" /><input id="p_filename" placeholder="Filename (blank = auto)" /><textarea id="p_text" rows="3" placeholder="Note text"></textarea>`,
  fetch_url: `<input id="p_url" placeholder="https://example.com" value="https://example.com" />`,
};

function renderParams() { $("paramBox").innerHTML = PARAMS[$("tType").value] || ""; }
$("tType").addEventListener("change", renderParams);
renderParams();

function collectParams(type) {
  const v = (id) => ($(id) ? $(id).value : "");
  if (type === "organize_files") return { folder: v("p_folder") };
  if (type === "bulk_rename") return { folder: v("p_folder"), pattern: v("p_pattern") || "file_{n}" };
  if (type === "clean_temp") return { folder: v("p_folder"), older_than_days: parseInt(v("p_days")) || 7 };
  if (type === "write_note") return { folder: v("p_folder"), filename: v("p_filename"), text: v("p_text") };
  if (type === "fetch_url") return { url: v("p_url") };
  return {};
}

async function loadAll() {
  try {
    const t = await (await fetch("/api/tasks")).json();
    const l = await (await fetch("/api/logs")).json();
    $("status").textContent = "● server online (port 8000)";
    $("status").style.color = "#22c55e";
    $("taskList").innerHTML = t.tasks.map(x => `
      <div class="task">
        <b>${esc(x.name)}</b>
        <span class="badge">${esc(x.type)}</span>
        <span class="badge ${x.enabled ? "on" : "off"}">${x.enabled ? "enabled" : "disabled"}</span>
        <div class="meta">${esc(JSON.stringify(x.params))}\nLast: ${esc(x.last_result || "never")} ${x.schedule_interval_sec ? "| every " + x.schedule_interval_sec + "s" : "| manual"}</div>
        <button onclick="runTask(${x.id})">▶ Run</button>
        <button onclick="toggleTask(${x.id})">${x.enabled ? "⏸ Disable" : "▶ Enable"}</button>
        <button class="danger" onclick="delTask(${x.id})">🗑</button>
      </div>`).join("") || "<p>No tasks yet.</p>";
    $("logs").textContent = l.logs.join("\n") || "(no logs)";
  } catch {
    $("status").textContent = "● offline — run: py app.py";
    $("status").style.color = "#f87171";
  }
}
function esc(s) { return String(s ?? "").replace(/[&<>\"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function createTask() {
  const type = $("tType").value;
  try {
    const r = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("tName").value || "Untitled", type, params: collectParams(type), schedule_interval_sec: parseInt($("tInterval").value) || 0 }) });
    const j = await r.json();
    showResult(r.ok ? "✅ Created task: " + j.task.name : "❌ Create failed: " + (j.error || r.status), r.ok);
  } catch (e) { showResult("❌ Create failed: server unreachable", false); }
  $("tName").value = "";
  loadAll();
}
function showResult(msg, ok) {
  const el = $("result");
  el.textContent = msg;
  el.className = "result " + (ok ? "ok" : "err");
}
async function runTask(id) {
  try {
    const r = await fetch("/api/run?id=" + id, { method: "POST" });
    const j = await r.json();
    showResult(r.ok ? ((j.ok ? "✅ " : "❌ ") + j.message) : "❌ Run failed: " + (j.error || r.status), r.ok && j.ok);
  } catch (e) { showResult("❌ Run failed: server unreachable", false); }
  loadAll();
}
async function toggleTask(id) { await fetch("/api/toggle?id=" + id, { method: "POST" }); loadAll(); }
async function delTask(id) { if (confirm("Delete task?")) { await fetch("/api/tasks?id=" + id, { method: "DELETE" }); loadAll(); } }
async function quickNote() {
  await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Quick note " + new Date().toLocaleTimeString(), type: "write_note", params: { text: "Quick note at " + new Date().toString() } }) });
  loadAll();
}
async function quickFetch() {
  const r = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Fetch example.com", type: "fetch_url", params: { url: "https://example.com" } }) });
  const j = await r.json();
  await fetch("/api/run?id=" + j.task.id, { method: "POST" });
  loadAll();
}
setInterval(loadAll, 5000);
loadAll();
