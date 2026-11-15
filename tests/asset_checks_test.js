"use strict";

const assert = require("node:assert/strict");
const { AssetValidationQueue } = require("../hookycreator/web/asset_checks.js");

const tick = () => new Promise((resolve) => setImmediate(resolve));
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function requests() {
  const pending = [];
  const fetcher = (url, { signal }) => new Promise((resolve, reject) => {
    const query = new URL(url, "http://local").searchParams;
    const request = { rel: query.get("path"), kind: query.get("kind"), resolve, reject, signal };
    pending.push(request);
    signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  });
  return { pending, fetcher };
}
function response(payload, status = 200) {
  return { status, ok: status >= 200 && status < 300,
    headers: { get: () => null }, json: async () => payload };
}

async function visibilityAndCancellation() {
  const { pending, fetcher } = requests();
  const changes = [];
  const queue = new AssetValidationQueue({ fetcher, onChange: (kind, rel, entry) =>
    changes.push([kind, rel, entry.status]) });
  const assets = [{ kind: "model", rel: "ok.bfres" },
                  { kind: "model", rel: "bad.bfres" },
                  { kind: "texture", rel: "map.bntx" }];
  queue.setScope("view", assets, "revision-1");
  assert.equal(pending.length, 1, "un seul décodage en cours");
  assert.equal(queue.valid("model", "ok.bfres"), false, "caché avant réponse");
  pending[0].resolve(response({ valid: true, injectable: true }));
  await tick();
  assert.equal(queue.valid("model", "ok.bfres"), true);
  assert.equal(queue.entry("model", "ok.bfres").injectable, true);
  pending[1].resolve(response({ valid: false, reason: "illisible" }));
  await tick();
  assert.equal(queue.entry("model", "bad.bfres").status, "invalid");
  assert.equal(pending[2].rel, "map.bntx");
  queue.clearScope("view");
  assert.equal(pending[2].signal.aborted, true, "la navigation annule le travail inutile");
  await tick();
  assert.equal(queue.entry("texture", "map.bntx"), null);
  assert.deepEqual(changes, [["model", "ok.bfres", "valid"], ["model", "bad.bfres", "invalid"]]);
}

async function revisionCannotPublishOldResponses() {
  const { pending, fetcher } = requests();
  const queue = new AssetValidationQueue({ fetcher });
  const asset = [{ kind: "texture", rel: "same.bntx" }];
  queue.setScope("view", asset, "old");
  queue.setScope("view", asset, "new");
  assert.equal(pending[0].signal.aborted, true);
  // Même si le serveur répond après l'annulation, il ne doit pas rendre l'ancien visible.
  pending[0].resolve(response({ valid: true }));
  await tick();
  assert.equal(queue.valid("texture", "same.bntx"), false);
  assert.equal(pending.length, 2);
  pending[1].resolve(response({ valid: false, reason: "new version" }));
  await tick();
  assert.equal(queue.entry("texture", "same.bntx").status, "invalid");
}

async function holdRetryAndInspectorPriority() {
  const { pending, fetcher } = requests();
  const queue = new AssetValidationQueue({ fetcher });
  queue.setScope("view", [{ kind: "model", rel: "A" }, { kind: "model", rel: "B" }], "v");
  const release = queue.hold();
  await tick();
  assert.equal(pending[0].signal.aborted, true);
  assert.equal(pending.length, 1);
  queue.setScope("inspector", [{ kind: "texture", rel: "T" }], "v");
  release(); release(); // libération idempotente
  assert.equal(pending[1].rel, "T");
  pending[1].reject(new Error("réseau"));
  await tick();
  assert.equal(queue.entry("texture", "T").status, "error");
  assert.equal(pending[2].rel, "A");
  pending[2].resolve(response({ valid: true }));
  await tick();
  queue.retryErrors();
  // B a déjà commencé : on ne le remplace pas par une requête concurrente.
  pending[3].resolve(response({ valid: false }));
  await tick();
  assert.equal(pending[4].rel, "T");
  pending[4].resolve(response({ valid: true }));
  await tick();
  assert.equal(queue.valid("texture", "T"), true);
  queue.markError("texture", "T", "PNG cassé");
  assert.equal(queue.valid("texture", "T"), false);
  assert.equal(queue.entry("texture", "T").status, "error");
  queue.clearScope("inspector");
  queue.clearScope("view");
}

async function timeoutIsNotAValidAsset() {
  const { fetcher } = requests();
  const queue = new AssetValidationQueue({ fetcher, timeoutMs: 15 });
  queue.setScope("view", [{ kind: "model", rel: "slow" }], "v");
  await delay(35);
  assert.equal(queue.entry("model", "slow").status, "error");
  assert.equal(queue.valid("model", "slow"), false);
  queue.clearScope("view");
}

(async () => {
  await visibilityAndCancellation();
  await revisionCannotPublishOldResponses();
  await holdRetryAndInspectorPriority();
  await timeoutIsNotAValidAsset();
  console.log("Asset validation queue: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
