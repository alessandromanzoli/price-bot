"""
Bot Telegram - Monitor Prezzi
==============================
Monitora i prezzi di prodotti su Amazon e altri siti e-commerce.
Avvisa l'utente quando il prezzo scende sotto la soglia impostata.

Installazione:
    pip install python-telegram-bot[job-queue]==21.5 requests beautifulsoup4 lxml

Variabili d'ambiente necessarie:
    BOT_TOKEN      - Token del bot Telegram (da @BotFather)
    SCRAPER_API_KEY - Chiave API di ScraperAPI (da scraperapi.com)
"""

import os
import json
import time
import logging
import asyncio
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ── Configurazione ──────────────────────────────────────────────────────────────

BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")
DATA_FILE       = "prodotti.json"
CHECK_INTERVAL  = 3600  # Controlla ogni ora (in secondi)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Stati conversazione per /aggiungi
ASK_URL, ASK_SOGLIA, ASK_NOME = range(3)

# ── Gestione dati ───────────────────────────────────────────────────────────────

def carica_dati() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def salva_dati(dati: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)

def ottieni_prodotti_utente(user_id: str) -> list:
    return carica_dati().get(user_id, [])

def salva_prodotti_utente(user_id: str, prodotti: list) -> None:
    dati = carica_dati()
    dati[user_id] = prodotti
    salva_dati(dati)

# ── Scraping prezzi ─────────────────────────────────────────────────────────────

def estrai_prezzo_amazon(soup: BeautifulSoup) -> float | None:
    selettori = [
        "span.a-price-whole",
        "span.a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        ".a-price .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#apex_offerDisplay_desktop .a-offscreen",
    ]
    for sel in selettori:
        elementi = soup.select(sel)
        for el in elementi:
            testo = el.get_text(strip=True)
            testo = re.sub(r"[^\d,\.]", "", testo).replace(",", ".")
            if not testo:
                continue
            try:
                parti = testo.split(".")
                if len(parti) >= 2:
                    prezzo = float(parti[0] + "." + parti[-1])
                else:
                    prezzo = float(testo)
                if prezzo > 0:
                    return prezzo
            except ValueError:
                continue
    return None

def estrai_prezzo_generico(soup: BeautifulSoup) -> float | None:
    candidati = soup.find_all(
        True,
        attrs={"class": re.compile(r"price|precio|prezzo|preis", re.I)},
    )
    for el in candidati:
        testo = el.get_text(strip=True)
        match = re.search(r"(\d+[\.,]\d{2})", testo)
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except ValueError:
                continue
    return None

def get_prezzo(url: str) -> tuple[float | None, str]:
    """Recupera il prezzo usando ScraperAPI per bypassare i blocchi."""
    try:
        payload = {
            "api_key": SCRAPER_API_KEY,
            "url": url,
            "render": "true",
            "country_code": "it",
        }
        resp = requests.get(
            "http://api.scraperapi.com",
            params=payload,
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return None, f"Errore di rete: {e}"

    soup = BeautifulSoup(resp.text, "lxml")

    if "amazon." in url:
        prezzo = estrai_prezzo_amazon(soup)
    else:
        prezzo = estrai_prezzo_generico(soup)

    if prezzo is None:
        return None, "Prezzo non trovato (il sito potrebbe bloccare i bot)"

    return prezzo, ""

# ── Handler comandi Telegram ─────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    testo = (
        "👋 *Ciao! Sono il tuo monitor prezzi.*\n\n"
        "Ti avviso quando il prezzo di un prodotto scende sotto la soglia che imposti.\n\n"
        "📦 *Comandi disponibili:*\n"
        "/aggiungi — Aggiungi un prodotto\n"
        "/lista — Vedi tutti i tuoi prodotti\n"
        "/controlla — Controlla i prezzi adesso\n"
        "/rimuovi — Rimuovi un prodotto\n\n"
        "_Supporta Amazon.it e molti altri siti e-commerce._"
    )
    await update.message.reply_text(testo, parse_mode="Markdown")

# ── Conversazione /aggiungi ──────────────────────────────────────────────────────

async def aggiungi_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔗 Inviami il *link* del prodotto da monitorare.\n"
        "_(Es: https://www.amazon.it/dp/B08N5WRWNW)_",
        parse_mode="Markdown",
    )
    return ASK_URL

async def ricevi_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ URL non valido. Riprova con /aggiungi")
        return ConversationHandler.END

    context.user_data["url"] = url
    await update.message.reply_text("⏳ Controllo il prezzo attuale, attendi...")

    prezzo, errore = get_prezzo(url)
    if prezzo is None:
        await update.message.reply_text(
            f"⚠️ {errore}\n\nPuoi comunque impostare una soglia manualmente.\n"
            "A quale *prezzo (€)* vuoi ricevere l'avviso?",
            parse_mode="Markdown",
        )
    else:
        context.user_data["prezzo_attuale"] = prezzo
        await update.message.reply_text(
            f"✅ Prezzo attuale: *€{prezzo:.2f}*\n\n"
            "A quale *prezzo (€)* vuoi ricevere l'avviso?\n"
            "_(Scrivi solo il numero, es: 29.99)_",
            parse_mode="Markdown",
        )
    return ASK_SOGLIA

async def ricevi_soglia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        soglia = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Scrivi solo un numero, es: *29.99*", parse_mode="Markdown")
        return ASK_SOGLIA

    context.user_data["soglia"] = soglia
    await update.message.reply_text(
        "📝 Dai un *nome* a questo prodotto (es: Cuffie Sony):",
        parse_mode="Markdown",
    )
    return ASK_NOME

async def ricevi_nome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nome = update.message.text.strip()
    user_id = str(update.effective_user.id)

    prodotti = ottieni_prodotti_utente(user_id)
    nuovo_id = int(time.time())

    prodotti.append({
        "id": nuovo_id,
        "nome": nome,
        "url": context.user_data["url"],
        "soglia": context.user_data["soglia"],
        "ultimo_prezzo": context.user_data.get("prezzo_attuale"),
        "aggiunto_il": datetime.now().isoformat(),
    })
    salva_prodotti_utente(user_id, prodotti)

    await update.message.reply_text(
        f"✅ *{nome}* aggiunto!\n\n"
        f"💰 Soglia: *€{context.user_data['soglia']:.2f}*\n"
        f"🔔 Ti avviso appena il prezzo scende sotto questa cifra.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def annulla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operazione annullata.")
    return ConversationHandler.END

# ── Comando /lista ───────────────────────────────────────────────────────────────

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    prodotti = ottieni_prodotti_utente(user_id)

    if not prodotti:
        await update.message.reply_text(
            "📭 Non stai monitorando nessun prodotto.\nUsa /aggiungi per iniziare!"
        )
        return

    testo = "📋 *I tuoi prodotti monitorati:*\n\n"
    for p in prodotti:
        prezzo_str = f"€{p['ultimo_prezzo']:.2f}" if p.get("ultimo_prezzo") else "—"
        testo += (
            f"*{p['nome']}*\n"
            f"  💰 Ultimo prezzo: {prezzo_str}\n"
            f"  🎯 Soglia: €{p['soglia']:.2f}\n"
            f"  🆔 ID: `{p['id']}`\n\n"
        )
    testo += "_Per rimuovere: /rimuovi <ID>_"
    await update.message.reply_text(testo, parse_mode="Markdown")

# ── Comando /rimuovi ─────────────────────────────────────────────────────────────

async def rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("❓ Usa: /rimuovi <ID>\nTrova l'ID con /lista")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido.")
        return

    prodotti = ottieni_prodotti_utente(user_id)
    nuovi = [p for p in prodotti if p["id"] != target_id]
    if len(nuovi) == len(prodotti):
        await update.message.reply_text("❌ Prodotto non trovato.")
        return
    salva_prodotti_utente(user_id, nuovi)
    await update.message.reply_text("✅ Prodotto rimosso.")

# ── Comando /controlla ───────────────────────────────────────────────────────────

async def controlla_manuale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    prodotti = ottieni_prodotti_utente(user_id)
    if not prodotti:
        await update.message.reply_text("📭 Nessun prodotto da controllare.")
        return

    await update.message.reply_text(f"⏳ Controllo {len(prodotti)} prodotti...")
    risultati = []
    for p in prodotti:
        prezzo, errore = get_prezzo(p["url"])
        if prezzo:
            p["ultimo_prezzo"] = prezzo
            if prezzo <= p["soglia"]:
                risultati.append(f"🔔 *{p['nome']}* — €{prezzo:.2f} (soglia: €{p['soglia']:.2f}) ✅")
            else:
                diff = prezzo - p["soglia"]
                risultati.append(f"📦 *{p['nome']}* — €{prezzo:.2f} (mancano €{diff:.2f})")
        else:
            risultati.append(f"⚠️ *{p['nome']}* — {errore}")

    salva_prodotti_utente(user_id, prodotti)
    await update.message.reply_text(
        "📊 *Risultati:*\n\n" + "\n".join(risultati),
        parse_mode="Markdown",
    )

# ── Job periodico ────────────────────────────────────────────────────────────────

async def controlla_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    dati = carica_dati()
    modificati = False

    for user_id, prodotti in dati.items():
        for p in prodotti:
            prezzo, errore = get_prezzo(p["url"])
            if prezzo is None:
                continue

            vecchio = p.get("ultimo_prezzo")
            p["ultimo_prezzo"] = prezzo
            modificati = True

            if prezzo <= p["soglia"] and (vecchio is None or vecchio > p["soglia"]):
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🚨 *PREZZO IN CALO!*\n\n"
                            f"📦 *{p['nome']}*\n"
                            f"💰 Prezzo attuale: *€{prezzo:.2f}*\n"
                            f"🎯 La tua soglia: €{p['soglia']:.2f}\n\n"
                            f"👉 [Vai al prodotto]({p['url']})"
                        ),
                        parse_mode="Markdown",
                        disable_web_page_preview=False,
                    )
                except Exception as e:
                    logger.error(f"Errore invio notifica a {user_id}: {e}")

    if modificati:
        salva_dati(dati)

# ── Avvio bot ────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("aggiungi", aggiungi_start)],
        states={
            ASK_URL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_url)],
            ASK_SOGLIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_soglia)],
            ASK_NOME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_nome)],
        },
        fallbacks=[CommandHandler("annulla", annulla)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("lista", lista))
    app.add_handler(CommandHandler("rimuovi", rimuovi))
    app.add_handler(CommandHandler("controlla", controlla_manuale))

    app.job_queue.run_repeating(controlla_automatico, interval=CHECK_INTERVAL, first=10)

    logger.info("Bot avviato.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
