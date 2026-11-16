"use strict";

/* HookyMod's is a design workspace, independent of the existing file editor.
   Blocks can be simulated locally but do not produce or run game logic.
   No third-party visual editor or code from other applications is used. */
window.HookyStudio = (() => {
  const MAX_ELEMENTS = 100, MAX_BLOCKS = 100;
  const MAX_ASSET = 2 * 1024 * 1024, MAX_ASSETS = 6 * 1024 * 1024;
  const MAX_ARCHIVE = 12 * 1024 * 1024;
  const kinds = {
    item: { label: "Objet", icon: "◇" },
    scene: { label: "Cinématique", icon: "▷" },
    dungeon: { label: "Donjon", icon: "⌑" },
    building: { label: "Bâtiment", icon: "⌂" },
  };
  const blocks = {
    when_enter: { label: "Quand on entre", category: "event" },
    when_interact: { label: "Quand on interagit", category: "event" },
    when_use: { label: "Quand on utilise", category: "event" },
    if_has_item: { label: "Si possède un objet", category: "condition" },
    if_flag: { label: "Si un état est actif", category: "condition" },
    show_message: { label: "Afficher un message", category: "action" },
    give_item: { label: "Donner un objet", category: "action" },
    open_door: { label: "Ouvrir une porte", category: "action" },
    play_scene: { label: "Jouer une scène", category: "action" },
    wait: { label: "Attendre", category: "action" },
  };
  const s = { projects: [], unreadable: 0, loaded: false, active: null, selected: null,
              dirty: false, busy: false, operation: null, listSequence: 0, drag: "", simulation: null };
  let request, notify;
  const $ = (selector) => document.querySelector(selector);
  const tr = (text) => window.HookyI18n?.translate(text) ?? text;
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const selected = () => s.active?.elements.find((element) => element.id === s.selected) || null;
  const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body) });

  function summary(workspace) {
    return { id: workspace.id, name: workspace.name, description: workspace.description,
      author: workspace.author, target: workspace.target, revision: workspace.revision,
      count: workspace.elements.length };
  }
  function upsert(workspace) {
    const item = summary(workspace), index = s.projects.findIndex((project) => project.id === item.id);
    if (index < 0) s.projects.push(item); else s.projects[index] = item;
    s.projects.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  }
  function renderList() {
    const list = $("#studio-project-list");
    $("#studio-project-count").textContent = String(s.projects.length);
    const html = s.projects.map((saved) => {
      const project = s.active?.id === saved.id ? summary(s.active) : saved;
      return `<button type="button" class="studio-project-card" data-studio-project="${escape(project.id)}"
                aria-current="${project.id === s.active?.id}">
        <span class="studio-project-icon" aria-hidden="true">✦</span><span>
          <strong data-no-i18n title="${escape(project.name)}">${escape(project.name || tr("Sans nom"))}</strong>
          <small><span>${project.count}</span> <span>${escape(tr("élément(s)"))}</span> · <code>${escape(project.id.slice(0, 6))}</code></small>
        </span></button>`;
    }).join("");
    const empty = s.loaded ? tr("Aucun projet pour l'instant.") : tr("Chargement des projets…");
    const unreadable = s.unreadable ? `<p class="studio-project-empty"><span>${s.unreadable}</span> ${escape(tr("projet(s) illisible(s)"))}</p>` : "";
    list.innerHTML = (html || `<p class="studio-project-empty">${escape(empty)}</p>`) + unreadable;
  }
  function renderStatus() {
    const node = $("#studio-save-state");
    node.textContent = tr(s.busy ? "Opération en cours…" : s.dirty ? "Modifications non enregistrées" : "Tout est enregistré");
    node.classList.toggle("dirty", s.dirty);
  }
  function clearSimulation() {
    s.simulation = null;
    $("#studio-simulation-result").hidden = true;
  }
  function markDirty() {
    s.dirty = true;
    $("#studio-report").hidden = true;
    $("#studio-download-link").hidden = true;
    clearSimulation();
    renderStatus();
  }
  function renderElements() {
    const list = $("#studio-element-list"), elements = s.active?.elements || [];
    $("#studio-element-count").textContent = String(elements.length);
    list.innerHTML = elements.length ? elements.map((element) => {
      const kind = kinds[element.kind];
      return `<button type="button" class="studio-element-card" data-studio-element="${escape(element.id)}"
              aria-current="${element.id === s.selected}">
        <span class="studio-element-icon" aria-hidden="true">${kind.icon}</span><span>
          <strong data-no-i18n title="${escape(element.name)}">${escape(element.name || tr("Sans nom"))}</strong>
          <small>${escape(tr(kind.label))}</small></span></button>`;
    }).join("") : `<p class="studio-project-empty">${escape(tr("Aucun élément pour l'instant."))}</p>`;
  }
  function renderBlocks() {
    const stack = $("#studio-block-stack"), element = selected();
    const items = element?.blocks || [];
    $("#studio-block-count").textContent = `${items.length} / ${MAX_BLOCKS}`;
    stack.innerHTML = items.map((block, index) => {
      const item = blocks[block.type];
      const placeholder = item.category === "event" ? "Précision facultative" : "Valeur de conception";
      return `<div class="studio-block" draggable="true" data-block-index="${index}" data-category="${item.category}">
        <span class="studio-block-label">${escape(tr(item.label))}</span>
        <input type="text" data-block-value="${index}" data-no-i18n maxlength="256"
          aria-label="${escape(tr("Valeur du bloc"))}" placeholder="${escape(tr(placeholder))}" value="${escape(block.value)}">
        <span class="studio-block-actions">
          <button type="button" data-block-move="up" data-index="${index}" title="${escape(tr("Monter le bloc"))}" aria-label="${escape(tr("Monter le bloc"))}" ${index === 0 ? "disabled" : ""}>↑</button>
          <button type="button" data-block-move="down" data-index="${index}" title="${escape(tr("Descendre le bloc"))}" aria-label="${escape(tr("Descendre le bloc"))}" ${index === items.length - 1 ? "disabled" : ""}>↓</button>
          <button type="button" data-block-move="remove" data-index="${index}" title="${escape(tr("Retirer le bloc"))}" aria-label="${escape(tr("Retirer le bloc"))}">×</button>
        </span></div>`;
    }).join("");
  }
  function renderEditor() {
    const element = selected();
    $("#studio-editor").hidden = !element;
    if (!element) return;
    $("#studio-element-name").value = element.name;
    $("#studio-element-description").value = element.description;
    $("#studio-asset-name").textContent = element.asset?.name || "";
    $("#studio-asset-remove").hidden = !element.asset;
    renderBlocks();
  }
  function renderWorkspace() {
    const workspace = s.active;
    $("#studio-empty").hidden = Boolean(workspace);
    $("#studio-workspace").hidden = !workspace;
    if (!workspace) return;
    $("#studio-project-title").textContent = workspace.name;
    $("#studio-name").value = workspace.name;
    $("#studio-author").value = workspace.author;
    $("#studio-target").value = workspace.target;
    $("#studio-description").value = workspace.description;
    renderStatus();
    renderElements();
    renderEditor();
  }
  function render() { renderList(); renderWorkspace(); }
  function setBusy(value) {
    s.busy = value;
    $("#studio-controls").disabled = value;
    renderStatus();
  }
  // Serialize UI operations; a native close or project switch can await them.
  function perform(fn) {
    if (s.operation) return Promise.resolve(false);
    setBusy(true);
    const promise = Promise.resolve().then(fn).catch((error) => {
      notify(error?.message || tr("Opération impossible."), "error");
      return false;
    }).finally(() => {
      s.operation = null;
      setBusy(false);
    });
    s.operation = promise;
    return promise;
  }
  async function refreshList() {
    const serial = ++s.listSequence;
    try {
      const result = await request("/api/studio/workspaces", { background: true });
      if (serial !== s.listSequence) return;
      s.projects = result.workspaces;
      s.unreadable = result.unreadable;
      s.loaded = true;
      renderList();
    } catch (error) {
      if (serial === s.listSequence) {
        s.loaded = true;
        renderList();
        notify(error.message, "error");
      }
    }
  }
  function setActive(workspace, selectedId = null) {
    s.active = workspace;
    s.selected = workspace?.elements.some((element) => element.id === selectedId)
      ? selectedId : workspace?.elements[0]?.id || null;
    s.dirty = false;
    $("#studio-report").hidden = true;
    $("#studio-download-link").hidden = true;
    clearSimulation();
    if (workspace) upsert(workspace);
    render();
  }
  function mayDiscard() {
    return !s.dirty || window.confirm(tr("Ce projet contient des modifications non enregistrées. Les abandonner ?"));
  }
  async function openProject(id, force = false) {
    if (s.busy || (!force && s.active?.id === id) || !mayDiscard()) return false;
    return perform(async () => {
      const result = await request(`/api/studio/workspace?id=${encodeURIComponent(id)}`);
      setActive(result.workspace);
      return true;
    });
  }
  async function flush() {
    if (s.operation) await s.operation;
    if (!s.active || !s.dirty) return true;
    return Boolean(await perform(async () => {
      const result = await post("/api/studio/workspace", { workspace: s.active });
      // Controls are disabled while the request is in flight, so no draft can
      // be silently overwritten by an older save response.
      const element = s.selected;
      setActive(result.workspace, element);
      void refreshList();
      return true;
    }));
  }
  function newId() {
    if (!globalThis.crypto?.getRandomValues) throw new Error(tr("Générateur d'identifiants indisponible."));
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  function addElement(kind) {
    if (s.busy || !s.active || !Object.hasOwn(kinds, kind)) return;
    if (s.active.elements.length >= MAX_ELEMENTS) return notify(tr("Limite de 100 éléments atteinte."), "error");
    try {
      const element = { id: newId(), kind, name: `${tr(kinds[kind].label)} ${s.active.elements.length + 1}`,
                        description: "", asset: null, blocks: [] };
      s.active.elements.push(element);
      s.selected = element.id;
      markDirty(); renderList(); renderElements(); renderEditor();
      $("#studio-element-name").focus();
    } catch (error) { notify(error.message, "error"); }
  }
  function insertBlock(type, index = null) {
    const element = selected();
    if (s.busy || !element || !Object.hasOwn(blocks, type)) return;
    if (element.blocks.length >= MAX_BLOCKS) return notify(tr("Limite de 100 blocs atteinte."), "error");
    const position = index === null ? element.blocks.length : Math.max(0, Math.min(index, element.blocks.length));
    element.blocks.splice(position, 0, { type, value: "" });
    markDirty(); renderBlocks();
  }
  function moveBlock(from, to) {
    const element = selected();
    if (s.busy || !element || !Number.isInteger(from) || !Number.isInteger(to)
        || from < 0 || from >= element.blocks.length || to < 0 || to >= element.blocks.length || from === to) return;
    const [item] = element.blocks.splice(from, 1);
    element.blocks.splice(to, 0, item);
    markDirty(); renderBlocks();
  }
  function byteLength(encoded) {
    return Math.floor(encoded.length * 3 / 4) - (encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0);
  }
  async function encodeFile(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  }
  async function attach(file) {
    const element = selected();
    if (s.busy || !file || !element) return;
    if (!/\.(png|obj|glb)$/i.test(file.name) || !file.size || file.size > MAX_ASSET) {
      return notify(tr("Choisis un PNG, OBJ ou GLB non vide de 2 Mio maximum."), "error");
    }
    const otherBytes = s.active.elements.reduce((sum, item) =>
      sum + (item !== element && item.asset ? byteLength(item.asset.data) : 0), 0);
    if (otherBytes + file.size > MAX_ASSETS) return notify(tr("Les ressources dépassent 6 Mio au total."), "error");
    await perform(async () => {
      element.asset = { name: file.name, data: await encodeFile(file) };
      markDirty(); renderEditor();
      return true;
    });
  }
  async function importProject(file) {
    if (s.busy || !file) return;
    if (!/\.hooky$/i.test(file.name) || !file.size || file.size > MAX_ARCHIVE) {
      return notify(tr("Choisis un fichier .hooky non vide de 12 Mio maximum."), "error");
    }
    if (!mayDiscard()) return;
    await perform(async () => {
      const result = await post("/api/studio/import", { archive: await encodeFile(file) });
      setActive(result.workspace);
      void refreshList();
      notify(tr("Projet importé dans un nouvel espace."));
      return true;
    });
  }
  async function exportProject() {
    if (s.busy || !s.active || !await flush()) return;
    const link = $("#studio-download-link");
    link.href = `/api/studio/export?id=${encodeURIComponent(s.active.id)}`;
    link.hidden = false;
    link.click();
  }
  async function checkProject() {
    if (s.busy || !s.active) return;
    await perform(async () => {
      const result = await post("/api/studio/check", { workspace: s.active });
      const report = $("#studio-report");
      report.innerHTML = `<strong>${escape(tr("Vérification du schéma"))}</strong>
        <p>${escape(tr(result.message))}</p>` + (result.issues.length
        ? `<ul>${result.issues.map((issue) => `<li data-no-i18n>${escape(issue)}</li>`).join("")}</ul>`
        : `<p>${escape(tr("Pas d'avertissement de conception."))}</p>`);
      report.hidden = false;
      return true;
    });
  }
  function renderSimulation() {
    const result = s.simulation, report = $("#studio-simulation-result");
    if (!result) { report.hidden = true; return; }
    const labels = { event_matched: "Événement sélectionné", event_ignored: "Événement non sélectionné",
      condition_true: "Condition remplie", condition_false: "Condition non remplie",
      skipped: "Ignoré", missing: "Valeur manquante", simulated: "Action simulée" };
    const values = (items) => items.length
      ? items.map((value) => `<span data-no-i18n>${escape(value)}</span>`).join(", ")
      : escape(tr("Aucun"));
    const hint = !result.trace.length ? "Aucun bloc à simuler."
      : !result.event_found ? "Aucun bloc ne correspond à cet événement." : "";
    report.innerHTML = `<strong>${escape(tr("Résultat de simulation"))}</strong>`
      + (hint ? `<p>${escape(tr(hint))}</p>` : "")
      + (result.trace.length ? `<ol>${result.trace.map((step) => `<li>
          <strong>${escape(tr(blocks[step.type].label))}</strong>
          <span class="studio-sim-state"> · ${escape(tr(labels[step.result]))}</span>
          ${step.value ? `: <span class="studio-sim-value" data-no-i18n>${escape(step.value)}</span>` : ""}
        </li>`).join("")}</ol>` : "")
      + `<p>${escape(tr("Inventaire simulé"))} : ${values(result.items)}</p>`
      + `<p>${escape(tr("Portes simulées"))} : ${values(result.doors)}</p>`
      + `<p>${escape(tr("Scènes simulées"))} : ${values(result.scenes)}</p>`;
    report.hidden = false;
  }
  async function simulate() {
    const element = selected();
    if (s.busy || !element) return;
    const lines = (selector) => $(selector).value.split(/\r\n|\r|\n/).map((line) => line.trim()).filter(Boolean);
    clearSimulation();
    await perform(async () => {
      // Asset bytes are irrelevant to this test and need not leave the browser.
      s.simulation = await post("/api/studio/simulate", {
        element: { ...element, asset: null }, event: $("#studio-simulation-event").value,
        items: lines("#studio-simulation-items"), flags: lines("#studio-simulation-flags"),
      });
      renderSimulation();
      return true;
    });
  }
  function wire({ api, toast }) {
    request = api; notify = toast;
    $("#studio-create-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (s.busy || !mayDiscard()) return;
      const name = $("#studio-new-name").value.trim();
      if (!name) return notify(tr("Donne un nom au projet."), "error");
      void perform(async () => {
        const result = await post("/api/studio/workspaces", { name });
        $("#studio-new-name").value = "";
        setActive(result.workspace);
        void refreshList();
        return true;
      });
    });
    $("#studio-project-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-studio-project]");
      if (button) void openProject(button.dataset.studioProject);
    });
    $("#studio-import-button").onclick = () => $("#studio-import-file").click();
    $("#studio-import-file").onchange = (event) => {
      const file = event.target.files?.[0]; event.target.value = "";
      void importProject(file);
    };
    $("#studio-save").onclick = () => { void flush(); };
    $("#studio-reload").onclick = () => { if (s.active) void openProject(s.active.id, true); };
    $("#studio-copy").onclick = () => {
      if (s.busy || !s.active) return;
      void perform(async () => {
        const result = await post("/api/studio/copy", { workspace: s.active });
        setActive(result.workspace, s.selected);
        void refreshList();
        notify(tr("Copie créée dans un nouvel espace."));
        return true;
      });
    };
    $("#studio-check").onclick = () => { void checkProject(); };
    $("#studio-export").onclick = () => { void exportProject(); };
    $("#studio-simulate").onclick = () => { void simulate(); };
    for (const selector of ["#studio-simulation-event", "#studio-simulation-items", "#studio-simulation-flags"]) {
      $(selector).addEventListener("input", clearSimulation);
      $(selector).addEventListener("change", clearSimulation);
    }
    $("#studio-delete").onclick = () => {
      if (s.busy || !s.active || !window.confirm(tr("Supprimer définitivement ce projet et ses éléments, y compris les modifications non enregistrées ?"))) return;
      void perform(async () => {
        const id = s.active.id;
        await post("/api/studio/delete", { id, revision: s.active.revision });
        s.projects = s.projects.filter((project) => project.id !== id);
        setActive(null);
        void refreshList();
        return true;
      });
    };
    for (const [id, key] of [["studio-name", "name"], ["studio-author", "author"],
                              ["studio-target", "target"], ["studio-description", "description"]]) {
      $(`#${id}`).addEventListener("input", (event) => {
        if (s.busy || !s.active) return;
        s.active[key] = event.target.value;
        if (key === "name") { $("#studio-project-title").textContent = s.active.name; renderList(); }
        markDirty();
      });
    }
    document.querySelectorAll("[data-studio-kind]").forEach((button) => {
      button.onclick = () => addElement(button.dataset.studioKind);
    });
    $("#studio-element-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-studio-element]");
      if (s.busy || !button || !s.active?.elements.some((item) => item.id === button.dataset.studioElement)) return;
      s.selected = button.dataset.studioElement;
      clearSimulation();
      renderElements(); renderEditor();
    });
    for (const [id, key] of [["studio-element-name", "name"], ["studio-element-description", "description"]]) {
      $(`#${id}`).addEventListener("input", (event) => {
        const element = selected();
        if (s.busy || !element) return;
        element[key] = event.target.value;
        if (key === "name") renderElements();
        markDirty();
      });
    }
    $("#studio-element-delete").onclick = () => {
      const element = selected();
      if (s.busy || !element || !window.confirm(tr("Supprimer cet élément et ses blocs ?"))) return;
      s.active.elements = s.active.elements.filter((item) => item.id !== element.id);
      s.selected = s.active.elements[0]?.id || null;
      markDirty(); renderList(); renderElements(); renderEditor();
    };
    $("#studio-asset-button").onclick = () => $("#studio-asset-file").click();
    $("#studio-asset-file").onchange = (event) => {
      const file = event.target.files?.[0]; event.target.value = ""; void attach(file);
    };
    $("#studio-asset-remove").onclick = () => {
      if (s.busy || !selected()) return;
      selected().asset = null; markDirty(); renderEditor();
    };
    document.querySelectorAll("[data-studio-block]").forEach((button) => {
      button.onclick = () => insertBlock(button.dataset.studioBlock);
      button.addEventListener("dragstart", (event) => {
        if (s.busy) { event.preventDefault(); return; }
        s.drag = `palette:${button.dataset.studioBlock}`;
        event.dataTransfer.setData("text/plain", `hooky:${s.drag}`);
        event.dataTransfer.effectAllowed = "copy";
      });
      button.addEventListener("dragend", () => { s.drag = ""; $("#studio-canvas").classList.remove("over"); });
    });
    const stack = $("#studio-block-stack"), canvas = $("#studio-canvas");
    stack.addEventListener("input", (event) => {
      const input = event.target.closest("[data-block-value]"), element = selected();
      if (s.busy || !input || !element) return;
      const index = Number(input.dataset.blockValue);
      if (!element.blocks[index]) return;
      element.blocks[index].value = input.value;
      markDirty();
    });
    stack.addEventListener("click", (event) => {
      const button = event.target.closest("[data-block-move]"), element = selected();
      if (s.busy || !button || !element) return;
      const index = Number(button.dataset.index), direction = button.dataset.blockMove;
      if (direction === "remove" && element.blocks[index]) {
        element.blocks.splice(index, 1); markDirty(); renderBlocks();
      } else if (direction === "up") moveBlock(index, index - 1);
      else if (direction === "down") moveBlock(index, index + 1);
    });
    stack.addEventListener("dragstart", (event) => {
      const block = event.target.closest("[data-block-index]");
      if (s.busy || !block || event.target.closest("input, button")) { event.preventDefault(); return; }
      s.drag = `stack:${block.dataset.blockIndex}`;
      event.dataTransfer.setData("text/plain", `hooky:${s.drag}`);
      event.dataTransfer.effectAllowed = "move";
    });
    stack.addEventListener("dragend", () => { s.drag = ""; canvas.classList.remove("over"); });
    canvas.addEventListener("dragover", (event) => {
      if (s.busy || !s.drag) return;
      event.preventDefault(); canvas.classList.add("over");
    });
    canvas.addEventListener("dragleave", (event) => {
      if (!canvas.contains(event.relatedTarget)) canvas.classList.remove("over");
    });
    canvas.addEventListener("drop", (event) => {
      event.preventDefault(); canvas.classList.remove("over");
      if (s.busy || !s.drag || !selected()) return;
      const target = event.target.closest("[data-block-index]");
      const destination = target ? Number(target.dataset.blockIndex) : selected().blocks.length;
      const [source, value] = s.drag.split(":"); s.drag = "";
      if (source === "palette") insertBlock(value, destination);
      else if (source === "stack") {
        const from = Number(value);
        // A drop on a block inserts before it; a drop on the background appends.
        moveBlock(from, Math.min(selected().blocks.length - 1, destination - (from < destination ? 1 : 0)));
      }
    });
    window.addEventListener("beforeunload", (event) => {
      if (s.dirty || s.operation) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("hooky-language-change", () => { render(); renderSimulation(); });
    render();
  }
  function show() { render(); void refreshList(); }
  return { wire, show, flush, isDirty: () => s.dirty };
})();
