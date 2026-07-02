// NotebookLM Local — frontend
const $ = (sel) => document.querySelector(sel);

const state = {
  notebooks: [],
  current: null,        // notebook id
  history: [],          // [{role, content}]
  citations: {},        // per-message: msgId -> [{n, source_name, page, text}]
  msgCounter: 0,
};

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

// ---------- Notebooks ----------
async function loadNotebooks() {
  state.notebooks = await api("/api/notebooks");
  const sel = $("#nb-select");
  sel.innerHTML = "";
  for (const nb of state.notebooks) {
    const opt = document.createElement("option");
    opt.value = nb.id;
    opt.textContent = `${nb.name} (${nb.source_count})`;
    sel.appendChild(opt);
  }
  if (state.notebooks.length === 0) {
    const nb = await api("/api/notebooks", { method: "POST", body: JSON.stringify({ name: "My Notebook" }) });
    return loadNotebooks();
  }
  if (!state.current || !state.notebooks.find((n) => n.id === state.current)) {
    state.current = state.notebooks[0].id;
  }
  sel.value = state.current;
  await loadSources();
}

$("#nb-select").addEventListener("change", async (e) => {
  state.current = e.target.value;
  state.history = [];
  $("#messages").innerHTML = "";
  await loadSources();
});

$("#nb-new").addEventListener("click", async () => {
  const name = prompt("Notebook name:");
  if (!name) return;
  const nb = await api("/api/notebooks", { method: "POST", body: JSON.stringify({ name }) });
  state.current = nb.id;
  state.history = [];
  $("#messages").innerHTML = "";
  await loadNotebooks();
});

$("#nb-delete").addEventListener("click", async () => {
  if (!state.current) return;
  const nb = state.notebooks.find((n) => n.id === state.current);
  if (!confirm(`Delete notebook "${nb.name}" and all its sources?`)) return;
  await api(`/api/notebooks/${state.current}`, { method: "DELETE" });
  state.current = null;
  state.history = [];
  $("#messages").innerHTML = "";
  await loadNotebooks();
});

// ---------- Sources ----------
async function loadSources() {
  const sources = await api(`/api/notebooks/${state.current}/sources`);
  const ul = $("#source-list");
  ul.innerHTML = "";
  for (const s of sources) {
    const li = document.createElement("li");
    const icon = s.kind === "pdf" ? "📕" : s.kind === "url" ? "🔗" : "📄";
    const meta = s.pages ? `${s.pages}p · ${s.chunk_count}ch` : `${s.chunk_count}ch`;
    li.innerHTML = `<span>${icon}</span><span class="src-name" title="${s.name}">${s.name}</span>
      <span class="src-meta">${meta}</span><button class="src-del" title="Remove">✕</button>`;
    li.querySelector(".src-del").addEventListener("click", async () => {
      await api(`/api/notebooks/${state.current}/sources/${s.id}`, { method: "DELETE" });
      await loadSources();
    });
    ul.appendChild(li);
  }
  $("#sources-hint").style.display = sources.length ? "none" : "block";
}

$("#file-input").addEventListener("change", async (e) => {
  for (const file of e.target.files) {
    const hint = $("#sources-hint");
    hint.style.display = "block";
    hint.textContent = `Indexing ${file.name}…`;
    const fd = new FormData();
    fd.append("file", file);
    try {
      await fetch(`/api/notebooks/${state.current}/sources/file`, { method: "POST", body: fd })
        .then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail); });
    } catch (err) {
      alert(`${file.name}: ${err.message}`);
    }
    hint.textContent = "";
  }
  e.target.value = "";
  await loadSources();
});

$("#url-add").addEventListener("click", async () => {
  const url = $("#url-input").value.trim();
  if (!url) return;
  const hint = $("#sources-hint");
  hint.style.display = "block";
  hint.textContent = "Fetching URL…";
  try {
    await api(`/api/notebooks/${state.current}/sources/url`, {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    $("#url-input").value = "";
  } catch (err) {
    alert(err.message);
  }
  hint.textContent = "";
  await loadSources();
});

// ---------- Chat ----------
function addMessage(role, text = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.dataset.msgId = ++state.msgCounter;
  div.textContent = text;
  $("#messages").appendChild(div);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return div;
}

function renderWithCitations(el, text, citations) {
  el.innerHTML = "";
  const parts = text.split(/(\[\d+\])/g);
  for (const part of parts) {
    const m = part.match(/^\[(\d+)\]$/);
    if (m && citations.find((c) => c.n === Number(m[1]))) {
      const chip = document.createElement("span");
      chip.className = "cite";
      chip.textContent = m[1];
      chip.addEventListener("click", () => showCitation(citations.find((c) => c.n === Number(m[1]))));
      el.appendChild(chip);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  }
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function showCitation(c) {
  $("#modal-title").textContent = c.page ? `${c.source_name} — page ${c.page}` : c.source_name;
  $("#modal-body").textContent = c.text;
  $("#modal-backdrop").hidden = false;
}
$("#modal-close").addEventListener("click", () => ($("#modal-backdrop").hidden = true));
$("#modal-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "modal-backdrop") $("#modal-backdrop").hidden = true;
});

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("#chat-input").value.trim();
  if (!question || !state.current) return;
  $("#chat-input").value = "";
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
        }
      }
    }

    state.history.push({ role: "user", content: question });
    state.history.push({ role: "assistant", content: fullText });
  } catch (err) {
    assistantEl.classList.remove("thinking");
    assistantEl.textContent = `⚠ ${err.message}`;
  } finally {
    $("#chat-send").disabled = false;
    $("#chat-input").focus();
  }
});

// ---------- Studio: Audio Overview ----------
$("#audio-overview-btn").addEventListener("click", async () => {
  if (!state.current) return;
  const btn = $("#audio-overview-btn");
  const status = $("#audio-overview-status");
  btn.disabled = true;
  btn.classList.add("busy");
  status.textContent = "Writing script + synthesizing… (a few minutes)";
  $("#audio-result").hidden = true;
  try {
    const meta = await api(`/api/notebooks/${state.current}/audio-overview`, { method: "POST" });
    $("#audio-title").textContent = `${meta.title} · ${Math.round(meta.duration_seconds)}s`;
    $("#audio-player").src = meta.url;
    $("#audio-download").href = meta.url;
    $("#audio-result").hidden = false;
    status.textContent = "Two-host podcast from your sources";
  } catch (err) {
    alert(`Audio Overview failed: ${err.message}`);
    status.textContent = "Two-host podcast from your sources";
  } finally {
    btn.disabled = false;
    btn.classList.remove("busy");
  }
});

// ---------- Init ----------
(async () => {
  try {
    const health = await api("/api/health");
    $("#model-badge").textContent =
      `chat: ${health.llm.chat_model} · embed: ${health.llm.embed_model} · tts: ${health.tts.backend}`;
  } catch {
    $("#model-badge").textContent = "⚠ backend unreachable";
  }
  await loadNotebooks();
})();
