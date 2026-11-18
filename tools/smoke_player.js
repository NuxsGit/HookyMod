"use strict";

/* Real browser + real local backend on synthetic BFRES data only. Requires the
   isolated Playwright/Chromium dependencies documented in PERFORMANCE.md. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require(process.env.HOOKY_PLAYWRIGHT || "playwright-core");
const ROOT = path.resolve(__dirname, "..");
const PYTHON_SERVER = String.raw`
import json, math, struct, sys
from pathlib import Path
from hookycreator.server import HookyCreatorServer
from tests.synth_animation import fska_fixture, fska_mixed_quaternion_fixture, named_rigid_model, player_variant
from tests import synth
base = Path(sys.argv[1]); root = base / 'source'; model = root / 'Model'; model.mkdir(parents=True)
rig = named_rigid_model()
for name in ('Player_Child', 'Player_Adult', 'Armor_Child_Tunic', 'Weapon_Sword_Test', 'Creature_Unknown'):
    (model / f'{name}.bfres').write_bytes(rig)
bad = bytearray(rig); bad[0x1802:0x1806] = b'Nope'
(model / 'Armor_Child_Unmatched.bfres').write_bytes(bad)
(model / 'Armor_Child_NoSkin.bfres').write_bytes(synth.make_bfres())
(model / 'Armor_Child_<b>Escaped</b>.bfres'.replace('/', '_')).write_bytes(rig)
# The synthetic root rests at x=5. Keep the clip close to its rest position
# so the complete Player workflow exercises visible pixels, not an empty view.
clip = bytearray(fska_fixture())
struct.pack_into('<4f', clip, 0x520, 5., .1, 6., .1)
for age in ('Child', 'Adult'):
    (model / f'Player_{age}_Animation.anim.bfres').write_bytes(clip)
# A second clip deliberately starts far from the model's rest pose; it checks
# that the optional camera follow keeps root motion in frame.
(model / 'Offset.anim.bfres').write_bytes(fska_fixture())
# Mixed rotations and nonuniform rest scale with Maya SSC, without game data.
scaled = player_variant()
for name in ('Player_Child_Scaled', 'Armor_Child_Scaled'):
    (model / f'{name}.bfres').write_bytes(scaled)
mixed = bytearray(fska_mixed_quaternion_fixture())
struct.pack_into('<3f', mixed, 0x360, 2., 3., 4.)
struct.pack_into('<4f', mixed, 0x520, 5., .1, 6., .1)
struct.pack_into('<8f', mixed, 0x900, 0., 0., math.sin(math.pi/4), math.cos(math.pi/4),
                 0., 0., -math.sin(3*math.pi/8), -math.cos(3*math.pi/8))
struct.pack_into('<4f', mixed, 0x760, 0., 0., math.pi/2, 1.)
struct.pack_into('<4f', mixed, 0x7b0, math.pi/2, .05, math.pi/2 + .5, .05)
(model / 'Player_Child_Scaled_Animation.anim.bfres').write_bytes(mixed)
# This clip's second track matches only the outfit, not the base. Its later
# zero scale exercises failure and recovery of an independent outfit skin.
outfit_only = bytearray(scaled)
outfit_only[0x1822:0x1827] = b'Outft'
(model / 'Armor_Child_OnlySingular.bfres').write_bytes(outfit_only)
outfit_singular = bytearray(mixed)
outfit_singular[0x2a2:0x2a7] = b'Outft'
struct.pack_into('<I', outfit_singular, 0x714, 0x04)
struct.pack_into('<4f', outfit_singular, 0x7b0, 1., -.1, 0., -.1)
(model / 'OutfitSingular.anim.bfres').write_bytes(outfit_singular)
# A later singular frame must stop playback without destroying the viewport.
singular = bytearray(fska_fixture())
struct.pack_into('<H', singular, 0x440, 0x0002)
struct.pack_into('<I', singular, 0x444, 0x04)
struct.pack_into('<4f', singular, 0x550, 1., -.1, 0., -.1)
(model / 'Singular.anim.bfres').write_bytes(singular)
server = HookyCreatorServer(root, base / 'work', host='0.0.0.0', port=0).start()
print(json.dumps({'origin': 'http://127.0.0.1:' + str(server.port), 'root': str(root)}), flush=True)
try: sys.stdin.read()
finally: server.stop()
`;

async function exerciseEmbeddedPreview(browser, origin) {
  // The preview proxy embeds the workspace in an iframe. A navigation from a
  // different origin must load, while subsequent API requests stay same-origin.
  const host = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(`<iframe src="${origin}/"></iframe>`);
  });
  await new Promise((resolve, reject) => { host.once("error", reject); host.listen(0, "0.0.0.0", resolve); });
  let page;
  try {
    page = await browser.newPage();
    const loaded = page.waitForResponse((response) => response.url() === `${origin}/`);
    await page.goto(`http://localhost:${host.address().port}/`);
    const documentResponse = await loaded;
    assert.equal(documentResponse.status(), 200, "the embedded preview document must load");
    assert.equal((await documentResponse.request().allHeaders())["sec-fetch-site"], "cross-site");
    const frame = page.frames().find((item) => item.url() === `${origin}/`);
    assert.ok(frame, "the workspace iframe must be available");
    await frame.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));
    const status = await frame.evaluate(async () => {
      const read = await fetch("/api/state?quick=1");
      const write = await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      return [read.status, write.status];
    });
    assert.deepEqual(status, [200, 200], "same-origin iframe API calls must work");
  } finally {
    if (page) await page.close();
    await new Promise((resolve) => host.close(resolve));
  }
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "hooky-player-smoke-"));
  const child = spawn(process.env.HOOKY_PYTHON || "python", ["-u", "-c", PYTHON_SERVER, temp], {
    cwd: ROOT, env: { ...process.env, PYTHONUTF8: "1" }, stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "", browser, failed = false;
  child.stderr.on("data", (data) => { stderr = (stderr + data.toString()).slice(-12000); });
  child.stdin.on("error", () => {});
  const exited = new Promise((resolve) => child.once("close", (code, signal) => resolve({ code, signal })));
  try {
    const info = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Synthetic server did not start")), 30000);
      const lines = readline.createInterface({ input: child.stdout });
      lines.once("line", (line) => {
        clearTimeout(timer); lines.close();
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      exited.then((result) => { clearTimeout(timer); reject(new Error(`Server stopped at startup: ${JSON.stringify(result)}`)); });
    });
    const originals = new Map(fs.readdirSync(path.join(info.root, "Model")).map((name) =>
      [name, fs.readFileSync(path.join(info.root, "Model", name))]));
    browser = await chromium.launch({ headless: true,
      ...(process.env.HOOKY_CHROMIUM ? { executablePath: process.env.HOOKY_CHROMIUM } : {}),
      args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    await exerciseEmbeddedPreview(browser, info.origin);
    const page = await browser.newPage({ viewport: { width: 1400, height: 960 } });
    page.setDefaultTimeout(25000);
    const errors = []; page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(info.origin + "/");
    await page.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));
    // Observe the real viewports without exposing a debug API in the app.
    await page.evaluate(() => {
      const original = ModelViewport.prototype.load;
      ModelViewport.prototype.load = function (...args) {
        (window.__playerViewports ||= []).push(this);
        return original.apply(this, args);
      };
    });

    // Start from Ensembles: an animation finds named character and outfit
    // candidates, excludes explicitly different ages and escapes filenames.
    await page.locator('[data-view="ensembles"]').click();
    await page.locator("#ensemble-animation").selectOption("Model/Player_Child_Animation.anim.bfres");
    let results = await page.locator("#ensemble-associations").innerText();
    assert.match(results, /Player_Child\.bfres/); assert.match(results, /Armor_Child_Tunic/);
    assert.doesNotMatch(results, /Player_Adult\.bfres/);
    assert.equal(await page.locator("#ensemble-associations b").count(), 0, "asset names must not become HTML");
    await page.locator('[data-ensemble-player="Model/Player_Child.bfres"]').click();
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Child.bfres");
    assert.equal(await page.locator("#player-animation-file").inputValue(), "Model/Player_Child_Animation.anim.bfres");
    assert.equal(await page.locator("#player-animation-clip").inputValue(), "0");
    assert.deepEqual(await page.locator("#player-bone option").allTextContents(), ["Choisir un os…", "Root", "Child"]);

    // A large root displacement leaves a fixed camera's frame. The default
    // follow option must keep it visible without changing the pose itself, and
    // turning the option off must restore the static camera center.
    await page.locator("#player-animation-file").selectOption("Model/Offset.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    const follow = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), gl = v.gl, control = document.querySelector("#player-follow-root");
      const initial = control.checked;
      v.playAnimation(false); v.yaw = 0; v.pitch = 0; v.seekAnimation(0);
      const capture = () => {
        v.draw();
        const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
        gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        let visible = 0;
        for (let i = 0; i < pixels.length; i += 4)
          if (pixels[i] > 50 || pixels[i+1] > 50 || pixels[i+2] > 50) visible += 1;
        return { visible, center: [...gl.getUniform(v.scene, gl.getUniformLocation(v.scene, "u_center"))],
          pose: [...HookyAnimation.pose(v.animation.binding, 0).palette.slice(0, 12)] };
      };
      const followed = capture();
      control.checked = false; control.dispatchEvent(new Event("change", { bubbles: true }));
      const fixed = capture();
      control.checked = true; control.dispatchEvent(new Event("change", { bubbles: true }));
      v.stopAnimation(); v.draw();
      const staticCenter = [...gl.getUniform(v.scene, gl.getUniformLocation(v.scene, "u_center"))];
      return { initial, followed, fixed, staticCenter, restCenter: v.model.center, rootIndex: v.rootIndex, error: gl.getError() };
    });
    assert.equal(follow.initial, true); assert.equal(follow.rootIndex, 0); assert.equal(follow.error, 0);
    assert.ok(follow.followed.visible > 100 && follow.fixed.visible === 0, JSON.stringify(follow));
    assert.deepEqual(follow.followed.pose, follow.fixed.pose, "camera follow must not alter the animation");
    assert.ok(Math.abs(follow.fixed.center[0] - follow.restCenter[0]) < .001);
    assert.ok(Math.abs(follow.staticCenter[0] - follow.restCenter[0]) < .001, "a static pose keeps its original center");
    assert.ok(Math.abs(follow.followed.center[0] - (follow.restCenter[0] - 5)) < .001);
    await page.locator("#player-animation-file").selectOption("Model/Player_Child_Animation.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));

    // Equip an independently skinned outfit, then mount an object to a chosen
    // bone with a manually adjusted offset. Both participate in the same clip.
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_Tunic.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("1 os associés"));
    const outfitPixels = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), gl = v.gl;
      v.playAnimation(false); v.setBaseVisible(false); v.yaw = 0; v.pitch = 0;
      const capture = (frame) => {
        v.seekAnimation(frame); v.draw();
        const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
        gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      };
      const first = capture(0), last = capture(8);
      let different = 0, visibleFirst = 0, visibleLast = 0;
      for (let i = 0; i < first.length; i += 4) {
        if (first[i] > 50 || first[i+1] > 50 || first[i+2] > 50) visibleFirst += 1;
        if (last[i] > 50 || last[i+1] > 50 || last[i+2] > 50) visibleLast += 1;
        if (first[i] !== last[i] || first[i+1] !== last[i+1] || first[i+2] !== last[i+2]) different += 1;
      }
      v.setBaseVisible(true); v.playAnimation(true);
      return { visibleFirst, visibleLast, different, error: gl.getError() };
    });
    assert.equal(outfitPixels.error, 0);
    assert.ok(outfitPixels.visibleFirst > 100 && outfitPixels.visibleLast > 100 && outfitPixels.different > 100,
      `independently skinned outfit must remain visible and move: ${JSON.stringify(outfitPixels)}`);
    await page.locator("#player-slot").selectOption("right");
    await page.locator("#player-equipment").selectOption("Model/Weapon_Sword_Test.bfres");
    await page.locator("#player-bone").selectOption("Child");
    await page.locator("#player-transform summary").click();
    await page.locator("#player-offset-x").fill("0.5");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Attaché à Child"));
    assert.deepEqual(await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), body = v.layers.get("body"), right = v.layers.get("right");
      return { independent: body.skin !== v.skin, matched: body.binding?.matched,
        bone: right.bone, offset: right.offset, layerCount: v.layers.size };
    }), { independent: true, matched: 1, bone: "Child", offset: [.5,0,0], layerCount: 2 });

    // Camera follow must leave the attachment matrix untouched, even with a
    // displaced root and an independently animated outfit in the same scene.
    await page.locator("#player-animation-file").selectOption("Model/Offset.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    const attachmentFollow = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), gl = v.gl, control = document.querySelector("#player-follow-root");
      v.playAnimation(false); v.seekAnimation(0);
      const capture = () => {
        v.draw();
        return { center: [...gl.getUniform(v.scene, gl.getUniformLocation(v.scene, "u_center"))],
          matrix: [...gl.getUniform(v.scene, gl.getUniformLocation(v.scene, "u_attachment"))] };
      };
      const followed = capture();
      control.checked = false; control.dispatchEvent(new Event("change", { bubbles: true }));
      const fixed = capture();
      control.checked = true; control.dispatchEvent(new Event("change", { bubbles: true }));
      return { followed, fixed, error: gl.getError() };
    });
    assert.equal(attachmentFollow.error, 0);
    assert.notDeepEqual(attachmentFollow.followed.center, attachmentFollow.fixed.center);
    assert.deepEqual(attachmentFollow.followed.matrix, attachmentFollow.fixed.matrix, "camera follow must not move attached equipment");
    await page.locator("#player-animation-file").selectOption("Model/Player_Child_Animation.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));

    // A valid skin whose bones do not match the animation stays static with a
    // visible diagnostic. An unsupported replacement keeps the previous one.
    await page.locator("#player-slot").selectOption("body");
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_Unmatched.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Tenue statique"));
    for (const [language, warning] of [["en", "Static outfit"], ["ru", "Статичная одежда"],
                                       ["zh", "静态服装"], ["fr", "Tenue statique"]]) {
      await page.evaluate((value) => HookyI18n.setLanguage(value), language);
      assert.match(await page.locator("#player-equipped").innerText(), new RegExp(warning));
      assert.match(await page.locator("#player-equipped").innerText(), /Armor_Child_Unmatched/);
    }
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_NoSkin.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equip-status")?.textContent.includes("L'ancien équipement est conservé"));
    await page.evaluate(() => HookyI18n.setLanguage("en"));
    assert.match(await page.locator("#player-equip-status").innerText(), /^Equipment not added:/);
    assert.match(await page.locator("#player-equip-status").innerText(), /The previous equipment is kept/);
    assert.match(await page.locator("#player-equipped").innerText(), /Armor_Child_Unmatched/);
    await page.evaluate(() => HookyI18n.setLanguage("fr"));
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_Tunic.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Armor_Child_Tunic"));

    // Player context is destroyed when leaving. Configure an economy profile,
    // which is persisted and applied to a new viewport on return.
    await page.locator('[data-view="settings"]').click();
    assert.equal(await page.evaluate(() => window.__playerViewports.at(-1).disposed), true);
    await page.locator("#set-viewer-upscale").selectOption("fsr");
    await page.locator("#set-fsr-quality").selectOption("performance");
    await page.locator("#set-viewer-msaa").selectOption("off");
    await page.locator("#set-viewer-dpr").selectOption("1");
    await page.locator("#set-viewer-fps").selectOption("15");
    await page.locator("#set-viewer-limit").selectOption("800");
    await page.locator("#set-fsr-sharp").evaluate((input) => {
      input.value = "0.3"; input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(async () => {
      const s = (await (await fetch("/api/settings")).json()).settings;
      return s.viewer_upscale === "fsr" && s.fsr_quality === "performance" && s.viewer_fps === "15"
        && s.viewer_limit === "800" && s.fsr_sharpness === .3;
    });

    // Leave again while an outfit is still being restored. Re-entering must
    // retain *all* slot selections, not discard those not reloaded yet.
    let hold = true, release, enteredResolve;
    const entered = new Promise((resolve) => { enteredResolve = resolve; });
    const waiting = new Promise((resolve) => { release = resolve; });
    const routePattern = "**/api/model/geometry?*";
    const routeHandler = async (route) => {
      const rel = new URL(route.request().url()).searchParams.get("path");
      if (hold && rel === "Model/Armor_Child_Tunic.bfres") {
        hold = false; enteredResolve(); await waiting;
      }
      await route.continue().catch(() => {});
    };
    await page.route(routePattern, routeHandler);
    await page.locator('[data-view="player"]').click();
    await Promise.race([entered, new Promise((_, reject) => setTimeout(() => reject(new Error("outfit restore not requested")), 12000))]);
    await page.locator('[data-view="settings"]').click();
    release(); await page.unroute(routePattern, routeHandler);
    await page.locator('[data-view="player"]').click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Attaché à Child")
      && document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    await page.waitForFunction(() => document.querySelector("#player-view-status")?.textContent.includes("50 %"));
    results = await page.locator("#player-equipped").innerText();
    assert.match(results, /Armor_Child_Tunic/); assert.match(results, /Weapon_Sword_Test/);
    assert.deepEqual(await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), s = v.renderSettings;
      return { mode: v.mode, quality: s.fsr_quality, msaa: s.viewer_msaa, dpr: s.viewer_dpr,
        fps: s.viewer_fps, limit: s.viewer_limit, sharpness: s.fsr_sharpness, layers: v.layers.size };
    }), { mode: "fsr", quality: "performance", msaa: "off", dpr: 1, fps: 15, limit: 800, sharpness: .3, layers: 2 });
    assert.ok(await page.evaluate(() => window.__playerViewports.slice(0, -1).every((v) => v.disposed)));

    // A rescan reloads GPU resources rather than keeping stale files. A missing
    // outfit is diagnosed and can be restored if the source reappears.
    const outfitPath = path.join(info.root, "Model", "Armor_Child_Tunic.bfres");
    const temporarilyRemoved = path.join(temp, "saved-outfit.bfres");
    fs.renameSync(outfitPath, temporarilyRemoved);
    await page.evaluate(() => rescanProject());
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Modèle absent du projet")
      && window.__playerViewports.at(-1).layers.has("right"));
    assert.equal(await page.evaluate(() => window.__playerViewports.at(-1).layers.has("body")), false);
    await page.evaluate(() => HookyI18n.setLanguage("en"));
    assert.match(await page.locator("#player-equipped").innerText(), /Model missing from project/);
    await page.evaluate(() => HookyI18n.setLanguage("fr"));
    fs.renameSync(temporarilyRemoved, outfitPath);
    await page.evaluate(() => rescanProject());
    await page.waitForFunction(() => window.__playerViewports.at(-1).layers.has("body")
      && window.__playerViewports.at(-1).layers.has("right")
      && document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.match(await page.locator("#player-equipped").innerText(), /Armor_Child_Tunic/);

    // The base can also disappear temporarily. The UI must explain the empty
    // view and keep the selected equipment until the base is scanned again.
    const basePath = path.join(info.root, "Model", "Player_Child.bfres");
    const savedBase = path.join(temp, "saved-base.bfres");
    fs.renameSync(basePath, savedBase);
    await page.evaluate(() => rescanProject());
    await page.waitForFunction(() => document.querySelector("#player-view-status")?.textContent.includes("n'est plus présent"));
    assert.equal(await page.locator("#player-placeholder strong").innerText(), "Aperçu indisponible");
    results = await page.locator("#player-equipped").innerText();
    assert.match(results, /Armor_Child_Tunic/); assert.match(results, /Weapon_Sword_Test/);
    fs.renameSync(savedBase, basePath);
    await page.evaluate(() => rescanProject());
    await page.waitForFunction(() => window.__playerViewports.at(-1).layers.has("body")
      && window.__playerViewports.at(-1).layers.has("right")
      && document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Child.bfres");

    // Manual, unclassified models stay accessible, and changing the character
    // deliberately clears the previous equipment rather than misapplying it.
    await page.locator("#player-family").selectOption("all");
    await page.locator("#player-base").selectOption("Model/Creature_Unknown.bfres");
    await page.waitForFunction(() => document.querySelector("#player-view-status")?.textContent.includes("triangles"));
    assert.match(await page.locator("#player-equipped").innerText(), /Aucun équipement/);
    await page.locator("#player-base").selectOption("Model/Player_Adult.bfres");
    await page.waitForFunction(() => document.querySelector("#player-bone")?.options.length > 1);
    await page.locator("#player-animation-file").selectOption("Model/Player_Adult_Animation.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    await page.locator("#player-play").click();
    await page.waitForFunction(() => document.querySelector("#player-play")?.textContent === "Lire");
    await page.locator("#player-frame").evaluate((input) => {
      input.value = "8"; input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForFunction(() => Math.abs(window.__playerViewports.at(-1).animation.frame - 8) < .01);

    // A slow earlier model cannot replace a newer character selection.
    let releaseBase, enteredBase;
    const baseEntered = new Promise((resolve) => { enteredBase = resolve; });
    const baseWait = new Promise((resolve) => { releaseBase = resolve; });
    const baseHandler = async (route) => {
      if (new URL(route.request().url()).searchParams.get("path") === "Model/Player_Child.bfres") {
        enteredBase(); await baseWait;
      }
      await route.continue().catch(() => {});
    };
    await page.route(routePattern, baseHandler);
    await page.locator("#player-base").selectOption("Model/Player_Child.bfres");
    await Promise.race([baseEntered, new Promise((_, reject) => setTimeout(() => reject(new Error("base not requested")), 12000))]);
    await page.locator("#player-base").selectOption("Model/Player_Adult.bfres");
    releaseBase(); await page.unroute(routePattern, baseHandler);
    await page.waitForFunction(() => document.querySelector("#player-bone")?.options.length > 1
      && window.__playerViewports.at(-1).ready);
    assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Adult.bfres");
    assert.ok(await page.evaluate(() => window.__playerViewports.slice(0, -1).every((v) => v.disposed)));

    // Likewise an old clip response must not restart an animation after the
    // user has already selected the static pose.
    let releaseClip, enteredClip;
    const clipEntered = new Promise((resolve) => { enteredClip = resolve; });
    const clipWait = new Promise((resolve) => { releaseClip = resolve; });
    const clipPattern = "**/api/animation/clip?*";
    const clipHandler = async (route) => { enteredClip(); await clipWait; await route.continue().catch(() => {}); };
    await page.route(clipPattern, clipHandler);
    await page.locator("#player-animation-file").selectOption("Model/Player_Adult_Animation.anim.bfres");
    await Promise.race([clipEntered, new Promise((_, reject) => setTimeout(() => reject(new Error("clip not requested")), 12000))]);
    await page.locator("#player-animation-file").selectOption("");
    releaseClip(); await page.unroute(clipPattern, clipHandler);
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent === "Pose statique.");
    assert.equal(await page.evaluate(() => window.__playerViewports.at(-1).animation), null);

    // New UI strings switch languages, but user-supplied paths never do.
    const animationHeading = page.locator(".player-block-title h3");
    for (const [lang, expected, followLabel, followHint] of [
      ["en", "Test an animation", "Follow root · camera", "camera follows a single root"],
      ["ru", "Проверить анимацию", "Следовать за корнем · камера", "камера следует за единственной"],
      ["zh", "测试动画", "跟随根骨骼 · 相机", "相机跟随唯一根骨骼"],
      ["fr", "Tester une animation", "Suivre la racine · caméra", "caméra suit la racine unique"],
    ]) {
      await page.evaluate((value) => HookyI18n.setLanguage(value), lang);
      await page.waitForFunction((value) => document.querySelector(".player-block-title h3")?.textContent === value, expected);
      assert.equal(await animationHeading.innerText(), expected);
      assert.equal(await page.locator("#player-follow-root").locator("..").innerText(), followLabel);
      assert.ok((await page.locator(".player-stage-actions + .hint").innerText()).includes(followHint));
      assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Adult.bfres");
    }

    // Opening Player from an animation or from its model inspector must carry
    // the chosen model, file and clip across without keeping two GPU contexts.
    const animationTile = '#animation-grid [data-rel="Model/Player_Child_Animation.anim.bfres"]';
    await page.locator('[data-view="animations"]').click();
    await page.locator(animationTile).click();
    await page.locator("#animation-model").selectOption("Model/Player_Child.bfres");
    await page.locator("#animation-open-player").click();
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Child.bfres");
    assert.equal(await page.locator("#player-animation-file").inputValue(), "Model/Player_Child_Animation.anim.bfres");
    assert.equal(await page.locator("#player-animation-clip").inputValue(), "0");

    await page.locator('[data-view="animations"]').click();
    await page.locator(animationTile).click();
    await page.locator("#animation-model").selectOption("Model/Player_Child.bfres");
    await page.locator("#animation-open-model").click();
    await page.waitForFunction(() => document.querySelector("#model-animation-status")?.textContent.includes("os associés"));
    await page.locator("#btn-compose-player").click();
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.equal(await page.locator("#player-animation-clip").inputValue(), "0");
    assert.ok(await page.evaluate(() => window.__playerViewports.slice(0, -1).every((v) => v.disposed)));

    // A narrow viewport remains usable; no game asset or source was edited.
    await page.setViewportSize({ width: 520, height: 900 });
    const overflow = await page.locator("#view-player").evaluate((node) => node.scrollWidth > node.clientWidth + 1);
    assert.equal(overflow, false, "Player must not overflow a narrow viewport");

    // Losing the context must disable stale playback controls and offer a way
    // to retry without discarding the selected base, outfit, or animation.
    await page.locator("#player-slot").selectOption("body");
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_Tunic.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => window.__playerViewports.at(-1).layers.has("body"));
    await page.evaluate(() => window.__playerViewports.at(-1).gl.getExtension("WEBGL_lose_context").loseContext());
    await page.waitForFunction(() => document.querySelector("#player-view-status")?.textContent === "Contexte WebGL perdu.");
    assert.equal(await page.locator("#player-placeholder strong").innerText(), "Aperçu indisponible");
    assert.equal(await page.evaluate(() => window.__playerViewports.at(-1).disposed), true);
    for (const id of ["player-animation-file", "player-animation-clip", "player-frame", "player-play", "player-equip"])
      assert.equal(await page.locator(`#${id}`).isDisabled(), true, `${id} must be disabled after context loss`);
    assert.match(await page.locator("#player-equipped").innerText(), /Armor_Child_Tunic/);
    await page.locator("#player-retry").click();
    await page.waitForFunction(() => window.__playerViewports.at(-1).layers.has("body")
      && document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    assert.equal(await page.locator("#player-base").inputValue(), "Model/Player_Child.bfres");
    assert.equal(await page.locator("#player-animation-file").inputValue(), "Model/Player_Child_Animation.anim.bfres");
    assert.equal(await page.locator("#player-play").isDisabled(), false);

    // Player-like synthetic variant: scaled mixed-mode skeleton, quaternion
    // animation, independent outfit skin and a bone-mounted object.
    await page.setViewportSize({ width: 1400, height: 960 });
    await page.locator("#player-family").selectOption("all");
    await page.locator("#player-base").selectOption("Model/Player_Child_Scaled.bfres");
    await page.waitForFunction(() => document.querySelector("#player-bone")?.options.length === 3);
    await page.locator("#player-animation-file").selectOption("Model/Player_Child_Scaled_Animation.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("2 os associés"));
    await page.locator("#player-slot").selectOption("body");
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_Scaled.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("2 os associés"));
    const scaledPixels = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), gl = v.gl;
      v.playAnimation(false); v.setBaseVisible(false); v.yaw = 0; v.pitch = 0;
      const capture = (frame) => {
        v.seekAnimation(frame); v.draw();
        const pixels = new Uint8Array(v.canvas.width * v.canvas.height * 4);
        gl.readPixels(0, 0, v.canvas.width, v.canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        return pixels;
      };
      const first = capture(0), last = capture(8), rest = HookyAnimation.pose(v.animation.binding, 0);
      let visibleFirst = 0, visibleLast = 0, different = 0;
      for (let i = 0; i < first.length; i += 4) {
        if (first[i] > 50 || first[i+1] > 50 || first[i+2] > 50) visibleFirst += 1;
        if (last[i] > 50 || last[i+1] > 50 || last[i+2] > 50) visibleLast += 1;
        if (first[i] !== last[i] || first[i+1] !== last[i+1] || first[i+2] !== last[i+2]) different += 1;
      }
      v.setBaseVisible(true);
      return { visibleFirst, visibleLast, different, error: gl.getError(),
        firstMatrix: [...rest.palette.slice(0, 12)], secondMatrix: [...rest.palette.slice(24, 36)],
        independent: v.layers.get("body").skin !== v.skin };
    });
    assert.equal(scaledPixels.error, 0); assert.equal(scaledPixels.independent, true);
    assert.ok(scaledPixels.visibleFirst > 100 && scaledPixels.visibleLast > 100 && scaledPixels.different > 100,
      `scaled mixed-mode outfit must be visible and move: ${JSON.stringify(scaledPixels)}`);
    const identity = [1,0,0,0, 0,1,0,0, 0,0,1,0];
    for (const matrix of [scaledPixels.firstMatrix, scaledPixels.secondMatrix])
      matrix.forEach((value, i) => assert.ok(Math.abs(value - identity[i]) < 1e-5, JSON.stringify(matrix)));
    await page.locator("#player-slot").selectOption("right");
    await page.locator("#player-equipment").selectOption("Model/Weapon_Sword_Test.bfres");
    await page.locator("#player-bone").selectOption("Child");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Attaché à Child"));
    const mounted = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), gl = v.gl;
      v.seekAnimation(5); v.draw();
      return { actual: [...gl.getUniform(v.scene, gl.getUniformLocation(v.scene, "u_attachment"))],
        world: HookyAnimation.pose(v.animation.binding, 5).worlds[1], error: gl.getError() };
    });
    assert.equal(mounted.error, 0);
    const w = mounted.world, expectedAttachment = [w[0],w[4],w[8],0, w[1],w[5],w[9],0,
      w[2],w[6],w[10],0, w[3],w[7],w[11],1];
    mounted.actual.forEach((value, i) => assert.ok(Math.abs(value - expectedAttachment[i]) < 1e-5));

    // A zero scale reached later in playback is reported rather than silently
    // substituting an identity matrix; the outfit and WebGL view survive.
    await page.locator("#player-animation-file").selectOption("Model/Singular.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("os associés"));
    const singular = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), mode = v.mode;
      v.playAnimation(false); v.seekAnimation(10); v.draw();
      return { stopped: v.animation === null, ready: v.ready && !v.disposed,
        modeUnchanged: v.mode === mode, outfit: v.layers.has("body") };
    });
    assert.deepEqual(singular, { stopped: true, ready: true, modeUnchanged: true, outfit: true });
    assert.match(await page.locator("#player-animation-status").innerText(), /Animation interrompue, pose de repos : .*singulière/);
    assert.equal(await page.locator("#player-play").isDisabled(), true);

    // The base remains valid here; only the outfit's extra bone becomes
    // singular. The composition must still stop and return to its rest pose.
    await page.locator("#player-slot").selectOption("body");
    await page.locator("#player-equipment").selectOption("Model/Armor_Child_OnlySingular.bfres");
    await page.locator("#player-equip").click();
    await page.waitForFunction(() => document.querySelector("#player-equipped")?.textContent.includes("Armor_Child_OnlySingular"));
    await page.locator("#player-animation-file").selectOption("Model/OutfitSingular.anim.bfres");
    await page.waitForFunction(() => document.querySelector("#player-animation-status")?.textContent.includes("1 os associés"));
    const outfitSingular = await page.evaluate(() => {
      const v = window.__playerViewports.at(-1), outfit = v.layers.get("body"), mode = v.mode;
      v.playAnimation(false); v.seekAnimation(10);
      const baseValid = HookyAnimation.pose(v.animation.binding, 10).palette.every(Number.isFinite);
      let outfitFails = false;
      try { HookyAnimation.pose(outfit.binding, 10); } catch (error) { outfitFails = error instanceof HookyAnimation.PoseError; }
      v.draw();
      return { baseValid, outfitFails, stopped: v.animation === null, ready: v.ready && !v.disposed,
        modeUnchanged: v.mode === mode, outfit: v.layers.get("body") === outfit };
    });
    assert.deepEqual(outfitSingular, { baseValid: true, outfitFails: true, stopped: true, ready: true,
      modeUnchanged: true, outfit: true });
    assert.match(await page.locator("#player-animation-status").innerText(), /Animation interrompue, pose de repos : .*singulière/);
    assert.equal(await page.locator("#player-play").isDisabled(), true);
    assert.deepEqual(errors, []);
    for (const [name, bytes] of originals) assert.deepEqual(fs.readFileSync(path.join(info.root, "Model", name)), bytes);
    console.log("Player + animation associations browser smoke: OK");
  } catch (error) {
    failed = true; throw error;
  } finally {
    if (browser) await browser.close();
    child.stdin.end();
    const result = await Promise.race([exited, new Promise((resolve) => setTimeout(() => resolve(null), 10000))]);
    if (!result) child.kill("SIGKILL");
    fs.rmSync(temp, { recursive: true, force: true });
    if (stderr && (failed || result?.code)) console.error(stderr);
    if (!failed && (!result || result.code !== 0)) throw new Error(`Synthetic server did not exit cleanly: ${JSON.stringify(result)}`);
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
