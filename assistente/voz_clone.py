"""Cliente da voz clonada (XTTS-v2) + escolha da voz do Klaus."""
import subprocess
import time

import numpy as np
import requests

from . import config, fonemas, guia

PORTA = 5005
URL = f"http://127.0.0.1:{PORTA}"
PY_TTS = config.RAIZ / ".venv_tts" / "Scripts" / "python.exe"


def preparar_texto(texto):
    """Mesmo dicionário de pronúncia do Klaus + números e abreviações por extenso."""
    t = fonemas._pre_processar(guia.aplicar(texto))
    t = " ".join(t.split())
    if t and t[-1] not in ".!?…":
        t += "."
    return t


class KlausClone:
    taxa = 24000
    serio = False        # modo sério: voz mais grave

    def __init__(self, esperar=120):
        if not self._vivo():
            self._iniciar(esperar)

    @staticmethod
    def _vivo():
        try:
            return requests.get(URL + "/ok", timeout=1.5).text == "ok"
        except requests.exceptions.RequestException:
            return False

    def _iniciar(self, esperar):
        import atexit
        log = open(config.DADOS / "tts_server.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen([str(PY_TTS), "-X", "utf8", "-m", "assistente.tts_server"], cwd=str(config.RAIZ),
                                     creationflags=0x08000000, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        atexit.register(lambda: self.proc.poll() is None and self.proc.terminate())
        t = time.time()
        while time.time() - t < esperar:
            if self._vivo():
                return
            if self.proc.poll() is not None:     # o servidor caiu: não adianta esperar
                fim = (config.DADOS / "tts_server.log").read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1:]
                raise RuntimeError(f"o servidor da voz clonada caiu: {fim}")
            time.sleep(1)
        raise RuntimeError("o servidor da voz clonada demorou demais (veja dados/tts_server.log)")

    def stream(self, texto):
        """Gera floats (24 kHz) em pedaços, ainda durante a síntese."""
        r = requests.post(URL + "/falar", json={"texto": preparar_texto(texto)}, stream=True, timeout=120)
        r.raise_for_status()
        resto = b""
        for b in r.iter_content(chunk_size=4096):
            b = resto + b
            n = len(b) - (len(b) % 2)
            resto = b[n:]
            if n:
                pedaco = np.frombuffer(b[:n], dtype="<i2").astype(np.float32) / 32768.0
                if self.serio:
                    from .voz import tom_serio
                    pedaco = tom_serio(pedaco, self.taxa)
                yield pedaco

    def sintetizar(self, texto, velocidade=None):
        partes = list(self.stream(texto))
        return np.concatenate(partes) if partes else np.zeros(0, dtype=np.float32)


def criar_voz(cfg):
    """Voz do Klaus conforme a configuração: "clone" (XTTS) ou "vits" (modelo treinado do app)."""
    if cfg.get("voz", "vits") == "clone" and PY_TTS.exists() and any((config.RAIZ / "voz" / "clone").glob("ref*.wav")):
        try:
            return KlausClone()
        except Exception as e:
            print(f"[voz] clone indisponível ({e}); usando a voz VITS")
    from .voz import Klaus
    return Klaus()
