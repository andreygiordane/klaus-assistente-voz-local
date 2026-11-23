"""Fala contínua: sintetiza em fluxo e toca sem lacunas, para a voz sair fluida e o Klaus começar logo."""
import queue
import re
import threading
import time

from .reprodutor import Reprodutor

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF‍️]")


def limpar_para_fala(t):
    """Tira o que não se fala (markdown, links, emojis) e escreve símbolos por extenso."""
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\b[kK]{3,}\b|\b(?:[rR][sS]){2,}\b|\b[hH][aA](?:[hH][aA]){1,}[hH]?\b", "rá rá rá", t)
    t = _EMOJI.sub("", t)
    t = re.sub(r"[*_`#>~|]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"(\d+)\s*°\s*C\b", r"\1 graus", t)
    t = t.replace("°", " graus")
    t = re.sub(r"R\$\s*(\d[\d.]*)(?:,(\d+))?", lambda m: m.group(1).replace(".", "") + (" vírgula " + m.group(2) if m.group(2) else "") + " reais", t)
    t = re.sub(r"(\d+)\s*%", r"\1 por cento", t)
    t = re.sub(r"(\d+)\s*km/h", r"\1 quilômetros por hora", t)
    t = re.sub(r"(\d+),(\d+)", r"\1 vírgula \2", t)
    t = re.sub(r"(\d+)\.(\d{1,2})\b(?!\d)", r"\1 vírgula \2", t)
    t = re.sub(r"(\d{1,3})\.(\d{3})\b", r"\1\2", t)
    t = re.sub(r"\(([^)]*)\)", r", \1,", t)
    return re.sub(r"\s+", " ", t).strip()


class Falante:
    def __init__(self, voz, ouvido=None, ao_mudar=None):
        """`voz` precisa de `.taxa` e `.stream(texto)` (gera pedaços de áudio). `ao_mudar(falando)` avisa a interface."""
        self.voz, self.ouvido, self.ao_mudar = voz, ouvido, ao_mudar
        self.repro = Reprodutor(voz.taxa)
        self.textos = queue.Queue()
        self.pendentes = 0
        self.mudo = False
        self.geracao = 0           # sobe a cada parar(): descarta o que ainda estava sendo sintetizado
        self.calado = False        # voz desligada pela interface
        self.trava = threading.Condition()
        threading.Thread(target=self._sintetizar, daemon=True).start()
        threading.Thread(target=self._vigia, daemon=True).start()

    def dizer(self, texto):
        texto = limpar_para_fala(texto)
        if self.calado or not re.search(r"\w", texto):
            return
        self._enfileirar(texto)

    def cantar(self, letra):
        """Canta a letra (só a voz do Klaus no PC canta; as outras apenas falam a letra)."""
        letra = limpar_para_fala(letra)[:600]
        if self.calado or not re.search(r"\w", letra):
            return
        self._enfileirar(("canto", letra) if hasattr(self.voz, "cantar") else letra)

    def _enfileirar(self, item):
        with self.trava:
            self.pendentes += 1
            if not self.mudo:
                self.mudo = True
                if self.ouvido:
                    self.ouvido.silenciar(True)
                if self.ao_mudar:
                    self.ao_mudar(True)
        self.textos.put(item)

    def _sintetizar(self):
        while True:
            texto = self.textos.get()
            g = self.geracao
            try:
                for pedaco in ([self.voz.cantar(texto[1])] if isinstance(texto, tuple) else self.voz.stream(texto)):
                    if g != self.geracao:           # foi interrompido: joga fora o resto
                        break
                    self.repro.escrever(pedaco)
                if g == self.geracao:
                    self.repro.silencio(0.12)       # respiro entre frases
            except Exception as e:
                print(f"[fala] erro ao sintetizar: {e}")
            finally:
                with self.trava:
                    self.pendentes -= 1
                    if self.pendentes == 0:
                        self.repro.fim_de_fala()    # acabou: toca o que restou
                    self.trava.notify_all()

    def _vigia(self):
        """Quando tudo terminou de tocar, devolve o microfone."""
        while True:
            time.sleep(0.05)
            with self.trava:
                livre = self.pendentes == 0 and self.mudo
            if livre and self.repro.ocioso():
                with self.trava:
                    if self.pendentes == 0 and self.mudo:
                        self.mudo = False
                        if self.ouvido:
                            self.ouvido.silenciar(False)
                        if self.ao_mudar:
                            self.ao_mudar(False)
                        self.trava.notify_all()

    def esperar(self):
        with self.trava:
            while self.pendentes > 0 or self.mudo:
                self.trava.wait(timeout=0.3)

    def parar(self):
        self.geracao += 1
        while not self.textos.empty():
            try:
                self.textos.get_nowait()
                with self.trava:
                    self.pendentes -= 1
            except queue.Empty:
                break
        self.repro.parar()


class Frases:
    """Junta pedaços de texto (streaming do LLM) e entrega frases COMPLETAS e não muito curtas
    (frases maiores têm entonação mais contínua e a voz sai mais fluida)."""

    MINIMOS = (28, 45)          # (primeira frase, demais) em caracteres; o principal ajusta conforme a voz

    def __init__(self, ao_completar):
        self.buf, self.ao_completar, self.primeira = "", ao_completar, True

    def add(self, pedaco):
        self.buf += pedaco
        while True:
            minimo = self.MINIMOS[0] if self.primeira else self.MINIMOS[1]
            m = None
            # com voz na CPU a primeira fala também quebra na vírgula (sai mais cedo); as demais só no fim da frase
            padrao = r"[.!?…;:,\n]+(?=\s|$)" if (self.primeira and self.MINIMOS[0] < 28) else r"[.!?…;\n]+(?=\s|$)"
            for x in re.finditer(padrao, self.buf):
                if x.end() >= minimo:
                    m = x
                    break
            if not m:
                return
            frase, self.buf = self.buf[:m.end()], self.buf[m.end():]
            self.primeira = False
            self.ao_completar(frase.strip())

    def fim(self):
        if self.buf.strip():
            self.ao_completar(self.buf.strip())
        self.buf = ""
