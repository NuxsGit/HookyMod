"use strict";

/* Vue WebGL2 à la demande : aucun modèle ni triangle n'est décimé. Le format
   H3D1 est produit localement par hookycreator/model_view.py. Les deux passes
   FSR1 facultatives sont les shaders MIT identifiés dans shaders/LICENSE-FSR1.txt. */

const VIEW_MAX_BYTES = 128 * 1024 * 1024;
const VIEW_MAX_VERTICES = 2_000_000;
const VIEW_MAX_TRIANGLES = 4_000_000;
const VIEW_MAX_TEXTURES = 16;
const VIEW_WIRE_BYTES = 64 * 1024 * 1024;
const FSR_SCALES = Object.freeze({ ultra: 0.77, quality: 0.67, balanced: 0.59, performance: 0.5 });
const VIEW_LAYER_SLOTS = Object.freeze(["head", "body", "legs", "right", "left"]);

// Keep the camera centred on the animated root without changing any model or
// attachment transforms. Ambiguous multi-root skeletons keep the static frame.
function uniqueRootIndex(bones) {
  let root = -1;
  for (let index = 0; index < bones.length; index += 1) {
    if (bones[index].parent !== -1) continue;
    if (root !== -1) return -1;
    root = index;
  }
  return root;
}
function followedCenter(center, rootIndex, pose) {
  if (!Number.isInteger(rootIndex) || rootIndex < 0 || !pose) return center;
  const m = pose.palette, at = rootIndex * 24;
  if (!m || m.length < at + 12) return center;
  const result = [0, 1, 2].map((row) => {
    const i = at + row * 4;
    return m[i] * center[0] + m[i+1] * center[1] + m[i+2] * center[2] + m[i+3];
  });
  return result.every(Number.isFinite) ? result : center;
}

// Validate again at the rendering boundary: old sessions and direct callers
// must never allocate arbitrary framebuffer sizes or silently call FSR 4.
function normalizeRenderSettings(settings = {}) {
  const pick = (value, choices, fallback) => choices.includes(String(value)) ? String(value) : fallback;
  const sharpness = Number(settings.fsr_sharpness);
  return {
    viewer_upscale: pick(settings.viewer_upscale, ["native", "fsr"], "native"),
    fsr_quality: pick(settings.fsr_quality, Object.keys(FSR_SCALES), "quality"),
    fsr_sharpness: Number.isFinite(sharpness) ? Math.max(0, Math.min(1, sharpness)) : 0.65,
    viewer_msaa: pick(settings.viewer_msaa, ["off", "2", "4"], "4"),
    viewer_dpr: Number(pick(settings.viewer_dpr, ["1", "2"], "2")),
    viewer_fps: Number(pick(settings.viewer_fps, ["15", "30", "60"], "60")),
    viewer_limit: Number(pick(settings.viewer_limit, ["800", "1200", "1600"], "1600")),
  };
}

// Bone world (row-major 3x4) × local translation/uniform scale, converted to
// WebGL's column-major matrices. No guessed game-specific attachment points.
function attachmentMatrices(world, transform = {}) {
  const offset = Array.isArray(transform.offset) ? transform.offset : [0, 0, 0];
  const rotation = Array.isArray(transform.rotation) ? transform.rotation : [0, 0, 0];
  const scale = transform.scale === undefined ? 1 : Number(transform.scale);
  if (!Array.isArray(world) || world.length !== 12 || !world.every(Number.isFinite)
      || offset.length !== 3 || !offset.every((x) => Number.isFinite(x) && Math.abs(x) <= 10000)
      || rotation.length !== 3 || !rotation.every((x) => Number.isFinite(x) && Math.abs(x) <= 3600)
      || !Number.isFinite(scale) || scale < 0.001 || scale > 1000) {
    throw new Error("Transformation d'équipement invalide.");
  }
  const [x,y,z] = rotation.map((angle) => angle * Math.PI / 180);
  const cx = Math.cos(x), sx = Math.sin(x), cy = Math.cos(y), sy = Math.sin(y), cz = Math.cos(z), sz = Math.sin(z);
  const r = [cz*cy, cz*sy*sx - sz*cx, cz*sy*cx + sz*sx,
    sz*cy, sz*sy*sx + cz*cx, sz*sy*cx - cz*sx, -sy, cy*sx, cy*cx];
  const m = new Array(12);
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1)
      m[row*4+col] = scale * (world[row*4]*r[col] + world[row*4+1]*r[3+col] + world[row*4+2]*r[6+col]);
    m[row*4+3] = world[row*4+3] + world[row*4]*offset[0] + world[row*4+1]*offset[1] + world[row*4+2]*offset[2];
  }
  const [a,b,c,,d,e,f,,g,h,i] = m;
  const det = a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g);
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) throw new Error("Os d'attache à échelle nulle.");
  const q = 1 / det;
  // Columns of the inverse transpose, not the original transform.
  const normal = new Float32Array([(e*i-f*h)*q, (c*h-b*i)*q, (b*f-c*e)*q,
    (f*g-d*i)*q, (a*i-c*g)*q, (c*d-a*f)*q,
    (d*h-e*g)*q, (b*g-a*h)*q, (a*e-b*d)*q]);
  return { matrix: new Float32Array([m[0],m[4],m[8],0, m[1],m[5],m[9],0,
    m[2],m[6],m[10],0, m[3],m[7],m[11],1]), normal };
}

function parseModelGeometry(buffer) {
  if (new Uint8Array(new Uint32Array([1]).buffer)[0] !== 1) {
    throw new Error("Ordre d'octets non pris en charge par la vue 3D.");
  }
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 12 || buffer.byteLength > VIEW_MAX_BYTES) {
    throw new Error("Géométrie 3D invalide ou trop volumineuse.");
  }
  const bytes = new Uint8Array(buffer);
  if (String.fromCharCode(...bytes.subarray(0, 4)) !== "H3D1") throw new Error("Format 3D inconnu.");
  const size = new DataView(buffer).getUint32(4, true);
  if (size > 4 * 1024 * 1024 || 8 + size > buffer.byteLength) throw new Error("En-tête 3D invalide.");
  let header;
  try { header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(8, 8 + size))); }
  catch (_) { throw new Error("En-tête 3D illisible."); }
  if (header.version !== 1 || !Array.isArray(header.meshes) || !header.meshes.length || header.meshes.length > 2048) {
    throw new Error("Liste de maillages 3D invalide.");
  }
  let cursor = Math.ceil((8 + size) / 4) * 4;
  let vertices = 0, triangles = 0;
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  function take(Type, count) {
    const length = count * 4;
    if (!Number.isSafeInteger(count) || count < 0 || cursor + length > buffer.byteLength) {
      throw new Error("Tampon 3D tronqué.");
    }
    const out = new Type(buffer, cursor, count);
    cursor += length;
    return out;
  }
  const meshes = header.meshes.map((entry) => {
    const n = entry.vertices, f = entry.triangles;
    if (!Number.isSafeInteger(n) || !Number.isSafeInteger(f) || n < 1 || f < 1
        || typeof entry.normals !== "boolean" || typeof entry.uvs !== "boolean") {
      throw new Error("Compte ou attribut 3D invalide.");
    }
    vertices += n; triangles += f;
    if (vertices > VIEW_MAX_VERTICES || triangles > VIEW_MAX_TRIANGLES) throw new Error("Modèle 3D trop volumineux.");
    const positions = take(Float32Array, n * 3);
    const normals = entry.normals ? take(Float32Array, n * 3) : null;
    const uvs = entry.uvs ? take(Float32Array, n * 2) : null;
    const indices = take(Uint32Array, f * 3);
    for (let i = 0; i < positions.length; i += 1) {
      const value = positions[i], axis = i % 3;
      if (!Number.isFinite(value)) throw new Error("Position 3D non finie.");
      low[axis] = Math.min(low[axis], value); high[axis] = Math.max(high[axis], value);
    }
    if (normals && normals.some((x) => !Number.isFinite(x))) throw new Error("Normale 3D non finie.");
    if (uvs && uvs.some((x) => !Number.isFinite(x))) throw new Error("UV 3D non finie.");
    if (indices.some((x) => x >= n)) throw new Error("Indice de triangle hors limites.");
    const texture = entry.texture && typeof entry.texture.path === "string"
      && Number.isSafeInteger(entry.texture.index) && entry.texture.index >= 0
      ? { path: entry.texture.path, index: entry.texture.index } : null;
    return { name: String(entry.name || ""), material: String(entry.material || ""),
      positions, normals, uvs, indices, texture };
  });
  if (cursor !== buffer.byteLength) throw new Error("Taille de géométrie 3D incohérente.");
  const center = low.map((v, i) => (v + high[i]) * 0.5);
  const extent = low.map((v, i) => (high[i] - v) * 0.5);
  const radius = Math.hypot(...extent) || 1;
  return { meshes, vertices, triangles, center, extent, radius };
}

const VIEW_VERTEX = `#version 300 es
precision highp float;
layout(location=0) in vec3 a_position;
layout(location=1) in vec3 a_normal;
layout(location=2) in vec2 a_uv;
layout(location=3) in uvec4 a_joints0;
layout(location=4) in uvec4 a_joints1;
layout(location=5) in vec4 a_weights0;
layout(location=6) in vec4 a_weights1;
uniform bool u_animated;
uniform highp sampler2D u_skin;
uniform bool u_attached;
uniform mat4 u_attachment;
uniform mat3 u_attachmentNormal;
uniform vec3 u_center;
uniform float u_radius;
uniform vec2 u_angles;
uniform vec2 u_fit;
out vec3 v_position;
out vec3 v_normal;
out vec2 v_uv;
vec3 turn(vec3 p) {
  float cy=cos(u_angles.x), sy=sin(u_angles.x), cp=cos(u_angles.y), sp=sin(u_angles.y);
  float x=p.x*cy+p.z*sy, z=-p.x*sy+p.z*cy;
  return vec3(x, p.y*cp-z*sp, p.y*sp+z*cp);
}
void influence(uint joint, float weight, inout vec3 p, inout vec3 n) {
  if (weight <= 0.0) return;
  int j=int(joint);
  vec4 p4=vec4(a_position,1.0), n4=vec4(a_normal,0.0);
  p+=weight*vec3(dot(texelFetch(u_skin,ivec2(0,j),0),p4),
                 dot(texelFetch(u_skin,ivec2(1,j),0),p4),
                 dot(texelFetch(u_skin,ivec2(2,j),0),p4));
  n+=weight*vec3(dot(texelFetch(u_skin,ivec2(3,j),0),n4),
                 dot(texelFetch(u_skin,ivec2(4,j),0),n4),
                 dot(texelFetch(u_skin,ivec2(5,j),0),n4));
}
void main() {
  vec3 position=a_position, normal=a_normal;
  if (u_animated) {
    position=vec3(0.0); normal=vec3(0.0);
    float total=0.0;
    for (int i=0;i<4;i++) {
      influence(a_joints0[i],a_weights0[i],position,normal); total+=a_weights0[i];
      influence(a_joints1[i],a_weights1[i],position,normal); total+=a_weights1[i];
    }
    position/=max(total,0.00001); normal/=max(total,0.00001);
  }
  if (u_attached) {
    position=(u_attachment*vec4(position,1.0)).xyz;
    normal=u_attachmentNormal*normal;
  }
  v_position=turn((position-u_center)/u_radius);
  v_normal=turn(normal);
  v_uv=a_uv;
  gl_Position=vec4(v_position.xy*u_fit, -v_position.z*0.25, 1.0);
}`;

const VIEW_FRAGMENT = `#version 300 es
precision highp float;
in vec3 v_position;
in vec3 v_normal;
in vec2 v_uv;
uniform vec3 u_color;
uniform sampler2D u_texture;
uniform bool u_textured;
uniform bool u_smooth;
uniform bool u_wire;
out vec4 color;
void main() {
  if (u_wire) { color=vec4(0.035,0.045,0.06,1.0); return; }
  vec3 normal = u_smooth ? normalize(v_normal) : normalize(cross(dFdx(v_position),dFdy(v_position)));
  if (!gl_FrontFacing) normal=-normal;
  float light=0.35+0.65*max(dot(normal,normalize(vec3(0.35,0.55,0.76))),0.0);
  vec4 base=u_textured ? texture(u_texture,v_uv) : vec4(u_color,1.0);
  if (u_textured && base.a < 0.1) discard;
  color=vec4(base.rgb*light,1.0);
}`;

function viewProgram(gl, vertex, fragment) {
  function shader(kind, source) {
    const item = gl.createShader(kind);
    if (!item) throw new Error("Création d'un shader WebGL impossible.");
    gl.shaderSource(item, source); gl.compileShader(item);
    if (!gl.getShaderParameter(item, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(item) || "compilation impossible";
      gl.deleteShader(item); throw new Error(message);
    }
    return item;
  }
  const vs = shader(gl.VERTEX_SHADER, vertex);
  let fs;
  try { fs = shader(gl.FRAGMENT_SHADER, fragment); }
  catch (error) { gl.deleteShader(vs); throw error; }
  const program = gl.createProgram();
  if (!program) { gl.deleteShader(vs); gl.deleteShader(fs); throw new Error("Programme WebGL indisponible."); }
  gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
  gl.deleteShader(vs); gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program) || "liaison impossible";
    gl.deleteProgram(program); throw new Error(message);
  }
  return program;
}

class ModelViewport {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = options;
    this.gl = canvas.getContext("webgl2", { alpha: false, antialias: true, powerPreference: "high-performance" });
    if (!this.gl) throw new Error("WebGL2 n'est pas disponible sur cette machine.");
    this.abort = new AbortController();
    this.listeners = [];
    this.disposed = false;
    this.contextReleased = false;
    this.ready = false;
    this.mode = "native";
    this.modeSerial = 0;
    this.renderSettings = normalizeRenderSettings(options.renderSettings);
    this.lastDraw = null;
    this.flat = false; this.wire = false; this.textured = options.textured !== false;
    this.yaw = 0.7; this.pitch = -0.35; this.zoom = 1;
    this.parts = []; this.textureMap = new Map(); this.pendingImages = [];
    this.layers = new Map(); this.pendingLayers = new Set(); this.layerSerial = new Map();
    this.layerRequests = new Map(); this.baseVisible = true; this.followRoot = false; this.rootIndex = -1;
    this.notes = [];
    this.scene = viewProgram(this.gl, VIEW_VERTEX, VIEW_FRAGMENT);
    this.fullscreenVao = this.gl.createVertexArray();
    if (!this.fullscreenVao) { this.gl.deleteProgram(this.scene); throw new Error("WebGL2 indisponible."); }
    this.listen(canvas, "webglcontextlost", (event) => {
      event.preventDefault();
      if (!this.disposed) this.options.onFailure?.("Contexte WebGL perdu.");
    });
    this.listen(canvas, "pointerdown", (event) => {
      if (event.button !== 0) return;
      this.drag = { x: event.clientX, y: event.clientY, id: event.pointerId };
      canvas.setPointerCapture?.(event.pointerId); event.preventDefault();
    });
    this.listen(canvas, "pointermove", (event) => {
      if (!this.drag || event.pointerId !== this.drag.id) return;
      this.yaw += (event.clientX - this.drag.x) * 0.012;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch + (event.clientY - this.drag.y) * 0.012));
      this.drag.x = event.clientX; this.drag.y = event.clientY; this.schedule();
    });
    const release = (event) => { if (this.drag?.id === event.pointerId) this.drag = null; };
    this.listen(canvas, "pointerup", release); this.listen(canvas, "pointercancel", release);
    this.listen(canvas, "wheel", (event) => {
      event.preventDefault(); this.zoom = Math.max(0.15, Math.min(8, this.zoom * Math.exp(-event.deltaY * 0.001)));
      this.schedule();
    }, { passive: false });
    this.listen(canvas, "keydown", (event) => {
      // Quitter explicitement sans déclencher le raccourci global qui ferme
      // la fiche (certaines webviews ne traitent pas Échap automatiquement).
      if (event.key === "Escape" && document.fullscreenElement === canvas) {
        event.preventDefault(); event.stopPropagation();
        document.exitFullscreen?.().catch(() => {}); return;
      }
      if (event.key === "ArrowLeft") this.yaw -= 0.15;
      else if (event.key === "ArrowRight") this.yaw += 0.15;
      else if (event.key === "ArrowUp") this.pitch = Math.max(-1.5, this.pitch - 0.15);
      else if (event.key === "ArrowDown") this.pitch = Math.min(1.5, this.pitch + 0.15);
      else if (event.key === "+" || event.key === "=") this.zoom = Math.min(8, this.zoom * 1.15);
      else if (event.key === "-") this.zoom = Math.max(0.15, this.zoom / 1.15);
      else if (event.key === "Home") { this.reset(); event.preventDefault(); return; }
      else return;
      event.preventDefault(); this.schedule();
    });
    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(() => this.schedule());
      this.resizeObserver.observe(canvas);
    } else this.listen(window, "resize", () => this.schedule());
  }

  listen(node, name, handler, options) {
    node.addEventListener(name, handler, options);
    this.listeners.push([node, name, handler, options]);
  }

  async fetchGeometry(url, signal = this.abort.signal) {
    const response = await fetch(url, { signal, cache: "no-store" });
    if (!response.ok) {
      let error = "Chargement de la géométrie impossible.";
      try { error = (await response.json()).error || error; } catch (_) { /* réponse non JSON */ }
      throw new Error(error);
    }
    if (Number(response.headers.get("Content-Length")) > VIEW_MAX_BYTES)
      throw new Error("Géométrie supérieure à 128 Mio ; aucune face supprimée.");
    const buffer = await response.arrayBuffer();
    return { model: parseModelGeometry(buffer), bytes: buffer.byteLength };
  }

  uploadGeometry(layer, model, bytes) {
    layer.model = model; layer.geometryBytes = bytes; layer.parts = [];
    const gl = this.gl, slots = new Map();
    const colors = [[.78,.81,.86], [.85,.72,.45], [.56,.78,.62], [.72,.59,.82], [.54,.72,.88], [.88,.62,.58]];
    try {
      for (const mesh of model.meshes) {
        const vao = gl.createVertexArray();
        if (!vao) throw new Error("Mémoire WebGL insuffisante.");
        const part = { vao, buffers: [], mesh, color: colors[(slots.has(mesh.material)
          ? slots.get(mesh.material) : (slots.set(mesh.material, slots.size), slots.size - 1)) % colors.length] };
        layer.parts.push(part);
        gl.bindVertexArray(vao);
        const attribute = (location, data, width) => {
          if (!data) { gl.disableVertexAttribArray(location); return; }
          const buffer = gl.createBuffer();
          if (!buffer) throw new Error("Mémoire WebGL insuffisante.");
          part.buffers.push(buffer); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
          gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, width, gl.FLOAT, false, 0, 0);
        };
        attribute(0, mesh.positions, 3); attribute(1, mesh.normals, 3); attribute(2, mesh.uvs, 2);
        const indices = gl.createBuffer();
        if (!indices) throw new Error("Mémoire WebGL insuffisante.");
        part.indexBuffer = indices; part.buffers.push(indices);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indices); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
        if (gl.getError() !== gl.NO_ERROR) throw new Error("Mémoire WebGL insuffisante pour le modèle entier.");
      }
      layer.ready = true;
    } catch (error) {
      this.deleteLayerResources(layer);
      throw error;
    } finally { gl.bindVertexArray(null); }
  }

  async load(url) {
    if (this.ready) throw new Error("Cette vue contient déjà un modèle.");
    const { model, bytes } = await this.fetchGeometry(url);
    if (this.disposed) return;
    this.uploadGeometry(this, model, bytes);
    if (this.textured) this.loadTextures();
    this.updateStatus(); this.schedule();
  }

  allLayers() { return [this, ...this.layers.values()]; }

  deleteLayerResources(layer) {
    layer.removed = true;
    const gl = this.gl;
    for (const part of layer.parts || []) {
      for (const buffer of part.buffers) gl.deleteBuffer(buffer);
      gl.deleteVertexArray(part.vao);
    }
    layer.parts = [];
    if (layer.skinTexture) gl.deleteTexture(layer.skinTexture);
    layer.skinTexture = null; layer.skin = null; layer.binding = null; layer.ready = false;
  }

  updateStatus() {
    if (!this.ready || this.disposed) return;
    const mode = this.mode === "fsr"
      ? `FSR 1 expérimental · ${Math.round(FSR_SCALES[this.renderSettings.fsr_quality] * 100)} %`
      : "WebGL2 · résolution native";
    const triangles = this.allLayers().reduce((sum, layer) => sum + layer.model.triangles, 0);
    this.options.onStatus?.(`${triangles.toLocaleString("fr-FR")} triangles · ${mode}`
      + (this.outputClamped ? ` · image plafonnée à ${this.renderSettings.viewer_limit} px / densité ×${this.renderSettings.viewer_dpr}` : "")
      + (this.notes.length ? ` · ${this.notes.join(" · ")}` : ""));
  }

  addNote(note) {
    if (!this.notes.includes(note)) this.notes.push(note);
    this.updateStatus();
  }

  loadTextures() {
    if (this.disposed || !this.ready || !this.textured) return;
    for (const part of this.allLayers().flatMap((layer) => layer.parts)) {
      const binding = part.mesh.texture;
      if (!binding || !part.mesh.uvs) continue;
      const key = `${binding.path}\n${binding.index}`;
      if (this.textureMap.has(key)) continue;
      if (this.textureMap.size >= VIEW_MAX_TEXTURES) {
        this.addNote(`plus de ${VIEW_MAX_TEXTURES} textures : certaines non affichées`); break;
      }
      const image = new Image(), item = { texture: null, image };
      this.textureMap.set(key, item);
      this.pendingImages.push(image);
      image.onload = () => {
        this.pendingImages = this.pendingImages.filter((entry) => entry !== image);
        item.image = null;
        if (this.disposed || this.textureMap.get(key) !== item || this.gl.isContextLost()) return;
        const gl = this.gl, texture = gl.createTexture();
        if (!texture) { this.addNote("mémoire texture insuffisante"); return; }
        try {
          gl.bindTexture(gl.TEXTURE_2D, texture);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
          if (gl.getError() !== gl.NO_ERROR) throw new Error("texture GPU");
          item.texture = texture; this.schedule();
        } catch (_) { gl.deleteTexture(texture); this.addNote("texture indisponible"); }
      };
      image.onerror = () => {
        this.pendingImages = this.pendingImages.filter((entry) => entry !== image);
        item.image = null;
        if (!this.disposed && this.textureMap.get(key) === item) this.addNote("texture indisponible");
      };
      const query = new URLSearchParams({ path: binding.path, index: String(binding.index), max: "1024",
        rev: String(this.options.revision || "") });
      image.src = `/api/texture/png?${query}`;
    }
    if (this.textureMap.size) this.addNote("albédos limités à 1 024 px dans la vue");
  }

  pruneTextures() {
    const needed = new Set(this.allLayers().flatMap((layer) => layer.parts)
      .filter((part) => part.mesh.texture && part.mesh.uvs)
      .map((part) => `${part.mesh.texture.path}\n${part.mesh.texture.index}`));
    for (const [key, item] of this.textureMap) {
      if (needed.has(key)) continue;
      if (item.texture) this.gl.deleteTexture(item.texture);
      if (item.image) {
        item.image.onload = item.image.onerror = null; item.image.src = "";
        this.pendingImages = this.pendingImages.filter((image) => image !== item.image);
      }
      this.textureMap.delete(key);
    }
  }

  async prepareSkin(url, layer = this) {
    if (layer.skin) return layer.skin;
    if (layer.skinLoading) return layer.skinLoading;
    if (!layer.ready) throw new Error("La géométrie 3D n'est pas encore chargée.");
    layer.skinLoading = (async () => {
      const response = await fetch(url, { signal: layer.controller?.signal || this.abort.signal, cache: "no-store" });
      if (!response.ok) {
        let message = "Skin du modèle indisponible.";
        try { message = (await response.json()).error || message; } catch (_) { /* non JSON */ }
        throw new Error(message);
      }
      if (Number(response.headers.get("Content-Length")) > 64 * 1024 * 1024) throw new Error("Skin trop volumineux.");
      const buffer = await response.arrayBuffer();
      if (this.disposed || layer.removed) throw new Error("Vue 3D fermée.");
      const groups = new Set([...this.allLayers(), ...this.pendingLayers]);
      const used = [...groups].reduce((sum, group) => sum + (group === layer ? 0 : (group.skinBytes || 0)), 0);
      if (used + buffer.byteLength > 64 * 1024 * 1024) throw new Error("Skins de la composition supérieurs à 64 Mio.");
      const skin = HookyAnimation.parseSkin(buffer, layer.model);
      const gl = this.gl, created = [];
      let texture = null;
      try {
        if (skin.bones.length > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new Error("Squelette trop grand pour WebGL.");
        for (let i = 0; i < layer.parts.length; i += 1) {
          const part = layer.parts[i], data = skin.meshes[i];
          gl.bindVertexArray(part.vao);
          const joints = gl.createBuffer(), weights = gl.createBuffer();
          if (joints) created.push(joints);
          if (weights) created.push(weights);
          if (!joints || !weights) throw new Error("Mémoire GPU insuffisante pour le skin.");
          gl.bindBuffer(gl.ARRAY_BUFFER, joints); gl.bufferData(gl.ARRAY_BUFFER, data.joints, gl.STATIC_DRAW);
          gl.enableVertexAttribArray(3); gl.vertexAttribIPointer(3, 4, gl.UNSIGNED_SHORT, 16, 0);
          gl.enableVertexAttribArray(4); gl.vertexAttribIPointer(4, 4, gl.UNSIGNED_SHORT, 16, 8);
          gl.bindBuffer(gl.ARRAY_BUFFER, weights); gl.bufferData(gl.ARRAY_BUFFER, data.weights, gl.STATIC_DRAW);
          gl.enableVertexAttribArray(5); gl.vertexAttribPointer(5, 4, gl.UNSIGNED_BYTE, true, 8, 0);
          gl.enableVertexAttribArray(6); gl.vertexAttribPointer(6, 4, gl.UNSIGNED_BYTE, true, 8, 4);
          part.skinBuffers = [joints, weights];
        }
        texture = gl.createTexture();
        if (!texture) throw new Error("Mémoire GPU insuffisante pour les os.");
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 6, skin.bones.length, 0, gl.RGBA, gl.FLOAT,
          new Float32Array(skin.bones.length * 24));
        if (gl.getError() !== gl.NO_ERROR) throw new Error("Allocation GPU du skin impossible.");
        for (const part of layer.parts) part.buffers.push(...part.skinBuffers);
        layer.rootIndex = uniqueRootIndex(skin.bones);
        layer.skinTexture = texture; layer.skin = skin; layer.skinBytes = buffer.byteLength;
        return skin;
      } catch (error) {
        for (const part of layer.parts) {
          gl.bindVertexArray(part.vao);
          for (let i = 3; i <= 6; i += 1) gl.disableVertexAttribArray(i);
          delete part.skinBuffers;
        }
        for (const buffer of created) gl.deleteBuffer(buffer);
        if (texture) gl.deleteTexture(texture);
        throw error;
      } finally { gl.bindVertexArray(null); gl.activeTexture(gl.TEXTURE0); }
    })();
    try { return await layer.skinLoading; }
    finally { layer.skinLoading = null; }
  }

  layerReports() {
    return [...this.layers.values()].map((layer) => ({
      slot: layer.slot, mode: layer.mode, bone: layer.bone || "", warning: layer.runtimeWarning || layer.warning || "",
      matched: layer.binding?.matched || 0, missing: layer.binding?.missing || 0,
    }));
  }

  updateLayerBinding(layer, clip) {
    layer.binding = null; layer.warning = "";
    if (layer.mode !== "skin" || !clip) return;
    try {
      const binding = HookyAnimation.bind(layer.skin, clip);
      HookyAnimation.palette(binding, 0);
      layer.binding = binding;
      if (binding.missing) layer.warning = `${binding.missing} piste(s) sans os correspondant`;
    } catch (error) {
      layer.warning = `Tenue statique : ${error.message}`;
    }
  }

  async setLayer(slot, { geometryUrl, skinUrl, mode = "skin", bone = "", offset = [0,0,0], rotation = [0,0,0], scale = 1 }) {
    if (!this.ready || this.disposed) throw new Error("Ouvre d'abord un modèle de base.");
    if (!VIEW_LAYER_SLOTS.includes(slot)) throw new Error("Emplacement d'équipement invalide.");
    // Even an invalid new selection cancels an older in-flight replacement;
    // otherwise that older result could silently win after showing an error.
    const serial = (this.layerSerial.get(slot) || 0) + 1;
    this.layerSerial.set(slot, serial);
    this.layerRequests.get(slot)?.abort();
    const controller = new AbortController(); this.layerRequests.set(slot, controller);
    let layer = null, committed = false;
    const current = () => !this.disposed && !controller.signal.aborted && this.layerSerial.get(slot) === serial;
    try {
      if (!["skin", "bone", "static"].includes(mode) || typeof geometryUrl !== "string"
          || !geometryUrl || (mode === "skin" && !skinUrl)) throw new Error("Équipement invalide.");
      if (mode === "bone" && (!this.skin || !this.skin.bones.some((item) => item.name === bone)))
        throw new Error("Choisis un os reconnu du modèle de base.");
      // Validate before network work; the transform may later use an animated bone.
      attachmentMatrices([1,0,0,0, 0,1,0,0, 0,0,1,0], { offset, rotation, scale });
      const { model, bytes } = await this.fetchGeometry(geometryUrl, controller.signal);
      if (!current()) return null;
      const groups = new Set([...this.allLayers(), ...this.pendingLayers]);
      const totals = [...groups].reduce((acc, group) => {
        acc.bytes += group.geometryBytes || 0; acc.vertices += group.model?.vertices || 0;
        acc.triangles += group.model?.triangles || 0; return acc;
      }, { bytes, vertices: model.vertices, triangles: model.triangles });
      if (totals.bytes > VIEW_MAX_BYTES || totals.vertices > VIEW_MAX_VERTICES || totals.triangles > VIEW_MAX_TRIANGLES)
        throw new Error("Composition trop volumineuse : aucune géométrie n'a été supprimée.");
      layer = { slot, mode, bone, offset: [...offset], rotation: [...rotation], scale, controller, parts: [] };
      this.uploadGeometry(layer, model, bytes); this.pendingLayers.add(layer);
      if (mode === "skin") await this.prepareSkin(skinUrl, layer);
      if (!current()) return null;
      this.updateLayerBinding(layer, this.animation?.binding.clip);
      const old = this.layers.get(slot);
      this.layers.set(slot, layer); this.pendingLayers.delete(layer); committed = true;
      if (old) this.deleteLayerResources(old);
      this.wire = false; // no uninitialized wire buffers for a freshly added layer
      this.pruneTextures(); if (this.textured) this.loadTextures();
      this.updateStatus(); this.schedule(); this.options.onLayersChanged?.(this.layerReports());
      return this.layerReports().find((item) => item.slot === slot);
    } catch (error) {
      if (!current()) return null;
      throw error;
    } finally {
      if (layer && !committed) { this.pendingLayers.delete(layer); this.deleteLayerResources(layer); }
      if (this.layerRequests.get(slot) === controller) this.layerRequests.delete(slot);
    }
  }

  cancelLayer(slot) {
    if (!VIEW_LAYER_SLOTS.includes(slot)) return;
    this.layerSerial.set(slot, (this.layerSerial.get(slot) || 0) + 1);
    this.layerRequests.get(slot)?.abort(); this.layerRequests.delete(slot);
  }

  removeLayer(slot) {
    if (!VIEW_LAYER_SLOTS.includes(slot)) return;
    this.cancelLayer(slot);
    const layer = this.layers.get(slot);
    if (layer) { this.layers.delete(slot); this.deleteLayerResources(layer); }
    this.pruneTextures(); if (this.textured) this.loadTextures();
    this.updateStatus(); this.schedule(); this.options.onLayersChanged?.(this.layerReports());
  }

  setBaseVisible(visible) { this.baseVisible = Boolean(visible); this.schedule(); }
  setFollowRoot(enabled) { this.followRoot = Boolean(enabled); this.schedule(); }

  async setAnimation(url, clip) {
    const serial = this.animationSerial = (this.animationSerial || 0) + 1;
    const skin = await this.prepareSkin(url);
    if (this.disposed || serial !== this.animationSerial) return null;
    const binding = HookyAnimation.bind(skin, clip);
    // Validate frame zero before changing the current preview.
    HookyAnimation.palette(binding, 0);
    this.animation = { binding, frame: 0, playing: true, loop: Boolean(clip.loop), last: null };
    for (const layer of this.layers.values()) this.updateLayerBinding(layer, clip);
    this.options.onLayersChanged?.(this.layerReports());
    this.schedule();
    return { matched: binding.matched, missing: binding.missing, layers: this.layerReports() };
  }

  stopAnimation() {
    this.animationSerial = (this.animationSerial || 0) + 1;
    this.animation = null;
    for (const layer of this.layers.values()) this.updateLayerBinding(layer, null);
    this.options.onLayersChanged?.(this.layerReports());
    this.schedule();
  }
  playAnimation(playing) {
    if (!this.animation) return;
    if (playing && this.animation.frame >= this.animation.binding.clip.frames) this.animation.frame = 0;
    this.animation.playing = Boolean(playing); this.animation.last = null; this.schedule();
  }
  seekAnimation(frame) {
    if (!this.animation || !Number.isFinite(frame)) return;
    this.animation.frame = Math.max(0, Math.min(this.animation.binding.clip.frames, frame));
    this.animation.last = null; this.schedule();
  }
  loopAnimation(enabled) { if (this.animation) this.animation.loop = Boolean(enabled); }

  setTextured(enabled) { this.textured = Boolean(enabled); if (this.textured) this.loadTextures(); this.schedule(); }
  setFlat(enabled) { this.flat = Boolean(enabled); this.schedule(); }
  setWire(enabled) {
    if (!enabled) { this.wire = false; this.schedule(); return true; }
    const groups = this.allLayers();
    if (!this.ready || groups.reduce((sum, group) => sum + group.model.triangles, 0) * 24 > VIEW_WIRE_BYTES) {
      this.addNote("fil de fer indisponible au-delà de 64 Mio d'indices"); return false;
    }
    const gl = this.gl, created = [];
    try {
      for (const part of groups.flatMap((group) => group.parts)) {
        if (part.wireBuffer) continue;
        const source = part.mesh.indices, lines = new Uint32Array(source.length * 2);
        for (let i = 0, j = 0; i < source.length; i += 3) {
          lines[j++] = source[i]; lines[j++] = source[i+1];
          lines[j++] = source[i+1]; lines[j++] = source[i+2];
          lines[j++] = source[i+2]; lines[j++] = source[i];
        }
        const buffer = gl.createBuffer();
        if (!buffer) throw new Error("mémoire");
        created.push([part, buffer]); gl.bindVertexArray(part.vao);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, buffer); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, lines, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.indexBuffer);
        if (gl.getError() !== gl.NO_ERROR) throw new Error("mémoire");
      }
      for (const [part, buffer] of created) { part.wireBuffer = buffer; part.buffers.push(buffer); }
      gl.bindVertexArray(null); this.wire = true; this.schedule(); return true;
    } catch (_) {
      for (const [, buffer] of created) gl.deleteBuffer(buffer);
      gl.bindVertexArray(null); this.addNote("fil de fer indisponible : mémoire GPU insuffisante"); return false;
    }
  }

  reset() { this.yaw = 0.7; this.pitch = -0.35; this.zoom = 1; this.schedule(); }

  configure(settings) {
    const previous = this.renderSettings, next = normalizeRenderSettings(settings);
    this.renderSettings = next;
    if (previous.fsr_quality !== next.fsr_quality || previous.viewer_msaa !== next.viewer_msaa
        || previous.viewer_dpr !== next.viewer_dpr || previous.viewer_limit !== next.viewer_limit) {
      this.releaseTargets();
    }
    this.lastDraw = null;
    this.updateStatus(); this.schedule();
    return this.setMode(next.viewer_upscale);
  }

  async setMode(mode) {
    const serial = ++this.modeSerial;
    if (mode !== "fsr") { this.mode = "native"; this.releaseTargets(); this.updateStatus(); this.schedule(); return; }
    this.notes = this.notes.filter((note) => !note.startsWith("FSR 1 indisponible :"));
    try {
      if (!this.fsr) {
        const files = ["fullscreen.vert.glsl", "common.glsl", "easu.frag.glsl", "rcas.frag.glsl"];
        const sources = await Promise.all(files.map(async (file) => {
          const response = await fetch(`/static/shaders/${file}`, { signal: this.abort.signal });
          if (!response.ok) throw new Error(`shader ${file} indisponible`);
          return response.text();
        }));
        if (this.disposed || serial !== this.modeSerial) return;
        const [vertex, common, easu, rcas] = sources;
        const prefix = "#version 300 es\nprecision highp float;\nprecision highp int;\n";
        const first = viewProgram(this.gl, vertex, prefix + common + "\n" + easu);
        let second;
        try { second = viewProgram(this.gl, vertex, prefix + common + "\n" + rcas); }
        catch (error) { this.gl.deleteProgram(first); throw error; }
        this.fsr = { easu: first, rcas: second };
      }
      if (this.disposed || serial !== this.modeSerial) return;
      this.mode = "fsr"; this.updateStatus(); this.schedule();
    } catch (error) {
      if (this.disposed || serial !== this.modeSerial) return;
      this.mode = "native"; this.options.onModeFallback?.();
      this.addNote(`FSR 1 indisponible : ${error.message}`); this.schedule();
    }
  }

  schedule() {
    if (this.disposed || !this.ready || this.frame) return;
    this.frame = requestAnimationFrame((time) => {
      this.frame = 0;
      if (this.disposed || !this.canvas.isConnected) return;
      const anim = this.animation;
      // Throttle animated rendering, not the animation clock. Interactions and
      // paused scrubbing still repaint immediately.
      if (anim?.playing && this.lastDraw !== null
          && time - this.lastDraw < 1000 / this.renderSettings.viewer_fps - 0.5) {
        this.schedule(); return;
      }
      this.lastDraw = time;
      if (anim && anim.playing) {
        if (anim.last !== null) anim.frame += Math.min(0.1, Math.max(0, (time - anim.last) / 1000)) * anim.binding.clip.fps;
        anim.last = time;
        const end = anim.binding.clip.frames;
        if (end && anim.frame >= end) {
          if (anim.loop) anim.frame %= end;
          else { anim.frame = end; anim.playing = false; }
        } else if (!end) anim.playing = false;
      }
      this.draw();
      if (this.animation) {
        this.options.onAnimationFrame?.(this.animation.frame, this.animation.playing);
        if (this.animation.playing) this.schedule();
      }
    });
  }

  sizeCanvas() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    const { viewer_dpr: maxDpr, viewer_limit: limit } = this.renderSettings;
    const actualDpr = Math.max(1, window.devicePixelRatio || 1), dpr = Math.min(actualDpr, maxDpr);
    let width = Math.max(1, Math.round(rect.width * dpr)), height = Math.max(1, Math.round(rect.height * dpr));
    const scale = Math.min(1, limit / width, limit / height);
    const clamped = actualDpr > maxDpr || scale < 1;
    if (clamped !== Boolean(this.outputClamped)) { this.outputClamped = clamped; this.updateStatus(); }
    width = Math.max(1, Math.round(width * scale)); height = Math.max(1, Math.round(height * scale));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width; this.canvas.height = height; this.releaseTargets();
    }
    return true;
  }

  drawScene(width, height) {
    const gl = this.gl, program = this.scene;
    gl.viewport(0, 0, width, height); gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
    gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND); gl.clearColor(0.078, 0.086, 0.11, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.useProgram(program);
    const loc = (name) => gl.getUniformLocation(program, name);
    const hasBoneLayer = [...this.layers.values()].some((layer) => layer.mode === "bone");
    const basePose = this.animation ? HookyAnimation.pose(this.animation.binding, this.animation.frame)
      : (hasBoneLayer && this.skin ? HookyAnimation.restPose(this.skin) : null);
    const center = this.followRoot && this.animation
      ? followedCenter(this.model.center, this.rootIndex, basePose) : this.model.center;
    gl.uniform3fv(loc("u_center"), center); gl.uniform1f(loc("u_radius"), this.model.radius);
    gl.uniform2f(loc("u_angles"), this.yaw, this.pitch);
    const aspect = width / height;
    // Cadrage initial sur la boîte projetée (pas sur sa sphère, souvent très
    // généreuse pour un animal allongé). Reste fixe quand on tourne ; la
    // molette laisse à l'utilisateur le contrôle du grossissement.
    const [ex, ey, ez] = this.model.extent.map((v) => v / this.model.radius);
    const sy = Math.sin(0.7), cy = Math.cos(0.7), sp = Math.sin(-0.35), cp = Math.cos(-0.35);
    const halfX = Math.abs(cy) * ex + Math.abs(sy) * ez;
    const halfY = Math.abs(sy * sp) * ex + Math.abs(cp) * ey + Math.abs(cy * sp) * ez;
    const factor = 0.88 * this.zoom * Math.min(aspect / Math.max(halfX, 1e-6), 1 / Math.max(halfY, 1e-6));
    gl.uniform2f(loc("u_fit"), factor / aspect, factor);
    gl.uniform1i(loc("u_texture"), 0); gl.uniform1i(loc("u_skin"), 1);
    // WebGL validates the integer generic attribute type even when skinning
    // is disabled and the corresponding vertex arrays are not enabled.
    gl.vertexAttribI4ui(3, 0, 0, 0, 0); gl.vertexAttribI4ui(4, 0, 0, 0, 0);
    let reportChanged = false;
    for (const layer of this.allLayers()) {
      if (layer === this && !this.baseVisible) continue;
      const binding = layer === this ? this.animation?.binding : layer.binding;
      const animated = Boolean(this.animation && binding);
      gl.uniform1i(loc("u_animated"), animated);
      if (animated) {
        try {
          const palette = layer === this ? basePose.palette : HookyAnimation.palette(binding, this.animation.frame);
          gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, layer.skinTexture);
          gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 6, layer.skin.bones.length, gl.RGBA, gl.FLOAT, palette);
          gl.activeTexture(gl.TEXTURE0);
        } catch (error) {
          if (layer === this) throw error;
          const warning = `Tenue non affichée : ${error.message}`;
          if (layer.runtimeWarning !== warning) { layer.runtimeWarning = warning; reportChanged = true; }
          continue;
        }
      }
      const attached = layer !== this && (layer.mode === "bone" || layer.mode === "static");
      gl.uniform1i(loc("u_attached"), attached);
      if (attached) {
        try {
          const boneIndex = layer.mode === "bone" ? this.skin.bones.findIndex((bone) => bone.name === layer.bone) : -1;
          const world = layer.mode === "bone" ? basePose?.worlds[boneIndex] : [1,0,0,0, 0,1,0,0, 0,0,1,0];
          const transform = attachmentMatrices(world, layer);
          gl.uniformMatrix4fv(loc("u_attachment"), false, transform.matrix);
          gl.uniformMatrix3fv(loc("u_attachmentNormal"), false, transform.normal);
        } catch (error) {
          const warning = `Équipement non affiché : ${error.message}`;
          if (layer.runtimeWarning !== warning) { layer.runtimeWarning = warning; reportChanged = true; }
          continue;
        }
      }
      if (layer.runtimeWarning) { layer.runtimeWarning = ""; reportChanged = true; }
      if (this.wire) { gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1); }
      for (const part of layer.parts) {
        gl.bindVertexArray(part.vao);
        if (!part.mesh.normals) gl.vertexAttrib3f(1, 0, 0, 1);
        if (!part.mesh.uvs) gl.vertexAttrib2f(2, 0, 0);
        const map = part.mesh.texture;
        const texture = map && this.textureMap.get(`${map.path}\n${map.index}`)?.texture;
        gl.uniform3fv(loc("u_color"), part.color); gl.uniform1i(loc("u_smooth"), Boolean(part.mesh.normals) && !this.flat);
        gl.uniform1i(loc("u_wire"), 0); gl.uniform1i(loc("u_textured"), Boolean(this.textured && texture));
        if (texture) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture); }
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.indexBuffer);
        gl.drawElements(gl.TRIANGLES, part.mesh.indices.length, gl.UNSIGNED_INT, 0);
      }
      if (this.wire) {
        gl.disable(gl.POLYGON_OFFSET_FILL); gl.uniform1i(loc("u_wire"), 1);
        for (const part of layer.parts) {
          gl.bindVertexArray(part.vao); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.wireBuffer);
          gl.drawElements(gl.LINES, part.mesh.indices.length * 2, gl.UNSIGNED_INT, 0);
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.indexBuffer);
        }
      }
    }
    gl.bindVertexArray(null);
    if (reportChanged) this.options.onLayersChanged?.(this.layerReports());
  }

  makeTarget(width, height, depth = false, samples = 0) {
    const gl = this.gl, target = { width, height, textures: [], buffers: [], frames: [] };
    try {
      const framebuffer = gl.createFramebuffer(), texture = gl.createTexture();
      if (framebuffer) target.frames.push(framebuffer);
      if (texture) target.textures.push(texture);
      if (!framebuffer || !texture) throw new Error("allocation framebuffer");
      target.framebuffer = framebuffer; target.texture = texture;
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
      if (depth && !samples) {
        const rb = gl.createRenderbuffer(); if (!rb) throw new Error("allocation profondeur");
        target.buffers.push(rb); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
        gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, width, height);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb);
      }
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error("framebuffer incomplet");
      if (samples) {
        const fb = gl.createFramebuffer(), color = gl.createRenderbuffer(), z = gl.createRenderbuffer();
        if (fb) target.frames.push(fb);
        if (color) target.buffers.push(color);
        if (z) target.buffers.push(z);
        if (!fb || !color || !z) throw new Error("allocation anticrénelage");
        target.msaa = fb;
        gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
        gl.bindRenderbuffer(gl.RENDERBUFFER, color);
        gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.RGBA8, width, height);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, color);
        gl.bindRenderbuffer(gl.RENDERBUFFER, z);
        gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.DEPTH_COMPONENT16, width, height);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, z);
        if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error("anticrénelage indisponible");
      }
      if (gl.getError() !== gl.NO_ERROR) throw new Error("mémoire framebuffer insuffisante");
      return target;
    } catch (error) { this.deleteTarget(target); throw error; }
  }

  deleteTarget(target) {
    if (!target) return;
    for (const item of target.textures) this.gl.deleteTexture(item);
    for (const item of target.buffers) this.gl.deleteRenderbuffer(item);
    for (const item of target.frames) this.gl.deleteFramebuffer(item);
  }

  releaseTargets() {
    if (!this.targets) return;
    this.deleteTarget(this.targets.input); this.deleteTarget(this.targets.output); this.targets = null;
  }

  ensureTargets(width, height) {
    if (this.targets) return;
    const requested = this.renderSettings.viewer_msaa === "off" ? 0 : Number(this.renderSettings.viewer_msaa);
    const samples = Math.min(requested, this.gl.getParameter(this.gl.MAX_SAMPLES) || 0);
    const scale = FSR_SCALES[this.renderSettings.fsr_quality];
    const iw = Math.max(1, Math.round(width * scale)), ih = Math.max(1, Math.round(height * scale));
    const input = this.makeTarget(iw, ih, true, samples >= 2 ? samples : 0);
    try { this.targets = { input, output: this.makeTarget(width, height) }; }
    catch (error) { this.deleteTarget(input); throw error; }
    if (requested && samples < 2) this.addNote("FSR sans anticrénelage multisample");
  }

  pass(program, texture, outWidth, outHeight, inWidth, inHeight) {
    const gl = this.gl; gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND); gl.useProgram(program);
    const loc = (name) => gl.getUniformLocation(program, name);
    gl.viewport(0, 0, outWidth, outHeight); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1i(loc("u_in"), 0); gl.uniform1i(loc("u_src"), 0);
    gl.uniform2f(loc("u_outSize"), outWidth, outHeight); gl.uniform2f(loc("u_inSize"), inWidth, inHeight);
    gl.uniform1i(loc("u_flipY"), 0); gl.uniform1f(loc("u_split"), -1); gl.uniform1i(loc("u_dither"), 0);
    gl.uniform1f(loc("u_sharpness"), this.renderSettings.fsr_sharpness); gl.uniform1i(loc("u_denoise"), 0);
    gl.bindVertexArray(this.fullscreenVao); gl.drawArrays(gl.TRIANGLES, 0, 3); gl.bindVertexArray(null);
  }

  draw() {
    if (!this.sizeCanvas()) return;
    const gl = this.gl, width = this.canvas.width, height = this.canvas.height;
    try {
      if (this.mode === "fsr" && this.fsr) {
        this.ensureTargets(width, height);
        const { input, output } = this.targets;
        gl.bindFramebuffer(gl.FRAMEBUFFER, input.msaa || input.framebuffer);
        this.drawScene(input.width, input.height);
        if (input.msaa) {
          gl.bindFramebuffer(gl.READ_FRAMEBUFFER, input.msaa); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, input.framebuffer);
          gl.blitFramebuffer(0, 0, input.width, input.height, 0, 0, input.width, input.height, gl.COLOR_BUFFER_BIT, gl.NEAREST);
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, output.framebuffer);
        this.pass(this.fsr.easu, input.texture, width, height, input.width, input.height);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        this.pass(this.fsr.rcas, output.texture, width, height, width, height);
        if (gl.getError() !== gl.NO_ERROR) throw new Error("pipeline WebGL");
      } else {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null); this.drawScene(width, height);
      }
    } catch (error) {
      if (this.mode === "fsr") {
        this.mode = "native"; this.releaseTargets(); this.options.onModeFallback?.();
        this.addNote(`FSR 1 indisponible : ${error.message}`);
        try { gl.bindFramebuffer(gl.FRAMEBUFFER, null); this.drawScene(width, height); }
        catch (nativeError) { this.options.onFailure?.(`Vue 3D indisponible : ${nativeError.message}`); }
      } else this.options.onFailure?.(`Vue 3D indisponible : ${error.message}`);
    }
  }

  dispose(releaseContext = false) {
    // Après un passage en PNG, les ressources sont déjà détruites mais le
    // contexte a été conservé pour le retour 3D. Le libérer à la fermeture.
    if (this.disposed) {
      if (releaseContext) this.loseContext();
      return;
    }
    this.disposed = true; this.modeSerial += 1; this.abort.abort();
    for (const request of this.layerRequests.values()) request.abort();
    this.layerRequests.clear();
    if (this.frame) cancelAnimationFrame(this.frame);
    this.resizeObserver?.disconnect();
    for (const [node, name, handler, options] of this.listeners) node.removeEventListener(name, handler, options);
    for (const image of this.pendingImages) { image.onload = image.onerror = null; image.src = ""; }
    const gl = this.gl;
    this.releaseTargets();
    for (const layer of new Set([...this.layers.values(), ...this.pendingLayers])) this.deleteLayerResources(layer);
    this.layers.clear(); this.pendingLayers.clear();
    this.deleteLayerResources(this);
    for (const entry of this.textureMap.values()) if (entry.texture) gl.deleteTexture(entry.texture);
    this.textureMap.clear();
    this.animation = null; this.animationSerial = (this.animationSerial || 0) + 1;
    if (this.fsr) { gl.deleteProgram(this.fsr.easu); gl.deleteProgram(this.fsr.rcas); }
    gl.deleteProgram(this.scene); gl.deleteVertexArray(this.fullscreenVao);
    // Si le canvas est retiré, libérer aussi le contexte tout de suite pour
    // pouvoir en ouvrir beaucoup successivement. Le garder lors d'un simple
    // passage à la planche PNG pour permettre le retour à la vue 3D.
    if (releaseContext) this.loseContext();
  }

  loseContext() {
    if (this.contextReleased) return;
    this.contextReleased = true;
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
  }
}

if (typeof window !== "undefined") window.ModelViewport = ModelViewport;
if (typeof module !== "undefined") module.exports = { parseModelGeometry, normalizeRenderSettings, attachmentMatrices, uniqueRootIndex, followedCenter, FSR_SCALES, ModelViewport };
