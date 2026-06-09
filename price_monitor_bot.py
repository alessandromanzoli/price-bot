"""
Bot Telegram - Monitor Prezzi
==============================
Monitora i prezzi di prodotti su Amazon e altri siti e-commerce.
Avvisa l'utente quando il prezzo scende sotto la soglia impostata.

Installazione:
    pip install python-telegram-bot requests beautifulsoup4 lxml

Utilizzo:
    1. Crea un bot su Telegram con @BotFather e ottieni il TOKEN
    2. Inserisci il token in BOT_TOKEN qui sotto
    3. Avvia con: python price_monitor_bot.py

Comandi bot:
    /start        - Messaggio di benvenuto
    /aggiungi     - Aggiungi un prodotto da monitorare
    /lista        - Mostra tutti i prodotti monitorati
    /rimuovi <id> - Rimuovi un prodotto dalla lista
    /controlla    - Controlla subito tutti i prezzi
"""

import os
import json
import time
import logging
import asyncio
import re
from datetime import datetime

import requests
import cloudscraper
scraper = cloudscraper.create_scraper()
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ── Configurazione ──────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "prodotti.json"       # File dove vengono salvati i prodotti
CHECK_INTERVAL = 3600             # Controlla ogni ora (in secondi)
SCRAPER_API_KEY = "46436de7d527a03b4e6118175382aa77"


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Stati conversazione per /aggiungi
ASK_URL, ASK_SOGLIA, ASK_NOME = range(3)

# Headers per sembrare un browser reale (evita blocchi anti-bot)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Gestione dati (salvataggio su JSON) ─────────────────────────────────────────

def carica_dati() -> dict:
    """Carica i prodotti salvati dal file JSON."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salva_dati(dati: dict) -> None:
    """Salva i prodotti nel file JSON."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)


def ottieni_prodotti_utente(user_id: str) -> list:
    """Restituisce i prodotti monitorati da un utente specifico."""
    dati = carica_dati()
    return dati.get(user_id, [])


def salva_prodotti_utente(user_id: str, prodotti: list) -> None:
    """Salva i prodotti di un utente."""
    dati = carica_dati()
    dati[user_id] = prodotti
    salva_dati(dati)


# ── Scraping prezzi ─────────────────────────────────────────────────────────────

def estrai_prezzo_amazon(soup: BeautifulSoup) -> float | None:
    """Estrae il prezzo da una pagina Amazon."""
    selettori = [
        "span.a-price-whole",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "span.a-offscreen",
        ".a-price .a-offscreen",
    ]
    for sel in selettori:
        el = soup.select_one(sel)
        if el:
            testo = el.get_text(strip=True)
            testo = re.sub(r"[^\d,\.]", "", testo).replace(",", ".")
            try:
                return float(testo.split(".")[0] + "." + testo.split(".")[-1] if testo.count(".") > 1 else testo)
            except ValueError:
                continue
    return None


def estrai_prezzo_generico(soup: BeautifulSoup) -> float | None:
    """Tenta di estrarre un prezzo da qualsiasi sito e-commerce."""
    # Cerca elementi con attributi comuni per i prezzi
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
    try:
        payload = {
            "api_key": SCRAPER_API_KEY,
            "url": url,
            "render": "false",
            "country_code": "it",
        }
        resp = requests.get(
            "http://api.scraperapi.com",
            params=payload,
            timeout=30,
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
        return None, "Prezzo non trovato."

    return prezzo, ""
    """
    Scarica la pagina e tenta di estrarre il prezzo.
    Restituisce (prezzo, messaggio_errore).
    """
    try:
        resp = scraper.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return None, f"Errore di rete: {e}"

    soup = BeautifulSoup(resp.text, "lxml")

    # Sceglie la strategia in base al dominio
    if "amazon." in url:
        prezzo = estrai_prezzo_amazon(soup)
    else:
        prezzo = estrai_prezzo_generico(soup)

    if prezzo is None:
        return None, "Prezzo non trovato (il sito potrebbe bloccare i bot)"

    return prezzo, ""


# ── Handler comandi Telegram ─────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Messaggio di benvenuto."""
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
    """Avvia la conversazione per aggiungere un prodotto."""
    await update.message.reply_text(
        "🔗 Inviami il *link* del prodotto da monitorare.\n"
        "_(Es: https://www.amazon.it/dp/B08N5WRWNW)_",
        parse_mode="Markdown",
    )
    return ASK_URL


async def ricevi_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Riceve l'URL e controlla subito il prezzo attuale."""
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("❌ URL non valido. Riprova con /aggiungi")
        return ConversationHandler.END

    context.user_data["url"] = url
    await update.message.reply_text("⏳ Controllo il prezzo attuale...")

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
    """Riceve la soglia di prezzo."""
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
    """Riceve il nome e salva il prodotto."""
    nome = update.message.text.strip()
    user_id = str(update.effective_user.id)

    prodotti = ottieni_prodotti_utente(user_id)
    nuovo_id = int(time.time())  # ID univoco basato sul timestamp

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
    """Annulla la conversazione corrente."""
    await update.message.reply_text("❌ Operazione annullata.")
    return ConversationHandler.END


# ── Comando /lista ───────────────────────────────────────────────────────────────

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra tutti i prodotti monitorati dall'utente."""
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
    """Rimuove un prodotto dalla lista."""
    user_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "❓ Usa: /rimuovi <ID>\nTrova l'ID con /lista"
        )
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
    """Controlla subito i prezzi di tutti i prodotti dell'utente."""
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
                risultati.append(
                    f"🔔 *{p['nome']}* — €{prezzo:.2f} (soglia: €{p['soglia']:.2f}) ✅"
                )
            else:
                diff = prezzo - p["soglia"]
                risultati.append(
                    f"📦 *{p['nome']}* — €{prezzo:.2f} (mancano €{diff:.2f})"
                )
        else:
            risultati.append(f"⚠️ *{p['nome']}* — {errore}")

    salva_prodotti_utente(user_id, prodotti)
    await update.message.reply_text(
        "📊 *Risultati:*\n\n" + "\n".join(risultati),
        parse_mode="Markdown",
    )


# ── Job periodico ────────────────────────────────────────────────────────────────

async def controlla_automatico(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Controlla automaticamente tutti i prezzi ogni CHECK_INTERVAL secondi.
    Invia una notifica se un prezzo scende sotto la soglia.
    """
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

            # Notifica solo se sotto soglia E il prezzo è cambiato (o è il primo controllo)
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
    """Avvia il bot."""
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversazione per /aggiungi
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

    # Job automatico ogni CHECK_INTERVAL secondi
    app.job_queue.run_repeating(controlla_automatico, interval=CHECK_INTERVAL, first=10)

    logger.info("Bot avviato. Premi Ctrl+C per fermare.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
