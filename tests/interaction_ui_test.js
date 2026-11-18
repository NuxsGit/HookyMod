"use strict";

// Vérifie les actions asynchrones réelles sans dépendance npm ni navigateur.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../hookycreator/web/app.js"), "utf8");
function section(from, to) {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `${from} introuvable`);
  return source.slice(start, end);
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
function elements() {
  const nodes = new Map();
  const $ = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: "", innerHTML: "", textContent: "", style: {}, files: [],
      setAttribute() {}, removeAttribute() {}, classList: { toggle() {} },
    });
    return nodes.get(selector);
  };
  return { $, nodes };
}

async function passivePollingDoesNotInterruptThumbnails() {
  const busyCalls = [], requests = [];
  const ctx = vm.createContext({
    busy: (active) => busyCalls.push(active),
    fetch: async (url, options) => {
      requests.push([url, options]);
      return { ok: true, headers: { get: () => "application/json" },
               json: async () => ({ value: 1 }) };
    },
  });
  vm.runInContext(section("async function api(", "/* ------------------------------------------------------------------ onglets */"), ctx);
  await vm.runInContext('api("/api/status", { background: true })', ctx);
  assert.deepEqual(busyCalls, [], "un sondage ne suspend pas les vignettes");
  assert.equal(requests[0][1].background, undefined, "option interne non transmise à fetch");
  await vm.runInContext('api("/api/interactive")', ctx);
  assert.deepEqual(busyCalls, [true, false], "une action garde la priorité");

  const { $ } = elements();
  let options;
  const bulk = vm.createContext({ $, console, setTimeout,
    api: async (_path, opts) => { options = opts; return { running: false }; },
    toast() {}, refresh: async () => {}, esc: String,
  });
  vm.runInContext(section("async function pollBulk()", "async function buildMod("), bulk);
  await vm.runInContext("pollBulk()", bulk);
  assert.equal(options?.background, true, "le suivi de l'export est passif");
}

async function incompleteScanIsVisibleInStatusAndExport() {
  const { $ } = elements();
  const state = { project: { root: "dump", assets: [], mobs: [], edited: [],
                             scan_truncated: true, scan_limit: 2 }, settings: { vram_gb: 6 } };
  const ctx = vm.createContext({ $, state, console, humanBytes: String, esc: String });
  vm.runInContext(section("function renderStatus()", "/* ------------------------------------------------------------------ accueil */"), ctx);
  vm.runInContext("renderStatus()", ctx);
  assert.equal($("#scan-warning").hidden, false);
  assert.match($("#scan-notice").textContent, /--scan-limit 0/);
  state.project.scan_truncated = false;
  state.project.scan_incomplete = true;
  state.project.scan_errors = 1;
  state.project.scan_error_examples = ["Interdit : accès refusé"];
  vm.runInContext("renderStatus()", ctx);
  assert.equal($("#scan-warning").hidden, false);
  assert.match($("#scan-notice").textContent, /Interdit : accès refusé/);
  assert.doesNotMatch($("#scan-notice").textContent, /--scan-limit/);
  state.project.scan_incomplete = false;
  state.project.scan_errors = 0;
  vm.runInContext("renderStatus()", ctx);
  assert.equal($("#scan-warning").hidden, true);
  assert.equal($("#scan-notice").hidden, true);

  const toasts = [];
  const report = { ok: 1, skip: 0, error: 0, textures: 0, models: 1, items: [],
                   scan_truncated: true };
  const bulk = vm.createContext({ $, console, setTimeout,
    api: async () => ({ running: false, done: 1, total: 1, report }),
    toast: (...args) => toasts.push(args), refresh: async () => {}, esc: String,
  });
  vm.runInContext(section("async function pollBulk()", "async function buildMod("), bulk);
  await vm.runInContext("pollBulk()", bulk);
  assert.match($("#bulk-label").textContent, /scan incomplet/i);
  assert.ok(toasts.some(([text, kind]) => /scan incomplet/i.test(text) && kind === "error"));
}

async function delayedSearchDoesNotRebuildHiddenViews() {
  const { $ } = elements();
  const work = [];
  const state = { view: "home", project: { assets: [] }, ensembles: [],
    filters: { models: "", textures: "", ensembles: "" },
    shown: { models: 48, textures: 48, ensembles: 48 } };
  const ctx = vm.createContext({ $, state, console,
    thumbnailQueue: { release: () => work.push("release"), observe: () => work.push("observe") },
    api: async () => { work.push("api"); return { ensembles: [] }; },
    renderBlenderState() {}, updateMore() {}, updateSelectionBar() {}, toast() {},
    matches: () => true, ensembleMatches: () => true, tileMarkup: () => "tile",
    ROLE_NAMES: {},
  });
  vm.runInContext(section("async function renderEnsembles()", "async function renderModels()") +
    section("async function renderModels()", "function onInputDebounced(") +
    section("async function renderTextures()", "async function openTexture("), ctx);
  await vm.runInContext("renderModels()", ctx);
  await vm.runInContext("renderTextures()", ctx);
  await vm.runInContext("renderEnsembles()", ctx);
  assert.deepEqual(work, [], "une recherche différée ne recharge pas une vue quittée");
}

async function ensembleListIsLoadedOnlyOnceWhileFiltering() {
  const { $ } = elements();
  const requests = [], work = [];
  const state = { view: "ensembles", project: { image_revision: "one" }, ensembles: [],
    filters: { ensembles: "" }, shown: { ensembles: 48 } };
  const ctx = vm.createContext({ $, state, console, esc: String, humanSize: String,
    ensembleMatches: () => true, ensembleThumb: () => "/image", renderEnsembleAssociations() {}, renderBlenderState() {}, toast() {},
    assetValidation: { setScope() {} }, updateValidatedAssets() {},
    thumbnailQueue: { release: () => work.push("release"), observe: () => work.push("observe") },
    api: () => new Promise((resolve) => requests.push(resolve)),
  });
  vm.runInContext("let ensembleLoad = null, ensembleRenderSequence = 0;\n" +
    section("async function renderEnsembles()", "async function renderModels()"), ctx);
  const first = vm.runInContext("renderEnsembles()", ctx);
  const second = vm.runInContext("renderEnsembles()", ctx);
  assert.equal(requests.length, 1, "une liste en cours de lecture est partagée");
  requests[0]({ ensembles: [{ rel: "Model/A.bfres", name: "A", size: 1,
                               textures: 0, animations: 0, complete: false }] });
  await Promise.all([first, second]);
  assert.equal(state.ensembles[0].rel, "Model/A.bfres");
  assert.deepEqual(work, ["release", "observe"], "seul le dernier affichage reconstruit la grille");
}

async function olderStateResponsesCannotUndoANewerScan() {
  const { $ } = elements();
  const requests = [];
  const state = { view: "home", project: { image_revision: "session-1", assets: [{ rel: "A" }] },
    settings: { cache_mb: 256 }, ensembles: [], shown: { models: 48, textures: 48, ensembles: 48 } };
  const ctx = vm.createContext({ $, state, console, status() {}, renderStatus() {}, renderHome() {},
    assetValidation: { revision: "session-1", setScope(_name, _items, revision) { this.revision = revision; } },
    renderModels: async () => {}, renderTextures: async () => {}, renderEnsembles: async () => {},
    renderMobs: async () => {}, renderBlends: async () => {}, renderSettings() {},
    refreshSystem() {}, refreshBlender() {}, applyUiSettings() {}, toast() {},
    api: (url) => new Promise((resolve) => requests.push({ url, resolve })),
  });
  vm.runInContext('let projectSnapshotRevision = "session-1", projectRequestSequence = 0, ' +
    'appliedProjectRequest = 0, settingsPending = {}, settingsInFlight = {};\n' +
    section("function revisionOrder(", "async function refresh(") +
    section("async function refresh(", "/* ------------------------------------------------------------------ câblage */") +
    section("async function rescanProject()", "/* ------------------------------------------------------------------ fenêtre */"), ctx);
  const project = (revision, assets, cache_mb = 256) =>
    ({ image_revision: `session-${revision}`, assets: assets.map((rel) => ({ rel })),
       settings: { cache_mb } });
  const scan = { count: 2, scan_seconds: 0.01 };
  const oldRefresh = vm.runInContext("refresh()", ctx);
  const newScan = vm.runInContext("rescanProject()", ctx);
  requests[1].resolve({ scan, project: project(2, ["A", "B"]) });
  await newScan;
  requests[0].resolve({ project: project(1, ["A"]) });
  await oldRefresh;
  assert.deepEqual(state.project.assets.map((asset) => asset.rel), ["A", "B"]);
  assert.equal(state.project.image_revision, "session-2");

  // Le scan peut commencer avant un rafraîchissement qui lit encore l'ancien
  // état : sa réponse plus récente reste acceptée même si sa requête est plus vieille.
  const earlierScan = vm.runInContext("rescanProject()", ctx);
  const laterRefresh = vm.runInContext("refresh()", ctx);
  requests[3].resolve({ project: project(2, ["A", "B"]) });
  await laterRefresh;
  requests[2].resolve({ scan, project: project(3, ["A", "B", "C"]) });
  await earlierScan;
  assert.equal(state.project.assets.length, 3);
  assert.equal(state.project.image_revision, "session-3");

  // Un réglage peut avancer la révision d'images pendant la réponse du scan,
  // sans que la nouvelle liste d'assets doive être rejetée ou ce réglage perdu.
  const pendingScan = vm.runInContext("rescanProject()", ctx);
  state.project.image_revision = "session-5";
  state.settings.cache_mb = 32;
  requests[4].resolve({ scan, project: project(4, ["A", "B", "C", "D"], 256) });
  await pendingScan;
  assert.equal(state.project.assets.length, 4);
  assert.equal(state.project.image_revision, "session-5");
  assert.equal(state.settings.cache_mb, 32);

  // Les saisies en cours ou en attente restent visibles pendant un refresh.
  vm.runInContext("settingsInFlight = {cache_mb: 40}; settingsPending = {preview_faces: 120000}", ctx);
  const pendingRefresh = vm.runInContext("refresh()", ctx);
  requests[5].resolve({ project: project(5, ["A", "B", "C", "D"], 32) });
  await pendingRefresh;
  assert.equal(state.settings.cache_mb, 40);
  assert.equal(state.settings.preview_faces, 120000);
}

async function rescanningUpdatesEnsemblesAndRejectsAnOldList() {
  const { $ } = elements();
  let renderCount = 0;
  const state = { view: "ensembles", settings: {}, ensembles: [],
                  shown: { models: 48, textures: 48, ensembles: 48 } };
  const ctx = vm.createContext({ $, state, status() {}, renderStatus() {}, renderHome() {},
    assetValidation: { revision: null, setScope(_name, _items, revision) { this.revision = revision; } },
    renderModels: async () => {}, renderTextures: async () => {},
    renderEnsembles: async () => { renderCount += 1; }, toast() {},
    api: async () => ({ project: { image_revision: "new", settings: {} },
                        scan: { count: 2, scan_seconds: 0.1 } }),
  });
  vm.runInContext("let projectSnapshotRevision = null, projectRequestSequence = 0, " +
    "appliedProjectRequest = 0, settingsPending = {}, settingsInFlight = {};\n" +
    section("function revisionOrder(", "async function refresh(") +
    section("async function rescanProject()", "/* ------------------------------------------------------------------ fenêtre */"), ctx);
  await vm.runInContext("rescanProject()", ctx);
  assert.equal(renderCount, 1, "la vue Ensembles est mise à jour après le scan");

  let resolve;
  const staleState = { view: "ensembles", project: { image_revision: "old" }, ensembles: [] };
  const stale = vm.createContext({ $, state: staleState, console,
    api: () => new Promise((done) => { resolve = done; }), renderBlenderState() {},
    renderEnsembleAssociations() {}, toast() {},
  });
  vm.runInContext("let ensembleLoad = null, ensembleRenderSequence = 0;\n" +
    section("async function renderEnsembles()", "async function renderModels()"), stale);
  const pending = vm.runInContext("renderEnsembles()", stale);
  staleState.project = { image_revision: "new" };
  staleState.view = "home";
  resolve({ ensembles: [{ rel: "old.bfres" }] });
  await pending;
  assert.deepEqual(staleState.ensembles, [], "la réponse d'avant scan ne remplit pas le nouveau projet");
}

async function uvDoesNotStealTheInspector() {
  const { $ } = elements();
  const requests = [], panels = [], toasts = [];
  const state = { settings: {} };
  let ctx;
  ctx = vm.createContext({ $, state, console, esc: String, uvShot: () => "/uv",
    showInspector: (title, html) => { panels.push({ title, html }); vm.runInContext("inspectionSequence++", ctx); },
    api: (url) => new Promise((resolve, reject) => requests.push({ url, resolve, reject })),
    toast: (...args) => toasts.push(args), log() {}, navigator: {}, openModel() {},
  });
  vm.runInContext("let inspectionSequence = 0;\n" +
    section("async function showUv(rel)", "function showExportResult("), ctx);
  const report = { has_uvs: false, warnings: [], conventions: { gltf: "ok", obj: "ok" } };
  const uv = vm.runInContext('showUv("Model/A.bfres")', ctx);
  vm.runInContext('showInspector("Texture", "nouvelle fiche")', ctx);
  requests.shift().resolve(report);
  await uv;
  assert.equal(panels.at(-1).title, "Texture", "un ancien résultat UV ne remplace pas la fiche choisie");

  const uvSuccess = vm.runInContext('showUv("Model/B.bfres")', ctx);
  requests.shift().resolve(report);
  await uvSuccess;
  assert.equal(panels.at(-1).title, "UV");
  assert.match(panels.at(-1).html, /aucune coordonnée/);
  assert.equal(typeof $("#uv-flip").onclick, "function");

  const staleFailure = vm.runInContext('showUv("Model/C.bfres")', ctx);
  vm.runInContext('showInspector("Texture", "autre fiche")', ctx);
  requests.shift().reject(new Error("ancien échec"));
  await staleFailure;
  assert.equal(panels.at(-1).title, "Texture");
  assert.equal(toasts.some(([text]) => text === "ancien échec"), false);


}

async function importDoesNotReopenAnOldInspector() {
  const { $ } = elements();
  const panels = [], toasts = [];
  let resolveImport, infos = 0, ctx;
  const info = { revision: "one", version: "1", orin: false, count: 1,
    textures: [{ index: 0, name: "texture", editable: true, format: "RGBA", width: 2, height: 2, mips: 1 }] };
  class Reader {
    readAsDataURL(file) {
      this.result = "data:image/png;base64,AAAA";
      queueMicrotask(() => file.fail ? this.onerror() : this.onload());
    }
  }
  ctx = vm.createContext({ $, console, FileReader: Reader, state: { tabs: [], tab: "" },
    esc: String, imageRevisionQuery: () => "", ensureTab() {}, renderTabs() {}, markTile() {},
    showInspector: (title, html) => { panels.push({ title, html }); vm.runInContext("inspectionSequence++", ctx); },
    toast: (...args) => toasts.push(args), refresh: async () => {},
    api: async (url) => {
      if (url.startsWith("/api/texture/info")) { infos += 1; return info; }
      if (url === "/api/texture/import") return new Promise((resolve) => { resolveImport = resolve; });
      throw new Error(`requête inattendue ${url}`);
    },
  });
  vm.runInContext("let inspectionSequence = 0;\n" +
    section("async function openTexture(rel, index = 0)", "/* ------------------------------------------------------------------ sons"), ctx);
  await vm.runInContext('openTexture("Tex/A.bntx")', ctx);
  $("#file-import").files = [{ name: "image.png" }];
  const imported = $("#file-import").onchange();
  await tick();
  vm.runInContext('showInspector("Modèle", "autre fiche")', ctx);
  resolveImport({ texture: "texture", wrote: "work/assets/Tex/A.bntx" });
  await imported;
  await tick();
  assert.equal(infos, 1, "pas de réouverture de l'ancienne texture");
  assert.equal(panels.at(-1).title, "Modèle");
  assert.ok(toasts.some(([text]) => String(text).includes("Texture remplacée")));

  await vm.runInContext('openTexture("Tex/A.bntx")', ctx);
  $("#file-import").files = [{ name: "illisible.png", fail: true }];
  await $("#file-import").onchange();
  assert.ok(toasts.some(([text]) => String(text).includes("Impossible de lire l'image")));
}

async function modelImportRejectsStaleAnalysisAndTargetResponses() {
  const { $ } = elements();
  const requests = [], toasts = [];
  class Reader {
    readAsDataURL() { this.result = "data:application/octet-stream;base64,AAAA"; queueMicrotask(() => this.onload()); }
  }
  const ctx = vm.createContext({ $, console, FileReader: Reader, queueMicrotask,
    esc: String, humanSize: String, status() {}, refresh: async () => {},
    toast: (...args) => toasts.push(args),
    assetValidation: { valid: () => true, entry: () => ({ injectable: true }) },
    api: (url, options) => new Promise((resolve, reject) =>
      requests.push({ url, payload: JSON.parse(options.body), resolve, reject })),
  });
  $("#model-target").value = "Model/A.bfres";
  $("#model-scale").value = "1";
  $("#model-axis").value = "keep";
  ctx.file = { name: "triangle.obj", size: 100 };
  vm.runInContext(section("let modelImportFiles = []", "/* ------------------------------------------------------------------ réglages */"), ctx);
  vm.runInContext("setModelImportFiles([file])", ctx);
  assert.equal($("#model-inject").disabled, true);
  const first = vm.runInContext("runModelImport(false)", ctx);
  await tick();
  assert.equal(requests[0].url, "/api/model/inspect");
  $("#model-scale").value = "2";
  vm.runInContext("modelImportOptionsChanged()", ctx);
  requests[0].resolve({ file: "ancien.obj", meshes: 1, vertices: 3, faces: 1,
                        can_import: true, warnings: [], preview: "" });
  await first;
  assert.equal($("#model-inject").disabled, true);
  assert.match($("#model-import-report").innerHTML, /Options modifiées/);
  assert.doesNotMatch($("#model-import-report").innerHTML, /ancien.obj/);

  const current = vm.runInContext("runModelImport(false)", ctx);
  await tick();
  requests[1].resolve({ file: "triangle.obj", meshes: 1, vertices: 3, faces: 1,
                        can_import: true, warnings: [], preview: "" });
  await current;
  assert.equal($("#model-inject").disabled, false);
  const injecting = vm.runInContext("runModelImport(true)", ctx);
  await tick();
  assert.equal(requests[2].url, "/api/model/import");
  assert.equal(requests[2].payload.target, "Model/A.bfres");
  $("#model-target").value = "Model/B.bfres";
  vm.runInContext("modelTargetChanged()", ctx);
  requests[2].resolve({ wrote: "Model/A.bfres", faces: 1, vertices: 3,
                        capacity_vertices: 3, report: [], notes: [], preview: "" });
  await injecting;
  assert.doesNotMatch($("#model-import-report").innerHTML, /écrit/);
  assert.ok(toasts.some(([text]) => String(text).includes("Model/A.bfres")));
}

function exportWarningsRemainVisible() {
  const { $ } = elements();
  const ctx = vm.createContext({ $, console });
  vm.runInContext(section("function showExportResult(", "async function startBulk()"), ctx);
  ctx.result = { download_url: "/api/export/download?id=123", absolute_folder: "/tmp/export",
    warnings: ["Squelette non exporté", "Texture introuvable <nom>"] };
  vm.runInContext('showExportResult(result, "Export prêt")', ctx);
  assert.equal($("#export-result").hidden, false);
  assert.equal($("#export-result-warnings").hidden, false);
  assert.match($("#export-result-warnings").textContent, /Squelette non exporté/);
  assert.match($("#export-result-warnings").textContent, /Texture introuvable <nom>/);
  ctx.result = { download_url: "/api/export/download?id=456&kind=glb", download_format: "glb",
    download_name: "model-animations.glb", warnings: ["Export partiel"],
    animations: { skipped: Array.from({ length: 15 }, (_, i) => ({
      path: `file-${i}.anim.bfres`, name: `<clip-${i}>`, index: i, reason: "rotation inconnue",
    })), exported: [{ path: "ok.anim.bfres", name: "Walk", index: 0, missing: ["Ghost"] }] } };
  vm.runInContext('showExportResult(result, "GLB partiel")', ctx);
  assert.equal($("#export-download").textContent, "Télécharger le GLB");
  assert.match($("#export-result-note").textContent, /un seul glb/i);
  assert.match($("#export-result-warnings").textContent, /file-14\.anim\.bfres · <clip-14>.*rotation inconnue/);
  assert.match($("#export-result-warnings").textContent, /Walk.*Ghost/);
  vm.runInContext('showAnimationExportError("Aucun clip\\n- fichier : refusé")', ctx);
  assert.equal($("#export-download").hidden, true);
  assert.match($("#export-result-title").textContent, /refusé/);
  assert.match($("#export-result-warnings").textContent, /fichier : refusé/);
  ctx.result = { download_url: "/api/export/download?id=789", warnings: [] };
  vm.runInContext('showExportResult(result, "Export suivant")', ctx);
  assert.equal($("#export-download").hidden, false);
  assert.equal($("#export-download").textContent, "Télécharger le ZIP");
  assert.equal($("#export-result-warnings").hidden, true);
  assert.equal($("#export-result-warnings").textContent, "");
}

(async () => {
  exportWarningsRemainVisible();
  await passivePollingDoesNotInterruptThumbnails();
  await incompleteScanIsVisibleInStatusAndExport();
  await delayedSearchDoesNotRebuildHiddenViews();
  await ensembleListIsLoadedOnlyOnceWhileFiltering();
  await olderStateResponsesCannotUndoANewerScan();
  await rescanningUpdatesEnsemblesAndRejectsAnOldList();
  await uvDoesNotStealTheInspector();
  await importDoesNotReopenAnOldInspector();
  await modelImportRejectsStaleAnalysisAndTargetResponses();
  console.log("Interaction UI: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
