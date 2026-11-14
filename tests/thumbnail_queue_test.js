"use strict";

// Tests sans navigateur ni dépendance npm ; la CI les lance avec Node.
const assert = require("node:assert/strict");
const { ThumbnailQueue } = require("../hookycreator/web/thumbnails.js");

function classes() {
  const values = new Set();
  return { add: (name) => values.add(name), remove: (name) => values.delete(name),
           contains: (name) => values.has(name) };
}
function image(url) {
  const box = { classList: classes() };
  return { dataset: { thumb: url }, classList: classes(), closest: () => box, box };
}
function root(images) {
  return { querySelectorAll: () => images, contains: (item) => images.includes(item) };
}
function harness(options = {}) {
  const requests = [];
  const revoked = [];
  let callback;
  const observer = { observe() {}, unobserve() {} };
  const queue = new ThumbnailQueue({
    observerFactory: (cb) => { callback = cb; return observer; },
    createURL: (blob) => `blob:${blob.id}`,
    revokeURL: (url) => revoked.push(url),
    retryMinMs: 1,
    ...options,
    fetcher: (url, { signal }) => new Promise((resolve, reject) => {
      const request = { url, resolve, reject, signal };
      requests.push(request);
      signal.addEventListener("abort", () => {
        const error = new Error("annulé");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
    }),
  });
  return { queue, requests, revoked,
           visible: (entries) => callback(entries.map(([target, isIntersecting]) => ({target, isIntersecting}))) };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const ok = (id) => ({ ok: true, status: 200, blob: async () => ({ id }) });

async function visibleOnlyAndSerial() {
  const h = harness();
  const a = image("a"), b = image("b"), c = image("c");
  const panel = root([a, b, c]);
  h.queue.observe(panel);
  assert.equal(h.requests.length, 0, "une image hors écran ne part pas");
  h.visible([[a, true], [b, true], [c, false]]);
  assert.equal(h.requests.length, 1, "un seul téléchargement simultané");
  h.requests[0].resolve(ok("a"));
  await tick();
  assert.equal(a.src, "blob:a");
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].url, "b");
  h.requests[1].resolve(ok("b"));
  await tick();
  assert.equal(h.requests.length, 2, "l'image invisible ne se lance pas");
  h.queue.release(panel);
  assert.deepEqual(h.revoked.sort(), ["blob:a", "blob:b"]);
  assert.equal(h.queue.items.size, 0);
}

async function interactivePauseAndDetach() {
  const h = harness();
  const a = image("a"), b = image("b");
  const panel = root([a, b]);
  h.queue.observe(panel);
  h.visible([[a, true], [b, true]]);
  const release = h.queue.hold();
  assert.equal(h.requests[0].signal.aborted, true);
  await tick();
  assert.equal(h.requests.length, 1, "une action suspend les nouvelles vignettes");
  release(); release(); // une libération répétée ne change pas le compteur
  assert.equal(h.queue.holds, 0);
  assert.equal(h.requests.length, 2);
  h.queue.setPaused(true);
  assert.equal(h.requests[1].signal.aborted, true);
  await tick();
  assert.equal(h.requests.length, 2);
  h.queue.setPaused(false);
  assert.equal(h.requests.length, 3);
  h.queue.release(panel);
  await tick();
  assert.equal(h.queue.items.size, 0);
  assert.equal(h.requests.length, 3, "quitter la vue annule sans relancer");
}

async function errorsDoNotBlockNextImage() {
  const h = harness();
  const a = image("a"), b = image("b");
  h.queue.observe(root([a, b]));
  h.visible([[a, true], [b, true]]);
  h.requests[0].resolve({ ok: false, status: 400 });
  await tick();
  assert.equal(a.dataset.broken, "1");
  assert.equal(a.box.classList.contains("broken"), true);
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(ok("b"));
  await tick();
  assert.equal(b.src, "blob:b");
}

async function busyServerRetriesWithoutFalseError() {
  const h = harness();
  const a = image("a");
  h.queue.observe(root([a]));
  h.visible([[a, true]]);
  h.requests[0].resolve({ ok: false, status: 429, headers: { get: () => "0.01" } });
  await tick();
  assert.equal(a.dataset.broken, undefined);
  assert.equal(h.requests.length, 1);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(ok("a"));
  await tick();
  assert.equal(a.src, "blob:a");
}

async function obsoleteRetryDoesNotDelayAnotherView() {
  const h = harness();
  const a = image("a"), b = image("b");
  const old = root([a]);
  h.queue.observe(old);
  h.visible([[a, true]]);
  h.requests[0].resolve({ ok: false, status: 429, headers: { get: () => "5" } });
  await tick();
  h.queue.release(old);
  h.queue.observe(root([b]));
  h.visible([[b, true]]);
  assert.equal(h.requests.length, 2, "le délai d'une ancienne vue ne bloque pas la nouvelle");
  assert.equal(h.requests[1].url, "b");
  h.requests[1].resolve(ok("b"));
  await tick();
  assert.equal(b.src, "blob:b");
}

async function scrollingAwayCancelsAnObsoleteRetry() {
  const h = harness();
  const a = image("a"), b = image("b");
  h.queue.observe(root([a, b]));
  h.visible([[a, true], [b, true]]);
  h.requests[0].resolve({ ok: false, status: 429, headers: { get: () => "5" } });
  await tick();
  h.visible([[a, false]]);
  assert.equal(h.requests.length, 2, "une image sortie de l'écran ne bloque pas les autres");
  assert.equal(h.requests[1].url, "b");
  h.requests[1].resolve(ok("b"));
  await tick();
}

async function scrollingAwayAbortsOnlyTheObsoleteImage() {
  const h = harness();
  const a = image("a"), b = image("b");
  h.queue.observe(root([a, b]));
  h.visible([[a, true], [b, true]]);
  h.visible([[a, false]]);
  await tick();
  assert.equal(h.requests[0].signal.aborted, true);
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].url, "b");
  h.requests[1].resolve(ok("b"));
  await tick();
  assert.equal(a.dataset.broken, undefined);
  assert.equal(b.src, "blob:b");
}

(async () => {
  await visibleOnlyAndSerial();
  await interactivePauseAndDetach();
  await errorsDoNotBlockNextImage();
  await busyServerRetriesWithoutFalseError();
  await obsoleteRetryDoesNotDelayAnotherView();
  await scrollingAwayCancelsAnObsoleteRetry();
  await scrollingAwayAbortsOnlyTheObsoleteImage();
  console.log("ThumbnailQueue: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
