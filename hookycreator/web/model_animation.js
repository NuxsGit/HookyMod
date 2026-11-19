"use strict";

/* Experimental FSKA playback on the validated BFRES 10.x skin. No game assets
   are changed. The skin is applied to the already placed H3D1 rest geometry. */
const HookyAnimation = (() => {
  const MAX_BYTES = 64 * 1024 * 1024;
  class PoseError extends Error {}
  const finiteArray = (value, count) => Array.isArray(value) && value.length === count
    && value.every(Number.isFinite);

  function parseSkin(buffer, model) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 12 || buffer.byteLength > MAX_BYTES)
      throw new Error("Skin invalide ou trop volumineux.");
    const bytes = new Uint8Array(buffer), view = new DataView(buffer);
    if (String.fromCharCode(...bytes.subarray(0, 4)) !== "H3S1") throw new Error("Format de skin inconnu.");
    const size = view.getUint32(4, true);
    if (size > 4 * 1024 * 1024 || size + 8 > buffer.byteLength) throw new Error("En-tête de skin invalide.");
    let header;
    try { header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(8, 8 + size))); }
    catch (_) { throw new Error("En-tête de skin illisible."); }
    if (header.version !== 1 || !Array.isArray(header.bones) || !header.bones.length
        || header.bones.length > 4096 || !Array.isArray(header.meshes)
        || header.meshes.length !== model.meshes.length) throw new Error("Squelette de skin invalide.");
    const names = new Set();
    for (const [index, b] of header.bones.entries()) {
      if (typeof b.name !== "string" || !b.name || names.has(b.name) || !Number.isInteger(b.parent)
          || b.parent < -1 || b.parent >= header.bones.length || b.parent === index
          || !finiteArray(b.scale, 3) || !finiteArray(b.rotation, 4) || !finiteArray(b.translation, 3)
          || !finiteArray(b.inverse, 12) || typeof b.ssc !== "boolean"
          || (b.rotation_mode !== undefined && !["euler", "quaternion"].includes(b.rotation_mode)))
        throw new Error("Os de skin invalide.");
      if (b.rotation_mode === "quaternion") {
        const length = Math.hypot(...b.rotation);
        if (length < 0.999 || length > 1.001) throw new Error("Quaternion de repos non unitaire.");
      }
      names.add(b.name);
    }
    // Parent-first order, checked without recursion (bones need not be ordered).
    const order = [], done = new Set();
    for (let i = 0; i < header.bones.length; i += 1) {
      let k = i; const path = [], seen = new Set();
      while (k !== -1 && !done.has(k)) {
        if (seen.has(k)) throw new Error("Cycle dans le squelette.");
        seen.add(k); path.push(k); k = header.bones[k].parent;
      }
      while (path.length) { k = path.pop(); done.add(k); order.push(k); }
    }
    let cursor = Math.ceil((8 + size) / 4) * 4;
    const meshes = header.meshes.map((entry, i) => {
      const n = model.meshes[i].positions.length / 3;
      if (entry.name !== model.meshes[i].name || entry.vertices !== n || cursor + n * 24 > buffer.byteLength)
        throw new Error("Skin et géométrie ne correspondent pas.");
      const joints = new Uint16Array(buffer, cursor, n * 8); cursor += n * 16;
      const weights = new Uint8Array(buffer, cursor, n * 8); cursor += n * 8;
      for (let j = 0; j < n; j += 1) {
        let sum = 0;
        for (let k = 0; k < 8; k += 1) {
          const at = j * 8 + k; sum += weights[at];
          if (weights[at] && joints[at] >= header.bones.length) throw new Error("Indice d'os hors limites.");
        }
        if (!sum) throw new Error("Sommet sans poids de skin.");
      }
      return { joints, weights };
    });
    if (cursor !== buffer.byteLength) throw new Error("Taille de skin incohérente.");
    return { bones: header.bones, order, meshes };
  }

  function evaluate(curve, frame) {
    const f = curve.frames, v = curve.values, s = curve.slopes;
    if (f.length === 1 || frame <= f[0]) return v[0];
    if (frame >= f[f.length - 1]) return v[v.length - 1];
    let low = 0, high = f.length;
    while (low < high) { const mid = (low + high) >> 1; if (f[mid] <= frame) low = mid + 1; else high = mid; }
    const i = Math.min(f.length - 2, low - 1), d = f[i + 1] - f[i];
    if (!d) return v[i + 1];
    const t = (frame - f[i]) / d, t2 = t * t, t3 = t2 * t;
    return (2*t3 - 3*t2 + 1)*v[i] + (t3 - 2*t2 + t)*d*s[i]
      + (-2*t3 + 3*t2)*v[i + 1] + (t3 - t2)*d*s[i + 1];
  }

  function evaluateQuaternion(curve, frame) {
    const f = curve.frames, v = curve.values;
    if (f.length === 1 || frame <= f[0]) return [...v[0]];
    if (frame >= f[f.length - 1]) return [...v[v.length - 1]];
    let low = 0, high = f.length;
    while (low < high) { const mid = (low + high) >> 1; if (f[mid] <= frame) low = mid + 1; else high = mid; }
    const i = Math.min(f.length - 2, low - 1), d = f[i + 1] - f[i];
    if (!d) return [...v[i + 1]];
    const u = (frame - f[i]) / d, a = v[i]; let b = v[i + 1];
    let dot = a.reduce((sum, x, j) => sum + x*b[j], 0);
    if (dot < 0) { b = b.map((x) => -x); dot = -dot; }
    dot = Math.min(1, Math.max(0, dot));
    let q;
    if (dot > 0.9995) q = a.map((x, j) => x + u*(b[j] - x));
    else {
      const angle = Math.acos(dot), divisor = Math.sin(angle);
      q = a.map((x, j) => (Math.sin((1-u)*angle)*x + Math.sin(u*angle)*b[j]) / divisor);
    }
    const length = Math.hypot(...q);
    if (!Number.isFinite(length) || length < 1e-8) throw new PoseError("Quaternion d'animation invalide.");
    return q.map((x) => x / length);
  }

  function bind(skin, clip) {
    if (clip?.scale_mode === 0x300) throw new Error("Mode d'échelle Softimage FSKA non pris en charge.");
    if (!clip || !Number.isFinite(clip.frames) || clip.frames < 0 || clip.frames > 100000
        || !Number.isFinite(clip.fps) || clip.fps <= 0 || clip.fps > 240 || !Array.isArray(clip.bones))
      throw new Error("Clip d'animation invalide.");
    const lookup = new Map(skin.bones.map((bone, i) => [bone.name, i]));
    const tracks = new Array(skin.bones.length).fill(null); let matched = 0, animated = 0;
    for (const track of clip.bones) {
      if (!track || typeof track.name !== "string" || !track.base || !Array.isArray(track.curves))
        throw new Error("Piste d'animation invalide.");
      const i = lookup.get(track.name);
      if (i === undefined) continue;
      if (tracks[i]) throw new Error("Pistes d'animation dupliquées.");
      for (const [key, length] of [["scale", 3], ["translation", 3], ["rotation", 4]]) {
        if (track.base[key] !== undefined && !finiteArray(track.base[key], length)) throw new Error("Pose de base invalide.");
      }
      const mode = track.rotation_mode || "euler", targets = new Set(), rotationTargets = new Set();
      if (!["euler", "quaternion"].includes(mode)) throw new Error("Mode de rotation de clip inconnu.");
      if (mode === "quaternion" && track.base.rotation !== undefined) {
        const length = Math.hypot(...track.base.rotation);
        if (length < 0.999 || length > 1.001) throw new Error("Quaternion de base non unitaire.");
      }
      for (const c of track.curves) {
        const quaternion = c.target === "rotation" && c.interpolation === "slerp" && mode === "quaternion";
        const scalar = /^(scale|translation|rotation)\.[xyz]$/.test(c.target) && c.interpolation === "cubic"
          && (mode !== "quaternion" || !c.target.startsWith("rotation."));
        if ((!quaternion && !scalar) || targets.has(c.target)
            || !Array.isArray(c.frames) || !c.frames.length || c.frames.length > 250000
            || !c.frames.every(Number.isFinite) || c.frames.some((v, k) => k && v < c.frames[k - 1])
            || (quaternion && (!Array.isArray(c.values) || c.values.length !== c.frames.length
              || c.values.some((value) => !finiteArray(value, 4) || Math.hypot(...value) < 0.999 || Math.hypot(...value) > 1.001)))
            || (scalar && (!finiteArray(c.values, c.frames.length) || !finiteArray(c.slopes, c.frames.length))))
          throw new Error("Courbe de clip non prise en charge.");
        targets.add(c.target);
        if (c.target.startsWith("rotation.")) rotationTargets.add(c.target);
      }
      if (rotationTargets.size && rotationTargets.size < 3 && track.base.rotation === undefined
          && skin.bones[i].rotation_mode === "quaternion")
        throw new Error(`Rotation Euler partielle sans base pour l'os quaternion « ${track.name} ».`);
      tracks[i] = track; matched += 1; if (track.curves.length) animated += 1;
    }
    if (!matched || !animated) throw new Error("Aucun os animé de ce clip ne correspond au modèle.");
    return { skin, clip, tracks, matched, missing: clip.bones.length - matched };
  }

  function mul(a, b) {
    const out = new Array(12);
    for (let r = 0; r < 3; r += 1) for (let c = 0; c < 4; c += 1)
      out[4*r+c] = a[4*r]*b[c] + a[4*r+1]*b[4+c] + a[4*r+2]*b[8+c] + (c === 3 ? a[4*r+3] : 0);
    return out;
  }

  function localMatrix(s, r, t, parentScale, rotationMode) {
    let rot;
    if (rotationMode === "quaternion") {
      const length = Math.hypot(...r);
      if (!Number.isFinite(length) || length < 1e-8) throw new PoseError("Quaternion d'os invalide.");
      const [x, y, z, w] = r.map((value) => value / length);
      rot = [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)];
    } else {
      const [x, y, z] = r, cx = Math.cos(x), sx = Math.sin(x), cy = Math.cos(y), sy = Math.sin(y),
        cz = Math.cos(z), sz = Math.sin(z);
      rot = [cz*cy, cz*sy*sx - sz*cx, cz*sy*cx + sz*sx,
        sz*cy, sz*sy*sx + cz*cx, sz*sy*cx - cz*sx, -sy, cy*sx, cy*cx];
    }
    // T * inverse(parent local scale) * R * S: translation is still scaled by
    // the parent; rotation and scale get Maya-style segment compensation.
    const p = parentScale || [1, 1, 1];
    return [rot[0]*s[0]/p[0], rot[1]*s[1]/p[0], rot[2]*s[2]/p[0], t[0],
      rot[3]*s[0]/p[1], rot[4]*s[1]/p[1], rot[5]*s[2]/p[1], t[1],
      rot[6]*s[0]/p[2], rot[7]*s[1]/p[2], rot[8]*s[2]/p[2], t[2]];
  }

  function normalRows(m) {
    // Normalize the linear block before testing the determinant: a small but
    // uniformly scaled transform is invertible, unlike a flattened bone.
    const raw = [m[0],m[1],m[2],m[4],m[5],m[6],m[8],m[9],m[10]];
    const magnitude = Math.max(...raw.map(Math.abs));
    if (!Number.isFinite(magnitude) || magnitude === 0) throw new PoseError("Matrice d'animation singulière.");
    const [a,b,c,d,e,f,g,h,i] = raw.map((value) => value / magnitude);
    const det = a*(e*i-f*h) - b*(d*i-f*g) + c*(d*h-e*g);
    if (!Number.isFinite(det) || Math.abs(det) < 1e-12) throw new PoseError("Matrice d'animation singulière.");
    const q = 1/det/magnitude;
    return [(e*i-f*h)*q, (f*g-d*i)*q, (d*h-e*g)*q, 0,
      (c*h-b*i)*q, (a*i-c*g)*q, (b*g-a*h)*q, 0,
      (b*f-c*e)*q, (c*d-a*f)*q, (a*e-b*d)*q, 0];
  }

  function pose(binding, frame) {
    if (!Number.isFinite(frame)) throw new Error("Temps d'animation invalide.");
    const { skin, tracks } = binding, locals = [], worlds = [], result = new Float32Array(skin.bones.length * 24);
    for (let i = 0; i < skin.bones.length; i += 1) {
      const bone = skin.bones[i], track = tracks[i], base = track?.base || {};
      const scale = [...(base.scale || bone.scale)], translation = [...(base.translation || bone.translation)];
      let rotation = [...(base.rotation || bone.rotation)], rotationMode = bone.rotation_mode || "euler";
      if (base.rotation || track?.curves.some((c) => c.target === "rotation" || c.target.startsWith("rotation.")))
        rotationMode = track?.rotation_mode || "euler";
      for (const c of track?.curves || []) {
        if (c.target === "rotation") { rotation = evaluateQuaternion(c, frame); continue; }
        const [key, component] = c.target.split("."), axis = { x: 0, y: 1, z: 2 }[component];
        ({ scale, rotation, translation })[key][axis] = evaluate(c, frame);
      }
      if (![...scale, ...rotation, ...translation].every(Number.isFinite)) throw new PoseError("Transformation non finie.");
      locals.push({ scale, rotation, translation, rotationMode });
    }
    for (const i of skin.order) {
      const b = skin.bones[i], local = locals[i], p = b.parent;
      const compensate = p >= 0 && (b.ssc || tracks[i]?.ssc) ? locals[p].scale : null;
      if (compensate?.some((x) => Math.abs(x) < 1e-8))
        throw new PoseError(`Compensation d'échelle impossible pour « ${b.name} » : échelle parent nulle.`);
      const m = localMatrix(local.scale, local.rotation, local.translation, compensate, local.rotationMode);
      worlds[i] = p < 0 ? m : mul(worlds[p], m);
      const matrix = mul(worlds[i], b.inverse);
      if (!matrix.every(Number.isFinite)) throw new PoseError("Matrice d'animation non finie.");
      result.set(matrix, i*24); result.set(normalRows(matrix), i*24 + 12);
      if (!result.subarray(i*24, i*24 + 24).every(Number.isFinite))
        throw new PoseError("Matrice d'animation trop grande pour WebGL.");
    }
    return { palette: result, worlds };
  }

  function palette(binding, frame) { return pose(binding, frame).palette; }
  function restPose(skin) { return pose({ skin, tracks: [] }, 0); }

  return { PoseError, parseSkin, evaluate, evaluateQuaternion, bind, palette, pose, restPose };
})();
if (typeof window !== "undefined") window.HookyAnimation = HookyAnimation;
if (typeof module !== "undefined") module.exports = HookyAnimation;
