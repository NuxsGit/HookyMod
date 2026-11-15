"use strict";

// Les navigateurs peuvent réutiliser une image décodée sans revalidation HTTP.
// Vérifier que les URL changent après édition, scan ou réglage.
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
const helpers = section("function thumbFaces()", "/* le suffixe d'un");
const ensemble = section("function ensembleThumb(row)", "function ensembleMatches(row)");
const texture = section("async function openTexture(rel, index = 0)", "/* ------------------------------------------------------------------ sons");
const uv = section("function uvShot(rel", "async function showUv(rel)");
const state = { settings: { thumb_faces: 20000 }, project: { image_revision: "session-1" },
                tabs: [], tab: "" };
const nodes = new Map();
const $ = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, { checked: true });
  return nodes.get(selector);
};
let html = "";
const calls = [], tabs = [];
const info = { revision: "texture-rev-1", version: "4", orin: false, count: 2,
  textures: [{ index: 0, name: "test", format: "RGBA", editable: true, width: 32, height: 32, mips: 1 },
             { index: 1, name: "seconde", format: "RGBA", editable: true, width: 32, height: 32, mips: 1 }] };
const ctx = vm.createContext({ state, $, console,
  esc: (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])),
  showInspector: (_title, content) => { html = content; vm.runInContext("inspectionSequence++", ctx); },
  api: async (url, options) => {
    calls.push({ url, body: options?.body });
    return url === "/api/texture/export" ? { file: "seconde.png" } : info;
  },
  ensureTab: (tab) => tabs.push(tab), renderTabs() {}, markTile() {}, toast() {}, refresh: async () => {},
});
vm.runInContext("let inspectionSequence = 0;\n" + helpers + ensemble + texture + uv, ctx);

(async () => {
  const before = vm.runInContext(`[
    modelThumb({rel:"Model/Test.bfres",mtime:1}),
    textureThumb({rel:"Tex/Test.bntx",mtime:1}),
    ensembleThumb({rel:"Model/Test.bfres",mtime:1}),
    uvShot("Model/Test.bfres",false)
  ]`, ctx);
  await vm.runInContext('openTexture("Tex/Test.bntx")', ctx);
  const textureBefore = html;
  assert.match(textureBefore, /rev=session-1/);
  assert.match(textureBefore, /assetrev=texture-rev-1/);
  state.project.image_revision = "session-2";
  const after = vm.runInContext(`[
    modelThumb({rel:"Model/Test.bfres",mtime:1}),
    textureThumb({rel:"Tex/Test.bntx",mtime:1}),
    ensembleThumb({rel:"Model/Test.bfres",mtime:1}),
    uvShot("Model/Test.bfres",false)
  ]`, ctx);
  for (let index = 0; index < before.length; index += 1) {
    assert.notEqual(before[index], after[index]);
    assert.match(after[index], /rev=session-2/);
  }
  info.revision = "texture-rev-2";
  await vm.runInContext('openTexture("Tex/Test.bntx")', ctx);
  assert.match(html, /rev=session-2/);
  assert.match(html, /assetrev=texture-rev-2/);
  assert.notEqual(textureBefore, html);
  await vm.runInContext('openTexture("Tex/Test.bntx", 1)', ctx);
  assert.match(html, /index=1/);
  assert.match(html, /seconde/);
  assert.equal(tabs.at(-1).id, "texture:Tex/Test.bntx#1");
  assert.equal(tabs.at(-1).doc.index, 1);
  await nodes.get("#btn-export-png").onclick();
  assert.equal(JSON.parse(calls.at(-1).body).index, 1);
  assert.ok(source.includes("imageRevisionQuery(info.revision)"), "aperçu détaillé versionné");
  console.log("Image revision UI: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
