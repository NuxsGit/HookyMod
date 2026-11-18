"use strict";

// Contrat binaire consommé par le vrai visualiseur, sans npm ni navigateur.
const assert = require("node:assert/strict");
const { parseModelGeometry, normalizeRenderSettings, attachmentMatrices, uniqueRootIndex, followedCenter, FSR_SCALES, ModelViewport } = require("../hookycreator/web/model_viewer.js");

function fixture() {
  const header = { version: 1, meshes: [
    { name: "Corps", material: "Mat_A", vertices: 3, triangles: 1,
      normals: true, uvs: true, texture: { path: "Tex/A.bntx", index: 1 } },
    { name: "Détail", material: "Mat_B", vertices: 3, triangles: 1,
      normals: false, uvs: false, texture: null },
  ] };
  const text = new TextEncoder().encode(JSON.stringify(header));
  const start = Math.ceil((8 + text.length) / 4) * 4;
  const bytes = new ArrayBuffer(start + (9 + 9 + 6 + 3 + 9 + 3) * 4);
  const view = new DataView(bytes);
  new Uint8Array(bytes).set([72, 51, 68, 49]);
  view.setUint32(4, text.length, true);
  new Uint8Array(bytes).set(text, 8);
  let at = start;
  const floats = (values) => { for (const value of values) { view.setFloat32(at, value, true); at += 4; } };
  const uints = (values) => { for (const value of values) { view.setUint32(at, value, true); at += 4; } };
  floats([0, 0, 0, 2, 0, 0, 0, 1, 0]);
  floats([0, 0, 1, 0, 0, 1, 0, 0, 1]);
  floats([0, 0, 1, 0, 0, 1]);
  const firstIndices = at; uints([0, 1, 2]);
  floats([0, 0, 1, 1, 0, 1, 0, 1, 1]);
  uints([2, 1, 0]);
  return { bytes, start, firstIndices };
}

const sample = fixture();
const model = parseModelGeometry(sample.bytes);
assert.equal(model.vertices, 6);
assert.equal(model.triangles, 2);
assert.deepEqual(model.center, [1, 0.5, 0.5]);
assert.deepEqual(model.extent, [1, 0.5, 0.5]);
assert.deepEqual([...model.meshes[0].indices], [0, 1, 2]);
assert.deepEqual([...model.meshes[1].indices], [2, 1, 0]);
assert.deepEqual(model.meshes[0].texture, { path: "Tex/A.bntx", index: 1 });
assert.equal(model.meshes[1].normals, null);
assert.equal(model.meshes[1].texture, null);

const invalidMagic = sample.bytes.slice(0); new Uint8Array(invalidMagic)[0] = 0;
assert.throws(() => parseModelGeometry(invalidMagic), /Format 3D inconnu/);
assert.throws(() => parseModelGeometry(sample.bytes.slice(0, -4)), /tronqué/);
const trailing = new Uint8Array(sample.bytes.byteLength + 4); trailing.set(new Uint8Array(sample.bytes));
assert.throws(() => parseModelGeometry(trailing.buffer), /Taille.*incohérente/);
const nan = sample.bytes.slice(0); new DataView(nan).setFloat32(sample.start, NaN, true);
assert.throws(() => parseModelGeometry(nan), /Position.*non finie/);
const index = sample.bytes.slice(0); new DataView(index).setUint32(sample.firstIndices, 3, true);
assert.throws(() => parseModelGeometry(index), /Indice.*hors limites/);
const header = sample.bytes.slice(0); new DataView(header).setUint32(4, 0xffffffff, true);
assert.throws(() => parseModelGeometry(header), /En-tête 3D invalide/);

assert.deepEqual(FSR_SCALES, { ultra: .77, quality: .67, balanced: .59, performance: .5 });
assert.deepEqual(normalizeRenderSettings(), { viewer_upscale: "native", fsr_quality: "quality",
  fsr_sharpness: .65, viewer_msaa: "4", viewer_dpr: 2, viewer_fps: 60, viewer_limit: 1600 });
assert.deepEqual(normalizeRenderSettings({ viewer_upscale: "fsr", fsr_quality: "performance", fsr_sharpness: 2,
  viewer_msaa: "off", viewer_dpr: "1", viewer_fps: "15", viewer_limit: "800" }),
{ viewer_upscale: "fsr", fsr_quality: "performance", fsr_sharpness: 1,
  viewer_msaa: "off", viewer_dpr: 1, viewer_fps: 15, viewer_limit: 800 });
assert.deepEqual(normalizeRenderSettings({ viewer_upscale: "fsr4", fsr_quality: "bad", fsr_sharpness: NaN,
  viewer_msaa: "999", viewer_dpr: 100, viewer_fps: 1, viewer_limit: 999999 }), normalizeRenderSettings());
assert.equal(normalizeRenderSettings({ fsr_sharpness: -10 }).fsr_sharpness, 0);

const world = [2,0,0,5, 0,3,0,7, 0,0,4,9];
const attached = attachmentMatrices(world, { offset: [1,2,3], rotation: [0,0,0], scale: 2 });
assert.deepEqual([...attached.matrix], [4,0,0,0, 0,6,0,0, 0,0,8,0, 7,13,21,1]);
for (const [actual, expected] of [...attached.normal].map((v, i) => [v, [1/4,0,0, 0,1/6,0, 0,0,1/8][i]]))
  assert.ok(Math.abs(actual - expected) < 1e-7);
const turn = attachmentMatrices([1,0,0,0, 0,1,0,0, 0,0,1,0], { rotation: [0,0,90] });
assert.ok(Math.abs(turn.matrix[1] - 1) < 1e-7 && Math.abs(turn.matrix[4] + 1) < 1e-7);
assert.throws(() => attachmentMatrices(world, { scale: 0 }), /invalide/);
assert.throws(() => attachmentMatrices(world, { offset: [Infinity,0,0] }), /invalide/);
assert.throws(() => attachmentMatrices(world, { rotation: [0,0,3601] }), /invalide/);
assert.throws(() => attachmentMatrices([0,0,0,0, 0,1,0,0, 0,0,1,0]), /échelle nulle/);

// Following the root changes only the camera center. It handles translation,
// rotation, an unavailable or ambiguous root, and non-finite palettes safely.
assert.equal(uniqueRootIndex([{ parent: 1 }, { parent: -1 }]), 1);
assert.equal(uniqueRootIndex([{ parent: -1 }, { parent: -1 }]), -1);
assert.equal(uniqueRootIndex([]), -1);
const restCenter = [2, 3, 4], rootPalette = new Float32Array(48);
rootPalette.set([0,-1,0,10, 1,0,0,20, 0,0,1,30], 24);
assert.deepEqual(followedCenter(restCenter, 1, { palette: rootPalette }), [7,22,34]);
assert.deepEqual(followedCenter(restCenter, -1, { palette: rootPalette }), restCenter);
assert.deepEqual(followedCenter(restCenter, 1, null), restCenter);
rootPalette[27] = Infinity;
assert.deepEqual(followedCenter(restCenter, 1, { palette: rootPalette }), restCenter);

// If FSR fails and the native scene also fails, report the second error
// instead of leaking an exception and leaving the preview silently empty.
const fallbackViewport = Object.create(ModelViewport.prototype), failures = [], notes = [];
Object.assign(fallbackViewport, {
  sizeCanvas: () => true, canvas: { width: 320, height: 240 }, gl: { FRAMEBUFFER: 0, bindFramebuffer() {} },
  mode: "fsr", fsr: {}, ensureTargets: () => { throw new Error("FSR échoué"); }, releaseTargets() {},
  drawScene: () => { throw new Error("Scène échouée"); }, addNote: (note) => notes.push(note),
  options: { onFailure: (message) => failures.push(message) },
});
assert.doesNotThrow(() => fallbackViewport.draw());
assert.equal(fallbackViewport.mode, "native");
assert.deepEqual(notes, ["FSR 1 indisponible : FSR échoué"]);
assert.deepEqual(failures, ["Vue 3D indisponible : Scène échouée"]);

// A slower refresh cap must reduce GPU draws, not slow the animation clock;
// paused scrubbing is still painted immediately between two capped frames.
const scheduled = [], oldRaf = global.requestAnimationFrame;
let id = 0, draws = 0;
global.requestAnimationFrame = (callback) => { scheduled.push(callback); return ++id; };
try {
  const viewport = Object.create(ModelViewport.prototype);
  Object.assign(viewport, { disposed: false, ready: true, frame: 0, lastDraw: null,
    canvas: { isConnected: true }, renderSettings: { viewer_fps: 15 }, options: {},
    animation: { binding: { clip: { fps: 30, frames: 1000 } }, frame: 0, playing: true, loop: false, last: null },
    draw: () => { draws += 1; } });
  viewport.schedule();
  for (let time = 0; time <= 1000; time += 10) scheduled.shift()?.(time);
  assert.ok(draws >= 14 && draws <= 16, `unexpected draw count: ${draws}`);
  assert.ok(viewport.animation.frame > 27 && viewport.animation.frame < 31, `clock: ${viewport.animation.frame}`);
  viewport.animation.playing = false;
  const before = draws; scheduled.shift()?.(1001);
  assert.equal(draws, before + 1, "paused frame is drawn without waiting for the animation cap");
} finally {
  if (oldRaf === undefined) delete global.requestAnimationFrame; else global.requestAnimationFrame = oldRaf;
}
console.log("Model viewer geometry: OK");
