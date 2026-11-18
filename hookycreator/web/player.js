"use strict";

/* Catalogue par nom : ce sont des suggestions, jamais une preuve que le
   modèle ou l'animation est compatible. Aucun fichier du jeu n'est fourni. */
const HookyPlayerCatalog = (() => {
  const rawName = (asset) => String(asset.name || asset.rel?.split("/").pop() || "").split(".")[0];
  const nameOf = (asset) => rawName(asset).toLowerCase();
  const keyOf = (asset) => rawName(asset).replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "_").replace(/^_+|_+$/g, "");
  const animationKey = (asset) => keyOf(asset).replace(/_(?:player_)?animation$|_anim$/, "");
  const player = (asset) => /(?:^|_)(?:player|link|hero|heros)(?:_|$)/.test(keyOf(asset));
  // An age label on armour is useful as a weak suggestion too; it is not a
  // compatibility check and absence of a label never implies an age.
  const markedAge = (asset) => {
    const name = keyOf(asset);
    if (/(?:^|_)(?:child|young|kid|enfant)(?:_|$)/.test(name)) return "child";
    if (/(?:^|_)(?:adult|adulte)(?:_|$)/.test(name)) return "adult";
    return "";
  };
  const age = (asset) => player(asset) ? markedAge(asset) : "";
  const category = (asset) => {
    const name = keyOf(asset);
    if (/(?:^|_)(?:weapon|sword|shield|bow|item|sheathe|equip)(?:_|$)/.test(name)) return "objet";
    if (/(?:^|_)(?:armor|armour|outfit|tunic|clothes|cloth|skin|helmet|hat|mask|head|body|upper|lower|leg|legs|boot|boots|shoe|glove)(?:_|$)/.test(name)) return "tenue / skin";
    return player(asset) ? "personnage" : "modèle";
  };
  const equipment = (asset) => category(asset) !== "modèle";
  const matches = (asset, query) => !query || String(asset.rel || "").toLowerCase().includes(query.toLowerCase().trim());
  const models = (assets, family, query) => assets.filter((asset) => asset.kind === "model" && matches(asset, query)
    && (family === "all" || (family === "player" ? player(asset) : age(asset) === family)));
  const equipmentModels = (assets, filter, query) => assets.filter((asset) => asset.kind === "model" && matches(asset, query)
    && (filter === "all" || equipment(asset)));
  const related = (a, b) => Boolean(a && b && (a === b || a.startsWith(`${b}_`) || b.startsWith(`${a}_`)));
  function animationPriority(asset, base) {
    const name = animationKey(asset), baseName = keyOf(base || {}), baseAge = age(base || {});
    if (related(name, baseName)) return 0;
    if (baseAge && age(asset) === baseAge) return 1;
    return 2;
  }
  function suggestModels(assets, animationRel) {
    const animation = assets.find((item) => item.kind === "animation" && item.rel === animationRel);
    if (!animation) return [];
    const key = animationKey(animation), parts = key.split("_"), animationAge = markedAge(animation);
    const isPlayer = player(animation);
    const suggestions = [];
    for (const asset of assets) {
      if (asset.kind !== "model") continue;
      const name = keyOf(asset), kind = category(asset), modelAge = markedAge(asset);
      // Never suggest an explicitly adult model for a child clip (or vice versa).
      if (isPlayer && animationAge && modelAge && animationAge !== modelAge) continue;
      let rank, reason;
      if (name && name === key) { rank = 0; reason = "Nom exact"; }
      else if (related(name, key)) { rank = 1; reason = "Nom proche"; }
      else if (parts.length >= 2 && name.startsWith(`${parts[0]}_${parts[1]}_`)) {
        rank = 2; reason = "Même famille de nom";
      } else if (isPlayer && animationAge && modelAge === animationAge
          && (kind === "personnage" || kind === "tenue / skin")) {
        rank = 3; reason = "Âge indiqué dans le nom";
      } else continue;
      suggestions.push({ asset, category: kind, reason, rank });
    }
    suggestions.sort((a, b) => a.rank - b.rank || a.asset.rel.localeCompare(b.asset.rel));
    return suggestions;
  }
  return { nameOf, keyOf, animationKey, player, age, markedAge, category, equipment,
    models, equipmentModels, animationPriority, suggestModels, matches };
})();

if (typeof window !== "undefined") window.HookyPlayerCatalog = HookyPlayerCatalog;

if (typeof module !== "undefined") module.exports = HookyPlayerCatalog;

if (typeof window !== "undefined") window.HookyPlayer = (() => {
  const LIMIT = 250;
  const labels = { head: "Tête", body: "Tenue", legs: "Jambes", right: "Main droite", left: "Main gauche" };
  const config = { base: "", slots: new Map(), animationFile: "", animationIndex: null,
    showBase: true, textured: true, followRoot: true };
  const slotRequests = new Map();
  let deps = null, active = false, viewport = null, baseSequence = 0, animationSequence = 0;
  let currentReports = [];
  const slotErrors = new Map();
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const assets = () => deps?.getAssets() || [];
  const assetFor = (kind, rel) => assets().find((asset) => asset.kind === kind && asset.rel === rel);
  const endpoint = (kind, rel) => `/api/model/${kind}?path=${encodeURIComponent(rel)}`;
  const clipEndpoint = (rel, index) => `/api/animation/clip?path=${encodeURIComponent(rel)}&index=${index}`;
  const sameView = (v, serial) => Boolean(v && active && viewport === v && serial === baseSequence && !v.disposed);
  const slotVersion = (slot) => slotRequests.get(slot) || 0;

  function fill(select, list, selected, placeholder) {
    const shown = list.slice(0, LIMIT);
    // A search or category change must not silently select a different asset.
    const current = selected && assets().find((asset) => asset.rel === selected);
    if (current && !shown.some((asset) => asset.rel === selected)) shown.unshift(current);
    select.innerHTML = `<option value="">${esc(placeholder)}</option>` + shown.map((asset) =>
      `<option data-no-i18n value="${esc(asset.rel)}" title="${esc(asset.rel)}">${esc(asset.rel)}</option>`).join("");
    select.value = current ? selected : "";
  }

  function refreshBaseChoices() {
    const all = HookyPlayerCatalog.models(assets(), $("#player-family").value, $("#player-base-search").value);
    fill($("#player-base"), all, config.base, "Choisir un modèle…");
    $("#player-base-count").textContent = `${all.length} candidat(s) par nom · ${Math.min(all.length, LIMIT)} affichés. Les catégories restent indicatives.`;
  }

  function refreshEquipmentChoices(selected = $("#player-equipment").value) {
    const select = $("#player-equipment");
    const all = HookyPlayerCatalog.equipmentModels(assets(), $("#player-equipment-filter").value, $("#player-equipment-search").value)
      .filter((asset) => asset.rel !== config.base);
    fill(select, all, selected, "Choisir un modèle…");
    select.title = `${all.length} résultat(s) · ${Math.min(all.length, LIMIT)} affichés`;
  }

  function refreshAnimationChoices() {
    const base = assetFor("model", config.base);
    const buckets = [[], [], []], query = $("#player-animation-search").value;
    for (const asset of assets()) {
      if (asset.kind === "animation" && HookyPlayerCatalog.matches(asset, query))
        buckets[HookyPlayerCatalog.animationPriority(asset, base)].push(asset);
    }
    fill($("#player-animation-file"), buckets.flat(), config.animationFile, "Pose statique");
    $("#player-animation-file").disabled = !active || !viewport?.ready;
  }

  function renderEquipped() {
    const reports = new Map(currentReports.map((report) => [report.slot, report]));
    const list = $("#player-equipped");
    list.innerHTML = [...config.slots].map(([slot, spec]) => {
      const report = reports.get(slot), name = assetFor("model", spec.rel)?.name || spec.rel.split("/").pop();
      let note = slotErrors.get(slot) || report?.warning || "";
      if (!note && !report) note = "Restauration en attente.";
      else if (!note && spec.mode === "skin" && report.matched) note = `${report.matched} os associés · aperçu expérimental`;
      else if (!note && spec.mode === "skin") note = "Squelette chargé · choisir un clip pour tester.";
      else if (!note && spec.mode === "bone") note = `Attaché à ${spec.bone} · position à vérifier.`;
      else if (!note) note = "Statique dans l'espace commun.";
      return `<li><strong>${esc(labels[slot])}</strong><span data-no-i18n title="${esc(spec.rel)}">${esc(name)}</span><small>${esc(note)}</small></li>`;
    }).join("") || '<li><span>Aucun équipement affiché.</span></li>';
  }

  function resetCanvas() {
    const previous = $("#player-canvas"), replacement = previous.cloneNode(false);
    previous.replaceWith(replacement);
    return replacement;
  }

  function resetPlayback(message = "Choisis un fichier d'animation puis un clip pris en charge.") {
    $("#player-animation-clip").innerHTML = '<option value="">Choisir un fichier</option>';
    $("#player-animation-clip").disabled = true;
    $("#player-frame").disabled = true; $("#player-frame").value = "0"; $("#player-frame").max = "1";
    $("#player-play").disabled = true; $("#player-play").textContent = "Pause";
    $("#player-time").textContent = "0 / 0"; $("#player-animation-status").textContent = message;
  }

  function setPlaceholder(message, unavailable = false) {
    $("#player-canvas").hidden = true; $("#player-placeholder").hidden = false;
    $("#player-placeholder strong").textContent = unavailable ? "Aperçu indisponible" : "Ta scène commence ici";
    $("#player-placeholder p").textContent = unavailable
      ? "Consulte le diagnostic sous la scène." : "Choisis un modèle de base dans ton projet.";
    $("#player-retry").hidden = !unavailable || !assetFor("model", config.base);
    $("#player-view-status").textContent = message;
  }

  function failViewport(message) {
    viewport?.dispose(true); viewport = null; currentReports = [];
    resetPlayback("Aperçu indisponible. Réessaie ou change de modèle.");
    refreshAnimationChoices(); renderEquipped(); setPlaceholder(message, true);
    $("#player-equip").disabled = true;
  }

  function updateBoneChoices(skin) {
    const select = $("#player-bone"), value = select.value;
    select.innerHTML = '<option value="">Choisir un os…</option>' + (skin?.bones || []).map((bone) =>
      `<option data-no-i18n value="${esc(bone.name)}">${esc(bone.name)}</option>`).join("");
    select.value = (skin?.bones || []).some((bone) => bone.name === value) ? value : "";
  }

  function mountControls() {
    const mode = $("#player-mount").value;
    $("#player-bone-field").hidden = mode !== "bone";
    $("#player-transform").hidden = mode === "skin";
  }

  function selectSlot() {
    const slot = $("#player-slot").value, spec = config.slots.get(slot);
    $("#player-mount").value = spec?.mode || (["right", "left"].includes(slot) ? "bone" : "skin");
    refreshEquipmentChoices(spec?.rel || "");
    $("#player-bone").value = spec?.bone || "";
    for (const [group, defaults] of [["offset", spec?.offset || [0,0,0]], ["rotation", spec?.rotation || [0,0,0]]]) {
      ["x", "y", "z"].forEach((axis, index) => { $(`#player-${group}-${axis}`).value = String(defaults[index]); });
    }
    $("#player-scale").value = String(spec?.scale ?? 1);
    mountControls();
  }

  async function install(v, slot, spec, serial, requestSerial) {
    const result = await v.setLayer(slot, { geometryUrl: endpoint("geometry", spec.rel), skinUrl: endpoint("skin", spec.rel),
      mode: spec.mode, bone: spec.bone, offset: spec.offset, rotation: spec.rotation, scale: spec.scale });
    if (!result || !sameView(v, serial) || requestSerial !== slotVersion(slot)) return null;
    config.slots.set(slot, spec); slotErrors.delete(slot); currentReports = v.layerReports(); renderEquipped();
    return result;
  }

  async function startBase(rel, preserve) {
    const savedSlots = preserve ? [...config.slots] : [];
    if (!preserve) {
      config.slots.clear(); config.animationFile = ""; config.animationIndex = null;
    }
    config.base = rel;
    const serial = ++baseSequence; animationSequence += 1;
    viewport?.dispose(true); viewport = null; currentReports = []; slotErrors.clear();
    resetPlayback(); updateBoneChoices(null); refreshBaseChoices(); refreshEquipmentChoices(); refreshAnimationChoices(); renderEquipped();
    $("#player-equip").disabled = true;
    if (!rel || !assetFor("model", rel)) {
      setPlaceholder(rel ? "Le modèle n'est plus présent dans le projet." : "Aucun modèle chargé.", Boolean(rel));
      return;
    }
    const canvas = resetCanvas(); canvas.hidden = false; $("#player-placeholder").hidden = true;
    $("#player-view-status").textContent = "Chargement de la géométrie complète…";
    let v;
    try {
      v = new ModelViewport(canvas, {
        textured: config.textured, revision: deps.getRevision(), renderSettings: deps.getSettings(),
        onStatus: (text) => { if (sameView(v, serial)) $("#player-view-status").textContent = text; },
        onFailure: (text) => {
          if (!sameView(v, serial)) return;
          failViewport(text);
        },
        onAnimationFrame: (frame, playing) => {
          if (!sameView(v, serial)) return;
          $("#player-frame").value = String(frame);
          $("#player-time").textContent = `${frame.toFixed(1)} / ${$("#player-frame").max}`;
          $("#player-play").textContent = playing ? "Pause" : "Lire";
        },
        onLayersChanged: (reports) => { if (sameView(v, serial)) { currentReports = reports; renderEquipped(); } },
      });
      viewport = v; v.setBaseVisible(config.showBase); v.setFollowRoot(config.followRoot);
      void v.setMode(deps.getSettings()?.viewer_upscale || "native");
      await v.load(endpoint("geometry", rel));
      if (!sameView(v, serial)) return;
      try {
        const skin = await v.prepareSkin(endpoint("skin", rel));
        if (!sameView(v, serial)) return;
        updateBoneChoices(skin);
      } catch (error) {
        if (!sameView(v, serial)) return;
        $("#player-animation-status").textContent = `Squelette du modèle indisponible : ${error.message}`;
      }
      if (!sameView(v, serial)) return;
      for (const [slot, spec] of savedSlots) {
        if (config.slots.get(slot) !== spec) continue;
        if (!assetFor("model", spec.rel)) {
          slotErrors.set(slot, "Modèle absent du projet ; recherche une autre ressource.");
          renderEquipped(); continue;
        }
        const requestSerial = slotVersion(slot);
        try { await install(v, slot, spec, serial, requestSerial); }
        catch (error) {
          if (sameView(v, serial) && requestSerial === slotVersion(slot)) {
            slotErrors.set(slot, `Non restauré : ${error.message}`); renderEquipped();
            $("#player-equip-status").textContent = `${labels[slot]} non restauré : ${error.message}`;
          }
        }
        if (!sameView(v, serial)) return;
      }
      $("#player-equip").disabled = false; selectSlot(); refreshAnimationChoices();
      if (config.animationFile && assetFor("animation", config.animationFile)) await loadAnimationFile(config.animationIndex);
    } catch (error) {
      if (v && !sameView(v, serial)) return;
      failViewport(`Vue 3D indisponible : ${error.message}`);
    }
  }

  function chooseBase(rel, preserve = false) {
    return startBase(rel, preserve);
  }

  async function loadSelectedClip() {
    const file = $("#player-animation-file").value, index = $("#player-animation-clip").value;
    if (!file || index === "") return;
    const serial = ++animationSequence, v = viewport, base = baseSequence;
    if (!v) { $("#player-animation-status").textContent = "Choisis d'abord un modèle."; return; }
    v.stopAnimation();
    $("#player-frame").disabled = true; $("#player-play").disabled = true;
    $("#player-animation-status").textContent = "Chargement du clip et du squelette…";
    try {
      const clip = await deps.api(clipEndpoint(file, Number(index)));
      if (serial !== animationSequence || !sameView(v, base)) return;
      const result = await v.setAnimation(endpoint("skin", config.base), clip);
      if (!result || serial !== animationSequence || !sameView(v, base)) return;
      config.animationFile = file; config.animationIndex = Number(index);
      $("#player-frame").max = String(Math.max(clip.frames, 1)); $("#player-frame").value = "0";
      $("#player-frame").disabled = false; $("#player-play").disabled = false;
      $("#player-loop").checked = Boolean(clip.loop); v.loopAnimation(Boolean(clip.loop));
      $("#player-animation-status").textContent = `${result.matched} os associés${result.missing ? ` · ${result.missing} pistes absentes` : ""} · 30 images/s par convention. Vérifie aussi les tenues.`;
      currentReports = v.layerReports(); renderEquipped();
    } catch (error) {
      if (serial !== animationSequence || !sameView(v, base)) return;
      v.stopAnimation(); $("#player-animation-status").textContent = `Animation indisponible : ${error.message}`;
    }
  }

  async function loadAnimationFile(preferred = null) {
    const file = $("#player-animation-file").value, serial = ++animationSequence, v = viewport, base = baseSequence;
    v?.stopAnimation(); config.animationFile = file; config.animationIndex = null;
    resetPlayback(file ? "Recherche des clips…" : "Pose statique.");
    if (!file || !v) return;
    try {
      const result = await deps.api(`/api/animation/list?path=${encodeURIComponent(file)}`);
      if (serial !== animationSequence || !sameView(v, base)) return;
      const clips = result.clips.filter((clip) => clip.supported);
      const select = $("#player-animation-clip");
      select.innerHTML = clips.length ? clips.map((clip) => `<option data-no-i18n value="${clip.index}">${esc(clip.name)} · ${clip.frames} images</option>`).join("")
        : '<option value="">Aucun clip Euler pris en charge</option>';
      if (!clips.length) { $("#player-animation-status").textContent = "Aucun clip squelettique Euler pris en charge."; return; }
      select.disabled = false;
      if (preferred !== null && clips.some((clip) => clip.index === preferred)) select.value = String(preferred);
      await loadSelectedClip();
    } catch (error) {
      if (serial === animationSequence && sameView(v, base)) $("#player-animation-status").textContent = `Animation indisponible : ${error.message}`;
    }
  }

  function readTransform() {
    const read = (id) => {
      const value = $(`#player-${id}`).value;
      return value.trim() === "" ? NaN : Number(value);
    };
    return { offset: ["x","y","z"].map((axis) => read(`offset-${axis}`)),
      rotation: ["x","y","z"].map((axis) => read(`rotation-${axis}`)), scale: read("scale") };
  }

  async function equip() {
    const v = viewport, base = baseSequence, slot = $("#player-slot").value, rel = $("#player-equipment").value;
    const serial = slotVersion(slot) + 1; slotRequests.set(slot, serial);
    v?.cancelLayer(slot);
    if (!v?.ready || !assetFor("model", rel) || rel === config.base) {
      $("#player-equip-status").textContent = "Choisis d'abord le personnage et un autre modèle."; return;
    }
    const mode = $("#player-mount").value;
    const spec = { rel, mode, bone: $("#player-bone").value,
      ...(mode === "skin" ? { offset: [0,0,0], rotation: [0,0,0], scale: 1 } : readTransform()) };
    $("#player-equip-status").textContent = "Chargement de l'équipement…";
    try {
      const report = await install(v, slot, spec, base, serial);
      if (!report || serial !== slotRequests.get(slot)) return;
      $("#player-equip-status").textContent = report.warning || "Équipement affiché dans l'aperçu. Vérifie sa position et son mouvement.";
    } catch (error) {
      if (serial === slotRequests.get(slot) && sameView(v, base)) {
        const retained = v.layers.has(slot) ? "L'ancien équipement est conservé." : "Aucun équipement n'a été ajouté.";
        $("#player-equip-status").textContent = `Équipement non ajouté : ${error.message} ${retained}`;
      }
    }
  }

  function remove() {
    const slot = $("#player-slot").value;
    slotRequests.set(slot, slotVersion(slot) + 1);
    viewport?.removeLayer(slot); config.slots.delete(slot); slotErrors.delete(slot);
    currentReports = viewport?.layerReports() || []; renderEquipped(); selectSlot();
    $("#player-equip-status").textContent = "Équipement retiré de l'aperçu.";
  }

  const debounce = (handler) => { let timer; return () => { clearTimeout(timer); timer = setTimeout(handler, 120); }; };
  function wire(options) {
    deps = options;
    $("#player-family").onchange = refreshBaseChoices;
    $("#player-base-search").oninput = debounce(refreshBaseChoices);
    $("#player-base").onchange = (event) => { void chooseBase(event.target.value); };
    $("#player-equipment-filter").onchange = () => refreshEquipmentChoices();
    $("#player-equipment-search").oninput = debounce(refreshEquipmentChoices);
    $("#player-animation-search").oninput = debounce(refreshAnimationChoices);
    $("#player-slot").onchange = selectSlot; $("#player-mount").onchange = mountControls;
    $("#player-equip").onclick = () => void equip(); $("#player-remove").onclick = remove;
    $("#player-show-base").onchange = (event) => { config.showBase = event.target.checked; viewport?.setBaseVisible(config.showBase); };
    $("#player-follow-root").onchange = (event) => { config.followRoot = event.target.checked; viewport?.setFollowRoot(config.followRoot); };
    $("#player-textured").onchange = (event) => { config.textured = event.target.checked; viewport?.setTextured(config.textured); };
    $("#player-reset").onclick = () => viewport?.reset();
    $("#player-retry").onclick = () => {
      if (config.base && assetFor("model", config.base)) void chooseBase(config.base, true);
    };
    $("#player-fullscreen").onclick = async () => {
      const canvas = $("#player-canvas");
      if (!viewport || canvas.hidden || !canvas.requestFullscreen) { deps.toast("Plein écran 3D indisponible.", "error"); return; }
      try { await canvas.requestFullscreen(); canvas.focus(); } catch (_) { deps.toast("Plein écran refusé.", "error"); }
    };
    $("#player-animation-file").onchange = () => void loadAnimationFile();
    $("#player-animation-clip").onchange = () => void loadSelectedClip();
    $("#player-frame").oninput = (event) => viewport?.seekAnimation(Number(event.target.value));
    $("#player-loop").onchange = (event) => viewport?.loopAnimation(event.target.checked);
    $("#player-play").onclick = () => viewport?.playAnimation(!viewport?.animation?.playing);
    mountControls(); renderEquipped();
  }

  function show() {
    if (!deps || active) return;
    active = true; refreshBaseChoices(); refreshEquipmentChoices(); refreshAnimationChoices(); renderEquipped();
    if (config.base) void chooseBase(config.base, true);
    else { setPlaceholder("Aucun modèle chargé."); $("#player-equip").disabled = true; }
  }

  function hide() {
    if (!active) return;
    active = false; baseSequence += 1; animationSequence += 1;
    viewport?.dispose(true); viewport = null; currentReports = [];
  }

  async function open(rel, options = {}) {
    if (!deps) return;
    active = true;
    const preserve = config.base === rel;
    const loading = chooseBase(rel, preserve), serial = baseSequence;
    await loading;
    if (!sameView(viewport, serial) || config.base !== rel) return;
    if (options.animationRel && assetFor("animation", options.animationRel)) {
      config.animationFile = options.animationRel; refreshAnimationChoices();
      await loadAnimationFile(Number.isInteger(options.animationIndex) ? options.animationIndex : null);
    }
  }

  function refresh() {
    if (!active) return;
    // A temporarily missing base should be diagnosed without discarding the
    // other selections. If it reappears on a later scan, restore the scene.
    if (config.base && !assetFor("model", config.base)) { void chooseBase(config.base, true); return; }
    if (config.animationFile && !assetFor("animation", config.animationFile)) {
      config.animationFile = ""; config.animationIndex = null;
    }
    // The scan may have changed a file in place or its texture revision. Keep
    // the selections but reload the GPU resources rather than showing stale data.
    if (config.base) void chooseBase(config.base, true);
    else { refreshBaseChoices(); refreshEquipmentChoices(); refreshAnimationChoices(); }
  }

  function configure(settings) { if (viewport && active) void viewport.configure(settings); }
  return { wire, show, hide, open, refresh, configure };
})();
