// SkullMaster iQ — frontend
const $ = (sel) => document.querySelector(sel);

const state = {
  notebooks: [],
  current: null,        // notebook id
  history: [],          // [{role, content}] for model context
  chatBusy: false,
  ingestBusy: false,
  chatModel: null,      // active chat model, mirrored from /api/models
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
  if (res.status === 401) {
    window.location.replace("/login");
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
}

// ---------- Sign out ----------
$("#sign-out").addEventListener("click", async () => {
  const btn = $("#sign-out");
  btn.disabled = true;
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch { /* falling through to /login is the right outcome either way */ }
  window.location.replace("/login");
});

// ---------- Models & readiness ----------
const ENGINE_NAMES = { ollama: "Ollama", mlx: "MLX" };

function setStatus(state, detail) {
  const dot = $("#status-dot");
  dot.classList.toggle("ok", state === "ok");
  dot.classList.toggle("err", state === "err");
  dot.title = detail;
  dot.setAttribute("aria-label", detail);

  // Only surface a written message when something is actually wrong.
  const badge = $("#model-badge");
  badge.hidden = state !== "err";
  badge.classList.toggle("err", state === "err");
  if (state === "err") badge.textContent = detail;
}

function problemFromHealth(h) {
  if (!h.llm.reachable) return "Model backend unreachable";
  if (!h.llm.chat_model_ready) return `${h.llm.chat_model} is not installed`;
  if (!h.llm.embed_model_ready) return `${h.llm.embed_model} is not installed`;
  if (!h.vector_store?.ready) return "Vector store unavailable";
  if (!h.database?.ready) return "Local database unavailable";
  return "Degraded — run diagnostics";
}

async function loadModels({ notify = false, refresh = false } = {}) {
  const select = $("#model-select");
  const btn = $("#health-refresh");
  btn.disabled = true;
  btn.classList.add("spinning");
  // Guarantee the spin is perceptible even when the backend answers instantly.
  const minSpin = new Promise((r) => setTimeout(r, 450));

  try {
    // Report a broken model list instead of silently emptying the picker —
    // e.g. when the server process predates the endpoint and needs a restart.
    let modelsError = null;
    const [health, models] = await Promise.all([
      api("/api/health"),
      // refresh=1 bypasses the server's cached per-model metadata (used by the
      // refresh button after a model was pulled/removed).
      api(`/api/models${refresh ? "?refresh=1" : ""}`)
        .catch((err) => { modelsError = err.message; return null; }),
    ]);

    if (modelsError) {
      setStatus("err", `Model list unavailable: ${modelsError}. Restart the server if it's running older code.`);
      select.innerHTML = "";
      select.disabled = true;
      if (notify) {
        await minSpin;
        toast(`Could not load models: ${modelsError}`);
      }
      return;
    }

    if (models) {
      const chatModels = models.models.filter((m) => m.can_chat);
      select.innerHTML = "";

      // Group by engine only when more than one is available.
      const byBackend = new Map();
      for (const m of chatModels) {
        const key = m.backend || "ollama";
        if (!byBackend.has(key)) byBackend.set(key, []);
        byBackend.get(key).push(m);
      }
      for (const [key, list] of byBackend) {
        const parent = byBackend.size > 1
          ? Object.assign(document.createElement("optgroup"), { label: ENGINE_NAMES[key] || key })
          : select;
        for (const m of list) {
          const opt = document.createElement("option");
          opt.value = m.name;
          // MLX ids carry an org prefix ("mlx-community/…") that adds no meaning here.
          opt.textContent = (m.label || m.name).split("/").pop();
          const caps = (m.capabilities || []).join(", ");
          opt.title = `${m.label || m.name}${caps ? ` · ${caps}` : ""}`;
          opt.dataset.backend = key;
          parent.appendChild(opt);
        }
        if (parent !== select) select.appendChild(parent);
      }

      select.value = models.chat_model;
      select.disabled = chatModels.length === 0;
      state.chatModel = models.chat_model;
    } else {
      select.innerHTML = "";
      select.disabled = true;
    }

    setStatus(health.ok ? "ok" : "err",
              health.ok ? `Ready · ${health.llm.chat_model}` : problemFromHealth(health));
    // A fallback (e.g. a saved MLX model while MLX is offline) is not an error,
    // but the user should know which model is actually answering.
    if (models?.warning) toast(models.warning);
    if (notify) {
      await minSpin;
      toast(health.ok ? `Models refreshed · ${models?.models.length ?? 0} installed`
                      : problemFromHealth(health),
            health.ok ? "success" : "error");
    }
  } catch (err) {
    setStatus("err", err.message || "Backend unreachable");
    select.disabled = true;
    if (notify) {
      await minSpin;
      toast(`Refresh failed: ${err.message}`);
    }
  } finally {
    await minSpin;
    btn.disabled = false;
    btn.classList.remove("spinning");
  }
}

$("#health-refresh").addEventListener("click", () => loadModels({ notify: true, refresh: true }));

$("#model-select").addEventListener("change", async (e) => {
  const name = e.target.value;
  const option = e.target.selectedOptions[0];
  const label = option?.textContent || name;
  const previous = state.chatModel;
  e.target.disabled = true;
  try {
    const res = await api("/api/models/chat", { method: "POST", body: JSON.stringify({ name }) });
    const active = res.chat_model || name;
    const activeLabel = (active === name) ? label : active.split("::").pop();
    state.chatModel = active;
    e.target.value = active;
    if (res.warning) {
      setStatus("err", res.warning);
      toast(res.warning);
    } else {
      setStatus("ok", `Ready · ${activeLabel}`);
      toast(option?.dataset.backend === "mlx"
        ? `Now using ${activeLabel} — your first message loads it into memory`
        : `Now using ${activeLabel}`, "success");
    }
  } catch (err) {
    e.target.value = previous || "";
    toast(`Could not switch model: ${err.message}`);
  } finally {
    e.target.disabled = false;
  }
});

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
    if (m.role === "assistant" && m.status && m.status !== "completed") {
      el.classList.add("interrupted");
      const note = document.createElement("div");
      note.className = "msg-note";
      note.textContent = m.content
        ? "Interrupted — this answer may be incomplete."
        : "Interrupted before an answer was produced.";
      el.appendChild(note);
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
  const prefix = c.notebook_name ? `${c.notebook_name} › ` : "";
  const where = isMedia && c.page != null
    ? `${c.source_name} — at ${fmtTime(c.page)}`
    : c.page ? `${c.source_name} — page ${c.page}` : c.source_name;
  $("#modal-title").textContent = prefix + where;
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
  const research = $("#research-all").checked;
  if (!question || (!research && !state.current)) return;
  $("#chat-input").value = "";
  state.chatBusy = true;
  $("#chat-send").disabled = true;

  addMessage("user", question);
  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("thinking");
  assistantEl.textContent = research ? "Searching all notebooks…" : "Searching sources…";

  let citations = [];
  let fullText = "";

  try {
    const url = research ? "/api/research" : `/api/notebooks/${state.current}/chat`;
    const body = research
      ? { question, notebook_ids: [], history: state.history }
      : { question, history: state.history };
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) { window.location.replace("/login"); return; }
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

    if (research && fullText) {
      const note = document.createElement("div");
      note.className = "msg-note";
      note.textContent = "Researched across all notebooks — not saved to this notebook.";
      assistantEl.appendChild(note);
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

$("#research-all").addEventListener("change", (e) => {
  $("#chat-input").placeholder = e.target.checked
    ? "Ask across all notebooks…"
    : "Ask about your sources…";
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
// Knowledge-graph typing (mirrors app/studio.py vocabularies).
const ENTITY_COLOURS = {
  concept: "#64748b", organization: "#4f46e5", person: "#8b5cf6",
  location: "#0ea5e9", date: "#f59e0b", metric: "#10b981",
  event: "#ef4444", document: "#6366f1",
};
const RELATION_STYLES = {
  related_to: { dash: "5 4", colour: "#5f7390" },
  part_of: { dash: "", colour: "#4f46e5" },
  causes: { dash: "", colour: "#ef4444" },
  measures: { dash: "2 3", colour: "#10b981" },
  located_at: { dash: "", colour: "#0ea5e9" },
  enables: { dash: "8 4", colour: "#8b5cf6" },
  contradicts: { dash: "2 2", colour: "#ef4444" },
  precedes: { dash: "10 4", colour: "#f59e0b" },
};
const ART_ICONS = {
  chart: "📊", infographic: "🪧", spreadsheet: "📋", mindgraph: "🧠",
  comparison: "⚖️",
  briefing: "📄", study_guide: "🎓", faq: "❓", timeline: "🕒", source_summary: "📝",
};
const SVG_RENDERERS = {
  chart: renderChart,
  infographic: renderInfographic,
  mindgraph: renderMindGraph,
};
const TEXT_RENDERERS = {
  comparison: renderComparison,
  briefing: renderBriefing,
  study_guide: renderStudyGuide,
  faq: renderFaq,
  timeline: renderTimeline,
  source_summary: renderSourceSummary,
};
const STANCE_LABELS = { agree: "Agrees", differ: "Differs", adds: "Adds" };

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

  // Render every accepted stat (the validator allows up to 6) — never silently
  // discard accepted information. Beyond one row of four they wrap to rows of 3.
  const stats = spec.stats;
  const perRow = stats.length <= 4 ? stats.length : 3;
  const cellW = (W - pad * 2) / perRow;
  const rowH = 74;
  let statBottom = yPos;
  stats.forEach((s, i) => {
    const col = i % perRow;
    const row = Math.floor(i / perRow);
    const x = pad + col * cellW + cellW / 2;
    const y = yPos + row * rowH;
    svgText(svg, x, y + 22, String(s.value),
            { "font-size": 27, "font-weight": 800, fill: PALETTE[0], "text-anchor": "middle" });
    let ly = y + 42;
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

function renderMindGraph(spec) {
  // Radial layout: root at centre, branches on a ring, children fanned out
  // within their branch's angular slice. Deterministic — no physics engine.
  const W = 1040, H = 800, cx = W / 2;
  const cy = H / 2 + 14;          // nudge down so the top ring clears the title
  const BRANCH_R = 165, CHILD_R = 288;
  // Neighbouring children collide once their pills get wide, so walk them
  // through three radii instead of sitting them all on one ring.
  const RING_STAGGER = 30;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, xmlns: SVGNS });
  svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#ffffff" }, svg);
  svgText(svg, cx, 30, spec.title, {
    "text-anchor": "middle", "font-size": 18, "font-weight": 700 });

  const edges = svgEl("g", {}, svg);
  const nodes = svgEl("g", {}, svg);

  const clip = (text, maxChars) =>
    text.length > maxChars ? `${text.slice(0, maxChars - 1)}…` : text;
  const pillSize = (text, size) => ({
    w: Math.max(46, text.length * size * 0.58 + 18),
    h: size + 14,
  });

  const drawPill = (node, { fill, textColor, size, bold, stroke }) => {
    svgEl("rect", { x: node.x - node.w / 2, y: node.y - node.h / 2,
                    width: node.w, height: node.h, rx: node.h / 2,
                    fill, "stroke-width": stroke ? 2 : 1,
                    stroke: stroke || "rgba(15,27,45,0.14)" }, nodes);
    const t = svgText(nodes, node.x, node.y + size * 0.35, node.text, {
      "text-anchor": "middle", "font-size": size, fill: textColor });
    if (bold) t.setAttribute("font-weight", "700");
    const title = svgEl("title", {}, t);
    title.textContent = node.full;      // untruncated label on hover
  };

  // ---- 1. place branches and children ----
  const branches = spec.branches;
  const nodeTypes = spec.node_types || {};
  const positions = new Map();          // label -> node (for cross-links)
  const branchNodes = [];
  const childNodes = [];
  const start = -Math.PI / 2;           // first branch straight up

  branches.forEach((branch, bi) => {
    const angle = start + (bi / branches.length) * 2 * Math.PI;
    const text = clip(branch.label, 22);
    const node = {
      text, full: branch.label,
      colour: ENTITY_COLOURS[nodeTypes[branch.label]] || PALETTE[bi % PALETTE.length],
      x: cx + BRANCH_R * Math.cos(angle), y: cy + BRANCH_R * Math.sin(angle),
      ...pillSize(text, 12),
    };
    branchNodes.push(node);
    positions.set(branch.label, node);

    const slice = (2 * Math.PI) / branches.length;
    const n = branch.children.length;
    branch.children.forEach((child, ci) => {
      const offset = n === 1 ? 0 : (ci / (n - 1) - 0.5) * slice * 0.78;
      const a = angle + offset;
      const r = CHILD_R + (ci % 3) * RING_STAGGER;
      const ctext = clip(child, 32);
      const cnode = {
        text: ctext, full: child, parent: node, a, r,
        etype: nodeTypes[child],
        x: cx + r * Math.cos(a), y: cy + r * Math.sin(a),
        ...pillSize(ctext, 11),
      };
      childNodes.push(cnode);
      positions.set(child, cnode);
    });
  });

  // ---- 2. resolve leftover collisions by pushing the outer pill further out ----
  const overlaps = (a, b) =>
    Math.abs(a.x - b.x) < (a.w + b.w) / 2 + 6 &&
    Math.abs(a.y - b.y) < (a.h + b.h) / 2 + 4;

  for (let pass = 0; pass < 24; pass++) {
    let moved = false;
    for (let i = 0; i < childNodes.length; i++) {
      for (let j = i + 1; j < childNodes.length; j++) {
        const a = childNodes[i], b = childNodes[j];
        if (!overlaps(a, b)) continue;
        const outer = a.r >= b.r ? a : b;
        outer.r += 14;
        outer.x = cx + outer.r * Math.cos(outer.a);
        outer.y = cy + outer.r * Math.sin(outer.a);
        moved = true;
      }
    }
    if (!moved) break;
  }

  // Keep everything inside the canvas after the pushes.
  let maxReach = 0;
  for (const n of childNodes) {
    maxReach = Math.max(maxReach,
      Math.abs(n.x - cx) + n.w / 2 + 12, (Math.abs(n.y - cy) + n.h / 2 + 12) * (W / H));
  }
  const limit = W / 2;
  if (maxReach > limit) {
    const k = limit / maxReach;
    for (const n of childNodes) {
      n.x = cx + (n.x - cx) * k;
      n.y = cy + (n.y - cy) * k;
    }
  }

  // ---- 3. edges, then cross-links, then nodes on top ----
  for (const b of branchNodes) {
    svgEl("path", {
      d: `M ${cx} ${cy} Q ${(cx + b.x) / 2 + (b.y - cy) * 0.12} ${(cy + b.y) / 2 - (b.x - cx) * 0.12} ${b.x} ${b.y}`,
      fill: "none", stroke: b.colour, "stroke-width": 2.4, opacity: 0.75,
    }, edges);
  }
  for (const c of childNodes) {
    svgEl("path", {
      d: `M ${c.parent.x} ${c.parent.y} Q ${(c.parent.x + c.x) / 2} ${(c.parent.y + c.y) / 2} ${c.x} ${c.y}`,
      fill: "none", stroke: c.parent.colour, "stroke-width": 1.5, opacity: 0.5,
    }, edges);
  }

  const usedRelationTypes = new Set();
  for (const link of spec.links || []) {
    const a = positions.get(link.from), b = positions.get(link.to);
    if (!a || !b) continue;
    const style = RELATION_STYLES[link.type] || RELATION_STYLES.related_to;
    usedRelationTypes.add(link.type in RELATION_STYLES ? link.type : "related_to");
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const dx = mx - cx, dy = my - cy, len = Math.hypot(dx, dy) || 1;
    const qx = mx + (dx / len) * 42, qy = my + (dy / len) * 42;
    const edge = svgEl("path", { d: `M ${a.x} ${a.y} Q ${qx} ${qy} ${b.x} ${b.y}`,
                    fill: "none", stroke: style.colour, "stroke-width": 1.6,
                    "stroke-dasharray": style.dash, opacity: 0.85 }, edges);
    const label = link.label || link.type;
    if (label) {
      const t = svgText(edges, qx, qy - 4, label, {
        "text-anchor": "middle", "font-size": 10, fill: style.colour });
      t.setAttribute("font-weight", "600");
      const title = svgEl("title", {}, edge);
      title.textContent = `${link.from} —${link.type}→ ${link.to}`;
    }
  }

  for (const c of childNodes) {
    drawPill(c, {
      fill: "#f4f7fc", textColor: "#1f2430", size: 11, bold: false,
      stroke: ENTITY_COLOURS[c.etype] || "rgba(15,27,45,0.14)",
    });
  }
  for (const b of branchNodes) {
    drawPill(b, { fill: b.colour, textColor: "#ffffff", size: 12, bold: true });
  }
  const rootText = clip(spec.root, 26);
  drawPill({ text: rootText, full: spec.root, x: cx, y: cy, ...pillSize(rootText, 14) },
           { fill: "#1f2430", textColor: "#ffffff", size: 14, bold: true });

  // ---- 4. legend: the entity and relation types actually present ----
  const entityTypes = [...new Set(Object.values(nodeTypes))]
    .filter((t) => t in ENTITY_COLOURS);
  const legend = svgEl("g", {}, svg);
  let ly = H - 12 - 16 * (entityTypes.length + usedRelationTypes.size);
  for (const type of entityTypes) {
    svgEl("circle", { cx: 22, cy: ly, r: 5, fill: ENTITY_COLOURS[type] }, legend);
    svgText(legend, 33, ly + 4, type, { "font-size": 11, fill: "#5f6774" });
    ly += 16;
  }
  for (const type of usedRelationTypes) {
    const style = RELATION_STYLES[type];
    svgEl("line", { x1: 15, y1: ly, x2: 29, y2: ly, stroke: style.colour,
                    "stroke-width": 2, "stroke-dasharray": style.dash }, legend);
    svgText(legend, 33, ly + 4, type, { "font-size": 11, fill: "#5f6774" });
    ly += 16;
  }

  return svg;
}

// ---- grounded text artifacts (briefing / study guide / FAQ / timeline / summary) ----

function artDoc() {
  const root = document.createElement("div");
  root.className = "art-doc";
  return root;
}

function artHeading(root, text, tag = "h3") {
  const h = document.createElement(tag);
  h.textContent = text;
  root.appendChild(h);
  return h;
}

function artPara(root, text) {
  const p = document.createElement("p");
  p.textContent = text;
  root.appendChild(p);
  return p;
}

function artList(root, items) {
  const ul = document.createElement("ul");
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    ul.appendChild(li);
  }
  root.appendChild(ul);
  return ul;
}

function renderBriefing(spec) {
  const root = artDoc();
  for (const s of spec.sections) { artHeading(root, s.heading); artPara(root, s.body); }
  return root;
}

function renderStudyGuide(spec) {
  const root = artDoc();
  artHeading(root, "Objectives");
  artList(root, spec.objectives);
  artHeading(root, "Key concepts");
  const dl = document.createElement("dl");
  for (const k of spec.key_concepts) {
    const dt = document.createElement("dt"); dt.textContent = k.term;
    const dd = document.createElement("dd"); dd.textContent = k.definition;
    dl.append(dt, dd);
  }
  root.appendChild(dl);
  artHeading(root, "Practice questions");
  for (const q of spec.questions) {
    artHeading(root, q.q, "h4");
    artPara(root, q.a);
  }
  return root;
}

function renderFaq(spec) {
  const root = artDoc();
  for (const it of spec.items) { artHeading(root, it.question, "h4"); artPara(root, it.answer); }
  return root;
}

function renderTimeline(spec) {
  const root = artDoc();
  const ul = document.createElement("ul");
  ul.className = "art-timeline";
  for (const ev of spec.events) {
    const li = document.createElement("li");
    const when = document.createElement("span");
    when.className = "art-time";
    when.textContent = ev.date;
    const what = document.createElement("span");
    what.textContent = ev.event;
    li.append(when, what);
    ul.appendChild(li);
  }
  root.appendChild(ul);
  return root;
}

function renderSourceSummary(spec) {
  const root = artDoc();
  artPara(root, spec.summary);
  artHeading(root, "Key points");
  artList(root, spec.key_points);
  return root;
}

function renderComparison(spec) {
  const root = artDoc();
  for (const topic of spec.topics) {
    artHeading(root, topic.topic);
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const label of ["Source", "Relation", "Claim"]) {
      const th = document.createElement("th");
      th.textContent = label;
      head.appendChild(th);
    }
    const tbody = table.createTBody();
    for (const p of topic.positions) {
      const tr = tbody.insertRow();
      tr.insertCell().textContent = p.source;
      const stance = document.createElement("span");
      stance.className = `stance stance-${p.stance}`;
      stance.textContent = STANCE_LABELS[p.stance] || p.stance;
      tr.insertCell().appendChild(stance);
      tr.insertCell().textContent = p.claim;
    }
    root.appendChild(table);
  }
  return root;
}

function renderEvidence(artifact) {
  const spec = artifact.spec || {};
  const evidence = spec.evidence || {};
  const wrap = document.createElement("div");
  wrap.className = "art-evidence";

  const heading = document.createElement("h3");
  heading.textContent = "Evidence";
  wrap.appendChild(heading);
  const nodes = document.createElement("ul");
  for (const [label, ev] of Object.entries(evidence)) {
    const li = document.createElement("li");
    const term = document.createElement("strong");
    term.textContent = label;
    const where = document.createElement("span");
    where.textContent = ev.page != null
      ? ` — ${ev.source} — page ${ev.page}`
      : ` — ${ev.source}`;
    li.append(term, where);
    nodes.appendChild(li);
  }
  if (!Object.keys(evidence).length) {
    const li = document.createElement("li");
    li.textContent = "No node could be traced to a source passage.";
    nodes.appendChild(li);
  }
  wrap.appendChild(nodes);

  const links = spec.links || [];
  if (links.length) {
    const relations = document.createElement("h3");
    relations.textContent = "Relations";
    wrap.appendChild(relations);
    const list = document.createElement("ul");
    for (const link of links) {
      const li = document.createElement("li");
      const term = document.createElement("strong");
      term.textContent = `${link.from} — ${link.type || "related_to"} → ${link.to}`;
      li.appendChild(term);
      const ev = link.evidence;
      if (ev && ev.source) {
        const span = document.createElement("span");
        span.textContent = ev.page != null
          ? ` — ${ev.source} — page ${ev.page}`
          : ` — ${ev.source}`;
        li.appendChild(span);
      }
      list.appendChild(li);
    }
    wrap.appendChild(list);
  }
  return wrap;
}

function artifactToMarkdown(kind, spec) {
  const lines = [`# ${spec.title}`, ""];
  if (kind === "briefing") {
    for (const s of spec.sections) lines.push(`## ${s.heading}`, "", s.body, "");
  } else if (kind === "study_guide") {
    lines.push("## Objectives", "");
    spec.objectives.forEach((o) => lines.push(`- ${o}`));
    lines.push("", "## Key concepts", "");
    spec.key_concepts.forEach((k) => lines.push(`**${k.term}** — ${k.definition}`));
    lines.push("", "## Practice questions", "");
    spec.questions.forEach((q) => lines.push(`**${q.q}**`, "", q.a, ""));
  } else if (kind === "faq") {
    spec.items.forEach((it) => lines.push(`**${it.question}**`, "", it.answer, ""));
  } else if (kind === "timeline") {
    spec.events.forEach((ev) => lines.push(`- **${ev.date}** — ${ev.event}`));
  } else if (kind === "source_summary") {
    lines.push(spec.summary, "", "## Key points", "");
    spec.key_points.forEach((p) => lines.push(`- ${p}`));
  } else if (kind === "comparison") {
    for (const t of spec.topics) {
      lines.push(`## ${t.topic}`, "");
      t.positions.forEach((p) => lines.push(`- **${p.source}** (${p.stance}) — ${p.claim}`));
      lines.push("");
    }
  }
  if (spec.source_note) lines.push("", `_Source: ${spec.source_note}_`);
  return lines.join("\n");
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

  if (SVG_RENDERERS[a.kind]) {
    const svg = SVG_RENDERERS[a.kind](a.spec);
    body.appendChild(svg);
    const dl = document.createElement("button");
    dl.type = "button";
    dl.textContent = "⬇ SVG";
    dl.setAttribute("aria-label", "Download as SVG");
    dl.addEventListener("click", () => downloadBlob(
      new XMLSerializer().serializeToString(svg), `${slug(a.title)}.svg`, "image/svg+xml"));
    actions.appendChild(dl);
    if (a.kind === "mindgraph") {
      body.appendChild(renderEvidence(a));
    }
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
  } else if (TEXT_RENDERERS[a.kind]) {
    body.appendChild(TEXT_RENDERERS[a.kind](a.spec));
    const md = document.createElement("button");
    md.type = "button";
    md.textContent = "⬇ Markdown";
    md.setAttribute("aria-label", "Download as Markdown");
    md.addEventListener("click", () => downloadBlob(
      artifactToMarkdown(a.kind, a.spec), `${slug(a.title)}.md`, "text/markdown"));
    actions.appendChild(md);
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

const KIND_LABELS = {
  chart: "Chart", infographic: "Infographic",
  spreadsheet: "Spreadsheet", mindgraph: "Mind Graph",
  comparison: "Source Comparison",
  briefing: "Briefing", study_guide: "Study Guide", faq: "FAQ",
  timeline: "Timeline", source_summary: "Source Summary",
};
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

// ---------- Library search ----------
function closeSearch() {
  $("#search-backdrop").hidden = true;
}
$("#library-search").addEventListener("click", () => {
  $("#search-backdrop").hidden = false;
  $("#search-input").focus();
  $("#search-input").select();
});
$("#search-close").addEventListener("click", closeSearch);
$("#search-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "search-backdrop") closeSearch();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#search-backdrop").hidden) closeSearch();
});

function renderSearch(data) {
  const box = $("#search-results");
  box.innerHTML = "";
  if (!data.results.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = `No matches for “${data.query}”.`;
    box.appendChild(empty);
    return;
  }
  const byNotebook = new Map();
  for (const result of data.results) {
    const key = result.notebook_name || result.notebook_id;
    if (!byNotebook.has(key)) byNotebook.set(key, []);
    byNotebook.get(key).push(result);
  }
  for (const [name, hits] of byNotebook) {
    const heading = document.createElement("div");
    heading.className = "search-notebook";
    heading.textContent = `${name} · ${hits.length}`;
    box.appendChild(heading);
    for (const hit of hits) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "search-hit";
      const where = document.createElement("span");
      where.className = "search-where";
      const isMedia = hit.kind === "video" || hit.kind === "audio";
      where.textContent = isMedia && hit.page != null
        ? `${hit.source_name} — at ${fmtTime(hit.page)}`
        : hit.page ? `${hit.source_name} — page ${hit.page}` : hit.source_name;
      const snippet = document.createElement("span");
      snippet.className = "search-snippet";
      snippet.textContent = hit.text.slice(0, 200);
      btn.append(where, snippet);
      btn.addEventListener("click", async () => {
        closeSearch();
        if (hit.notebook_id && hit.notebook_id !== state.current) {
          state.current = hit.notebook_id;
          const sel = $("#nb-select");
          if (sel) sel.value = hit.notebook_id;
          await openNotebook().catch((err) => toast(err.message));
        }
        showCitation(hit);
      });
      box.appendChild(btn);
    }
  }
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#search-input").value.trim();
  if (!q) return;
  const box = $("#search-results");
  box.innerHTML = '<p class="muted">Searching…</p>';
  try {
    renderSearch(await api(`/api/search?q=${encodeURIComponent(q)}&k=12`));
  } catch (err) {
    box.innerHTML = "";
    const problem = document.createElement("p");
    problem.className = "muted";
    problem.textContent = err.message;
    box.appendChild(problem);
  }
});

// ---------- Init ----------
(async () => {
  await loadModels();
  try {
    await loadNotebooks();
  } catch (err) {
    toast(`Could not load notebooks: ${err.message}`);
  }
})();
