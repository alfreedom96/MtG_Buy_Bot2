/**
 * Cloudflare Worker - ponte Telegram -> GitHub Actions
 * ------------------------------------------------------
 * Riceve i messaggi Telegram in tempo reale (via webhook) e fa partire
 * immediatamente il workflow "Comandi Telegram CardTrader" su GitHub,
 * passandogli il testo del comando come input. Così bot_commands.py non
 * deve più aspettare un giro di polling programmato: risponde in pochi
 * secondi (il tempo che GitHub Actions impiega ad avviare il job).
 *
 * Variabili d'ambiente da configurare nel Worker (Settings -> Variables):
 *   TELEGRAM_CHAT_ID   la tua chat id (per ignorare messaggi non tuoi)
 *   GITHUB_REPO        "utente/nome-repository", es. "alfreedom96/MtG_Buy_Bot"
 *   GITHUB_TOKEN        un Personal Access Token con permesso di scrivere
 *                       su "Actions" per quel repository (da salvare come
 *                       variabile "criptata"/secret nel Worker)
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("OK", { status: 200 });
    }

    const messaggio = update.message || update.edited_message;
    if (!messaggio || !messaggio.text) {
      return new Response("OK", { status: 200 });
    }

    const chatId = String(messaggio.chat && messaggio.chat.id);
    if (chatId !== env.TELEGRAM_CHAT_ID) {
      // Messaggio da una chat non autorizzata: lo ignoriamo silenziosamente.
      return new Response("OK", { status: 200 });
    }

    const testo = messaggio.text.trim();
    if (!testo.startsWith("/")) {
      return new Response("OK", { status: 200 });
    }

    const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/bot_commands.yml/dispatches`;
    const risposta = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "cardtrader-price-watch-webhook",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { comando: testo },
      }),
    });

    if (!risposta.ok) {
      console.log("Errore chiamando GitHub:", risposta.status, await risposta.text());
    }

    return new Response("OK", { status: 200 });
  },
};
