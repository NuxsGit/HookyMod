"use strict";

// Exerce la capture, la restauration et le protocole de fermeture réels avec
// un DOM minimal ; la CI vérifie aussi une vraie webview native.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../hookycreator/web/app.js"), "utf8");
const start = source.indexOf('const SESSION_TEXT_FIELDS =');
const end = source.indexOf('async function boot()', start);
assert.ok(start >= 0 && end > start);
const code = source.slice(start, end);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function harness() {
  const nodes = new Map();
  function add(id, extra = {}) {
    const listeners = {};
    const node = { id, value: "", checked: false, hidden: false, scrollTop: 0, options: [],
      addEventListener: (type, callback) => { listeners[type] = callback; }, listeners, ...extra };
    Object.defineProperty(node, "selectedOptions", { get() { return this.options.filter((o) => o.selected); } });
    nodes.set(`#${id}`, node);
    return node;
  }
  for (const id of ["search", "search-tex", "search-ens", "search-sound", "tex-role",
    "model-axis", "model-scale", "model-target-search", "model-shots", "ens-textures",
    "bulk-anim", "model-fit", "model-target", "views", "inspector", "console", "selection-count", "tabs"]) add(id);
  nodes.get("#inspector").hidden = true;
  nodes.get("#console").hidden = true;
  const $ = (selector) => nodes.get(selector) || null;
  const listeners = {}, winListeners = {}, actions = [], navigations = [], writes = [];
  const state = {
    project: { assets: [
      { kind: "model", rel: "Model/Héros.bfres", name: "Héros" },
      { kind: "texture", rel: "Model/Héros_Alb.bntx", name: "Albedo" },
      { kind: "sound", rel: "Sound/One.bwav", name: "One" },
    ] },
    view: "home", tabs: [], tab: "view:home",
    filters: {}, selection: new Set(), shown: { models: 48, textures: 48, ensembles: 48, sounds: 48, chooser: 48 },
  };
  const document = { documentElement: { dataset: {} }, body: { inert: false }, visibilityState: "visible",
    addEventListener: (type, callback) => { listeners[type] = callback; } };
  const window = { addEventListener: (type, callback) => { winListeners[type] = callback; } };
  const thumb = { paused: false };
  const labels = { home: "Accueil", models: "Modèles", textures: "Textures", sounds: "Sons", import: "Importer" };
  class FakeWriter {
    constructor(capture, options) { this.capture = capture; this.options = options; this.enabled = false; }
    enable() { this.enabled = true; }
    schedule(delay) { writes.push(["schedule", delay]); }
    async flush(options = {}) { writes.push(["flush", options]); return this.result !== false; }
  }
  const ctx = vm.createContext({
    state, $, document, window, location: { assign: (url) => navigations.push(url) },
    restoredChoices: {}, restoredModelQuality: "", uiSessionWriter: null,
    uiSessionBoot: Promise.resolve(), sessionLoadFailed: false, nativeCheckpointSerial: 0,
    thumbnailQueue: thumb, VIEW_LABELS: labels, UiSessionWriter: FakeWriter,
    MutationObserver: class { observe() {} },
    ensureTab: (tab) => {
      let found = state.tabs.find((item) => item.id === tab.id);
      if (!found) { found = tab; state.tabs.push(found); }
      return found;
    },
    ensureViewTab: (view, label) => {
      let found = state.tabs.find((item) => item.id === `view:${view}`);
      if (!found) { found = { id: `view:${view}`, view, label }; state.tabs.push(found); }
      return found;
    },
    showView: (view) => { state.view = view; }, renderTabs() {}, updateSelectionBar() {},
    toggleConsole: (show) => { $("#console").hidden = !show; },
    setThumbnailPause: (paused) => { thumb.paused = paused; },
    openDoc: (doc) => { actions.push(["openDoc", doc]); $("#inspector").hidden = false; },
    requestAnimationFrame: (callback) => callback(),
    finishPendingSettings: async () => { writes.push(["settings"]); return true; },
    toast: (message) => actions.push(["toast", message]), status: (...args) => actions.push(["status", ...args]),
    log: (...args) => actions.push(["log", ...args]),
  });
  vm.runInContext(code, ctx);
  return { ctx, nodes, $, state, document, window, listeners, winListeners, actions, navigations, writes, thumb };
}

async function restoreAndCapture() {
  const h = harness();
  h.ctx.saved = {
    version: 1, view: "models", active: "model:Model/Héros.bfres",
    tabs: [{ type: "view", view: "home" }, { type: "view", view: "models" },
      { type: "model", rel: "Model/Héros.bfres" }, { type: "model", rel: "missing.bfres" },
      { type: "texture", rel: "../../other" }, { type: "texture", rel: "Model/Héros_Alb.bntx", index: 1 }, { type: "mob", slug: "MyMob" },
      { type: "blend", ref: "work:one.blend" }, { type: "blend", ref: "work:missing.blend" }],
    selection: ["model:Model/Héros.bfres", "texture:Model/Héros_Alb.bntx", "model:missing.bfres"],
    fields: { search: " HÉROS ", "mod-name": "Mon brouillon", "model-shots": true,
              "ens-textures": false, "model-quality": "detail" },
    choices: { "mob-model": "Model/Héros.bfres", "model-target": "Model/Héros.bfres",
               "mob-textures": ["Model/Héros_Alb.bntx", "missing.bntx"] },
    shown: { models: 96, textures: 72 }, scroll: 500, inspector: true, console: true,
    reduced: true, thumbnails_paused: true,
  };
  await vm.runInContext("restoreUiSession(saved)", h.ctx);
  assert.equal(h.state.view, "models");
  assert.equal(h.state.tab, "model:Model/Héros.bfres");
  assert.equal(h.state.filters.models, "héros");
  assert.equal(h.$("#model-shots").checked, true);
  assert.equal(h.$("#ens-textures").checked, false);
  assert.equal(h.$("#views").scrollTop, 500);
  assert.deepEqual([...h.state.selection], ["model:Model/Héros.bfres", "texture:Model/Héros_Alb.bntx"]);
  assert.equal(h.state.tabs.some((tab) => tab.id.includes("missing") || tab.id.includes("../")), false);
  assert.equal(h.state.tabs.some((tab) => tab.id === "mob:MyMob"), false);
  assert.equal(h.state.tabs.some((tab) => tab.id.startsWith("blend:")), false);
  assert.equal(h.state.tabs.some((tab) => tab.id === "texture:Model/Héros_Alb.bntx#1" && tab.doc.index === 1), true);
  assert.equal(h.actions.filter(([name]) => name === "openDoc").length, 1);
  assert.equal(h.document.documentElement.dataset.min, "1");
  assert.equal(h.thumb.paused, true);
  const captured = JSON.parse(JSON.stringify(vm.runInContext("captureUiSession()", h.ctx)));
  assert.deepEqual(Object.keys(captured.choices), ["model-target"]);
  assert.equal(captured.choices["model-target"], "Model/Héros.bfres", "choix maintenu pendant la validation");
  assert.equal(captured.fields["model-quality"], "detail");
  assert.equal(captured.shown.models, 96);

  h.$("#model-target").options.push({ value: "Model/Héros.bfres" });
  vm.runInContext("applyRestoredChoices()", h.ctx);
  assert.equal(h.$("#model-target").value, "Model/Héros.bfres");
}

async function userActionDuringLoadWins() {
  const h = harness();
  h.ctx.saved = { version: 1, view: "models", tabs: [{ type: "view", view: "models" }],
                  fields: { search: "ancienne valeur" } };
  h.$("#search").value = "saisie récente";
  vm.runInContext("sessionTouchedDuringBoot = true", h.ctx);
  await vm.runInContext("restoreUiSession(saved)", h.ctx);
  assert.equal(h.$("#search").value, "saisie récente");
  assert.equal(h.state.view, "home");
}

async function nativeCheckpointAwaitsBootSettingsAndWrite() {
  const h = harness();
  vm.runInContext("wireUiSession(); uiSessionWriter.enable()", h.ctx);
  let boot, settings;
  h.ctx.uiSessionBoot = new Promise((resolve) => { boot = resolve; });
  h.ctx.finishPendingSettings = () => new Promise((resolve) => { settings = resolve; });
  const pending = h.window.__hookyCheckpoint(42);
  assert.equal(h.document.body.inert, true);
  assert.deepEqual(h.writes, []);
  boot(); await tick();
  assert.deepEqual(h.writes, [], "attend le réglage en vol avant la capture");
  settings(true); await pending;
  assert.deepEqual(h.writes.map((item) => item[0]), ["flush"]);
  assert.equal(h.writes[0][1].keepalive, undefined);
  assert.deepEqual(h.navigations, ["/__hooky/checkpoint?id=42&saved=1"]);
  h.window.__hookyResume();
  assert.equal(h.document.body.inert, false);

  h.ctx.finishPendingSettings = async () => false;
  await h.window.__hookyCheckpoint(43);
  assert.equal(h.navigations[1], "/__hooky/checkpoint?id=43&saved=0");
  h.window.__hookyResume();

  let resolveSettings;
  h.ctx.finishPendingSettings = () => new Promise((resolve) => { resolveSettings = resolve; });
  const cancelled = h.window.__hookyCheckpoint(44);
  await tick();
  h.window.__hookyResume();
  resolveSettings(true); await cancelled;
  assert.equal(h.navigations.length, 2, "une réponse tardive ne ferme pas après annulation");
}

async function nativeCheckpointDoesNotInvokeArchivedMaker() {
  const h = harness();
  vm.runInContext("wireUiSession(); uiSessionWriter.enable()", h.ctx);
  h.window.HookyStudio = { flush: () => { throw new Error("maker must not run"); } };
  await h.window.__hookyCheckpoint(50);
  assert.deepEqual(h.navigations, ["/__hooky/checkpoint?id=50&saved=1"]);
}

async function programmaticScrollDoesNotCancelRestore() {
  const h = harness();
  vm.runInContext("wireUiSession()", h.ctx);
  h.$("#views").listeners.scroll({ type: "scroll", isTrusted: true });
  assert.equal(vm.runInContext("sessionTouchedDuringBoot", h.ctx), false);
  h.listeners.wheel({ type: "wheel", isTrusted: true });
  assert.equal(vm.runInContext("sessionTouchedDuringBoot", h.ctx), true);
}

(async () => {
  await restoreAndCapture();
  await userActionDuringLoadWins();
  await nativeCheckpointAwaitsBootSettingsAndWrite();
  await nativeCheckpointDoesNotInvokeArchivedMaker();
  await programmaticScrollDoesNotCancelRestore();
  console.log("UI session restoration: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
