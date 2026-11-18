"use strict";
const assert = require("node:assert/strict");
const { parseSkin, evaluate, evaluateQuaternion, bind, palette, restPose, PoseError } = require("../hookycreator/web/model_animation.js");

const identity = [1,0,0,0, 0,1,0,0, 0,0,1,0];
const skin = {
  bones: [
    { name: "Root", parent: -1, scale: [1,1,1], rotation: [0,0,0,1], translation: [0,0,0], inverse: identity, ssc: false },
    { name: "Child", parent: 0, scale: [1,1,1], rotation: [0,0,0,1], translation: [0,2,0], inverse: [1,0,0,0,0,1,0,-2,0,0,1,0], ssc: false },
  ], order: [0, 1], meshes: [],
};
const x = { target: "translation.x", interpolation: "cubic", frames: [0,10], values: [0,2], slopes: [.2,.2] };
const clip = { frames: 10, fps: 30, bones: [{ name: "Root", base: {}, curves: [x] }] };
assert.equal(evaluate(x, 5), 1);
assert.equal(evaluate(x, -1), 0);
assert.equal(evaluate(x, 50), 2);
// Repeated times encode a jump, not a duplicate animation.
const jump = { ...x, frames: [0, 5, 5, 10], values: [0, 1, 3, 4], slopes: [.2, .2, .2, .2] };
assert.ok(Math.abs(evaluate(jump, 4.9) - .98) < 1e-10);
assert.equal(evaluate(jump, 5), 3);
assert.ok(Math.abs(evaluate(jump, 5.1) - 3.02) < 1e-10);
assert.equal(bind(skin, { ...clip, bones: [{ name: "Root", base: {}, curves: [jump] }] }).matched, 1);
const binding = bind(skin, clip);
assert.equal(binding.matched, 1);
let matrices = palette(binding, 5);
assert.equal(matrices.length, 48);
assert.deepEqual([...matrices.slice(0, 12)], [1,0,0,1, 0,1,0,0, 0,0,1,0]);
assert.deepEqual([...matrices.slice(24, 36)], [1,0,0,1, 0,1,0,0, 0,0,1,0]);
assert.throws(() => bind(skin, { ...clip, scale_mode: 0x300 }), /Softimage/);
assert.throws(() => bind(skin, { ...clip, bones: [{ name: "Other", base: {}, curves: [x] }] }), /Aucun os/);
assert.throws(() => bind(skin, { ...clip, bones: [{ name: "Root", base: {}, curves: [{ ...x, frames: [10,0] }] }] }), /Courbe/);
assert.throws(() => palette(binding, NaN), /Temps/);

// Segment scale compensation removes the direct parent's nonuniform scale from
// the child's orientation, while its translation remains scaled by the parent.
const root = { ...skin.bones[0], scale: [2,3,4] };
const child = { ...skin.bones[1], translation: [1,0,0], inverse: [1,0,0,-1,0,1,0,0,0,0,1,0], ssc: true };
const compensated = { bones: [root, child], order: [0,1], meshes: [] };
const dummy = { ...x, values: [0,0], slopes: [0,0] };
matrices = palette(bind(compensated, { ...clip, bones: [{ name: "Root", base: {}, curves: [dummy] }] }), 0);
assert.deepEqual([...matrices.slice(24,36)], [1,0,0,1, 0,1,0,0, 0,0,1,0]);
assert.deepEqual([...matrices.slice(12,24)], [.5,0,0,0, 0,Math.fround(1/3),0,0, 0,0,.25,0]);
const varyingScale = { target: "scale.x", interpolation: "cubic", frames: [0,10], values: [2,4], slopes: [0,0] };
const varyingBinding = bind(compensated, { ...clip, bones: [{ name: "Root", base: {}, curves: [varyingScale] }] });
assert.deepEqual([...palette(varyingBinding, 10).slice(24,36)], [1,0,0,3, 0,1,0,0, 0,0,1,0]);

// Quaternion keys interpolate via the shortest arc, including opposite signs.
const qcurve = { target: "rotation", interpolation: "slerp", frames: [0,10],
  values: [[0,0,0,1], [0,0,-Math.SQRT1_2,-Math.SQRT1_2]] };
const q = evaluateQuaternion(qcurve, 5);
assert.ok(Math.abs(q[2] - Math.sin(Math.PI/8)) < 1e-7 && Math.abs(q[3] - Math.cos(Math.PI/8)) < 1e-7);
const qskin = { ...skin, bones: [{ ...skin.bones[0], rotation_mode: "quaternion",
  rotation: [0,0,Math.SQRT1_2,Math.SQRT1_2] }, skin.bones[1]] };
const qclip = { ...clip, bones: [{ name: "Root", rotation_mode: "quaternion", base: {}, curves: [qcurve] }] };
const qp = palette(bind(qskin, qclip), 5);
assert.ok(Math.abs(qp[0] - Math.SQRT1_2) < 1e-6 && Math.abs(qp[1] + Math.SQRT1_2) < 1e-6);
const partialEuler = { ...clip, bones: [{ name: "Root", base: {}, curves: [{ ...x, target: "rotation.z" }] }] };
assert.throws(() => bind(qskin, partialEuler), /Rotation Euler partielle/);
const translationOnly = { ...clip, bones: [{ name: "Root", base: {}, curves: [x] }] };
assert.deepEqual([...palette(bind(qskin, translationOnly), 0).slice(0,12)],
  [...restPose(qskin).palette.slice(0,12)], "translation-only tracks retain the quaternion rest rotation");

// A later zero-scale frame is not silently replaced by an identity normal or
// an uncompensated pose. A small uniform, invertible scale is still accepted.
const scaleCurve = { target: "scale.x", interpolation: "cubic", frames: [0,10], values: [1,0], slopes: [0,0] };
const scaleBinding = bind(skin, { ...clip, bones: [{ name: "Root", base: {}, curves: [scaleCurve] }] });
assert.throws(() => palette(scaleBinding, 10), PoseError);
const tiny = { ...skin, bones: [{ ...skin.bones[0], scale: [1e-5,1e-5,1e-5] }, skin.bones[1]] };
assert.ok([...restPose(tiny).palette].every(Number.isFinite));

function packet() {
  const header = { version: 1, bones: skin.bones, meshes: [{ name: "triangle", vertices: 1 }] };
  const text = new TextEncoder().encode(JSON.stringify(header)), start = Math.ceil((8 + text.length) / 4) * 4;
  const buf = new ArrayBuffer(start + 24), bytes = new Uint8Array(buf), view = new DataView(buf);
  bytes.set([72,51,83,49]); view.setUint32(4, text.length, true); bytes.set(text, 8);
  view.setUint16(start, 1, true); bytes[start + 16] = 255;
  return { buf, start };
}
const sample = packet(), model = { meshes: [{ name: "triangle", positions: [0,0,0] }] };
const decoded = parseSkin(sample.buf, model);
assert.equal(decoded.meshes[0].joints[0], 1);
assert.equal(decoded.meshes[0].weights[0], 255);
const bad = sample.buf.slice(0); new DataView(bad).setUint16(sample.start, 5, true);
assert.throws(() => parseSkin(bad, model), /Indice/);
assert.throws(() => parseSkin(sample.buf.slice(0,-1), model), /correspondent|Taille/);
assert.throws(() => parseSkin(sample.buf, { meshes: [{ name: "other", positions: [0,0,0] }] }), /correspondent/);
console.log("Animation curves, skin, hierarchy and compensation: OK");
