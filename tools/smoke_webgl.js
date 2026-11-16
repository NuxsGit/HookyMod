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
const redBlue = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAFUlEQVR4nGP4z8DwHxkzYAig8TEFACxQH+H1DG8tAAAAAElFTkSuQmCC", "base64");
const green = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAD0lEQVR4nGNg+I8GSRcAACxQH+H81I9XAAAAAElFTkSuQmCC", "base64");

function geometry() {
  const materials = ["A", "B"];
  const header = Buffer.from(JSON.stringify({ version: 1, meshes: materials.map((name, index) => ({
    name, material: name, vertices: 4, triangles: 2, normals: true, uvs: true,
    texture: { path: `Tex/${name}.bntx`, index },
  })) }));
  const start = Math.ceil((8 + header.length) / 4) * 4;
  const blob = Buffer.alloc(start + 2 * (12 + 12 + 8 + 6) * 4);
  blob.write("H3D1"); blob.writeUInt32LE(header.length, 4); header.copy(blob, 8);
  let cursor = start;
  const floats = (values) => { for (const value of values) { blob.writeFloatLE(value, cursor); cursor += 4; } };
  const ints = (values) => { for (const value of values) { blob.writeUInt32LE(value, cursor); cursor += 4; } };
  for (const left of [-1, 0]) {
    floats([left,-.5,0, left+1,-.5,0, left+1,.5,0, left,.5,0]);
    floats([0,0,1, 0,0,1, 0,0,1, 0,0,1]);
    floats([0,1, 1,1, 1,0, 0,0]);
    ints([0,1,2, 0,2,3]);
  }
  assert.equal(cursor, blob.length);
  return blob;
}

function skin() {
  const bone = { name: "Root", parent: -1, scale: [1,1,1], rotation: [0,0,0,1], translation: [0,0,0],
    inverse: [1,0,0,0, 0,1,0,0, 0,0,1,0], ssc: false };
  const header = Buffer.from(JSON.stringify({ version: 1, bones: [bone],
    meshes: [{ name: "A", vertices: 4 }, { name: "B", vertices: 4 }] }));
  const start = Math.ceil((8 + header.length) / 4) * 4, blob = Buffer.alloc(start + 2 * 4 * 24);
  blob.write("H3S1"); blob.writeUInt32LE(header.length, 4); header.copy(blob, 8);
  for (let i = 0; i < 2; i += 1) for (let j = 0; j < 4; j += 1) blob[start + i*96 + 64 + j*8] = 255;
  return blob;
}

async function main() {
  const blob = geometry(), skinBlob = skin();
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
    if (url.pathname === "/skin") {
      response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": skinBlob.length });
      return response.end(skinBlob);
    }
    if (url.pathname === "/geometry") {
      response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": blob.length });
      return response.end(blob);
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
      ["en", "Settings", "Videos", "5.62 MiB · source file", "1 skeletal clip(s), 1 with supported Euler rotations."],
      ["ru", "Настройки", "Видео", "5.62 MiB · исходный файл", "Скелетных клипов: 1; с поддерживаемыми вращениями Эйлера: 1."],
      ["zh", "设置", "视频", "5.62 MiB · 源文件", "1 个骨骼片段，其中 1 个使用受支持的欧拉旋转。"],
      ["fr", "Réglages", "Vidéos", "5.62 MiB · fichier source", "1 clip(s) squelettique(s), 1 en rotations Euler prises en charge."],
    ]) {
      await translated.evaluate((value) => {
        HookyI18n.setLanguage(value);
        document.querySelector("#dynamic").textContent = "Vidéos";
        document.querySelector("#summary").textContent = "5.62 MiB · fichier source";
        document.querySelector("#animation").textContent = "1 clip(s) squelettique(s), 1 en rotations Euler prises en charge.";
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
      const buildDialog = await translated.evaluate(() => HookyI18n.translate(
        "Assembler ce mod ?\n\nLe nom du dossier est nettoyé : seuls les lettres, chiffres, tirets et _ sont conservés (ou HookyMod s'il ne reste rien). Si ce dossier existe déjà dans mods/, il sera entièrement remplacé, sans sauvegarde."));
      assert.match(buildDialog, /mods\//);
      if (lang !== "fr") {
        assert.ok(!dialog.startsWith("Remplacer"));
        assert.ok(!audioDialog.startsWith("Remplacer"));
        assert.ok(!buildDialog.startsWith("Assembler"));
        assert.ok(!(await translated.locator("#validation").innerText()).includes("vérifié"));
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
    console.log("WebGL2 / FSR 1 smoke: OK — triangles, textures, UV, skin animation, fallback, cleanup and four UI languages");
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
