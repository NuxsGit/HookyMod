"use strict";

/* ============================================================================
   HookyCreator — application (pas une page web : une fenêtre d'atelier)
   ========================================================================== */

const state = {
  project: null,
  settings: null,
  view: "home",
  filter: "",
  tabs: [],
  tab: "home",
  logs: [],
  models: [],
  ensembles: [],
  textures: [],
  blends: [],
  blend: { ref: "", objects: [] },
  busy: 0,
  selection: new Set(),
  anchor: null,
  shown: { models: 48, textures: 48, ensembles: 48 },
  system: null,
  fullscreen: false,
};

/* ------------------------------------------------------------------ outils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
));

function humanBytes(bytes) {
  if (!bytes) return "0 o";
  const units = ["o", "Ko", "Mo", "Go"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i ? 2 : 0)} ${units[i]}`;
}

const humanSize = (bytes) => humanBytes(bytes);
const now = () => new Date().toLocaleTimeString("fr-FR", { hour12: false });

function log(kind, text) {
  state.logs.push({ at: now(), kind, text: String(text) });
  if (state.logs.length > 300) state.logs.splice(0, state.logs.length - 300);
  renderLogs();
}

function renderLogs() {
  const console_ = $("#console-log");

  if (console_) {
    console_.innerHTML = state.logs.slice(-120).map((entry) => `
      <li class="${entry.kind}"><span class="time">${entry.at}</span>${esc(entry.text)}</li>`
    ).join("") || `<li class="muted">(rien pour l'instant)</li>`;
    console_.scrollTop = console_.scrollHeight;
  }
  const home = $("#home-log");
  if (home) {
    const last = [...state.logs].reverse().slice(0, 8);
    home.innerHTML = last.map((entry) => `
      <li class="${entry.kind}"><span class="time">${entry.at}</span>${esc(entry.text)}</li>`
    ).join("") || `<li class="muted">Aucune action pour l'instant.</li>`;
  }
}

function status(message, kind = "ok") {
  const node = $("#status-message");
  if (node) node.textContent = message;
  const dot = $("#status-dot");
  if (dot) dot.className = `status-dot ${kind === "ok" ? "" : kind}`.trim();
}

function busy(active) {
  state.busy = Math.max(0, state.busy + (active ? 1 : -1));
  const dot = $("#status-dot");
  if (dot && state.busy) dot.className = "status-dot busy";
}

function toast(message, kind = "ok") {
  const node = document.createElement("div");
  node.className = `toast ${kind === "error" ? "error" : ""}`;
  node.textContent = message;
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 9000 : 4000);
  log(kind === "error" ? "error" : "ok", message);
  status(message, kind === "error" ? "error" : "ok");
}

async function api(path, options = {}) {
  busy(true);
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const isJson = (response.headers.get("Content-Type") || "").includes("json");
    const payload = isJson ? await response.json() : await response.blob();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  } finally {
    busy(false);
  }
}

/* ------------------------------------------------------------------ onglets */
function ensureTab(tab) {
  const existing = state.tabs.find((item) => item.id === tab.id);
  if (existing) return existing;
  state.tabs.push(tab);
  return tab;
}

function renderTabs() {
  const strip = $("#tabs");
  if (!strip) return;
  strip.innerHTML = state.tabs.map((tab) => `
    <button class="tab" data-tab="${esc(tab.id)}" data-kind="${esc(tab.kind || "view")}"
            aria-selected="${state.tab === tab.id}">
      <i class="dot"></i>
      <span class="label">${esc(tab.label)}</span>
      ${tab.closable === false ? "" : `<span class="close" data-close="${esc(tab.id)}"
        title="Fermer">✕</span>`}
    </button>`).join("");
}

function ensureViewTab(view, label) {
  return ensureTab({ id: `view:${view}`, kind: "view", label, view, closable: view !== "home" });
}

function activateTab(id) {
  const tab = state.tabs.find((item) => item.id === id);
  if (!tab) return;
  state.tab = id;
  renderTabs();
  if (tab.view) return showView(tab.view, { tab: false });
  if (tab.doc) return openDoc(tab.doc);
}

function closeTab(id) {
  const index = state.tabs.findIndex((item) => item.id === id);
  if (index < 0) return;
  const tab = state.tabs[index];
  if (tab.closable === false) return;
  state.tabs.splice(index, 1);
  if (state.tab === id) {
    const fallback = state.tabs[Math.max(0, index - 1)] || state.tabs[0];
    if (fallback) activateTab(fallback.id);
  }
  renderTabs();
}

/* ------------------------------------------------------------------ vues */
function showView(view, options = {}) {
  state.view = view;
  $$(".view").forEach((node) => { node.hidden = node.id !== `view-${view}`; });
  $$(".rail-btn[data-view]").forEach((node) => {
    if (node.dataset.view === view) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (options.tab !== false) {
    ensureViewTab(view, VIEW_LABELS[view] || view);
    state.tab = `view:${view}`;
    renderTabs();
  }
  if (view === "home") renderHome();
  if (view === "models") renderModels();
  if (view === "ensembles") renderEnsembles();
  if (view === "textures") renderTextures();
  if (view === "mobs") renderMobs();
  if (view === "blender") renderBlends();
  if (view === "settings") { renderSettings(); refreshSystem(); }
}

const VIEW_LABELS = {
  home: "Accueil",
  models: "Modèles",
  ensembles: "Ensembles",
  textures: "Textures",
  mobs: "Mobs",
  blender: "Blender",
  settings: "Réglages",
};

/* une image qui ne se charge pas : on marque la tuile au lieu d'afficher
   l'icône « fichier manquant » du navigateur (visible dans toutes les grilles) */
document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || image.dataset.broken) return;
  image.dataset.broken = "1";
  image.classList.add("broken");
  const box = image.closest(".thumb, .strip-item");
  if (box) box.classList.add("broken");
}, true);

/* ------------------------------------------------------------------ barre d'état */
function renderStatus() {
  const assets = state.project?.assets || [];
  const count = (kind) => assets.filter((asset) => asset.kind === kind).length;
  const settings = state.settings || {};
  const node = $("#status-counts");
  if (node) {
    node.textContent = `${count("model")} modèles · ${count("animation")} anims · `
      + `${count("texture")} textures · `
      + `${state.project?.mobs?.length || 0} mobs`;
  }
  const budget = $("#status-budget");
  if (budget) {
    const cache = state.project?.cache?.thumbs;
    budget.textContent = `budget ${(settings.vram_gb ?? 6).toFixed(1).replace(".0", "")} Go`
      + (cache ? ` · cache ${humanBytes(cache.bytes)}` : "");
  }
  const project = $("#status-project");
  if (project) project.textContent = state.project?.root || "—";
  const name = $("#project-name");
  if (name) name.textContent = state.project?.root || "—";
  const stats = $("#stats");
  if (stats) {
    stats.innerHTML = `
      <span class="chip"><b>${count("texture")}</b> textures</span>
      <span class="chip"><b>${count("model")}</b> modèles</span>
      <span class="chip"><b>${count("animation")}</b> anims</span>
      <span class="chip"><b>${state.project?.mobs?.length || 0}</b> mobs</span>
      <span class="chip"><b>${state.project?.edited?.length || 0}</b> fichiers modifiés</span>
      <span class="chip" title="Budget mémoire des aperçus">budget
        <b>${(settings.vram_gb ?? 6).toFixed(1).replace(".0", "")} Go</b></span>`;
  }
}

/* ------------------------------------------------------------------ accueil */
function renderHome() {
  const project = state.project;
  if (!project) return;
  const edited = project.edited || [];
  const sub = $("#home-sub");
  if (sub) {
    sub.textContent = `${project.assets.length} fichier(s) repéré(s) · `
      + `${project.zstd ? (project.dicts ? `${project.dicts} dictionnaire(s) zstd` : "sans dictionnaire zstd") : "zstandard absent"}`;
  }
  const activity = $("#activity");
  if (activity) {
    activity.innerHTML = [
      ["Racine", `<code>${esc(project.root)}</code>`],
      ["Dossier de travail", `<code>${esc(project.work)}</code>`],
      ["Fichiers modifiés", edited.length ? `${edited.length} en attente d'assemblage` : "aucun"],
      ["Mobs créés", project.mobs.length || "aucun"],
      ["Réglages", `écrits dans <code>settings.json</code>`],
    ].map(([key, value]) => `<li><span class="k">${key}</span><span>${value}</span></li>`).join("");
  }
  renderLogs();
  renderStatus();
}

/* ------------------------------------------------------------------ modèles */
function thumbFaces() {
  return state.settings?.thumb_faces || "";
}

// 320 × 240 et « faces » du réglage : mêmes paramètres que le préchauffage serveur,
// sinon la vignette serait rendue une deuxième fois pour rien.
function modelThumb(asset) {
  const faces = thumbFaces();
  return `/api/model/preview?path=${encodeURIComponent(asset.rel)}&w=320&h=240`
    + (faces ? `&faces=${faces}` : "")
    + (asset.mtime ? `&v=${asset.mtime}` : "");
}

function textureThumb(asset) {
  return `/api/texture/png?path=${encodeURIComponent(asset.rel)}&index=0`
    + (asset.mtime ? `&v=${asset.mtime}` : "");
}

/* le suffixe d'un `.bntx` dit tout (Alb, Nrm, Rgh…) : on l'affiche en clair,
   sinon le nom tronqué ne dit plus de quelle carte il s'agit */
const ROLE_NAMES = {
  albedo: "Alb", normal: "Nrm", roughness: "Rgh", metallic: "Mtl",
  metalrough: "MR", occlusion: "AO", emissive: "Emi", specular: "Spc",
  mask: "Msk", autre: "?",
};
const ROLE_TITLES = {
  albedo: "albédo — couleur de base (Base Color)",
  normal: "normale — Normal Map",
  roughness: "rugosité — Roughness",
  metallic: "métallique — Metallic",
  metalrough: "metallicRoughness combinée (G = rugosité, B = métal)",
  occlusion: "occlusion — facteur AO",
  emissive: "émission — Emission",
  specular: "spéculaire — pas d'équivalent glTF, à régler à la main",
  mask: "masque / opacité — à brancher à la main",
  autre: "rôle inconnu — ouvre l'image pour décider",
};

function ROLE_BADGE(role) {
  if (!role) return "";
  const label = ROLE_NAMES[role] || "?";
  return `<span class="badge role role-${esc(role)}" title="${esc(ROLE_TITLES[role] || "")}">${label}</span>`;
}

function tileMarkup(asset, kind) {
  const key = `${kind}:${asset.rel}`;
  const chosen = state.selection.has(key);
  const shown = kind === "model"
    ? (state.shots ? `<img loading="lazy" decoding="async" alt="" src="${modelThumb(asset)}">`
                   : "aperçu désactivé")
    : `<img loading="lazy" decoding="async" alt="" src="${textureThumb(asset)}">`;
  return `
    <article class="tile ${chosen ? "selected" : ""}" data-kind="${kind}"
             data-rel="${esc(asset.rel)}" data-key="${esc(key)}">
      <button class="pick" data-pick="${esc(key)}" title="Sélectionner (Ctrl+clic)">✓</button>
      <div class="thumb ${shown === "aperçu désactivé" ? "placeholder" : ""} ${kind === "texture" ? "checker" : ""}">${shown}</div>
      <div class="meta">
        <strong title="${esc(asset.rel)}">${esc(asset.name)}</strong>
        <small>${humanSize(asset.size)}${asset.compressed ? " · compressé" : ""}
          ${kind === "texture" ? ROLE_BADGE(asset.role) : ""}</small>
      </div>
    </article>`;
}

function updateMore(kind, total, shown) {
  const box = $(`#${kind}-more`);
  const button = $(`#${kind}-more-btn`);
  if (!box || !button) return;
  box.hidden = total <= shown;
  button.textContent = `Afficher ${Math.min(48, total - shown)} de plus `
    + `(${total - shown} restant(s))`;
}

/* ------------------------------------------------------------------ ensembles */
function ensembleThumb(row) {
  const faces = thumbFaces();
  const textured = $("#ens-textures")?.checked !== false;
  return `/api/model/preview?path=${encodeURIComponent(row.rel)}&w=320&h=240`
    + (faces ? `&faces=${faces}` : "")
    + (textured ? "&tex=1" : "&tex=0")
    + (row.mtime ? `&v=${row.mtime}` : "");
}

function ensembleMatches(row) {
  return !state.filter || row.rel.toLowerCase().includes(state.filter);
}

async function renderEnsembles() {
  const grid = $("#ensemble-grid");
  if (!grid) return;
  if (!state.ensembles.length) {
    try {
      const payload = await api("/api/ensembles");
      state.ensembles = payload.ensembles || [];
      state.blender = payload.blender || null;
      renderBlenderState();
    } catch (error) { toast(error.message, "error"); return; }
  }
  const rows = state.ensembles.filter(ensembleMatches);
  const shown = rows.slice(0, state.shown.ensembles);
  const count = $("#ensemble-count");
  if (count) {
    const complete = state.ensembles.filter((row) => row.complete).length;
    count.textContent = `${state.ensembles.length} modèle(s) · ${complete} complet(s) `
      + "(textures + animations repérées)"
      + (state.filter ? ` · filtre « ${state.filter} »` : "");
  }
  grid.innerHTML = shown.length
    ? shown.map((row) => `
      <article class="tile ensemble" data-kind="model" data-rel="${esc(row.rel)}"
               data-key="model:${esc(row.rel)}" data-ensemble="1">
        <button class="pick" data-pick="model:${esc(row.rel)}" title="Sélectionner (Ctrl+clic)">✓</button>
        <div class="thumb">
          <img loading="lazy" decoding="async" alt="" src="${ensembleThumb(row)}">
        </div>
        <div class="meta">
          <strong title="${esc(row.rel)}">${esc(row.name)}</strong>
          <small>${humanSize(row.size)}</small>
          <span class="badges">
            <span class="badge ${row.textures ? "" : "off"}">${row.textures} texture(s)</span>
            <span class="badge ${row.animations ? "" : "off"}">${row.animations} anim(s)</span>
          </span>
        </div>
      </article>`).join("")
    : `<p class="muted">Aucun modèle repéré dans ce dossier.</p>`;
  const more = $("#ensemble-more");
  const button = $("#ensemble-more-btn");
  if (more && button) {
    more.hidden = rows.length <= shown.length;
    button.textContent = `Afficher ${Math.min(48, rows.length - shown.length)} de plus `
      + `(${rows.length - shown.length} restant(s))`;
  }
}

async function renderModels() {
  const grid = $("#model-grid");
  if (!grid) return;
  state.shots = $("#model-shots")?.checked !== false;
  const models = (state.project?.assets || [])
    .filter((asset) => asset.kind === "model" && matches(asset));
  state.models = models;
  const shown = models.slice(0, state.shown.models);
  const count = $("#model-count");
  if (count) {
    count.textContent = `${models.length} modèle(s)`
      + (state.filter ? ` pour « ${state.filter} »` : "")
      + (shown.length < models.length ? ` — ${shown.length} affichés` : "");
  }
  grid.innerHTML = shown.length
    ? shown.map((asset) => tileMarkup(asset, "model")).join("")
    : `<p class="muted">Aucun modèle repéré dans ce dossier.</p>`;
  updateMore("model", models.length, shown.length);
  updateSelectionBar();
}

function matches(asset) {
  return !state.filter || asset.rel.toLowerCase().includes(state.filter);
}

async function openModel(rel, options = {}) {
  let info;
  try {
    info = await api(`/api/model/info?path=${encodeURIComponent(rel)}`);
  } catch (error) { toast(error.message, "error"); return; }

  const name = info.models[0] || rel.split("/").pop();
  const qualities = [
    { id: "auto", label: "Auto (budget)", faces: null },
    { id: "detail", label: "Détaillé", faces: 400000 },
    { id: "rapide", label: "Rapide", faces: 30000 },
  ];
  const textured = info.textures.length ? "&tex=1" : "";
  const shot = (faces) => `/api/model/preview?path=${encodeURIComponent(rel)}&w=460&h=345`
    + (faces ? `&faces=${faces}` : "") + textured;

  showInspector("Modèle", `
    <h3>${esc(name)}</h3>
    <div class="sub">${esc(rel)}</div>
    <img class="preview" id="model-shot" alt="" src="${shot(null)}">
    <dl class="kv">
      <dt>Version</dt><dd>${esc(info.version)}</dd>
      <dt>Maillages</dt><dd>${info.meshes}${info.dropped ? ` <span class="warn">(${info.dropped} ignoré(s))</span>` : ""}</dd>
      <dt>Faces</dt><dd>${info.faces.toLocaleString("fr-FR")}</dd>
      <dt>Sommets</dt><dd>${info.vertices.toLocaleString("fr-FR")}</dd>
      <dt>Dimensions</dt><dd>${info.bounds.map((value) => value.toFixed(3)).join(" × ")}</dd>
      <dt>Matériaux</dt><dd>${info.materials.length ? info.materials.map(esc).join(", ") : "—"}</dd>
      <dt>Textures</dt><dd>${info.textures.length ? info.textures.map((t) => esc(t.name)).join(", ") : "aucune trouvée"}</dd>
      <dt>Animations</dt><dd>${info.animations.length ? `${info.animations.length} fichier(s)` : "aucune repérée"}</dd>
      <dt>Budget</dt><dd id="model-budget" class="muted">calcul…</dd>
    </dl>
    ${info.warnings.length ? `<details class="warns"><summary>${info.warnings.length} remarque(s) de décodage</summary>
      <ul class="todo">${info.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
    ${info.textures.length ? `<div class="ensemble-strip">
      <h4>Cartes du modèle (${info.textures.length})</h4>
      <div class="strip">${info.textures.map((t) => `
        <button class="strip-item" data-open-texture="${esc(t.rel)}" title="${esc(t.rel)}">
          <img loading="lazy" alt="" src="/api/texture/png?path=${encodeURIComponent(t.rel)}&index=0${t.mtime ? `&v=${t.mtime}` : ""}">
          <span>${esc(t.name)}</span>
          <span class="strip-role">${ROLE_BADGE(t.role)}</span>
        </button>`).join("")}</div>
    </div>` : ""}
    ${info.animations.length ? `<details class="warns anims"><summary>${info.animations.length} animation(s) liée(s)</summary>
      <ul class="todo">${info.animations.map((a) => `<li>${esc(a.name)} <span class="muted">${humanSize(a.size)}</span></li>`).join("")}</ul>
      <p class="hint">Les animations restent des fichiers séparés du jeu : elles ne sont pas
        encore injectées dans le modèle exporté.</p></details>` : ""}
    <div class="actions">
      <button class="btn" id="btn-uv">Vérifier les UV</button>
      <button class="btn primary" id="btn-blend">Exporter en .blend</button>
      <button class="btn" id="btn-blender">Exporter pour Blender (glTF/OBJ)</button>
      <button class="btn ghost" id="btn-turntable">Planche de rotations</button>
      <button class="btn ghost" id="btn-translate-model">Injecter un .blend…</button>
    </div>
    <label class="field"><span>Qualité de l'aperçu</span>
      <select id="model-quality">
        ${qualities.map((q) => `<option value="${q.id}">${esc(q.label)}</option>`).join("")}
      </select>
    </label>
    <p class="hint" id="model-budget-note">Le budget et le culling se règlent dans Réglages.</p>`);

  ensureTab({ id: `model:${rel}`, kind: "model", label: name, doc: { type: "model", rel } });
  state.tab = `model:${rel}`;
  renderTabs();
  markTile("model", rel);

  async function refreshBudget(faces) {
    const node = $("#model-budget");
    const note = $("#model-budget-note");
    if (!node) return;
    try {
      const payload = await api(`/api/model/plan?path=${encodeURIComponent(rel)}&w=460&h=345`
        + (faces ? `&faces=${faces}` : ""));
      const plan = payload.plan;
      node.innerHTML = `${humanBytes(plan.total_bytes)} estimés sur `
        + `${humanBytes(plan.budget_bytes)} (${Math.round(plan.fill * 100)} %)`;
      note.textContent = plan.notes.length ? plan.notes.join(" · ")
        : "Le modèle tient dans le budget sans dégradation.";
    } catch (error) {
      node.textContent = "indisponible";
      note.textContent = error.message;
    }
  }

  $("#btn-turntable").onclick = () => {
    $("#model-shot").src = `/api/model/preview?path=${encodeURIComponent(rel)}&w=460&h=345&views=4`;
  };
  $("#btn-blender").onclick = () => exportModel(rel);
  $("#btn-blend").onclick = () => exportBlend(rel);
  $("#btn-uv").onclick = () => showUv(rel);
  $$("[data-open-texture]", document).forEach((node) => {
    node.onclick = () => openTexture(node.dataset.openTexture);
  });
  $("#btn-translate-model").onclick = () => {
    showView("blender");
    const select = $("#blend-target");
    if (select) select.value = rel;
  };
  $("#model-quality").onchange = (event) => {
    const chosen = qualities.find((q) => q.id === event.target.value) || qualities[0];
    $("#model-shot").src = shot(chosen.faces);
    refreshBudget(chosen.faces);
  };
  refreshBudget(null);
  if (options.inspect === false) return;
}

/* ------------------------------------------------------------------ textures */
async function renderTextures() {
  const grid = $("#texture-grid");
  if (!grid) return;
  const role = $("#tex-role")?.value || "";
  const textures = (state.project?.assets || [])
    .filter((asset) => asset.kind === "texture" && matches(asset)
      && (!role || (asset.role || "autre") === role));
  state.textures = textures;
  const shown = textures.slice(0, state.shown.textures);
  const count = $("#texture-count");
  if (count) {
    const roleName = role ? (ROLE_NAMES[role] || role) : "";
    count.textContent = `${textures.length} texture(s)`
      + (role ? ` de type ${roleName}` : "")
      + (state.filter ? ` pour « ${state.filter} »` : "")
      + (shown.length < textures.length ? ` — ${shown.length} affichées` : "");
  }
  grid.innerHTML = shown.length
    ? shown.map((asset) => tileMarkup(asset, "texture")).join("")
    : `<p class="muted">Aucune texture repérée dans ce dossier.</p>`;
  updateMore("texture", textures.length, shown.length);
  updateSelectionBar();
}

async function openTexture(rel) {
  let info;
  try {
    info = await api(`/api/texture/info?path=${encodeURIComponent(rel)}`);
  } catch (error) { toast(error.message, "error"); return; }
  const texture = info.textures[0];
  const editable = info.textures.filter((item) => item.editable).length;
  showInspector("Texture", `
    <h3>${esc(texture.name)}</h3>
    <div class="sub">${esc(rel)}</div>
    <img class="preview" alt=""
      src="/api/texture/png?path=${encodeURIComponent(rel)}&index=0">
    <dl class="kv">
      <dt>Version</dt><dd>${esc(info.version)}${info.orin ? " · Orin" : ""}</dd>
      <dt>Format</dt><dd>${esc(texture.format)} ${texture.editable
        ? `<span class="badge">modifiable</span>`
        : `<span class="badge warn">non encodable</span>`}</dd>
      <dt>Taille</dt><dd>${texture.width} × ${texture.height}</dd>
      <dt>Mips</dt><dd>${texture.mips}</dd>
      <dt>Textures</dt><dd>${info.count} (${editable} modifiable(s))</dd>
    </dl>
    ${texture.editable ? "" : `<p class="hint">${esc(texture.hint)} — l'import est refusé
      plutôt que de produire un fichier faux.</p>`}
    <div class="actions">
      <button class="btn primary" id="btn-import" ${texture.editable ? "" : "disabled"}>
        Remplacer par une image…</button>
      <button class="btn ghost" id="btn-export-png">Exporter en PNG</button>
      <input type="file" id="file-import" accept="image/*" hidden>
    </div>`);

  ensureTab({ id: `texture:${rel}`, kind: "texture", label: texture.name, doc: { type: "texture", rel } });
  state.tab = `texture:${rel}`;
  renderTabs();
  markTile("texture", rel);

  $("#btn-export-png").onclick = async () => {
    try {
      const result = await api(`/api/texture/export?path=${encodeURIComponent(rel)}&index=0`);
      toast(`PNG écrit : ${result.file}`);
    } catch (error) { toast(error.message, "error"); }
  };

  const input = $("#file-import");
  $("#btn-import").onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const base64 = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.readAsDataURL(file);
    });
    try {
      const result = await api("/api/texture/import", {
        method: "POST",
        body: JSON.stringify({ path: rel, index: 0, image: base64 }),
      });
      toast(`Texture remplacée (${result.texture}) → ${result.wrote}`);
      await refresh();
      openTexture(rel);
    } catch (error) { toast(error.message, "error"); }
  };
}

/* ------------------------------------------------------------------ mobs */
function fillMobSelects() {
  const models = (state.project?.assets || []).filter((asset) => asset.kind === "model");
  const textures = (state.project?.assets || []).filter((asset) => asset.kind === "texture");
  const modelSelect = $("#mob-model");
  if (modelSelect) {
    modelSelect.innerHTML = models.length
      ? models.map((asset) => `<option value="${esc(asset.rel)}">${esc(asset.name)}</option>`).join("")
      : `<option value="">(aucun modèle)</option>`;
  }
  const textureSelect = $("#mob-textures");
  if (textureSelect) {
    textureSelect.innerHTML = textures
      .map((asset) => `<option value="${esc(asset.rel)}">${esc(asset.name)}</option>`).join("");
  }
  const target = $("#blend-target");
  if (target) {
    target.innerHTML = models.length
      ? models.map((asset) => `<option value="${esc(asset.rel)}">${esc(asset.name)}</option>`).join("")
      : `<option value="">(aucun modèle)</option>`;
  }
}

async function renderMobs() {
  fillMobSelects();
  const mobs = state.project?.mobs || [];
  $("#mob-grid").innerHTML = mobs.map((mob) => `
    <article class="tile" data-kind="mob" data-slug="${esc(mob.slug)}">
      <div class="meta">
        <strong>${esc(mob.name)}</strong>
        <small>parent : ${esc(mob.parent)}</small>
        <small>${mob.files.length} fichier(s) prêt(s)</small>
      </div>
    </article>`).join("") || `<p class="muted">Aucune fiche pour l'instant.</p>`;
}

function openMob(slug) {
  const mob = (state.project?.mobs || []).find((item) => item.slug === slug);
  if (!mob) return;
  showInspector("Mob", `
    <h3>${esc(mob.name)}</h3>
    <div class="sub">${esc(mob.slug)} · parent ${esc(mob.parent)}</div>
    <dl class="kv">
      <dt>Modèle source</dt><dd>${esc(mob.model_source || "—")}</dd>
      <dt>Textures</dt><dd>${mob.texture_sources.map(esc).join(", ") || "—"}</dd>
      <dt>Fichiers</dt><dd>${mob.files.map((file) => esc(file.to)).join(", ") || "—"}</dd>
    </dl>
    <strong>Reste à faire côté jeu</strong>
    <ul class="todo">${mob.todo.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`);
  ensureTab({ id: `mob:${slug}`, kind: "mob", label: mob.name, doc: { type: "mob", slug } });
  state.tab = `mob:${slug}`;
  renderTabs();
}

function openDoc(doc) {
  if (doc.type === "model") return openModel(doc.rel);
  if (doc.type === "texture") return openTexture(doc.rel);
  if (doc.type === "mob") return openMob(doc.slug);
  if (doc.type === "blend") return selectBlend(doc.ref, { tab: true });
}

/* ------------------------------------------------------------------ blender */
async function renderBlends() {
  fillMobSelects();
  try {
    const payload = await api("/api/blend/list");
    state.blends = payload.blends || [];
  } catch (error) {
    toast(error.message, "error");
    state.blends = [];
  }
  const list = $("#blend-list");
  if (list) {
    list.innerHTML = state.blends.length
      ? state.blends.map((entry) => `
          <option value="${esc(entry.ref)}" ${entry.ref === state.blend.ref ? "selected" : ""}>
            ${esc(entry.name)} — ${humanSize(entry.size)} (${esc(entry.origin)})
          </option>`).join("")
      : `<option value="">(aucun .blend : dépose un fichier)</option>`;
  }
  if (!state.blend.ref && state.blends.length) {
    selectBlend(state.blends[0].ref);
  }
}

async function selectBlend(ref, options = {}) {
  state.blend.ref = ref;
  const list = $("#blend-list");
  if (list) list.value = ref;
  if (options.tab && ref) {
    const entry = state.blends.find((item) => item.ref === ref);
    ensureTab({ id: `blend:${ref}`, kind: "blend", label: entry?.name || ref,
                doc: { type: "blend", ref } });
    state.tab = `blend:${ref}`;
    renderTabs();
  }
  await inspectBlend();
}

function blendOptions() {
  return {
    axis: $("#blend-axis")?.value || "y_up",
    scale: Number($("#blend-scale")?.value || 1),
    only: $("#blend-only")?.value?.trim() || null,
  };
}

async function inspectBlend() {
  const ref = state.blend.ref || $("#blend-list")?.value;
  if (!ref) return;
  state.blend.ref = ref;
  const info = $("#blend-info");
  if (info) info.innerHTML = `<dt class="muted">analyse…</dt><dd></dd>`;
  try {
    const payload = await api("/api/blend/inspect", {
      method: "POST",
      body: JSON.stringify({ ref, ...blendOptions() }),
    });
    state.blend.objects = payload.objects || [];
    const objects = payload.objects || [];
    if (info) {
      info.innerHTML = `
        <dt>Fichier</dt><dd>${esc(payload.file)}</dd>
        <dt>Version Blender</dt><dd>${esc(payload.version)}</dd>
        <dt>Objets</dt><dd>${objects.length}</dd>
        <dt>Maillages</dt><dd>${payload.meshes}</dd>
        <dt>Faces</dt><dd>${payload.faces.toLocaleString("fr-FR")}</dd>
        <dt>Sommets</dt><dd>${payload.vertices.toLocaleString("fr-FR")}</dd>
        <dt>Matériaux</dt><dd>${payload.materials.length ? payload.materials.map(esc).join(", ") : "—"}</dd>`;
    }
    const report = $("#blend-report");
    if (report) {
      const rows = [];
      objects.forEach((object) => {
        rows.push(`<li><span class="badge">${object.faces} f.</span>
          <span><b>${esc(object.name)}</b>
          ${object.materials.length ? `<small class="muted"> · ${object.materials.map(esc).join(", ")}</small>` : ""}
          ${object.reason ? `<small class="muted"> — ${esc(object.reason)}</small>` : ""}</span></li>`);
      });
      (payload.warnings || []).forEach((warning) => {
        rows.push(`<li><span class="badge warn">note</span><span>${esc(warning)}</span></li>`);
      });
      report.innerHTML = rows.join("") || `<li class="muted">Aucune remarque.</li>`;
    }
    log("ok", `${payload.file} : ${payload.faces} face(s) lues`);
  } catch (error) {
    if (info) info.innerHTML = `<dt class="muted">erreur</dt><dd>${esc(error.message)}</dd>`;
    toast(error.message, "error");
  }
}

function previewBlend() {
  const ref = state.blend.ref || $("#blend-list")?.value;
  if (!ref) { toast("Choisis d'abord un fichier .blend", "error"); return; }
  const { axis, scale, only } = blendOptions();
  const url = `/api/blend/preview?ref=${encodeURIComponent(ref)}&w=560&h=420&axis=${axis}`
    + `&scale=${encodeURIComponent(scale)}` + (only ? `&only=${encodeURIComponent(only)}` : "");
  const image = $("#blend-preview");
  if (image) image.src = `${url}&t=${Date.now()}`;
}

async function uploadBlend(file) {
  if (!file) return;
  if (!/\.blend$/i.test(file.name)) { toast("Il faut un fichier .blend", "error"); return; }
  try {
    const response = await fetch("/api/blend/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Hooky-Filename": file.name },
      body: file,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    toast(`${payload.name} envoyé (${humanSize(payload.size)})`);
    await renderBlends();
    await selectBlend(payload.ref, { tab: true });
    previewBlend();
  } catch (error) { toast(error.message, "error"); }
}

async function translateBlend() {
  const ref = state.blend.ref || $("#blend-list")?.value;
  const target = $("#blend-target")?.value;
  if (!ref) { toast("Choisis un fichier .blend", "error"); return; }
  if (!target) { toast("Choisis le modèle du jeu à modifier", "error"); return; }
  const { axis, scale, only } = blendOptions();
  try {
    const result = await api("/api/blend/translate", {
      method: "POST",
      body: JSON.stringify({
        ref, target, axis, scale, only,
        fit: $("#blend-fit")?.checked !== false,
      }),
    });
    const shot = $("#blend-result-shot");
    if (shot && result.preview) shot.src = result.preview;
    const report = $("#blend-report");
    if (report) {
      report.innerHTML = [
        `<li><span class="badge">écrit</span><span><code>${esc(result.wrote)}</code></span></li>`,
        `<li><span class="badge">${result.faces} f.</span><span>${result.vertices} sommet(s)
          sur ${result.capacity_vertices} disponibles</span></li>`,
        ...result.report.map((line) => `<li><span class="badge">détail</span><span>${esc(line)}</span></li>`),
        ...(result.notes || []).map((note) => `<li><span class="badge warn">note</span><span>${esc(note)}</span></li>`),
      ].join("");
    }
    toast(`${target} modifié : ${result.wrote}`);
    await refresh();
  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ réglages */
function settingsOrDefault() {
  return state.settings || {
    vram_gb: 6, vram_max_gb: 25, vram_min_gb: 1, supersample: 2,
    cull: true, frustum: true, adaptive: true, max_faces: null, lod: 0,
    accent: "menthe", density: "confort", export_textures: true, thumb_faces: null,
    accents: ["menthe", "azur", "ambre", "framboise"], densities: ["confort", "compact"],
  };
}

function applyUiSettings(settings = settingsOrDefault()) {
  document.documentElement.dataset.accent = settings.accent || "menthe";
  document.documentElement.dataset.density = settings.density || "confort";
}

function renderSettings() {
  const settings = settingsOrDefault();
  state.settings = settings;
  applyUiSettings(settings);
  const vram = $("#set-vram");
  if (!vram) return;
  vram.min = settings.vram_min_gb ?? 1;
  vram.max = settings.vram_max_gb ?? 25;
  vram.value = settings.vram_gb;
  $("#set-vram-out").textContent = `${settings.vram_gb} Go`;
  $("#set-vram-fill").style.width =
    `${((settings.vram_gb - (settings.vram_min_gb ?? 1)) / ((settings.vram_max_gb ?? 25) - (settings.vram_min_gb ?? 1))) * 100}%`;
  $("#set-ss").value = settings.supersample;
  $("#set-ss-out").textContent = `×${settings.supersample}`;
  $("#set-cull").checked = settings.cull;
  $("#set-frustum").checked = settings.frustum;
  $("#set-adaptive").checked = settings.adaptive;
  $("#set-faces").value = settings.max_faces || 0;
  $("#set-faces-out").textContent = settings.max_faces
    ? `${settings.max_faces.toLocaleString("fr-FR")} faces` : "auto";

  const accents = $("#set-accent");
  accents.innerHTML = (settings.accents || ["menthe"]).map((accent) =>
    `<option value="${accent}" ${accent === settings.accent ? "selected" : ""}>${accent}</option>`).join("");
  const densities = $("#set-density");
  densities.innerHTML = (settings.densities || ["confort"]).map((density) =>
    `<option value="${density}" ${density === settings.density ? "selected" : ""}>${density}</option>`).join("");
  $("#set-thumb-faces").value = String(settings.thumb_faces || 0);
  $("#set-preview-faces").value = String(settings.preview_faces ?? 60000);
  const cacheSlider = $("#set-cache");
  if (cacheSlider) {
    cacheSlider.max = settings.cache_max_mb || 1024;
    cacheSlider.value = settings.cache_mb ?? 256;
    $("#set-cache-out").textContent = `${settings.cache_mb ?? 256} Mo`;
  }
  $("#set-export-textures").checked = settings.export_textures !== false;
  $("#set-textured").checked = settings.textured_previews !== false;
  renderBlenderState();

  $("#settings-summary").innerHTML = [
    `<li><span class="k">Budget</span>${humanBytes(settings.budget_bytes
      || settings.vram_gb * 1024 ** 3)} (${settings.vram_gb} Go)</li>`,
    `<li><span class="k">Anticrénelage</span>×${settings.supersample}</li>`,
    `<li><span class="k">Culling</span>${[
      settings.cull ? "faces arrière" : null,
      settings.frustum ? "hors écran" : null,
    ].filter(Boolean).join(" + ") || "désactivé"}</li>`,
    `<li><span class="k">Adaptation</span>${settings.adaptive
      ? "active" : "désactivée (refus net)"}</li>`,
    `<li><span class="k">Faces</span>${settings.max_faces
      ? `${settings.max_faces.toLocaleString("fr-FR")} maximum` : "automatique"}</li>`,
    `<li><span class="k">Vignettes</span>${settings.thumb_faces
      ? `${settings.thumb_faces.toLocaleString("fr-FR")} faces` : "selon le budget"}</li>`,
    `<li><span class="k">Export Blender</span>${settings.export_textures !== false
      ? "avec les PNG" : "géométrie seule"}</li>`,
  ].join("");
}

let settingsTimer = null;
async function saveSettings(patch, options = {}) {
  state.settings = { ...settingsOrDefault(), ...patch };
  applyUiSettings(state.settings);
  renderSettings();
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(async () => {
    try {
      const payload = await api("/api/settings", {
        method: "POST",
        body: JSON.stringify(patch),
      });
      state.settings = payload.settings;
      renderSettings();
      renderStatus();
      if (state.view === "models") await renderModels();
      if (!options.quiet) toast("Réglages enregistrés");
    } catch (error) { toast(error.message, "error"); }
  }, 180);
}

function wireSettings() {
  const on = (selector, event, handler) => {
    const node = $(selector);
    if (node) node[event] = handler;
  };
  on("#set-vram", "oninput", (event) => saveSettings({ vram_gb: Number(event.target.value) }));
  on("#set-ss", "oninput", (event) => saveSettings({ supersample: Number(event.target.value) }));
  on("#set-faces", "oninput", (event) => {
    const value = Number(event.target.value);
    saveSettings({ max_faces: value > 0 ? value : null });
  });
  on("#set-cull", "onchange", (event) => saveSettings({ cull: event.target.checked }));
  on("#set-frustum", "onchange", (event) => saveSettings({ frustum: event.target.checked }));
  on("#set-adaptive", "onchange", (event) => saveSettings({ adaptive: event.target.checked }));
  on("#set-accent", "onchange", (event) => saveSettings({ accent: event.target.value }));
  on("#set-density", "onchange", (event) => saveSettings({ density: event.target.value }));
  on("#set-textured", "onchange", (event) => {
    saveSettings({ textured_previews: event.target.checked });
  });
  on("#set-blender-path", "change", async (event) => {
    await saveSettings({ blender_path: event.target.value.trim() });
    await refreshBlender();
  });
  on("#set-preview-faces", "onchange", (event) => {
    saveSettings({ preview_faces: Number(event.target.value) });
  });
  on("#set-thumb-faces", "onchange", (event) => {
    const value = Number(event.target.value);
    saveSettings({ thumb_faces: value > 0 ? value : null });
  });
  on("#set-export-textures", "onchange", (event) =>
    saveSettings({ export_textures: event.target.checked }));
  on("#set-cache", "oninput", (event) => {
    const value = Number(event.target.value);
    $("#set-cache-out").textContent = `${value} Mo`;
    saveSettings({ cache_mb: value });
  });
  on("#cache-clear", "click", clearCache);
  on("#scan-now", "click", rescanProject);
  on("#set-reset", "onclick", () => saveSettings({
    vram_gb: 6, supersample: 2, cull: true, frustum: true, adaptive: true,
    max_faces: null, lod: 0, accent: "menthe", density: "confort",
    export_textures: true, thumb_faces: null, cache_mb: 256,
  }, { quiet: false }));
}

/* ------------------------------------------------------------------ sélection */
function selectionKeys() {
  const view = state.view;
  if (view === "models") return (state.models || []).map((asset) => `model:${asset.rel}`);
  if (view === "textures") return (state.textures || []).map((asset) => `texture:${asset.rel}`);
  return [];
}

function toggleSelection(key, force) {
  const on = force === undefined ? !state.selection.has(key) : force;
  if (on) state.selection.add(key);
  else state.selection.delete(key);
  refreshTileState();
  updateSelectionBar();
}

function refreshTileState() {
  $$(".tile[data-key]").forEach((node) => {
    const chosen = state.selection.has(node.dataset.key);
    node.classList.toggle("selected", chosen);
  });
}

function updateSelectionBar() {
  const bar = $("#selection-bar");
  if (!bar) return;
  const total = state.selection.size;
  bar.hidden = total === 0;
  const label = $("#selection-count");
  if (label) label.textContent = `${total} sélectionné${total > 1 ? "s" : ""}`;
}

function selectRange(key) {
  const keys = selectionKeys();
  const target = keys.indexOf(key);
  const anchor = keys.indexOf(state.anchor);
  if (target < 0) return;
  if (anchor < 0) { toggleSelection(key, true); return; }
  const [from, to] = anchor <= target ? [anchor, target] : [target, anchor];
  for (let index = from; index <= to; index += 1) state.selection.add(keys[index]);
  refreshTileState();
  updateSelectionBar();
}

function clearSelection() {
  state.selection.clear();
  state.anchor = null;
  refreshTileState();
  updateSelectionBar();
}

async function exportSelection(blend = false) {
  const items = [...state.selection].map((key) => {
    const cut = key.indexOf(":");
    return { kind: key.slice(0, cut), rel: key.slice(cut + 1) };
  });
  if (!items.length) { toast("Rien de sélectionné", "error"); return; }
  if (blend && !items.some((item) => item.kind === "model")) {
    toast("Sélectionne au moins un modèle pour un export .blend", "error");
    return;
  }
  status(`export de ${items.length} élément(s)…`, "busy");
  try {
    const response = await fetch("/api/export/selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, blend }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = blend ? "hookycreator-blender.zip" : "hookycreator-selection.zip";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast(`${items.length} élément(s) exportés (${humanSize(blob.size)})`);
    if (blend) log("info", "ZIP Blender : un dossier par modèle, .blend si Blender "
      + "est installé, sinon creer_blend.py + la commande dans LIRE-MOI.txt");
  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ inspecteur */
function showInspector(title, html) {
  $("#inspector-title").textContent = title;
  $("#inspector-body").innerHTML = html;
  $("#inspector").hidden = false;
  $(".workspace").classList.add("with-inspector");
}

function hideInspector() {
  $("#inspector").hidden = true;
  $(".workspace").classList.remove("with-inspector");
}

function markTile(kind, rel) {
  $$(`.tile[data-kind="${kind}"]`).forEach((node) => {
    node.classList.toggle("selected", node.dataset.rel === rel);
  });
}

/* ------------------------------------------------------------------ actions */
async function exportModel(rel) {
  const button = $("#btn-blender");
  if (button) { button.disabled = true; button.textContent = "Export en cours…"; }
  try {
    const result = await api("/api/model/export", {
      method: "POST",
      body: JSON.stringify({ path: rel, textures: state.settings?.export_textures !== false }),
    });
    toast(`Export Blender : ${result.files.length} fichier(s), ${result.faces.toLocaleString("fr-FR")} faces`);
    log("ok", `export → ${result.folder}`);
    log("info", `fichiers : ${result.files.join(", ")}`);
    if (!result.textures) {
      log("warn", "aucune texture trouvée pour ce modèle dans le dossier scanné : "
        + "le glTF sort sans image (les PNG sont à côté du BFRES dans le dump)");
      toast("Export fait, mais aucune texture n'a été trouvée", "error");
    } else {
      // le rôle de chaque carte dit sur quelle entrée de shader la mettre
      const byRole = (result.maps || [])
        .map((map) => `${map.label} → ${map.file}`).join(" · ");
      log("ok", `${result.textures} carte(s) : ${byRole}`);
      log("info", "TEXTURES.md explique où va chaque image ; assembler_shaders.py "
        + "les branche dans Blender");
      toast(`${result.textures} carte(s) de texture exportée(s)`);
    }
    (result.warnings || []).forEach((warning) => log("warn", warning));
  } catch (error) {
    toast(error.message, "error");
    log("error", error.message);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Exporter pour Blender"; }
  }
}

/* ------------------------------------------------------------------ vérificateur d'UV */
function uvShot(rel, flip, size = 512) {
  return `/api/model/uv?path=${encodeURIComponent(rel)}&size=${size}`
    + `&flip=${flip ? 1 : 0}`;
}

async function showUv(rel) {
  let report;
  try {
    report = await api(`/api/model/uv/info?path=${encodeURIComponent(rel)}`);
  } catch (error) { toast(error.message, "error"); return; }
  const name = rel.split("/").pop();
  const range = (pair) => `${pair[0].toFixed(2)} → ${pair[1].toFixed(2)}`;
  const facts = report.has_uvs
    ? `<li><span class="k">UV décodées</span>${report.mapped_meshes}/${report.meshes} maillage(s)</li>
       <li><span class="k">Bornes U</span>${range(report.u)}</li>
       <li><span class="k">Bornes V</span>${range(report.v)}</li>
       <li><span class="k">Hors [0, 1]</span>${report.outside}</li>
       <li><span class="k">Triangles plats en UV</span>${report.degenerate}</li>
       <li><span class="k">Étirement max</span>×${report.stretch.toFixed(1)}</li>`
    : `<li><span class="k">UV</span>aucune coordonnée de texture dans ce modèle</li>`;
  showInspector("UV", `
    <h3>${esc(name)}</h3>
    <div class="sub">Contours des triangles UV dessinés sur l'atlas :
      si le maillage épouse les zones utiles de la texture, elle tombe juste.</div>
    <img class="uv-shot" id="uv-shot" alt="atlas UV" src="${uvShot(rel, false)}">
    <div class="toolbar">
      <button class="btn tiny" id="uv-normal" aria-pressed="true">V du jeu (0 en haut)</button>
      <button class="btn tiny" id="uv-flip" aria-pressed="false">V retourné</button>
    </div>
    <ul class="facts">${facts}</ul>
    ${report.warnings.length ? `<ul class="todo">${report.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
      : (report.has_uvs ? `<p class="hint">Aucun défaut mesurable : coordonnées dans [0, 1],
         pas de triangle plat, densité régulière.</p>` : "")}
    <p class="hint">Texture comparée : ${report.texture ? esc(report.texture) : "aucune trouvée"}.
      L'export suit chaque spécification — glTF : ${esc(report.conventions.gltf)} ;
      OBJ : ${esc(report.conventions.obj)}. Une texture à l'envers dans Blender venait
      d'un V retourné deux fois ; ce n'est plus le cas.</p>
    <div class="actions">
      <button class="btn ghost tiny" id="uv-back">← Retour à la fiche</button>
    </div>`);

  const shot = $("#uv-shot");
  const setFlip = (flip) => {
    shot.src = uvShot(rel, flip);
    $("#uv-normal").setAttribute("aria-pressed", String(!flip));
    $("#uv-flip").setAttribute("aria-pressed", String(flip));
  };
  $("#uv-normal").onclick = () => setFlip(false);
  $("#uv-flip").onclick = () => setFlip(true);
  $("#uv-back").onclick = () => openModel(rel);
}

async function exportBlend(rel) {
  const button = $("#btn-blend");
  const label = button ? button.textContent : "";
  if (button) { button.disabled = true; button.textContent = "Blender travaille…"; }
  try {
    const result = await api("/api/model/blend", {
      method: "POST",
      body: JSON.stringify({ path: rel, textures: state.settings?.export_textures !== false }),
    });
    if (result.method === "blender") {
      toast(`.blend écrit : ${result.blend}`);
      log("ok", `${result.message}`);
    } else {
      // le .blend n'a pas pu être produit : on dit exactement quoi faire
      toast("Blender introuvable : le script est prêt", "error");
      log("warn", result.message);
      if (result.command) log("info", `à lancer : ${result.command}`);
      if (result.platform_note) log("info", result.platform_note);
      showInspector("Blender introuvable", `
        <h3>Le .blend n'a pas été écrit</h3>
        <p class="muted">${esc(result.message)}</p>
        <p>Deux solutions :</p>
        <ol class="todo">
          <li>renseigne le chemin de <code>blender.exe</code> dans <b>Réglages</b>, puis relance ;</li>
          <li>ou lance la commande ci-dessous dans le dossier de l'export.</li>
        </ol>
        <pre class="command" id="blend-command">${esc(result.command || "")}</pre>
        <button class="btn ghost tiny" id="copy-command">Copier la commande</button>
        <p class="hint">${esc(result.platform_note || "")}</p>
        <p class="hint">Le dossier contient déjà ${result.files.length} fichier(s) :
          ${esc(result.files.join(", "))}</p>`);
      const copy = $("#copy-command");
      if (copy) copy.onclick = async () => {
        try { await navigator.clipboard.writeText(result.command || ""); toast("Commande copiée"); }
        catch (error) { toast("Copie impossible : sélectionne le texte", "error"); }
      };
    }
    if (result.output) log("info", result.output.split("\n").slice(-6).join(" · "));
  } catch (error) {
    toast(error.message, "error");
    log("error", error.message);
  } finally {
    if (button) { button.disabled = false; button.textContent = label; }
  }
}

function renderBlenderState() {
  const info = state.blender;
  const nodes = $$("[data-blender-state]");
  if (!nodes.length) return;
  const text = !info ? "recherche…"
    : info.found ? `Blender détecté : ${info.path}`
    : "Blender introuvable — l'export glTF reste disponible et le script .blend est fourni";
  nodes.forEach((node) => {
    node.textContent = text;
    node.classList.toggle("warn", Boolean(info && !info.found));
  });
  const input = $("#set-blender-path");
  // le chemin saisi reste affiché ; à défaut, celui que la détection a trouvé
  if (input && info && !input.value) input.value = info.configured || info.path || "";
}

async function refreshBlender() {
  try {
    state.blender = await api("/api/blender");
    renderBlenderState();
  } catch (error) { /* l'atelier reste utilisable sans Blender */ }
}

async function startBulk() {
  const kinds = ["texture", "model"];
  if ($("#bulk-anim")?.checked) kinds.push("animation");
  try {
    await api("/api/bulk/start", { method: "POST", body: JSON.stringify({ kinds }) });
  } catch (error) { toast(error.message, "error"); return; }
  $("#bulk-bar").hidden = false;
  $("#bulk-report").innerHTML = "";
  $("#bulk-start").disabled = true;
  pollBulk();
}

async function pollBulk() {
  let job;
  try {
    job = await api("/api/bulk/status");
  } catch (error) {
    toast(error.message, "error");
    $("#bulk-start").disabled = false;
    return;
  }
  const total = job.total || 0;
  const done = job.done || 0;
  $("#bulk-fill").style.width = total ? `${Math.min(100, (done / total) * 100)}%` : "6%";
  $("#bulk-label").textContent = job.running
    ? `${done}/${total || "?"} — ${job.label || "en cours…"}`
    : (job.report ? "terminé" : "");

  if (job.running) { setTimeout(pollBulk, 400); return; }

  $("#bulk-start").disabled = false;
  if (job.error) { $("#bulk-label").textContent = `échec : ${job.error}`; return; }
  const report = job.report;
  if (!report) return;
  $("#bulk-fill").style.width = "100%";
  $("#bulk-label").textContent =
    `${report.ok} réussi(s) · ${report.skip} ignoré(s) · ${report.error} erreur(s) — `
    + `${report.textures} PNG, ${report.models} modèle(s)`;
  $("#bulk-report").innerHTML = report.items.slice(0, 60).map((item) => {
    const flavor = item.status === "ok"
      ? `<span class="badge">ok</span>`
      : `<span class="badge warn">${item.status === "skip" ? "ignoré" : "erreur"}</span>`;
    const detail = item.status === "ok"
      ? (item.kind === "model" ? `${item.faces} faces · v${item.version}` : item.format)
      : (item.reason || "");
    return `<li>${flavor}<span>${esc(item.label)}<br><small class="muted">${esc(detail)}</small></span></li>`;
  }).join("");
  toast(`Export complet : ${report.ok} élément(s)`);
  await refresh();
}

async function buildMod(event) {
  event.preventDefault();
  const name = new FormData(event.target).get("name");
  try {
    const result = await api("/api/mod/build", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    $("#mod-log").innerHTML = `
      <li><span class="k">Mod</span><span>${esc(result.mod)}</span></li>
      <li><span class="k">Dossier</span><code>${esc(result.path)}</code></li>
      <li><span class="k">Fichiers</span><span>${result.files}</span></li>
      <li><span class="k">Mobs</span><span>${result.mobs}</span></li>`;
    toast(`Mod assemblé : ${result.path} (${result.files} fichier(s))`);
  } catch (error) { toast(error.message, "error"); }
}

async function createMob(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const textures = [...($("#mob-textures")?.selectedOptions || [])].map((option) => option.value);
  try {
    const mob = await api("/api/mobs", {
      method: "POST",
      body: JSON.stringify({
        name: form.get("name"),
        parent: form.get("parent") || "",
        model: form.get("model"),
        textures,
      }),
    });
    toast(`Fiche créée : ${mob.name}`);
    await refresh();
    renderMobs();
  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ menus */
const MENUS = {
  fichier: [
    { label: "Rafraîchir le projet", hint: "F5", run: () => refresh({ quiet: false }) },
    { label: "Rescanner les fichiers", run: () => rescanProject() },
    { label: "Exporter la sélection (ZIP)", run: () => exportSelection() },
    { sep: true },
    { label: "Exporter tout le projet", run: () => { showView("home"); startBulk(); } },
    { label: "Assembler le mod", run: () => { showView("home"); $("#mod-form")?.requestSubmit(); } },
    { sep: true },
    { label: "Ouvrir le dossier de travail", run: () => toast(`Dossier : ${state.project?.work || "—"}`) },
    { sep: true },
    { label: "Quitter l'atelier", danger: true, run: quitApp },
  ],
  edition: [
    { label: "Palette de commandes", hint: "Ctrl+K", run: openPalette },
    { label: "Chercher dans la vue", hint: "Ctrl+F", run: () => $("#search")?.focus() },
    { sep: true },
    { label: "Vider le journal", run: () => { state.logs = []; renderLogs(); } },
  ],
  modele: [
    { label: "Vue des modèles", hint: "Ctrl+2", run: () => showView("models") },
    { label: "Vue des ensembles (modèle + textures + animations)", run: () => showView("ensembles") },
    { label: "Exporter en .blend", run: () => {
      const rel = state.tabs.find((tab) => tab.doc?.type === "model")?.doc?.rel;
      if (rel) exportBlend(rel); else toast("Ouvre d'abord un modèle", "error");
    } },
    { label: "Exporter le modèle sélectionné pour Blender", run: () => {
      const rel = state.tabs.find((tab) => tab.doc?.type === "model")?.doc?.rel;
      if (rel) exportModel(rel); else toast("Ouvre d'abord un modèle", "error");
    } },
    { label: "Injecter un .blend…", hint: "Ctrl+5", run: () => showView("blender") },
  ],
  texture: [
    { label: "Vue des textures", hint: "Ctrl+3", run: () => showView("textures") },
    { label: "Exporter la texture sélectionnée en PNG", run: () => {
      const rel = state.tabs.find((tab) => tab.doc?.type === "texture")?.doc?.rel;
      if (!rel) { toast("Ouvre d'abord une texture", "error"); return; }
      api(`/api/texture/export?path=${encodeURIComponent(rel)}&index=0`)
        .then((result) => toast(`PNG écrit : ${result.file}`))
        .catch((error) => toast(error.message, "error"));
    } },
  ],
  blender: [
    { label: "Vue Blender", hint: "Ctrl+5", run: () => showView("blender") },
    { label: "Analyser le .blend sélectionné", run: () => inspectBlend() },
    { label: "Aperçu du .blend", run: () => previewBlend() },
    { sep: true },
    { label: "Injecter dans le modèle cible", run: () => translateBlend() },
  ],
  affichage: [
    { label: "Accueil", hint: "Ctrl+1", run: () => showView("home") },
    { label: "Réglages", hint: "Ctrl+6", run: () => showView("settings") },
    { sep: true },
    { label: "Journal", hint: "Ctrl+J", run: toggleConsole },
    { label: "Masquer l'inspecteur", hint: "Échap", run: hideInspector },
    { sep: true },
    { label: "Densité compacte", run: () => saveSettings({
      density: state.settings?.density === "compact" ? "confort" : "compact" }) },
    { label: "Réduire l'interface", run: toggleReduced },
    { label: "Plein écran", run: toggleFullscreen },
    { sep: true },
    { label: "Vider le cache des vignettes", run: clearCache },
  ],
  aide: [
    { label: "Raccourcis clavier", run: () => toast(
      "Ctrl+K palette · Ctrl+J journal · Ctrl+1…6 vues · Ctrl+F filtrer · F5 rafraîchir · Échap fermer") },
    { label: "À propos", run: () => toast(
      "HookyCreator 1.1 — traduction BFRES/BNTX et .blend faite maison, sans bibliothèque tierce.") },
  ],
};

function buildMenu(menu) {
  const node = $("#menus");
  if (!node) return;
  const anchor = $(`.menu-btn[data-menu="${menu}"]`).getBoundingClientRect();
  node.innerHTML = `<div class="menu-panel" style="left:${anchor.left}px;top:${anchor.bottom + 6}px">
    ${MENUS[menu].map((item) => item.sep
      ? `<div class="menu-sep"></div>`
      : `<button class="menu-item ${item.danger ? "danger" : ""}" data-cmd="${esc(item.label)}">
           <span>${esc(item.label)}</span>${item.hint ? `<kbd>${esc(item.hint)}</kbd>` : ""}
         </button>`).join("")}
  </div>`;
  node.hidden = false;
  $$(".menu-btn").forEach((btn) => btn.setAttribute("aria-expanded", String(btn.dataset.menu === menu)));
  $$(".menu-item", node).forEach((button) => {
    button.onclick = () => {
      const item = MENUS[menu].find((entry) => entry.label === button.dataset.cmd);
      closeMenus();
      item?.run();
    };
  });
}

function closeMenus() {
  const node = $("#menus");
  if (node) { node.hidden = true; node.innerHTML = ""; }
  $$(".menu-btn").forEach((btn) => btn.removeAttribute("aria-expanded"));
}

/* ------------------------------------------------------------------ palette */
function paletteCommands() {
  const commands = [
    { label: "Aller à l'accueil", group: "vue", run: () => showView("home") },
    { label: "Aller aux modèles", group: "vue", run: () => showView("models") },
    { label: "Aller aux textures", group: "vue", run: () => showView("textures") },
    { label: "Aller aux mobs", group: "vue", run: () => showView("mobs") },
    { label: "Aller à Blender → jeu", group: "vue", run: () => showView("blender") },
    { label: "Aller aux réglages", group: "vue", run: () => showView("settings") },
    { label: "Rafraîchir le projet", group: "action", run: () => refresh({ quiet: false }) },
    { label: "Exporter tout le projet", group: "action", run: () => { showView("home"); startBulk(); } },
    { label: "Assembler le mod", group: "action", run: () => { showView("home"); $("#mod-form")?.requestSubmit(); } },
    { label: "Analyser le .blend sélectionné", group: "blender", run: () => inspectBlend() },
    { label: "Injecter le .blend dans le modèle cible", group: "blender", run: () => translateBlend() },
    { label: "Basculer le journal", group: "affichage", run: toggleConsole },
    { label: "Densité compacte", group: "affichage", run: () => saveSettings({
      density: state.settings?.density === "compact" ? "confort" : "compact" }) },
    { label: "Réinitialiser les réglages", group: "affichage", run: () => $("#set-reset")?.click() },
    { label: "Rescanner les fichiers du projet", group: "action", run: () => rescanProject() },
    { label: "Exporter la sélection (ZIP)", group: "action", run: () => exportSelection() },
    { label: "Tout sélectionner", group: "sélection", run: () => $("#selection-all")?.click() },
    { label: "Vider la sélection", group: "sélection", run: () => clearSelection() },
    { label: "Vider le cache des vignettes", group: "mémoire", run: () => clearCache() },
    { label: "Plein écran", group: "fenêtre", run: () => toggleFullscreen() },
    { label: "Réduire l'interface", group: "fenêtre", run: () => toggleReduced() },
    { label: "Quitter l'atelier", group: "fenêtre", run: () => quitApp() },
  ];
  (state.project?.assets || []).filter((asset) => asset.kind === "model").slice(0, 400)
    .forEach((asset) => commands.push({
      label: `Ouvrir le modèle ${asset.name}`, group: "modèle", hint: asset.rel,
      run: () => { showView("models"); openModel(asset.rel); },
    }));
  (state.project?.assets || []).filter((asset) => asset.kind === "texture").slice(0, 400)
    .forEach((asset) => commands.push({
      label: `Ouvrir la texture ${asset.name}`, group: "texture", hint: asset.rel,
      run: () => { showView("textures"); openTexture(asset.rel); },
    }));
  (state.blends || []).forEach((entry) => commands.push({
    label: `Charger le .blend ${entry.name}`, group: "blender", hint: entry.ref,
    run: () => { showView("blender"); selectBlend(entry.ref, { tab: true }); },
  }));
  return commands;
}

let paletteItems = [];
let paletteIndex = 0;

function openPalette() {
  const node = $("#palette");
  if (!node) return;
  node.hidden = false;
  const input = $("#palette-input");
  input.value = "";
  paletteIndex = 0;
  filterPalette("");
  input.focus();
}

function closePalette() {
  const node = $("#palette");
  if (node) node.hidden = true;
}

function filterPalette(query) {
  const text = String(query || "").toLowerCase().trim();
  const all = paletteCommands();
  paletteItems = (text
    ? all.filter((item) => `${item.label} ${item.hint || ""}`.toLowerCase().includes(text))
    : all
  ).slice(0, 60);
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
  const list = $("#palette-list");
  list.innerHTML = paletteItems.length
    ? paletteItems.map((item, index) => `
        <li data-index="${index}" aria-selected="${index === paletteIndex}">
          <span>${esc(item.label)}</span>
          ${item.hint ? `<small>${esc(item.hint)}</small>` : ""}
          <span class="group">${esc(item.group || "")}</span>
        </li>`).join("")
    : `<li class="empty">aucun résultat</li>`;
  const active = list.querySelector(`li[data-index="${paletteIndex}"]`);
  if (active) active.scrollIntoView({ block: "nearest" });
}

function runPalette(index) {
  const item = paletteItems[index];
  if (!item) return;
  closePalette();
  item.run();
}

/* ------------------------------------------------------------------ préchauffage */
let warmTimer = null;

function watchWarm(warm) {
  clearTimeout(warmTimer);
  if (!warm || !warm.total) return;
  const label = $("#status-message");
  if (label) label.textContent = `vignettes ${warm.done}/${warm.total}`;
  if (!warm.running) {
    log("ok", `vignettes prêtes : ${warm.done}/${warm.total}`);
    if (state.view === "models") renderModels();
    refreshSystem();
    return;
  }
  warmTimer = setTimeout(async () => {
    try {
      const payload = await api("/api/thumbs/status");
      watchWarm(payload.warm);
    } catch (error) { /* le serveur peut être occupé, on retentera */ }
  }, 1200);
}

async function startWarming() {
  try {
    const payload = await api("/api/thumbs/warm", { method: "POST", body: "{}" });
    if (payload.warm?.running) {
      log("ok", `préparation des vignettes : ${payload.warm.total} modèle(s)`);
      watchWarm(payload.warm);
    }
  } catch (error) { log("error", `préchauffage impossible : ${error.message}`); }
}

/* ------------------------------------------------------------------ mémoire */
async function refreshSystem() {
  try {
    const payload = await api("/api/system");
    state.system = payload.cache;
    renderMemory();
    renderStatus();
  } catch (error) {
    log("error", `mémoire indisponible : ${error.message}`);
  }
}

function renderMemory() {
  const list = $("#memory-list");
  const cache = state.system;
  if (!list || !cache) return;
  const thumbs = cache.thumbs || {};
  const meshes = cache.meshes || {};
  const percent = thumbs.limit_bytes
    ? Math.min(100, (thumbs.bytes / thumbs.limit_bytes) * 100) : 0;
  list.innerHTML = `
    <li><span class="k">Mémoire du processus</span>
      <b>${cache.rss_bytes ? humanBytes(cache.rss_bytes) : "inconnue"}</b></li>
    <li><span class="k">Vignettes sur disque</span>
      <b>${humanBytes(thumbs.bytes || 0)} · ${thumbs.entries || 0} fichier(s)</b></li>
    <li><span class="k">Cache lu / écrit</span>
      <b>${thumbs.hits || 0} / ${thumbs.misses || 0}</b></li>
    <li><span class="k">Maillages en mémoire</span>
      <b>${humanBytes(meshes.bytes || 0)} · ${meshes.entries || 0} modèle(s)</b></li>
    <li><span class="k">Dernier scan</span>
      <b>${cache.scan_seconds != null ? `${cache.scan_seconds} s` : "—"}
        ${cache.scanned_at ? `<small class="muted">(${esc(cache.scanned_at.replace("T", " "))})</small>` : ""}</b></li>
    <li style="display:block">
      <span class="k">Remplissage du cache</span>
      <div class="bar"><i style="width:${percent}%"></i></div>
    </li>`;
}

async function clearCache() {
  try {
    const payload = await api("/api/cache/clear", { method: "POST", body: "{}" });
    state.system = payload.cache;
    renderMemory();
    toast(`Cache vidé : ${payload.cleared.removed_entries} vignette(s), `
      + `${humanBytes(payload.cleared.freed_bytes)} libérés`);
  } catch (error) { toast(error.message, "error"); }
}

async function rescanProject() {
  status("scan du projet…", "busy");
  try {
    const payload = await api("/api/scan", { method: "POST", body: "{}" });
    state.project = payload.project;
    state.settings = payload.project.settings || state.settings;
    state.shown.models = 48;
    state.shown.textures = 48;
    renderStatus();
    if (state.view === "models") await renderModels();
    if (state.view === "textures") await renderTextures();
    if (state.view === "home") renderHome();
    toast(`${payload.scan.count} fichier(s) en ${payload.scan.scan_seconds} s`);
  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ fenêtre */
function sessionOver() {
  const overlay = document.createElement("div");
  overlay.className = "session-over";
  overlay.innerHTML = `
    <div>
      <h2>Session terminée</h2>
      <p>Le serveur de l'atelier est arrêté. Ton travail est enregistré dans le dossier
         de travail ; relance <code>python -m hookycreator</code> pour reprendre.</p>
    </div>`;
  document.body.append(overlay);
  status("session terminée", "error");
}

async function quitApp() {
  if (!window.confirm("Quitter l'atelier ? Le serveur local va s'arrêter.")) return;
  try {
    await api("/api/shutdown", { method: "POST", body: "{}" });
  } catch (error) { /* le serveur peut couper avant la réponse */ }
  sessionOver();
}

function toggleReduced() {
  const root = document.documentElement;
  const reduced = root.dataset.min === "1";
  if (reduced) delete root.dataset.min;
  else root.dataset.min = "1";
  $("#win-min").title = reduced ? "Réduire l'interface (mode compact, sans rail)"
                                : "Rétablir l'interface complète";
  log("ok", reduced ? "interface complète" : "interface réduite (rail masqué)");
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      state.fullscreen = false;
    } else {
      await document.documentElement.requestFullscreen();
      state.fullscreen = true;
    }
  } catch (error) {
    toast("Le plein écran est refusé par le navigateur ici", "error");
  }
  $("#win-max").title = state.fullscreen ? "Quitter le plein écran" : "Plein écran";
}

/* ------------------------------------------------------------------ journal */
function toggleConsole(force) {
  const node = $("#console");
  if (!node) return;
  const show = force === undefined ? node.hidden : Boolean(force);
  node.hidden = !show;
  $(".editor").classList.toggle("console-open", show);
  $("#rail-console")?.setAttribute("aria-current", show ? "page" : "false");
  if (!show) $("#rail-console")?.removeAttribute("aria-current");
}

/* ------------------------------------------------------------------ état */
async function refresh(options = {}) {
  const payload = await api("/api/state");
  state.project = payload.project;
  state.settings = payload.project.settings || state.settings;
  state.ensembles = [];          // la liste dépend des fichiers : on la relit
  applyUiSettings(state.settings);
  renderStatus();
  if (state.view === "home") renderHome();
  if (state.view === "models") await renderModels();
  if (state.view === "ensembles") await renderEnsembles();
  if (state.view === "textures") await renderTextures();
  if (state.view === "mobs") await renderMobs();
  if (state.view === "blender") await renderBlends();
  if (state.view === "settings") { renderSettings(); refreshSystem(); refreshBlender(); }
  if (options.quiet === false) {
    const counts = payload.project.assets.length;
    toast(`Projet rafraîchi : ${counts} fichier(s)`);
  }
}

/* ------------------------------------------------------------------ câblage */
function wire() {
  $$(".rail-btn[data-view]").forEach((node) => {
    node.onclick = () => showView(node.dataset.view);
  });
  $$("[data-goto]").forEach((node) => {
    node.onclick = () => showView(node.dataset.goto);
  });
  $$(".menu-btn").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      const menu = button.dataset.menu;
      const open = button.getAttribute("aria-expanded") === "true";
      closeMenus();
      if (!open) buildMenu(menu);
    };
  });
  $("#menus").onclick = (event) => { if (event.target.id === "menus") closeMenus(); };
  $("#palette-btn").onclick = openPalette;

  $("#search").oninput = (event) => {
    state.filter = event.target.value.trim().toLowerCase();
    state.shown.models = 48;
    renderModels();
  };
  $("#search-tex").oninput = (event) => {
    state.filter = event.target.value.trim().toLowerCase();
    state.shown.textures = 48;
    renderTextures();
  };
  $("#model-shots").onchange = renderModels;
  const ensMore = $("#ensemble-more-btn");
  if (ensMore) ensMore.onclick = () => {
    state.shown.ensembles += 48;
    renderEnsembles();
  };
  const ensTex = $("#ens-textures");
  if (ensTex) ensTex.onchange = () => renderEnsembles();
  const texRole = $("#tex-role");
  if (texRole) texRole.onchange = () => {
    state.shown.textures = 48;
    renderTextures();
  };
  $("#model-more-btn").onclick = () => {
    state.shown.models += 48;
    renderModels();
  };
  $("#texture-more-btn").onclick = () => {
    state.shown.textures += 48;
    renderTextures();
  };
  $("#selection-export").onclick = () => exportSelection(false);
  const selBlend = $("#selection-blend");
  if (selBlend) selBlend.onclick = () => exportSelection(true);
  $("#selection-clear").onclick = clearSelection;
  $("#selection-all").onclick = () => {
    selectionKeys().forEach((key) => state.selection.add(key));
    refreshTileState();
    updateSelectionBar();
  };
  $("#selection-invert").onclick = () => {
    selectionKeys().forEach((key) => {
      if (state.selection.has(key)) state.selection.delete(key);
      else state.selection.add(key);
    });
    refreshTileState();
    updateSelectionBar();
  };
  $("#win-close").onclick = quitApp;
  $("#win-min").onclick = toggleReduced;
  $("#win-max").onclick = toggleFullscreen;
  document.addEventListener("fullscreenchange", () => {
    state.fullscreen = Boolean(document.fullscreenElement);
    $("#win-max").title = state.fullscreen ? "Quitter le plein écran" : "Plein écran";
  });
  $("#inspector-close").onclick = hideInspector;
  $("#rail-console").onclick = () => toggleConsole();
  $("#console-clear").onclick = () => { state.logs = []; renderLogs(); };
  $("#console-close").onclick = () => toggleConsole(false);

  document.body.addEventListener("click", (event) => {
    const close = event.target.closest("[data-close]");
    if (close) { event.stopPropagation(); closeTab(close.dataset.close); return; }
    const tab = event.target.closest(".tab");
    if (tab) { activateTab(tab.dataset.tab); return; }
    const pick = event.target.closest("[data-pick]");
    if (pick) {
      event.stopPropagation();
      toggleSelection(pick.dataset.pick);
      state.anchor = pick.dataset.pick;
      return;
    }
    const tile = event.target.closest(".tile");
    if (tile) {
      const key = tile.dataset.key;
      if (key && (event.ctrlKey || event.metaKey)) {
        toggleSelection(key);
        state.anchor = key;
        return;
      }
      if (key && event.shiftKey) {
        selectRange(key);
        return;
      }
      if (state.selection.size) clearSelection();
      if (tile.dataset.kind === "model") openModel(tile.dataset.rel);
      if (tile.dataset.kind === "texture") openTexture(tile.dataset.rel);
      if (tile.dataset.kind === "mob") openMob(tile.dataset.slug);
    }
  });

  $("#mob-form").onsubmit = createMob;
  $("#mod-form").onsubmit = buildMod;
  $("#bulk-start").onclick = startBulk;

  // blender
  $("#blend-list").onchange = (event) => selectBlend(event.target.value, { tab: true });
  $("#blend-inspect").onclick = inspectBlend;
  $("#blend-preview-btn").onclick = previewBlend;
  $("#blend-translate").onclick = translateBlend;
  $("#blend-file").onchange = (event) => uploadBlend(event.target.files[0]);
  const drop = $("#blend-drop");
  if (drop) {
    drop.ondragover = (event) => { event.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      uploadBlend(event.dataTransfer.files[0]);
    };
  }

  // palette
  const input = $("#palette-input");
  input.oninput = () => filterPalette(input.value);
  input.onkeydown = (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      paletteIndex = Math.min(paletteIndex + 1, paletteItems.length - 1);
      filterPalette(input.value);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      paletteIndex = Math.max(0, paletteIndex - 1);
      filterPalette(input.value);
    } else if (event.key === "Enter") {
      event.preventDefault();
      runPalette(paletteIndex);
    }
  };
  $("#palette-list").onclick = (event) => {
    const item = event.target.closest("li[data-index]");
    if (item) runPalette(Number(item.dataset.index));
  };
  $("#palette").onclick = (event) => { if (event.target.id === "palette") closePalette(); };

  document.addEventListener("keydown", (event) => {
    const meta = event.ctrlKey || event.metaKey;
    if (meta && event.key.toLowerCase() === "k") { event.preventDefault(); openPalette(); return; }
    if (meta && event.key.toLowerCase() === "j") { event.preventDefault(); toggleConsole(); return; }
    if (meta && event.key.toLowerCase() === "f") {
      event.preventDefault();
      (state.view === "textures" ? $("#search-tex") : $("#search"))?.focus();
      return;
    }
    if (meta && "123456".includes(event.key)) {
      event.preventDefault();
      const views = ["home", "models", "textures", "mobs", "blender", "settings"];
      showView(views[Number(event.key) - 1]);
      return;
    }
    if (event.key === "F5") { event.preventDefault(); refresh({ quiet: false }); return; }
    if (meta && event.key.toLowerCase() === "a") {
      if (state.view === "models" || state.view === "textures") {
        event.preventDefault();
        selectionKeys().forEach((key) => state.selection.add(key));
        refreshTileState();
        updateSelectionBar();
      }
      return;
    }
    if (event.key === "Escape") {
      closeMenus();
      closePalette();
      if (state.selection.size) { clearSelection(); return; }
      hideInspector();
    }
  });

  wireSettings();
  setInterval(() => {
    const clock = $("#status-clock");
    if (clock) clock.textContent = now();
  }, 1000);
}

async function boot() {
  applyUiSettings();
  ensureViewTab("home", "Accueil");
  state.tabs[0].closable = false;
  wire();
  renderTabs();
  showView("home");
  try {
    await refresh();
    log("ok", `Projet ouvert : ${state.project.root}`);
    refreshSystem();
    startWarming();
    status("prêt");
  } catch (error) {
    toast(error.message, "error");
  }
}

boot();
