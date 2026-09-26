// archdiag web viewer. All model text reaches the DOM through textContent; the diagram SVG is
// sanitised with DOMPurify and stripped of every link before it is inserted.

const $ = (id) => document.getElementById(id);

const state = {
  payload: null,
  source: "",
  svg: null,
  nodeElements: new Map(),
  selected: null,
  scale: 1,
  tx: 0,
  ty: 0,
  renders: 0,
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

function nodesById() {
  return new Map((state.payload?.model.nodes ?? []).map((node) => [node.id, node]));
}

async function load() {
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
  const diagram = await fetch("/api/diagram");
  state.source = diagram.ok ? await diagram.text() : "";
  $("empty").hidden = true;
  renderMeta();
  renderExplanation();
  await renderDiagram();
  if (state.selected && nodesById().has(state.selected)) selectNode(state.selected);
  else closeDetails();
}

function renderMeta() {
  const { model, run, repo_name: repoName } = state.payload;
  $("title").textContent = model.title || repoName;
  document.title = `${model.title || repoName} · archdiag`;
  const bits = [repoName];
  if (run.model) bits.push(run.model);
  if (run.created_at) bits.push(new Date(run.created_at).toLocaleString());
  $("meta").textContent = bits.join(" · ");

  const warnings = run.warnings ?? [];
  $("warnings-panel").hidden = warnings.length === 0;
  $("warnings").replaceChildren(
    ...warnings.map((warning) => {
      const li = document.createElement("li");
      li.textContent = warning;
      return li;
    }),
  );
}

function renderExplanation() {
  const paragraphs = (state.payload.model.explanation || "").split(/\n\s*\n/);
  $("explanation").replaceChildren(
    ...paragraphs
      .filter((text) => text.trim())
      .map((text) => {
        const p = document.createElement("p");
        p.textContent = text.trim();
        return p;
      }),
  );
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
  if (!state.source) return;
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
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      selectNode(id);
    });
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(id);
      }
    });
  }
  state.svg = svgEl;
  $("viewport").replaceChildren(svgEl);
  fit();
}

function vscodeLink(path) {
  const root = state.payload.root.replace(/\/$/, "");
  return `vscode://file${encodeURI(`${root}/${path}`)}`;
}

function edgeItem(otherId, label, byId) {
  const li = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = byId.get(otherId)?.label ?? otherId;
  button.addEventListener("click", () => selectNode(otherId));
  li.append(button);
  if (label) {
    const span = document.createElement("span");
    span.className = "edge-label";
    span.textContent = label;
    li.append(span);
  }
  return li;
}

function listOrNone(listEl, items) {
  if (items.length) {
    listEl.replaceChildren(...items);
    return;
  }
  const li = document.createElement("li");
  li.className = "none";
  li.textContent = "—";
  listEl.replaceChildren(li);
}

function selectNode(id) {
  const byId = nodesById();
  const node = byId.get(id);
  if (!node) return;
  state.nodeElements.get(state.selected)?.classList.remove("selected");
  state.selected = id;
  state.nodeElements.get(id)?.classList.add("selected");

  const groups = new Map((state.payload.model.groups ?? []).map((g) => [g.id, g]));
  $("details-title").textContent = node.label;
  const group = node.group ? groups.get(node.group)?.label : null;
  $("details-kind").textContent = group ? `${node.kind} · ${group}` : node.kind;
  $("details-description").textContent = node.description || "";

  listOrNone(
    $("details-paths"),
    node.paths.map((path) => {
      const li = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = path;
      const open = document.createElement("a");
      open.href = vscodeLink(path);
      open.textContent = "Open";
      open.title = "Open in VS Code";
      const preview = document.createElement("button");
      preview.type = "button";
      preview.textContent = "Preview";
      preview.addEventListener("click", () => previewFile(path));
      li.append(code, open, preview);
      return li;
    }),
  );
  const edges = state.payload.model.edges ?? [];
  listOrNone(
    $("details-out"),
    edges.filter((e) => e.from === id).map((e) => edgeItem(e.to, e.label, byId)),
  );
  listOrNone(
    $("details-in"),
    edges.filter((e) => e.to === id).map((e) => edgeItem(e.from, e.label, byId)),
  );
  $("details").hidden = false;
}

function closeDetails() {
  state.nodeElements.get(state.selected)?.classList.remove("selected");
  state.selected = null;
  $("details").hidden = true;
}

async function previewFile(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
  const viewer = $("viewer");
  $("viewer-path").textContent = path;
  $("viewer-open").href = vscodeLink(path);
  if (response.ok) {
    $("viewer-text").textContent = (await response.json()).text;
  } else {
    const body = await response.json().catch(() => ({}));
    $("viewer-text").textContent = body.detail ?? `Preview unavailable (HTTP ${response.status}).`;
  }
  viewer.showModal();
}

// ---- pan and zoom -------------------------------------------------------------------------

function applyTransform() {
  $("viewport").style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
}

function fit() {
  if (!state.svg) return;
  const canvas = $("canvas").getBoundingClientRect();
  const width = Number(state.svg.getAttribute("width")) || state.svg.getBBox().width;
  const height = Number(state.svg.getAttribute("height")) || state.svg.getBBox().height;
  const scale = Math.min(canvas.width / width, canvas.height / height, 2) * 0.92;
  state.scale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  state.tx = (canvas.width - width * state.scale) / 2;
  state.ty = (canvas.height - height * state.scale) / 2;
  applyTransform();
}

function setupPanZoom() {
  const canvas = $("canvas");
  canvas.addEventListener(
    "wheel",
    (event) => {
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
    },
    { passive: false },
  );
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

// ---- export -------------------------------------------------------------------------------

function baseName() {
  return (state.payload?.repo_name || "diagram").replace(/[^\w.-]+/g, "_");
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function svgMarkup() {
  const clone = state.svg.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  for (const el of clone.querySelectorAll(".selected")) el.classList.remove("selected");
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

// ---- re-analysis over server-sent events ----------------------------------------------------

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
        if (event === "done") setStatus(`Done: ${data.nodes} components, ${data.edges} relations.`);
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

// ---- start ----------------------------------------------------------------------------------

function wire() {
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
