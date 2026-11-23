"""Klaus como aplicativo: janela própria (sem navegador), ícone na bandeja e execução em segundo plano.

  pythonw -m assistente.app            abre a janela
  pythonw -m assistente.app --oculto   começa só na bandeja (usado ao iniciar com o Windows)

Fechar a janela (X) só a esconde: o Klaus continua ouvindo. Para encerrar de verdade: ícone da bandeja > Sair.
"""
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from . import config
from .ponte import PORTA_HTTP

RAIZ = config.RAIZ
ICO, PNG = RAIZ / "interface" / "klaus.ico", RAIZ / "interface" / "klaus.png"
URL = f"http://127.0.0.1:{PORTA_HTTP}/klaus_hud.html"
INICIO_WINDOWS = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Klaus.lnk"
PYW = RAIZ / ".venv" / "Scripts" / "pythonw.exe"

SAIR = threading.Event()
KLAUS = {"obj": None, "erro": None}
UI = {"janela": None, "icone": None}

TELA_INICIAL = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#020813;color:#9fdcff;font-family:Segoe UI,sans-serif;
display:grid;place-items:center;height:100vh;text-align:center"><div><div style="font-size:54px;letter-spacing:.2em;color:#fff;
text-shadow:0 0 18px #2fd4ff">KLAUS</div><div style="margin-top:14px;opacity:.8">Iniciando...</div>
<div style="margin:18px auto 0;width:260px;height:4px;background:#0b2a40;border-radius:3px;overflow:hidden"><div style="width:40%;height:100%;background:#2fd4ff;box-shadow:0 0 12px #2fd4ff;
animation:m 1.4s ease-in-out infinite alternate"></div></div></div><style>@keyframes m{from{margin-left:0}to{margin-left:60%}}</style></body>"""


# --------------------------------------------------------------------------- utilidades
def _redirecionar_saida():
    """Sem console (pythonw): tudo que o Klaus escreve vai para dados/klaus.log."""
    if sys.stdout is None or "pythonw" in Path(sys.executable).name.lower():
        if LOG_ARQ.exists() and LOG_ARQ.stat().st_size > 2_000_000:
            LOG_ARQ.write_text("", encoding="utf-8")
        f = open(LOG_ARQ, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    from . import admin as _adm
    print(f"\n===== Klaus iniciado {time.strftime('%d/%m/%Y %H:%M:%S')} | administrador: {_adm.eh_admin()} =====")


LOG_ARQ = config.DADOS / "klaus.log"


def _caixa(texto, titulo="Klaus"):
    try:
        ctypes.windll.user32.MessageBoxW(0, texto, titulo, 0x40)
    except Exception:
        pass


def _instancia_unica():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8767))
    except OSError:
        return None
    return s


def _atalho(destino, argumentos=""):
    """Cria um atalho .lnk do Klaus (ícone próprio, sem janela de console)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{destino}');"
          f"$s.TargetPath='{PYW}';$s.Arguments='-m assistente.app {argumentos}'.Trim();$s.WorkingDirectory='{RAIZ}';"
          f"$s.IconLocation='{ICO}';$s.Description='Klaus - assistente pessoal de voz (administrador)';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=0x08000000)
    try:                                                   # marca o atalho "Executar como administrador" (o Windows pede a confirmação UAC ao abrir)
        dados = bytearray(destino.read_bytes())
        dados[0x15] |= 0x20
        destino.write_bytes(bytes(dados))
    except Exception as e:
        print(f"[app] não consegui marcar o atalho como administrador: {e}")


def _inicia_com_windows():
    return INICIO_WINDOWS.exists()


# ----------------------------------------------------------------------------- backend
def _backend():
    try:
        from .principal import Klaus_
        k = Klaus_()
        KLAUS["obj"] = k
        k.ao_encerrar = lambda: UI["sair"]() if UI.get("sair") else os._exit(0)
        icone = UI.get("icone")
        if icone:
            try:
                icone.notify("Pronto. Diga \"Klaus\" quando quiser.", "Klaus")
            except Exception:
                pass
        k.correr()
    except Exception:
        KLAUS["erro"] = traceback.format_exc()
        print(KLAUS["erro"])


def _encerrar_tudo():
    k = KLAUS["obj"]
    try:
        if k is not None:
            if getattr(k, "visao", None):
                k.visao.fechar()
            proc = getattr(k.voz, "proc", None)       # servidor da voz clonada, se este processo o abriu
            if proc is not None and proc.poll() is None:
                proc.terminate()
    except Exception:
        pass


# -------------------------------------------------------------------------------- janela
def _aguardar_e_carregar():
    janela = UI["janela"]
    t = time.time()
    while time.time() - t < 120 and not SAIR.is_set():
        try:
            if urllib.request.urlopen(URL, timeout=1).status == 200:
                break
        except Exception:
            time.sleep(0.5)
    janela.load_url(URL)
    _icone_da_janela(janela)


def _icone_da_janela(janela):
    """Ícone do Klaus na barra de tarefas e na janela (o padrão seria o do Python)."""
    try:
        import clr  # noqa: F401  (pythonnet, já usado pelo pywebview)
        from System import Action
        from System.Drawing import Icon
        form = janela.native

        def aplicar():
            form.Icon = Icon(str(ICO))
        form.Invoke(Action(aplicar))
    except Exception as e:
        print(f"[app] não consegui trocar o ícone da janela: {e}")


def _mostrar(*_):
    j = UI["janela"]
    if j is not None:
        j.show()
        try:
            j.restore()
        except Exception:
            pass


# --------------------------------------------------------------------------------- bandeja
def _bandeja():
    import pystray
    from PIL import Image

    def klaus():
        return KLAUS["obj"]

    def alternar_voz(icone, item):
        k = klaus()
        if k:
            k.mudar_voz(not k.falante.calado)

    def alternar_camera(icone, item):
        k = klaus()
        if k and k.visao:
            k.ligar_camera(not k.visao.acompanhando)

    def alternar_inicio(icone, item):
        if _inicia_com_windows():
            INICIO_WINDOWS.unlink(missing_ok=True)
        else:
            _atalho(INICIO_WINDOWS, "--oculto")

    def sair(icone, item):
        SAIR.set()
        _encerrar_tudo()
        icone.stop()
        j = UI["janela"]
        if j is not None:
            j.destroy()
        threading.Timer(4, lambda: os._exit(0)).start()

    UI["sair"] = lambda: sair(UI.get("icone") or icone, None)       # "Klaus, encerre" por voz usa o mesmo caminho do menu da bandeja

    menu = pystray.Menu(
        pystray.MenuItem("Abrir Klaus", _mostrar, default=True),
        pystray.MenuItem("Voz do Klaus ligada", alternar_voz, checked=lambda i: bool(klaus()) and not klaus().falante.calado,
                         enabled=lambda i: bool(klaus())),
        pystray.MenuItem("Câmera (acompanhar)", alternar_camera,
                         checked=lambda i: bool(klaus() and klaus().visao and klaus().visao.acompanhando),
                         enabled=lambda i: bool(klaus() and klaus().visao)),
        pystray.MenuItem("Iniciar com o Windows", alternar_inicio, checked=lambda i: _inicia_com_windows()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair do Klaus", sair),
    )
    icone = pystray.Icon("klaus", Image.open(PNG), "Klaus", menu)
    UI["icone"] = icone
    icone.run_detached()


# ----------------------------------------------------------------------------------- main
def main():
    _redirecionar_saida()
    trava = _instancia_unica()
    if trava is None:
        print("[app] já existe um Klaus rodando.")
        _caixa("O Klaus já está rodando. Procure o ícone dele na bandeja do Windows (perto do relógio).")
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klaus.Assistente.1")   # ícone próprio na barra de tarefas
    except Exception:
        pass
    oculto = "--oculto" in sys.argv
    _bandeja()
    threading.Thread(target=_backend, daemon=True).start()

    import webview
    janela = webview.create_window("KLAUS", html=TELA_INICIAL, width=1440, height=880, min_size=(1100, 720),
                                   background_color="#020813", hidden=oculto, text_select=False)
    UI["janela"] = janela

    def ao_fechar():
        if SAIR.is_set():
            return True
        janela.hide()          # o X só esconde: o Klaus continua ouvindo em segundo plano
        return False
    janela.events.closing += ao_fechar
    webview.start(_aguardar_e_carregar, icon=str(ICO))
    # chegou aqui: janela destruída (Sair)
    SAIR.set()
    _encerrar_tudo()
    if UI["icone"]:
        UI["icone"].stop()
    os._exit(0)


if __name__ == "__main__":
    main()
