"""Tocador contínuo: UM fluxo de saída sempre aberto, alimentado por um buffer.

Sem `sd.play()` por frase (que abre/fecha o dispositivo e deixa lacunas e estalos). Se a síntese atrasar,
o fluxo toca silêncio e retoma sem clique; só começa a falar quando há um pouco de áudio guardado.
"""
import threading
from collections import deque

import numpy as np
import sounddevice as sd


class Reprodutor:
    def __init__(self, taxa, pre_buffer=0.80):
        self.taxa = taxa
        self.pre = int(pre_buffer * taxa)
        self.fila = deque()
        self.n = 0                  # amostras guardadas
        self.tocando = False
        self.fim = False            # nada mais vem: toca o resto mesmo que seja pouco
        self.trava = threading.Lock()
        self.zcr = 0.2              # chiado do som (0 = vogal grave, 0.5 = 's'/'f'): define a forma da boca
        self.nivel = 0.0            # volume da voz saindo (0 a 1), para animar a boca do rosto
        self.atrasos = 0            # quantas vezes faltou áudio no meio da fala (diagnóstico)
        self.stream = sd.OutputStream(samplerate=taxa, channels=1, dtype="float32", latency="low",
                                      callback=self._cb)
        self.stream.start()

    def _cb(self, saida, frames, tempo, status):
        with self.trava:
            if not self.tocando:
                if self.n >= self.pre or (self.fim and self.n > 0):
                    self.tocando = True
                else:
                    saida.fill(0)
                    self.nivel *= 0.5
                    return
            pos = 0
            while pos < frames and self.fila:
                x = self.fila[0]
                k = min(len(x), frames - pos)
                saida[pos:pos + k, 0] = x[:k]
                if k == len(x):
                    self.fila.popleft()
                else:
                    self.fila[0] = x[k:]
                pos += k
                self.n -= k
            bloco = saida[:, 0]
            self.nivel = 0.5 * self.nivel + 0.5 * min(1.0, float(np.sqrt(np.mean(bloco ** 2))) * 5)
            if self.nivel > 0.05:
                self.zcr = 0.7 * self.zcr + 0.3 * float(np.mean(np.diff(np.signbit(bloco).astype(np.int8)) != 0))
            if pos < frames:
                saida[pos:, 0] = 0
                if self.fim:                 # terminou de verdade
                    self.tocando, self.fim = False, False
                else:                        # a síntese atrasou: espera encher de novo
                    self.tocando = False
                    self.atrasos += 1

    def escrever(self, x):
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if x.size:
            with self.trava:
                self.fila.append(x)
                self.n += x.size
                self.fim = False

    def silencio(self, segundos):
        self.escrever(np.zeros(int(segundos * self.taxa), dtype=np.float32))

    def fim_de_fala(self):
        with self.trava:
            self.fim = True

    def ocioso(self):
        with self.trava:
            return self.n == 0 and not self.tocando

    def parar(self):
        with self.trava:
            self.fila.clear()
            self.n, self.tocando, self.fim = 0, False, False
