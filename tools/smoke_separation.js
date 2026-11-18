"use strict";

/* Browser smoke on synthetic data: an old maker session resumes safely, while
   model import/export and Player remain available. The same test can target a
   packaged desktop sidecar through HOOKY_DESKTOP_BACKEND. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { chromium } = require(process.env.HOOKY_PLAYWRIGHT || "playwright-core");
const ROOT = path.resolve(__dirname, "..");
const PYTHON_SERVER = String.raw`
import json, os, struct, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from desktop_fixture import make_desktop_fixture
from hookycreator.session import SessionStore
from hookycreator.server import HookyCreatorServer
base = Path(sys.argv[1]); root = make_desktop_fixture(base / 'romfs')
model = root / 'Model' / 'Heros.bfres'
data = bytearray(model.read_bytes()); struct.pack_into('<I', data, 8, 0x00090002); model.write_bytes(data)
work = root / 'hookycreator_work'
for filename, content in [('hookymods/legacy.json', b'design'), ('mobs/mobs.json', b'sheets')]:
    target = work / filename; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
mod = root / 'mods' / 'Existing' / 'keep.txt'; mod.parent.mkdir(parents=True); mod.write_bytes(b'old mod')
store = SessionStore(work, root); session = store._path(); session.parent.mkdir(parents=True, exist_ok=True)
old = {'version': 1, 'view': 'studio', 'active': 'mob:MyMob',
       'tabs': [{'type': 'view', 'view': 'home'}, {'type': 'view', 'view': 'studio'},
                {'type': 'view', 'view': 'models'}, {'type': 'mob', 'slug': 'MyMob'},
                {'type': 'model', 'rel': 'Model/Heros.bfres'}],
       'selection': ['model:Model/Heros.bfres'],
       'fields': {'search': 'Heros', 'mod-name': 'Old draft'},
       'choices': {'model-target': 'Model/Heros.bfres', 'mob-model': 'Model/Heros.bfres'},
       'scroll': 12, 'inspector': True}
session.write_text(json.dumps({'root': store.root, 'state': old}), encoding='utf-8')
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
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "hooky-separation-"));
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
    page.setDefaultTimeout(60000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(info.origin + (info.token ? `/desktop-login#token=${encodeURIComponent(info.token)}` : "/"));
    if (info.token) await page.waitForURL(info.origin + "/");
    await page.waitForFunction(() => uiSessionWriter?.enabled && state.project);
    assert.equal(await page.evaluate(() => state.view), "home");
    assert.equal(await page.evaluate(() => state.tab), "view:home");
    assert.equal(await page.locator("#search").inputValue(), "Heros");
    assert.deepEqual(await page.evaluate(() => [...state.selection]), ["model:Model/Heros.bfres"]);
    const tabs = await page.evaluate(() => state.tabs.map((tab) => tab.id));
    assert.ok(tabs.includes("view:models") && tabs.includes("model:Model/Heros.bfres"));
    assert.ok(!tabs.some((tab) => tab.startsWith("mob:") || tab === "view:studio"));
    assert.equal(await page.locator('[data-view="studio"], [data-view="create"], [data-view="mobs"]').count(), 0);

    // The old import target must remain pending until it is verified; do not
    // accidentally pick another target while the validator is still running.
    await page.locator('[data-view="import"]').click();
    await page.waitForFunction(() => document.querySelector("#model-target")?.value === "Model/Heros.bfres");
    const original = fs.readFileSync(path.join(info.root, "Model", "Heros.bfres"));
    const obj = Buffer.from("v 0 0 0\nv 2 0 0\nv 0 3 0\nf 1 2 3\n");
    await page.locator("#model-files").setInputFiles({ name: "triangle.obj", mimeType: "text/plain", buffer: obj });
    const inspect = page.waitForResponse((response) => response.url().endsWith("/api/model/inspect"));
    await page.locator("#model-inspect").click();
    assert.equal((await (await inspect).json()).can_import, true);
    await page.waitForFunction(() => !document.querySelector("#model-inject").disabled);
    const inject = page.waitForResponse((response) => response.url().endsWith("/api/model/import"));
    await page.locator("#model-inject").click();
    const result = await (await inject).json();
    assert.equal(result.faces, 1);
    assert.deepEqual(fs.readFileSync(path.join(info.root, "Model", "Heros.bfres")), original);
    assert.ok(fs.existsSync(path.join(info.root, "hookycreator_work", "assets", "Model", "Heros.bfres")));

    await page.keyboard.press("Control+4");
    await page.waitForSelector("#view-player:visible");
    assert.ok(await page.locator("#player-canvas, #player-placeholder").count() > 0);
    await page.locator('[data-view="models"]').click();
    await page.waitForSelector('#model-grid .tile[data-rel="Model/Heros.bfres"]:not([hidden])');
    await page.locator('#model-grid .tile[data-rel="Model/Heros.bfres"]').click();
    await page.waitForSelector("#btn-export-model");
    const download = page.waitForEvent("download");
    const exportResponse = page.waitForResponse((response) => response.url().endsWith("/api/model/export"));
    await page.locator("#btn-export-model").click();
    assert.ok((await (await exportResponse).json()).files.includes("Heros.glb"));
    const zip = await download;
    assert.equal(await zip.failure(), null);
    assert.equal(fs.readFileSync(await zip.path()).subarray(0, 2).toString(), "PK");
    assert.equal(fs.readFileSync(path.join(info.root, "hookycreator_work", "hookymods", "legacy.json"), "utf8"), "design");
    assert.equal(fs.readFileSync(path.join(info.root, "hookycreator_work", "mobs", "mobs.json"), "utf8"), "sheets");
    assert.equal(fs.readFileSync(path.join(info.root, "mods", "Existing", "keep.txt"), "utf8"), "old mod");
    assert.equal(await page.evaluate(() => saveUiCheckpoint()), true);
    const sessions = path.join(info.root, "hookycreator_work", "sessions");
    const backups = fs.readdirSync(sessions).filter((file) => file.includes(".pre-modmaker-") && file.endsWith(".json.bak"));
    assert.equal(backups.length, 1);
    assert.equal(JSON.parse(fs.readFileSync(path.join(sessions, backups[0]), "utf8")).state.fields["mod-name"], "Old draft");
    assert.deepEqual(errors, []);
    console.log(`Browser separation smoke (${info.token ? "authenticated sidecar" : "source backend"}): OK — legacy session, model import/export, Player and preserved files`);
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
    if (!failed && (!result || result.code !== 0)) throw new Error(`Server exit: ${JSON.stringify(result)}`);
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
