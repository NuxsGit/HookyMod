"use strict";

// Navigation and the shared model-import chooser after separating the maker.
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
  const views = ["home", "edit", "player", "models"].map((name) => ({ id: `view-${name}`, hidden: false }));
  const rail = ["home", "edit", "player", "models"].map((name) => ({
    dataset: { view: name }, current: null,
    setAttribute(_key, value) { this.current = value; },
    removeAttribute() { this.current = null; },
  }));
  const calls = [], tabs = [], scroll = { scrollTop: 260 };
  const state = { view: "home", tab: "" };
  const $ = (selector) => selector === "#views" ? scroll : views.find((node) => `#${node.id}` === selector);
  const $$ = (selector) => selector === ".view" ? views : rail;
  const ctx = vm.createContext({ state, $, $$, VIEW_LABELS: { edit: "Modification", player: "Player" },
    window: { HookyPlayer: { show: () => calls.push("player"), hide() {} } },
    thumbnailQueue: { release: (node) => calls.push(`release:${node.id}`) },
    assetValidation: { clearScope() {} },
    hideInspector: () => calls.push("hide-inspector"),
    ensureViewTab: (view, label) => tabs.push([view, label]), renderTabs() {},
    renderHome() {}, renderModels() {}, renderEnsembles() {}, renderTextures() {},
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
  vm.runInContext('showView("player")', ctx);
  assert.equal(state.tab, "view:player");
  assert.equal(scroll.scrollTop, 0);
  assert.equal(views.find((view) => view.id === "view-player").hidden, false);
  assert.equal(rail.find((node) => node.dataset.view === "edit").current, null);
  assert.ok(calls.includes("player"));
  assert.equal(calls.filter((call) => call === "hide-inspector").length, 2);
  assert.deepEqual(tabs.map(([view]) => view), ["edit", "player"]);
}

function modelImportChooserRetainsValidationAndExplicitSelection() {
  const select = { value: "", dataset: {}, options: [] };
  Object.defineProperty(select, "innerHTML", {
    get() { return this.html || ""; },
    set(html) {
      this.html = html;
      this.options = [...html.matchAll(/<option(?: [^>]*)? value="([^"]*)"/g)].map((m) => ({ value: m[1] }));
      this.value = this.options[0]?.value || "";
    },
  });
  const search = { value: "" }, more = { hidden: true }, scope = [], updates = [];
  const assets = [
    { kind: "model", rel: "A.bfres", name: "A" },
    { kind: "model", rel: "B.bfres", name: "B" },
    { kind: "model", rel: "C.bfres", name: "C" },
    { kind: "texture", rel: "T.bntx", name: "T" },
  ];
  const state = { chooserModels: assets.slice(0, 3), project: { assets, image_revision: "rev" }, shown: { chooser: 2 } };
  const ctx = vm.createContext({ state, restoredChoices: {}, esc: String,
    $: (name) => ({ "#model-target": select, "#model-target-search": search,
                    "#model-import-validate-more": more })[name],
    assetValidation: {
      valid: (_kind, rel) => rel !== "C.bfres",
      entry: (_kind, rel) => ({ injectable: rel !== "B.bfres" }),
      setScope: (name, items, rev) => scope.push([name, [...items], rev]),
    },
    matches: (asset, filter) => asset.name.toLowerCase().includes(filter),
    updateModelImportControls: () => updates.push("controls"),
    updateValidatedAssets: () => updates.push("validation"),
  });
  vm.runInContext(section("function applyRestoredChoices()", "function captureUiSession()"), ctx);
  vm.runInContext(section("function fillModelTargets()", "function openDoc(doc)"), ctx);
  vm.runInContext("fillModelTargets()", ctx);
  assert.deepEqual(select.options.map((item) => item.value), ["", "A.bfres"]);
  assert.equal(select.value, "", "never inject automatically into the first valid target");
  ctx.restoredChoices["model-target"] = "A.bfres";
  vm.runInContext("fillModelTargets()", ctx);
  assert.equal(select.value, "A.bfres");
  assert.equal(ctx.restoredChoices["model-target"], undefined);
  ctx.restoredChoices["model-target"] = "B.bfres";
  vm.runInContext("fillModelTargets()", ctx);
  assert.equal(select.value, "", "an unvalidated target cannot be silently replaced");
  assert.equal(ctx.restoredChoices["model-target"], "B.bfres");
  vm.runInContext("requestChooserChecks()", ctx);
  assert.deepEqual(scope[0], ["view", assets.slice(0, 2), "rev"]);
  assert.equal(more.hidden, false);
  search.value = "b";
  vm.runInContext("requestChooserChecks()", ctx);
  assert.deepEqual(scope[1], ["view", [assets[1]], "rev"]);
  assert.equal(more.hidden, true);
  assert.ok(updates.includes("controls") && updates.includes("validation"));
}

workspaceNavigation();
modelImportChooserRetainsValidationAndExplicitSelection();
console.log("Workspaces UI: OK");
