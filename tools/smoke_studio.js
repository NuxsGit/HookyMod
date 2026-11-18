"use strict";

/* End-to-end HookyMod's smoke test with an empty synthetic project. No game
   assets are used. Set HOOKY_DESKTOP_BACKEND to exercise the packaged sidecar,
   HOOKY_PLAYWRIGHT / HOOKY_CHROMIUM for the isolated browser dependencies. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require(process.env.HOOKY_PLAYWRIGHT || "playwright-core");
const ROOT = path.resolve(__dirname, "..");
const PYTHON_SERVER = String.raw`
import json, os, subprocess, sys
from pathlib import Path
from hookycreator.server import HookyCreatorServer
base = Path(sys.argv[1]); root = base / 'source'; root.mkdir()
(root / 'original.dat').write_bytes(b'synthetic original')
backend = os.environ.get('HOOKY_DESKTOP_BACKEND')
if backend:
    env = dict(os.environ); env.pop('PYTHONPATH', None); env.pop('LD_LIBRARY_PATH', None)
    sidecar = subprocess.Popen([str(Path(backend).resolve()), str(root)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=sys.stderr, env=env)
    try:
        line = sidecar.stdout.readline()
        if not line: raise RuntimeError('Sidecar stopped before reporting its session')
        ready = json.loads(line)
        if 'error' in ready: raise RuntimeError(ready['error'])
        print(json.dumps({'origin': 'http://127.0.0.1:' + str(ready['port']),
                          'token': ready['token'], 'root': str(root)}), flush=True)
        sys.stdin.read()
    finally:
        sidecar.stdin.close()
        try: sidecar.wait(timeout=8)
        except subprocess.TimeoutExpired: sidecar.kill(); sidecar.wait(timeout=5)
        sidecar.stdout.close()
        if sidecar.returncode and sys.exc_info()[0] is None:
            raise RuntimeError(f'Sidecar stopped with status {sidecar.returncode}')
else:
    server = HookyCreatorServer(root, host='127.0.0.1', port=0).start()
    print(json.dumps({'origin': 'http://127.0.0.1:' + str(server.port), 'root': str(root)}), flush=True)
    try: sys.stdin.read()
    finally: server.stop()
`;

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "hooky-studio-smoke-"));
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
    browser = await chromium.launch({ headless: true,
      ...(process.env.HOOKY_CHROMIUM ? { executablePath: process.env.HOOKY_CHROMIUM } : {}),
      args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await browser.newPage({ viewport: { width: 1400, height: 960 }, acceptDownloads: true });
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(info.origin + (info.token ? `/desktop-login#token=${encodeURIComponent(info.token)}` : "/"));
    if (info.token) await page.waitForURL(info.origin + "/");
    await page.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));
    await page.locator('[data-view="studio"]').click();
    await page.waitForSelector("#studio-empty:visible");
    assert.match(await page.locator("#studio-notice, .studio-notice").innerText(), /ne créent pas encore de contenu jouable/);
    await page.locator(".studio-compatibility summary").click();
    assert.match(await page.locator(".studio-compatibility").innerText(), /projets \.hooky de conception, non jouables/);
    assert.match(await page.locator(".studio-compatibility").innerText(), /fichiers de mod déjà préparés/);
    assert.match(await page.locator(".studio-compatibility").innerText(), /contenant romfs/);
    await page.locator(".studio-compatibility summary").click();

    await page.locator("#studio-new-name").fill("Fichier");
    await page.locator('#studio-create-form button[type="submit"]').click();
    await page.waitForSelector("#studio-workspace:visible");
    assert.equal(await page.locator("#studio-project-title").innerText(), "Fichier");
    await page.locator('[data-studio-kind="item"]').click();
    await page.locator("#studio-element-name").fill("Fichier");
    await page.locator("#studio-element-description").fill("Objet imaginé avec soin");
    const obj = Buffer.from("# Synthetic only\no Sample\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n");
    await page.locator("#studio-asset-file").setInputFiles({ name: "Fichier.obj", mimeType: "text/plain", buffer: obj });
    await page.waitForFunction(() => document.querySelector("#studio-asset-name").textContent === "Fichier.obj");

    // Click and actual drag-and-drop both add blocks; arrow controls reorder them.
    await page.locator('[data-studio-block="when_interact"]').click();
    await page.locator('[data-studio-block="show_message"]').click();
    await page.locator('[data-block-value="1"]').fill("Fichier");
    await page.locator('[data-studio-block="if_flag"]').dragTo(page.locator("#studio-canvas"),
      { targetPosition: { x: 40, y: 260 } });
    await page.waitForFunction(() => document.querySelectorAll(".studio-block").length === 3);
    await page.locator('[data-block-value="2"]').fill("porte_ouverte");
    await page.locator('[data-block-move="up"][data-index="2"]').click();
    assert.deepEqual(await page.locator(".studio-block-label").allInnerTexts(),
      ["Quand on interagit", "Si un état est actif", "Afficher un message"]);
    assert.equal(await page.locator('[data-block-value="2"]').inputValue(), "Fichier");
    await page.locator("#studio-check").click();
    await page.waitForSelector("#studio-report:visible");
    assert.match(await page.locator("#studio-report").innerText(), /aucun générateur ou lanceur/);

    // The simulator is isolated: event groups and conditions affect only a
    // temporary trace, and markup from project values remains inert text.
    await page.locator("#studio-simulate").click();
    await page.waitForSelector("#studio-simulation-result:visible");
    let trace = page.locator("#studio-simulation-result ol li");
    assert.match(await trace.nth(1).innerText(), /Condition non remplie/);
    assert.match(await trace.nth(2).innerText(), /Ignoré/);
    await page.locator("#studio-simulation-flags").fill("porte_ouverte");
    assert.equal(await page.locator("#studio-simulation-result").isHidden(), true);
    await page.locator("#studio-simulate").click();
    await page.waitForSelector("#studio-simulation-result:visible");
    assert.match(await trace.nth(1).innerText(), /Condition remplie/);
    assert.match(await trace.nth(2).innerText(), /Action simulée.*Fichier/);
    await page.evaluate(() => window.HookyI18n.setLanguage("en"));
    assert.match(await trace.nth(1).innerText(), /Condition met/);
    assert.match(await trace.nth(2).innerText(), /Simulated action.*Fichier/);
    await page.evaluate(() => window.HookyI18n.setLanguage("fr"));
    assert.match(await trace.nth(2).innerText(), /Action simulée.*Fichier/);
    const injected = '<img src=x onerror="window.__studioXss=1">';
    await page.locator('[data-block-value="2"]').fill(injected);
    await page.locator("#studio-simulate").click();
    await page.waitForSelector("#studio-simulation-result:visible");
    assert.equal(await page.locator("#studio-simulation-result img").count(), 0);
    assert.match(await trace.nth(2).innerText(), /<img src=x/);
    assert.equal(await page.evaluate(() => window.__studioXss), undefined);
    await page.locator('[data-block-value="2"]').fill("Fichier");
    assert.equal(await page.locator("#studio-simulation-result").isHidden(), true);
    assert.equal(await page.evaluate(() => window.HookyStudio.isDirty()), true);

    // Switching element retains drafts; editing treats markup as data.
    await page.locator('[data-studio-kind="scene"]').click();
    const malicious = '<svg onload="window.__studioXss=1">';
    await page.locator("#studio-element-name").fill(malicious);
    assert.equal(await page.evaluate(() => window.__studioXss), undefined);
    assert.equal(await page.locator("#studio-element-list svg").count(), 0);
    await page.locator(".studio-element-card").first().click();
    assert.equal(await page.locator("#studio-element-name").inputValue(), "Fichier");
    assert.equal(await page.locator('[data-block-value="2"]').inputValue(), "Fichier");
    assert.equal(await page.evaluate(() => window.HookyStudio.isDirty()), true);
    await page.locator("#studio-save").click();
    await page.waitForFunction(() => !window.HookyStudio.isDirty());
    const stored = await page.evaluate(async () => {
      const list = await (await fetch("/api/studio/workspaces")).json();
      return (await (await fetch(`/api/studio/workspace?id=${list.workspaces[0].id}`)).json()).workspace;
    });
    assert.equal(stored.revision, 1); assert.equal(stored.elements.length, 2);
    assert.equal(stored.elements[0].asset.name, "Fichier.obj");
    assert.deepEqual(Buffer.from(stored.elements[0].asset.data, "base64"), obj);
    assert.equal(stored.elements[1].name, malicious);

    // Reload and re-open a persisted workspace, then change language. User data
    // that happens to equal an existing translation key must never be translated.
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));
    await page.locator('[data-view="settings"]').click();
    await page.locator("#set-language").selectOption("en");
    assert.equal(await page.evaluate(() => finishPendingSettings()), true);
    await page.locator('[data-view="studio"]').click();
    await page.waitForSelector(".studio-project-card");
    await page.locator(".studio-project-card").first().click();
    await page.waitForSelector("#studio-editor:visible");
    assert.equal(await page.locator("#studio-project-title").innerText(), "Fichier");
    assert.equal(await page.locator(".studio-project-card strong").first().innerText(), "Fichier");
    assert.equal(await page.locator(".studio-element-card strong").first().innerText(), "Fichier");
    assert.equal(await page.locator("#studio-element-name").inputValue(), "Fichier");
    assert.equal(await page.locator('[data-block-value="2"]').inputValue(), "Fichier");
    assert.equal(await page.locator('[data-studio-block="show_message"]').innerText(), "Show a message");
    await page.locator(".studio-compatibility summary").click();
    assert.match(await page.locator(".studio-compatibility").innerText(), /non-playable \.hooky design projects/);
    assert.match(await page.locator(".studio-compatibility").innerText(), /already prepared mod files/);
    await page.locator(".studio-compatibility summary").click();
    await page.locator("#studio-simulation-flags").fill("porte_ouverte");
    await page.locator("#studio-simulate").click();
    await page.waitForSelector("#studio-simulation-result:visible");
    trace = page.locator("#studio-simulation-result ol li");
    assert.match(await trace.nth(1).innerText(), /Condition met/);
    assert.match(await trace.nth(2).innerText(), /Simulated action.*Fichier/);
    assert.equal(await page.evaluate(() => window.HookyStudio.isDirty()), false);

    const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#studio-export").click()]);
    assert.equal(await download.failure(), null);
    assert.match(download.suggestedFilename(), /\.hooky$/i);
    const exported = fs.readFileSync(await download.path());
    assert.equal(exported.subarray(0, 2).toString(), "PK");
    await page.locator("#studio-import-file").setInputFiles({ name: "project.hooky", mimeType: "application/zip", buffer: exported });
    await page.waitForFunction(() => document.querySelectorAll(".studio-project-card").length === 2);
    assert.equal(await page.locator("#studio-element-name").inputValue(), "Fichier");
    const ids = await page.locator(".studio-project-card").evaluateAll((nodes) => nodes.map((node) => node.dataset.studioProject));
    assert.notEqual(ids[0], ids[1]);
    const active = await page.locator('.studio-project-card[aria-current="true"]').getAttribute("data-studio-project");
    const other = ids.find((id) => id !== active);

    // Cancel and then accept the unsaved-change prompt on workspace switching.
    await page.locator("#studio-description").fill("Unsaved private draft");
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.locator(`[data-studio-project="${other}"]`).click();
    assert.equal(await page.locator("#studio-description").inputValue(), "Unsaved private draft");
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator(`[data-studio-project="${other}"]`).click();
    await page.waitForFunction((id) => document.querySelector('.studio-project-card[aria-current="true"]')?.dataset.studioProject === id, other);
    assert.notEqual(await page.locator("#studio-description").inputValue(), "Unsaved private draft");

    // Force a stale revision, then recover the unsaved draft using Duplicate.
    await page.locator("#studio-description").fill("Recover this draft");
    const status = await page.evaluate(async (id) => {
      const workspace = (await (await fetch(`/api/studio/workspace?id=${id}`)).json()).workspace;
      workspace.author = "Other window";
      const response = await fetch("/api/studio/workspace", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace }) });
      return response.status;
    }, other);
    assert.equal(status, 200);
    await page.locator("#studio-save").click();
    await page.waitForSelector(".toast.error");
    assert.equal(await page.evaluate(() => window.HookyStudio.isDirty()), true);
    await page.locator("#studio-copy").click();
    await page.waitForFunction(() => document.querySelectorAll(".studio-project-card").length === 3
      && !window.HookyStudio.isDirty());
    assert.equal(await page.locator("#studio-description").inputValue(), "Recover this draft");
    await page.locator("#studio-description").fill("Checkpoint saves the draft");
    assert.equal(await page.evaluate(() => saveUiCheckpoint()), true);
    assert.equal(await page.evaluate(() => window.HookyStudio.isDirty()), false);

    // Controls stay usable without horizontal overflow in narrow layouts.
    await page.setViewportSize({ width: 520, height: 900 });
    const overflow = await page.locator("#view-studio").evaluate((node) => node.scrollWidth > node.clientWidth + 1);
    assert.equal(overflow, false, "studio must not overflow the narrow viewport");
    if (process.env.HOOKY_STUDIO_SCREENSHOT) {
      await page.setViewportSize({ width: 1400, height: 960 });
      await page.screenshot({ path: process.env.HOOKY_STUDIO_SCREENSHOT, fullPage: true });
    }
    assert.deepEqual(errors, []);
    assert.equal(fs.readFileSync(path.join(info.root, "original.dat"), "utf8"), "synthetic original");
    console.log("HookyMod's browser workspace: OK");
  } catch (error) {
    failed = true;
    throw error;
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
