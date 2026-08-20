"use strict";

const elements = {
  pdfTab: document.querySelector("#pdfTab"),
  latexTab: document.querySelector("#latexTab"),
  pdfPanel: document.querySelector("#pdfPanel"),
  latexPanel: document.querySelector("#latexPanel"),
  pdfFrame: document.querySelector("#pdfFrame"),
  pdfEmpty: document.querySelector("#pdfEmpty"),
  saveStatus: document.querySelector("#saveStatus"),
  recompileButton: document.querySelector("#recompileButton"),
  filePicker: document.querySelector("#filePicker"),
  searchButton: document.querySelector("#searchButton"),
  searchBar: document.querySelector("#searchBar"),
  searchInput: document.querySelector("#searchInput"),
  searchCount: document.querySelector("#searchCount"),
  searchPrevious: document.querySelector("#searchPrevious"),
  searchNext: document.querySelector("#searchNext"),
  searchClose: document.querySelector("#searchClose"),
  undoButton: document.querySelector("#undoButton"),
  redoButton: document.querySelector("#redoButton"),
  saveButton: document.querySelector("#saveButton"),
  sourceEditor: document.querySelector("#sourceEditor"),
  highlightLayer: document.querySelector("#highlightLayer"),
  lineNumbers: document.querySelector("#lineNumbers"),
  conflictBanner: document.querySelector("#conflictBanner"),
  keepUnsavedButton: document.querySelector("#keepUnsavedButton"),
  reloadExternalButton: document.querySelector("#reloadExternalButton"),
  compileFailure: document.querySelector("#compileFailure"),
  compileDetailsButton: document.querySelector("#compileDetailsButton"),
  compileLog: document.querySelector("#compileLog"),
  toast: document.querySelector("#toast"),
};

const editorState = {
  currentPath: "",
  baseHash: "",
  savedContent: "",
  dirty: false,
  conflict: false,
  files: new Map(),
  pdfVersion: "",
  compileStatus: "saved",
  loading: false,
  searchMatches: [],
  searchIndex: -1,
  toastTimer: null,
  history: [],
  historyIndex: -1,
  historyTimer: null,
  applyingHistory: false,
};

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function token(kind, value) {
  return `<span class="tok-${kind}">${escapeHtml(value)}</span>`;
}

function findComment(line) {
  for (let index = 0; index < line.length; index += 1) {
    if (line[index] !== "%") continue;
    let slashCount = 0;
    for (let cursor = index - 1; cursor >= 0 && line[cursor] === "\\"; cursor -= 1) slashCount += 1;
    if (slashCount % 2 === 0) return index;
  }
  return -1;
}

function highlightCodeSegment(value) {
  let output = "";
  let index = 0;
  while (index < value.length) {
    const rest = value.slice(index);
    const command = rest.match(/^\\(?:[A-Za-z@]+\*?|.)/);
    if (command) {
      output += token("command", command[0]);
      index += command[0].length;
      continue;
    }
    const number = rest.match(/^\d+(?:\.\d+)?/);
    if (number) {
      output += token("number", number[0]);
      index += number[0].length;
      continue;
    }
    if ("{}[]".includes(value[index])) {
      output += token("brace", value[index]);
    } else if (value[index] === "$") {
      output += token("math", value[index]);
    } else {
      output += escapeHtml(value[index]);
    }
    index += 1;
  }
  return output;
}

function highlightLatexLine(line) {
  const commentAt = findComment(line);
  const code = commentAt >= 0 ? line.slice(0, commentAt) : line;
  const comment = commentAt >= 0 ? line.slice(commentAt) : "";
  return highlightCodeSegment(code) + (comment ? token("comment", comment) : "");
}

function highlightBibLine(line) {
  const commentAt = findComment(line);
  const code = commentAt >= 0 ? line.slice(0, commentAt) : line;
  const comment = commentAt >= 0 ? line.slice(commentAt) : "";
  const entry = code.match(/^(\s*)(@[A-Za-z]+)(.*)$/);
  if (entry) {
    return escapeHtml(entry[1]) + token("entry", entry[2]) + highlightCodeSegment(entry[3])
      + (comment ? token("comment", comment) : "");
  }
  const field = code.match(/^(\s*)([A-Za-z][A-Za-z0-9_-]*)(\s*=.*)$/);
  if (field) {
    return escapeHtml(field[1]) + token("field", field[2]) + highlightCodeSegment(field[3])
      + (comment ? token("comment", comment) : "");
  }
  return highlightCodeSegment(code) + (comment ? token("comment", comment) : "");
}

function renderEditor() {
  const value = elements.sourceEditor.value;
  const bib = editorState.currentPath.toLowerCase().endsWith(".bib");
  const lines = value.split("\n");
  elements.highlightLayer.innerHTML = lines
    .map((line) => (bib ? highlightBibLine(line) : highlightLatexLine(line)))
    .join("\n") + (value.endsWith("\n") ? " " : "");
  elements.lineNumbers.textContent = Array.from({ length: Math.max(1, lines.length) }, (_, index) => index + 1).join("\n");
  syncScroll();
}

function resetHistory(value) {
  window.clearTimeout(editorState.historyTimer);
  editorState.history = [value];
  editorState.historyIndex = 0;
}

function recordHistory() {
  const value = elements.sourceEditor.value;
  if (editorState.history[editorState.historyIndex] === value) return;
  editorState.history = editorState.history.slice(0, editorState.historyIndex + 1);
  editorState.history.push(value);
  if (editorState.history.length > 200) editorState.history.shift();
  editorState.historyIndex = editorState.history.length - 1;
}

function scheduleHistory() {
  window.clearTimeout(editorState.historyTimer);
  editorState.historyTimer = window.setTimeout(recordHistory, 280);
}

function applyHistory(index) {
  if (index < 0 || index >= editorState.history.length) return;
  editorState.applyingHistory = true;
  editorState.historyIndex = index;
  elements.sourceEditor.value = editorState.history[index];
  editorState.dirty = elements.sourceEditor.value !== editorState.savedContent;
  renderEditor();
  updateSearch();
  updateStatus();
  elements.sourceEditor.focus();
  editorState.applyingHistory = false;
}

function performUndo() {
  window.clearTimeout(editorState.historyTimer);
  recordHistory();
  if (editorState.historyIndex > 0) applyHistory(editorState.historyIndex - 1);
}

function performRedo() {
  window.clearTimeout(editorState.historyTimer);
  if (editorState.historyIndex < editorState.history.length - 1) {
    applyHistory(editorState.historyIndex + 1);
  }
}

function syncScroll() {
  elements.highlightLayer.scrollTop = elements.sourceEditor.scrollTop;
  elements.highlightLayer.scrollLeft = elements.sourceEditor.scrollLeft;
  elements.lineNumbers.scrollTop = elements.sourceEditor.scrollTop;
}

function setTab(name) {
  const pdf = name === "pdf";
  elements.pdfTab.classList.toggle("active", pdf);
  elements.pdfTab.setAttribute("aria-selected", String(pdf));
  elements.latexTab.classList.toggle("active", !pdf);
  elements.latexTab.setAttribute("aria-selected", String(!pdf));
  elements.pdfPanel.classList.toggle("active", pdf);
  elements.latexPanel.classList.toggle("active", !pdf);
  elements.pdfPanel.hidden = !pdf;
  elements.latexPanel.hidden = pdf;
  if (!pdf) elements.sourceEditor.focus();
}

function statusText() {
  if (editorState.conflict) return ["Unsaved · External Change", "failed"];
  if (editorState.dirty && editorState.compileStatus === "compiling") return ["Unsaved · Compiling", "unsaved"];
  if (editorState.dirty) return ["Unsaved", "unsaved"];
  if (editorState.compileStatus === "compiling") return ["Compiling", "compiling"];
  if (editorState.compileStatus === "compile_failed") return ["Compile Failed", "failed"];
  return ["Saved", "saved"];
}

function updateStatus() {
  const [label, className] = statusText();
  elements.saveStatus.textContent = label;
  elements.saveStatus.className = `status ${className}`;
}

function showToast(message) {
  window.clearTimeout(editorState.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  editorState.toastTimer = window.setTimeout(() => elements.toast.classList.add("hidden"), 2400);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-Workspace-Request"] = "1";
  const response = await fetch(path, { cache: "no-store", ...options, headers });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function updateFilePicker(files) {
  const prior = editorState.currentPath;
  editorState.files = new Map(files.map((record) => [record.path, record]));
  const names = files.map((record) => record.path);
  const currentOptions = Array.from(elements.filePicker.options).map((option) => option.value);
  if (JSON.stringify(names) !== JSON.stringify(currentOptions)) {
    elements.filePicker.replaceChildren(...names.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    }));
  }
  if (prior && names.includes(prior)) elements.filePicker.value = prior;
}

async function loadFile(path, externalReload = false) {
  editorState.loading = true;
  try {
    const record = await api(`/api/file?path=${encodeURIComponent(path)}`);
    editorState.currentPath = record.path;
    editorState.baseHash = record.sha256;
    editorState.savedContent = record.content;
    editorState.dirty = false;
    editorState.conflict = false;
    elements.sourceEditor.value = record.content;
    resetHistory(record.content);
    elements.filePicker.value = record.path;
    elements.conflictBanner.classList.add("hidden");
    renderEditor();
    updateSearch();
    updateStatus();
    if (externalReload) showToast(`${record.path} reloaded from disk`);
  } finally {
    editorState.loading = false;
  }
}

function showConflict() {
  editorState.conflict = true;
  elements.conflictBanner.classList.remove("hidden");
  updateStatus();
}

async function saveFile() {
  if (!editorState.currentPath || editorState.loading) return;
  try {
    const result = await api(`/api/file?path=${encodeURIComponent(editorState.currentPath)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: elements.sourceEditor.value,
        expected_sha256: editorState.baseHash,
      }),
    });
    editorState.baseHash = result.file.sha256;
    editorState.savedContent = elements.sourceEditor.value;
    editorState.files.set(result.file.path, result.file);
    editorState.dirty = false;
    editorState.conflict = false;
    editorState.compileStatus = "compiling";
    elements.conflictBanner.classList.add("hidden");
    updateStatus();
    showToast("Saved — preview compile scheduled");
  } catch (error) {
    if (error.status === 409) {
      showConflict();
      return;
    }
    showToast(error.message);
  }
}

async function recompile() {
  try {
    await api("/api/recompile", { method: "POST" });
    editorState.compileStatus = "compiling";
    updateStatus();
    showToast("Recompile scheduled");
  } catch (error) {
    showToast(error.message);
  }
}

function updatePdf(pdf) {
  elements.pdfEmpty.classList.toggle("hidden", pdf.available);
  elements.pdfFrame.classList.toggle("hidden", !pdf.available);
  if (pdf.available && pdf.version !== editorState.pdfVersion) {
    editorState.pdfVersion = pdf.version;
    elements.pdfFrame.src = `${pdf.url}?v=${encodeURIComponent(pdf.version)}`;
    showToast("PDF preview updated");
  }
}

function updateCompileFailure(serverState) {
  const failed = serverState.compile_status === "compile_failed";
  elements.compileFailure.classList.toggle("hidden", !failed);
  elements.compileLog.textContent = serverState.compile_error || "No compiler diagnostic was returned.";
  if (!failed) elements.compileLog.classList.add("hidden");
}

async function pollState(initial = false) {
  try {
    const serverState = await api("/api/state");
    editorState.compileStatus = serverState.compile_status;
    updateFilePicker(serverState.files);
    updatePdf(serverState.pdf);
    updateCompileFailure(serverState);

    if (!editorState.currentPath && serverState.files.length) {
      const initialPath = serverState.files.some((record) => record.path === serverState.main_file)
        ? serverState.main_file
        : serverState.files[0].path;
      await loadFile(initialPath);
    } else if (editorState.currentPath) {
      const disk = editorState.files.get(editorState.currentPath);
      if (!disk) {
        if (editorState.dirty) showConflict();
        else if (serverState.files.length) await loadFile(serverState.files[0].path, true);
      } else if (disk.sha256 !== editorState.baseHash) {
        if (editorState.dirty) showConflict();
        else await loadFile(editorState.currentPath, true);
      }
    }
    updateStatus();
  } catch (error) {
    if (initial) {
      elements.pdfEmpty.classList.remove("hidden");
      elements.pdfEmpty.querySelector("h2").textContent = "Workspace unavailable";
      elements.pdfEmpty.querySelector("p").textContent = error.message;
    }
  }
}

function openSearch() {
  elements.searchBar.classList.remove("hidden");
  elements.searchInput.focus();
  elements.searchInput.select();
  updateSearch();
}

function closeSearch() {
  elements.searchBar.classList.add("hidden");
  elements.sourceEditor.focus();
}

function updateSearch(direction = 0) {
  const query = elements.searchInput.value;
  editorState.searchMatches = [];
  editorState.searchIndex = -1;
  if (!query) {
    elements.searchCount.textContent = "0 / 0";
    return;
  }
  const source = elements.sourceEditor.value.toLocaleLowerCase();
  const needle = query.toLocaleLowerCase();
  let offset = 0;
  while (offset <= source.length - needle.length) {
    const match = source.indexOf(needle, offset);
    if (match < 0) break;
    editorState.searchMatches.push(match);
    offset = match + Math.max(1, needle.length);
  }
  if (!editorState.searchMatches.length) {
    elements.searchCount.textContent = "0 / 0";
    return;
  }
  const selectionStart = elements.sourceEditor.selectionStart;
  let index = editorState.searchMatches.findIndex((match) => match >= selectionStart);
  if (index < 0) index = 0;
  if (direction < 0) index = (index - 1 + editorState.searchMatches.length) % editorState.searchMatches.length;
  if (direction > 0 && editorState.searchMatches[index] === selectionStart) index = (index + 1) % editorState.searchMatches.length;
  editorState.searchIndex = index;
  const start = editorState.searchMatches[index];
  elements.sourceEditor.focus();
  elements.sourceEditor.setSelectionRange(start, start + query.length);
  elements.searchCount.textContent = `${index + 1} / ${editorState.searchMatches.length}`;
}

function insertTab(event) {
  event.preventDefault();
  const editor = elements.sourceEditor;
  const start = editor.selectionStart;
  const end = editor.selectionEnd;
  editor.setRangeText("  ", start, end, "end");
  editor.dispatchEvent(new Event("input", { bubbles: true }));
}

elements.pdfTab.addEventListener("click", () => setTab("pdf"));
elements.latexTab.addEventListener("click", () => setTab("latex"));
elements.saveButton.addEventListener("click", saveFile);
elements.recompileButton.addEventListener("click", recompile);
elements.searchButton.addEventListener("click", openSearch);
elements.searchClose.addEventListener("click", closeSearch);
elements.searchInput.addEventListener("input", () => updateSearch());
elements.searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    updateSearch(event.shiftKey ? -1 : 1);
  } else if (event.key === "Escape") {
    closeSearch();
  }
});
elements.searchPrevious.addEventListener("click", () => updateSearch(-1));
elements.searchNext.addEventListener("click", () => updateSearch(1));
elements.undoButton.addEventListener("click", () => {
  performUndo();
});
elements.redoButton.addEventListener("click", () => {
  performRedo();
});
elements.sourceEditor.addEventListener("input", () => {
  if (editorState.loading || editorState.applyingHistory) return;
  editorState.dirty = true;
  scheduleHistory();
  renderEditor();
  updateStatus();
});
elements.sourceEditor.addEventListener("scroll", syncScroll);
elements.sourceEditor.addEventListener("keydown", (event) => {
  const command = event.metaKey || event.ctrlKey;
  if (event.key === "Tab") insertTab(event);
  if (command && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveFile();
  }
  if (command && event.key.toLowerCase() === "f") {
    event.preventDefault();
    openSearch();
  }
  if (command && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) performRedo();
    else performUndo();
  }
  if (command && event.key.toLowerCase() === "y") {
    event.preventDefault();
    performRedo();
  }
});
elements.filePicker.addEventListener("change", async () => {
  const next = elements.filePicker.value;
  if (editorState.dirty && !window.confirm("Discard unsaved changes and open another file?")) {
    elements.filePicker.value = editorState.currentPath;
    return;
  }
  await loadFile(next);
});
elements.reloadExternalButton.addEventListener("click", () => loadFile(editorState.currentPath, true));
elements.keepUnsavedButton.addEventListener("click", () => {
  elements.conflictBanner.classList.add("hidden");
  showToast("Unsaved text kept; saving remains blocked until you reload the disk version");
});
elements.compileDetailsButton.addEventListener("click", () => {
  const hidden = elements.compileLog.classList.toggle("hidden");
  elements.compileDetailsButton.textContent = hidden ? "Show log" : "Hide log";
});
window.addEventListener("beforeunload", (event) => {
  if (!editorState.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

setTab("pdf");
pollState(true);
window.setInterval(() => pollState(false), 1200);
