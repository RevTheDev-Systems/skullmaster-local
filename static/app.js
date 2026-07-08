// SkullMaster Local — frontend
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
applyTheme(localStorage.getItem("skullmaster-theme") || "light");
$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

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
  await Promise.all([loadSources(), loadMessages(), loadAudioOverviews()]);
}

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
    const icon = s.kind === "pdf" ? "📕" : s.kind === "url" ? "🔗" : s.kind === "docx" ? "📘" : "📄";
    const meta = s.status === "failed" ? "failed"
      : s.pages ? `${s.pages}p · ${s.chunk_count} chunks` : `${s.chunk_count} chunks`;

    const name = document.createElement("span");
    name.className = "src-name";
    name.title = s.origin || s.name;
    name.textContent = s.name;

    li.append(Object.assign(document.createElement("span"), { textContent: icon }));
    li.append(name);
    li.append(Object.assign(document.createElement("span"), { className: "src-meta", textContent: meta }));

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
  $("#modal-title").textContent = c.page ? `${c.source_name} — page ${c.page}` : c.source_name;
  $("#modal-body").textContent = c.text;
  $("#modal-backdrop").hidden = false;
  $("#modal-close").focus();
}
function closeModal() { $("#modal-backdrop").hidden = true; }
$("#modal-close").addEventListener("click", closeModal);
$("#modal-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "modal-backdrop") closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal-backdrop").hidden) closeModal();
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
  $("#audio-empty").style.display = rows.length ? "none" : "block";
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

// ---------- Init ----------
(async () => {
  await loadHealth();
  try {
    await loadNotebooks();
  } catch (err) {
    toast(`Could not load notebooks: ${err.message}`);
  }
})();
