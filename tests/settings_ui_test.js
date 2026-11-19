"use strict";

// Exerce les vrais gestionnaires et l'écriture différée, sans DOM ni npm.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../hookycreator/web/app.js"), "utf8");
const start = source.indexOf("let settingsTimer = null;");
const end = source.indexOf("/* ------------------------------------------------------------------ sélection */", start);
assert.ok(start >= 0 && end > start);
const code = source.slice(start, end);
const revisionStart = source.indexOf("function revisionOrder(");
const revisionEnd = source.indexOf("function acceptProjectSnapshot(", revisionStart);
assert.ok(revisionStart >= 0 && revisionEnd > revisionStart);
const revisionCode = source.slice(revisionStart, revisionEnd);
const nodes = new Map();
const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, { value: "", checked: false, textContent: "" });
  return nodes.get(selector);
};
const calls = [];
const events = [];
const state = { settings: { vram_gb: 6, supersample: 2, preview_faces: 60000 }, view: "settings" };
const context = vm.createContext({
  state, $: node, settingsOrDefault: () => state.settings,
  applyUiSettings() {}, renderSettings() {}, renderStatus() {}, renderModels: async () => {},
  toast: (message, kind) => events.push(["toast", message, kind]),
  clearCache: () => events.push(["clear"]),
  rescanProject: () => events.push(["scan"]),
  prepareThumbnails: () => events.push(["warm-start"]),
  stopPreparingThumbnails: () => events.push(["warm-stop"]),
  api: (route, options) => new Promise((resolve, reject) => {
    calls.push({ route, patch: JSON.parse(options.body), resolve, reject });
  }),
  setTimeout, clearTimeout, console,
});
vm.runInContext(revisionCode + code + "\nwireSettings();", context);

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitForCall(count) {
  for (let i = 0; i < 100 && calls.length < count; i += 1) await wait(10);
  assert.equal(calls.length, count, "requête de réglages attendue");
}

async function handlersAndBatching() {
  assert.equal(typeof node("#cache-clear").onclick, "function");
  assert.equal(typeof node("#scan-now").onclick, "function");
  assert.equal(typeof node("#set-density").onchange, "function");
  node("#cache-clear").onclick();
  node("#scan-now").onclick();
  assert.deepEqual(events.slice(0, 2), [["clear"], ["scan"]]);

  const first = vm.runInContext("saveSettings({vram_gb: 3})", context);
  const second = vm.runInContext("saveSettings({supersample: 1})", context);
  let firstResolved = false;
  first.then(() => { firstResolved = true; });
  await waitForCall(1);
  assert.deepEqual(calls[0].patch, { vram_gb: 3, supersample: 1 });
  assert.equal(firstResolved, false, "attendre l'enregistrement réel");

  // Un nouveau changement pendant la première requête ne la dépasse pas.
  const third = vm.runInContext("saveSettings({preview_faces: 150000})", context);
  await wait(220);
  assert.equal(calls.length, 1, "une seule écriture active");
  calls[0].resolve({ settings: { vram_gb: 3, supersample: 1, preview_faces: 60000 } });
  assert.equal(await first, true);
  assert.equal(await second, true);
  await waitForCall(2);
  assert.deepEqual(calls[1].patch, { preview_faces: 150000 });
  assert.equal(state.settings.preview_faces, 150000, "la saisie récente reste visible");
  calls[1].resolve({ settings: { vram_gb: 3, supersample: 1, preview_faces: 150000 } });
  assert.equal(await third, true);

  // Un changement depuis un vrai contrôle est également enregistré.
  const changed = node("#set-density").onchange({ target: { value: "compact" } });
  await waitForCall(3);
  assert.deepEqual(calls[2].patch, { density: "compact" });
  calls[2].resolve({ settings: { ...state.settings, density: "compact" } });
  assert.equal(await changed, true);

  // Une erreur est signalée et ne bloque pas les prochaines sauvegardes.
  const bad = vm.runInContext("saveSettings({vram_gb: 9})", context);
  await waitForCall(4);
  calls[3].reject(new Error("disque indisponible"));
  assert.equal(await bad, false);
  assert.ok(events.some((entry) => entry[1] === "disque indisponible" && entry[2] === "error"));
  const good = vm.runInContext("saveSettings({vram_gb: 4})", context);
  await waitForCall(5);
  calls[4].resolve({ settings: { ...state.settings, vram_gb: 4 } });
  assert.equal(await good, true);

  // La réponse d'un réglage plus ancien qu'un état déjà reçu ne doit ni
  // ramener les images en arrière, ni écraser des réglages plus récents.
  state.project = { image_revision: "session-7" };
  const old = vm.runInContext("saveSettings({cache_mb: 32})", context);
  await waitForCall(6);
  state.project.image_revision = "session-9";
  state.settings.cache_mb = 64;
  calls[5].resolve({ settings: { ...state.settings, cache_mb: 32 }, image_revision: "session-8" });
  assert.equal(await old, true);
  assert.equal(state.project.image_revision, "session-9");
  assert.equal(state.settings.cache_mb, 64);

  // All GPU economy controls send their own fields and are batched together.
  const changes = [
    ["#set-viewer-upscale", "onchange", "fsr"], ["#set-fsr-quality", "onchange", "balanced"],
    ["#set-fsr-sharp", "oninput", "0.3"], ["#set-viewer-msaa", "onchange", "off"],
    ["#set-viewer-dpr", "onchange", "1"], ["#set-viewer-fps", "onchange", "15"],
    ["#set-viewer-limit", "onchange", "800"],
  ];
  const pending = changes.map(([selector, event, value]) => {
    assert.equal(typeof node(selector)[event], "function", `${selector} wired`);
    return node(selector)[event]({ target: { value } });
  });
  await waitForCall(7);
  const gpu = { viewer_upscale: "fsr", fsr_quality: "balanced", fsr_sharpness: .3, viewer_msaa: "off",
    viewer_dpr: "1", viewer_fps: "15", viewer_limit: "800" };
  assert.deepEqual(calls[6].patch, gpu);
  calls[6].resolve({ settings: { ...state.settings, ...gpu } });
  assert.deepEqual(await Promise.all(pending), changes.map(() => true));

  node("#set-reset").onclick(); await waitForCall(8);
  assert.deepEqual(Object.fromEntries(Object.keys(gpu).map((key) => [key, calls[7].patch[key]])),
    { viewer_upscale: "native", fsr_quality: "quality", fsr_sharpness: .65, viewer_msaa: "4",
      viewer_dpr: "2", viewer_fps: "60", viewer_limit: "1600" });
  calls[7].resolve({ settings: { ...state.settings, ...calls[7].patch } });
}

handlersAndBatching().then(() => console.log("Settings UI: OK"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
