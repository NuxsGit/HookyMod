"use strict";

/* ============================================================================
   HookyCreator — application (pas une page web : une fenêtre d'atelier)
   ========================================================================== */

const state = {
  project: null,
  settings: null,
  view: "home",
  filters: { models: "", textures: "", ensembles: "", sounds: "", animations: "", videos: "" },
  tabs: [],
  tab: "home",
  logs: [],
  models: [],
  ensembles: [],
  ensembleAnimation: "",
  textures: [],
  busy: 0,
  selection: new Set(),
  anchor: null,
  shown: { models: 48, textures: 48, ensembles: 48, sounds: 48, animations: 48, videos: 48, chooser: 48 },
  modelCandidates: [], textureCandidates: [], ensembleCandidates: [], soundCandidates: [],
  chooserModels: [],
  system: null,
  engine: null,
  warm: null,
  fullscreen: false,
};

const thumbnailQueue = new ThumbnailQueue({
  onFailure: (image, reason) => {
    const owner = image.closest("[data-check-kind][data-check-rel]");
    if (owner) assetValidation.markError(owner.dataset.checkKind, owner.dataset.checkRel, reason);
  },
});
const assetValidation = new AssetValidationQueue({ onChange: updateValidatedAssets });
let apiThumbnailHold = null;
let apiValidationHold = null;
let inspectionSequence = 0;
let modelViewport = null;
// Après la planche PNG, conserver uniquement de quoi libérer le contexte à
// la fermeture ; le canvas doit rester réutilisable si l'on revient en 3D.
let modelViewportForCleanup = null;
let ensembleLoad = null;
let ensembleRenderSequence = 0;
let projectSnapshotRevision = null;
let projectRequestSequence = 0;
let appliedProjectRequest = 0;
// Des sélecteurs ne sont remplis qu'après la validation progressive des assets.
let restoredChoices = {};
let restoredModelQuality = "";
let uiSessionWriter = null;
let uiSessionBoot = Promise.resolve();
let sessionLoadFailed = false;
let nativeCheckpointSerial = 0;

function applyViewerSettings() {
  const settings = state.settings || {};
  if (modelViewport) {
    const select = document.querySelector("#model-upscale");
    if (select) select.value = settings.viewer_upscale || "native";
    void modelViewport.configure(settings);
  }
  window.HookyPlayer?.configure(settings);
}

/* ------------------------------------------------------------------ outils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
// Native dialogs are not DOM nodes, so translate them explicitly.
const tr = (text) => window.HookyI18n?.translate(text) ?? text;

const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
));

function humanBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
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
  if (active && !state.busy) {
    apiThumbnailHold = thumbnailQueue.hold();
    apiValidationHold = assetValidation.hold();
  }
  state.busy = Math.max(0, state.busy + (active ? 1 : -1));
  if (!state.busy && apiThumbnailHold) {
    apiThumbnailHold();
    apiThumbnailHold = null;
    apiValidationHold?.();
    apiValidationHold = null;
  }
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
  const { background = false, ...requestOptions } = options;
  if (!background) busy(true);
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...requestOptions,
    });
    const isJson = (response.headers.get("Content-Type") || "").includes("json");
    const payload = isJson ? await response.json() : await response.blob();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  } finally {
    if (!background) busy(false);
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
      <span class="label" ${tab.doc ? "data-no-i18n" : ""}>${esc(tab.label)}</span>
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
  if (state.view !== view) {
    if (state.view === "player") window.HookyPlayer?.hide();
    assetValidation.clearScope("view");
    thumbnailQueue.release($(`#view-${state.view}`));
    const scroll = $("#views");
    if (scroll) scroll.scrollTop = 0;
    // Les espaces d'accueil doivent rester lisibles, surtout sur mobile où
    // l'inspecteur est superposé. L'onglet du document reste disponible.
    if (view === "edit" || view === "player" || globalThis.matchMedia?.("(max-width: 900px)").matches) {
      hideInspector();
    }
  }
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
  if (view === "player" && !options.skipPlayer) window.HookyPlayer?.show();
  if (view === "textures") renderTextures();
  if (view === "animations") renderAnimations();
  if (view === "videos") renderVideos();
  if (view === "sounds") renderSounds();
  if (view === "import") renderModelImport();
  if (view === "settings") { renderSettings(); refreshSystem(); }
}

const VIEW_LABELS = {
  home: "Accueil",
  edit: "Modification",
  models: "Modèles",
  ensembles: "Ensembles",
  player: "Player",
  textures: "Textures",
  animations: "Animations",
  videos: "Vidéos",
  sounds: "Sons",
  import: "Importer un modèle",
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
  const owner = image.closest("[data-check-kind][data-check-rel]");
  if (owner) assetValidation.markError(owner.dataset.checkKind, owner.dataset.checkRel,
                                       "Aperçu impossible à décoder.");
}, true);

/* ------------------------------------------------------------------ barre d'état */
function renderStatus() {
  const assets = state.project?.assets || [];
  const counts = {};
  for (const asset of assets) counts[asset.kind] = (counts[asset.kind] || 0) + 1;
  const count = (kind) => counts[kind] || 0;
  const auxiliary = count("animation") + count("auxiliary");
  const settings = state.settings || {};
  const node = $("#status-counts");
  if (node) {
    node.textContent = `${count("model")} modèles repérés · ${auxiliary} ressources auxiliaires · `
      + `${count("texture")} textures repérées`;
  }
  const scan = state.project || {};
  const incomplete = Boolean(scan.scan_incomplete || scan.scan_truncated || scan.scan_errors);
  const reasons = [];
  if (scan.scan_truncated) {
    reasons.push(`limite de ${scan.scan_limit || "?"} fichiers reconnus atteinte. `
      + "Relance avec --scan-limit 0 pour tous les indexer (plus de mémoire peut être nécessaire)");
  }
  if (scan.scan_errors) {
    reasons.push(`${scan.scan_errors} entrée(s) illisible(s)`
      + ((scan.scan_error_examples || []).length ? ` : ${scan.scan_error_examples.join(" · ")}` : ""));
  }
  const guidance = `Scan incomplet : ${reasons.join(" ; ")}.`;
  const warning = $("#scan-warning");
  if (warning) { warning.hidden = !incomplete; warning.title = incomplete ? guidance : ""; }
  const notice = $("#scan-notice");
  if (notice) { notice.hidden = !incomplete; notice.textContent = incomplete ? guidance : ""; }
  const budget = $("#status-budget");
  if (budget) {
    const cache = state.system?.thumbs || state.project?.cache?.thumbs;
    budget.textContent = `budget ${(settings.vram_gb ?? 6).toFixed(1).replace(".0", "")} GiB`
      + (cache ? ` · cache ${humanBytes(cache.bytes)}` : "");
  }
  const project = $("#status-project");
  if (project) project.textContent = state.project?.root || "—";
  const name = $("#project-name");
  if (name) name.textContent = state.project?.root || "—";
  const stats = $("#stats");
  if (stats) {
    stats.innerHTML = `
      <span class="chip"><b>${count("texture")}</b> textures repérées</span>
      <span class="chip"><b>${count("model")}</b> modèles repérés</span>
      <span class="chip"><b>${count("sound")}</b> BWAV repérés</span>
      <span class="chip"><b>${auxiliary}</b> BFRES auxiliaires</span>
      <span class="chip"><b>${state.project?.edited?.length || 0}</b> fichiers modifiés</span>
      <span class="chip" title="Budget mémoire des aperçus">budget
        <b>${(settings.vram_gb ?? 6).toFixed(1).replace(".0", "")} GiB</b></span>`;
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
      ["Fichiers modifiés", edited.length ? `${edited.length} dans le dossier de travail` : "aucun"],
      ["Réglages", `écrits dans <code>settings.json</code>`],
    ].map(([key, value]) => `<li><span class="k">${key}</span><span>${value}</span></li>`).join("");
  }
  renderLogs();
  renderStatus();
}

/* ------------------------------------------------------------------ modèles */
// L'inventaire est une liste de candidats, pas une preuve de décodage. Les
// tuiles, liens de textures et listes déroulantes restent cachés tant que le
// serveur n'a pas réellement construit le maillage ou décodé la texture.
function updateValidatedAssets() {
  $$("[data-check-kind][data-check-rel]").forEach((node) => {
    const kind = node.dataset.checkKind, rel = node.dataset.checkRel;
    const entry = assetValidation.entry(kind, rel);
    node.hidden = entry?.status !== "valid";
    if (entry?.status === "invalid" || entry?.status === "error") state.selection.delete(`${kind}:${rel}`);
  });
  const summary = (items, kind, label, selector) => {
    const entries = (items || []).map((asset) => assetValidation.entry(kind, asset.rel));
    const valid = entries.filter((entry) => entry?.status === "valid").length;
    const invalid = entries.filter((entry) => entry?.status === "invalid").length;
    const errors = entries.filter((entry) => entry?.status === "error").length;
    const pending = entries.length - valid - invalid - errors;
    const node = $(selector);
    if (node) node.textContent = `${valid} ${label} vérifié(s)`
      + (pending ? ` · ${pending} en vérification` : "")
      + (invalid ? ` · ${invalid} non pris en charge masqué(s)` : "")
      + (errors ? ` · ${errors} vérification(s) à réessayer` : "");
    return { valid, errors };
  };
  state.models = (state.modelCandidates || []).filter((asset) => assetValidation.valid("model", asset.rel));
  state.textures = (state.textureCandidates || []).filter((asset) => assetValidation.valid("texture", asset.rel));
  let errors = 0;
  if (state.view === "models") errors = summary(state.modelCandidates, "model", "modèle(s)", "#model-count").errors;
  if (state.view === "textures") errors = summary(state.textureCandidates, "texture", "texture(s)", "#texture-count").errors;
  if (state.view === "ensembles") errors = summary(state.ensembleCandidates, "model", "ensemble(s)", "#ensemble-count").errors;
  if (state.view === "sounds") errors = summary(state.soundCandidates, "sound", "son(s)", "#sound-count").errors;
  if (state.view === "import") {
    errors = summary(state.chooserModels, "model", "modèle(s)", "#model-import-validation-status").errors;
    fillModelTargets();
  }
  $$("[data-validate-retry]").forEach((button) => { button.hidden = !errors; });
  updateSelectionBar();
}

function thumbFaces() {
  // Les préférences d'anciennes versions pouvaient limiter à 20 000 faces
  // sans que l'utilisateur l'ait demandé. Les vignettes actuelles sont entières.
  return 0;
}

// Certains navigateurs réutilisent l'image décodée en mémoire sans requête,
// même avec un ETag à revalider. Changer l'URL après une édition ou un réglage.
function imageRevisionQuery(assetRevision = "") {
  return `&rev=${encodeURIComponent(state.project?.image_revision || "0")}`
    + (assetRevision ? `&assetrev=${encodeURIComponent(assetRevision)}` : "");
}

// Le serveur reconnaît les vignettes et réserve une place aux aperçus détaillés.
function modelThumb(asset) {
  const faces = thumbFaces();
  return `/api/model/preview?path=${encodeURIComponent(asset.rel)}&w=320&h=240&thumb=1`
    + `&faces=${faces}`
    + (asset.mtime ? `&v=${asset.mtime}` : "") + imageRevisionQuery();
}

// 320 px : la tuile en fait 160, et une texture de 1024² pèse 20 fois plus
// lourd pour des pixels que l'écran ne montre jamais
function textureThumb(asset, size = 320) {
  const index = Number.isInteger(asset.index) && asset.index >= 0 ? asset.index : 0;
  return `/api/texture/png?path=${encodeURIComponent(asset.rel)}&index=${index}&thumb=1`
    + `&max=${size}`
    + (asset.mtime ? `&v=${asset.mtime}` : "") + imageRevisionQuery();
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
    ? (state.shots ? `<img decoding="async" alt="" data-thumb="${esc(modelThumb(asset))}">`
                   : "aperçu désactivé")
    : `<img decoding="async" alt="" data-thumb="${esc(textureThumb(asset))}">`;
  return `
    <article class="tile ${chosen ? "selected" : ""}" role="button" tabindex="0"
             aria-label="Ouvrir ${esc(asset.name)}" data-kind="${kind}"
             data-rel="${esc(asset.rel)}" data-key="${esc(key)}"
             data-check-kind="${kind}" data-check-rel="${esc(asset.rel)}" hidden>
      <button class="pick" data-pick="${esc(key)}" title="Sélectionner (Ctrl+clic)">✓</button>
      <div class="thumb ${shown === "aperçu désactivé" ? "placeholder" : ""} ${kind === "texture" ? "checker" : ""}">${shown}</div>
      <div class="meta">
        <strong data-no-i18n title="${esc(asset.rel)}">${esc(asset.name)}</strong>
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
  return `/api/model/preview?path=${encodeURIComponent(row.rel)}&w=320&h=240&thumb=1`
    + `&faces=${faces}`
    + (textured ? "&tex=1" : "&tex=0")
    + (row.mtime ? `&v=${row.mtime}` : "") + imageRevisionQuery();
}

function ensembleMatches(row) {
  const filter = state.filters.ensembles;
  return !filter || row.rel.toLowerCase().includes(filter);
}

function renderEnsembleAssociations() {
  const catalog = window.HookyPlayerCatalog, select = $("#ensemble-animation");
  if (!catalog || !select) return;
  const inventory = state.project?.assets || [];
  const current = inventory.find((asset) => asset.kind === "animation" && asset.rel === state.ensembleAnimation);
  if (!current) state.ensembleAnimation = "";
  const query = $("#ensemble-animation-search").value;
  const matches = inventory.filter((asset) => asset.kind === "animation" && catalog.matches(asset, query));
  const choices = matches.slice(0, 250);
  if (current && !choices.includes(current)) choices.unshift(current);
  select.innerHTML = '<option value="">Choisir une animation…</option>' + choices.map((asset) =>
    `<option data-no-i18n value="${esc(asset.rel)}" title="${esc(asset.rel)}">${esc(asset.rel)}</option>`).join("");
  select.value = state.ensembleAnimation;
  select.disabled = !choices.length;
  const status = $("#ensemble-association-status"), list = $("#ensemble-associations");
  if (!current) {
    status.textContent = matches.length
      ? `${matches.length} animation(s) trouvée(s) · ${Math.min(matches.length, 250)} affichées. Choisis-en une.`
      : "Aucune animation trouvée ; vérifie le filtre ou le scan du projet.";
    list.innerHTML = "";
    return;
  }
  const suggestions = catalog.suggestModels(inventory, current.rel), shown = suggestions.slice(0, 60);
  status.textContent = suggestions.length
    ? `${suggestions.length} modèle(s) suggéré(s) par nom · ${shown.length} affichés. À vérifier dans Player.`
    : "Aucun modèle suggéré par nom. Le choix manuel reste possible dans Animations et Player.";
  list.innerHTML = shown.map(({ asset, category, reason }) => `
    <li><div class="association-name"><strong data-no-i18n>${esc(asset.rel)}</strong>
      <small><span>${esc(reason)}</span> · <span>${esc(category)}</span> · <span>non vérifié</span></small></div>
      <div class="actions"><button type="button" class="btn tiny" data-ensemble-model="${esc(asset.rel)}">Voir la fiche</button>
      <button type="button" class="btn tiny primary" data-ensemble-player="${esc(asset.rel)}">Ouvrir comme base dans Player</button></div></li>`).join("");
}

async function renderEnsembles() {
  const grid = $("#ensemble-grid");
  if (!grid || state.view !== "ensembles") return;
  renderEnsembleAssociations();
  const render = ++ensembleRenderSequence;
  if (!state.ensembles.length) {
    const revision = state.project?.image_revision;
    // Plusieurs frappes ou changements de vue partagent la même requête.
    if (!ensembleLoad || ensembleLoad.revision !== revision) {
      ensembleLoad = { revision, promise: api("/api/ensembles") };
    }
    const load = ensembleLoad;
    try {
      const payload = await load.promise;
      if (render !== ensembleRenderSequence) return;
      // Un scan ou une édition a pu remplacer le projet pendant la requête.
      if (state.project?.image_revision !== revision) {
        if (state.view === "ensembles") return renderEnsembles();
        return;
      }
      state.ensembles = payload.ensembles || [];
    } catch (error) {
      if (render === ensembleRenderSequence && state.view === "ensembles") toast(error.message, "error");
      return;
    } finally {
      if (render === ensembleRenderSequence && ensembleLoad === load) ensembleLoad = null;
    }
  }
  // La vue ou le filtre a pu changer pendant le chargement de la liste.
  if (render !== ensembleRenderSequence || state.view !== "ensembles") return;
  const rows = state.ensembles.filter(ensembleMatches);
  const shown = rows.slice(0, state.shown.ensembles);
  state.ensembleCandidates = shown;
  thumbnailQueue.release(grid);
  grid.innerHTML = shown.length
    ? shown.map((row) => `
      <article class="tile ensemble" role="button" tabindex="0" aria-label="Ouvrir ${esc(row.name)}"
               data-kind="model" data-rel="${esc(row.rel)}"
               data-key="model:${esc(row.rel)}" data-ensemble="1"
               data-check-kind="model" data-check-rel="${esc(row.rel)}" hidden>
        <button class="pick" data-pick="model:${esc(row.rel)}" title="Sélectionner (Ctrl+clic)">✓</button>
        <div class="thumb">
          <img decoding="async" alt="" data-thumb="${esc(ensembleThumb(row))}">
        </div>
        <div class="meta">
          <strong data-no-i18n title="${esc(row.rel)}">${esc(row.name)}</strong>
          <small>${humanSize(row.size)}</small>
          <span class="badges">
            <span class="badge ${row.textures ? "" : "off"}">${row.textures} texture(s)</span>
            <span class="badge ${row.animations ? "" : "off"}">${row.animations} anim(s)</span>
          </span>
        </div>
      </article>`).join("")
    : `<p class="muted">Aucun modèle repéré dans ce dossier.</p>`;
  thumbnailQueue.observe(grid);
  assetValidation.setScope("view", shown.map((row) => ({ kind: "model", rel: row.rel })),
                           state.project?.image_revision);
  updateValidatedAssets();
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
  if (!grid || state.view !== "models") return;
  state.shots = $("#model-shots")?.checked !== false;
  const models = (state.project?.assets || [])
    .filter((asset) => asset.kind === "model" && matches(asset, state.filters.models));
  const shown = models.slice(0, state.shown.models);
  state.modelCandidates = shown;
  thumbnailQueue.release(grid);
  grid.innerHTML = shown.length
    ? shown.map((asset) => tileMarkup(asset, "model")).join("")
    : `<p class="muted">Aucun modèle repéré dans ce dossier.</p>`;
  thumbnailQueue.observe(grid);
  assetValidation.setScope("view", shown, state.project?.image_revision);
  updateValidatedAssets();
  updateMore("model", models.length, shown.length);
}

/* Une frappe = un rendu différé : sans ça, chaque lettre relançait la
   construction de 48 tuiles et le navigateur passait son temps à repeindre. */
function onInputDebounced(selector, handler, delay = 120) {
  const node = $(selector);
  if (!node) return;
  let timer = 0;
  node.oninput = (event) => {
    const value = event.target.value;
    clearTimeout(timer);
    timer = setTimeout(() => handler(value), delay);
  };
}

function matches(asset, filter) {
  return !filter || asset.rel.toLowerCase().includes(filter);
}

function focusSearch() {
  const selector = { models: "#search", textures: "#search-tex", ensembles: "#search-ens", player: "#player-base-search", sounds: "#search-sound", animations: "#search-animation", videos: "#search-video" }[state.view];
  if (selector) $(selector)?.focus();
}

function openPlayer(rel, options = {}) {
  // Changer de vue avant de charger : aucun contexte 3D de l'inspecteur ne
  // reste actif derrière la scène Player, même sur une petite machine.
  hideInspector();
  showView("player", { skipPlayer: true });
  void window.HookyPlayer?.open(rel, options);
}

async function openModel(rel, options = {}) {
  showInspector("Modèle", `<h3>Chargement du modèle…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let info;
  try {
    info = await api(`/api/model/info?path=${encodeURIComponent(rel)}`);
  } catch (error) {
    if (request === inspectionSequence) {
      showInspector("Modèle", `<h3>Modèle indisponible</h3><p class="diagnostic">${esc(error.message)}</p>`);
      toast(error.message, "error");
    }
    return;
  }
  if (request !== inspectionSequence) return;

  const name = info.models[0] || rel.split("/").pop();
  const animationCount = Number.isInteger(info.animation_count) ? info.animation_count : info.animations.length;
  const animationFiles = [...info.animations];
  if (options.animationRel && !animationFiles.some((item) => item.rel === options.animationRel)) {
    animationFiles.push({ rel: options.animationRel, name: options.animationRel.split("/").pop() });
  }
  const textured = info.textured ? "&tex=1" : "&tex=0";
  // Une ancienne session peut contenir « rapide » : ne jamais reprendre son
  // ancien plafond implicite de 30 000 faces dans cette vue.
  const shot = () => `/api/model/preview?path=${encodeURIComponent(rel)}&w=460&h=345&faces=0`
    + textured + imageRevisionQuery(info.revision);

  showInspector("Modèle", `
    <h3 data-no-i18n>${esc(name)}</h3>
    <div class="sub" data-no-i18n>${esc(rel)}</div>
    <canvas class="preview model-canvas" id="model-canvas" tabindex="0"
      aria-label="Vue 3D : glisser pour tourner, molette pour zoomer, flèches et plus ou moins au clavier"></canvas>
    <img class="preview" id="model-shot" alt="Aperçu fixe de ${esc(name)}" hidden>
    <p class="model-view-status" id="model-view-status" role="status">Chargement de la géométrie complète…</p>
    <p class="hint">Glisser pour tourner · molette pour zoomer · flèches et +/− au clavier · Échap quitte le plein écran</p>
    <div class="model-view-controls" aria-label="Contrôles de la vue 3D">
      <button class="btn tiny" id="model-reset" type="button">Recentrer</button>
      <button class="btn tiny" id="model-fullscreen" type="button">Plein écran</button>
      <label><input type="checkbox" id="model-flat"> Faces plates</label>
      <label><input type="checkbox" id="model-wire"> Fil de fer</label>
      <label><input type="checkbox" id="model-tex" ${info.textured ? "checked" : ""}> Texturé</label>
      <label for="model-upscale">Rendu</label>
      <select id="model-upscale" aria-label="Rendu 3D">
        <option value="native">Résolution native</option>
        <option value="fsr">FSR 1 expérimental</option>
      </select>
    </div>
    <p class="hint">Matériaux du jeu non reproduits. FSR n'altère pas les faces ; l'animation est une prévisualisation expérimentale.</p>
    ${animationFiles.length ? `<div class="animation-controls">
      <label>Fichier d'animation <select id="model-animation-file"><option value="">Pose statique</option>
        ${animationFiles.map((a) => `<option data-no-i18n value="${esc(a.rel)}">${esc(a.name)}</option>`).join("")}</select></label>
      <label>Clip <select id="model-animation-clip" disabled><option value="">Choisir un fichier</option></select></label>
      <div class="model-view-controls">
        <button class="btn tiny" type="button" id="model-animation-play" disabled>Pause</button>
        <label><input type="checkbox" id="model-animation-loop"> Boucle</label>
        <input type="range" id="model-animation-frame" min="0" max="1" step="0.1" value="0" disabled aria-label="Image d'animation">
        <output id="model-animation-time">0 / 0</output>
      </div>
      <p class="hint" id="model-animation-status">30 images/s par convention ; rig, matériaux et physique non validés en jeu.</p>
    </div>` : ""}
    <dl class="kv">
      <dt>Version</dt><dd>${esc(info.version)}</dd>
      <dt>Maillages</dt><dd>${info.meshes}${info.dropped ? ` <span class="warn">(${info.dropped} ignoré(s))</span>` : ""}</dd>
      <dt>Faces</dt><dd>${info.faces.toLocaleString("fr-FR")}</dd>
      <dt>Sommets</dt><dd>${info.vertices.toLocaleString("fr-FR")}</dd>
      <dt>Dimensions</dt><dd>${info.bounds.map((value) => value.toFixed(3)).join(" × ")}</dd>
      <dt>Matériaux</dt><dd data-no-i18n>${info.materials.length ? info.materials.map(esc).join(", ") : "—"}</dd>
      <dt>Textures</dt><dd>${info.textures.length ? esc(info.texture_match || "candidats à vérifier") : "aucune repérée"}</dd>
      <dt>Animations</dt><dd>${animationCount ? `${animationCount} fichier(s) suggéré(s) par nom` : "aucune repérée"}</dd>
      <dt>Budget</dt><dd id="model-budget" class="muted">calcul…</dd>
    </dl>
    ${info.warnings.length ? `<details class="warns"><summary>${info.warnings.length} remarque(s) de décodage</summary>
      <ul class="todo">${info.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
    ${info.textures.length ? `<div class="ensemble-strip">
      <h4>Cartes à vérifier · ${esc(info.texture_match || "candidats")}</h4>
      <div class="strip">${info.textures.map((t) => `
        <button class="strip-item" data-open-texture="${esc(t.rel)}" data-open-texture-index="${Number(t.index) || 0}" title="${esc(t.rel)}">
          <img decoding="async" alt="" data-thumb="${esc(textureThumb(t, 192))}">
          <span data-no-i18n>${esc(t.name)}${t.material ? ` · ${esc(t.material)}` : ""}</span>
          <span class="strip-role">${ROLE_BADGE(t.role)}</span>
        </button>`).join("")}</div>
    </div>` : ""}
    ${info.animations.length ? `<details class="warns anims"><summary>${animationCount} fichier(s) d'animation suggéré(s)${animationCount > info.animations.length ? ` · ${info.animations.length} affichés` : ""}</summary>
      <ul class="todo">${info.animations.map((a) => `<li><span data-no-i18n>${esc(a.name)}</span> <span class="muted">${humanSize(a.size)}</span></li>`).join("")}</ul>
      <p class="hint">Les fichiers d'animation du jeu restent inchangés.</p></details>` : ""}
    <div class="actions">
      <button class="btn" id="btn-uv">Vérifier les UV</button>
      <button class="btn" id="btn-compose-player" type="button">Composer dans Player</button>
      <button class="btn primary" id="btn-export-animations" ${animationFiles.length ? "" : "disabled"}>Télécharger 1 GLB · toutes les animations détectées</button>
      <button class="btn" id="btn-export-model">Exporter en GLB / glTF / OBJ</button>
      <button class="btn ghost" id="btn-turntable">Planche PNG</button>
      <button class="btn ghost" id="btn-translate-model">Importer un modèle…</button>
    </div>
    <p class="hint">Le GLB groupé inclut les clips des fichiers suggérés par nom et le fichier d'aperçu sélectionné. L'association est indicative : d'autres fichiers peuvent manquer ; les clips incompatibles sont signalés.</p>
    ${!animationFiles.length ? `<p class="hint">Aucune animation suggérée : ouvre un fichier depuis Animations et choisis ce modèle.</p>` : ""}
    <p class="hint">Le clip sélectionné est exporté dans GLB/glTF si son squelette est compatible. Sans clip, l'export est statique. OBJ/MTL restent toujours statiques.</p>
    <p class="hint" id="model-budget-note">Estimation de l'aperçu PNG complet…</p>`);

  ensureTab({ id: `model:${rel}`, kind: "model", label: name, doc: { type: "model", rel } });
  state.tab = `model:${rel}`;
  renderTabs();
  markTile("model", rel);

  async function refreshBudget() {
    const node = $("#model-budget");
    const note = $("#model-budget-note");
    if (!node) return;
    try {
      const payload = await api(`/api/model/plan?path=${encodeURIComponent(rel)}&w=460&h=345&faces=0`);
      if (node !== $("#model-budget")) return;
      const plan = payload.plan;
      node.innerHTML = `${humanBytes(plan.total_bytes)} estimés sur `
        + `${humanBytes(plan.budget_bytes)} (${Math.round(plan.fill * 100)} %) · PNG`;
      note.textContent = plan.notes.length ? plan.notes.join(" · ")
        : "Aperçu PNG complet : toutes les faces dans le budget.";
    } catch (error) {
      if (node !== $("#model-budget")) return;
      node.textContent = "PNG indisponible";
      note.textContent = error.message;
    }
  }

  const canvas = $("#model-canvas"), image = $("#model-shot"), viewStatus = $("#model-view-status");
  const animFile = $("#model-animation-file"), animClip = $("#model-animation-clip");
  const animFrame = $("#model-animation-frame"), animPlay = $("#model-animation-play");
  const animLoop = $("#model-animation-loop"), animStatus = $("#model-animation-status");
  let animationRequest = 0, modelReady = Promise.resolve();
  const animationIdle = () => {
    if (!animClip) return;
    animClip.disabled = true; animFrame.disabled = true; animPlay.disabled = true;
    animFrame.value = "0"; $("#model-animation-time").textContent = "0 / 0";
  };
  const loadSelectedClip = async () => {
    if (!animFile?.value || !animClip?.value) return;
    const serial = ++animationRequest, file = animFile.value, index = Number(animClip.value);
    // Invalider immédiatement un éventuel setAnimation en attente de skin :
    // une sélection plus récente ne doit pas afficher l'ancien clip.
    modelViewport?.stopAnimation();
    animStatus.textContent = "Chargement du clip et du squelette…";
    animFrame.disabled = true; animPlay.disabled = true;
    try {
      const [clip] = await Promise.all([
        api(`/api/animation/clip?path=${encodeURIComponent(file)}&index=${index}`), modelReady,
      ]);
      if (serial !== animationRequest || canvas !== $("#model-canvas")) return;
      const viewport = modelViewport;
      if (!viewport || canvas.hidden) throw new Error("Ouvre d'abord la vue 3D.");
      const matched = await viewport.setAnimation(`/api/model/skin?path=${encodeURIComponent(rel)}`, clip);
      if (!matched || serial !== animationRequest || viewport !== modelViewport) return;
      animFrame.max = String(Math.max(clip.frames, 1)); animFrame.value = "0"; animFrame.disabled = false;
      animPlay.disabled = false; animPlay.textContent = "Pause";
      animLoop.checked = Boolean(clip.loop); viewport.loopAnimation(animLoop.checked);
      animStatus.textContent = `${matched.matched} os associés${matched.missing ? `, ${matched.missing} absents` : ""} · `
        + "30 images/s par convention · lecture expérimentale, non validée en jeu.";
    } catch (error) {
      if (serial !== animationRequest || canvas !== $("#model-canvas")) return;
      modelViewport?.stopAnimation(); animStatus.textContent = `Animation indisponible : ${error.message}`;
    }
  };
  const loadAnimationFile = async (preferred = null) => {
    if (!animFile) return;
    const serial = ++animationRequest, file = animFile.value;
    modelViewport?.stopAnimation(); animationIdle();
    animClip.innerHTML = '<option value="">Choisir un fichier</option>';
    if (!file) { animStatus.textContent = "Pose statique."; return; }
    animStatus.textContent = "Recherche des clips…";
    try {
      const result = await api(`/api/animation/list?path=${encodeURIComponent(file)}`);
      if (serial !== animationRequest || canvas !== $("#model-canvas")) return;
      const clips = result.clips.filter((clip) => clip.supported);
      animClip.innerHTML = clips.length ? clips.map((clip) => `<option value="${clip.index}">${esc(clip.name)} · ${clip.frames} images</option>`).join("")
        : '<option value="">Aucun clip avec en-tête pris en charge</option>';
      if (!clips.length) { animStatus.textContent = "Aucun en-tête de clip squelettique pris en charge."; return; }
      animClip.disabled = false;
      if (preferred !== null && clips.some((clip) => clip.index === preferred)) animClip.value = String(preferred);
      await loadSelectedClip();
    } catch (error) {
      if (serial === animationRequest && canvas === $("#model-canvas")) animStatus.textContent = `Animation indisponible : ${error.message}`;
    }
  };
  if (animFile) {
    animFile.onchange = () => loadAnimationFile();
    animClip.onchange = loadSelectedClip;
    animFrame.oninput = () => modelViewport?.seekAnimation(Number(animFrame.value));
    animLoop.onchange = () => modelViewport?.loopAnimation(animLoop.checked);
    animPlay.onclick = () => modelViewport?.playAnimation(!modelViewport?.animation?.playing);
  }
  const failView = (message) => {
    if (canvas !== $("#model-canvas")) return;
    disposeModelViewport();
    canvas.hidden = true; image.hidden = false; image.src = shot();
    $("#btn-turntable").textContent = "Réessayer la 3D";
    viewStatus.textContent = `${message} Tentative d'aperçu PNG fixe.`;
  };
  image.onerror = () => { viewStatus.textContent += " PNG indisponible : consulte le budget ci-dessous."; };
  const startView = () => {
    canvas.hidden = false; image.hidden = true; $("#btn-turntable").textContent = "Planche PNG";
    $("#model-flat").checked = false; $("#model-wire").checked = false;
    $("#model-upscale").value = state.settings?.viewer_upscale || "native";
    viewStatus.textContent = "Chargement de la géométrie complète…";
    try {
      const viewport = new ModelViewport(canvas, {
        textured: $("#model-tex").checked, revision: info.revision, renderSettings: settingsOrDefault(),
        onStatus: (message) => { if (modelViewport === viewport) viewStatus.textContent = message; },
        onFailure: failView,
        onModeFallback: () => { if (modelViewport === viewport) $("#model-upscale").value = "native"; },
        onAnimationError: (message) => {
          if (modelViewport !== viewport || !animFrame) return;
          animFrame.disabled = true; animPlay.disabled = true;
          animStatus.textContent = `Animation interrompue, pose de repos : ${message}`;
        },
        onAnimationFrame: (frame, playing) => {
          if (modelViewport !== viewport || !animFrame) return;
          animFrame.value = String(frame);
          $("#model-animation-time").textContent = `${frame.toFixed(1)} / ${animFrame.max}`;
          animPlay.textContent = playing ? "Pause" : "Lire";
        },
      });
      modelViewport = viewport;
      modelViewportForCleanup = viewport;
      void viewport.setMode(state.settings?.viewer_upscale || "native");
      modelReady = viewport.load(`/api/model/geometry?path=${encodeURIComponent(rel)}`).catch((error) => {
        if (modelViewport === viewport) failView(`Vue 3D indisponible : ${error.message}`);
      });
      if (animFile?.value && animClip?.value) void loadSelectedClip();
    } catch (error) { failView(`Vue 3D indisponible : ${error.message}`); }
  };
  startView();
  if (animFile && options.animationRel) {
    animFile.value = options.animationRel;
    void loadAnimationFile(Number.isInteger(options.animationIndex) ? options.animationIndex : null);
  }
  $("#model-reset").onclick = () => modelViewport?.reset();
  $("#model-fullscreen").onclick = async () => {
    if (!modelViewport || canvas.hidden || !canvas.requestFullscreen) {
      toast("Plein écran 3D indisponible.", "error"); return;
    }
    try { await canvas.requestFullscreen(); canvas.focus(); }
    catch (_) { toast("Plein écran refusé par le navigateur ou la fenêtre native.", "error"); }
  };
  $("#model-flat").onchange = (event) => modelViewport?.setFlat(event.target.checked);
  $("#model-wire").onchange = (event) => {
    if (!modelViewport?.setWire(event.target.checked)) event.target.checked = false;
  };
  $("#model-tex").onchange = (event) => modelViewport?.setTextured(event.target.checked);
  $("#model-upscale").onchange = (event) => modelViewport?.setMode(event.target.value);
  $("#btn-turntable").onclick = () => {
    if (canvas.hidden) { startView(); return; }
    animationRequest += 1;
    disposeModelViewport();
    if (animFrame) { animFrame.disabled = true; animPlay.disabled = true; }
    canvas.hidden = true; image.hidden = false; image.src = `${shot()}&views=4`;
    $("#btn-turntable").textContent = "Retour 3D";
    viewStatus.textContent = "Planche PNG fixe · toutes les faces décodées";
  };
  $("#btn-export-model").onclick = () => exportModel(rel);
  $("#btn-compose-player").onclick = () => {
    const animationRel = animFile?.value || "";
    const animationIndex = animClip?.value ? Number(animClip.value) : null;
    openPlayer(rel, { animationRel, animationIndex });
  };
  $("#btn-export-animations").onclick = () => exportAllAnimations(rel);
  $("#btn-uv").onclick = () => showUv(rel);
  $$("[data-open-texture]", document).forEach((node) => {
    node.onclick = () => openTexture(node.dataset.openTexture, Number(node.dataset.openTextureIndex) || 0);
  });
  $("#btn-translate-model").onclick = () => {
    const search = $("#model-target-search");
    if (search) search.value = rel;
    state.shown.chooser = 48;
    restoredChoices["model-target"] = rel;
    showView("import");
    const select = $("#model-target");
    if (select && assetValidation.entry("model", rel)?.injectable) select.value = rel;
    modelTargetChanged();
  };
  refreshBudget();
  if (options.inspect === false) return;
}

/* ------------------------------------------------------------------ animations */
function renderAnimations() {
  const grid = $("#animation-grid");
  if (!grid || state.view !== "animations") return;
  const assets = (state.project?.assets || []).filter((a) => a.kind === "animation" && matches(a, state.filters.animations));
  const shown = assets.slice(0, state.shown.animations);
  $("#animation-count").textContent = `${assets.length} fichier(s) d'animation repéré(s) · cliquer pour inspecter`;
  grid.innerHTML = shown.length ? shown.map((a) => `
    <article class="tile sound-tile" role="button" tabindex="0" aria-label="Ouvrir ${esc(a.name)}"
             data-kind="animation" data-rel="${esc(a.rel)}">
      <div class="thumb" aria-hidden="true">▶</div>
      <div class="meta"><strong data-no-i18n title="${esc(a.rel)}">${esc(a.name)}</strong>
        <small>${humanSize(a.size)} · BFRES</small></div>
    </article>`).join("") : `<p class="muted">Aucune animation repérée dans ce dossier.</p>`;
  updateMore("animation", assets.length, shown.length);
}

async function openAnimation(rel) {
  showInspector("Animation", `<h3>Chargement des clips…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let result;
  try { result = await api(`/api/animation/list?path=${encodeURIComponent(rel)}`); }
  catch (error) {
    if (request === inspectionSequence) showInspector("Animation", `<h3>Animation indisponible</h3><p>${esc(error.message)}</p>`);
    return;
  }
  if (request !== inspectionSequence) return;
  const clips = result.clips.filter((clip) => clip.supported);
  const models = (state.project?.assets || []).filter((a) => a.kind === "model");
  const key = rel.split("/").pop().split(".")[0].replace(/_(?:player_)?animation$/i, "").replace(/_anim$/i, "").toLowerCase();
  const candidate = models.find((a) => a.name.split(".")[0].toLowerCase() === key);
  const filename = rel.split("/").pop();
  showInspector("Animation", `
    <h3 data-no-i18n>${esc(filename)}</h3><div class="sub" data-no-i18n>${esc(rel)}</div>
    <p>${result.clips.length} clip(s) squelettique(s), ${clips.length} avec en-tête pris en charge ; courbes vérifiées à l'ouverture.</p>
    <label>Clip <select id="animation-clip" ${clips.length ? "" : "disabled"}>
      ${clips.map((c) => `<option value="${c.index}">${esc(c.name)} · ${c.frames} images</option>`).join("")}</select></label>
    <label>Modèle à associer <select id="animation-model" ${models.length ? "" : "disabled"}>
      <option value="">Choisir un modèle…</option>
      ${models.map((m) => `<option data-no-i18n value="${esc(m.rel)}" ${m === candidate ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></label>
    <div class="actions"><button class="btn primary" type="button" id="animation-open-model" ${clips.length && models.length ? "" : "disabled"}>
      Ouvrir le modèle et lire</button>
      <button class="btn" type="button" id="animation-open-player" ${clips.length && models.length ? "" : "disabled"}>Composer dans Player</button>
      <a class="btn" id="animation-download" download="animation.json">Exporter les courbes JSON</a></div>
    <p class="hint">Association par nom proposée à titre indicatif. Ouvre un modèle : le bouton de GLB groupé inclura tous les clips de ce fichier et ceux suggérés pour le modèle. OBJ/MTL restent statiques et les fichiers du jeu ne sont pas modifiés.</p>`);
  const clipSelect = $("#animation-clip"), download = $("#animation-download");
  const updateDownload = () => {
    download.hidden = !clipSelect.value;
    download.href = `/api/animation/clip?path=${encodeURIComponent(rel)}&index=${encodeURIComponent(clipSelect.value)}&download=1`;
  };
  clipSelect.onchange = updateDownload; updateDownload();
  $("#animation-open-model").onclick = () => {
    const model = $("#animation-model").value;
    if (!model || !clipSelect.value) { toast("Choisis un modèle et un clip.", "error"); return; }
    void openModel(model, { animationRel: rel, animationIndex: Number(clipSelect.value) });
  };
  $("#animation-open-player").onclick = () => {
    const model = $("#animation-model").value;
    if (!model || !clipSelect.value) { toast("Choisis un modèle et un clip.", "error"); return; }
    openPlayer(model, { animationRel: rel, animationIndex: Number(clipSelect.value) });
  };
  ensureTab({ id: `animation:${rel}`, kind: "animation", label: filename, doc: { type: "animation", rel } });
  state.tab = `animation:${rel}`; renderTabs();
}

/* ------------------------------------------------------------------ textures */
async function renderTextures() {
  const grid = $("#texture-grid");
  if (!grid || state.view !== "textures") return;
  const role = $("#tex-role")?.value || "";
  const textures = (state.project?.assets || [])
    .filter((asset) => asset.kind === "texture" && matches(asset, state.filters.textures)
      && (!role || (asset.role || "autre") === role));
  const shown = textures.slice(0, state.shown.textures);
  state.textureCandidates = shown;
  thumbnailQueue.release(grid);
  grid.innerHTML = shown.length
    ? shown.map((asset) => tileMarkup(asset, "texture")).join("")
    : `<p class="muted">Aucune texture repérée dans ce dossier.</p>`;
  thumbnailQueue.observe(grid);
  assetValidation.setScope("view", shown, state.project?.image_revision);
  updateValidatedAssets();
  updateMore("texture", textures.length, shown.length);
}

async function openTexture(rel, index = 0) {
  index = Number.isInteger(index) && index >= 0 ? index : 0;
  showInspector("Texture", `<h3>Chargement de la texture…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let info;
  try {
    info = await api(`/api/texture/info?path=${encodeURIComponent(rel)}`);
  } catch (error) {
    if (request === inspectionSequence) {
      showInspector("Texture", `<h3>Texture indisponible</h3><p>${esc(error.message)}</p>`);
      toast(error.message, "error");
    }
    return;
  }
  if (request !== inspectionSequence) return;
  const texture = info.textures.find((item) => item.index === index);
  if (!texture) {
    showInspector("Texture", `<h3>Texture indisponible</h3><p>Texture ${index} absente de ce fichier.</p>`);
    return;
  }
  const editable = info.textures.filter((item) => item.editable).length;
  const image = `/api/texture/png?path=${encodeURIComponent(rel)}&index=${index}`
    + imageRevisionQuery(info.revision);
  showInspector("Texture", `
    <h3 data-no-i18n>${esc(texture.name)}</h3>
    <div class="sub" data-no-i18n>${esc(rel)}</div>
    ${info.textures.length > 1 ? `<label class="field"><span>Texture du conteneur</span>
      <select id="texture-index">${info.textures.map((item) =>
        `<option data-no-i18n value="${item.index}" ${item.index === index ? "selected" : ""}>${esc(item.name)} · ${item.index}</option>`).join("")}</select></label>` : ""}
    <img class="preview" alt="" src="${esc(image)}">
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

  const tabId = `texture:${rel}${index ? `#${index}` : ""}`;
  ensureTab({ id: tabId, kind: "texture", label: texture.name, doc: { type: "texture", rel, index } });
  state.tab = tabId;
  renderTabs();
  markTile("texture", rel);
  const textureSelect = $("#texture-index");
  if (textureSelect) textureSelect.onchange = (event) => openTexture(rel, Number(event.target.value));

  $("#btn-export-png").onclick = async () => {
    try {
      const result = await api("/api/texture/export", {
        method: "POST", body: JSON.stringify({ path: rel, index }),
      });
      toast(`PNG écrit : ${result.file}`);
    } catch (error) { toast(error.message, "error"); }
  };

  const input = $("#file-import");
  $("#btn-import").onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const request = inspectionSequence;
    try {
      const base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Impossible de lire l'image choisie."));
        reader.onabort = () => reject(new Error("Lecture de l'image annulée."));
        reader.readAsDataURL(file);
      });
      const result = await api("/api/texture/import", {
        method: "POST",
        body: JSON.stringify({ path: rel, index, image: base64 }),
      });
      toast(`Texture remplacée (${result.texture}) → ${result.wrote}`);
      await refresh();
      // L'utilisateur a pu ouvrir une autre fiche pendant l'import.
      if (request === inspectionSequence) openTexture(rel, index);
    } catch (error) { toast(error.message, "error"); }
  };
}

/* ------------------------------------------------------------------ sons */
async function renderSounds() {
  const grid = $("#sound-grid");
  if (!grid || state.view !== "sounds") return;
  const sounds = (state.project?.assets || [])
    .filter((asset) => asset.kind === "sound" && matches(asset, state.filters.sounds));
  const shown = sounds.slice(0, state.shown.sounds);
  state.soundCandidates = shown;
  grid.innerHTML = shown.length ? shown.map((asset) => `
    <article class="tile sound-tile" role="button" tabindex="0" aria-label="Ouvrir ${esc(asset.name)}"
             data-kind="sound" data-rel="${esc(asset.rel)}"
             data-check-kind="sound" data-check-rel="${esc(asset.rel)}" hidden>
      <div class="thumb" aria-hidden="true">♫</div>
      <div class="meta"><strong data-no-i18n title="${esc(asset.rel)}">${esc(asset.name)}</strong>
        <small>${humanSize(asset.size)} · BWAV</small></div>
    </article>`).join("") : `<p class="muted">Aucun fichier BWAV repéré dans ce dossier.</p>`;
  assetValidation.setScope("view", shown, state.project?.image_revision);
  updateValidatedAssets();
  updateMore("sound", sounds.length, shown.length);
}

async function openSound(rel) {
  showInspector("Son", `<h3>Ouverture du son…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let info;
  try {
    info = await api(`/api/sound/info?path=${encodeURIComponent(rel)}`);
  } catch (error) {
    if (request === inspectionSequence) {
      showInspector("Son", `<h3>Son indisponible</h3><p>${esc(error.message)}</p>`);
      toast(error.message, "error");
    }
    return;
  }
  if (request !== inspectionSequence) return;
  const baseUrl = `/api/sound/convert?path=${encodeURIComponent(rel)}`;
  const url = baseUrl + `&format=${info.output === "opus" ? "opus" : "wav"}` + imageRevisionQuery();
  const filename = rel.split("/").pop().replace(/\.bwav(?:\.zs)?$/i, "");
  const formats = ["wav", "mp3", "flac", "ogg", ...(info.output === "opus" ? ["opus"] : [])];
  showInspector("Son", `
    <h3 data-no-i18n>${esc(filename)}</h3><div class="sub" data-no-i18n>${esc(rel)}</div>
    <audio class="sound-player" id="sound-player" controls preload="none" src="${esc(url)}"></audio>
    <dl class="kv">
      <dt>Format source</dt><dd>${esc(info.format)}</dd>
      <dt>Canaux</dt><dd>${info.channels}</dd>
      <dt>Fréquence</dt><dd>${info.sample_rate.toLocaleString(document.documentElement.lang || "fr")} Hz</dd>
      <dt>Durée</dt><dd>${info.seconds.toFixed(2)} s</dd>
      <dt>Boucle</dt><dd>${info.loop_start === null ? "aucune repérée"
        : `${info.loop_start} → ${info.loop_end} échantillons`}</dd>
    </dl>
    <div class="actions">${formats.map((format) => `<a class="btn ${format === "wav" ? "primary" : ""}"
      href="${esc(baseUrl + `&format=${format}&download=1`)}" download="${esc(filename + "." + format)}">${format.toUpperCase()}</a>`).join("")}</div>
    <p class="hint">Opus est exporté sans réencodage. WAV, MP3, FLAC et Ogg sont décodés puis convertis ; les marqueurs de boucle ne sont pas conservés dans ces exports.</p>
    <button class="btn" id="sound-import-btn" type="button">Remplacer le son…</button>
    <input type="file" id="sound-import-file" accept=".wav,.mp3,.flac,.ogg,.opus,.aiff,audio/*" hidden>
    <p class="hint">La source doit avoir ${info.channels} canal(aux) à ${info.sample_rate} Hz (24 Mio maximum).
      Le remplacement produit un BWAV PCM16 dans la copie de travail, même si l'original est Opus ou DSP.
      BARS, événements et RESTBL ne sont pas modifiés ; fonctionnement en jeu non garanti.</p>`);
  $("#sound-player").onerror = () => {
    toast("Lecture audio impossible dans ce navigateur.", "error");
  };
  const input = $("#sound-import-file");
  $("#sound-import-btn").onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 24 * 1024 * 1024) { toast("Fichier audio trop volumineux (24 Mio maximum).", "error"); input.value = ""; return; }
    if (!window.confirm(tr("Remplacer la copie de travail ? Le son sera converti en BWAV PCM16 expérimental. BARS et RESTBL ne seront pas mis à jour ; la compatibilité en jeu n'est pas garantie."))) { input.value = ""; return; }
    const selected = inspectionSequence;
    try {
      const encoded = await readModelFile(file);
      const result = await api("/api/sound/import", {
        method: "POST", body: JSON.stringify({ path: rel, audio: encoded.data }),
      });
      toast(`Copie audio enregistrée → ${result.wrote}`);
      await refresh();
      if (selected === inspectionSequence) openSound(rel);
    } catch (error) { toast(error.message, "error"); }
    finally { input.value = ""; }
  };
  ensureTab({ id: `sound:${rel}`, kind: "sound", label: filename, doc: { type: "sound", rel } });
  state.tab = `sound:${rel}`;
  renderTabs();
}

/* ------------------------------------------------------------------ vidéos */
function renderVideos() {
  const grid = $("#video-grid");
  if (!grid || state.view !== "videos") return;
  const assets = (state.project?.assets || []).filter((a) => a.kind === "video" && matches(a, state.filters.videos));
  const shown = assets.slice(0, state.shown.videos);
  $("#video-count").textContent = `${assets.length} vidéo(s) WebM repérée(s)`;
  grid.innerHTML = shown.length ? shown.map((a) => `
    <article class="tile sound-tile" role="button" tabindex="0" aria-label="Ouvrir ${esc(a.name)}"
             data-kind="video" data-rel="${esc(a.rel)}">
      <div class="thumb" aria-hidden="true">▶</div>
      <div class="meta"><strong data-no-i18n title="${esc(a.rel)}">${esc(a.name)}</strong>
        <small>${humanSize(a.size)} · WebM</small></div>
    </article>`).join("") : `<p class="muted">Aucune vidéo WebM repérée dans ce dossier.</p>`;
  updateMore("video", assets.length, shown.length);
}

async function openVideo(rel) {
  showInspector("Vidéo", `<h3>Ouverture de la vidéo…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let info;
  try { info = await api(`/api/video/info?path=${encodeURIComponent(rel)}`); }
  catch (error) {
    if (request === inspectionSequence) showInspector("Vidéo", `<h3>Vidéo indisponible</h3><p>${esc(error.message)}</p>`);
    return;
  }
  if (request !== inspectionSequence) return;
  const url = `/api/video/stream?path=${encodeURIComponent(rel)}`;
  showInspector("Vidéo", `
    <h3 data-no-i18n>${esc(rel.split("/").pop())}</h3><div class="sub" data-no-i18n>${esc(rel)}</div>
    <video class="video-player" id="video-player" controls preload="metadata" src="${esc(url + imageRevisionQuery())}"></video>
    <p class="hint" id="video-status">Conteneur WebM vérifié ; lecture selon les codecs du navigateur.</p>
    <p class="hint">${humanSize(info.size)} · ${info.edited ? "copie de travail" : "fichier source"}</p>
    <div class="actions"><a class="btn primary" href="${esc(url + "&download=1")}" download="video.webm">Télécharger WebM</a>
      <button class="btn" id="video-import-btn" type="button">Remplacer la vidéo…</button></div>
    <input type="file" id="video-import-file" accept=".webm,video/webm" hidden>
    <p class="warning-note">Copie WebM sans transcodage (32 Mio maximum). Seul l'en-tête du conteneur est vérifié ;
      codecs, durée, résolution et compatibilité en jeu ne sont pas garantis. Les données NDVE, audio séparé et RESTBL ne sont pas mis à jour.</p>`);
  const player = $("#video-player"), statusNode = $("#video-status"), input = $("#video-import-file");
  player.onloadedmetadata = () => { statusNode.textContent = `${player.videoWidth} × ${player.videoHeight} · ${Number.isFinite(player.duration) ? player.duration.toFixed(2) + " s" : "durée inconnue"}`; };
  player.onerror = () => { statusNode.textContent = "Lecture impossible : ce navigateur ne prend peut-être pas en charge les codecs de cette vidéo."; };
  $("#video-import-btn").onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files?.[0]; if (!file) return;
    if (file.size > 32 * 1024 * 1024) { toast("Fichier vidéo trop volumineux (32 Mio maximum).", "error"); input.value = ""; return; }
    if (!window.confirm(tr("Remplacer la copie de travail ? Aucun transcodage ni vérification en jeu ; NDVE et RESTBL ne seront pas mis à jour."))) { input.value = ""; return; }
    const selected = inspectionSequence;
    try {
      const encoded = await readModelFile(file);
      const result = await api("/api/video/import", { method: "POST", body: JSON.stringify({ path: rel, video: encoded.data }) });
      toast(`Copie vidéo enregistrée → ${result.wrote}`);
      await refresh();
      if (selected === inspectionSequence) openVideo(rel);
    } catch (error) { toast(error.message, "error"); }
    finally { input.value = ""; }
  };
  ensureTab({ id: `video:${rel}`, kind: "video", label: rel.split("/").pop(), doc: { type: "video", rel } });
  state.tab = `video:${rel}`; renderTabs();
}

/* ------------------------------------------------------------------ modèles cibles pour l'import */
function fillModelTargets() {
  const models = (state.chooserModels || []).filter((asset) => assetValidation.valid("model", asset.rel)
    && assetValidation.entry("model", asset.rel)?.injectable);
  const select = $("#model-target");
  if (select) {
    const prompt = models.length ? "(choisir un modèle cible)" : "(aucun modèle réinjectable vérifié)";
    const html = `<option value="">${prompt}</option>` + models.map((asset) =>
      `<option data-no-i18n value="${esc(asset.rel)}">${esc(asset.name)}</option>`).join("");
    if (select.dataset.optionsHtml !== html) {
      const previous = select.value;
      select.innerHTML = html;
      select.dataset.optionsHtml = html;
      if (models.some((item) => item.rel === previous)) select.value = previous;
    }
  }
  // Ne jamais injecter par défaut dans le premier modèle validé.
  applyRestoredChoices();
  updateModelImportControls();
}

function requestChooserChecks() {
  const assets = state.project?.assets || [];
  const cap = state.shown.chooser || 48;
  const filter = ($("#model-target-search")?.value || "").trim().toLowerCase();
  const models = assets.filter((asset) => asset.kind === "model" && matches(asset, filter));
  state.chooserModels = models.slice(0, cap);
  assetValidation.setScope("view", state.chooserModels, state.project?.image_revision);
  const more = $("#model-import-validate-more");
  if (more) more.hidden = models.length <= cap;
  updateValidatedAssets();
}

function openDoc(doc) {
  if (state.view === "player") showView("models", { tab: false });
  if (doc.type === "model") return openModel(doc.rel);
  if (doc.type === "texture") return openTexture(doc.rel, doc.index || 0);
  if (doc.type === "sound") return openSound(doc.rel);
  if (doc.type === "animation") return openAnimation(doc.rel);
  if (doc.type === "video") return openVideo(doc.rel);
}

/* ------------------------------------------------------------------ import OBJ / glTF */
let modelImportFiles = [];
let modelImportRevision = 0;
let modelTargetRevision = 0;
let modelImportAnalysisRevision = -1;
let modelImportCanImport = false;
let modelImportPending = false;

function updateModelImportControls() {
  const inspect = $("#model-inspect");
  const inject = $("#model-inject");
  const target = $("#model-target")?.value || "";
  if (inspect) inspect.disabled = modelImportPending || !modelImportFiles.length;
  if (inject) inject.disabled = modelImportPending || !modelImportFiles.length
    || modelImportAnalysisRevision !== modelImportRevision || !modelImportCanImport
    || !target || !assetValidation.valid("model", target)
    || !assetValidation.entry("model", target)?.injectable;
}

function renderModelImport() {
  requestChooserChecks();
  updateModelImportControls();
}

function setModelImportFiles(fileList) {
  const files = [...(fileList || [])];
  const names = new Set();
  const primary = files.filter((file) => /\.(obj|gltf|glb)$/i.test(file.name));
  let error = "";
  if (!files.length) error = "Aucun fichier choisi.";
  else if (files.length > 17 || primary.length !== 1) error = "Choisis un seul modèle et au plus 16 fichiers .bin.";
  else if (files.some((file) => !/\.(obj|gltf|glb|bin)$/i.test(file.name))) error = "Formats acceptés : OBJ, glTF, GLB et fichiers .bin.";
  else if (files.length > 1 && !/\.gltf$/i.test(primary[0].name)) error = "Les fichiers .bin accompagnent uniquement un .gltf.";
  else if (files.some((file) => { const name = file.name.toLocaleLowerCase(); const duplicate = names.has(name); names.add(name); return duplicate; })) error = "Deux fichiers portent le même nom.";
  else if (files.some((file) => !file.size) || files.reduce((total, file) => total + file.size, 0) > 64 * 1024 * 1024) error = "Fichier vide ou ensemble supérieur à 64 Mio.";
  modelImportFiles = error ? [] : files;
  modelImportRevision += 1;
  modelImportCanImport = false;
  updateModelImportControls();
  for (const selector of ["#model-source-shot", "#model-result-shot"]) {
    const image = $(selector);
    if (image) { image.hidden = true; image.removeAttribute("src"); }
  }
  const list = $("#model-file-summary");
  if (list) list.innerHTML = modelImportFiles.length
    ? modelImportFiles.map((file) => `<li><strong data-no-i18n>${esc(file.name)}</strong><span>${humanSize(file.size)}</span></li>`).join("")
    : `<li>${esc(error || "Aucun fichier choisi")}</li>`;
  const report = $("#model-import-report");
  if (report) report.innerHTML = `<li class="muted">${error ? esc(error) : "Prêt à analyser le fichier."}</li>`;
  if (error && files.length) toast(error, "error");
}

function modelImportOptionsChanged() {
  modelImportRevision += 1;
  modelImportCanImport = false;
  updateModelImportControls();
  for (const selector of ["#model-source-shot", "#model-result-shot"]) {
    const image = $(selector);
    if (image) { image.hidden = true; image.removeAttribute("src"); }
  }
  const report = $("#model-import-report");
  if (report && modelImportFiles.length) report.innerHTML = `<li class="muted">Options modifiées : relance l'analyse.</li>`;
}

function modelTargetChanged() {
  modelTargetRevision += 1;
  const image = $("#model-result-shot");
  if (image) { image.hidden = true; image.removeAttribute("src"); }
  updateModelImportControls();
}

function readModelFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result || "");
      const comma = encoded.indexOf(",");
      if (comma < 0) reject(new Error(`Lecture impossible : ${file.name}`));
      else resolve({ name: file.name, data: encoded.slice(comma + 1) });
    };
    reader.onerror = () => reject(new Error(`Lecture impossible : ${file.name}`));
    reader.onabort = () => reject(new Error("Lecture du modèle annulée."));
    reader.readAsDataURL(file);
  });
}

async function runModelImport(inject = false) {
  if (modelImportPending) return;
  if (!modelImportFiles.length) { toast("Choisis d'abord un fichier OBJ, glTF ou GLB.", "error"); return; }
  const target = $("#model-target")?.value || "";
  if (inject && (modelImportAnalysisRevision !== modelImportRevision || !modelImportCanImport)) {
    toast("Analyse d'abord le fichier et corrige les éventuels problèmes.", "error"); return;
  }
  if (inject && (!target || !assetValidation.valid("model", target)
      || !assetValidation.entry("model", target)?.injectable)) {
    toast("Choisis un modèle cible compatible et vérifié.", "error"); return;
  }
  const scale = Number($("#model-scale")?.value || 1);
  if (!Number.isFinite(scale) || scale < 0.001 || scale > 1000) {
    toast("Échelle attendue entre 0,001 et 1 000.", "error"); return;
  }
  const revision = modelImportRevision;
  const targetRevision = modelTargetRevision;
  const files = [...modelImportFiles];
  const axis = $("#model-axis")?.value || "keep";
  const fit = Boolean($("#model-fit")?.checked);
  modelImportPending = true;
  updateModelImportControls();
  status(inject ? "Injection en cours…" : "Analyse du modèle…", "busy");
  try {
    const encoded = [];
    for (const file of files) {
      encoded.push(await readModelFile(file));
      if (revision !== modelImportRevision || (inject && targetRevision !== modelTargetRevision)) {
        status("prêt");
        return;
      }
    }
    const result = await api(inject ? "/api/model/import" : "/api/model/inspect", {
      method: "POST", body: JSON.stringify({ files: encoded, axis, scale, fit, target }),
    });
    // La cible a pu être écrite même si le choix a changé pendant la requête.
    if (inject) toast(`Copie modifiée : ${result.wrote}`);
    const current = revision === modelImportRevision && (!inject || (
      targetRevision === modelTargetRevision && target === $("#model-target")?.value
      && fit === Boolean($("#model-fit")?.checked)));
    if (current) {
      if (!inject) {
        modelImportAnalysisRevision = revision;
        modelImportCanImport = result.can_import === true;
      }
      const shot = $(inject ? "#model-result-shot" : "#model-source-shot");
      if (shot) {
        shot.hidden = !result.preview;
        if (result.preview) shot.src = result.preview;
      }
      const report = $("#model-import-report");
      if (report) {
        const summary = inject
          ? `<li><span class="badge">écrit</span><span><code>${esc(result.wrote)}</code></span></li>`
            + `<li><span class="badge">${result.faces} f.</span><span>${result.vertices} sommet(s) sur ${result.capacity_vertices} disponibles</span></li>`
          : `<li><span class="badge">source</span><span><b data-no-i18n>${esc(result.file)}</b> · ${result.meshes} maillage(s) · ${result.vertices} sommet(s) · ${result.faces} triangle(s)</span></li>`;
        const notes = inject ? [...(result.report || []), ...(result.notes || [])] : (result.warnings || []);
        report.innerHTML = summary + notes.map((note) => `<li><span class="badge warn">note</span><span>${esc(note)}</span></li>`).join("");
      }
      if (!inject) toast(result.can_import ? "Analyse terminée : vérifie la cible avant l'injection." : "Analyse terminée : fichier non injectable en l'état.", result.can_import ? "ok" : "error");
    }
    if (inject) {
      try { await refresh(); }
      catch (error) { toast(`Copie écrite, mais actualisation impossible : ${error.message}`, "error"); }
    } else if (!current) status("prêt");
  } catch (error) {
    if (revision === modelImportRevision && (!inject || targetRevision === modelTargetRevision)) {
      if (!inject) modelImportCanImport = false;
      toast(error.message, "error");
    } else status("prêt");
  } finally {
    modelImportPending = false;
    updateModelImportControls();
  }
}

/* ------------------------------------------------------------------ réglages */
function settingsOrDefault() {
  return state.settings || {
    vram_gb: 6, vram_max_gb: 25, vram_min_gb: 1, supersample: 2,
    cull: true, frustum: true, adaptive: true, max_faces: null, lod: 0,
    accent: "azur", density: "confort", theme: "light", language: "fr", export_textures: true, thumb_faces: null,
    viewer_upscale: "native", fsr_quality: "quality", fsr_sharpness: 0.65, viewer_msaa: "4",
    viewer_dpr: "2", viewer_fps: "60", viewer_limit: "1600",
    accents: ["menthe", "azur", "ambre", "framboise"], densities: ["confort", "compact"],
  };
}

function applyUiSettings(settings = settingsOrDefault()) {
  document.documentElement.dataset.accent = settings.accent || "azur";
  document.documentElement.dataset.density = settings.density || "confort";
  document.documentElement.dataset.theme = settings.theme || "light";
  window.HookyI18n?.setLanguage(settings.language || "fr");
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
  $("#set-vram-out").textContent = `${settings.vram_gb} GiB`;
  $("#set-vram-fill").style.width =
    `${((settings.vram_gb - (settings.vram_min_gb ?? 1)) / ((settings.vram_max_gb ?? 25) - (settings.vram_min_gb ?? 1))) * 100}%`;
  $("#set-ss").value = settings.supersample;
  $("#set-ss-out").textContent = `×${settings.supersample}`;
  $("#set-cull").checked = settings.cull;
  $("#set-frustum").checked = settings.frustum;
  $("#set-adaptive").checked = settings.adaptive;
  $("#set-viewer-upscale").value = settings.viewer_upscale || "native";
  $("#set-fsr-quality").value = settings.fsr_quality || "quality";
  $("#set-fsr-sharp").value = settings.fsr_sharpness ?? 0.65;
  $("#set-fsr-sharp-out").textContent = `${Math.round((settings.fsr_sharpness ?? 0.65) * 100)} %`;
  $("#set-viewer-msaa").value = settings.viewer_msaa || "4";
  $("#set-viewer-dpr").value = settings.viewer_dpr || "2";
  $("#set-viewer-fps").value = settings.viewer_fps || "60";
  $("#set-viewer-limit").value = settings.viewer_limit || "1600";

  $("#set-theme").value = settings.theme || "light";
  $("#set-language").value = settings.language || "fr";
  const accents = $("#set-accent");
  accents.innerHTML = (settings.accents || ["menthe"]).map((accent) =>
    `<option value="${accent}" ${accent === settings.accent ? "selected" : ""}>${accent}</option>`).join("");
  const densities = $("#set-density");
  densities.innerHTML = (settings.densities || ["confort"]).map((density) =>
    `<option value="${density}" ${density === settings.density ? "selected" : ""}>${density}</option>`).join("");
  const cacheSlider = $("#set-cache");
  if (cacheSlider) {
    cacheSlider.max = settings.cache_max_mb || 1024;
    cacheSlider.value = settings.cache_mb ?? 256;
    $("#set-cache-out").textContent = `${settings.cache_mb ?? 256} MiB`;
  }
  $("#set-export-textures").checked = settings.export_textures !== false;
  $("#set-textured").checked = settings.textured_previews !== false;
  updateWarmStatus(state.warm);

  $("#settings-summary").innerHTML = [
    `<li><span class="k">Budget</span>${humanBytes(settings.budget_bytes
      || settings.vram_gb * 1024 ** 3)} (${settings.vram_gb} GiB)</li>`,
    `<li><span class="k">Anticrénelage</span>×${settings.supersample}</li>`,
    `<li><span class="k">Culling</span>${[
      settings.cull ? "faces arrière" : null,
      settings.frustum ? "hors écran" : null,
    ].filter(Boolean).join(" + ") || "désactivé"}</li>`,
    `<li><span class="k">Anticrénelage adaptatif</span>${settings.adaptive
      ? "actif" : "désactivé (refus net)"}</li>`,
    `<li><span class="k">Faces</span>toutes les faces décodées ou refus</li>`,
    `<li><span class="k">Export GLB / glTF / OBJ</span>${settings.export_textures !== false
      ? "avec les PNG" : "géométrie seule"}</li>`,
  ].join("");
}

let settingsTimer = null;
let settingsPending = {};
let settingsInFlight = {};
let settingsWaiters = [];
let settingsSending = false;
let settingsNotify = false;
let settingsIdleWaiters = [];
let settingsSaveFailed = false;

function saveSettings(patch, options = {}) {
  state.settings = { ...settingsOrDefault(), ...patch };
  applyUiSettings(state.settings);
  renderSettings();
  if (typeof applyViewerSettings === "function") applyViewerSettings();
  // Regrouper toutes les modifications rapprochées, pas seulement la dernière.
  Object.assign(settingsPending, patch);
  settingsNotify ||= !options.quiet;
  clearTimeout(settingsTimer);
  const saved = new Promise((resolve) => settingsWaiters.push(resolve));
  settingsTimer = setTimeout(() => {
    settingsTimer = null;
    flushSettings();
  }, 180);
  return saved;
}

async function flushSettings() {
  if (settingsSending || !Object.keys(settingsPending).length) return;
  const patch = settingsPending;
  const waiters = settingsWaiters;
  const notify = settingsNotify;
  settingsPending = {};
  settingsInFlight = patch;
  settingsWaiters = [];
  settingsNotify = false;
  settingsSending = true;
  let saved = false;
  try {
    const payload = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(patch),
    });
    // Une modification arrivée pendant l'écriture reste affichée et sera
    // envoyée ensuite ; une réponse plus ancienne qu'un scan ne l'annule pas.
    const obsolete = payload.image_revision
      && revisionOrder(payload.image_revision, state.project?.image_revision) === -1;
    state.settings = { ...(obsolete ? state.settings : payload.settings), ...settingsPending };
    if (state.project && payload.image_revision && !obsolete) {
      state.project.image_revision = payload.image_revision;
      state.project.settings = payload.settings;
      if (assetValidation.revision !== payload.image_revision) {
        if (state.selection) { state.selection.clear(); updateSelectionBar(); }
        assetValidation.setScope("view", [], payload.image_revision);
      }
    }
    renderSettings();
    renderStatus();
    if (state.view === "models") await renderModels();
    if (state.view === "textures") await renderTextures();
    if (state.view === "ensembles") await renderEnsembles();
    if (state.view === "sounds") await renderSounds();
    if (state.view === "import") requestChooserChecks();
    if (notify) toast("Réglages enregistrés");
    settingsSaveFailed = false;
    saved = true;
  } catch (error) { settingsSaveFailed = true; toast(error.message, "error"); }
  finally {
    settingsSending = false;
    settingsInFlight = {};
    waiters.forEach((resolve) => resolve(saved));
    if (Object.keys(settingsPending).length && !settingsTimer) flushSettings();
    settingsIdleWaiters.splice(0).forEach((resolve) => resolve());
  }
}

async function finishPendingSettings() {
  clearTimeout(settingsTimer);
  settingsTimer = null;
  while (settingsSending || Object.keys(settingsPending).length) {
    if (settingsSending) await new Promise((resolve) => settingsIdleWaiters.push(resolve));
    else await flushSettings();
  }
  return !settingsSaveFailed;
}

function wireSettings() {
  const on = (selector, event, handler) => {
    const node = $(selector);
    if (node) node[event] = handler;
  };
  on("#set-vram", "oninput", (event) => saveSettings({ vram_gb: Number(event.target.value) }));
  on("#set-ss", "oninput", (event) => saveSettings({ supersample: Number(event.target.value) }));
  on("#set-cull", "onchange", (event) => saveSettings({ cull: event.target.checked }));
  on("#set-frustum", "onchange", (event) => saveSettings({ frustum: event.target.checked }));
  on("#set-adaptive", "onchange", (event) => saveSettings({ adaptive: event.target.checked }));
  on("#set-viewer-upscale", "onchange", (event) => saveSettings({ viewer_upscale: event.target.value }));
  on("#set-fsr-quality", "onchange", (event) => saveSettings({ fsr_quality: event.target.value }));
  on("#set-fsr-sharp", "oninput", (event) => saveSettings({ fsr_sharpness: Number(event.target.value) }));
  on("#set-viewer-msaa", "onchange", (event) => saveSettings({ viewer_msaa: event.target.value }));
  on("#set-viewer-dpr", "onchange", (event) => saveSettings({ viewer_dpr: event.target.value }));
  on("#set-viewer-fps", "onchange", (event) => saveSettings({ viewer_fps: event.target.value }));
  on("#set-viewer-limit", "onchange", (event) => saveSettings({ viewer_limit: event.target.value }));
  on("#set-accent", "onchange", (event) => saveSettings({ accent: event.target.value }));
  on("#set-density", "onchange", (event) => saveSettings({ density: event.target.value }));
  on("#set-theme", "onchange", (event) => saveSettings({ theme: event.target.value }));
  on("#set-language", "onchange", (event) => saveSettings({ language: event.target.value }));
  on("#set-textured", "onchange", (event) => {
    saveSettings({ textured_previews: event.target.checked });
  });
  on("#set-export-textures", "onchange", (event) =>
    saveSettings({ export_textures: event.target.checked }));
  on("#set-cache", "oninput", (event) => {
    const value = Number(event.target.value);
    $("#set-cache-out").textContent = `${value} MiB`;
    saveSettings({ cache_mb: value });
  });
  on("#cache-clear", "onclick", clearCache);
  on("#scan-now", "onclick", rescanProject);
  on("#warm-start", "onclick", prepareThumbnails);
  on("#warm-stop", "onclick", stopPreparingThumbnails);
  on("#set-reset", "onclick", () => saveSettings({
    vram_gb: 6, supersample: 2, cull: true, frustum: true, adaptive: true,
    max_faces: null, lod: 0, accent: "azur", density: "confort", theme: "light", language: "fr",
    export_textures: true, thumb_faces: null, preview_faces: 0, cache_mb: 256,
    viewer_upscale: "native", fsr_quality: "quality", fsr_sharpness: 0.65,
    viewer_msaa: "4", viewer_dpr: "2", viewer_fps: "60", viewer_limit: "1600",
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

async function exportSelection() {
  const items = [...state.selection].map((key) => {
    const cut = key.indexOf(":");
    return { kind: key.slice(0, cut), rel: key.slice(cut + 1) };
  });
  if (!items.length) { toast("Rien de sélectionné", "error"); return; }
  status(`export de ${items.length} élément(s)…`, "busy");
  try {
    const result = await api("/api/export/selection", {
      method: "POST",
      body: JSON.stringify({ items, download_link: true }),
    });
    showExportResult(result, "ZIP de sélection prêt");
    toast(`${items.length} élément(s) préparés dans le ZIP`);

  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ inspecteur */
function disposeModelViewport(releaseContext = false) {
  const viewport = modelViewport || (releaseContext ? modelViewportForCleanup : null);
  if (viewport) viewport.dispose(releaseContext);
  modelViewport = null;
  if (releaseContext) modelViewportForCleanup = null;
}

function showInspector(title, html) {
  disposeModelViewport(true);
  inspectionSequence += 1;
  assetValidation.clearScope("inspector");
  const body = $("#inspector-body");
  thumbnailQueue.release(body);
  $("#inspector-title").textContent = title;
  body.innerHTML = html;
  body.scrollTop = 0;
  $("#inspector").hidden = false;
  $(".workspace").classList.add("with-inspector");
  thumbnailQueue.observe(body);
}

function hideInspector() {
  disposeModelViewport(true);
  inspectionSequence += 1;
  assetValidation.clearScope("inspector");
  thumbnailQueue.release($("#inspector-body"));
  $("#inspector").hidden = true;
  $(".workspace").classList.remove("with-inspector");
}

function markTile(kind, rel) {
  $$(`.tile[data-kind="${kind}"]`).forEach((node) => {
    node.classList.toggle("selected", node.dataset.rel === rel);
  });
}

/* ------------------------------------------------------------------ actions */
async function exportAllAnimations(rel) {
  const button = $("#btn-export-animations");
  const file = state.tab === `model:${rel}` ? $("#model-animation-file")?.value : "";
  const oldResult = $("#export-result");
  if (oldResult) oldResult.hidden = true;
  if (button) { button.disabled = true; button.textContent = "Export en cours…"; }
  try {
    const payload = { path: rel, textures: state.settings?.export_textures !== false, all_animations: true };
    if (file) payload.animation_file = file;
    const result = await api("/api/model/export", { method: "POST", body: JSON.stringify(payload) });
    const group = result.animations;
    const count = group.exported.length;
    showExportResult(result, group.partial ? "GLB prêt · export partiel" : `GLB prêt · ${count} clip(s)`);
    toast(group.partial
      ? `GLB partiel : ${count} clip(s) exporté(s). Consulte les omissions ci-dessous.`
      : `Un GLB prêt : ${count} clip(s), un cycle chacun.`, group.partial ? "error" : "ok");
    log("ok", `GLB → ${result.absolute_folder || result.folder}`);
    (result.warnings || []).forEach((warning) => log("warn", warning));
  } catch (error) {
    const message = String(error.message || "Export animé impossible.");
    showAnimationExportError(message);
    toast(message.split("\n")[0], "error");
    log("error", message);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Télécharger 1 GLB · toutes les animations détectées"; }
  }
}

async function exportModel(rel) {
  const button = $("#btn-export-model");
  const file = state.tab === `model:${rel}` ? $("#model-animation-file")?.value : "";
  const clip = state.tab === `model:${rel}` ? $("#model-animation-clip")?.value : "";
  if (file && (!clip || !/^\d+$/.test(clip))) {
    toast("Choisis un clip compatible ou reviens à la pose statique.", "error");
    return;
  }
  if (button) { button.disabled = true; button.textContent = "Export en cours…"; }
  try {
    const payload = { path: rel, textures: state.settings?.export_textures !== false };
    if (file) payload.animation = { path: file, index: Number(clip) };
    const result = await api("/api/model/export", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showExportResult(result, "Export GLB / glTF / OBJ prêt");
    toast(`Export prêt : ${result.files.length} fichier(s), ${result.faces.toLocaleString("fr-FR")} faces`);
    log("ok", `export → ${result.absolute_folder || result.folder}`);
    log("info", `fichiers : ${result.files.join(", ")}`);
    if (!result.textures) {
      const reason = state.settings?.export_textures === false
        ? "Export des textures désactivé dans les réglages."
        : "Aucune texture exportée : consulte les remarques pour les références manquantes ou illisibles.";
      log("warn", reason);
      toast(reason, "error");
    } else {
      // le rôle de chaque carte dit sur quelle entrée de shader la mettre
      const byRole = (result.maps || [])
        .map((map) => `${map.label} → ${map.file}`).join(" · ");
      log("ok", `${result.textures} carte(s) : ${byRole}`);
      log("info", "TEXTURES.md décrit les cartes et leurs éventuelles liaisons par matériau.");
      toast(`${result.textures} carte(s) de texture exportée(s)`);
    }
    (result.warnings || []).forEach((warning) => log("warn", warning));
  } catch (error) {
    toast(error.message, "error");
    log("error", error.message);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Exporter en GLB / glTF / OBJ"; }
  }
}

/* ------------------------------------------------------------------ vérificateur d'UV */
function uvShot(rel, flip, size = 512) {
  return `/api/model/uv?path=${encodeURIComponent(rel)}&size=${size}`
    + `&flip=${flip ? 1 : 0}` + imageRevisionQuery();
}

async function showUv(rel) {
  showInspector("UV", `<h3>Analyse des UV…</h3><p class="muted">${esc(rel)}</p>`);
  const request = inspectionSequence;
  let report;
  try {
    report = await api(`/api/model/uv/info?path=${encodeURIComponent(rel)}`);
  } catch (error) {
    if (request === inspectionSequence) {
      showInspector("UV", `<h3>Analyse indisponible</h3><p>${esc(error.message)}</p>`);
      toast(error.message, "error");
    }
    return;
  }
  if (request !== inspectionSequence) return;
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
    <h3 data-no-i18n>${esc(name)}</h3>
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

// Le dossier reste visible après les notifications ; le lien permet de
// retélécharger le fichier temporaire (ZIP classique ou GLB groupé autonome).
function showExportResult(result, title) {
  if (!result.download_url || !result.download_url.startsWith("/api/export/download?id=")) return;
  const panel = $("#export-result");
  const link = $("#export-download");
  if (!panel || !link) return;
  const glb = result.download_format === "glb";
  $("#export-result-title").textContent = title;
  $("#export-result-folder").textContent = result.absolute_folder || result.folder || "";
  const note = $("#export-result-note");
  if (note) note.textContent = glb
    ? "Un seul GLB est téléchargé. Le détail des clips et des omissions se trouve aussi dans RAPPORT_ANIMATIONS.txt du dossier."
    : result.temporary_archive
      ? "Archive temporaire : télécharge-la maintenant ; consulte les rapports de modèle inclus."
      : (result.stale_files || []).length
        ? "Ce dossier contient aussi d'anciens fichiers. Le ZIP contient uniquement cet export."
        : "Si le téléchargement ne démarre pas, utilise le bouton ou ouvre ce dossier.";
  const warningBox = $("#export-result-warnings");
  const warnings = Array.isArray(result.warnings) ? result.warnings.filter((w) => typeof w === "string") : [];
  const group = glb ? result.animations : null;
  const skipped = Array.isArray(group?.skipped) ? group.skipped : [];
  const missing = Array.isArray(group?.exported) ? group.exported.filter((item) => item.missing?.length) : [];
  if (warningBox) {
    const lines = warnings.slice(0, 12).map((warning) => `• ${warning}`);
    if (warnings.length > 12) lines.push(`Autres remarques dans le journal et ${glb ? "RAPPORT_ANIMATIONS.txt" : "RAPPORT_MODELE.txt"}.`);
    if (skipped.length) {
      lines.push(`Clips ou fichiers non exportés (${skipped.length}) :`);
      skipped.forEach((item) => lines.push(`• ${item.path}${item.index === null ? "" : ` · ${item.name} (index ${item.index})`} : ${item.reason}`));
    }
    if (missing.length) {
      lines.push(`Pistes sans os correspondant (${missing.length} clip(s)) :`);
      missing.forEach((item) => lines.push(`• ${item.path} · ${item.name} (index ${item.index}) : ${item.missing.join(", ")}`));
    }
    warningBox.hidden = !lines.length;
    warningBox.textContent = lines.length ? "À vérifier :\n" + lines.join("\n") : "";
  }
  link.href = result.download_url;
  link.download = result.download_name || (glb ? "hookycreator-animations.glb" : "hookycreator-modeles.zip");
  link.textContent = glb ? "Télécharger le GLB" : "Télécharger le ZIP";
  link.hidden = false;
  panel.hidden = false;
  if (typeof link.click === "function") link.click();
}

function showAnimationExportError(message) {
  const panel = $("#export-result"), link = $("#export-download"), warnings = $("#export-result-warnings");
  if (!panel || !link || !warnings) return;
  $("#export-result-title").textContent = "Export animé refusé";
  $("#export-result-folder").textContent = "—";
  $("#export-result-note").textContent = "Aucun nouveau GLB n'a été téléchargé.";
  warnings.textContent = message;
  warnings.hidden = false;
  link.hidden = true;
  panel.hidden = false;
}

async function startBulk() {
  const kinds = ["texture", "model"];
  if ($("#bulk-anim")?.checked) kinds.push("animation", "auxiliary");
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
    // Le suivi périodique ne doit pas annuler une vignette toutes les 400 ms.
    job = await api("/api/bulk/status", { background: true, cache: "no-store" });
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
  const incompleteScan = Boolean(report.scan_incomplete || report.scan_truncated || report.scan_errors);
  $("#bulk-label").textContent =
    `${report.ok} réussi(s) · ${report.skip} ignoré(s) · ${report.error} erreur(s) — `
    + `${report.textures} PNG, ${report.models} modèle(s)`
    + (incompleteScan ? " · scan incomplet" : "");
  $("#bulk-report").innerHTML = report.items.slice(0, 60).map((item) => {
    const flavor = item.status === "ok"
      ? `<span class="badge">ok</span>`
      : `<span class="badge warn">${item.status === "skip" ? "ignoré" : "erreur"}</span>`;
    const detail = item.status === "ok"
      ? (item.kind === "model" ? `${item.faces} faces · v${item.version}` : item.format)
      : (item.reason || "");
    return `<li>${flavor}<span><span data-no-i18n>${esc(item.label)}</span><br><small class="muted">${esc(detail)}</small></span></li>`;
  }).join("");
  if (incompleteScan) {
    toast(`Export partiel : ${report.ok} élément(s), scan incomplet`, "error");
  } else {
    toast(`Export terminé : ${report.ok} élément(s)`);
  }
  await refresh();
}

/* ------------------------------------------------------------------ menus */
const MENUS = {
  fichier: [
    { label: "Rafraîchir le projet", hint: "F5", run: () => refresh({ quiet: false }) },
    { label: "Rescanner les fichiers", run: () => rescanProject() },
    { label: "Exporter la sélection (ZIP)", run: () => exportSelection() },
    { sep: true },
    { label: "Exporter tout le projet", run: () => { showView("home"); startBulk(); } },
    { sep: true },
    { label: "Ouvrir le dossier de travail", run: () => toast(`Dossier : ${state.project?.work || "—"}`) },
    { sep: true },
    { label: "Quitter l'atelier", danger: true, run: quitApp },
  ],
  edition: [
    { label: "Palette de commandes", hint: "Ctrl+K", run: openPalette },
    { label: "Chercher dans la vue", hint: "Ctrl+F", run: focusSearch },
    { sep: true },
    { label: "Vider le journal", run: () => { state.logs = []; renderLogs(); } },
  ],
  modele: [
    { label: "Vue des modèles", hint: "Ctrl+2", run: () => showView("models") },
    { label: "Vue des ensembles (modèle + textures + animations)", run: () => showView("ensembles") },
    { label: "Exporter le modèle sélectionné en GLB / glTF / OBJ", run: () => {
      const rel = state.tabs.find((tab) => tab.id === state.tab && tab.doc?.type === "model")?.doc?.rel;
      if (rel) exportModel(rel); else toast("Ouvre d'abord un modèle", "error");
    } },
    { label: "Importer un modèle…", hint: "Ctrl+5", run: () => showView("import") },
  ],
  texture: [
    { label: "Vue des textures", hint: "Ctrl+3", run: () => showView("textures") },
    { label: "Exporter la texture sélectionnée en PNG", run: () => {
      const rel = state.tabs.find((tab) => tab.doc?.type === "texture")?.doc?.rel;
      if (!rel) { toast("Ouvre d'abord une texture", "error"); return; }
      api("/api/texture/export", { method: "POST", body: JSON.stringify({ path: rel, index: 0 }) })
        .then((result) => toast(`PNG écrit : ${result.file}`))
        .catch((error) => toast(error.message, "error"));
    } },
  ],
  import: [
    { label: "Importer un modèle", hint: "Ctrl+5", run: () => showView("import") },
    { label: "Analyser les fichiers choisis", run: () => runModelImport(false) },
    { label: "Injecter dans la copie de travail", run: () => runModelImport(true) },
  ],
  affichage: [
    { label: "Accueil", hint: "Ctrl+1", run: () => showView("home") },
    { label: "Espace modification", run: () => showView("edit") },
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
      "HookyCreator — atelier local de modèles et textures. Consulte la documentation pour les limites et licences.") },
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
    { label: "Espace modification", group: "vue", run: () => showView("edit") },
    { label: "Aller aux modèles", group: "vue", run: () => showView("models") },
    { label: "Aller à Player", group: "vue", run: () => showView("player") },
    { label: "Aller aux animations", group: "vue", run: () => showView("animations") },
    { label: "Aller aux textures", group: "vue", run: () => showView("textures") },
    { label: "Aller aux sons", group: "vue", run: () => showView("sounds") },
    { label: "Aller aux vidéos", group: "vue", run: () => showView("videos") },
    { label: "Importer un modèle", group: "vue", run: () => showView("import") },
    { label: "Aller aux réglages", group: "vue", run: () => showView("settings") },
    { label: "Rafraîchir le projet", group: "action", run: () => refresh({ quiet: false }) },
    { label: "Exporter tout le projet", group: "action", run: () => { showView("home"); startBulk(); } },
    { label: "Analyser les fichiers choisis", group: "import", run: () => runModelImport(false) },
    { label: "Injecter le modèle source", group: "import", run: () => runModelImport(true) },
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
  (state.project?.assets || []).filter((asset) => asset.kind === "model"
    && assetValidation.valid("model", asset.rel)).slice(0, 400)
    .forEach((asset) => commands.push({
      label: `Ouvrir le modèle ${asset.name}`, group: "modèle", hint: asset.rel,
      run: () => { showView("models"); openModel(asset.rel); },
    }));
  (state.project?.assets || []).filter((asset) => asset.kind === "texture"
    && assetValidation.valid("texture", asset.rel)).slice(0, 400)
    .forEach((asset) => commands.push({
      label: `Ouvrir la texture ${asset.name}`, group: "texture", hint: asset.rel,
      run: () => { showView("textures"); openTexture(asset.rel); },
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
  // Search both the displayed translation and the original command, with or
  // without accents. User-supplied asset paths are not translated.
  const searchable = (value) => String(value || "").normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const text = searchable(query).trim();
  const all = paletteCommands();
  paletteItems = (text
    ? all.filter((item) => searchable(`${item.label} ${tr(item.label)} ${tr(item.group || "")} ${item.hint || ""}`).includes(text))
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

/* ------------------------------------------------------------------ mémoire */
let warmTimer = null;

function updateWarmStatus(warm) {
  if (!warm) return;
  state.warm = warm;
  const start = $("#warm-start");
  const stop = $("#warm-stop");
  const label = $("#warm-status");
  if (start) start.disabled = Boolean(warm.running) || state.settings?.cache_mb === 0;
  if (stop) {
    stop.hidden = !warm.running;
    stop.disabled = Boolean(warm.stopping);
  }
  if (label) {
    if (warm.running) {
      label.textContent = `${warm.stopping ? "Arrêt en cours" : "Préparation"} : ${warm.done}/${warm.total}`;
    } else if (warm.cancelled) {
      label.textContent = `Préparation arrêtée : ${warm.done}/${warm.total}`;
    } else if (warm.total) {
      label.textContent = `Préparation terminée : ${warm.done}/${warm.total}`;
    } else {
      label.textContent = state.settings?.cache_mb === 0
        ? "Active le cache pour préparer les vignettes." : "Préparation non lancée.";
    }
    if (warm.failed) label.textContent += ` · ${warm.failed} échec(s)`;
    label.title = warm.error || "";
  }
  clearTimeout(warmTimer);
  warmTimer = warm.running ? setTimeout(pollWarmStatus, 1500) : null;
}

async function pollWarmStatus() {
  warmTimer = null;
  try {
    // Une simple lecture d'état ne doit pas interrompre les images en cours.
    const payload = await api("/api/thumbs/status", { background: true, cache: "no-store" });
    updateWarmStatus(payload.warm);
  } catch (error) {
    const label = $("#warm-status");
    if (label) label.textContent = `État de préparation indisponible : ${error.message}`;
    if (state.warm?.running) warmTimer = setTimeout(pollWarmStatus, 3000);
  }
}

async function prepareThumbnails() {
  const start = $("#warm-start");
  if (start) start.disabled = true;
  try {
    const payload = await api("/api/thumbs/warm", { method: "POST", body: "{}" });
    updateWarmStatus(payload.warm);
    if (!payload.warm?.total) toast("Aucun modèle à préparer.");
  } catch (error) {
    if (start) start.disabled = false;
    toast(error.message, "error");
  }
}

async function stopPreparingThumbnails() {
  try {
    const payload = await api("/api/thumbs/stop", { method: "POST", body: "{}" });
    updateWarmStatus(payload.warm);
  } catch (error) { toast(error.message, "error"); }
}

async function refreshSystem() {
  try {
    const payload = await api("/api/system", { background: true });
    state.system = payload.cache;
    state.engine = payload.engine;
    updateWarmStatus(payload.cache?.warm);
    renderMemory();
    renderEngine();
    renderStatus();
  } catch (error) {
    log("error", `mémoire indisponible : ${error.message}`);
  }
}

function renderEngine() {
  const list = $("#engine-list");
  const hint = $("#engine-hint");
  if (!list || !state.engine) return;
  const rust = state.engine.rust || {};
  const texture = state.engine.texture || {};
  const zstd = state.engine.zstd || {};
  const sarc = state.engine.sarc || {};
  const restbl = state.engine.restbl || {};
  const renderer = rust.available ? "Rust" : "Python (repli)";
  const geometry = rust.available ? "Rust si compatible, sinon Python" : "Python (repli)";
  list.innerHTML = `
    <li><span class="k">Moteur Rust</span><b>${rust.available ? "actif" : "inactif"}</b></li>
    <li><span class="k">Rendu des aperçus</span><b>${renderer}</b></li>
    <li><span class="k">Tables de formes BFRES 10</span><b>${geometry}</b></li>
    <li><span class="k">Indices et positions BFRES 10</span><b>${geometry}</b></li>
    <li><span class="k">Filtre des triangles BFRES 10</span><b>${geometry}</b></li>
    <li><span class="k">Disposition des textures</span><b>${esc(texture.deswizzle || "Python")}</b></li>
    <li><span class="k">Encodage BNTX pris en charge</span><b>${renderer}</b></li>
    <li><span class="k">Décodage BCn</span><b>${esc(texture.label || "inconnu")}</b></li>
    <li><span class="k">Décompression Zstandard</span><b>${esc(zstd.label || "inconnu")}</b></li>
    <li><span class="k">Lecture et reconstruction SARC</span><b>${esc(sarc.label || "inconnu")}</b></li>
    <li><span class="k">Lecture et reconstruction RESTBL</span><b>${esc(restbl.label || "inconnu")}</b></li>`;
  if (hint) {
    hint.textContent = [rust.available ? "" : rust.reason, texture.hint, zstd.hint]
      .filter(Boolean).join(" · ") || "Moteur natif chargé.";
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
  const request = ++projectRequestSequence;
  status("scan du projet…", "busy");
  try {
    const payload = await api("/api/scan", { method: "POST", body: "{}" });
    if (!acceptProjectSnapshot(payload.project, request)) { status("scan terminé"); return; }
    state.ensembles = [];
    state.shown.models = 48;
    state.shown.textures = 48;
    state.shown.ensembles = 48;
    state.shown.sounds = 48;
    state.shown.animations = 48;
    state.shown.videos = 48;
    renderStatus();
    if (state.view === "models") await renderModels();
    if (state.view === "ensembles") await renderEnsembles();
    if (state.view === "player") window.HookyPlayer?.refresh();
    if (state.view === "textures") await renderTextures();
    if (state.view === "animations") renderAnimations();
    if (state.view === "videos") renderVideos();
    if (state.view === "sounds") await renderSounds();
    if (state.view === "import") await renderModelImport();
    if (state.view === "home") renderHome();
    if (payload.scan.incomplete || payload.scan.truncated) {
      toast("Scan incomplet : consulte les détails à l'accueil.", "error");
    } else {
      toast(`${payload.scan.count} fichier(s) en ${payload.scan.scan_seconds} s`);
    }
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
  if (window.__HOOKY_DESKTOP__) {
    // La fenêtre native intercepte ce chemin : aucun accès IPC n'est accordé
    // au contenu distant, et la fermeture attend la sauvegarde confirmée.
    location.assign("/__hooky/quit");
    return;
  }
  if (!window.confirm(tr("Quitter l'atelier ? Le serveur local va s'arrêter."))) return;
  document.body.inert = true;
  let timeout;
  const saved = await Promise.race([
    saveUiCheckpoint(),
    new Promise((resolve) => { timeout = setTimeout(() => resolve(false), 5000); }),
  ]);
  clearTimeout(timeout);
  if (!saved && !window.confirm(tr(
    "La session ou les réglages n'ont pas pu être enregistrés. Quitter quand même ?"))) {
    document.body.inert = false;
    return;
  }
  try {
    await api("/api/shutdown", { method: "POST", body: "{}" });
  } catch (error) { /* le serveur peut couper avant la réponse */ }
  sessionOver();
  document.body.inert = false;
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
function revisionOrder(candidate, current) {
  if (!candidate || !current) return null;
  if (candidate === current) return 0;
  const a = String(candidate).match(/^(.*)-(\d+)$/);
  const b = String(current).match(/^(.*)-(\d+)$/);
  if (!a || !b || a[1] !== b[1]) return null;
  const left = Number(a[2]), right = Number(b[2]);
  return left < right ? -1 : left > right ? 1 : 0;
}

function acceptProjectSnapshot(project, request) {
  const previous = projectSnapshotRevision || state.project?.image_revision;
  const order = revisionOrder(project?.image_revision, previous);
  if (order === -1 || (order === null && request < appliedProjectRequest)) return false;
  appliedProjectRequest = Math.max(appliedProjectRequest, request);
  projectSnapshotRevision = project.image_revision || null;
  // Un réglage peut avoir avancé la révision d'image après ce scan. Garder ses
  // images et ses réglages tout en acceptant le nouvel inventaire du scan.
  const keepNewerImages = revisionOrder(project.image_revision, state.project?.image_revision) === -1;
  const incomingSettings = keepNewerImages ? state.settings : (project.settings || state.settings);
  state.project = keepNewerImages
    ? { ...project, image_revision: state.project.image_revision }
    : project;
  state.settings = { ...(incomingSettings || {}), ...settingsInFlight, ...settingsPending };
  if (assetValidation.revision !== state.project.image_revision) {
    if (state.selection) { state.selection.clear(); updateSelectionBar(); }
    assetValidation.setScope("view", [], state.project.image_revision);
  }
  return true;
}

async function refresh(options = {}) {
  const request = ++projectRequestSequence;
  const payload = await api(options.quick ? "/api/state?quick=1" : "/api/state");
  if (!acceptProjectSnapshot(payload.project, request)) return;
  state.ensembles = [];          // la liste dépend des fichiers : on la relit
  applyUiSettings(state.settings);
  renderStatus();
  if (state.view === "home") renderHome();
  if (state.view === "models") await renderModels();
  if (state.view === "ensembles") await renderEnsembles();
  if (state.view === "player") window.HookyPlayer?.refresh();
  if (state.view === "textures") await renderTextures();
  if (state.view === "animations") renderAnimations();
  if (state.view === "videos") renderVideos();
  if (state.view === "sounds") await renderSounds();
  if (state.view === "import") await renderModelImport();
  if (state.view === "settings") { renderSettings(); refreshSystem(); }
  if (options.quiet === false) {
    const counts = payload.project.assets.length;
    toast(`Projet rafraîchi : ${counts} fichier(s)`);
  }
}

/* ------------------------------------------------------------------ câblage */
function setThumbnailPause(paused) {
  thumbnailQueue.setPaused(paused);
  const button = $("#thumb-toggle");
  if (button) {
    button.textContent = paused ? "▶ Reprendre vignettes" : "⏸ Pause vignettes";
    button.setAttribute("aria-pressed", String(paused));
  }
  try { localStorage.setItem("hooky-thumbnails-paused", paused ? "1" : "0"); }
  catch (_) { /* stockage facultatif */ }
}

// Les cartes reçoivent le focus comme les autres commandes. Les flèches se
// déplacent selon leur position réelle, y compris quand la grille se réorganise.
function handleTileNavigation(event) {
  const tile = event.target.closest?.(".tile[tabindex]");
  if (!tile || event.target !== tile || event.altKey || event.ctrlKey || event.metaKey) return false;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    tile.click();
    return true;
  }
  const direction = {
    ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
  }[event.key];
  if (!direction) return false;
  const grid = tile.closest(".grid");
  if (!grid) return false;
  const rect = tile.getBoundingClientRect();
  const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
  let best = null, score = Infinity;
  for (const candidate of grid.querySelectorAll(".tile[tabindex]:not([hidden])")) {
    if (candidate === tile) continue;
    const box = candidate.getBoundingClientRect();
    const dx = box.left + box.width / 2 - x, dy = box.top + box.height / 2 - y;
    const along = dx * direction[0] + dy * direction[1];
    if (along <= 1) continue;
    const across = direction[0] ? Math.abs(dy) : Math.abs(dx);
    const distance = along + across * 2;
    if (distance < score) { best = candidate; score = distance; }
  }
  if (best) { event.preventDefault(); best.focus(); }
  return Boolean(best);
}

function wire() {
  window.HookyPlayer?.wire({ api, toast, getAssets: () => state.project?.assets || [],
    getSettings: settingsOrDefault, getRevision: () => state.project?.image_revision || "" });
  let paused = false;
  try { paused = localStorage.getItem("hooky-thumbnails-paused") === "1"; }
  catch (_) { /* stockage facultatif */ }
  setThumbnailPause(paused);
  $("#thumb-toggle").onclick = () => setThumbnailPause(!thumbnailQueue.paused);
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

  // taper dans la recherche ne doit pas reconstruire la grille à chaque touche :
  // sur un dump de 12 000 fichiers, l'affichage attend 120 ms de calme
  onInputDebounced("#search", (value) => {
    state.filters.models = value.trim().toLowerCase();
    state.shown.models = 48;
    renderModels();
  });
  onInputDebounced("#search-tex", (value) => {
    state.filters.textures = value.trim().toLowerCase();
    state.shown.textures = 48;
    renderTextures();
  });
  onInputDebounced("#search-ens", (value) => {
    state.filters.ensembles = value.trim().toLowerCase();
    state.shown.ensembles = 48;
    renderEnsembles();
  });
  onInputDebounced("#ensemble-animation-search", renderEnsembleAssociations);
  $("#ensemble-animation").onchange = (event) => {
    state.ensembleAnimation = event.target.value;
    renderEnsembleAssociations();
  };
  $("#ensemble-associations").onclick = (event) => {
    const button = event.target.closest("[data-ensemble-model], [data-ensemble-player]");
    if (!button || !state.ensembleAnimation) return;
    if (button.dataset.ensemblePlayer) openPlayer(button.dataset.ensemblePlayer, { animationRel: state.ensembleAnimation });
    else if (button.dataset.ensembleModel) void openModel(button.dataset.ensembleModel, { animationRel: state.ensembleAnimation });
  };
  onInputDebounced("#search-sound", (value) => {
    state.filters.sounds = value.trim().toLowerCase();
    state.shown.sounds = 48;
    renderSounds();
  });
  onInputDebounced("#search-animation", (value) => {
    state.filters.animations = value.trim().toLowerCase();
    state.shown.animations = 48;
    renderAnimations();
  });
  onInputDebounced("#search-video", (value) => {
    state.filters.videos = value.trim().toLowerCase();
    state.shown.videos = 48;
    renderVideos();
  });
  onInputDebounced("#model-target-search", () => {
    state.shown.chooser = 48;
    requestChooserChecks();
  });
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
  $("#sound-more-btn").onclick = () => {
    state.shown.sounds += 48;
    renderSounds();
  };
  $("#animation-more-btn").onclick = () => {
    state.shown.animations += 48;
    renderAnimations();
  };
  $("#video-more-btn").onclick = () => {
    state.shown.videos += 48;
    renderVideos();
  };
  $$("[data-validate-retry]").forEach((button) => {
    button.onclick = () => {
      assetValidation.retryErrors();
      if (state.view === "models") renderModels();
      if (state.view === "textures") renderTextures();
      if (state.view === "ensembles") renderEnsembles();
      if (state.view === "sounds") renderSounds();
      if (state.view === "import") requestChooserChecks();
    };
  });
  $("#model-import-validate-more").onclick = () => {
    state.shown.chooser += 48;
    requestChooserChecks();
  };
  $("#selection-export").onclick = () => exportSelection();
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
  $("#export-dismiss").onclick = () => { $("#export-result").hidden = true; };
  $("#native-download-dismiss").onclick = () => { $("#native-download-result").hidden = true; };
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
      if (tile.dataset.kind === "sound") openSound(tile.dataset.rel);
      if (tile.dataset.kind === "animation") openAnimation(tile.dataset.rel);
      if (tile.dataset.kind === "video") openVideo(tile.dataset.rel);
    }
  });

  $("#bulk-start").onclick = startBulk;

  // Import de fichiers standards. Les fichiers ne sont jamais stockés dans la session.
  $("#model-file-choose").onclick = () => $("#model-files").click();
  $("#model-files").onchange = (event) => {
    setModelImportFiles(event.target.files);
    event.target.value = ""; // autorise la nouvelle sélection du même fichier
  };
  $("#model-inspect").onclick = () => runModelImport(false);
  $("#model-inject").onclick = () => runModelImport(true);
  $("#model-axis").onchange = modelImportOptionsChanged;
  $("#model-scale").oninput = modelImportOptionsChanged;
  $("#model-fit").onchange = modelTargetChanged;
  $("#model-target").onchange = modelTargetChanged;
  updateModelImportControls();
  const drop = $("#model-drop");
  if (drop) {
    drop.ondragover = (event) => { event.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      setModelImportFiles(event.dataTransfer.files);
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
    if (handleTileNavigation(event)) return;
    const meta = event.ctrlKey || event.metaKey;
    if (meta && event.key.toLowerCase() === "k") { event.preventDefault(); openPalette(); return; }
    if (meta && event.key.toLowerCase() === "j") { event.preventDefault(); toggleConsole(); return; }
    if (meta && event.key.toLowerCase() === "f") {
      event.preventDefault();
      focusSearch();
      return;
    }
    if (meta && "123456".includes(event.key)) {
      event.preventDefault();
      const views = ["home", "models", "textures", "player", "import", "settings"];
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

/* ------------------------------------------------------------------ reprise automatique */
const SESSION_TEXT_FIELDS = ["search", "search-tex", "search-ens", "search-sound", "search-animation", "search-video", "tex-role",
  "model-axis", "model-scale",
  "model-target-search"];
const SESSION_CHECK_FIELDS = ["model-shots", "ens-textures", "bulk-anim", "model-fit"];
let sessionTouchedDuringBoot = false;

function applyRestoredChoices() {
  const desired = restoredChoices["model-target"];
  const select = $("#model-target");
  if (!desired || !select) return;
  if ([...select.options].some((option) => option.value === desired)) {
    select.value = desired;
    delete restoredChoices["model-target"];
  } else {
    // Ne pas injecter par accident dans le premier modèle en cours de validation.
    select.value = "";
  }
}

function captureUiSession() {
  const fields = {};
  for (const id of SESSION_TEXT_FIELDS) {
    const node = $(`#${id}`);
    if (node) fields[id] = String(node.value || "").slice(0, 2048);
  }
  for (const id of SESSION_CHECK_FIELDS) {
    const node = $(`#${id}`);
    if (node) fields[id] = Boolean(node.checked);
  }
  const quality = $("#model-quality")?.value || restoredModelQuality;
  if (quality) fields["model-quality"] = quality;
  const choices = {
    "model-target": restoredChoices["model-target"] || $("#model-target")?.value || "",
  };
  const tabs = state.tabs.slice(0, 64).map((tab) => {
    if (tab.view) return { type: "view", view: tab.view };
    return tab.doc ? { ...tab.doc } : null;
  }).filter(Boolean);
  const shown = {};
  for (const key of ["models", "textures", "ensembles", "sounds", "animations", "videos", "chooser"]) {
    shown[key] = Math.max(48, Math.min(10000, Math.round(state.shown[key] || 48)));
  }
  return {
    version: 1, view: state.view, tabs, active: state.tab,
    selection: [...state.selection].slice(0, 5000), fields, choices, shown,
    scroll: Math.max(0, Math.min(100000000, Math.round($("#views")?.scrollTop || 0))),
    inspector: $("#inspector")?.hidden === false,
    console: $("#console")?.hidden === false,
    reduced: document.documentElement.dataset.min === "1",
    thumbnails_paused: Boolean(thumbnailQueue.paused),
  };
}

async function restoreUiSession(saved) {
  if (!saved || saved.version !== 1 || !state.project) return;
  const assets = new Map((state.project.assets || []).map((asset) => [`${asset.kind}:${asset.rel}`, asset]));
  if (sessionTouchedDuringBoot) return;
  for (const id of [...SESSION_TEXT_FIELDS, ...SESSION_CHECK_FIELDS]) {
    const value = saved.fields?.[id];
    const node = $(`#${id}`);
    if (!node) continue;
    if (SESSION_CHECK_FIELDS.includes(id) && typeof value === "boolean") node.checked = value;
    else if (SESSION_TEXT_FIELDS.includes(id) && typeof value === "string") node.value = value;
  }
  restoredModelQuality = ["auto", "detail", "rapide"].includes(saved.fields?.["model-quality"])
    ? saved.fields["model-quality"] : "";
  for (const [kind, id] of Object.entries({ models: "search", textures: "search-tex",
                                            ensembles: "search-ens", sounds: "search-sound", animations: "search-animation", videos: "search-video" })) {
    state.filters[kind] = ($(`#${id}`)?.value || "").trim().toLowerCase();
  }
  for (const key of ["models", "textures", "ensembles", "sounds", "animations", "videos", "chooser"]) {
    const value = saved.shown?.[key];
    if (Number.isInteger(value)) state.shown[key] = Math.max(48, Math.min(10000, value));
  }
  state.selection = new Set((saved.selection || []).filter((key) => assets.has(key)
    && (key.startsWith("model:") || key.startsWith("texture:"))));
  const choices = saved.choices || {};
  restoredChoices = {};
  if (assets.has(`model:${choices["model-target"]}`)) restoredChoices["model-target"] = choices["model-target"];

  state.tabs = [];
  ensureViewTab("home", VIEW_LABELS.home).closable = false;
  for (const tab of saved.tabs || []) {
    if (tab.type === "view" && Object.hasOwn(VIEW_LABELS, tab.view)) {
      ensureViewTab(tab.view, VIEW_LABELS[tab.view]);
    } else if (["model", "texture", "sound", "animation", "video"].includes(tab.type)) {
      const asset = assets.get(`${tab.type}:${tab.rel}`);
      const index = tab.type === "texture" && Number.isInteger(tab.index) && tab.index >= 0 ? tab.index : 0;
      if (asset) ensureTab({ id: `${tab.type}:${tab.rel}${index ? `#${index}` : ""}`,
                             kind: tab.type, label: asset.name,
                             doc: { type: tab.type, rel: tab.rel, ...(tab.type === "texture" ? { index } : {}) } });
    }
  }
  const view = Object.hasOwn(VIEW_LABELS, saved.view) ? saved.view : "home";
  ensureViewTab(view, VIEW_LABELS[view]);
  showView(view, { tab: false });
  const active = state.tabs.find((tab) => tab.id === saved.active)
    || state.tabs.find((tab) => tab.id === `view:${view}`);
  state.tab = active.id;
  renderTabs();
  updateSelectionBar();
  toggleConsole(Boolean(saved.console));
  if (saved.reduced) document.documentElement.dataset.min = "1";
  else delete document.documentElement.dataset.min;
  setThumbnailPause(Boolean(saved.thumbnails_paused));
  applyRestoredChoices();
  if (saved.inspector && active.doc) void openDoc(active.doc);
  // Les grilles synchrones sont en place à la prochaine image. Ne pas voler
  // le défilement si l'utilisateur a déjà commencé à interagir.
  const scroll = $("#views");
  if (scroll && saved.scroll) requestAnimationFrame(() => {
    if (!sessionTouchedDuringBoot) scroll.scrollTop = saved.scroll;
  });
}

async function saveUiCheckpoint() {
  try {
    // Ne pas remplacer une sauvegarde existante avec l'interface encore en
    // cours de chargement. Les réglages terminent avant la capture finale.
    await uiSessionBoot;
    const settingsSaved = await finishPendingSettings();
    const sessionSaved = uiSessionWriter?.enabled
      ? await uiSessionWriter.flush() : !sessionTouchedDuringBoot;
    return settingsSaved && sessionSaved;
  } catch (error) {
    toast(`Sauvegarde de session impossible : ${error.message}`, "error");
    return false;
  }
}

function wireUiSession() {
  uiSessionWriter = new UiSessionWriter(captureUiSession, {
    onError: (error) => toast(`Sauvegarde de session impossible : ${error.message}`, "error"),
  });
  const schedule = (event) => {
    // Un scroll provoqué par le rendu ou par scrollTop peut lui aussi être
    // « trusted » ; seuls les gestes réels annulent la restauration initiale.
    if (event?.isTrusted && event.type !== "scroll") {
      sessionTouchedDuringBoot = true;
      if (sessionLoadFailed && state.project && !uiSessionWriter.enabled) {
        uiSessionWriter.enable();
        sessionLoadFailed = false;
      }
    }
    if (event?.type === "change" && event.target?.id === "model-target") {
      delete restoredChoices[event.target.id];
    }
    uiSessionWriter.schedule(event?.type === "input" || event?.type === "scroll" ? 250 : 100);
  };
  for (const type of ["click", "input", "change", "keydown", "wheel", "touchstart"]) {
    document.addEventListener(type, schedule, type === "wheel" || type === "touchstart" ? { passive: true } : undefined);
  }
  $("#views")?.addEventListener("scroll", schedule, { passive: true });
  const observer = new MutationObserver(() => uiSessionWriter.schedule());
  for (const selector of ["#tabs", "#inspector", "#console", "#selection-count"]) {
    const node = $(selector);
    if (node) observer.observe(node, { childList: true, subtree: true, attributes: true,
                                      attributeFilter: ["hidden"] });
  }
  // La fenêtre native intercepte cette navigation sans ouvrir de page ni
  // exposer d'API Tauri. Elle attend la confirmation avant d'arrêter le moteur.
  window.__hookyCheckpoint = async (id) => {
    const serial = ++nativeCheckpointSerial;
    document.body.inert = true;
    status("enregistrement de la session…", "busy");
    const saved = await saveUiCheckpoint();
    if (serial === nativeCheckpointSerial) {
      location.assign(`/__hooky/checkpoint?id=${encodeURIComponent(id)}&saved=${saved ? 1 : 0}`);
    }
  };
  window.__hookyResume = () => {
    nativeCheckpointSerial += 1;
    document.body.inert = false;
    status("prêt");
  };
  const checkpoint = (keepalive) => {
    void uiSessionWriter.flush({ keepalive });
    void finishPendingSettings();
  };
  window.addEventListener("pagehide", () => checkpoint(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") checkpoint(false);
  });
}

async function boot() {
  if (window.__HOOKY_DESKTOP__) {
    document.documentElement.dataset.desktop = "1";
    window.addEventListener("hooky:status", (event) => {
      if (event.detail) toast(String(event.detail));
    });
    window.addEventListener("hooky:download", (event) => {
      const detail = event.detail || {};
      const title = detail.status === "ok" ? "Téléchargement terminé"
        : detail.status === "started" ? "Téléchargement en cours" : "Téléchargement impossible";
      $("#native-download-title").textContent = title;
      $("#native-download-path").textContent = String(detail.path || detail.message || "");
      $("#native-download-result").hidden = false;
      if (detail.status !== "started") {
        toast(`${title} : ${detail.path || detail.message || ""}`, detail.status === "ok" ? "ok" : "error");
      }
    });
  }
  applyUiSettings();
  ensureViewTab("home", "Accueil");
  state.tabs[0].closable = false;
  wire();
  wireUiSession();
  renderTabs();
  showView("home");
  try {
    // Charger la sauvegarde en parallèle de l'inventaire ; les statistiques
    // détaillées des caches sont demandées après le premier affichage.
    const saved = api("/api/ui-state", { background: true })
      .then((payload) => ({ payload }), (error) => ({ error }));
    await refresh({ quick: true });
    const result = await saved;
    if (result.error) {
      // Éviter d'écraser une sauvegarde qu'on n'a pas réussi à lire. Dès que
      // l'utilisateur modifie l'interface, les écritures sont réessayées.
      sessionLoadFailed = true;
      toast(`Reprise automatique indisponible : ${result.error.message}`, "error");
      if (sessionTouchedDuringBoot) {
        uiSessionWriter.enable();
        sessionLoadFailed = false;
        uiSessionWriter.schedule();
      }
    } else {
      if (!sessionTouchedDuringBoot) await restoreUiSession(result.payload.state);
      uiSessionWriter.enable();
      if (sessionTouchedDuringBoot) uiSessionWriter.schedule();
    }
    log("ok", `Projet ouvert : ${state.project.root}`);
    refreshSystem();
    if (state.project.scan_incomplete || state.project.scan_truncated) {
      toast("Scan incomplet : consulte les détails à l'accueil.", "error");
    } else {
      status("prêt");
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

uiSessionBoot = boot();
