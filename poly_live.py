import asyncio
import json
import websockets
import logging
import os
import sys
from datetime import datetime, timezone
from whale_tracker import (
    load_state, save_state, analyze_with_claude,
    send_telegram, build_message, log, MIN_SIZE_USDC,
    is_wash_trader, _is_sport, check_resolutions,
    is_future_market, _is_past_market, is_market_resolved,
    build_gamma_resolution_cache
)

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Endpoint WebSocket per i trade in tempo reale (RTDS)
# Nota: RTDS è più adatto per il monitoraggio globale di tutti i trade
WS_URL = "wss://ws-live-data.polymarket.com"

async def process_trade(trade_data, state):
    """Processa un singolo trade ricevuto via WebSocket."""
    try:
        # Estrai dati fondamentali
        market_title = trade_data.get("market_title") or trade_data.get("title")
        size = float(trade_data.get("usdc_size") or trade_data.get("size") or 0)
        price = float(trade_data.get("price") or 0.5)
        side = trade_data.get("side") or "YES"
        wallet = (trade_data.get("user_address") or trade_data.get("maker") or "").lower()
        
        if not market_title or size < MIN_SIZE_USDC:
            return

        # 1. Filtro Sport
        if _is_sport(market_title):
            return

        # 2a. Filtro testuale date passate
        if _is_past_market(market_title):
            return

        # 2b. Filtro risoluzione Gamma API
        if is_market_resolved(market_title):
            return

        # 2c. Filtro strutturato su endDate (quando disponibile)
        if not is_future_market(trade_data):
            return

        # 3. Filtro Wash Trading
        if is_wash_trader(wallet):
            log(f"Live: Escluso wash trader {wallet[:14]}...", "WARN")
            return

        log(f"🔥 GRANDE TRADE RILEVATO: ${size:,.0f} su '{market_title[:50]}...'", "OK")

        # 3. Analisi con Claude
        # Prepariamo un oggetto trade compatibile con la funzione esistente
        trade_obj = {
            "title": market_title,
            "usdcSize": str(size),
            "price": str(price),
            "side": side,
            "userAddress": wallet,
            "whale_trust_score": 50, # Default per nuovi wallet rilevati live
            "whale_username": wallet[:10]
        }
        
        # Arricchisci con trust score se presente in leaderboard
        if wallet in state["leaderboard"]:
            trade_obj["whale_trust_score"] = state["leaderboard"][wallet].get("trust_score", 50)
            trade_obj["whale_username"] = state["leaderboard"][wallet].get("username", wallet[:10])

        result = analyze_with_claude(trade_obj, state)
        
        # 4. Notifica se COPY o WATCH
        if result["verdict"] in ["COPY", "WATCH"]:
            log(f"🎯 VERDETTO LIVE: {result['verdict']}! Invio notifiche...", "OK")
            msg = build_message([result], state)
            send_telegram(msg)
            
            # Salva nei watched_markets
            from whale_tracker import update_watched_markets
            update_watched_markets(state, [result])
            save_state(state)
            
    except Exception as e:
        logger.error(f"Errore nel processamento del trade: {e}")

async def main_loop():
    log("=" * 50)
    log("POLY LIVE: Monitoraggio H24 avviato")
    log(f"Soglia minima: ${MIN_SIZE_USDC:,.0f}")
    log("=" * 50)
    
    state = load_state()

    # Build Gamma resolution cache e controllo risoluzioni all'avvio
    build_gamma_resolution_cache()
    check_resolutions(state)
    save_state(state)

    while True:
        try:
            async with websockets.connect(WS_URL) as websocket:
                log(f"Connesso al flusso dati Polymarket ({WS_URL})", "OK")
                
                async for message in websocket:
                    data = json.loads(message)
                    
                    # Il formato RTDS solitamente invia eventi di tipo 'trade'
                    if data.get("event_type") == "trade" or "usdc_size" in data:
                        await process_trade(data, state)
                        
                    # Ogni ora circa, ricarica lo stato e controlla risoluzioni
                    if datetime.now().minute == 0 and datetime.now().second < 5:
                        check_resolutions(state)
                        save_state(state)
                        
        except Exception as e:
            log(f"Connessione persa: {e}. Riconnessione tra 10 secondi...", "WARN")
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log("Monitoraggio H24 terminato dall'utente.")
    except Exception as e:
        log(f"Errore fatale: {e}", "ERR")
