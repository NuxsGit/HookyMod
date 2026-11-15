"use strict";

/* Sauvegardes rapprochées regroupées, mais numérotées : même si un beacon de
   fermeture dépasse une ancienne requête, le serveur garde le plus récent. */
class UiSessionWriter {
  constructor(capture, options = {}) {
    this.capture = capture;
    this.fetcher = options.fetcher || globalThis.fetch.bind(globalThis);
    this.onError = options.onError || (() => {});
    this.client = options.client || UiSessionWriter.clientId();
    this.sequence = 0;
    this.latestSuccess = 0;
    this.lastSaved = "";
    this.inFlight = 0;
    this.timer = null;
    this.enabled = false;
    this.reportedError = false;
  }

  static clientId() {
    const bytes = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    return [...bytes].map((n) => n.toString(16).padStart(2, "0")).join("");
  }

  enable() { this.enabled = true; }

  schedule(delay = 150) {
    if (!this.enabled) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, delay);
  }

  async flush(options = {}) {
    if (!this.enabled) return true;
    clearTimeout(this.timer);
    this.timer = null;
    let state, serialized;
    try {
      state = this.capture();
      serialized = JSON.stringify(state);
    } catch (error) {
      this.report(error);
      return false;
    }
    // Si l'utilisateur revient à une ancienne valeur pendant qu'une écriture
    // est en vol, il faut quand même envoyer cette dernière intention.
    if (!options.force && !this.inFlight && serialized === this.lastSaved) return true;
    const sequence = ++this.sequence;
    const body = JSON.stringify({ client: this.client, sequence, state });
    // Les navigateurs limitent l'ensemble des requêtes keepalive à 64 Kio.
    // Au-delà, une requête ordinaire est best-effort sur pagehide ; la
    // fermeture native attend, elle, une requête normale confirmée.
    const keepalive = Boolean(options.keepalive)
      && new TextEncoder().encode(body).byteLength <= 60 * 1024;
    this.inFlight += 1;
    try {
      const response = await this.fetcher("/api/ui-state", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body, keepalive,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      if (sequence >= this.latestSuccess && payload.saved !== false) {
        this.latestSuccess = sequence;
        this.lastSaved = serialized;
        this.reportedError = false;
      }
      return true;
    } catch (error) {
      if (sequence > this.latestSuccess) this.report(error);
      return false;
    } finally {
      this.inFlight -= 1;
    }
  }

  report(error) {
    if (!this.reportedError) this.onError(error);
    this.reportedError = true;
  }
}

if (typeof module !== "undefined" && module.exports) module.exports = { UiSessionWriter };
