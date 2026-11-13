"use strict";

const state = { project: null, filter: "", view: "home", selection: null };

/* ------------------------------------------------------------------ outils */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
));
const humanSize = (bytes) => {
  if (!bytes) return "0 o";
  const units = ["o", "Ko", "Mo", "Go"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
};

function toast(message, kind = "ok") {
  const node = document.createElement("div");
  node.className = `toast ${kind === "error" ? "error" : ""}`;
  node.textContent = message;
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 9000 : 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const isJson = (response.headers.get("Content-Type") || "").includes("json");
  const payload = isJson ? await response.json() : await response.blob();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

/* ------------------------------------------------------------------ vues */
function showView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => {
    node.hidden = node.id !== `view-${view}`;
  });
  document.querySelectorAll(".nav").forEach((node) => {
    if (node.dataset.view === view) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (view === "textures") renderTextures();
  if (view === "models") renderModels();
  if (view === "mobs") renderMobs();
  if (view === "home") renderActivity();
}

function matches(asset) {
  return !state.filter || asset.rel.toLowerCase().includes(state.filter);
}

/* ------------------------------------------------------------------ accueil */
function renderStats() {
  const assets = state.project?.assets || [];
  const count = (kind) => assets.filter((a) => a.kind === kind).length;
  $("#stats").innerHTML = `
    <span class="chip"><b>${count("texture")}</b> textures</span>
    <span class="chip"><b>${count("model")}</b> modèles</span>
    <span class="chip"><b>${state.project?.mobs.length || 0}</b> mobs</span>`;
  $("#root-path").textContent = state.project?.root || "—";
  const dicts = state.project?.dicts || 0;
  $("#dict-state").textContent = state.project?.zstd
    ? (dicts ? `${dicts} dictionnaire(s) chargés` : "aucun dictionnaire zstd")
    : "zstandard absent";
}

function renderActivity() {
  const project = state.project;
  if (!project) return;
  const edited = project.edited || [];
  $("#activity").innerHTML = [
    `<li><span class="k">Racine</span><code>${esc(project.root)}</code></li>`,
    `<li><span class="k">Dossier de travail</span><code>${esc(project.work)}</code></li>`,
    `<li><span class="k">Fichiers modifiés</span>${edited.length ? edited.length + " en attente d'assemblage" : "aucun"}</li>`,
    `<li><span class="k">Mobs créés</span>${project.mobs.length || "aucun"}</li>`,
  ].join("");
}

/* ------------------------------------------------------------------ textures */
async function renderTextures() {
  const grid = $("#texture-grid");
  const textures = (state.project?.assets || []).filter((a) => a.kind === "texture" && matches(a));
  if (!textures.length) {
    grid.innerHTML = `<p class="muted">Aucune texture dans ce projet.</p>`;
    return;
  }
  grid.innerHTML = textures.map((asset) => `
    <article class="tile" data-kind="texture" data-rel="${esc(asset.rel)}">
      <div class="thumb"><img loading="lazy" alt=""
        src="/api/texture/png?path=${encodeURIComponent(asset.rel)}&index=0"></div>
      <div class="meta">
        <strong>${esc(asset.name)}</strong>
        <small>${humanSize(asset.size)}${asset.compressed ? " · compressé" : ""}</small>
      </div>
    </article>`).join("");
}

async function openTexture(rel) {
  const info = await api(`/api/texture/info?path=${encodeURIComponent(rel)}`);
  const texture = info.textures[0];
  const editable = info.textures.filter((t) => t.editable).length;
  const body = `
    <h3>${esc(texture.name)}</h3>
    <div class="sub">${esc(rel)}</div>
    <img class="preview" alt="" src="/api/texture/png?path=${encodeURIComponent(rel)}&index=0">
    <dl class="kv">
      <dt>Version</dt><dd>${esc(info.version)}${info.orin ? " · Orin" : ""}</dd>
      <dt>Format</dt><dd>${esc(texture.format)} ${texture.editable
        ? `<span class="badge">modifiable</span>` : `<span class="badge warn">non encodable</span>`}</dd>
      <dt>Taille</dt><dd>${texture.width} × ${texture.height}</dd>
      <dt>Mips</dt><dd>${texture.mips}</dd>
      <dt>Textures</dt><dd>${info.count} (${editable} modifiable(s))</dd>
    </dl>
    ${texture.editable ? "" : `<p class="muted">${esc(texture.hint)} — ce format ne peut pas
      être réencodé pour l'instant&nbsp;; l'import est refusé plutôt que de produire un fichier faux.</p>`}
    <div class="actions">
      <button class="primary" id="btn-import" ${texture.editable ? "" : "disabled"}>Remplacer par une image…</button>
      <button class="ghost" id="btn-export-png">Exporter en PNG</button>
      <input type="file" id="file-import" accept="image/*" hidden>
    </div>`;
  showDetail(body);

  $("#btn-export-png").onclick = async () => {
    try {
      const result = await api(`/api/texture/export?path=${encodeURIComponent(rel)}&index=0`);
      toast(`PNG écrit : ${result.file}`);
    } catch (error) { toast(error.message, "error"); }
  };

  const input = $("#file-import");
  $("#btn-import").onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const base64 = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.readAsDataURL(file);
    });
    try {
      const result = await api("/api/texture/import", {
        method: "POST",
        body: JSON.stringify({ path: rel, index: 0, image: base64 }),
      });
      toast(`Texture remplacée (${result.texture}) → ${result.wrote}`);
      await refresh();
    } catch (error) { toast(error.message, "error"); }
  };
}

/* ------------------------------------------------------------------ modèles */
async function renderModels() {
  const grid = $("#model-grid");
  const models = (state.project?.assets || []).filter((a) => a.kind === "model" && matches(a));
  if (!models.length) {
    grid.innerHTML = `<p class="muted">Aucun modèle dans ce projet.</p>`;
    return;
  }
  grid.innerHTML = models.map((asset) => `
    <article class="tile" data-kind="model" data-rel="${esc(asset.rel)}">
      <div class="thumb"><img loading="lazy" alt=""
        src="/api/model/preview?path=${encodeURIComponent(asset.rel)}&w=420&h=320"></div>
      <div class="meta">
        <strong>${esc(asset.name)}</strong>
        <small>${humanSize(asset.size)}</small>
      </div>
    </article>`).join("");
}

async function openModel(rel) {
  let info;
  try {
    info = await api(`/api/model/info?path=${encodeURIComponent(rel)}`);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  showDetail(`
    <h3>${esc(info.models[0] || rel.split("/").pop())}</h3>
    <div class="sub">${esc(rel)}</div>
    <img class="preview" id="model-shot" alt=""
      src="/api/model/preview?path=${encodeURIComponent(rel)}&w=440&h=340">
    <dl class="kv">
      <dt>Version</dt><dd>${esc(info.version)}</dd>
      <dt>Maillages</dt><dd>${info.meshes}</dd>
      <dt>Faces</dt><dd>${info.faces.toLocaleString("fr-FR")}</dd>
      <dt>Sommets</dt><dd>${info.vertices.toLocaleString("fr-FR")}</dd>
      <dt>Dimensions</dt><dd>${info.bounds.map((v) => v.toFixed(3)).join(" × ")}</dd>
      <dt>Matériaux</dt><dd>${info.materials.length ? info.materials.map(esc).join(", ") : "—"}</dd>
    </dl>
    ${info.warnings.length ? `<ul class="todo">${info.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    <div class="actions">
      <button class="primary" id="btn-blender">Exporter pour Blender</button>
      <button class="ghost" id="btn-turntable">Planche de rotations</button>
    </div>`);

  $("#btn-turntable").onclick = () => {
    $("#model-shot").src = `/api/model/preview?path=${encodeURIComponent(rel)}&w=440&h=340&views=4`;
  };
  $("#btn-blender").onclick = async () => {
    try {
      const result = await api("/api/model/export", {
        method: "POST",
        body: JSON.stringify({ path: rel, textures: true }),
      });
      toast(`Export Blender : ${result.folder} (${result.faces} faces, ${result.textures} texture(s))`);
    } catch (error) { toast(error.message, "error"); }
  };
}

/* ------------------------------------------------------------------ mobs */
function fillSelects() {
  const models = (state.project?.assets || []).filter((a) => a.kind === "model");
  const textures = (state.project?.assets || []).filter((a) => a.kind === "texture");
  $("#mob-model").innerHTML = models.length
    ? models.map((a) => `<option value="${esc(a.rel)}">${esc(a.name)}</option>`).join("")
    : `<option value="">(aucun modèle)</option>`;
  $("#mob-textures").innerHTML = textures
    .map((a) => `<option value="${esc(a.rel)}">${esc(a.name)}</option>`).join("");
}

async function renderMobs() {
  fillSelects();
  const mobs = state.project?.mobs || [];
  $("#mob-grid").innerHTML = mobs.map((mob) => `
    <article class="tile" data-kind="mob" data-slug="${esc(mob.slug)}">
      <div class="meta" style="padding:14px 14px 12px">
        <strong>${esc(mob.name)}</strong>
        <small>parent : ${esc(mob.parent)}</small>
        <small>${mob.files.length} fichier(s) préparé(s)</small>
      </div>
    </article>`).join("") || `<p class="muted">Aucun mob pour l'instant.</p>`;
}

function openMob(slug) {
  const mob = (state.project?.mobs || []).find((m) => m.slug === slug);
  if (!mob) return;
  showDetail(`
    <h3>${esc(mob.name)}</h3>
    <div class="sub">${esc(mob.slug)} · parent ${esc(mob.parent)}</div>
    <dl class="kv">
      <dt>Modèle source</dt><dd>${esc(mob.model_source || "—")}</dd>
      <dt>Textures</dt><dd>${mob.texture_sources.map(esc).join(", ") || "—"}</dd>
      <dt>Fichiers</dt><dd>${mob.files.map((f) => esc(f.to)).join(", ") || "—"}</dd>
    </dl>
    <strong>Reste à faire côté jeu</strong>
    <ul class="todo">${mob.todo.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`);
}

/* ------------------------------------------------------------------ mod */
async function buildMod(event) {
  event.preventDefault();
  const name = new FormData(event.target).get("name");
  try {
    const result = await api("/api/mod/build", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    $("#mod-log").innerHTML = `
      <li><span class="k">Mod</span>${esc(result.mod)}</li>
      <li><span class="k">Dossier</span><code>${esc(result.path)}</code></li>
      <li><span class="k">Fichiers</span>${result.files}</li>
      <li><span class="k">Mobs</span>${result.mobs}</li>`;
    toast(`Mod assemblé : ${result.path} (${result.files} fichier(s))`);
  } catch (error) { toast(error.message, "error"); }
}

/* ------------------------------------------------------------------ détail */
function showDetail(html) {
  $("#detail-body").innerHTML = html;
  $("#detail").hidden = false;
}
function hideDetail() { $("#detail").hidden = true; }

/* ------------------------------------------------------------------ init */
async function refresh() {
  const payload = await api("/api/state");
  state.project = payload.project;
  renderStats();
  if (state.view === "home") renderActivity();
  if (state.view === "textures") await renderTextures();
  if (state.view === "models") await renderModels();
  if (state.view === "mobs") await renderMobs();
}

function wire() {
  document.querySelectorAll(".nav").forEach((node) => {
    node.onclick = () => showView(node.dataset.view);
  });
  document.querySelectorAll("[data-goto]").forEach((node) => {
    node.onclick = () => showView(node.dataset.goto);
  });
  $("#search").oninput = (event) => {
    state.filter = event.target.value.trim().toLowerCase();
    if (state.view === "textures") renderTextures();
    if (state.view === "models") renderModels();
  };
  document.body.addEventListener("click", (event) => {
    const tile = event.target.closest(".tile");
    if (!tile) return;
    if (tile.dataset.kind === "texture") openTexture(tile.dataset.rel);
    if (tile.dataset.kind === "model") openModel(tile.dataset.rel);
    if (tile.dataset.kind === "mob") openMob(tile.dataset.slug);
  });
  $("#detail-close").onclick = hideDetail;
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideDetail();
  });
  $("#mob-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const textures = [...$("#mob-textures").selectedOptions].map((o) => o.value);
    try {
      const mob = await api("/api/mobs", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("name"),
          model: form.get("model"),
          textures,
        }),
      });
      toast(`Fiche créée : ${mob.name}`);
      await refresh();
    } catch (error) { toast(error.message, "error"); }
  };
  $("#mod-form").onsubmit = buildMod;
}

async function boot() {
  wire();
  try {
    await refresh();
    showView("home");
  } catch (error) {
    toast(error.message, "error");
  }
}

boot();
