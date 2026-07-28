// SkullMaster iQ — frontend
const $ = (sel) => document.querySelector(sel);

const state = {
  notebooks: [],
  current: null,        // notebook id
  history: [],          // [{role, content}] for model context
  chatBusy: false,
  ingestBusy: false,
};

// ---------- Toasts ----------
function toast(message, kind = "error") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

// ---------- Theme ----------
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("skullmaster-theme", theme);
  $("#theme-toggle").textContent = theme === "dark" ? "☀️" : "🌙";
}
applyTheme(localStorage.getItem("skullmaster-theme") || "dark");
$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

// ---------- Mobile panels ----------
function setMobilePanel(name) {
  localStorage.setItem("skullmaster-panel", name);
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.dataset.active = panel.dataset.panel === name ? "true" : "false";
  });
  document.querySelectorAll(".mobile-nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.target === name);
  });
}

for (const btn of document.querySelectorAll(".mobile-nav button")) {
  btn.addEventListener("click", () => setMobilePanel(btn.dataset.target));
}
setMobilePanel(localStorage.getItem("skullmaster-panel") || "chat");

// ---------- API helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData)
      ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
}

// ---------- Health ----------
async function loadHealth() {
  const badge = $("#model-badge");
  badge.classList.remove("err");
  badge.textContent = "checking…";
  try {
    const h = await api("/api/health");
    if (h.ok) {
      badge.textContent =
        `chat: ${h.llm.chat_model} · embed: ${h.llm.embed_model} · tts: ${h.tts.backend}`;
    } else {
      badge.classList.add("err");
      badge.textContent = !h.llm.reachable ? "⚠ Ollama unreachable"
        : !h.llm.chat_model_ready ? `⚠ chat model ${h.llm.chat_model} missing`
        : !h.llm.embed_model_ready ? `⚠ embed model ${h.llm.embed_model} missing`
        : "⚠ degraded — run diagnostics";
    }
  } catch {
    badge.classList.add("err");
    badge.textContent = "⚠ backend unreachable";
  }
}
$("#health-refresh").addEventListener("click", loadHealth);

// ---------- Notebooks ----------
async function loadNotebooks() {
  state.notebooks = await api("/api/notebooks");
  if (state.notebooks.length === 0) {
    await api("/api/notebooks", { method: "POST", body: JSON.stringify({ name: "My Notebook" }) });
    return loadNotebooks();
  }
  const sel = $("#nb-select");
  sel.innerHTML = "";
  for (const nb of state.notebooks) {
    const opt = document.createElement("option");
    opt.value = nb.id;
    opt.textContent = `${nb.name} (${nb.source_count})`;
    sel.appendChild(opt);
  }
  if (!state.current || !state.notebooks.find((n) => n.id === state.current)) {
    state.current = state.notebooks[0].id;
  }
  sel.value = state.current;
  await openNotebook();
}

async function openNotebook() {
  state.history = [];
  $("#messages").innerHTML = "";
  await Promise.all([loadSources(), loadMessages(), loadAudioOverviews(), loadArtifacts()]);
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
           : `${m}:${String(sec).padStart(2, "0")}`;
}

const KIND_ICONS = {
  pdf: "📕", url: "🔗", docx: "📘", sheet: "📊", video: "🎬", audio: "🎵", text: "📄",
};

$("#nb-select").addEventListener("change", async (e) => {
  state.current = e.target.value;
  await openNotebook().catch((err) => toast(err.message));
});

$("#nb-new").addEventListener("click", async () => {
  const name = prompt("Notebook name:");
  if (!name || !name.trim()) return;
  try {
    const nb = await api("/api/notebooks", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    state.current = nb.id;
    await loadNotebooks();
    toast(`Notebook "${nb.name}" created`, "success");
  } catch (err) { toast(err.message); }
});

$("#nb-rename").addEventListener("click", async () => {
  if (!state.current) return;
  const nb = state.notebooks.find((n) => n.id === state.current);
  const name = prompt("New notebook name:", nb.name);
  if (!name || !name.trim() || name.trim() === nb.name) return;
  try {
    await api(`/api/notebooks/${state.current}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
    await loadNotebooks();
    toast("Notebook renamed", "success");
  } catch (err) { toast(err.message); }
});

$("#nb-delete").addEventListener("click", async () => {
  if (!state.current) return;
  const nb = state.notebooks.find((n) => n.id === state.current);
  if (!confirm(`Delete notebook "${nb.name}" and all its sources, chat, and audio?`)) return;
  try {
    await api(`/api/notebooks/${state.current}`, { method: "DELETE" });
    state.current = null;
    await loadNotebooks();
    toast("Notebook deleted", "success");
  } catch (err) { toast(err.message); }
});

// ---------- Sources ----------
function setIngestBusy(busy, label = "") {
  state.ingestBusy = busy;
  $("#file-btn").disabled = busy;
  $("#url-add").disabled = busy;
  $("#url-input").disabled = busy;
  const prog = $("#ingest-progress");
  prog.hidden = !busy;
  prog.textContent = label;
}

async function loadSources() {
  const sources = await api(`/api/notebooks/${state.current}/sources`);
  const ul = $("#source-list");
  ul.innerHTML = "";
  for (const s of sources) {
    const li = document.createElement("li");
    if (s.status === "failed") li.classList.add("failed");
    const icon = KIND_ICONS[s.kind] || "📄";
    const meta = s.status === "failed" ? "failed"
      : s.pages ? `${s.pages}p · ${s.chunk_count} chunks` : `${s.chunk_count} chunks`;

    const name = document.createElement("span");
    name.className = "src-name";
    name.title = s.origin || s.name;
    name.textContent = s.name;

    li.append(Object.assign(document.createElement("span"), { textContent: icon }));
    li.append(name);
    li.append(Object.assign(document.createElement("span"), { className: "src-meta", textContent: meta }));

    if ((s.kind === "video" || s.kind === "audio") && s.status === "ready") {
      const play = document.createElement("button");
      play.className = "icon-btn retry";
      play.title = "Play";
      play.setAttribute("aria-label", `Play ${s.name}`);
      play.textContent = "▶";
      play.addEventListener("click", () => openMedia(s.id, s.name, s.kind, 0));
      li.append(play);
    }

    if (s.status === "failed") {
      const retry = document.createElement("button");
      retry.className = "icon-btn retry";
      retry.title = "Retry indexing";
      retry.setAttribute("aria-label", `Retry indexing ${s.name}`);
      retry.textContent = "↻";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        try {
          await api(`/api/notebooks/${state.current}/sources/${s.id}/retry`, { method: "POST" });
          toast(`"${s.name}" indexed`, "success");
        } catch (err) { toast(`Retry failed: ${err.message}`); }
        await loadSources();
      });
      li.append(retry);
    }

    const del = document.createElement("button");
    del.className = "icon-btn";
    del.title = "Remove source";
    del.setAttribute("aria-label", `Remove source ${s.name}`);
    del.textContent = "✕";
    del.addEventListener("click", async () => {
      if (!confirm(`Remove source "${s.name}"?`)) return;
      del.disabled = true;
      try {
        await api(`/api/notebooks/${state.current}/sources/${s.id}`, { method: "DELETE" });
      } catch (err) { toast(err.message); }
      await loadSources();
      await refreshNotebookCounts();
    });
    li.append(del);

    if (s.status === "failed" && s.error) {
      const err = document.createElement("span");
      err.className = "src-err";
      err.textContent = s.error;
      li.append(err);
    }
    ul.appendChild(li);
  }
  $("#sources-empty").style.display = sources.length ? "none" : "block";
}

async function refreshNotebookCounts() {
  state.notebooks = await api("/api/notebooks");
  const sel = $("#nb-select");
  for (const opt of sel.options) {
    const nb = state.notebooks.find((n) => n.id === opt.value);
    if (nb) opt.textContent = `${nb.name} (${nb.source_count})`;
  }
}

$("#file-btn").addEventListener("click", () => $("#file-input").click());

$("#file-input").addEventListener("change", async (e) => {
  if (state.ingestBusy) return;
  const files = [...e.target.files];
  e.target.value = "";
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    setIngestBusy(true, `Indexing ${file.name}… (${i + 1}/${files.length})`);
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/api/notebooks/${state.current}/sources/file`, { method: "POST", body: fd });
      toast(`"${file.name}" added`, "success");
    } catch (err) {
      toast(`${file.name}: ${err.message}`);
    }
  }
  setIngestBusy(false);
  await loadSources();
  await refreshNotebookCounts();
});

$("#url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.ingestBusy) return;
  const url = $("#url-input").value.trim();
  if (!url) return;
  setIngestBusy(true, "Fetching and indexing URL…");
  try {
    const src = await api(`/api/notebooks/${state.current}/sources/url`, {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    $("#url-input").value = "";
    toast(`"${src.name}" added`, "success");
  } catch (err) {
    toast(err.message);
  }
  setIngestBusy(false);
  await loadSources();
  await refreshNotebookCounts();
});

// ---------- Chat ----------
function addMessage(role, text = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("#messages").appendChild(div);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  updateChatEmpty();
  return div;
}

function updateChatEmpty() {
  $("#chat-empty").style.display = $("#messages").children.length ? "none" : "block";
}

document.querySelector("#chat-empty")?.addEventListener("click", (e) => {
  const chip = e.target.closest(".quick-prompt");
  if (!chip) return;
  $("#chat-input").value = chip.dataset.prompt || "";
  $("#chat-input").focus();
});

function renderWithCitations(el, text, citations) {
  el.innerHTML = "";
  const parts = text.split(/(\[\d+\])/g);
  for (const part of parts) {
    const m = part.match(/^\[(\d+)\]$/);
    const cite = m && citations.find((c) => c.n === Number(m[1]));
    if (cite) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cite";
      chip.textContent = m[1];
      chip.setAttribute("aria-label", `Citation ${m[1]}: ${cite.source_name}`);
      chip.addEventListener("click", () => showCitation(cite));
      el.appendChild(chip);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  }
}

async function loadMessages() {
  const msgs = await api(`/api/notebooks/${state.current}/messages`);
  $("#messages").innerHTML = "";
  state.history = [];
  for (const m of msgs) {
    const el = addMessage(m.role, m.content);
    if (m.role === "assistant" && m.citations.length) {
      renderWithCitations(el, m.content, m.citations);
    }
    state.history.push({ role: m.role, content: m.content });
  }
  $("#messages").scrollTop = $("#messages").scrollHeight;
  updateChatEmpty();
}

$("#chat-clear").addEventListener("click", async () => {
  if (!state.current || !state.history.length) return;
  if (!confirm("Clear the chat history for this notebook?")) return;
  try {
    await api(`/api/notebooks/${state.current}/messages`, { method: "DELETE" });
    await loadMessages();
  } catch (err) { toast(err.message); }
});

function showCitation(c) {
  const isMedia = c.kind === "video" || c.kind === "audio";
  $("#modal-title").textContent = isMedia && c.page != null
    ? `${c.source_name} — at ${fmtTime(c.page)}`
    : c.page ? `${c.source_name} — page ${c.page}` : c.source_name;
  $("#modal-body").textContent = c.text;
  const play = $("#modal-play");
  play.hidden = !isMedia;
  if (isMedia) {
    play.textContent = `▶ Play from ${fmtTime(c.page || 0)}`;
    play.onclick = () => {
      closeModal();
      openMedia(c.source_id, c.source_name, c.kind, c.page || 0);
    };
  }
  $("#modal-backdrop").hidden = false;
  $("#modal-close").focus();
}
function closeModal() { $("#modal-backdrop").hidden = true; }
$("#modal-close").addEventListener("click", closeModal);
$("#modal-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "modal-backdrop") closeModal();
});

// ---------- Media playback modal ----------
function openMedia(sourceId, name, kind, seekSeconds) {
  $("#media-title").textContent = name;
  const body = $("#media-body");
  body.innerHTML = "";
  const el = document.createElement(kind === "video" ? "video" : "audio");
  el.controls = true;
  el.src = `/api/media/${sourceId}`;
  el.setAttribute("aria-label", `${kind === "video" ? "Video" : "Audio"} player for ${name}`);
  el.addEventListener("loadedmetadata", () => {
    if (seekSeconds) el.currentTime = seekSeconds;
    el.play().catch(() => {});
  });
  el.addEventListener("error", () => toast("Could not load media file"));
  body.appendChild(el);
  $("#media-backdrop").hidden = false;
  $("#media-close").focus();
}
function closeMedia() {
  const el = $("#media-body").firstChild;
  if (el && el.pause) el.pause();
  $("#media-body").innerHTML = "";
  $("#media-backdrop").hidden = true;
}
$("#media-close").addEventListener("click", closeMedia);
$("#media-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "media-backdrop") closeMedia();
});

function closeArtifact() { $("#artifact-backdrop").hidden = true; $("#artifact-body").innerHTML = ""; }
$("#artifact-close").addEventListener("click", closeArtifact);
$("#artifact-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "artifact-backdrop") closeArtifact();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#modal-backdrop").hidden) closeModal();
  if (!$("#media-backdrop").hidden) closeMedia();
  if (!$("#artifact-backdrop").hidden) closeArtifact();
});

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.chatBusy) return;
  const question = $("#chat-input").value.trim();
  if (!question || !state.current) return;
  $("#chat-input").value = "";
  state.chatBusy = true;
  $("#chat-send").disabled = true;

  addMessage("user", question);
  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("thinking");
  assistantEl.textContent = "Searching sources…";

  let citations = [];
  let fullText = "";

  try {
    const res = await fetch(`/api/notebooks/${state.current}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history: state.history }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const eventMatch = raw.match(/^event: (\w+)$/m);
        const dataMatch = raw.match(/^data: (.*)$/m);
        if (!eventMatch || !dataMatch) continue;
        const event = eventMatch[1];
        const data = JSON.parse(dataMatch[1]);

        if (event === "sources") {
          citations = data;
        } else if (event === "token") {
          if (assistantEl.classList.contains("thinking")) {
            assistantEl.classList.remove("thinking");
            assistantEl.textContent = "";
          }
          fullText += data;
          assistantEl.textContent = fullText;
          $("#messages").scrollTop = $("#messages").scrollHeight;
        } else if (event === "done") {
          fullText = data;
          renderWithCitations(assistantEl, fullText, citations);
          $("#messages").scrollTop = $("#messages").scrollHeight;
        } else if (event === "error") {
          throw new Error(data);
        }
      }
    }

    state.history.push({ role: "user", content: question });
    state.history.push({ role: "assistant", content: fullText });
  } catch (err) {
    assistantEl.classList.remove("thinking");
    assistantEl.classList.add("error");
    assistantEl.textContent = `⚠ ${err.message}`;
  } finally {
    state.chatBusy = false;
    $("#chat-send").disabled = false;
    $("#chat-input").focus();
  }
});

// ---------- Studio: Audio Overview ----------
function renderAudioList(rows) {
  const ul = $("#audio-list");
  ul.innerHTML = "";
  for (const r of rows) {
    const li = document.createElement("li");
    const title = Object.assign(document.createElement("div"), {
      className: "audio-title", textContent: r.title,
    });
    const meta = Object.assign(document.createElement("div"), {
      className: "audio-meta",
      textContent: `${Math.round(r.duration_seconds)}s · ${new Date(r.created_at).toLocaleString()}`,
    });
    const player = document.createElement("audio");
    player.controls = true;
    player.preload = "none";
    player.src = r.url;
    player.setAttribute("aria-label", `Play ${r.title}`);
    const dl = document.createElement("a");
    dl.className = "audio-download";
    dl.href = r.url;
    dl.download = r.filename;
    dl.textContent = "⬇ Download WAV";
    li.append(title, meta, player, dl);
    ul.appendChild(li);
  }
  updateStudioEmpty();
}

async function loadAudioOverviews() {
  renderAudioList(await api(`/api/notebooks/${state.current}/audio-overviews`));
}

$("#audio-overview-btn").addEventListener("click", async () => {
  if (!state.current) return;
  const btn = $("#audio-overview-btn");
  if (btn.disabled) return;
  const status = $("#audio-overview-status");
  btn.disabled = true;
  btn.classList.add("busy");
  status.textContent = "Writing script + synthesizing… (a few minutes)";
  try {
    const meta = await api(`/api/notebooks/${state.current}/audio-overview`, { method: "POST" });
    toast(`"${meta.title}" is ready`, "success");
    await loadAudioOverviews();
  } catch (err) {
    toast(`Audio Overview failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
    status.textContent = "Two-host podcast from your sources";
  }
});

// ---------- Studio: chart / infographic / spreadsheet artifacts ----------
const SVGNS = "http://www.w3.org/2000/svg";
const PALETTE = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
                 "#14b8a6", "#f43f5e", "#84cc16", "#6366f1", "#eab308", "#06b6d4"];
const ART_ICONS = { chart: "📊", infographic: "🪧", spreadsheet: "📋" };

function svgEl(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function svgText(parent, x, y, str, attrs = {}) {
  const t = svgEl("text", { x, y, fill: "#1f2430", "font-family": "sans-serif", ...attrs }, parent);
  t.textContent = str;
  return t;
}

function wrapText(str, maxChars) {
  const words = String(str).split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    if ((line + " " + w).trim().length > maxChars && line) { lines.push(line); line = w; }
    else line = (line + " " + w).trim();
  }
  if (line) lines.push(line);
  return lines;
}

function renderChart(spec) {
  const W = 640, H = 400, ml = 64, mr = 24, mt = 48, mb = 76;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, xmlns: SVGNS });
  svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#ffffff" }, svg);
  svgText(svg, W / 2, 26, spec.title, { "text-anchor": "middle", "font-size": 17, "font-weight": 700 });

  if (spec.type === "pie") {
    const total = spec.values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
    const cx = 200, cy = 215, r = 130;
    let angle = -Math.PI / 2;
    spec.values.forEach((v, i) => {
      const frac = Math.max(0, v) / total;
      const a2 = angle + frac * 2 * Math.PI;
      const large = frac > 0.5 ? 1 : 0;
      const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
      const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
      const d = frac >= 0.999
        ? `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy}`
        : `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
      svgEl("path", { d, fill: PALETTE[i % PALETTE.length], stroke: "#fff", "stroke-width": 1.5 }, svg);
      angle = a2;
    });
    spec.labels.forEach((lab, i) => {
      const y = 80 + i * 24;
      svgEl("rect", { x: 390, y: y - 11, width: 13, height: 13, rx: 3,
                      fill: PALETTE[i % PALETTE.length] }, svg);
      svgText(svg, 410, y, `${lab} — ${spec.values[i]}`, { "font-size": 12.5 });
    });
    return svg;
  }

  // bar / line share axes
  const plotW = W - ml - mr, plotH = H - mt - mb;
  const vmin = Math.min(0, ...spec.values), vmax = Math.max(...spec.values, vmin + 1e-9);
  const y = (v) => mt + plotH - ((v - vmin) / (vmax - vmin)) * plotH;
  const n = spec.values.length;

  for (let i = 0; i <= 5; i++) {
    const v = vmin + (i / 5) * (vmax - vmin);
    const yy = y(v);
    svgEl("line", { x1: ml, y1: yy, x2: W - mr, y2: yy, stroke: "#e4e2dc" }, svg);
    svgText(svg, ml - 7, yy + 4, Number(v.toPrecision(3)).toLocaleString(),
            { "text-anchor": "end", "font-size": 10.5, fill: "#5f6774" });
  }

  const rotate = spec.labels.some((l) => l.length > 7);
  spec.labels.forEach((lab, i) => {
    const xc = ml + ((i + 0.5) / n) * plotW;
    const t = svgText(svg, xc, H - mb + 18, lab, {
      "text-anchor": rotate ? "end" : "middle", "font-size": 11, fill: "#5f6774" });
    if (rotate) t.setAttribute("transform", `rotate(-30 ${xc} ${H - mb + 18})`);
  });

  if (spec.type === "bar") {
    const bw = Math.min(60, (plotW / n) * 0.64);
    spec.values.forEach((v, i) => {
      const xc = ml + ((i + 0.5) / n) * plotW;
      const yTop = Math.min(y(v), y(0)), h = Math.abs(y(v) - y(0));
      svgEl("rect", { x: xc - bw / 2, y: yTop, width: bw, height: Math.max(h, 1),
                      rx: 3, fill: PALETTE[0] }, svg);
      svgText(svg, xc, yTop - 5, Number(v).toLocaleString(),
              { "text-anchor": "middle", "font-size": 10.5, fill: "#1f2430" });
    });
  } else {
    const pts = spec.values.map((v, i) => [ml + ((i + 0.5) / n) * plotW, y(v)]);
    svgEl("polyline", { points: pts.map((p) => p.join(",")).join(" "),
                        fill: "none", stroke: PALETTE[0], "stroke-width": 2.5 }, svg);
    pts.forEach(([px, py], i) => {
      svgEl("circle", { cx: px, cy: py, r: 4, fill: PALETTE[0] }, svg);
      svgText(svg, px, py - 9, Number(spec.values[i]).toLocaleString(),
              { "text-anchor": "middle", "font-size": 10.5, fill: "#1f2430" });
    });
  }

  if (spec.y_label) {
    svgText(svg, 16, mt + plotH / 2, spec.y_label,
            { "font-size": 11.5, fill: "#5f6774", "text-anchor": "middle",
              transform: `rotate(-90 16 ${mt + plotH / 2})` });
  }
  if (spec.x_label) {
    svgText(svg, ml + plotW / 2, H - 8, spec.x_label,
            { "font-size": 11.5, fill: "#5f6774", "text-anchor": "middle" });
  }
  return svg;
}

function renderInfographic(spec) {
  const W = 640, pad = 28;
  const svg = svgEl("svg", { xmlns: SVGNS });
  const bg = svgEl("rect", { x: 0, y: 0, width: W, fill: "#ffffff" }, svg);
  let yPos = 44;

  for (const line of wrapText(spec.title, 44)) {
    svgText(svg, pad, yPos, line, { "font-size": 24, "font-weight": 800 });
    yPos += 30;
  }
  svgEl("rect", { x: pad, y: yPos - 18, width: 56, height: 5, rx: 2.5, fill: PALETTE[0] }, svg);
  yPos += 6;
  if (spec.subtitle) {
    for (const line of wrapText(spec.subtitle, 78)) {
      svgText(svg, pad, yPos, line, { "font-size": 13.5, fill: "#5f6774" });
      yPos += 19;
    }
  }
  yPos += 16;

  const stats = spec.stats.slice(0, 4);
  const cellW = (W - pad * 2) / stats.length;
  let statBottom = yPos;
  stats.forEach((s, i) => {
    const x = pad + i * cellW + cellW / 2;
    svgText(svg, x, yPos + 22, String(s.value),
            { "font-size": 27, "font-weight": 800, fill: PALETTE[0], "text-anchor": "middle" });
    let ly = yPos + 42;
    for (const line of wrapText(s.label, Math.floor(cellW / 6.2)).slice(0, 3)) {
      svgText(svg, x, ly, line, { "font-size": 11, fill: "#5f6774", "text-anchor": "middle" });
      ly += 14;
    }
    statBottom = Math.max(statBottom, ly);
  });
  yPos = statBottom + 22;

  for (const sec of spec.sections) {
    svgText(svg, pad, yPos, sec.heading, { "font-size": 15, "font-weight": 700, fill: PALETTE[0] });
    yPos += 21;
    for (const point of sec.points) {
      const lines = wrapText(point, 82);
      lines.forEach((line, li) => {
        svgText(svg, pad + 14, yPos, (li === 0 ? "" : "") + line, { "font-size": 12.5 });
        if (li === 0) svgText(svg, pad, yPos, "•", { "font-size": 12.5, fill: PALETTE[0] });
        yPos += 17;
      });
      yPos += 3;
    }
    yPos += 12;
  }
  if (spec.source_note) {
    for (const line of wrapText(`Source: ${spec.source_note}`, 92)) {
      svgText(svg, pad, yPos, line, { "font-size": 10.5, fill: "#98a3b1" });
      yPos += 14;
    }
  }
  const H = yPos + 16;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  bg.setAttribute("height", H);
  return svg;
}

function downloadBlob(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function slug(title) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 50) || "artifact";
}

function showArtifact(a) {
  $("#artifact-title").textContent = `${ART_ICONS[a.kind] || ""} ${a.title}`;
  const body = $("#artifact-body");
  const actions = $("#artifact-actions");
  body.innerHTML = "";
  actions.innerHTML = "";

  if (a.kind === "chart" || a.kind === "infographic") {
    const svg = a.kind === "chart" ? renderChart(a.spec) : renderInfographic(a.spec);
    body.appendChild(svg);
    const dl = document.createElement("button");
    dl.type = "button";
    dl.textContent = "⬇ SVG";
    dl.setAttribute("aria-label", "Download as SVG");
    dl.addEventListener("click", () => downloadBlob(
      new XMLSerializer().serializeToString(svg), `${slug(a.title)}.svg`, "image/svg+xml"));
    actions.appendChild(dl);
  } else if (a.kind === "spreadsheet") {
    const table = document.createElement("table");
    const thead = table.createTHead().insertRow();
    for (const c of a.spec.columns) {
      const th = document.createElement("th");
      th.textContent = c;
      thead.appendChild(th);
    }
    const tbody = table.createTBody();
    for (const r of a.spec.rows) {
      const tr = tbody.insertRow();
      for (const cell of r) tr.insertCell().textContent = cell ?? "";
    }
    body.appendChild(table);

    const csvBtn = document.createElement("button");
    csvBtn.type = "button";
    csvBtn.textContent = "⬇ CSV";
    csvBtn.setAttribute("aria-label", "Download as CSV");
    csvBtn.addEventListener("click", () => {
      const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const csv = [a.spec.columns, ...a.spec.rows].map((r) => r.map(esc).join(",")).join("\n");
      downloadBlob(csv, `${slug(a.title)}.csv`, "text/csv");
    });
    actions.appendChild(csvBtn);
    if (a.file_url) {
      const x = document.createElement("a");
      x.className = "audio-download";
      x.href = a.file_url;
      x.textContent = "⬇ XLSX";
      x.setAttribute("aria-label", "Download as Excel file");
      actions.appendChild(x);
    }
  }

  // the infographic SVG already prints its own source line
  if (a.spec.source_note && a.kind !== "infographic") {
    const note = document.createElement("div");
    note.className = "source-note";
    note.textContent = `Source: ${a.spec.source_note}`;
    body.appendChild(note);
  }
  $("#artifact-backdrop").hidden = false;
  $("#artifact-close").focus();
}

async function loadArtifacts() {
  const rows = await api(`/api/notebooks/${state.current}/artifacts`);
  const ul = $("#artifact-list");
  ul.innerHTML = "";
  for (const a of rows) {
    const li = document.createElement("li");
    li.append(Object.assign(document.createElement("span"),
                            { textContent: ART_ICONS[a.kind] || "📦" }));
    const title = document.createElement("button");
    title.type = "button";
    title.className = "art-title";
    title.textContent = a.title;
    title.title = `View ${a.kind}`;
    title.addEventListener("click", () => showArtifact(a));
    li.append(title);
    const del = document.createElement("button");
    del.className = "icon-btn";
    del.title = "Delete artifact";
    del.setAttribute("aria-label", `Delete ${a.kind} ${a.title}`);
    del.textContent = "✕";
    del.addEventListener("click", async () => {
      if (!confirm(`Delete ${a.kind} "${a.title}"?`)) return;
      del.disabled = true;
      try {
        await api(`/api/notebooks/${state.current}/artifacts/${a.id}`, { method: "DELETE" });
      } catch (err) { toast(err.message); }
      await loadArtifacts();
    });
    li.append(del);
    ul.appendChild(li);
  }
  updateStudioEmpty();
}

function updateStudioEmpty() {
  const empty = !$("#audio-list").children.length && !$("#artifact-list").children.length;
  $("#studio-empty").style.display = empty ? "block" : "none";
}

const KIND_LABELS = { chart: "Chart", infographic: "Infographic", spreadsheet: "Spreadsheet" };
for (const btn of document.querySelectorAll(".artifact-buttons button")) {
  btn.addEventListener("click", async () => {
    if (!state.current || btn.disabled) return;
    const kind = btn.dataset.kind;
    const all = document.querySelectorAll(".artifact-buttons button");
    all.forEach((b) => (b.disabled = true));
    btn.classList.add("busy");
    const prog = $("#artifact-progress");
    prog.hidden = false;
    prog.textContent = `Generating ${KIND_LABELS[kind].toLowerCase()} from your sources…`;
    try {
      const artifact = await api(`/api/notebooks/${state.current}/artifacts`, {
        method: "POST",
        body: JSON.stringify({ kind }),
      });
      toast(`${KIND_LABELS[kind]} "${artifact.title}" is ready`, "success");
      await loadArtifacts();
      showArtifact(artifact);
    } catch (err) {
      toast(`${KIND_LABELS[kind]} failed: ${err.message}`);
    } finally {
      all.forEach((b) => (b.disabled = false));
      btn.classList.remove("busy");
      prog.hidden = true;
    }
  });
}

// ---------- Init ----------
(async () => {
  await loadHealth();
  try {
    await loadNotebooks();
  } catch (err) {
    toast(`Could not load notebooks: ${err.message}`);
  }
})();
