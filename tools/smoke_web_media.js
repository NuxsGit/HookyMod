"use strict";

/* End-to-end browser smoke on an isolated synthetic project, not game files.
   Install requirements.txt and the isolated tools/webgl dependencies first.
   HOOKY_CHROMIUM, HOOKY_PLAYWRIGHT and HOOKY_PYTHON may override paths.
   HOOKY_DESKTOP_BACKEND optionally selects a built, authenticated sidecar. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require(process.env.HOOKY_PLAYWRIGHT || "playwright-core");
const ROOT = path.resolve(__dirname, "..");

// Keep the backend alive until stdin closes; all test inputs and outputs live in
// the disposable directory. The fixtures contain no user or game resources.
const PYTHON_SERVER = String.raw`
import json, os, struct, subprocess, sys, wave
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from desktop_fixture import make_desktop_fixture
from synth_animation import fska_fixture, named_rigid_model
from hookycreator.server import HookyCreatorServer
base = Path(sys.argv[1])
root = make_desktop_fixture(base / 'romfs')
rigged = bytearray(named_rigid_model())
# Deliberately use a model name equal to a UI translation key. It must remain
# a user-supplied name, not become "File" in the English interface.
struct.pack_into('<Q', rigged, 0x208, 0x1840)
struct.pack_into('<H', rigged, 0x1840, 7)
rigged[0x1842:0x1849] = b'Fichier'
(root / 'Model' / 'Rigged.bfres').write_bytes(rigged)
# The model's root rests at x=5. Keep the clip near that rest pose so its
# motion remains visible instead of moving the entire model off-screen.
playable = bytearray(fska_fixture())
struct.pack_into('<4f', playable, 0x520, 5., .1, 6., .1)
(root / 'Model' / 'Rigged.anim.bfres').write_bytes(playable)
with wave.open(str(base / 'replacement.wav'), 'wb') as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(48000)
    output.writeframes(struct.pack('<4000h', *[i % 500 for i in range(4000)]))
video = (root / 'Model' / 'Synthetic.webm').read_bytes()
(base / 'replacement.webm').write_bytes(video + b'synthetic replacement')
backend = os.environ.get('HOOKY_DESKTOP_BACKEND')
if backend:
    # Use the packaged backend without borrowing any Python modules or library
    # paths from the test runner. The token stays in the pipe and URL fragment.
    env = dict(os.environ)
    env.pop('PYTHONPATH', None); env.pop('LD_LIBRARY_PATH', None)
    sidecar = subprocess.Popen([str(Path(backend).resolve()), str(root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=sys.stderr, env=env)
    try:
        line = sidecar.stdout.readline()
        if not line:
            raise RuntimeError('The sidecar stopped before reporting its session')
        ready = json.loads(line)
        if 'error' in ready:
            raise RuntimeError(ready['error'])
        print(json.dumps({'origin': 'http://127.0.0.1:' + str(ready['port']),
                          'root': str(root), 'token': ready['token']}), flush=True)
        sys.stdin.read()
    finally:
        sidecar.stdin.close()
        try:
            sidecar.wait(timeout=8)
        except subprocess.TimeoutExpired:
            sidecar.kill(); sidecar.wait(timeout=5)
        sidecar.stdout.close()
        if sidecar.returncode and sys.exc_info()[0] is None:
            raise RuntimeError(f'Sidecar stopped with status {sidecar.returncode}')
else:
    server = HookyCreatorServer(root, host='127.0.0.1', port=0).start()
    print(json.dumps({'origin': 'http://127.0.0.1:' + str(server.port), 'root': str(root)}), flush=True)
    try:
        sys.stdin.read()
    finally:
        server.stop()
`;

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "hooky-browser-media-"));
  const child = spawn(process.env.HOOKY_PYTHON || "python", ["-u", "-c", PYTHON_SERVER, temp], {
    cwd: ROOT, env: { ...process.env, PYTHONUTF8: "1" }, stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "", browser, failed = false;
  child.stderr.on("data", (data) => { stderr = (stderr + data.toString()).slice(-12000); });
  child.stdin.on("error", () => { /* backend may have failed before stdin closed */ });
  const exited = new Promise((resolve) => child.once("close", (code, signal) => resolve({ code, signal })));
  try {
    const info = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Synthetic backend did not start")), 30000);
      const lines = readline.createInterface({ input: child.stdout });
      lines.once("line", (line) => {
        clearTimeout(timer); lines.close();
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      exited.then((result) => { clearTimeout(timer); reject(new Error(`Backend stopped at startup: ${JSON.stringify(result)}`)); });
    });
    browser = await chromium.launch({ headless: true,
      ...(process.env.HOOKY_CHROMIUM ? { executablePath: process.env.HOOKY_CHROMIUM } : {}),
      args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, acceptDownloads: true });
    page.setDefaultTimeout(60000);
    page.setDefaultNavigationTimeout(60000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    const entry = info.token ? `/desktop-login#token=${encodeURIComponent(info.token)}` : "/";
    await page.goto(info.origin + entry);
    if (info.token) {
      await page.waitForURL(info.origin + "/");
      const session = (await page.context().cookies()).find((cookie) => cookie.name.startsWith("hooky_session_"));
      assert.ok(session?.httpOnly && session.sameSite === "Strict");
      assert.equal(await page.evaluate(() => document.cookie.includes("hooky_session_")), false);
      assert.match(await page.evaluate(async () => (await fetch("/")).headers.get("Content-Security-Policy")),
        /frame-ancestors 'none'/);
    }
    await page.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));

    // Each language and theme is saved by the real server and restored on reload.
    for (const [lang, label, engine, menu, query, command] of [
      ["en", "Settings", "Rust engine", "Refresh project", "videos", "Go to videos"],
      ["ru", "Настройки", "Движок Rust", "Обновить проект", "видео", "Перейти к видео"],
      ["zh", "设置", "Rust 引擎", "刷新项目", "视频", "前往视频"],
      ["fr", "Réglages", "Moteur Rust", "Rafraîchir le projet", "videos", "Aller aux vidéos"],
    ]) {
      await page.locator('[data-view="settings"]').click();
      await page.locator("#set-language").selectOption(lang);
      assert.equal(await page.evaluate(() => finishPendingSettings()), true);
      await page.reload();
      await page.waitForFunction((code) => document.documentElement.lang === code, lang);
      assert.equal(await page.locator('[data-view="settings"] span').innerText(), label);
      await page.waitForFunction((text) => document.querySelector("#engine-list")?.innerText.includes(text), engine);
      await page.locator('.menu-btn[data-menu="fichier"]').click();
      await page.waitForFunction((text) => document.querySelector("#menus").innerText.includes(text), menu);
      await page.keyboard.press("Escape");
      await page.keyboard.press("Control+k");
      await page.locator("#palette-input").fill(query);
      await page.waitForFunction((text) => document.querySelector("#palette-list").innerText.includes(text), command);
      await page.keyboard.press("Escape");
    }
    await page.locator('[data-view="settings"]').click();
    const colours = [];
    for (const theme of ["light", "dark", "aurora"]) {
      await page.locator("#set-theme").selectOption(theme);
      assert.equal(await page.evaluate(() => finishPendingSettings()), true);
      colours.push(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()));
    }
    assert.equal(new Set(colours).size, 3);
    await page.locator("#set-language").selectOption("en");
    assert.equal(await page.evaluate(() => finishPendingSettings()), true);
    await page.reload();
    await page.waitForFunction(() => document.documentElement.lang === "en"
      && document.documentElement.dataset.theme === "aurora");

    async function downloaded(selector) {
      const [download] = await Promise.all([page.waitForEvent("download"), page.locator(selector).click()]);
      const failure = await download.failure();
      assert.equal(failure, null);
      return fs.readFileSync(await download.path());
    }

    // Follow the animation through the actual inspector to its compatible model.
    await page.locator('[data-view="animations"]').click();
    await page.locator('#animation-grid .tile').filter({ hasText: "Rigged.anim.bfres" }).click();
    await page.waitForSelector("#animation-clip");
    assert.match(await page.locator("#inspector-body").innerText(), /1 skeletal clip/);
    assert.equal(await page.locator("#animation-model").inputValue(), "Model/Rigged.bfres");
    const clip = JSON.parse((await downloaded("#animation-download")).toString());
    assert.equal(clip.name, "Walk"); assert.equal(clip.bones[0].name, "Root");
    await page.locator("#animation-open-model").click();
    await page.waitForFunction(() => modelViewport?.animation?.playing === true, null, { timeout: 60000 });
    assert.equal(await page.locator("#inspector-body h3").innerText(), "Fichier");
    assert.equal(await page.locator('#tabs .tab[data-kind="model"] .label').innerText(), "Fichier");
    await page.locator("#model-animation-play").click();
    await page.waitForFunction(() => modelViewport?.animation?.playing === false);
    const animation = await page.evaluate(() => {
      const viewer = modelViewport, gl = viewer.gl;
      function capture(frame) {
        viewer.seekAnimation(frame); viewer.draw();
        const pixels = new Uint8Array(viewer.canvas.width * viewer.canvas.height * 4);
        gl.readPixels(0, 0, viewer.canvas.width, viewer.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      }
      const first = capture(0), second = capture(5);
      let different = 0;
      for (let i = 0; i < first.length; i += 1) if (first[i] !== second[i]) different += 1;
      return { different, matched: viewer.animation.binding.matched,
        triangles: viewer.model.triangles, error: gl.getError() };
    });
    assert.equal(animation.matched, 1); assert.equal(animation.triangles, 2);
    assert.equal(animation.error, 0); assert.ok(animation.different > 100, JSON.stringify(animation));

    // Verify real audio conversion downloads, plus cancel/accept on the same
    // selected file (cancellation must reset the file input).
    const soundSource = path.join(info.root, "Model", "Silence.bwav");
    const originalSound = fs.readFileSync(soundSource);
    const soundCopy = path.join(info.root, "hookycreator_work", "assets", "Model", "Silence.bwav");
    await page.locator('[data-view="sounds"]').click();
    await page.waitForSelector('#sound-grid .tile:not([hidden])', { timeout: 30000 });
    await page.locator('#sound-grid .tile:not([hidden])').click();
    await page.waitForSelector("#sound-player");
    await page.locator("#sound-player").evaluate((player) => { player.preload = "auto"; player.load(); });
    await page.waitForFunction(() => {
      const player = document.querySelector("#sound-player"); return player && (player.readyState >= 1 || player.error);
    });
    assert.equal(await page.locator("#sound-player").evaluate((player) => player.error), null);
    await page.locator("#sound-player").evaluate((player) => player.play());
    await page.waitForFunction(() => {
      const player = document.querySelector("#sound-player"); return player && (player.currentTime > 0 || player.ended);
    });
    for (const [format, magic] of [["wav", "RIFF"], ["flac", "fLaC"], ["ogg", "OggS"], ["opus", "OggS"]]) {
      const result = await downloaded(`#inspector-body a[href*="format=${format}"]`);
      assert.equal(result.subarray(0, 4).toString(), magic);
    }
    const mp3 = await downloaded('#inspector-body a[href*="format=mp3"]');
    assert.ok(mp3.subarray(0, 3).toString() === "ID3" || (mp3[0] === 0xff && (mp3[1] & 0xe0) === 0xe0));
    let message = "";
    page.once("dialog", async (dialog) => { message = dialog.message(); await dialog.dismiss(); });
    await page.locator("#sound-import-file").setInputFiles(path.join(temp, "replacement.wav"));
    assert.match(message, /Replace the working copy\?/); assert.match(message, /BARS/); assert.match(message, /RESTBL/);
    assert.equal(fs.existsSync(soundCopy), false);
    page.once("dialog", (dialog) => dialog.accept());
    const soundResponse = page.waitForResponse((response) => response.url().endsWith("/api/sound/import")
      && response.request().method() === "POST");
    await page.locator("#sound-import-file").setInputFiles(path.join(temp, "replacement.wav"));
    assert.equal((await (await soundResponse).json()).format, "BWAV PCM16");
    assert.equal(fs.readFileSync(soundCopy).subarray(0, 4).toString(), "BWAV");
    assert.deepEqual(fs.readFileSync(soundSource), originalSound);

    const videoSource = path.join(info.root, "Model", "Synthetic.webm");
    const originalVideo = fs.readFileSync(videoSource);
    const videoCopy = path.join(info.root, "hookycreator_work", "assets", "Model", "Synthetic.webm");
    await page.locator('[data-view="videos"]').click();
    await page.locator('#video-grid .tile').click();
    await page.waitForSelector("#video-player");
    await page.waitForFunction(() => {
      const player = document.querySelector("#video-player"); return player && (player.readyState >= 1 || player.error);
    });
    assert.equal(await page.locator("#video-player").evaluate((player) => player.error), null);
    assert.equal(await page.locator("#video-player").evaluate((player) => player.videoWidth), 32);
    await page.locator("#video-player").evaluate((player) => { player.currentTime = 0.5; });
    await page.waitForFunction(() => {
      const player = document.querySelector("#video-player");
      return player && !player.seeking && Math.abs(player.currentTime - 0.5) < 0.05;
    });
    await page.locator("#video-player").evaluate((player) => { player.muted = true; return player.play(); });
    await page.waitForFunction(() => document.querySelector("#video-player")?.currentTime > 0.5);
    await page.locator("#video-player").evaluate((player) => player.pause());
    assert.deepEqual(await downloaded('#inspector-body a[href*="/api/video/stream"]'), originalVideo);
    message = "";
    page.once("dialog", async (dialog) => { message = dialog.message(); await dialog.dismiss(); });
    await page.locator("#video-import-file").setInputFiles(path.join(temp, "replacement.webm"));
    assert.match(message, /Replace the working copy\?/); assert.match(message, /NDVE/); assert.match(message, /RESTBL/);
    assert.equal(fs.existsSync(videoCopy), false);
    page.once("dialog", (dialog) => dialog.accept());
    const videoResponse = page.waitForResponse((response) => response.url().endsWith("/api/video/import")
      && response.request().method() === "POST");
    await page.locator("#video-import-file").setInputFiles(path.join(temp, "replacement.webm"));
    assert.ok((await videoResponse).ok());
    assert.deepEqual(fs.readFileSync(videoCopy), fs.readFileSync(path.join(temp, "replacement.webm")));
    assert.deepEqual(fs.readFileSync(videoSource), originalVideo);

    // A declined build must leave an existing mod untouched, and the dialog
    // must explicitly explain that accepting replaces it without a backup.
    const sentinel = path.join(info.root, "mods", "HookyMod", "keep.txt");
    fs.mkdirSync(path.dirname(sentinel), { recursive: true });
    fs.writeFileSync(sentinel, "keep");
    await page.locator('[data-view="create"]').click();
    message = "";
    page.once("dialog", async (dialog) => { message = dialog.message(); await dialog.dismiss(); });
    await page.locator('#mod-form button[type="submit"]').click();
    assert.match(message, /mods\//); assert.match(message, /without a backup/);
    assert.equal(fs.readFileSync(sentinel, "utf8"), "keep");
    // User-defined names must not be mistaken for French UI labels.
    await page.locator("#mod-name").fill("Fichier");
    page.once("dialog", (dialog) => dialog.accept());
    const buildResponse = page.waitForResponse((response) => response.url().endsWith("/api/mod/build")
      && response.request().method() === "POST");
    await page.locator('#mod-form button[type="submit"]').click();
    const built = await (await buildResponse).json();
    assert.equal(built.mod, "Fichier"); assert.equal(built.files, 2);
    await page.waitForFunction(() => document.querySelector("#mod-log li span[data-no-i18n]")?.textContent === "Fichier");
    assert.deepEqual(fs.readFileSync(path.join(info.root, "mods", "Fichier", "romfs", "Model", "Synthetic.webm")),
      fs.readFileSync(path.join(temp, "replacement.webm")));
    assert.deepEqual(fs.readFileSync(path.join(info.root, "mods", "Fichier", "romfs", "Model", "Silence.bwav")),
      fs.readFileSync(soundCopy));
    assert.deepEqual(fs.readFileSync(soundSource), originalSound);
    assert.deepEqual(fs.readFileSync(videoSource), originalVideo);
    assert.equal(fs.readFileSync(sentinel, "utf8"), "keep");
    assert.deepEqual(errors, []);
    console.log(`Browser media smoke (${info.token ? "authenticated sidecar" : "source backend"}): OK — `
      + "languages/themes, visible animation, audio/video playback and replacements, build confirmation");
  } catch (error) {
    failed = true;
    if (stderr) console.error("Synthetic backend log:\n" + stderr);
    throw error;
  } finally {
    if (browser) await browser.close();
    child.stdin.end();
    let timer;
    const result = await Promise.race([exited, new Promise((resolve) => { timer = setTimeout(() => resolve(null), 10000); })]);
    clearTimeout(timer);
    if (!result) { child.kill(); await exited; }
    fs.rmSync(temp, { recursive: true, force: true });
    if (!failed && (!result || result.code !== 0)) {
      throw new Error(`Synthetic backend did not exit cleanly: ${JSON.stringify(result)}`);
    }
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
