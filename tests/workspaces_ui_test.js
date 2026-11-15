"use strict";

// Les deux parcours et la confirmation d'écrasement, sans dépendance npm.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../hookycreator/web/app.js"), "utf8");
function section(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `${from} introuvable`);
  return source.slice(start, end);
}

function workspaceNavigation() {
  const views = ["home", "edit", "create", "models"].map((name) => ({ id: `view-${name}`, hidden: false }));
  const rail = ["home", "edit", "create", "models"].map((name) => ({
    dataset: { view: name }, current: null,
    setAttribute(_key, value) { this.current = value; },
    removeAttribute() { this.current = null; },
  }));
  const calls = [], tabs = [], scroll = { scrollTop: 260 };
  const state = { view: "home", tab: "" };
  const $ = (selector) => selector === "#views" ? scroll : views.find((node) => `#${node.id}` === selector);
  const $$ = (selector) => selector === ".view" ? views : rail;
  const ctx = vm.createContext({ state, $, $$, VIEW_LABELS: { edit: "Modification", create: "Création de mod" },
    thumbnailQueue: { release: (node) => calls.push(`release:${node.id}`) },
    assetValidation: { clearScope() {} },
    hideInspector: () => calls.push("hide-inspector"),
    ensureViewTab: (view, label) => tabs.push([view, label]), renderTabs() {},
    renderHome() {}, renderCreation: () => calls.push("creation"), renderModels() {},
    renderEnsembles() {}, renderTextures() {}, renderMobs() {}, renderBlends() {},
    renderSettings() {}, refreshSystem() {},
  });
  vm.runInContext(section("function showView(", "const VIEW_LABELS ="), ctx);
  vm.runInContext('showView("edit")', ctx);
  assert.equal(state.view, "edit");
  assert.equal(state.tab, "view:edit");
  assert.equal(views.find((view) => view.id === "view-edit").hidden, false);
  assert.equal(views.find((view) => view.id === "view-home").hidden, true);
  assert.equal(rail.find((node) => node.dataset.view === "edit").current, "page");
  assert.equal(scroll.scrollTop, 0);
  scroll.scrollTop = 180;
  vm.runInContext('showView("create")', ctx);
  assert.equal(state.tab, "view:create");
  assert.equal(scroll.scrollTop, 0);
  assert.equal(views.find((view) => view.id === "view-create").hidden, false);
  assert.equal(rail.find((node) => node.dataset.view === "edit").current, null);
  assert.ok(calls.includes("creation"));
  assert.equal(calls.filter((call) => call === "hide-inspector").length, 2);
  assert.deepEqual(tabs.map(([view]) => view), ["edit", "create"]);
}

function creationReflectsTheCurrentProject() {
  const list = { innerHTML: "" };
  const state = { project: { edited: ["a", "b"], mobs: [{ slug: "x" }] } };
  const ctx = vm.createContext({ state, $: () => list });
  vm.runInContext(section("function renderCreation()", "/* ------------------------------------------------------------------ modèles */"), ctx);
  vm.runInContext("renderCreation()", ctx);
  assert.match(list.innerHTML, /Copies modifiées<\/span><span>2/);
  assert.match(list.innerHTML, /Fiches de mobs préparées<\/span><span>1/);
  state.project.edited = [];
  vm.runInContext("renderCreation()", ctx);
  assert.match(list.innerHTML, /Copies modifiées<\/span><span>0/);
}

async function buildingRequiresConfirmationAndCannotDoubleSubmit() {
  const button = { disabled: false }, log = { innerHTML: "" }, messages = [], requests = [];
  let confirm = false, resolve, reject;
  const form = { querySelector: () => button, name: "MonMod" };
  const event = { target: form, preventDefault() {} };
  const ctx = vm.createContext({ event, console, esc: String, $: () => log,
    FormData: class { constructor(target) { this.target = target; } get() { return this.target.name; } },
    window: { confirm: (message) => { messages.push(message); return confirm; } },
    toast: (...args) => messages.push(args),
    api: (url, options) => new Promise((done, fail) => {
      requests.push([url, options]); resolve = done; reject = fail;
    }),
  });
  vm.runInContext("let modBuildPending = false;\n" +
    section("async function buildMod(", "async function createMob("), ctx);
  await vm.runInContext("buildMod(event)", ctx);
  assert.equal(requests.length, 0, "annuler ne modifie rien");
  assert.match(messages[0], /entièrement remplacé/);
  confirm = true;
  const first = vm.runInContext("buildMod(event)", ctx);
  assert.equal(button.disabled, true);
  await vm.runInContext("buildMod(event)", ctx);
  assert.equal(requests.length, 1, "une deuxième soumission ne lance pas de construction concurrente");
  assert.equal(requests[0][0], "/api/mod/build");
  assert.equal(JSON.parse(requests[0][1].body).name, "MonMod");
  resolve({ mod: "MonMod", path: "mods/MonMod", files: 2, mobs: 1 });
  await first;
  assert.equal(button.disabled, false);
  assert.match(log.innerHTML, /Fiches de mobs/);
  assert.match(log.innerHTML, /mods\/MonMod/);
  const failed = vm.runInContext("buildMod(event)", ctx);
  assert.equal(button.disabled, true);
  reject(new Error("Échec de l'assemblage"));
  await failed;
  assert.equal(button.disabled, false, "un échec ne laisse pas le formulaire bloqué");
  assert.ok(messages.some((item) => Array.isArray(item) && item[0] === "Échec de l'assemblage"));
}

(async () => {
  workspaceNavigation();
  creationReflectsTheCurrentProject();
  await buildingRequiresConfirmationAndCannotDoubleSubmit();
  console.log("Workspaces UI: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
