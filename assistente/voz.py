"""Voz do Klaus no PC: texto → fonemas (fonemas.py) → modelo VITS no ONNX Runtime → áudio.

Port do modo "fluido" (naturalProsody) do MotorVoz.kt, que é o usado pelo Klaus no app.
"""
from pathlib import Path

import numpy as np
import onnxruntime as ort

from . import fonemas, guia

TAXA = 22050
PASTA = Path(__file__).resolve().parent.parent / "voz" / "klaus"


class Klaus:
    taxa = TAXA
    serio = False        # modo sério: voz mais grave e contida
    tom = 1.0            # efeito de imitação: >1 voz fina, <1 voz grossa
    ritmo_extra = 1.0    # efeito de imitação: >1 fala mais rápido
    MELODIA = [0, 2, 4, 2, 0, 2, 4, 5, 4, 2, 0, -3, 0, 2, 0]    # semitons: a melodia que as palavras seguem ao cantar

    def stream(self, texto):
        """Mesma interface da voz clonada: devolve o áudio em pedaços (aqui, um só por frase)."""
        a = self.sintetizar(texto)
        if a.size:
            yield a

    def __init__(self, pasta=PASTA, ruido=0.60, ritmo=1.0, ruido_duracao=1.2, velocidade=1.2):
        # valores do enum Narrator.KLAUS no app: noiseScale 0.60, lengthScale 1.0, noiseScaleW 1.2, baseSpeed 1.2
        self.ruido, self.ritmo, self.ruido_duracao, self.velocidade = ruido, ritmo, ruido_duracao, velocidade
        self.sessao = ort.InferenceSession(str(Path(pasta) / "model.onnx"), providers=["CPUExecutionProvider"])
        self.mapa = {}
        for linha in (Path(pasta) / "tokens.txt").read_text(encoding="utf-8").split("\n"):
            linha = linha.rstrip("\r")
            corte = linha.rfind(" ")
            if corte < 1:
                continue
            self.mapa[ord(linha[0])] = int(linha[corte + 1:].strip())
        self.pausa, self.inicio, self.fim = self.mapa[ord("_")], self.mapa[ord("^")], self.mapa[ord("$")]

    def sintetizar(self, texto, velocidade=None):
        vel = (velocidade if velocidade is not None else self.velocidade) * self.ritmo_extra
        a = self._sintetizar(texto, vel)
        if abs(self.tom - 1.0) > 0.01 and a.size > 64:
            a = np.interp(np.linspace(0, len(a) - 1, int(len(a) / self.tom)), np.arange(len(a)), a).astype(np.float32)
        return a

    def cantar(self, letra):
        """Canta: cada palavra é sintetizada e vai para a nota da melodia (reamostragem), a última de cada linha se alonga."""
        import re
        linhas = [l.strip() for l in re.split(r"[\n;]+|(?<=[.!?])\s+", letra) if l.strip()]
        out, i = [], 0
        for linha in linhas:
            palavras = linha.split()
            for j, p in enumerate(palavras):
                a = self.sintetizar(p, 0.95)
                if a.size < 64:
                    continue
                r = 2 ** ((self.MELODIA[i % len(self.MELODIA)] + 1) / 12)
                i += 1
                b = np.interp(np.linspace(0, len(a) - 1, max(int(len(a) / r), 2)), np.arange(len(a)), a).astype(np.float32)
                if j == len(palavras) - 1 and b.size > 2000:
                    k1, k2 = int(len(b) * 0.35), int(len(b) * 0.65)
                    b = np.concatenate([b[:k2], b[k1:k2], b[k1:k2], b[k2:]])   # alonga a vogal final
                out += [b, np.zeros(int(TAXA * 0.03), dtype=np.float32)]
            out.append(np.zeros(int(TAXA * 0.3), dtype=np.float32))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def _sintetizar(self, texto, velocidade=None):
        frases = fonemas.frases(guia.aplicar(texto))
        if not frases:
            return np.zeros(0, dtype=np.float32)
        vel = max(velocidade if velocidade is not None else self.velocidade, 0.1)
        if self.serio:      # fala contida: menos variação de tom, ritmo um pouco mais lento (a reamostragem do tom alonga a duração, por isso 0.88)
            escalas = np.array([0.38, self.ritmo / vel * 0.88 * 1.08, 0.55], dtype=np.float32)
            return _envelope(tom_serio(_cortar_bordas(self._gerar(" ".join(frases), escalas))))
        escalas = np.array([self.ruido, self.ritmo / vel, self.ruido_duracao], dtype=np.float32)
        bruto = self._gerar(" ".join(frases), escalas)
        return _envelope(_cortar_bordas(bruto))

    def _gerar(self, fon, escalas):
        ids = [self.inicio, self.pausa]
        for ch in fon:
            i = self.mapa.get(ord(ch))
            if i is not None:
                ids += [i, self.pausa]
        ids.append(self.fim)
        entrada = np.array([ids], dtype=np.int64)
        saida = self.sessao.run(None, {
            "input": entrada,
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": escalas,
        })[0]
        return np.asarray(saida, dtype=np.float32).reshape(-1)


def tom_serio(x, taxa=TAXA):
    """Voz séria: tom ~12% mais grave (reamostragem), com mais corpo nos graves. A duração é compensada por quem chama."""
    if x.size < 64:
        return x
    fator = 0.88
    y = np.interp(np.linspace(0, len(x) - 1, int(len(x) / fator)), np.arange(len(x)), x).astype(np.float32)
    k = max(3, int(taxa / 320))
    graves = np.convolve(y, np.ones(k, dtype=np.float32) / k, mode="same")
    y = y * 0.82 + graves * 0.55
    pico = float(np.max(np.abs(x))) or 1.0
    return (y * (pico / (float(np.max(np.abs(y))) or 1.0))).astype(np.float32)


def _cortar_bordas(x, limiar=0.0025):
    if x.size == 0:
        return x
    ativo = np.nonzero(np.abs(x) >= limiar)[0]
    if ativo.size == 0:
        return x[:0]
    ini = max(ativo[0] - TAXA * 18 // 1000, 0)
    fim = min(ativo[-1] + TAXA * 75 // 1000, x.size - 1)
    return x[ini:fim + 1].copy()


def _envelope(x):
    if x.size < 64:
        return x
    fade_in = min(12 * TAXA // 1000, x.size // 4)
    fade_out = min(18 * TAXA // 1000, x.size // 4)
    x[:fade_in] *= 0.5 * (1 - np.cos(np.pi * np.arange(fade_in) / fade_in))
    x[x.size - fade_out:] *= 0.5 * (1 + np.cos(np.pi * np.arange(fade_out) / fade_out))
    return x


def falar(klaus, texto, velocidade=None):
    """Fala e espera terminar."""
    import sounddevice as sd
    audio = klaus.sintetizar(texto, velocidade)
    if audio.size:
        sd.play(audio, TAXA)
        sd.wait()


if __name__ == "__main__":
    import sys
    import soundfile as sf

    texto = " ".join(sys.argv[1:]) or "Olá, eu sou o Klaus. Agora eu falo direto no seu computador, sem internet."
    k = Klaus()
    print("fonemas:", fonemas.frases(guia.aplicar(texto)))
    audio = k.sintetizar(texto)
    sf.write(Path(__file__).resolve().parent.parent / "teste-klaus-pc.wav", audio, TAXA)
    print(f"{audio.size / TAXA:.1f}s de áudio salvo em teste-klaus-pc.wav")
    falar(k, texto)
