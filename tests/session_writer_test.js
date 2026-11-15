"use strict";

const assert = require("node:assert/strict");
const { UiSessionWriter } = require("../hookycreator/web/session.js");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const response = (payload = { saved: true }, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => payload,
});

async function orderedAndDebounced() {
  let state = { version: 1, view: "home" };
  const calls = [];
  const writer = new UiSessionWriter(() => state, { client: "client_123", fetcher: (url, options) =>
    new Promise((resolve, reject) => calls.push({ url, options, resolve, reject })) });
  writer.schedule(1);
  await delay(10);
  assert.equal(calls.length, 0, "pas d'écriture avant la lecture initiale");
  writer.enable();
  const first = writer.flush();
  state = { version: 1, view: "models" };
  const latest = writer.flush();
  assert.equal(calls.length, 2);
  assert.equal(JSON.parse(calls[0].options.body).sequence, 1);
  assert.equal(JSON.parse(calls[1].options.body).sequence, 2);
  calls[1].resolve(response());
  assert.equal(await latest, true);
  calls[0].resolve(response({ saved: false }));
  assert.equal(await first, true);
  assert.equal(writer.latestSuccess, 2);
  assert.equal(writer.lastSaved, JSON.stringify(state), "une ancienne réponse ne rembobine pas l'état");
  assert.equal(await writer.flush(), true);
  assert.equal(calls.length, 2, "pas de réécriture identique");

  state = { version: 1, view: "sounds" };
  writer.schedule(40);
  writer.schedule(10);
  await delay(30);
  assert.equal(calls.length, 3, "les changements rapprochés sont regroupés");
  assert.equal(JSON.parse(calls[2].options.body).state.view, "sounds");
  calls[2].resolve(response());
  await delay(5);
  assert.equal(writer.lastSaved, JSON.stringify(state));
}

async function failuresRecoveryAndKeepaliveLimit() {
  let state = { version: 1, view: "home" };
  const calls = [];
  const errors = [];
  const writer = new UiSessionWriter(() => state, { client: "client_123",
    onError: (error) => errors.push(error.message),
    fetcher: async (_url, options) => {
      calls.push(options);
      if (calls.length <= 2) throw new Error("disque indisponible");
      return response();
    },
  });
  writer.enable();
  assert.equal(await writer.flush(), false);
  assert.equal(await writer.flush(), false);
  assert.deepEqual(errors, ["disque indisponible"], "ne pas répéter la même notification");
  assert.equal(await writer.flush({ keepalive: true }), true);
  assert.equal(calls[2].keepalive, true);
  assert.equal(writer.reportedError, false);
  // La limite est en octets UTF-8, pas en caractères JS.
  state = { version: 1, fields: { search: "é".repeat(32000) } };
  assert.equal(await writer.flush({ keepalive: true }), true);
  assert.equal(calls[3].keepalive, false, "une grande session n'est pas refusée par keepalive");
  state = { version: 1, view: "settings" };
  assert.equal(await writer.flush(), true);
  assert.equal(calls[4].keepalive, false, "la fermeture native attend une requête normale");
}

async function returningToPriorStateWhileWriteInFlight() {
  let state = { version: 1, view: "home" };
  const calls = [];
  const writer = new UiSessionWriter(() => state, { client: "client_123", fetcher: (_url, options) =>
    new Promise((resolve) => calls.push({ options, resolve })) });
  writer.enable();
  const initial = writer.flush(); calls[0].resolve(response()); await initial;
  state = { version: 1, view: "models" };
  const pending = writer.flush();
  state = { version: 1, view: "home" };
  const restore = writer.flush();
  assert.equal(calls.length, 3, "le retour à la valeur précédente doit être envoyé");
  assert.equal(JSON.parse(calls[2].options.body).sequence, 3);
  calls[2].resolve(response()); await restore;
  calls[1].resolve(response({ saved: false })); await pending;
  assert.equal(writer.lastSaved, JSON.stringify(state));
}

(async () => {
  await orderedAndDebounced();
  await failuresRecoveryAndKeepaliveLimit();
  await returningToPriorStateWhileWriteInFlight();
  console.log("UI session writer: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
