"use strict";

/* End-to-end selection/export smoke, using only synthetic BFRES fixtures.
   Install tools/webgl dependencies; HOOKY_PLAYWRIGHT / HOOKY_CHROMIUM may
   point to isolated test dependencies. HOOKY_DESKTOP_BACKEND optionally selects
   a built, authenticated sidecar. No game resources are used. */
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
from tests.synth_animation import named_rigid_model, fska_fixture
from hookycreator.server import HookyCreatorServer
root = Path(sys.argv[1]) / 'source'; root.mkdir()
(root / 'Rigid.bfres').write_bytes(named_rigid_model())
(root / 'Walk.anim.bfres').write_bytes(fska_fixture())
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
                          'token': ready['token']}), flush=True)
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
    print(json.dumps({'origin': 'http://127.0.0.1:' + str(server.port)}), flush=True)
    try: sys.stdin.read()
    finally: server.stop()
`;

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "hooky-animation-smoke-"));
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
      lines.once("line", (line) => { clearTimeout(timer); lines.close();
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      exited.then((result) => { clearTimeout(timer); reject(new Error(`Server stopped: ${JSON.stringify(result)}`)); });
    });
    browser = await chromium.launch({ headless: true,
      ...(process.env.HOOKY_CHROMIUM ? { executablePath: process.env.HOOKY_CHROMIUM } : {}),
      args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await browser.newPage({ viewport: { width: 1400, height: 960 }, acceptDownloads: true });
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    const bodies = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/model/export")) bodies.push(request.postDataJSON());
    });
    await page.goto(info.origin + (info.token ? `/desktop-login#token=${encodeURIComponent(info.token)}` : "/"));
    if (info.token) await page.waitForURL(info.origin + "/");
    await page.waitForFunction(() => document.querySelector("#home-sub")?.textContent.includes("zstd"));

    // Open an animation that is not matched by filename through the animation
    // view. It must remain the selected clip when opening the model.
    await page.locator('[data-view="animations"]').click();
    await page.waitForSelector('#animation-grid .tile:not([hidden])');
    await page.locator('#animation-grid .tile:not([hidden])').first().click();
    await page.waitForSelector('#animation-open-model:not([disabled])');
    await page.locator('#animation-model').selectOption('Rigid.bfres');
    await page.locator('#animation-open-model').click();
    await page.waitForSelector('#btn-export-model');
    await page.waitForFunction(() => document.querySelector('#model-animation-file')?.value === 'Walk.anim.bfres'
      && document.querySelector('#model-animation-clip')?.value === '0');
    assert.equal(await page.locator('#model-animation-file').inputValue(), 'Walk.anim.bfres');

    // A pending list must not silently produce a static export. Delay and hold
    // the response while clicking export, then verify that no POST was made.
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let entered;
    const intercepted = new Promise((resolve) => { entered = resolve; });
    await page.route('**/api/animation/list?**', async (route) => {
      entered(); await gate; await route.continue();
    });
    await page.locator('#model-animation-file').selectOption('');
    await page.locator('#model-animation-file').selectOption('Walk.anim.bfres');
    await intercepted;
    assert.equal(await page.locator('#model-animation-clip').inputValue(), '');
    await page.locator('#btn-export-model').click();
    assert.equal(bodies.length, 0);
    release();
    await page.waitForFunction(() => document.querySelector('#model-animation-clip')?.value === '0');
    await page.unroute('**/api/animation/list?**');
    const animatedDownload = page.waitForEvent('download');
    const animatedResponse = page.waitForResponse((response) => response.url().includes('/api/model/export'));
    await page.locator('#btn-export-model').click();
    const animated = await (await animatedResponse).json();
    await animatedDownload;
    assert.deepEqual(bodies[0].animation, { path: 'Walk.anim.bfres', index: 0 });
    assert.equal(animated.animation.name, 'Walk');
    assert.ok(animated.files.includes('Rigid.glb'));
    assert.ok(animated.files.includes('Rigid.mtl'));
    assert.match(await page.locator('#export-result-warnings').innerText(), /OBJ\/MTL restent statiques/);

    await page.locator('#model-animation-file').selectOption('');
    const staticDownload = page.waitForEvent('download');
    const staticResponse = page.waitForResponse((response) => response.url().includes('/api/model/export'));
    await page.locator('#btn-export-model').click();
    const plain = await (await staticResponse).json();
    await staticDownload;
    assert.equal(bodies[1].animation, undefined);
    assert.equal(plain.animation, null);
    assert.deepEqual(errors, []);
    console.log(`Animated export UI (${info.token ? 'desktop sidecar' : 'source backend'}): OK — delayed selection, clip, GLB/MTL and static fallback`);
  } catch (error) { failed = true; throw error; }
  finally {
    if (browser) await browser.close();
    child.stdin.end();
    const result = await Promise.race([exited, new Promise((resolve) => setTimeout(() => resolve(null), 10000))]);
    if (!result) child.kill('SIGKILL');
    fs.rmSync(temp, { recursive: true, force: true });
    if (stderr && (failed || result?.code)) console.error(stderr);
    if (!failed && (!result || result.code !== 0)) throw new Error(`Server did not exit cleanly: ${JSON.stringify(result)}`);
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
