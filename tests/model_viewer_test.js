"use strict";

// Contrat binaire consommé par le vrai visualiseur, sans npm ni navigateur.
const assert = require("node:assert/strict");
const { parseModelGeometry } = require("../hookycreator/web/model_viewer.js");

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
console.log("Model viewer geometry: OK");
