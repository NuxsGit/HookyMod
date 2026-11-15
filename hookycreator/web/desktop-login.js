"use strict";

// Le fragment n'est jamais transmis au serveur ni inclus dans les références.
// Effacer l'URL avant d'échanger le jeton contre un cookie HttpOnly temporaire.
(async () => {
  const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
  history.replaceState(null, "", location.pathname);
  const message = document.getElementById("message");
  try {
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(token)) throw new Error("session absente");
    const response = await fetch("/api/desktop/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error("session refusée");
    location.replace("/");
  } catch (_) {
    message.textContent = "Impossible d'ouvrir la session. Ferme et relance HookyCreator.";
  }
})();
