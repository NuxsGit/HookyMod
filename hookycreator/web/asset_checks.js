"use strict";

/* Un seul décodage de vérification en arrière-plan. Les candidats ne deviennent
   visibles qu'après réponse positive ; changer de vue/scan annule les demandes
   obsolètes. Les échecs réseau ne sont jamais considérés comme une validation. */
class AssetValidationQueue {
  constructor(options = {}) {
    this.fetcher = options.fetcher || globalThis.fetch.bind(globalThis);
    this.onChange = options.onChange || (() => {});
    this.timeoutMs = options.timeoutMs || 120000;
    this.revision = null;
    this.scopes = new Map();
    this.entries = new Map();
    this.active = null;
    this.holds = 0;
    this.retryTimer = null;
  }

  key(kind, rel) { return `${kind}:${rel}`; }
  entry(kind, rel) { return this.entries.get(this.key(kind, rel)) || null; }
  valid(kind, rel) { return this.entry(kind, rel)?.status === "valid"; }

  setScope(scope, assets, revision) {
    if (this.revision !== revision) {
      if (this.active) this.active.controller.abort();
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.revision = revision;
      this.scopes.clear();
      this.entries.clear();
    }
    const next = new Map();
    for (const asset of assets || []) {
      if (asset && ["model", "texture", "sound"].includes(asset.kind) && asset.rel) {
        next.set(this.key(asset.kind, asset.rel), { kind: asset.kind, rel: asset.rel });
      }
    }
    this.scopes.set(scope, next);
    if (this.active && !this.wanted(this.active.key)) this.active.controller.abort();
    this.pump();
  }

  clearScope(scope) {
    this.scopes.delete(scope);
    if (this.active && !this.wanted(this.active.key)) this.active.controller.abort();
    if (![...this.scopes.values()].some((items) => items.size)) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.pump();
  }

  wanted(key) { return [...this.scopes.values()].some((items) => items.has(key)); }

  hold() {
    this.holds += 1;
    if (this.active) this.active.controller.abort();
    let done = false;
    return () => {
      if (done) return;
      done = true;
      this.holds = Math.max(0, this.holds - 1);
      this.pump();
    };
  }

  markError(kind, rel, reason) {
    const key = this.key(kind, rel);
    if (this.active?.key === key) this.active.controller.abort();
    this.entries.set(key, { status: "error", reason });
    this.onChange(kind, rel, this.entries.get(key));
  }

  retryErrors() {
    for (const [key, entry] of this.entries) {
      if (entry.status === "error") this.entries.delete(key);
    }
    this.pump();
  }

  pump() {
    if (this.holds || this.active || this.retryTimer) return;
    const scopes = [...this.scopes.entries()].sort(([a], [b]) =>
      (a === "inspector" ? -1 : 0) - (b === "inspector" ? -1 : 0));
    for (const [, items] of scopes) {
      for (const [key, asset] of items) {
        if (!this.entries.has(key)) { this.start(key, asset); return; }
      }
    }
  }

  async start(key, asset) {
    const controller = new AbortController();
    const revision = this.revision;
    const active = { key, controller };
    this.active = active;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeoutMs);
    try {
      const url = `/api/asset/check?kind=${encodeURIComponent(asset.kind)}`
        + `&path=${encodeURIComponent(asset.rel)}&thumb=1`;
      const response = await this.fetcher(url, { signal: controller.signal });
      if (revision !== this.revision || controller.signal.aborted || !this.wanted(key)) return;
      if (response.status === 429 || response.status === 503) {
        const seconds = Number(response.headers?.get("Retry-After"));
        const delay = Math.max(250, Math.min(5000, seconds > 0 ? seconds * 1000 : 750));
        this.retryTimer = setTimeout(() => { this.retryTimer = null; this.pump(); }, delay);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (revision !== this.revision || controller.signal.aborted || !this.wanted(key)) return;
      const status = payload.retry ? "error" : payload.valid === true ? "valid" : "invalid";
      this.entries.set(key, { status, reason: payload.reason || "", injectable: payload.injectable === true });
      this.onChange(asset.kind, asset.rel, this.entries.get(key));
    } catch (error) {
      if (revision === this.revision && this.wanted(key) && (!controller.signal.aborted || timedOut)) {
        this.entries.set(key, { status: "error", reason: timedOut ? "Vérification trop longue" : error.message });
        this.onChange(asset.kind, asset.rel, this.entries.get(key));
      }
    } finally {
      clearTimeout(timeout);
      if (this.active === active) this.active = null;
      this.pump();
    }
  }
}

if (typeof module !== "undefined" && module.exports) module.exports = { AssetValidationQueue };
