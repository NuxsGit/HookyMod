"use strict";

/* Vérification optionnelle avec un vrai navigateur WebGL2, sans asset du jeu.
   Installer playwright-core séparément et fournir HOOKY_CHROMIUM (facultatif si
   Playwright connaît déjà Chromium). HOOKY_PLAYWRIGHT peut nommer le répertoire
   de playwright-core installé hors du dépôt. Un rendu logiciel est suffisant. */
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.HOOKY_PLAYWRIGHT || "playwright-core");
const web = path.resolve(__dirname, "../hookycreator/web");
// Synthetic 4x4 RGBA PNGs: png_bytes(4, 4, red*8 + blue*8) and green*16.
const redBlue = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAFUlEQVR4nGP4z8DwHxkzYAig8TEFACxQH+H1DG8tAAAAAElFTkSuQmCC", "base64");
const green = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAD0lEQVR4nGNg+I8GSRcAACxQH+H81I9XAAAAAElFTkSuQmCC", "base64");

function geometry(materials = ["A", "B"]) {
  const header = Buffer.from(JSON.stringify({ version: 1, meshes: materials.map((name, index) => ({
    name, material: name, vertices: 4, triangles: 2, normals: true, uvs: true,
    texture: name === "A" || name === "B" ? { path: `Tex/${name}.bntx`, index } : null,
  })) }));
  const start = Math.ceil((8 + header.length) / 4) * 4;
  const blob = Buffer.alloc(start + materials.length * (12 + 12 + 8 + 6) * 4);
  blob.write("H3D1"); blob.writeUInt32LE(header.length, 4); header.copy(blob, 8);
  let cursor = start;
  const floats = (values) => { for (const value of values) { blob.writeFloatLE(value, cursor); cursor += 4; } };
  const ints = (values) => { for (const value of values) { blob.writeUInt32LE(value, cursor); cursor += 4; } };
  for (const [index] of materials.entries()) {
    const left = materials.length === 1 ? -.5 : index - 1;
    floats([left,-.5,0, left+1,-.5,0, left+1,.5,0, left,.5,0]);
    floats([0,0,1, 0,0,1, 0,0,1, 0,0,1]);
    floats([0,1, 1,1, 1,0, 0,0]);
    ints([0,1,2, 0,2,3]);
  }
  assert.equal(cursor, blob.length);
  return blob;
}

function skin(materials = ["A", "B"], boneName = "Root") {
  const bone = { name: boneName, parent: -1, scale: [1,1,1], rotation: [0,0,0,1], translation: [0,0,0],
    inverse: [1,0,0,0, 0,1,0,0, 0,0,1,0], ssc: false };
  const header = Buffer.from(JSON.stringify({ version: 1, bones: [bone],
    meshes: materials.map((name) => ({ name, vertices: 4 })) }));
  const start = Math.ceil((8 + header.length) / 4) * 4, blob = Buffer.alloc(start + materials.length * 4 * 24);
  blob.write("H3S1"); blob.writeUInt32LE(header.length, 4); header.copy(blob, 8);
  for (let i = 0; i < materials.length; i += 1) for (let j = 0; j < 4; j += 1) blob[start + i*96 + 64 + j*8] = 255;
  return blob;
}

async function main() {
  const blob = geometry(), skinBlob = skin(), outfitBlob = geometry(["C"]);
  const outfitSkin = skin(["C"]), incompatibleSkin = skin(["C"], "Other");
  const files = new Set(["model_viewer.js", "model_animation.js", "i18n.js", "shaders/common.glsl", "shaders/easu.frag.glsl",
    "shaders/rcas.frag.glsl", "shaders/fullscreen.vert.glsl"]);
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname === "/") {
      response.writeHead(200, { "Content-Type": "text/html" });
      return response.end('<!doctype html><canvas id="view" style="width:400px;height:300px"></canvas>'
        + '<script src="/static/model_animation.js"></script><script src="/static/model_viewer.js"></script>');
    }
    if (url.pathname === "/i18n") {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      return response.end('<!doctype html><html lang="fr"><body><h1>Réglages</h1>'
        + '<input placeholder="Filtrer…" value="user input"><div id="dynamic"></div>'
        + '<div id="summary"></div><div id="animation"></div><div id="validation"></div>'
        + '<script src="/static/i18n.js"></script></body></html>');
    }
    const binary = { "/skin": skinBlob, "/skin-c": outfitSkin, "/skin-other": incompatibleSkin,
      "/geometry": blob, "/geometry-c": outfitBlob, "/slow-geometry": outfitBlob }[url.pathname];
    if (binary) {
      const send = () => {
        if (response.destroyed) return;
        response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": binary.length });
        response.end(binary);
      };
      if (url.pathname === "/slow-geometry") setTimeout(send, 250); else send();
      return;
    }
    if (url.pathname === "/api/texture/png") {
      const asset = url.searchParams.get("path"), index = url.searchParams.get("index");
      if ((asset === "Tex/A.bntx" && index === "0") || (asset === "Tex/B.bntx" && index === "1")) {
        response.writeHead(200, { "Content-Type": "image/png" });
        return response.end(index === "0" ? redBlue : green);
      }
    }
    const name = decodeURIComponent(url.pathname.replace(/^\/static\//, ""));
    if (files.has(name)) {
      response.writeHead(200, { "Content-Type": name.endsWith(".js") ? "text/javascript; charset=utf-8" : "text/plain; charset=utf-8" });
      return response.end(fs.readFileSync(path.join(web, name)));
    }
    response.writeHead(404); response.end("not found");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true,
      ...(process.env.HOOKY_CHROMIUM ? { executablePath: process.env.HOOKY_CHROMIUM } : {}),
      args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await browser.newPage({ viewport: { width: 640, height: 480 } });
    const errors = []; page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async () => {
      window.notes = [];
      window.viewer = new ModelViewport(document.querySelector("#view"), {
        textured: true, onStatus: (text) => window.notes.push(text),
      });
      await window.viewer.load("/geometry");
    });
    await page.waitForFunction(() => window.viewer.textureMap.size === 2
      && [...window.viewer.textureMap.values()].every((item) => item.texture));
    const native = await page.evaluate(() => {
      const v = window.viewer, gl = v.gl;
      v.yaw = 0; v.pitch = 0; v.draw();
      const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
      gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      window.nativePixels = pixels;
      let red = 0, blue = 0, green = 0, redY = 0, blueY = 0;
      for (let y = 0; y < v.canvas.height; y += 1) for (let x = 0; x < v.canvas.width; x += 1) {
        const offset = (y * v.canvas.width + x) * 4;
        const r = pixels[offset], g = pixels[offset + 1], b = pixels[offset + 2];
        if (r > 60 && r > g * 2 && r > b * 2) { red += 1; redY += y; }
        if (b > 60 && b > r * 2 && b > g * 2) { blue += 1; blueY += y; }
        if (g > 60 && g > r * 2 && g > b * 2) green += 1;
      }
      return { triangles: v.model.triangles, red, blue, green,
        redY: redY / red, blueY: blueY / blue, error: gl.getError() };
    });
    assert.equal(native.triangles, 4);
    assert.equal(native.error, 0);
    assert.ok(native.red > 100 && native.blue > 100 && native.green > 100, JSON.stringify(native));
    assert.ok(native.redY > native.blueY, "l'origine V=0 doit correspondre au haut du PNG");
    // Real GPU skinning using synthetic geometry and a synthetic cubic clip.
    const animated = await page.evaluate(async () => {
      const v = window.viewer, gl = v.gl;
      const clip = { frames: 10, fps: 30, loop: true, bones: [{ name: "Root", base: {}, ssc: false,
        curves: [{ target: "translation.x", interpolation: "cubic", frames: [0,10], values: [0,.5], slopes: [.05,.05] }] }] };
      const matched = await v.setAnimation("/skin", clip); v.playAnimation(false);
      function capture(frame) {
        v.seekAnimation(frame); v.draw();
        const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
        gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      }
      const first = capture(0), second = capture(8);
      let different = 0;
      for (let i = 0; i < first.length; i += 1) if (first[i] !== second[i]) different += 1;
      const error = gl.getError(); v.stopAnimation();
      return { matched: matched.matched, missing: matched.missing, different, triangles: v.model.triangles, error };
    });
    assert.equal(animated.matched, 1); assert.equal(animated.missing, 0);
    assert.equal(animated.triangles, 4); assert.equal(animated.error, 0);
    assert.ok(animated.different > 100, JSON.stringify(animated));

    // Player composition: each outfit must use its own geometry AND its own
    // skin. The fixture C has one mesh, while the base has two; sharing the
    // base skin would be rejected. These are entirely synthetic assets.
    const composition = await page.evaluate(async () => {
      const v = window.viewer, gl = v.gl;
      const clip = { frames: 10, fps: 30, loop: true, bones: [{ name: "Root", base: {}, ssc: false,
        curves: [{ target: "translation.x", interpolation: "cubic", frames: [0,10], values: [0,.5], slopes: [.05,.05] }] }] };
      await v.setAnimation("/skin", clip); v.playAnimation(false);
      const body = await v.setLayer("body", { geometryUrl: "/geometry-c", skinUrl: "/skin-c", mode: "skin" });
      const independent = v.layers.get("body").skin !== v.skin && v.layers.get("body").skin.meshes.length === 1
        && v.skin.meshes.length === 2 && v.layers.get("body").binding.matched === 1;
      v.setBaseVisible(false);
      function capture(frame) {
        v.seekAnimation(frame); v.draw();
        const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
        gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      }
      const first = capture(0), last = capture(8);
      let outfitMotion = 0;
      for (let i = 0; i < first.length; i += 1) if (first[i] !== last[i]) outfitMotion += 1;
      let mismatch = "";
      const savedBody = v.layers.get("body");
      try { await v.setLayer("body", { geometryUrl: "/geometry-c", skinUrl: "/skin", mode: "skin" }); }
      catch (error) { mismatch = error.message; }
      const mismatchPreserved = v.layers.get("body") === savedBody;
      const badTracks = await v.setLayer("head", { geometryUrl: "/geometry-c", skinUrl: "/skin-other", mode: "skin" });
      const attached = await v.setLayer("right", { geometryUrl: "/geometry-c", mode: "bone", bone: "Root",
        offset: [.25,0,0], rotation: [0,0,0], scale: 1 });
      v.seekAnimation(0); v.draw();
      const loc = gl.getUniformLocation(v.scene, "u_attachment");
      const x0 = gl.getUniform(v.scene, loc)[12];
      v.seekAnimation(8); v.draw();
      const x8 = gl.getUniform(v.scene, loc)[12];
      v.removeLayer("head");
      const original = gl.drawElements.bind(gl), counts = [];
      gl.drawElements = (...args) => { counts.push(args[1]); return original(...args); };
      v.draw(); gl.drawElements = original;
      const hiddenDrawn = counts.reduce((a, b) => a + b, 0);

      const oldBytes = v.geometryBytes; v.geometryBytes = 128 * 1024 * 1024;
      let geometryLimit = "";
      try { await v.setLayer("left", { geometryUrl: "/geometry-c", mode: "static" }); }
      catch (error) { geometryLimit = error.message; }
      v.geometryBytes = oldBytes;
      const oldSkinBytes = v.skinBytes; v.skinBytes = 64 * 1024 * 1024;
      let skinLimit = "";
      try { await v.setLayer("left", { geometryUrl: "/geometry-c", skinUrl: "/skin-c", mode: "skin" }); }
      catch (error) { skinLimit = error.message; }
      v.skinBytes = oldSkinBytes;

      // An invalid later request must cancel the old pending replacement while
      // keeping the already installed outfit, not commit the delayed result.
      const slow = v.setLayer("body", { geometryUrl: "/slow-geometry", mode: "static" });
      let invalid = "";
      try { await v.setLayer("body", { geometryUrl: "/geometry-c", mode: "bone", bone: "Missing" }); }
      catch (error) { invalid = error.message; }
      const slowResult = await slow;
      const stillOld = v.layers.get("body") === savedBody;
      const previousBuffer = savedBody.parts[0].buffers[0], previousTexture = savedBody.skinTexture;
      const beforeDelete = gl.isBuffer(previousBuffer) && gl.isTexture(previousTexture);
      const slowAgain = v.setLayer("body", { geometryUrl: "/slow-geometry", mode: "static" });
      const replacement = await v.setLayer("body", { geometryUrl: "/geometry-c", mode: "static", offset: [1,0,0] });
      const superseded = await slowAgain;
      const freed = !gl.isBuffer(previousBuffer) && !gl.isTexture(previousTexture);
      const pendingRemove = v.setLayer("left", { geometryUrl: "/slow-geometry", mode: "static" });
      v.removeLayer("left"); await pendingRemove;
      const noPending = v.pendingLayers.size === 0 && v.layerRequests.size === 0 && !v.layers.has("left");
      const removedBuffer = v.layers.get("body").parts[0].buffers[0];
      v.removeLayer("body"); v.removeLayer("right");
      const removed = !gl.isBuffer(removedBuffer) && v.layers.size === 0;
      v.setBaseVisible(true); v.stopAnimation(); v.draw();
      return { body, independent, outfitMotion, mismatch, mismatchPreserved, badTracks, attached, x0, x8, hiddenDrawn,
        geometryLimit, skinLimit, invalid, slowResult, stillOld, beforeDelete, replacement, superseded, freed, noPending,
        removed, error: gl.getError() };
    });
    assert.equal(composition.independent, true);
    assert.equal(composition.body.matched, 1);
    assert.ok(composition.outfitMotion > 100, JSON.stringify(composition));
    assert.match(composition.mismatch, /Squelette|correspondent/); assert.equal(composition.mismatchPreserved, true);
    assert.match(composition.badTracks.warning, /Tenue statique/);
    assert.equal(composition.attached.bone, "Root");
    assert.ok(Math.abs(composition.x0 - .25) < .001 && Math.abs(composition.x8 - .65) < .001, JSON.stringify(composition));
    assert.equal(composition.hiddenDrawn, 12);
    assert.match(composition.geometryLimit, /Composition trop volumineuse/);
    assert.match(composition.skinLimit, /Skins de la composition/);
    assert.match(composition.invalid, /os reconnu/);
    assert.equal(composition.slowResult, null); assert.equal(composition.stillOld, true);
    assert.equal(composition.beforeDelete, true); assert.equal(composition.replacement.mode, "static");
    assert.equal(composition.superseded, null); assert.equal(composition.freed, true);
    assert.equal(composition.noPending, true); assert.equal(composition.removed, true); assert.equal(composition.error, 0);

    const box = await page.locator("#view").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 20);
    await page.mouse.up(); await page.mouse.wheel(0, -200);
    const controls = await page.evaluate(() => ({ yaw: window.viewer.yaw, pitch: window.viewer.pitch, zoom: window.viewer.zoom }));
    assert.ok(controls.yaw !== 0 && controls.pitch !== 0 && controls.zoom > 1);
    await page.evaluate(() => { window.viewer.yaw = 0; window.viewer.pitch = 0; window.viewer.zoom = 1; });

    await page.evaluate(() => window.viewer.setMode("fsr"));
    const fsr = await page.evaluate(() => {
      const v = window.viewer, gl = v.gl, original = gl.drawElements.bind(gl), counts = [];
      gl.drawElements = (...args) => { counts.push(args[1]); return original(...args); };
      v.draw(); gl.drawElements = original;
      const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
      gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      let different = 0, red = 0, blue = 0, green = 0, redY = 0, blueY = 0;
      for (let i = 0; i < pixels.length; i += 1) if (pixels[i] !== window.nativePixels[i]) different += 1;
      for (let y = 0; y < v.canvas.height; y += 1) for (let x = 0; x < v.canvas.width; x += 1) {
        const at = (y * v.canvas.width + x) * 4;
        const r = pixels[at], g = pixels[at + 1], b = pixels[at + 2];
        if (r > 60 && r > g * 2 && r > b * 2) { red += 1; redY += y; }
        if (b > 60 && b > r * 2 && b > g * 2) { blue += 1; blueY += y; }
        if (g > 60 && g > r * 2 && g > b * 2) green += 1;
      }
      return { mode: v.mode, drawn: counts.reduce((a, b) => a + b, 0), different,
        red, blue, green, redY: redY / red, blueY: blueY / blue,
        size: [v.targets.input.width, v.targets.input.height, v.targets.output.width, v.targets.output.height],
        error: gl.getError() };
    });
    assert.equal(fsr.mode, "fsr"); assert.equal(fsr.drawn, 12); assert.equal(fsr.error, 0);
    assert.ok(fsr.different > 100, JSON.stringify(fsr));
    assert.ok(fsr.red > 100 && fsr.blue > 100 && fsr.green > 100 && fsr.redY > fsr.blueY, JSON.stringify(fsr));
    assert.ok(fsr.size[0] < fsr.size[2] && fsr.size[1] < fsr.size[3]);

    const profiles = await page.evaluate(async () => {
      const v = window.viewer, gl = v.gl, result = [];
      for (const [quality, scale] of [["ultra", .77], ["quality", .67], ["balanced", .59], ["performance", .5]]) {
        await v.configure({ viewer_upscale: "fsr", fsr_quality: quality, fsr_sharpness: .8, viewer_msaa: "off" });
        v.draw();
        result.push({ quality, scale, input: [v.targets.input.width, v.targets.input.height],
          output: [v.canvas.width, v.canvas.height], msaa: Boolean(v.targets.input.msaa),
          sharpness: gl.getUniform(v.fsr.rcas, gl.getUniformLocation(v.fsr.rcas, "u_sharpness")), error: gl.getError() });
      }
      await v.configure({ viewer_upscale: "fsr", fsr_quality: "quality", viewer_msaa: "2" }); v.draw();
      const msaa = { requested: Boolean(v.targets.input.msaa), max: gl.getParameter(gl.MAX_SAMPLES), error: gl.getError() };
      const descriptor = Object.getOwnPropertyDescriptor(window, "devicePixelRatio");
      Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });
      try {
        await v.configure({ viewer_upscale: "native", viewer_dpr: "1", viewer_limit: "1600" }); v.draw();
        const dpr1 = [v.canvas.width, v.canvas.height];
        await v.configure({ viewer_upscale: "native", viewer_dpr: "2", viewer_limit: "1600" }); v.draw();
        const dpr2 = [v.canvas.width, v.canvas.height];
        v.canvas.style.width = "1400px"; v.canvas.style.height = "900px";
        await v.configure({ viewer_upscale: "native", viewer_dpr: "2", viewer_limit: "800", viewer_fps: "15" }); v.draw();
        const capped = [v.canvas.width, v.canvas.height], fps = v.renderSettings.viewer_fps;
        v.canvas.style.width = "400px"; v.canvas.style.height = "300px";
        return { result, msaa, dpr1, dpr2, capped, fps, error: gl.getError() };
      } finally {
        if (descriptor) Object.defineProperty(window, "devicePixelRatio", descriptor);
        else delete window.devicePixelRatio;
        await v.configure({ viewer_upscale: "fsr", fsr_quality: "quality" }); v.draw();
      }
    });
    for (const profile of profiles.result) {
      assert.deepEqual(profile.input, profile.output.map((value) => Math.max(1, Math.round(value * profile.scale))));
      assert.equal(profile.msaa, false); assert.ok(Math.abs(profile.sharpness - .8) < .001); assert.equal(profile.error, 0);
    }
    assert.equal(profiles.msaa.requested, profiles.msaa.max >= 2); assert.equal(profiles.msaa.error, 0);
    assert.deepEqual(profiles.dpr1, [400, 300]); assert.deepEqual(profiles.dpr2, [800, 600]);
    assert.deepEqual(profiles.capped, [800, 514]); assert.equal(profiles.fps, 15); assert.equal(profiles.error, 0);

    const wires = await page.evaluate(() => {
      const v = window.viewer, gl = v.gl, original = gl.drawElements.bind(gl), counts = [];
      v.setWire(true); gl.drawElements = (...args) => { counts.push([args[0], args[1]]); return original(...args); };
      v.draw(); gl.drawElements = original;
      return { triangles: counts.filter((item) => item[0] === gl.TRIANGLES).reduce((a, b) => a + b[1], 0),
        lines: counts.filter((item) => item[0] === gl.LINES).reduce((a, b) => a + b[1], 0), error: gl.getError() };
    });
    assert.deepEqual(wires, { triangles: 12, lines: 24, error: 0 });

    // Une panne de shader doit revenir au natif, puis pouvoir être corrigée.
    await page.evaluate(async () => { window.viewer.dispose();
      window.viewer = new ModelViewport(document.querySelector("#view"), { textured: false });
      await window.viewer.load("/geometry"); });
    await page.route("**/static/shaders/**", (route) => route.fulfill({ status: 404, body: "indisponible" }));
    await page.evaluate(() => window.viewer.setMode("fsr"));
    const fallback = await page.evaluate(() => ({ mode: window.viewer.mode, notes: window.viewer.notes }));
    assert.equal(fallback.mode, "native"); assert.ok(fallback.notes.some((note) => note.includes("FSR 1 indisponible")));
    await page.unroute("**/static/shaders/**");
    await page.evaluate(() => window.viewer.setMode("fsr"));
    assert.equal(await page.evaluate(() => window.viewer.mode), "fsr");
    // Language changes translate existing and dynamically inserted UI text,
    // preserve typed values, and reverse cleanly back to French.
    const translated = await browser.newPage();
    translated.on("pageerror", (error) => errors.push(String(error)));
    await translated.goto(`http://127.0.0.1:${server.address().port}/i18n`);
    assert.ok(await translated.evaluate(() => !!window.HookyI18n), JSON.stringify(errors));
    for (const [lang, heading, dynamic, summary, clip] of [
      ["en", "Settings", "Videos", "5.62 MiB · source file", "1 skeletal clip(s), 1 with a supported header; curves are checked on opening."],
      ["ru", "Настройки", "Видео", "5.62 MiB · исходный файл", "Скелетных клипов: 1, с поддерживаемым заголовком: 1; кривые проверяются при открытии."],
      ["zh", "设置", "视频", "5.62 MiB · 源文件", "1 个骨骼片段，其中 1 个标头受支持；打开时检查曲线。"],
      ["fr", "Réglages", "Vidéos", "5.62 MiB · fichier source", "1 clip(s) squelettique(s), 1 avec en-tête pris en charge ; courbes vérifiées à l'ouverture."],
    ]) {
      await translated.evaluate((value) => {
        HookyI18n.setLanguage(value);
        document.querySelector("#dynamic").textContent = "Vidéos";
        document.querySelector("#summary").textContent = "5.62 MiB · fichier source";
        document.querySelector("#animation").textContent = "1 clip(s) squelettique(s), 1 avec en-tête pris en charge ; courbes vérifiées à l'ouverture.";
        document.querySelector("#validation").textContent = "1 modèle(s) vérifié(s) · 2 en vérification · 3 non pris en charge masqué(s)";
      }, lang);
      await translated.waitForFunction((value) => document.querySelector("#dynamic").textContent === value, dynamic);
      assert.equal(await translated.locator("h1").innerText(), heading);
      assert.equal(await translated.locator("#summary").innerText(), summary);
      assert.equal(await translated.locator("#animation").innerText(), clip);
      assert.equal(await translated.locator("input").inputValue(), "user input");
      assert.equal(await translated.evaluate(() => document.documentElement.lang), lang);
      const dialog = await translated.evaluate(() => HookyI18n.translate(
        "Remplacer la copie de travail ? Aucun transcodage ni vérification en jeu ; NDVE et RESTBL ne seront pas mis à jour."));
      assert.match(dialog, /NDVE/); assert.match(dialog, /RESTBL/);
      const audioDialog = await translated.evaluate(() => HookyI18n.translate(
        "Remplacer la copie de travail ? Le son sera converti en BWAV PCM16 expérimental. BARS et RESTBL ne seront pas mis à jour ; la compatibilité en jeu n'est pas garantie."));
      assert.match(audioDialog, /BARS/); assert.match(audioDialog, /RESTBL/);
      const checkpointDialog = await translated.evaluate(() => HookyI18n.translate(
        "La session ou les réglages n'ont pas pu être enregistrés. Quitter quand même ?"));
      if (lang !== "fr") {
        assert.ok(!dialog.startsWith("Remplacer"));
        assert.ok(!audioDialog.startsWith("Remplacer"));
        assert.ok(!checkpointDialog.startsWith("La session"));
        assert.ok(!(await translated.locator("#validation").innerText()).includes("vérifié"));
        const viewStatus = await translated.evaluate(() => HookyI18n.translate(
          "42 triangles · FSR 1 expérimental · 50 % · image plafonnée à 800 px / densité ×1 · plus de 16 textures : certaines non affichées · albédos limités à 1 024 px dans la vue"));
        assert.ok(!viewStatus.includes("expérimental") && !viewStatus.includes("plafonnée")
          && !viewStatus.includes("non affichées") && !viewStatus.includes("albédos"), viewStatus);
        const diagnostic = await translated.evaluate(() => HookyI18n.translate("Non restauré : User/Example.bfres"));
        assert.ok(diagnostic.includes("User/Example.bfres") && !diagnostic.startsWith("Non restauré"));
        const poseError = await translated.evaluate(() => HookyI18n.translate("Animation interrompue, pose de repos : Matrice d'animation singulière."));
        assert.ok(!poseError.includes("Animation interrompue") && !poseError.includes("singulière"), poseError);
        const scaleError = await translated.evaluate(() => HookyI18n.translate("Animation indisponible : Compensation d'échelle impossible pour « UserBone » : échelle parent nulle."));
        assert.ok(scaleError.includes("UserBone") && !scaleError.includes("échelle"), scaleError);
      }
    }
    await translated.close();
    assert.deepEqual(errors, []);
    const cleanup = await page.evaluate(() => {
      const view = window.viewer, gl = view.gl;
      view.dispose();
      const lostBeforeClosing = gl.isContextLost();
      view.dispose(true); view.dispose(true);
      return { lostBeforeClosing, released: view.contextReleased, lostAfterClosing: gl.isContextLost() };
    });
    assert.deepEqual(cleanup, { lostBeforeClosing: false, released: true, lostAfterClosing: true });
    console.log("WebGL2 / FSR 1 smoke: OK — profiles, triangles, textures, independent skins, attachments, limits, races, fallback and cleanup");
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
