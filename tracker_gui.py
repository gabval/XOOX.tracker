"""
XOOX Hunter & Sniper Pro - GUI Tracker
======================================
Monitor de alta velocidad para detección de Contract Addresses (CA) en Monad.
Rastreo asíncrono de xoox.fun (Web + scripts JS) y perfiles de X (Twitter) vía twikit.
Alertas automáticas en Telegram, copia inmediata al portapapeles y alarma sonoro en Windows.

Desarrollado con CustomTkinter, Asyncio, Aiohttp, Twikit y Pyperclip.
"""

import os
import sys
import re
import json
import time
import random
import asyncio
import threading
import datetime
import urllib.parse
from typing import Optional, Set, Dict, Any, List

# Dependencias visuales y utilitarias
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import pyperclip
import aiohttp
from bs4 import BeautifulSoup

# Sonido en Windows
try:
    import winsound
except ImportError:
    winsound = None

# Módulo Twikit para X (Twitter)
try:
    from twikit import Client as TwikitClient
    from twikit.errors import TwitterException
    TWIKIT_AVAILABLE = True
except ImportError:
    TWIKIT_AVAILABLE = False


# ==============================================================================
# CONSTANTES Y CONFIGURACIONES POR DEFECTO
# ==============================================================================
CONFIG_FILE = "config.json"
COOKIES_FILE = "cookies.json"

DEFAULT_CONFIG = {
    "telegram": {
        "bot_token": "",
        "chat_id": ""
    },
    "sniper": {
        "private_key": "",
        "rpc_url": "https://testnet-rpc.monad.xyz",
        "buy_amount_mon": "0.1"
    },
    "tracker": {
        "x_targets": ["xooxdotfun", "gabfag_"],
        "web_target_url": "https://www.xoox.fun/",
        "web_interval_seconds": 4.0,
        "x_interval_min_seconds": 12.0,
        "x_interval_max_seconds": 16.0
    }
}

# Regex para detección de direcciones Ethereum / Monad (40 caracteres hexadecimales)
CA_REGEX = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Filtro de direcciones nulas o quemadas (dead / burn)
NULL_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000000000000000000000000"
}


# ==============================================================================
# GESTIÓN DE CONFIGURACIÓN Y COOKIES
# ==============================================================================
def load_config() -> dict:
    """Carga config.json o crea uno por defecto si no existe."""
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Combinar con valores por defecto para asegurar todas las claves
            merged = DEFAULT_CONFIG.copy()
            if "telegram" in data:
                merged["telegram"].update(data["telegram"])
            if "sniper" in data:
                merged["sniper"].update(data["sniper"])
            if "tracker" in data:
                merged["tracker"].update(data["tracker"])
            return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(config_data: dict) -> None:
    """Guarda la configuración en config.json de forma segura."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)


def parse_cookie_editor_file(filepath: str) -> Optional[dict]:
    """
    Lee cookies.json y normaliza tanto el formato array de Cookie-Editor
    como un diccionario plano llave-valor.
    """
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)

        cookies_dict = {}
        if isinstance(raw, list):
            # Formato Cookie-Editor: [{"name": "auth_token", "value": "xyz", ...}, ...]
            for item in raw:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    cookies_dict[str(item["name"])] = str(item["value"])
        elif isinstance(raw, dict):
            # Formato plano: {"auth_token": "xyz", "ct0": "abc"}
            cookies_dict = {str(k): str(v) for k, v in raw.items()}

        if cookies_dict:
            return cookies_dict
        return None
    except Exception:
        return None


# ==============================================================================
# MOTOR ASÍNCRONO DEL TRACKER
# ==============================================================================
class TrackerEngine:
    """
    Motor central asíncrono que orquesta el rastreo web, la lectura de X (Twitter),
    la detección de contratos y la propagación de alertas hacia la GUI y Telegram.
    """

    def __init__(self, config: dict, log_callback, on_ca_found_callback, on_status_change_callback):
        self.config = config
        self.log = log_callback
        self.on_ca_found = on_ca_found_callback
        self.on_status_change = on_status_change_callback

        self.is_running = False
        self.stop_requested = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # Historial para deduplicación
        self.detected_cas: Set[str] = set()
        self.scanned_script_urls: Set[str] = set()
        self.seen_tweet_ids: Set[str] = set()
        self.previous_bios: Dict[str, str] = {}

    def request_stop(self):
        """Solicita el apagado limpio del motor asíncrono."""
        self.stop_requested = True
        self.log("⚠️ Solicitud de detención recibida. Finalizando tareas asíncronas...", "WARN")

    def is_valid_ca(self, ca: str) -> bool:
        """Valida que la dirección no sea nula ni repetida."""
        ca_lower = ca.lower()
        if ca_lower in NULL_ADDRESSES:
            return False
        # Descartar si son 40 ceros
        if ca_lower.replace("0x", "") == "0" * 40:
            return False
        return True

    def trigger_local_reaction(self, ca: str, source_name: str, is_drill: bool = False):
        """
        Ejecuta las reacciones locales:
        1. Copia al portapapeles con pyperclip.
        2. Alarma sonora (winsound.Beep repetido 5 veces) en hilo secundario.
        3. Registro visual destacado en la GUI.
        """
        # 1. Copia rápida al portapapeles
        try:
            pyperclip.copy(ca)
            clip_ok = True
        except Exception as e:
            clip_ok = False
            self.log(f"Error copiando al portapapeles: {e}", "WARN")

        # 2. Alarma sonora no bloqueante (5 beeps de 1200Hz y 400ms)
        def _beep_thread():
            if winsound:
                for _ in range(5):
                    try:
                        winsound.Beep(1200, 400)
                        time.sleep(0.04)
                    except Exception:
                        break

        threading.Thread(target=_beep_thread, daemon=True).start()

        # 3. Registro visual
        tag_type = "SIMULACRO" if is_drill else "ALERT"
        status_clip = " [📋 Copiado al Portapapeles!]" if clip_ok else ""
        self.log(f"🚨🚨 ¡CONTRATO DETECTADO! 🚨🚨", tag_type)
        self.log(f"📍 Fuente: {source_name}", tag_type)
        self.log(f"💎 CA: {ca}{status_clip}", tag_type)

        # Disparar callback para actualizar contadores en GUI
        if self.on_ca_found:
            self.on_ca_found(ca, source_name, is_drill)

    async def send_telegram_alert(self, session: aiohttp.ClientSession, ca: str, source_name: str, is_drill: bool = False):
        """
        Envía notificación instantánea a Telegram con formato HTML y enlaces directos.
        """
        bot_token = self.config.get("telegram", {}).get("bot_token", "").strip()
        chat_id = self.config.get("telegram", {}).get("chat_id", "").strip()

        if not bot_token or not chat_id:
            self.log("ℹ️ Telegram omitido: Token o Chat ID no configurados.", "INFO")
            return

        header = "🧪 <b>[SIMULACRO - @gabfag_]</b>" if is_drill else "🚨 <b>[OFICIAL - XOOX ALERT]</b>"
        dex_url = f"https://dexscreener.com/search?q={ca}"
        nad_url = f"https://nad.fun/token/{ca}"
        monad_scan_url = f"https://testnet.monadexplorer.com/address/{ca}"

        message_html = (
            f"{header}\n"
            f"<b>¡Nuevo Contract Address (CA) Detectado!</b>\n\n"
            f"<b>📍 Origen:</b> <code>{source_name}</code>\n"
            f"<b>💎 Contrato:</b>\n<code>{ca}</code>\n\n"
            f"<b>🔗 Acceso Inmediato:</b>\n"
            f"• <a href=\"{dex_url}\">Ver en DexScreener</a>\n"
            f"• <a href=\"{nad_url}\">Ver en nad.fun</a>\n"
            f"• <a href=\"{monad_scan_url}\">Ver en Monad Explorer</a>\n\n"
            f"⚡ <i>Detectado en milisegundos por XOOX Sniper Tracker</i>"
        )

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    self.log(f"✅ Alerta de Telegram enviada exitosamente para {ca[:10]}...", "SUCCESS")
                else:
                    err_txt = await resp.text()
                    self.log(f"⚠️ Error Telegram API (Status {resp.status}): {err_txt}", "WARN")
        except Exception as e:
            self.log(f"⚠️ Error enviando alerta a Telegram: {e}", "WARN")

    async def process_text_for_cas(self, text: str, source_name: str, is_drill: bool = False, session: Optional[aiohttp.ClientSession] = None):
        """
        Escanea cualquier fragmento de texto (HTML, JS, Tweets, Bios) en busca de CAs.
        """
        if not text:
            return

        matches = CA_REGEX.findall(text)
        for ca in matches:
            if not self.is_valid_ca(ca):
                continue

            ca_lower = ca.lower()
            if ca_lower not in self.detected_cas:
                self.detected_cas.add(ca_lower)
                # 1. Alarma y portapapeles
                self.trigger_local_reaction(ca, source_name, is_drill)
                # 2. Envío a Telegram
                if session:
                    await self.send_telegram_alert(session, ca, source_name, is_drill)

    # --------------------------------------------------------------------------
    # RASTREADOR WEB (xoox.fun + CDN Bypass + Scripts JS)
    # --------------------------------------------------------------------------
    async def run_web_tracker(self, session: aiohttp.ClientSession):
        """
        Ciclo de escaneo Web permanente cada 4 segundos con bypass de caché.
        """
        base_url = self.config.get("tracker", {}).get("web_target_url", "https://www.xoox.fun/")
        interval = float(self.config.get("tracker", {}).get("web_interval_seconds", 4.0))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        }

        self.log(f"🌐 [WEB] Iniciando monitor en {base_url} (Intervalo: {interval}s)", "INFO")

        while not self.stop_requested:
            try:
                # Bypass CDN mediante parámetro timestamp único
                ts_param = f"_cb={int(time.time() * 1000)}"
                target_url = f"{base_url}{'&' if '?' in base_url else '?'}{ts_param}"

                async with session.get(target_url, headers=headers, timeout=aiohttp.ClientTimeout(total=4.5)) as resp:
                    if resp.status == 200:
                        html_text = await resp.text()

                        # 1. Buscar CAs en el propio HTML
                        await self.process_text_for_cas(html_text, f"Web Principal ({base_url})", is_drill=False, session=session)

                        # 2. Analizar etiquetas <script> con BeautifulSoup
                        soup = BeautifulSoup(html_text, "html.parser")
                        script_tags = soup.find_all("script")

                        for script in script_tags:
                            # Script inline
                            if script.string:
                                await self.process_text_for_cas(script.string, f"Inline JS ({base_url})", is_drill=False, session=session)

                            # Script externo .js
                            src = script.get("src")
                            if src:
                                js_url = urllib.parse.urljoin(base_url, src)
                                # Si es un script no escaneado o un bundle relevante
                                if js_url not in self.scanned_script_urls:
                                    self.scanned_script_urls.add(js_url)
                                    self.log(f"🔎 [WEB] Nuevo script detectado: {js_url.split('/')[-1]}", "INFO")
                                    await self.fetch_and_scan_js(session, js_url, headers)

                    else:
                        self.log(f"⚠️ [WEB] Estado HTTP inusual: {resp.status} en {base_url}", "WARN")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log(f"⚠️ [WEB] Error al escanear sitio web: {e}", "WARN")

            # Espera no bloqueante
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def fetch_and_scan_js(self, session: aiohttp.ClientSession, js_url: str, headers: dict):
        """Descarga un archivo .js y busca CAs en su contenido."""
        try:
            ts_param = f"_cb={int(time.time() * 1000)}"
            target_js = f"{js_url}{'&' if '?' in js_url else '?'}{ts_param}"
            async with session.get(target_js, headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    js_content = await resp.text()
                    await self.process_text_for_cas(js_content, f"Script JS ({js_url})", is_drill=False, session=session)
        except Exception as e:
            self.log(f"⚠️ [WEB] Error descargando script {js_url}: {e}", "WARN")

    # --------------------------------------------------------------------------
    # RASTREADOR X (TWIKIT + COOKIES + BIOS + TWEETS)
    # --------------------------------------------------------------------------
    async def run_x_tracker(self, session: aiohttp.ClientSession):
        """
        Ciclo de monitoreo de perfiles de X (Twitter) cada 12-16 segundos.
        """
        if not TWIKIT_AVAILABLE:
            self.log("❌ [X-TRACKER] La librería 'twikit' no está instalada. Monitoreo de X desactivado.", "WARN")
            return

        cookies_dict = parse_cookie_editor_file(COOKIES_FILE)
        if not cookies_dict:
            self.log("⚠️ [X-TRACKER] No se encontró 'cookies.json' válido en el directorio.", "WARN")
            self.log("💡 Para habilitar el rastreo de X: Exporta tus cookies de Twitter usando 'Cookie-Editor' y guárdalas en 'cookies.json'.", "INFO")
            self.log("🌐 El rastreo WEB seguirá funcionando normalmente.", "INFO")
            return

        self.log("🔑 [X-TRACKER] Cargando cookies desde cookies.json...", "INFO")
        try:
            client = TwikitClient(language="en-US")
            client.set_cookies(cookies_dict)
            self.log("✅ [X-TRACKER] Sesión de Twikit inicializada con éxito.", "SUCCESS")
        except Exception as e:
            self.log(f"❌ [X-TRACKER] Fallo al cargar cookies de Twikit: {e}", "WARN")
            return

        targets = self.config.get("tracker", {}).get("x_targets", ["xooxdotfun", "gabfag_"])
        min_sec = float(self.config.get("tracker", {}).get("x_interval_min_seconds", 12.0))
        max_sec = float(self.config.get("tracker", {}).get("x_interval_max_seconds", 16.0))

        self.log(f"🎯 [X-TRACKER] Monitoreando objetivos: {', '.join(['@' + t for t in targets])}", "INFO")

        # Cargar bios iniciales para detectar modificaciones futuras
        for target in targets:
            try:
                user = await client.get_user_by_screen_name(target)
                self.previous_bios[target] = user.description or ""
                # Escaneo inicial de bio
                is_drill = (target.lower() == "gabfag_")
                await self.process_text_for_cas(user.description or "", f"Bio Inicial @{target}", is_drill=is_drill, session=session)
            except Exception as e:
                self.log(f"⚠️ [X-TRACKER] No se pudo obtener bio inicial de @{target}: {e}", "WARN")

        # Bucle principal de chequeo de X
        while not self.stop_requested:
            for target in targets:
                if self.stop_requested:
                    break

                is_drill = (target.lower() == "gabfag_")
                try:
                    # 1. Chequeo de Usuario y Bio
                    user = await client.get_user_by_screen_name(target)
                    current_bio = user.description or ""
                    prev_bio = self.previous_bios.get(target, "")

                    if current_bio != prev_bio:
                        self.log(f"📝 [X-TRACKER] ¡Cambio de bio detectado en @{target}!", "ALERT")
                        self.previous_bios[target] = current_bio
                        await self.process_text_for_cas(current_bio, f"Bio Actualizada @{target}", is_drill=is_drill, session=session)

                    # 2. Chequeo de Últimos Tweets
                    tweets = await user.get_tweets("Tweets", count=4)
                    for tweet in tweets:
                        tweet_id = str(getattr(tweet, "id", ""))
                        if not tweet_id or tweet_id in self.seen_tweet_ids:
                            continue

                        self.seen_tweet_ids.add(tweet_id)
                        tweet_text = getattr(tweet, "text", "") or getattr(tweet, "full_text", "")

                        self.log(f"🐦 [X-TRACKER] Nuevo tweet de @{target}: {tweet_text[:60]}...", "INFO")
                        await self.process_text_for_cas(tweet_text, f"Tweet @{target}", is_drill=is_drill, session=session)

                except TwitterException as te:
                    self.log(f"⚠️ [X-TRACKER] Twitter API Rate Limit / Error en @{target}: {te}", "WARN")
                except Exception as e:
                    self.log(f"⚠️ [X-TRACKER] Error chequeando @{target}: {e}", "WARN")

                # Pequeño escalonamiento entre targets para no golpear rate-limits
                await asyncio.sleep(2.0)

            # Espera aleatoria entre 12 y 16 segundos
            sleep_time = random.uniform(min_sec, max_sec)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    # --------------------------------------------------------------------------
    # ENTRYPOINT DEL HILO ASÍNCRONO
    # --------------------------------------------------------------------------
    def run_main_loop(self):
        """Método sincrónico ejecutado dentro de threading.Thread."""
        self.is_running = True
        self.stop_requested = False
        if self.on_status_change:
            self.on_status_change(True)

        async def _async_runner():
            conn = aiohttp.TCPConnector(limit=50, ssl=False)
            async with aiohttp.ClientSession(connector=conn) as session:
                self.log("🚀 Motor Asíncrono iniciado. Lanzando tareas de monitoreo...", "SUCCESS")
                web_task = asyncio.create_task(self.run_web_tracker(session))
                x_task = asyncio.create_task(self.run_x_tracker(session))

                # Esperar hasta que se solicite el apagado
                while not self.stop_requested:
                    await asyncio.sleep(0.5)

                # Cancelar tareas al detener
                web_task.cancel()
                x_task.cancel()
                await asyncio.gather(web_task, x_task, return_exceptions=True)
                self.log("⏹️ Motor Asíncrono detenido correctamente.", "INFO")

        try:
            asyncio.run(_async_runner())
        except Exception as e:
            self.log(f"❌ Error crítico en el loop asíncrono: {e}", "WARN")
        finally:
            self.is_running = False
            if self.on_status_change:
                self.on_status_change(False)


# ==============================================================================
# INTERFAZ GRÁFICA MODERNA (CUSTOMTKINTER)
# ==============================================================================
class XOOXTrackerApp(ctk.CTk):
    """
    Ventana principal moderna construida con CustomTkinter.
    Incluye paneles de configuración, preparación de Sniper Wallet,
    consola en tiempo real con colores y control de ejecución multi-hilo.
    """

    def __init__(self):
        super().__init__()

        # Configuración de apariencia
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("XOOX Hunter & Sniper Pro | Monad Contract Sniffer")
        self.geometry("1060x860")
        self.minsize(980, 780)

        # Cargar configuración inicial
        self.config = load_config()

        # Estado del motor
        self.tracker_engine: Optional[TrackerEngine] = None
        self.worker_thread: Optional[threading.Thread] = None

        # Contadores de sesión
        self.ca_found_count = 0
        self.start_timestamp: Optional[float] = None

        # Construcción de la interfaz
        self._build_header()
        self._build_top_panels()
        self._build_control_bar()
        self._build_console_panel()
        self._build_status_bar()

        # Enlazar cierre seguro de ventana
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Log inicial de bienvenida
        self.log_to_gui("✨ Sistema XOOX Hunter inicializado en Modo Oscuro.", "SUCCESS")
        self.log_to_gui(f"🎯 Objetivos X configurados: {', '.join(self.config['tracker']['x_targets'])}", "INFO")
        self.log_to_gui(f"🌐 Objetivo Web: {self.config['tracker']['web_target_url']}", "INFO")

    # --------------------------------------------------------------------------
    # CONSTRUCCIÓN DE COMPONENTES UI
    # --------------------------------------------------------------------------
    def _build_header(self):
        """Encabezado superior con título, versión y estado."""
        header_frame = ctk.CTkFrame(self, fg_color="#18181B", corner_radius=12, height=75)
        header_frame.pack(fill="x", padx=15, pady=(15, 10))
        header_frame.pack_propagate(False)

        # Título y subtítulo
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=20, pady=10)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="⚡ XOOX HUNTER & SNIPER",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#F4F4F5"
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = ctk.CTkLabel(
            title_box,
            text="Ultralow-Latency Monad Contract Address Sniffer & Watcher",
            font=ctk.CTkFont(size=12),
            text_color="#A1A1AA"
        )
        subtitle_lbl.pack(anchor="w")

        # Insignia de estado (Badge)
        self.status_badge = ctk.CTkLabel(
            header_frame,
            text="● DETENIDO",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#EF4444",
            fg_color="#27272A",
            corner_radius=8,
            padx=16,
            pady=6
        )
        self.status_badge.pack(side="right", padx=20)

    def _build_top_panels(self):
        """Contenedor dividido para Alertas Telegram y Sniper Wallet."""
        top_container = ctk.CTkFrame(self, fg_color="transparent")
        top_container.pack(fill="x", padx=15, pady=5)
        top_container.grid_columnconfigure((0, 1), weight=1, uniform="panels")

        # ======================================================================
        # SECCIÓN 1: Configuración de Alertas (Telegram)
        # ======================================================================
        tg_card = ctk.CTkFrame(top_container, fg_color="#1E1E24", corner_radius=12)
        tg_card.grid(row=0, column=0, padx=(0, 7), sticky="nsew")

        tg_title = ctk.CTkLabel(
            tg_card,
            text="📡 Configuración de Alertas (Telegram)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#60A5FA"
        )
        tg_title.pack(anchor="w", padx=18, pady=(15, 10))

        # Telegram Token
        lbl_tok = ctk.CTkLabel(tg_card, text="Telegram Bot Token:", font=ctk.CTkFont(size=12), text_color="#D1D5DB")
        lbl_tok.pack(anchor="w", padx=18, pady=(2, 2))
        self.entry_tg_token = ctk.CTkEntry(
            tg_card,
            placeholder_text="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ",
            show="*",
            height=32
        )
        self.entry_tg_token.pack(fill="x", padx=18, pady=(0, 8))
        self.entry_tg_token.insert(0, self.config.get("telegram", {}).get("bot_token", ""))

        # Telegram Chat ID
        lbl_cid = ctk.CTkLabel(tg_card, text="Telegram Chat ID (Grupo o Privado):", font=ctk.CTkFont(size=12), text_color="#D1D5DB")
        lbl_cid.pack(anchor="w", padx=18, pady=(2, 2))
        self.entry_tg_chat_id = ctk.CTkEntry(
            tg_card,
            placeholder_text="-1001234567890 o @MiCanal",
            height=32
        )
        self.entry_tg_chat_id.pack(fill="x", padx=18, pady=(0, 12))
        self.entry_tg_chat_id.insert(0, self.config.get("telegram", {}).get("chat_id", ""))

        # Botones de Telegram: Test y Guardar
        tg_btn_box = ctk.CTkFrame(tg_card, fg_color="transparent")
        tg_btn_box.pack(fill="x", padx=18, pady=(0, 15))

        self.btn_test_tg = ctk.CTkButton(
            tg_btn_box,
            text="Probar Telegram",
            command=self._on_test_telegram,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            height=32,
            font=ctk.CTkFont(weight="bold")
        )
        self.btn_test_tg.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_save_config = ctk.CTkButton(
            tg_btn_box,
            text="💾 Guardar Configuración",
            command=self._on_save_config_click,
            fg_color="#4F46E5",
            hover_color="#4338CA",
            height=32,
            font=ctk.CTkFont(weight="bold")
        )
        self.btn_save_config.pack(side="right", expand=True, fill="x", padx=(6, 0))

        # ======================================================================
        # SECCIÓN 2: Sniper Wallet (Auto-Buy Preparación)
        # ======================================================================
        sniper_card = ctk.CTkFrame(top_container, fg_color="#1E1E24", corner_radius=12)
        sniper_card.grid(row=0, column=1, padx=(7, 0), sticky="nsew")

        sniper_header = ctk.CTkFrame(sniper_card, fg_color="transparent")
        sniper_header.pack(fill="x", padx=18, pady=(15, 6))

        sniper_title = ctk.CTkLabel(
            sniper_header,
            text="🎯 Sniper Wallet (Preparación)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#F59E0B"
        )
        sniper_title.pack(side="left")

        # Texto descriptivo solicitado
        dev_badge = ctk.CTkLabel(
            sniper_header,
            text="(Función de Auto-Buy en desarrollo)",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="#FBBF24"
        )
        dev_badge.pack(side="right")

        # Private Key con toggle para ocultar/mostrar
        lbl_pk = ctk.CTkLabel(sniper_card, text="Private Key (Monad / EVM):", font=ctk.CTkFont(size=12), text_color="#D1D5DB")
        lbl_pk.pack(anchor="w", padx=18, pady=(2, 2))

        pk_box = ctk.CTkFrame(sniper_card, fg_color="transparent")
        pk_box.pack(fill="x", padx=18, pady=(0, 8))

        self.entry_pk = ctk.CTkEntry(
            pk_box,
            placeholder_text="0x... o clave hexadecimal de 64 caracteres",
            show="*",
            height=32
        )
        self.entry_pk.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.entry_pk.insert(0, self.config.get("sniper", {}).get("private_key", ""))

        self.pk_visible = False
        self.btn_toggle_pk = ctk.CTkButton(
            pk_box,
            text="👁",
            width=36,
            height=32,
            fg_color="#374151",
            hover_color="#4B5563",
            command=self._toggle_pk_visibility
        )
        self.btn_toggle_pk.pack(side="right")

        # RPC URL y Buy Amount en dos columnas internas
        rpc_amount_frame = ctk.CTkFrame(sniper_card, fg_color="transparent")
        rpc_amount_frame.pack(fill="x", padx=18, pady=(0, 15))
        rpc_amount_frame.grid_columnconfigure(0, weight=3)
        rpc_amount_frame.grid_columnconfigure(1, weight=2)

        lbl_rpc = ctk.CTkLabel(rpc_amount_frame, text="RPC URL (Monad):", font=ctk.CTkFont(size=12), text_color="#D1D5DB")
        lbl_rpc.grid(row=0, column=0, sticky="w", padx=(0, 5))

        lbl_amt = ctk.CTkLabel(rpc_amount_frame, text="Buy Amount (MON):", font=ctk.CTkFont(size=12), text_color="#D1D5DB")
        lbl_amt.grid(row=0, column=1, sticky="w", padx=(5, 0))

        self.entry_rpc = ctk.CTkEntry(
            rpc_amount_frame,
            placeholder_text="https://testnet-rpc.monad.xyz",
            height=32
        )
        self.entry_rpc.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(2, 0))
        self.entry_rpc.insert(0, self.config.get("sniper", {}).get("rpc_url", "https://testnet-rpc.monad.xyz"))

        self.entry_amount = ctk.CTkEntry(
            rpc_amount_frame,
            placeholder_text="0.1",
            height=32
        )
        self.entry_amount.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(2, 0))
        self.entry_amount.insert(0, str(self.config.get("sniper", {}).get("buy_amount_mon", "0.1")))

    def _build_control_bar(self):
        """Botones de control gigantes (START / STOP) y acciones secundarias."""
        ctrl_frame = ctk.CTkFrame(self, fg_color="transparent")
        ctrl_frame.pack(fill="x", padx=15, pady=8)

        # Botón verde gigante "START TRACKER"
        self.btn_start = ctk.CTkButton(
            ctrl_frame,
            text="▶  START TRACKER",
            command=self.start_tracker,
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            corner_radius=10
        )
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 8))

        # Botón rojo gigante "STOP"
        self.btn_stop = ctk.CTkButton(
            ctrl_frame,
            text="⏹  STOP",
            command=self.stop_tracker,
            fg_color="#EF4444",
            hover_color="#DC2626",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            corner_radius=10,
            state="disabled"
        )
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=(8, 8))

        # Botón secundario para probar la alarma de sonido
        self.btn_test_alarm = ctk.CTkButton(
            ctrl_frame,
            text="🔔 Probar Alarma",
            command=self._on_test_alarm,
            fg_color="#374151",
            hover_color="#4B5563",
            width=130,
            height=50,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_test_alarm.pack(side="left", padx=(0, 8))

        # Botón para limpiar logs de la consola
        self.btn_clear_logs = ctk.CTkButton(
            ctrl_frame,
            text="🗑 Limpiar",
            command=self._clear_logs,
            fg_color="#27272A",
            hover_color="#3F3F46",
            width=90,
            height=50,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.btn_clear_logs.pack(side="left")

    def _build_console_panel(self):
        """Sección 3: Consola de Logs con CTkTextbox read-only y tags de colores."""
        console_card = ctk.CTkFrame(self, fg_color="#18181B", corner_radius=12)
        console_card.pack(fill="both", expand=True, padx=15, pady=(5, 10))

        # Encabezado de la consola
        console_header = ctk.CTkFrame(console_card, fg_color="transparent")
        console_header.pack(fill="x", padx=16, pady=(10, 6))

        lbl_console = ctk.CTkLabel(
            console_header,
            text="📟 Consola de Monitoreo & Logs en Tiempo Real",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#E4E4E7"
        )
        lbl_console.pack(side="left")

        # Indicador de CAs encontrados
        self.lbl_ca_counter = ctk.CTkLabel(
            console_header,
            text="CAs Detectados: 0",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10B981",
            fg_color="#27272A",
            corner_radius=6,
            padx=10,
            pady=2
        )
        self.lbl_ca_counter.pack(side="right")

        # CTkTextbox grande read-only
        self.log_textbox = ctk.CTkTextbox(
            console_card,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#E4E4E7",
            fg_color="#0F0F12",
            wrap="word",
            corner_radius=8
        )
        self.log_textbox.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Inicialmente en modo disabled para simular read-only
        self.log_textbox.configure(state="disabled")

        # Configuración de tags de color en el widget subyacente de tkinter
        raw_text_widget = self.log_textbox._textbox
        raw_text_widget.tag_config("INFO", foreground="#94A3B8")
        raw_text_widget.tag_config("SUCCESS", foreground="#34D399")
        raw_text_widget.tag_config("WARN", foreground="#FBBF24")
        raw_text_widget.tag_config("ALERT", foreground="#F43F5E", font=("Consolas", 12, "bold"))
        raw_text_widget.tag_config("SIMULACRO", foreground="#A78BFA", font=("Consolas", 12, "bold"))

    def _build_status_bar(self):
        """Barra inferior con estado de objetivos y atajos."""
        status_bar = ctk.CTkFrame(self, fg_color="#121215", height=28, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")

        lbl_targets = ctk.CTkLabel(
            status_bar,
            text="Targets X: @xooxdotfun | @gabfag_ (Simulacro)   •   Web: xoox.fun (CDN Bypass)",
            font=ctk.CTkFont(size=11),
            text_color="#71717A"
        )
        lbl_targets.pack(side="left", padx=15)

        lbl_help = ctk.CTkLabel(
            status_bar,
            text="Auto-Copy Clipboard: ACTIVO   •   Alarma Windows: ACTIVA",
            font=ctk.CTkFont(size=11),
            text_color="#71717A"
        )
        lbl_help.pack(side="right", padx=15)

    # --------------------------------------------------------------------------
    # MÉTODOS DE UI Y LOGGING SEGURO (THREAD-SAFE)
    # --------------------------------------------------------------------------
    def log_to_gui(self, mensaje: str, level: str = "INFO"):
        """
        Función thread-safe requerida que utiliza app.after(0, ...)
        para insertar texto de forma segura desde hilos asíncronos o secundarios.
        """
        self.after(0, self._insert_log_main_thread, mensaje, level)

    def _insert_log_main_thread(self, mensaje: str, level: str):
        """Inserta la línea de log en el CTkTextbox con timestamp y tag de color."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_line = f"[{timestamp}] [{level.upper()}] {mensaje}\n"

        self.log_textbox.configure(state="normal")
        self.log_textbox._textbox.insert("end", formatted_line, level.upper())
        self.log_textbox._textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def _clear_logs(self):
        """Limpia la consola de texto."""
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")
        self.log_to_gui("Consola limpiada.", "INFO")

    def _toggle_pk_visibility(self):
        """Alterna la visualización de la clave privada."""
        if self.pk_visible:
            self.entry_pk.configure(show="*")
            self.btn_toggle_pk.configure(text="👁")
            self.pk_visible = False
        else:
            self.entry_pk.configure(show="")
            self.btn_toggle_pk.configure(text="🔒")
            self.pk_visible = True

    # --------------------------------------------------------------------------
    # GUARDADO Y SINCRONIZACIÓN DE CONFIGURACIÓN
    # --------------------------------------------------------------------------
    def _read_gui_to_config(self) -> dict:
        """Lee los valores actuales de los inputs visuales hacia el diccionario."""
        current_cfg = self.config.copy()
        current_cfg["telegram"]["bot_token"] = self.entry_tg_token.get().strip()
        current_cfg["telegram"]["chat_id"] = self.entry_tg_chat_id.get().strip()
        current_cfg["sniper"]["private_key"] = self.entry_pk.get().strip()
        current_cfg["sniper"]["rpc_url"] = self.entry_rpc.get().strip()
        current_cfg["sniper"]["buy_amount_mon"] = self.entry_amount.get().strip()
        return current_cfg

    def _on_save_config_click(self):
        """Actualiza config.json con los datos de ambas secciones."""
        self.config = self._read_gui_to_config()
        try:
            save_config(self.config)
            self.log_to_gui("💾 Configuración guardada exitosamente en config.json", "SUCCESS")
            messagebox.showinfo("XOOX Tracker", "¡Configuración guardada correctamente!")
        except Exception as e:
            self.log_to_gui(f"❌ Error al guardar config.json: {e}", "WARN")
            messagebox.showerror("Error", f"No se pudo guardar la configuración: {e}")

    def _on_test_alarm(self):
        """Prueba manual de la alarma de Windows."""
        self.log_to_gui("🔔 Probando alarma sonora (5 beeps)...", "INFO")

        def _test_beep():
            if winsound:
                for _ in range(5):
                    winsound.Beep(1200, 400)
                    time.sleep(0.04)

        threading.Thread(target=_test_beep, daemon=True).start()

    def _on_test_telegram(self):
        """Prueba de conexión con el bot de Telegram."""
        cfg = self._read_gui_to_config()
        token = cfg["telegram"]["bot_token"]
        chat_id = cfg["telegram"]["chat_id"]

        if not token or not chat_id:
            messagebox.showwarning("Atención", "Por favor completa el Bot Token y el Chat ID de Telegram antes de probar.")
            return

        self.log_to_gui("📡 Enviando mensaje de prueba a Telegram...", "INFO")

        def _async_test_worker():
            async def _send():
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": "🟢 <b>[XOOX TRACKER TEST]</b>\n¡Conexión establecida exitosamente con el bot de alertas!",
                    "parse_mode": "HTML"
                }
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                self.log_to_gui("✅ Prueba de Telegram exitosa: Mensaje recibido en el canal/grupo.", "SUCCESS")
                            else:
                                txt = await resp.text()
                                self.log_to_gui(f"❌ Telegram respondió error ({resp.status}): {txt}", "WARN")
                    except Exception as ex:
                        self.log_to_gui(f"❌ Error al conectar con Telegram: {ex}", "WARN")

            asyncio.run(_send())

        threading.Thread(target=_async_test_worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # CONTROL DE EJECUCIÓN (START / STOP TRACKER)
    # --------------------------------------------------------------------------
    def start_tracker(self):
        """Lanza el hilo secundario daemon con el bucle asíncrono."""
        if self.tracker_engine and self.tracker_engine.is_running:
            return

        # Sincronizar configuración visual
        self.config = self._read_gui_to_config()
        save_config(self.config)

        # Actualizar interfaz
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_badge.configure(text="● RASTREANDO", text_color="#10B981", fg_color="#064E3B")

        self.log_to_gui("==================================================", "INFO")
        self.log_to_gui("🚀 INICIANDO SESIÓN DE TRACKING EN SEGUNDO PLANO...", "SUCCESS")

        # Instanciar motor
        self.tracker_engine = TrackerEngine(
            config=self.config,
            log_callback=self.log_to_gui,
            on_ca_found_callback=self._on_ca_found_event,
            on_status_change_callback=self._on_engine_status_changed
        )

        # Lanzar hilo en segundo plano (daemon=True para no bloquear la app)
        self.worker_thread = threading.Thread(target=self.tracker_engine.run_main_loop, daemon=True)
        self.worker_thread.start()

    def stop_tracker(self):
        """Detiene de forma limpia el motor asíncrono."""
        if self.tracker_engine and self.tracker_engine.is_running:
            self.tracker_engine.request_stop()
            self.btn_stop.configure(state="disabled")

    def _on_engine_status_changed(self, is_running: bool):
        """Callback thread-safe para sincronizar botones al terminar."""
        def _update():
            if not is_running:
                self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")
                self.status_badge.configure(text="● DETENIDO", text_color="#EF4444", fg_color="#27272A")
        self.after(0, _update)

    def _on_ca_found_event(self, ca: str, source_name: str, is_drill: bool):
        """Actualiza el contador visual de CAs encontrados."""
        def _inc():
            self.ca_found_count += 1
            self.lbl_ca_counter.configure(text=f"CAs Detectados: {self.ca_found_count}")
        self.after(0, _inc)

    def on_closing(self):
        """Maneja el cierre seguro de la ventana principal."""
        if self.tracker_engine and self.tracker_engine.is_running:
            if messagebox.askokcancel("Salir", "El tracker está activo. ¿Deseas detenerlo y cerrar la aplicación?"):
                self.tracker_engine.request_stop()
                self.destroy()
        else:
            self.destroy()


# ==============================================================================
# PUNTO DE ENTRADA PRINCIPAL
# ==============================================================================
def main():
    """Inicialización del ejecutable / script."""
    app = XOOXTrackerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
