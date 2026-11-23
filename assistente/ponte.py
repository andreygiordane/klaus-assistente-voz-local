"""Ponte entre o Klaus (Python) e a interface HUD no navegador. Tudo local (127.0.0.1).

  http://127.0.0.1:8766/klaus_hud.html   a interface
  ws://127.0.0.1:8765                    eventos do Klaus -> interface e comandos da interface -> Klaus
"""
import asyncio
import functools
import http.server
import json
import os
import platform
import subprocess
import threading
import time

import psutil

from . import config

PORTA_WS, PORTA_HTTP = 8765, 8766
PASTA_UI = config.RAIZ / "interface"
NOMES_APPS = {   # executável -> nome amigável
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox", "discord.exe": "Discord",
    "code.exe": "Visual Studio Code", "steam.exe": "Steam", "spotify.exe": "Spotify", "explorer.exe": "Explorador",
    "idea64.exe": "IntelliJ IDEA", "studio64.exe": "Android Studio", "obs64.exe": "OBS Studio",
    "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram", "slack.exe": "Slack", "teams.exe": "Teams",
    "notepad.exe": "Bloco de Notas", "winword.exe": "Word", "excel.exe": "Excel", "vlc.exe": "VLC",
    "epicgameslauncher.exe": "Epic Games", "ollama app.exe": "Ollama", "ollama.exe": "Ollama",
}


def _gpu():
    """(nome, uso %, temperatura °C, VRAM usada MB, VRAM total MB) pela nvidia-smi, ou None."""
    try:
        s = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                           creationflags=0x08000000).stdout.strip().split(",")
        return s[0].strip(), *[float(x) for x in s[1:]]
    except Exception:
        return None


def _cpu_nome():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        return platform.processor()


class Ponte:
    def __init__(self, ao_texto=None, ao_parar=None, ao_limpar=None, ao_ouvir=None, ao_mudo=None, ao_abrir=None,
                 ao_tarefa=None, ao_camera=None, ao_modo=None, info=None, niveis=None, clima=None, tarefas=None):
        self.ao_texto, self.ao_parar, self.ao_limpar, self.ao_ouvir = ao_texto, ao_parar, ao_limpar, ao_ouvir
        self.ao_mudo, self.ao_abrir, self.ao_tarefa = ao_mudo, ao_abrir, ao_tarefa
        self.ao_camera = ao_camera
        self.ao_modo = ao_modo
        self.info = info or (lambda: {})
        self.niveis = niveis or (lambda: (0.0, 0.0))   # (microfone, voz do Klaus), de 0 a 1
        self.clima = clima                             # função que devolve o dicionário do clima
        self.tarefas = tarefas or (lambda: [])
        self.loop = None
        self.conexoes = set()
        self.estado = "LOADING"
        self.ultimo_clima = None
        self.carga = {"pct": 0, "passo": 0, "etapa": "Iniciando..."}   # progresso do carregamento (a janela que abrir depois também vê)

    # ------------------------------------------------------------- início
    def iniciar(self):
        for alvo in (self._rodar_ws, self._rodar_http, self._telemetria, self._apps, self._niveis, self._clima):
            threading.Thread(target=alvo, daemon=True).start()

    def _rodar_http(self):
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(PASTA_UI))
        h.log_message = lambda *a, **k: None
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        try:
            http.server.ThreadingHTTPServer(("127.0.0.1", PORTA_HTTP), h).serve_forever()
        except OSError as e:
            print(f"[ponte] porta {PORTA_HTTP} ocupada (outro Klaus aberto?): {e}")

    def _rodar_ws(self):
        from websockets.asyncio.server import serve

        async def tratar(ws):
            self.conexoes.add(ws)
            await ws.send(json.dumps({"tipo": "estado", "valor": self.estado}))
            await ws.send(json.dumps({"tipo": "carga", **self.carga}, ensure_ascii=False))
            await ws.send(json.dumps({"tipo": "info", **self.info()}, ensure_ascii=False))
            await ws.send(json.dumps({"tipo": "tarefas", "lista": self.tarefas()}, ensure_ascii=False))
            if self.ultimo_clima:
                await ws.send(json.dumps({"tipo": "clima", **self.ultimo_clima}, ensure_ascii=False))
            try:
                async for bruto in ws:
                    try:
                        m = json.loads(bruto)
                    except ValueError:
                        continue
                    threading.Thread(target=self._comando, args=(m,), daemon=True).start()
            finally:
                self.conexoes.discard(ws)

        async def principal():
            async with serve(tratar, "127.0.0.1", PORTA_WS):
                await asyncio.get_running_loop().create_future()

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(principal())
        except OSError as e:
            print(f"[ponte] porta {PORTA_WS} ocupada (outro Klaus aberto?): {e}")

    def _comando(self, m):
        t = m.get("tipo")
        try:
            if t == "texto" and self.ao_texto:
                self.ao_texto(str(m.get("texto", "")).strip())
            elif t == "parar" and self.ao_parar:
                self.ao_parar()
            elif t == "limpar" and self.ao_limpar:
                self.ao_limpar()
            elif t == "ouvir" and self.ao_ouvir:
                self.ao_ouvir()
            elif t == "mudo" and self.ao_mudo:
                self.ao_mudo(bool(m.get("valor")))
            elif t == "abrir" and self.ao_abrir:
                self.ao_abrir(str(m.get("nome", "")))
            elif t == "modo" and self.ao_modo:
                self.ao_modo(str(m.get("valor", "normal")))
            elif t == "camera" and self.ao_camera:
                self.ao_camera(bool(m.get("valor")))
            elif t == "tarefa" and self.ao_tarefa:
                self.ao_tarefa(m.get("id"), bool(m.get("feito")))
        except Exception as e:
            print(f"[ponte] erro no comando {t}: {e}")

    # ------------------------------------------------------------- eventos
    def emitir(self, **ev):
        if not self.loop or not self.conexoes:
            return
        msg = json.dumps(ev, ensure_ascii=False)

        async def enviar():
            for ws in list(self.conexoes):
                try:
                    await ws.send(msg)
                except Exception:
                    self.conexoes.discard(ws)

        asyncio.run_coroutine_threadsafe(enviar(), self.loop)

    def carregando(self, pct, passo, etapa):
        """Progresso de inicialização: pct 0-100, passo (índice da etapa em andamento) e texto da etapa."""
        self.carga = {"pct": pct, "passo": passo, "etapa": etapa}
        self.emitir(tipo="carga", **self.carga)

    def definir_estado(self, valor):
        if valor != self.estado:
            self.estado = valor
            self.emitir(tipo="estado", valor=valor)

    def _niveis(self):
        """Nível do microfone e da voz do Klaus, ~14 vezes por segundo: anima a boca e a onda sonora."""
        while True:
            time.sleep(0.07)
            if self.conexoes:
                v = self.niveis()
                self.emitir(tipo="nivel", mic=round(v[0], 3), voz=round(v[1], 3), zcr=round(v[2], 3) if len(v) > 2 else 0.2)

    def _telemetria(self):
        psutil.cpu_percent(None)
        cpu_nome = _cpu_nome()
        rede = psutil.net_io_counters()
        t_rede = time.time()
        disco = os.environ.get("SystemDrive", "C:") + "\\"
        while True:
            time.sleep(1.0)
            if not self.conexoes:
                continue
            g = _gpu()
            mem = psutil.virtual_memory()
            try:
                d = psutil.disk_usage(disco)
            except Exception:
                d = None
            agora, r = time.time(), psutil.net_io_counters()
            dt = max(agora - t_rede, 0.001)
            down, up = (r.bytes_recv - rede.bytes_recv) * 8 / dt / 1e6, (r.bytes_sent - rede.bytes_sent) * 8 / dt / 1e6
            rede, t_rede = r, agora
            bat = psutil.sensors_battery()
            self.emitir(
                tipo="telemetria", cpu=psutil.cpu_percent(None), cpu_nome=cpu_nome,
                ram=mem.percent, ram_usada=round(mem.used / 2**30, 1), ram_total=round(mem.total / 2**30, 1),
                gpu=g[1] if g else None, gpu_nome=g[0] if g else None, temp=g[2] if g else None,
                vram=round(100 * g[3] / g[4]) if g else None,
                disco=d.percent if d else None, disco_usado=round(d.used / 2**30) if d else None,
                disco_total=round(d.total / 2**30) if d else None,
                down=round(down, 1), up=round(up, 1),
                bateria=round(bat.percent) if bat else None, carregando=bool(bat and bat.power_plugged))

    def _apps(self):
        """Aplicativos em execução (os que mais usam memória), agrupados por programa."""
        while True:
            time.sleep(3.0)
            if not self.conexoes:
                continue
            uso = {}
            for p in psutil.process_iter(["name", "memory_info"]):
                try:
                    nome = (p.info["name"] or "").lower()
                    if nome in NOMES_APPS:
                        uso[NOMES_APPS[nome]] = uso.get(NOMES_APPS[nome], 0) + p.info["memory_info"].rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            top = sorted(uso.items(), key=lambda kv: kv[1], reverse=True)[:5]
            self.emitir(tipo="apps", lista=[{"nome": n, "mb": round(b / 2**20)} for n, b in top])

    def _clima(self):
        while True:
            if self.clima:
                try:
                    self.ultimo_clima = self.clima()
                    if self.ultimo_clima:
                        self.emitir(tipo="clima", **self.ultimo_clima)
                except Exception as e:
                    print(f"[clima] indisponível: {e}")
            time.sleep(600)
