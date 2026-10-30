// archdiag web viewer. All model text reaches the DOM through textContent; the diagram SVG is
// sanitised with DOMPurify and stripped of every link before it is inserted.

const $ = (id) => document.getElementById(id);

const state = {
  payload: null,
  views: [],
  view: null,
  source: "",
  svg: null,
  nodeElements: new Map(),
  selected: null,
  scale: 1,
  tx: 0,
  ty: 0,
  renders: 0,
  ad: null,
};

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function initMermaid() {
  window.mermaid.initialize({
    startOnLoad: false,
    securityLevel: "antiscript",
    theme: darkQuery.matches ? "dark" : "neutral",
    htmlLabels: false,
    flowchart: { htmlLabels: false, useMaxWidth: false, curve: "basis" },
    maxTextSize: 500000,
  });
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`.trim();
  el.hidden = !text;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

const model = () => state.payload?.model ?? { nodes: [], edges: [], groups: [], views: [] };
const nodesById = () => new Map(model().nodes.map((node) => [node.id, node]));
const viewById = (id) => state.views.find((view) => view.id === id);

// ---- loading ---------------------------------------------------------------------------------

async function load(preferredView) {
  const response = await fetch("/api/model");
  if (response.status === 404) {
    state.payload = null;
    $("viewport").replaceChildren();
    $("empty").hidden = false;
    $("title").textContent = "archdiag";
    return;
  }
  if (!response.ok) throw new Error(`Could not load the model (HTTP ${response.status}).`);
  state.payload = await response.json();
  const views = await fetch("/api/views");
  state.views = views.ok ? await views.json() : [];
  const ad = await fetch("/api/ad");
  state.ad = ad.ok ? await ad.json() : null;
  $("btn-ad").hidden = !state.ad;
  $("empty").hidden = true;
  renderMeta();
  renderExplanation();
  renderViolations();
  renderViewPicker();
  const fromHash = decodeURIComponent(location.hash.replace(/^#view=/, ""));
  const wanted = [preferredView, fromHash, state.view?.id, "dependencies", "overview"]
    .find((id) => id && (viewById(id) || adViewFile(id)));
  await showView(wanted ?? state.views[0]?.id ?? null);
}

function renderMeta() {
  const { run, repo_name: repoName } = state.payload;
  const title = model().title || repoName;
  $("title").textContent = title;
  document.title = `${title} · archdiag`;
  const bits = [repoName, run.mode === "static" ? "static analysis" : "LLM only"];
  if (run.model && run.annotated !== false) bits.push(run.model);
  if (run.created_at) bits.push(new Date(run.created_at).toLocaleString());
  $("meta").textContent = bits.join(" · ");

  const warnings = run.warnings ?? [];
  $("warnings-panel").hidden = warnings.length === 0;
  $("warnings").replaceChildren(...warnings.map((warning) => el("li", { text: warning })));
}

function renderExplanation() {
  const text = model().explanation || "";
  const paragraphs = text.split(/\n\s*\n/).filter((p) => p.trim());
  $("explanation").replaceChildren(
    ...(paragraphs.length
      ? paragraphs.map((p) => el("p", { text: p.trim() }))
      : [el("p", { class: "none", text: "No written overview: static analysis only (--no-llm)." })]),
  );
}

function renderViolations() {
  const violations = model().violations ?? [];
  $("violations-panel").hidden = violations.length === 0;
  $("violations-count").textContent = violations.length ? `(${violations.length})` : "";
  const byId = nodesById();
  $("violations").replaceChildren(
    ...violations.map((violation) => {
      const li = el("li", {}, el("span", { class: "rule", text: violation.rule }),
        el("span", { text: violation.message }));
      const list = evidenceList(violation.evidence, violation.evidence_total);
      if (list) li.append(list);
      if (byId.has(violation.from)) {
        li.append(el("button", {
          type: "button", text: `Show ${byId.get(violation.from).label}`,
          onclick: () => focusNode(violation.from),
        }));
      }
      return li;
    }),
  );
}

const AD_TITLES = {
  "c4/context.mmd": "C4: system context",
  "c4/container.mmd": "C4: containers",
  "autosar/swc.mmd": "AUTOSAR-style SWCs (derived)",
  "autosar/arxml.mmd": "AUTOSAR composition (ARXML)",
  "safety/safety.mmd": "Safety architecture (ASIL)",
};

function adDiagrams() {
  return (state.ad?.files ?? []).filter((file) => file.endsWith(".mmd"));
}

function adViewFile(id) {
  return id?.startsWith("ad:") && adDiagrams().includes(id.slice(3)) ? id.slice(3) : null;
}

function adTitle(file) {
  if (AD_TITLES[file]) return AD_TITLES[file];
  const component = /^c4\/component-(.+)\.mmd$/.exec(file);
  if (component) return `C4: components of ${component[1]}`;
  const code = /^c4\/code-(.+)\.mmd$/.exec(file);
  if (code) return `C4: code of ${code[1]}`;
  return file;
}

function renderViewPicker() {
  const select = $("view-select");
  const analysis = el("optgroup", { label: "Analysis views" },
    ...state.views.map((view) => el("option", {
      value: view.id,
      text: view.parent ? `   ↳ ${view.title}` : view.title,
    })));
  const standard = adDiagrams();
  const groups = [analysis];
  if (standard.length) {
    groups.push(el("optgroup", { label: "Standard views (archdiag ad)" },
      ...standard.map((file) => el("option", { value: `ad:${file}`, text: adTitle(file) }))));
  }
  select.replaceChildren(...groups);
  select.hidden = state.views.length + standard.length < 2;
}

// ---- views and rendering ---------------------------------------------------------------------

async function showView(id) {
  const file = adViewFile(id);
  state.view = file ? { id, title: adTitle(file), description: "" } : id ? viewById(id) : null;
  $("view-select").value = id ?? "";
  $("btn-up").hidden = !state.view?.parent;
  $("view-description").textContent = file
    ? `${adTitle(file)} — generated by archdiag ad (${file})`
    : state.view?.description ?? "";
  if (id) history.replaceState(null, "", `#view=${encodeURIComponent(id)}`);
  const url = file
    ? `/api/ad/file?name=${encodeURIComponent(file)}`
    : id ? `/api/diagram?view=${encodeURIComponent(id)}` : "/api/diagram";
  const response = await fetch(url);
  state.source = response.ok ? await response.text() : "";
  await renderDiagram();
  if (state.selected && state.nodeElements.has(state.selected)) selectNode(state.selected);
  else closeDetails();
}

function nodeIdOf(element, known) {
  for (const candidate of [element.getAttribute("data-id"), element.id]) {
    if (!candidate) continue;
    const match = /(?:^|-)n_([a-z][a-z0-9_]*)(?:-\d+)?$/.exec(candidate);
    if (match && known.has(match[1])) return match[1];
  }
  return null;
}

async function renderDiagram() {
  if (!state.source) {
    $("viewport").replaceChildren();
    state.svg = null;
    return;
  }
  state.renders += 1;
  const { svg } = await window.mermaid.render(`archdiag-graph-${state.renders}`, state.source);
  const clean = window.DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ["style"],
  });
  const template = document.createElement("template");
  template.innerHTML = clean;
  const svgEl = template.content.querySelector("svg");
  if (!svgEl) throw new Error("The diagram could not be rendered.");
  for (const anchor of svgEl.querySelectorAll("a")) anchor.replaceWith(...anchor.childNodes);

  const box = svgEl.viewBox.baseVal;
  svgEl.removeAttribute("style");
  if (box && box.width) {
    svgEl.setAttribute("width", String(box.width));
    svgEl.setAttribute("height", String(box.height));
  }

  const known = nodesById();
  state.nodeElements = new Map();
  for (const element of svgEl.querySelectorAll("g.node")) {
    const id = nodeIdOf(element, known);
    if (!id) continue;
    state.nodeElements.set(id, element);
    element.classList.add("clickable");
    element.setAttribute("tabindex", "0");
    element.setAttribute("role", "button");
    element.setAttribute("aria-label", known.get(id).label);
    const activate = () => {
      const child = viewById(`dependencies/${id}`);
      if (known.get(id).level === "layer" && child) showView(child.id);
      else selectNode(id);
    };
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      activate();
    });
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  }
  state.svg = svgEl;
  $("viewport").replaceChildren(svgEl);
  fit();
}

// ---- details -----------------------------------------------------------------------------------

function vscodeLink(path, line) {
  const root = state.payload.root.replace(/\/$/, "");
  return `vscode://file${encodeURI(`${root}/${path}`)}${line ? `:${line}` : ""}`;
}

function evidenceList(evidence, total) {
  if (!evidence?.length) return null;
  const list = el("ul", { class: "evidence" });
  for (const item of evidence) {
    list.append(el("li", {},
      el("a", { href: vscodeLink(item.file, item.line), text: `${item.file}:${item.line}` }),
      el("span", { class: `detail confidence-${item.confidence}`, text: item.detail || item.fact })));
  }
  if (total > evidence.length) list.append(el("li", { class: "none", text: `… ${total - evidence.length} more` }));
  return list;
}

function edgeItem(edge, otherId, byId) {
  const li = el("li", {},
    el("button", { type: "button", text: byId.get(otherId)?.label ?? otherId,
                   onclick: () => focusNode(otherId) }),
    el("span", { class: "meta-line",
                 text: [edge.label, edge.kind, `×${edge.weight}`, edge.confidence]
                   .filter(Boolean).join(" · ") }));
  const list = evidenceList(edge.evidence, edge.evidence_total);
  if (list) li.append(el("details", {}, el("summary", { text: "evidence" }), list));
  return li;
}

function listOrNone(listEl, items) {
  listEl.replaceChildren(...(items.length ? items : [el("li", { class: "none", text: "—" })]));
}

function focusNode(id) {
  if (state.nodeElements.has(id)) {
    selectNode(id);
    return;
  }
  const node = nodesById().get(id);
  const home = state.views.find((view) => view.node_ids?.includes?.(id))
    ?? model().views.find((view) => view.node_ids.includes(id));
  if (home && home.id !== state.view?.id) {
    state.selected = id;
    showView(home.id);
  } else if (node) {
    selectNode(id);
  }
}

function selectNode(id) {
  const byId = nodesById();
  const node = byId.get(id);
  if (!node) return;
  state.nodeElements.get(state.selected)?.classList.remove("selected");
  state.selected = id;
  state.nodeElements.get(id)?.classList.add("selected");

  const groups = new Map((model().groups ?? []).map((g) => [g.id, g]));
  $("details-title").textContent = node.label;
  const facts = [node.kind];
  if (node.layer) facts.push(`layer ${node.layer}`);
  else if (node.group && groups.has(node.group)) facts.push(groups.get(node.group).label);
  if (node.metrics?.files) facts.push(`${node.metrics.files} files`);
  if (node.metrics?.lines) facts.push(`${node.metrics.lines} lines`);
  $("details-kind").textContent = facts.join(" · ");
  $("details-description").textContent = node.description || "";
  $("details-responsibilities").replaceChildren(
    ...(node.responsibilities ?? []).map((item) => el("li", { text: item })));

  const actions = [];
  const classes = viewById(`classes/${id}`);
  if (classes) actions.push(el("button", { type: "button", text: "Show classes",
                                           onclick: () => showView(classes.id) }));
  const layerView = viewById(`dependencies/${id}`);
  if (layerView) actions.push(el("button", { type: "button", text: "Open layer",
                                             onclick: () => showView(layerView.id) }));
  $("details-actions").replaceChildren(...actions);

  listOrNone($("details-paths"), node.paths.map((path) => el("li", {},
    el("code", { text: path }),
    el("a", { href: vscodeLink(path), text: "Open", title: "Open in VS Code" }),
    el("button", { type: "button", text: "Preview", onclick: () => previewFile(path) }))));
  const edges = model().edges ?? [];
  listOrNone($("details-out"), edges.filter((e) => e.from === id).map((e) => edgeItem(e, e.to, byId)));
  listOrNone($("details-in"), edges.filter((e) => e.to === id).map((e) => edgeItem(e, e.from, byId)));
  $("details").hidden = false;
}

function closeDetails() {
  state.nodeElements.get(state.selected)?.classList.remove("selected");
  state.selected = null;
  $("details").hidden = true;
}

async function previewFile(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
  $("viewer-path").textContent = path;
  $("viewer-open").href = vscodeLink(path);
  if (response.ok) {
    $("viewer-text").textContent = (await response.json()).text;
  } else {
    const body = await response.json().catch(() => ({}));
    $("viewer-text").textContent = body.detail ?? `Preview unavailable (HTTP ${response.status}).`;
  }
  $("viewer").showModal();
}

// ---- pan and zoom ------------------------------------------------------------------------------

function applyTransform() {
  $("viewport").style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
}

function fit() {
  if (!state.svg) return;
  const canvas = $("canvas").getBoundingClientRect();
  const width = Number(state.svg.getAttribute("width")) || state.svg.getBBox().width;
  const height = Number(state.svg.getAttribute("height")) || state.svg.getBBox().height;
  const scale = Math.min(canvas.width / width, (canvas.height - 40) / height, 2) * 0.92;
  state.scale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  state.tx = (canvas.width - width * state.scale) / 2;
  state.ty = (canvas.height - height * state.scale) / 2 + 10;
  applyTransform();
}

function setupPanZoom() {
  const canvas = $("canvas");
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const next = Math.min(8, Math.max(0.1, state.scale * Math.exp(-event.deltaY * 0.0015)));
    const factor = next / state.scale;
    state.tx = px - (px - state.tx) * factor;
    state.ty = py - (py - state.ty) * factor;
    state.scale = next;
    applyTransform();
  }, { passive: false });
  let drag = null;
  canvas.addEventListener("pointerdown", (event) => {
    if (event.target.closest?.("g.node.clickable")) return;
    drag = { x: event.clientX, y: event.clientY, tx: state.tx, ty: state.ty };
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    state.tx = drag.tx + event.clientX - drag.x;
    state.ty = drag.ty + event.clientY - drag.y;
    applyTransform();
  });
  const end = () => {
    drag = null;
    canvas.classList.remove("dragging");
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
}

// ---- export ------------------------------------------------------------------------------------

function baseName() {
  const view = state.view ? `-${state.view.id.replace(/\//g, "_")}` : "";
  return `${state.payload?.repo_name || "diagram"}${view}`.replace(/[^\w.-]+/g, "_");
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: name });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function svgMarkup() {
  const clone = state.svg.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  for (const node of clone.querySelectorAll(".selected")) node.classList.remove("selected");
  return new XMLSerializer().serializeToString(clone);
}

function exportSvg() {
  if (!state.svg) return;
  download(new Blob([svgMarkup()], { type: "image/svg+xml" }), `${baseName()}.svg`);
}

function exportPng() {
  if (!state.svg) return;
  const width = Number(state.svg.getAttribute("width"));
  const height = Number(state.svg.getAttribute("height"));
  const url = URL.createObjectURL(new Blob([svgMarkup()], { type: "image/svg+xml" }));
  const image = new Image();
  image.onload = () => {
    const ratio = 2;
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * ratio);
    canvas.height = Math.ceil(height * ratio);
    const context = canvas.getContext("2d");
    context.fillStyle = getComputedStyle(document.body).backgroundColor;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    canvas.toBlob((blob) => blob && download(blob, `${baseName()}.png`), "image/png");
  };
  image.onerror = () => setStatus("PNG export failed.", "error");
  image.src = url;
}

async function copyMermaid() {
  if (!state.source) return;
  await navigator.clipboard.writeText(state.source);
  setStatus("Mermaid source copied.");
  setTimeout(() => setStatus(""), 1500);
}

// ---- re-analysis over server-sent events ---------------------------------------------------------

function parseEvent(block) {
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, data: data.length ? JSON.parse(data.join("\n")) : {} };
}

async function reanalyze() {
  const button = $("btn-analyze");
  button.disabled = true;
  setStatus("Starting analysis…");
  try {
    const response = await fetch("/api/analyze", { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail ?? `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let failed = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const { event, data } = parseEvent(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (event === "progress") setStatus(`[${data.step}] ${data.message}`);
        if (event === "error") failed = data.message;
        if (event === "done") setStatus(`Done (${data.mode}): ${data.detail}.`);
      }
    }
    if (failed) throw new Error(failed);
    await load();
  } catch (error) {
    setStatus(`Analysis failed: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

// ---- architecture description ------------------------------------------------------------------

async function sanitizedDiagram(source) {
  state.renders += 1;
  const { svg } = await window.mermaid.render(`archdiag-doc-${state.renders}`, source);
  const clean = window.DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ["style"],
  });
  const template = document.createElement("template");
  template.innerHTML = clean;
  const svgEl = template.content.querySelector("svg");
  for (const anchor of svgEl?.querySelectorAll("a") ?? []) anchor.replaceWith(...anchor.childNodes);
  return svgEl;
}

function inline(text) {
  const parts = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith("`")) parts.push(el("code", { text: token.slice(1, -1) }));
    else if (token.startsWith("**")) parts.push(el("strong", { text: token.slice(2, -2) }));
    else parts.push(el("em", { text: token.slice(1, -1) }));
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(document.createTextNode(text.slice(last)));
  return parts;
}

function cells(line) {
  return line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

// Renders the subset of Markdown that archdiag writes. Every piece of text goes through
// textContent; Mermaid blocks become sanitised SVG.
function renderMarkdown(text, root) {
  const lines = text.split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i += 1;
      if (language === "mermaid") {
        const holder = el("div", { class: "diagram", text: "Rendering diagram…" });
        sanitizedDiagram(body.join("\n"))
          .then((svg) => holder.replaceChildren(svg ?? el("span", { text: "(empty diagram)" })))
          .catch((error) => holder.replaceChildren(el("pre", { text: `Diagram error: ${error.message}` })));
        blocks.push(holder);
      } else {
        blocks.push(el("pre", {}, el("code", { text: body.join("\n") })));
      }
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(el(`h${heading[1].length}`, {}, ...inline(heading[2])));
      i += 1;
      continue;
    }
    if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const [head, , ...body] = rows;
      blocks.push(el("table", {},
        el("thead", {}, el("tr", {}, ...cells(head).map((c) => el("th", {}, ...inline(c))))),
        el("tbody", {}, ...body.map((row) => el("tr", {}, ...cells(row).map((c) => el("td", {}, ...inline(c))))))));
      continue;
    }
    if (/^\s*[-*] /.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) {
        items.push(el("li", {}, ...inline(lines[i].replace(/^\s*[-*] /, ""))));
        i += 1;
      }
      blocks.push(el("ul", {}, ...items));
      continue;
    }
    if (line.startsWith(">")) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(el("blockquote", {}, ...inline(quote.join(" "))));
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !/^(#|\||```|>|\s*[-*] )/.test(lines[i])) {
      paragraph.push(lines[i++]);
    }
    blocks.push(el("p", {}, ...inline(paragraph.join(" "))));
  }
  root.replaceChildren(...blocks);
}

function openAD() {
  if (!state.ad) return;
  $("canvas").hidden = true;
  $("ad-page").hidden = false;
  $("ad-files").replaceChildren(...state.ad.files.map((file) => el("option", { value: file, text: file })));
  renderMarkdown(state.ad.document, $("ad-doc"));
}

function closeAD() {
  $("ad-page").hidden = true;
  $("canvas").hidden = false;
  fit();
}

async function downloadADFile() {
  const name = $("ad-files").value;
  if (!name) return;
  const response = await fetch(`/api/ad/file?name=${encodeURIComponent(name)}`);
  if (!response.ok) {
    setStatus(`Download failed (HTTP ${response.status}).`, "error");
    return;
  }
  download(new Blob([await response.text()], { type: "text/plain" }), name.replace(/\//g, "_"));
}

// ---- start -------------------------------------------------------------------------------------

function wire() {
  $("view-select").addEventListener("change", (event) => showView(event.target.value));
  $("btn-up").addEventListener("click", () => state.view?.parent && showView(state.view.parent));
  $("btn-ad").addEventListener("click", openAD);
  $("btn-ad-close").addEventListener("click", closeAD);
  $("btn-ad-download").addEventListener("click", () => downloadADFile().catch((e) => setStatus(e.message, "error")));
  $("btn-fit").addEventListener("click", fit);
  $("btn-copy").addEventListener("click", () => copyMermaid().catch((e) => setStatus(e.message, "error")));
  $("btn-svg").addEventListener("click", exportSvg);
  $("btn-png").addEventListener("click", exportPng);
  $("btn-analyze").addEventListener("click", reanalyze);
  $("details-close").addEventListener("click", closeDetails);
  $("viewer-close").addEventListener("click", () => $("viewer").close());
  window.addEventListener("resize", fit);
  darkQuery.addEventListener("change", () => {
    initMermaid();
    renderDiagram().catch((e) => setStatus(e.message, "error"));
  });
  setupPanZoom();
}

initMermaid();
wire();
load().catch((error) => setStatus(error.message, "error"));
