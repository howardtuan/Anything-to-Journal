"use strict";

// Local-only PDF rendering and explicit evidence locators. No claim text is ever
// substituted for a source quotation, and ambiguous matches stay unhighlighted.
(() => {
  // Keep matching independently testable in Node; the browser owns only the UI.
  if (typeof document === "undefined") {
    globalThis.WorkspaceEvidenceMatchers = { normalizedText, textIndex, findInIndex, matchesOnPage, locate, normalizedRect };
    return;
  }
  const byId = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "pdfEmpty", "pdfFrame", "pdfPages", "pdfScroll", "pdfPageCount", "pdfOriginal",
    "previewNotice", "referenceToggle", "referenceSidebar", "referenceCount",
    "referenceList", "referenceWarnings", "sourceDialog", "sourceDialogTitle",
    "sourceClaim", "sourceChoices", "sourceLocation", "sourceOriginal", "sourceNotice",
    "sourceViewer", "sourceClose",
  ].map((id) => [id, byId(id)]));
  const state = {
    pdfKey: null, evidenceKey: null, pdfGeneration: 0, evidenceGeneration: 0,
    sourceGeneration: 0, document: null, sourceDocument: null, pages: [], claims: [],
    stale: false, fallback: false, loading: false, observer: null, returnFocus: null,
  };
  let pdfLibraryPromise;

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function safeLocalUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, window.location.href);
      return url.origin === window.location.origin && url.protocol === window.location.protocol ? url.href : null;
    } catch { return null; }
  }

  function originalLink(element, value) {
    const url = safeLocalUrl(value);
    element.classList.toggle("hidden", !url);
    if (url) element.href = url;
    else element.removeAttribute("href");
  }

  async function json(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Source information unavailable (HTTP ${response.status}).`);
    return response.json();
  }

  async function pdfLibrary() {
    if (!pdfLibraryPromise) {
      pdfLibraryPromise = import("/assets/pdfjs/pdf.mjs").then((library) => {
        library.GlobalWorkerOptions.workerSrc = "/assets/pdfjs/pdf.worker.mjs";
        return library;
      });
    }
    return pdfLibraryPromise;
  }

  async function openPdf(url) {
    const safe = safeLocalUrl(url);
    if (!safe) throw new Error("The PDF URL is not a local workspace resource.");
    const library = await pdfLibrary();
    const loadingTask = library.getDocument({
      url: safe, isEvalSupported: false, useSystemFonts: true,
      cMapUrl: "/assets/pdfjs/cmaps/", cMapPacked: true,
      standardFontDataUrl: "/assets/pdfjs/standard_fonts/", wasmUrl: "/assets/pdfjs/wasm/",
      iccUrl: "/assets/pdfjs/iccs/",
    });
    try { return await loadingTask.promise; }
    catch (error) { await loadingTask.destroy().catch(() => {}); throw error; }
  }

  function destroyPdf(pdf) {
    // PDF.js 6 exposes teardown on the loading task, not PDFDocumentProxy.
    // Catch cancellation both during page rendering and worker initialization.
    return Promise.resolve().then(() => pdf?.loadingTask?.destroy()).catch(() => {});
  }

  function normalizedCharacters(value) {
    const result = [];
    let offset = 0;
    for (const character of value) {
      for (const normalized of character.normalize("NFKC")) {
        if (normalized !== "\u00ad") {
          const mapped = /\s/u.test(normalized) ? " " : normalized;
          // String#indexOf uses UTF-16 offsets, including for supplementary glyphs.
          for (let unit = 0; unit < mapped.length; unit += 1) result.push({ char: mapped[unit], offset, end: offset + character.length });
        }
      }
      offset += character.length;
    }
    return result;
  }

  function normalizedText(value) {
    return normalizedCharacters(String(value || "")).map((part) => part.char).join("").replace(/ +/g, " ").trim();
  }

  function textIndex(items, dehyphenate = false) {
    const chars = [];
    const positions = [];
    let prior = null;
    function append(char, position) {
      if (char === " " && (!chars.length || chars[chars.length - 1] === " ")) return;
      chars.push(char);
      positions.push(position);
    }
    items.forEach((item, itemIndex) => {
      if (!item.str) return;
      if (prior) {
        const priorHeight = Math.max(1, Math.hypot(prior.transform[2], prior.transform[3]));
        const newLine = prior.hasEOL || Math.abs(item.transform[5] - prior.transform[5]) > priorHeight * 0.45;
        const gap = item.transform[4] - prior.transform[4] - prior.width;
        // Join PDF fragments on the same baseline; insert spaces only across an
        // actual word gap or line boundary. A second index handles line hyphens.
        if (dehyphenate && newLine && /[\p{L}]-$/u.test(prior.str) && /^\p{L}/u.test(item.str)) {
          while (chars[chars.length - 1] === " ") { chars.pop(); positions.pop(); }
          if (chars[chars.length - 1] === "-") { chars.pop(); positions.pop(); }
        } else if (newLine || gap > priorHeight * 0.12) append(" ", null);
      }
      normalizedCharacters(item.str).forEach((part) => append(part.char, { item: itemIndex, start: part.offset, end: part.end }));
      prior = item;
    });
    return { text: chars.join(""), positions };
  }

  function findInIndex(index, quote) {
    const needle = normalizedText(quote);
    if (!needle) return [];
    const matches = [];
    let cursor = 0;
    while (cursor <= index.text.length - needle.length) {
      const start = index.text.indexOf(needle, cursor);
      if (start < 0) break;
      const end = start + needle.length;
      cursor = start + 1;
      // Latin words and numbers need boundaries (0.9 must not match 10.9).
      // CJK passages naturally occur inside prose without separating spaces.
      const word = (char) => Boolean(char && /[\p{Script=Latin}\p{N}]/u.test(char));
      if ((word(needle[0]) && word(index.text[start - 1])) || (word(needle[needle.length - 1]) && word(index.text[end]))) continue;
      const groups = new Map();
      for (const position of index.positions.slice(start, end)) {
        if (!position) continue;
        const group = groups.get(position.item);
        if (group) { group.start = Math.min(group.start, position.start); group.end = Math.max(group.end, position.end); }
        else groups.set(position.item, { ...position });
      }
      if (groups.size) matches.push({ start, end, groups: [...groups.values()] });
    }
    return matches;
  }

  function matchesOnPage(page, quote) {
    const exact = findInIndex(page.index, quote);
    const wrapped = findInIndex(page.wrappedIndex, quote);
    const unique = new Map();
    [...exact, ...wrapped].forEach((match) => {
      const key = match.groups.map((group) => `${group.item}:${group.start}:${group.end}`).join("|");
      unique.set(key, { page, ...match });
    });
    return [...unique.values()].sort((a, b) => a.groups[0].item - b.groups[0].item || a.groups[0].start - b.groups[0].start);
  }

  function normalizedRect(rect) {
    if (!Array.isArray(rect) || rect.length !== 4 || !rect.every((value) => Number.isFinite(value))) return null;
    const [x, y, width, height] = rect;
    return x >= 0 && y >= 0 && width > 0 && height > 0 && x + width <= 1.000001 && y + height <= 1.000001 ? rect : null;
  }

  function locate(pages, locator = {}) {
    const candidates = locator.page ? pages.filter((page) => page.number === locator.page) : pages;
    if (locator.page && !candidates.length) return { reason: `Page ${locator.page} is outside this PDF.` };
    const rect = normalizedRect(locator.rect);
    if (rect && locator.page) {
      return { page: candidates[0], rect, explicitRegion: true };
    }
    if (!normalizedText(locator.text)) return { reason: locator.page ? "A page is provided, but no exact passage or region is recorded." : "No precise passage or region is recorded for this source." };
    const matches = candidates.flatMap((page) => matchesOnPage(page, locator.text));
    if (locator.occurrence) {
      const match = matches[locator.occurrence - 1];
      return match || { reason: `Recorded occurrence ${locator.occurrence} was not found. No passage was highlighted.` };
    }
    if (matches.length > 1) return { reason: `The recorded passage appears ${matches.length} times. A page or occurrence is needed for an exact highlight.` };
    return matches[0] || { reason: "The recorded passage could not be matched to the PDF text. No passage was highlighted; check the original source." };
  }

  async function pageRecord(document, number) {
    const page = await document.getPage(number);
    const viewport = page.getViewport({ scale: 1.25 });
    const content = await page.getTextContent();
    const items = content.items.filter((item) => typeof item.str === "string");
    return { page, number, viewport, items, styles: content.styles, index: textIndex(items), wrappedIndex: textIndex(items, true) };
  }

  function pageShell(record, prefix) {
    const wrapper = node("div", "pdf-page");
    wrapper.id = `${prefix}-page-${record.number}`;
    wrapper.style.aspectRatio = `${record.viewport.width} / ${record.viewport.height}`;
    wrapper.setAttribute("aria-label", `PDF page ${record.number}`);
    const canvas = node("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", `PDF page ${record.number}. Use reference buttons to read linked evidence.`);
    const loading = node("p", "pdf-page-loading", `Page ${record.number}`);
    const overlay = node("div", "pdf-highlights");
    const markers = node("div", "pdf-markers");
    wrapper.append(canvas, loading, overlay, markers, node("span", "pdf-page-number", `Page ${record.number}`));
    Object.assign(record, { wrapper, canvas, loading, overlay, markers });
    return wrapper;
  }

  async function renderPage(record) {
    if (record.renderPromise) return record.renderPromise;
    record.renderPromise = (async () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      record.canvas.width = Math.ceil(record.viewport.width * ratio);
      record.canvas.height = Math.ceil(record.viewport.height * ratio);
      await record.page.render({
        canvasContext: record.canvas.getContext("2d"), viewport: record.viewport,
        transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
      }).promise;
      record.loading.remove();
    })();
    return record.renderPromise;
  }

  function itemRect(record, group) {
    const item = record.items[group.item];
    const m = record.viewport.transform;
    const t = item.transform;
    const transform = [m[0] * t[0] + m[2] * t[1], m[1] * t[0] + m[3] * t[1], m[0] * t[2] + m[2] * t[3], m[1] * t[2] + m[3] * t[3], m[0] * t[4] + m[2] * t[5] + m[4], m[1] * t[4] + m[3] * t[5] + m[5]];
    const height = Math.hypot(transform[2], transform[3]);
    const angle = Math.atan2(transform[1], transform[0]);
    const ascent = (record.styles[item.fontName]?.ascent || 0.8) * height;
    const width = item.width * record.viewport.scale;
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = `${height}px ${record.styles[item.fontName]?.fontFamily || "serif"}`;
    const total = measure.measureText(item.str).width || item.str.length || 1;
    let from = measure.measureText(item.str.slice(0, group.start)).width / total;
    let to = measure.measureText(item.str.slice(0, group.end)).width / total;
    if (item.dir === "rtl") [from, to] = [1 - to, 1 - from];
    return {
      x: (transform[4] + ascent * Math.sin(angle) + from * width * Math.cos(angle)) / record.viewport.width,
      y: (transform[5] - ascent * Math.cos(angle) + from * width * Math.sin(angle)) / record.viewport.height,
      width: (to - from) * width / record.viewport.width,
      height: height / record.viewport.height, angle,
    };
  }

  function boxesForMatch(match) {
    if (match.rect) return [{ x: match.rect[0], y: match.rect[1], width: match.rect[2], height: match.rect[3], angle: 0 }];
    return (match.groups || []).map((group) => itemRect(match.page, group));
  }

  function drawBoxes(match, className, container) {
    const boxes = boxesForMatch(match);
    for (const box of boxes) {
      const highlight = node("span", className);
      highlight.style.left = `${box.x * 100}%`;
      highlight.style.top = `${box.y * 100}%`;
      highlight.style.width = `${Math.max(0.001, box.width) * 100}%`;
      highlight.style.height = `${box.height * 100}%`;
      if (box.angle) { highlight.style.transform = `rotate(${box.angle}rad)`; highlight.style.transformOrigin = "top left"; }
      container.append(highlight);
    }
    return boxes;
  }

  function notice() {
    const messages = [];
    if (state.stale) messages.push("This is the last successful PDF. Recompile successfully to place current references beside its text.");
    if (state.fallback) messages.push("Using the browser PDF viewer. Reference details remain available in the References panel; inline markers need the local PDF renderer.");
    ui.previewNotice.textContent = messages.join(" ");
    ui.previewNotice.classList.toggle("hidden", !messages.length);
  }

  function renderReferenceList() {
    ui.referenceCount.textContent = String(state.claims.length);
    ui.referenceList.replaceChildren();
    if (!state.claims.length) {
      ui.referenceList.append(node("p", "reference-empty", "No reference locations have been recorded yet. Add source links and precise locations to the manuscript evidence map to see them here."));
      return;
    }
    state.claims.forEach((claim, index) => {
      const button = node("button", "reference-card");
      button.type = "button";
      const heading = node("span", "reference-card-heading");
      heading.append(node("span", "reference-number", String(index + 1)), node("span", "", String(claim.kind || "Reference").replaceAll("_", " ")));
      const sources = claim.sources || [];
      const status = claim.status ? ` · ${String(claim.status).replaceAll("_", " ")}` : "";
      button.append(heading, node("span", "reference-card-title", claim.text || claim.preview?.text || claim.id), node("span", "reference-card-meta", `${sources.length} source${sources.length === 1 ? "" : "s"}${status}`));
      const location = node("span", "reference-card-location");
      button.append(location);
      claim.locationElement = location;
      button.addEventListener("click", () => showClaim(claim, index, button));
      ui.referenceList.append(button);
    });
  }

  function placeReferences() {
    state.pages.forEach((page) => { page.overlay.replaceChildren(); page.markers.replaceChildren(); });
    const occupied = new Map();
    state.claims.forEach((claim, index) => {
      const location = claim.locationElement;
      if (!location) return;
      if (state.stale) { location.textContent = "Waiting for a current PDF"; return; }
      if (state.fallback) { location.textContent = "Source details available"; return; }
      if (state.loading) { location.textContent = "Locating passage…"; return; }
      // Manuscript geometry can move after any successful compilation. Verify
      // the rendered phrase every time; only immutable source files use rects.
      const preview = { ...(claim.preview || {}) };
      delete preview.rect;
      const match = locate(state.pages, preview);
      if (!match.page) { location.textContent = match.reason || "No preview location recorded"; return; }
      location.textContent = `Page ${match.page.number} · ${match.explicitRegion ? "linked region" : "linked passage"}`;
      const boxes = drawBoxes(match, "pdf-claim-highlight", match.page.overlay);
      let y = Math.max(0, boxes[0]?.y || 0);
      const used = occupied.get(match.page.number) || [];
      const separation = 31 / Math.max(350, match.page.wrapper.getBoundingClientRect().height);
      while (used.some((prior) => Math.abs(y - prior) < separation)) y += separation;
      used.push(y);
      occupied.set(match.page.number, used);
      const button = node("button", "pdf-reference-node", String(index + 1));
      button.type = "button";
      button.style.top = `${Math.min(0.97, y) * 100}%`;
      button.title = `${claim.text || claim.id} — inspect original source`;
      button.setAttribute("aria-label", `Reference ${index + 1}: ${claim.text || claim.id}`);
      button.addEventListener("click", () => showClaim(claim, index, button));
      match.page.markers.append(button);
    });
  }

  function fallBack(pdf, generation) {
    if (generation !== state.pdfGeneration) return;
    state.fallback = true;
    state.loading = false;
    ui.pdfPages.classList.add("hidden");
    ui.pdfFrame.classList.remove("hidden");
    const url = new URL(pdf.url, window.location.href);
    url.searchParams.set("v", pdf.version || "");
    ui.pdfFrame.src = url.href;
    notice();
    placeReferences();
  }

  async function loadManuscript(pdf) {
    const generation = ++state.pdfGeneration;
    state.loading = true;
    state.fallback = false;
    state.observer?.disconnect();
    const oldDocument = state.document;
    state.document = null;
    if (oldDocument) destroyPdf(oldDocument);
    state.pages = [];
    ui.pdfPages.replaceChildren();
    ui.pdfPages.classList.remove("hidden");
    ui.pdfFrame.classList.add("hidden");
    ui.pdfFrame.removeAttribute("src");
    ui.pdfEmpty.classList.toggle("hidden", Boolean(pdf.available));
    originalLink(ui.pdfOriginal, pdf.available ? pdf.url : null);
    ui.pdfPageCount.textContent = pdf.available ? "Loading…" : "";
    if (!pdf.available) { state.loading = false; placeReferences(); return; }
    try {
      const url = new URL(pdf.url, window.location.href);
      url.searchParams.set("v", pdf.version || "");
      const document = await openPdf(url.href);
      if (generation !== state.pdfGeneration) { await destroyPdf(document); return; }
      state.document = document;
      ui.pdfPageCount.textContent = `${document.numPages} page${document.numPages === 1 ? "" : "s"}`;
      state.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const record = state.pages.find((page) => page.wrapper === entry.target);
          if (record) renderPage(record).catch(() => fallBack(pdf, generation));
          state.observer?.unobserve(entry.target);
        }
      }, { root: ui.pdfScroll, rootMargin: "700px 0px" });
      for (let number = 1; number <= document.numPages; number += 1) {
        const record = await pageRecord(document, number);
        if (generation !== state.pdfGeneration) return;
        state.pages.push(record);
        ui.pdfPages.append(pageShell(record, "manuscript"));
        state.observer.observe(record.wrapper);
      }
      state.loading = false;
      placeReferences();
    } catch {
      fallBack(pdf, generation);
    }
  }

  async function loadEvidence() {
    const generation = ++state.evidenceGeneration;
    try {
      const evidence = await json("/api/evidence");
      if (generation !== state.evidenceGeneration) return;
      state.claims = Array.isArray(evidence.claims) ? evidence.claims : [];
      ui.referenceWarnings.replaceChildren(...(evidence.warnings || []).map((warning) => node("p", "", String(warning))));
    } catch {
      if (generation !== state.evidenceGeneration) return;
      state.claims = [];
      ui.referenceWarnings.replaceChildren(node("p", "", "Reference information is not available for this workspace. The manuscript PDF is still available."));
      // Retry after transient server errors without repeatedly replacing the PDF.
      state.evidenceKey = null;
    }
    renderReferenceList();
    placeReferences();
  }

  function update(serverState) {
    const pdf = serverState.pdf || {};
    const stale = pdf.stale === true || pdf.current === false;
    const changedCurrent = stale !== state.stale;
    state.stale = stale;
    const pdfKey = `${Boolean(pdf.available)}:${pdf.version || ""}`;
    if (pdfKey !== state.pdfKey) { state.pdfKey = pdfKey; loadManuscript(pdf); }
    const evidenceKey = serverState.evidence_revision || serverState.evidence?.revision || "initial";
    if (evidenceKey !== state.evidenceKey) {
      if (state.evidenceKey !== null && ui.sourceDialog.open) ui.sourceDialog.close();
      state.evidenceKey = evidenceKey;
      loadEvidence();
    }
    if (changedCurrent) placeReferences();
    notice();
  }

  function sourceLocation(source) {
    const locator = source.locator || {};
    const parts = [source.name || source.id];
    if (locator.page) parts.push(`Page ${locator.page}`);
    if (locator.line_start) parts.push(locator.line_end && locator.line_end !== locator.line_start ? `Lines ${locator.line_start}–${locator.line_end}` : `Line ${locator.line_start}`);
    if (locator.label) parts.push(locator.label);
    if (locator.occurrence) parts.push(`Occurrence ${locator.occurrence}`);
    return parts.join(" · ");
  }

  function showClaim(claim, index, trigger) {
    state.returnFocus = trigger;
    ui.sourceDialogTitle.textContent = `Reference ${index + 1}`;
    ui.sourceClaim.textContent = claim.text || claim.preview?.text || claim.id;
    const sources = claim.sources || [];
    ui.sourceChoices.replaceChildren();
    sources.forEach((source, sourceIndex) => {
      const button = node("button", "button source-choice", `${sourceIndex + 1}. ${source.name || source.id}`);
      button.type = "button";
      button.title = source.name || source.id;
      button.setAttribute("aria-pressed", String(sourceIndex === 0));
      button.addEventListener("click", () => {
        for (const choice of ui.sourceChoices.children) choice.setAttribute("aria-pressed", String(choice === button));
        showSource(source);
      });
      ui.sourceChoices.append(button);
    });
    if (!ui.sourceDialog.open) ui.sourceDialog.showModal();
    if (sources.length) showSource(sources[0]);
    else {
      state.sourceGeneration += 1;
      ui.sourceLocation.textContent = "No source files recorded";
      originalLink(ui.sourceOriginal, null);
      ui.sourceNotice.textContent = "This reference has no linked source. Its provenance cannot be inspected yet.";
      ui.sourceViewer.replaceChildren();
    }
  }

  function sourceMessage(messages) {
    ui.sourceNotice.textContent = [...new Set(messages.filter(Boolean))].join(" ");
  }

  function quotedPassage(locator) {
    return locator.text ? node("blockquote", "source-quote", `Recorded source passage: “${locator.text}”`) : null;
  }

  function renderTextSource(preview, locator, messages) {
    const text = String(preview.text || "");
    const lines = text.split("\n");
    const lineStarts = [];
    let offset = 0;
    lines.forEach((line) => { lineStarts.push(offset); offset += line.length + 1; });
    const startLine = Number(locator.line_start);
    const endLine = Number(locator.line_end || locator.line_start);
    const hasLines = Number.isInteger(startLine) && startLine > 0 && Number.isInteger(endLine) && endLine >= startLine && endLine <= lines.length;
    if (locator.line_start && !hasLines) messages.push("The recorded line range is outside the available text. No lines were highlighted.");
    const first = hasLines ? lineStarts[startLine - 1] : 0;
    const last = hasLines ? lineStarts[endLine - 1] + lines[endLine - 1].length : text.length;
    const chars = normalizedCharacters(text.slice(first, last));
    const positions = [];
    const indexChars = [];
    chars.forEach((part) => {
      if (part.char === " " && indexChars[indexChars.length - 1] === " ") return;
      indexChars.push(part.char);
      positions.push({ item: 0, start: first + part.offset, end: first + part.end });
    });
    const matches = locator.text ? findInIndex({ text: indexChars.join(""), positions }, locator.text) : [];
    let match = null;
    if (locator.text) {
      if (locator.occurrence) match = matches[locator.occurrence - 1];
      else if (matches.length === 1) match = matches[0];
      if (!match) messages.push(matches.length > 1 && !locator.occurrence ? `The source passage appears ${matches.length} times. No exact passage was highlighted.` : "The recorded source passage was not found in the selected text. No exact passage was highlighted.");
    }
    if (match) messages.push("Yellow marks the matching source passage.");
    else if (hasLines) messages.push("Yellow marks the recorded source lines; the highlight does not independently verify the claim.");
    else if (!locator.text && !locator.line_start) messages.push("No precise text location is recorded. Showing the source file without a highlight.");
    const range = match?.groups[0];
    const pre = node("pre", "source-text");
    let target;
    lines.forEach((line, index) => {
      const row = node("span", "source-text-line");
      const number = node("span", "source-line-number", String(index + 1));
      number.setAttribute("aria-hidden", "true");
      const content = node("span", "source-line-content");
      const lineStart = lineStarts[index];
      if (range && range.start < lineStart + line.length && range.end > lineStart) {
        const a = Math.max(0, range.start - lineStart);
        const b = Math.min(line.length, range.end - lineStart);
        content.append(document.createTextNode(line.slice(0, a)), node("mark", "", line.slice(a, b)), document.createTextNode(line.slice(b)));
        target ||= row;
      } else content.textContent = line || " ";
      if (hasLines && !match && index + 1 >= startLine && index + 1 <= endLine) { row.classList.add("is-highlighted"); target ||= row; }
      row.append(number, content);
      pre.append(row);
    });
    ui.sourceViewer.append(pre);
    if (target) requestAnimationFrame(() => { ui.sourceViewer.scrollTop = Math.max(0, target.offsetTop - pre.offsetTop - 100); });
    sourceMessage(messages);
  }

  async function renderPdfSource(preview, locator, messages, generation) {
    let document;
    try {
      document = await openPdf(preview.url);
      if (generation !== state.sourceGeneration) { await destroyPdf(document); return; }
      state.sourceDocument = document;
      let pages = [];
      if (locator.page && locator.page <= document.numPages) pages.push(await pageRecord(document, locator.page));
      else if (locator.text && !locator.page) {
        for (let number = 1; number <= document.numPages; number += 1) {
          if (generation !== state.sourceGeneration) return;
          pages.push(await pageRecord(document, number));
        }
      }
      if (generation !== state.sourceGeneration) return;
      let match = locate(pages, locator);
      let record = match.page || pages[0];
      if (!record) record = await pageRecord(document, 1);
      if (generation !== state.sourceGeneration) return;
      const container = node("div", "source-pdf-pages");
      container.append(pageShell(record, "source"));
      ui.sourceViewer.append(container);
      await renderPage(record);
      if (generation !== state.sourceGeneration) return;
      if (match.page) {
        const boxes = drawBoxes(match, `pdf-source-highlight${match.explicitRegion ? " pdf-region-highlight" : ""}`, record.overlay);
        messages.push(match.explicitRegion ? "Yellow outlines the recorded figure or table region." : "Yellow marks the matching source passage.");
        requestAnimationFrame(() => {
          const y = boxes[0]?.y || 0;
          ui.sourceViewer.scrollTop = Math.max(0, record.wrapper.offsetTop - ui.sourceViewer.offsetTop + y * record.wrapper.clientHeight - 100);
        });
      } else messages.push(match.reason);
      ui.sourceLocation.textContent += ` · Displaying page ${record.number} of ${document.numPages}`;
      sourceMessage(messages);
    } catch {
      if (generation !== state.sourceGeneration) return;
      messages.push("The local renderer could not display this source. The browser viewer below has no evidence highlight.");
      ui.sourceViewer.replaceChildren();
      const quote = quotedPassage(locator);
      if (quote) ui.sourceViewer.append(quote);
      const frame = node("iframe", "source-native-pdf");
      frame.title = "Original source PDF — no evidence highlight";
      const url = safeLocalUrl(preview.url);
      if (url) {
        frame.src = `${url.split("#")[0]}#page=${Number(locator.page) || 1}`;
        ui.sourceViewer.append(frame);
      }
      sourceMessage(messages);
    }
  }

  async function showSource(source) {
    const generation = ++state.sourceGeneration;
    if (state.sourceDocument) { destroyPdf(state.sourceDocument); state.sourceDocument = null; }
    ui.sourceLocation.textContent = sourceLocation(source);
    originalLink(ui.sourceOriginal, source.available ? source.url : null);
    ui.sourceViewer.replaceChildren(node("p", "source-loading", "Opening original source…"));
    ui.sourceViewer.scrollTop = 0;
    ui.sourceNotice.textContent = "";
    const locator = source.locator || {};
    const messages = [source.warning];
    if (!source.available) {
      ui.sourceViewer.replaceChildren(node("p", "source-unavailable", "This source is unavailable. Restore the original file to inspect the evidence."));
      sourceMessage(messages);
      return;
    }
    try {
      const url = safeLocalUrl(source.preview_url);
      if (!url) throw new Error("No local source preview is available.");
      const preview = await json(url);
      if (generation !== state.sourceGeneration) return;
      messages.push(preview.warning);
      ui.sourceViewer.replaceChildren();
      const quote = quotedPassage(locator);
      if (quote) ui.sourceViewer.append(quote);
      if (preview.kind === "pdf") await renderPdfSource(preview, locator, messages, generation);
      else if (preview.kind === "text") renderTextSource(preview, locator, messages);
      else if (preview.kind === "image") {
        const wrapper = node("div", "source-image");
        const image = node("img");
        const imageUrl = safeLocalUrl(preview.url);
        if (!imageUrl) throw new Error("No local image preview is available.");
        image.src = imageUrl;
        image.alt = preview.name || source.name || "Original source figure";
        wrapper.append(image);
        image.addEventListener("error", () => {
          if (generation !== state.sourceGeneration) return;
          sourceMessage([...messages, "The image preview is unavailable. Open the original file to inspect it."]);
        });
        if (normalizedRect(locator.rect)) { drawBoxes({ rect: locator.rect }, "pdf-source-highlight pdf-region-highlight", wrapper); messages.push("Yellow outlines the recorded region in the original image."); }
        else messages.push("Showing the original image. No smaller region is recorded.");
        ui.sourceViewer.append(wrapper);
        sourceMessage(messages);
      } else {
        ui.sourceViewer.append(node("p", "source-unavailable", "This file type has no inline preview. Open the original file using the link above; the recorded location is shown here."));
        sourceMessage(messages);
      }
    } catch (error) {
      if (generation !== state.sourceGeneration) return;
      ui.sourceViewer.replaceChildren(node("p", "source-unavailable", error.message));
      sourceMessage(messages);
    }
  }

  ui.referenceToggle.addEventListener("click", () => {
    const collapsed = ui.referenceSidebar.classList.toggle("hidden");
    ui.referenceToggle.setAttribute("aria-expanded", String(!collapsed));
    placeReferences();
  });
  if (window.matchMedia("(max-width: 720px)").matches) {
    ui.referenceSidebar.classList.add("hidden");
    ui.referenceToggle.setAttribute("aria-expanded", "false");
  }
  ui.sourceClose.addEventListener("click", () => ui.sourceDialog.close());
  ui.sourceDialog.addEventListener("click", (event) => { if (event.target === ui.sourceDialog) {
    const bounds = ui.sourceDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) ui.sourceDialog.close();
  } });
  ui.sourceDialog.addEventListener("close", () => {
    state.sourceGeneration += 1;
    if (state.sourceDocument) { destroyPdf(state.sourceDocument); state.sourceDocument = null; }
    ui.sourceViewer.replaceChildren();
    state.returnFocus?.focus();
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(placeReferences, 120);
  });
  window.WorkspaceEvidence = { update, refresh: placeReferences };
})();
