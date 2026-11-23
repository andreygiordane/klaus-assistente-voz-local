"""Ouvido do Klaus: microfone → detecção de fala → Whisper local (faster-whisper)."""
import os
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

TAXA = 16000
QUADRO = 480  # 30 ms


def _preparar_cuda():
    """CUDA/cuDNN para o Whisper. Se o PyTorch estiver instalado (YOLO11), carrega-o PRIMEIRO e deixa o Whisper usar as mesmas
    DLLs dele: duas versões diferentes de cuDNN com o mesmo nome travam o processo ("cudnnGetLibConfig", código 127).
    Sem PyTorch, usa as DLLs dos pacotes nvidia-* do pip."""
    try:
        import torch  # noqa: F401
        return
    except Exception:
        pass
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        d = base / sub / "bin"
        if d.exists():
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


# Frases que o Whisper "inventa" em silêncio/ruído
ALUCINACOES = ("obrigado por assistir", "legendas pela comunidade", "amara.org", "inscreva-se", "tchau tchau",
               "legendado por", "até a próxima")


class Ouvido:
    def __init__(self, modelo="small", microfone=None, prompt="Klaus, assistente. Oi, Klaus. Bom dia, Klaus. Klaus, modo sério. Modo agressivo. Modo Ultron. Modo normal. PowerShell. Prompt de comando. Desligar o computador."):
        _preparar_cuda()
        from faster_whisper import WhisperModel
        try:
            self.whisper = WhisperModel(modelo, device="cuda", compute_type="float16")
            self.dispositivo = "cuda"
        except Exception as e:  # sem CUDA: roda no processador
            print(f"[ouvido] GPU indisponível ({e}); usando CPU")
            self.whisper = WhisperModel(modelo, device="cpu", compute_type="int8")
            self.dispositivo = "cpu"
        self.prompt = self.prompt_base = prompt
        self.nomes, self.clipou, self._ultimo_cb = [], False, time.time()
        self.ocupado = False                         # True enquanto o Klaus está processando um pedido (também vale ouvir "para")
        self.fila = queue.Queue()
        self.mudo = False
        self.fila_int = queue.Queue(maxsize=300)   # áudio captado ENQUANTO o Klaus fala (só para ouvir "parar")
        self.t_fala = 0.0
        self._ao_parar = self._eh_parar = None
        self._trava_whisper = threading.Lock()
        self.nivel = 0.0   # volume do microfone (0 a 1), para a interface
        self.chao = 0.01   # nível de ruído de fundo (RMS), calibrado ao ouvir
        self.microfone = microfone
        self._abrir()
        threading.Thread(target=self._vigia_stream, daemon=True, name="vigia-microfone").start()

    def _abrir(self):
        self._ultimo_cb = time.time()
        self.stream = sd.InputStream(samplerate=TAXA, channels=1, dtype="float32", blocksize=QUADRO,
                                     device=self.microfone, callback=self._callback)
        self.stream.start()

    def _vigia_stream(self):
        """Se o microfone parar de entregar áudio (fone desconectado, Windows suspendeu o dispositivo), reabre sozinho."""
        while True:
            time.sleep(2.0)
            if time.time() - self._ultimo_cb > 3.0:
                print("[ouvido] o microfone parou de entregar áudio; reabrindo")
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                try:
                    self._abrir()
                except Exception as e:
                    print(f"[ouvido] não consegui reabrir o microfone: {e}")

    def definir_nomes(self, nomes):
        """Ensina ao Whisper os nomes das pessoas, para escrever "Rafael" em vez de "Raphael"."""
        self.nomes = [n for n in nomes if n]
        self.prompt = self.prompt_base + (" Nomes das pessoas: " + ", ".join(self.nomes) + "." if self.nomes else "")

    def preparar(self, audio, medir=True):
        """Prepara a fala para o Whisper e para o reconhecimento de voz: tira o desvio de DC e normaliza o volume,
        para que gritar (ou falar baixo) não estrague o reconhecimento. Detecta microfone estourando (clipping)."""
        a = np.asarray(audio, dtype=np.float32)
        if a.size == 0:
            return a
        if medir:
            self.clipou = float(np.mean(np.abs(a) >= 0.97)) > 0.02
        a = a - float(np.mean(a))
        pico = float(np.max(np.abs(a)))
        if pico < 1e-4:
            return a
        return (a * min(0.7 / pico, 8.0)).astype(np.float32)

    def _callback(self, dados, frames, tempo, status):
        self._ultimo_cb = time.time()
        self.nivel = 0.6 * self.nivel + 0.4 * min(1.0, float(np.sqrt(np.mean(dados * dados))) * 8)
        if not self.mudo:
            self.fila.put(dados[:, 0].copy())
        if (self.mudo or self.ocupado) and self._ao_parar is not None:   # falando OU trabalhando: fica de ouvido para "para"/"chega"/"encerrar"
            try:
                self.fila_int.put_nowait(dados[:, 0].copy())
            except queue.Full:
                pass

    def silenciar(self, sim):
        """Enquanto o Klaus fala, o microfone é ignorado (senão ele ouviria a si mesmo)."""
        self.mudo = sim
        if sim:
            self.t_fala = time.time()
        self._limpar_int()
        if not sim:
            time.sleep(0.25)
            self.limpar()

    def _limpar_int(self):
        while not self.fila_int.empty():
            try:
                self.fila_int.get_nowait()
            except queue.Empty:
                break

    def iniciar_interrupcao(self, ao_parar, eh_parar):
        """Continua ouvindo enquanto o Klaus fala e chama `ao_parar()` quando você disser um comando de parada
        (`eh_parar(texto)` decide). Aprende o nível do eco da própria voz e só considera falas mais fortes que ele."""
        self._ao_parar, self._eh_parar = ao_parar, eh_parar
        threading.Thread(target=self._vigia_interrupcao, daemon=True, name="interrupcao").start()

    def _vigia_interrupcao(self):
        """Ouve durante todo o tempo em que o Klaus está falando OU trabalhando e, assim que você diz "para", "chega", "encerrar"...,
        chama ao_parar(). Não espera você terminar a frase: a cada ~0,3 s confere o que já foi dito (como a Alexa)."""
        base, pre, seg, ativo, n_alto, mudos, ult = 0.0, [], [], False, 0, 0, 0
        while True:
            try:
                q = self.fila_int.get(timeout=0.3)
            except queue.Empty:                          # silêncio total: zera tudo
                base, pre, seg, ativo, n_alto, mudos, ult = 0.0, [], [], False, 0, 0, 0
                continue
            if not (self.mudo or self.ocupado) or time.time() < getattr(self, "_muda_ate", 0.0):
                continue                                 # sobrou áudio de antes (ou acabou de parar): descarta
            nivel = float(np.sqrt(np.mean(q * q)))
            if self.mudo and time.time() - self.t_fala < 0.7:    # começo da fala do Klaus: só aprende o nível do eco
                base = 0.7 * base + 0.3 * nivel
                continue
            if not ativo:
                alto = nivel > max(base * 1.8, 0.012)
                if not alto:
                    base = 0.95 * base + 0.05 * nivel
                pre.append(q)
                pre = pre[-8:]
                n_alto = n_alto + 1 if alto else 0
                if n_alto >= 3:
                    ativo, seg, mudos, ult = True, list(pre), 0, len(pre)
                continue
            seg.append(q)
            mudos = 0 if nivel > max(base * 1.4, 0.010) else mudos + 1
            fim = mudos * QUADRO / TAXA >= 0.35 or len(seg) * QUADRO / TAXA >= 3.0
            if fim or len(seg) - ult >= 10:              # a cada ~0,3 s confere o que já foi dito, sem esperar acabar a frase
                ult = len(seg)
                audio = np.concatenate(seg)
                if len(audio) >= TAXA * 0.25:
                    try:
                        texto = self.transcrever_rapido(audio)
                        if texto and self._eh_parar(texto):
                            print(f'[interrupção] "{texto}"')
                            self.limpar()                # a fala do "para" não pode virar um comando depois
                            self._limpar_int()
                            self._muda_ate = time.time() + 1.0     # a mesma fala não dispara duas vezes
                            self._ao_parar()
                            pre, seg, ativo, n_alto, mudos, ult = [], [], False, 0, 0, 0
                            continue
                    except Exception as e:
                        print(f"[interrupção] erro: {e}")
            if fim:
                pre, seg, ativo, n_alto, mudos, ult = [], [], False, 0, 0, 0

    def transcrever_rapido(self, audio):
        """Reconhecimento curto e rápido (sem busca em feixe) para pegar "para", "chega", "encerrar" na hora."""
        a = self.preparar(audio, medir=False)
        with self._trava_whisper:
            segs, _ = self.whisper.transcribe(a, language="pt", beam_size=1, best_of=1, vad_filter=False, condition_on_previous_text=False,
                                              temperature=0.0, without_timestamps=True, no_speech_threshold=0.6)
            texto = " ".join(s.text.strip() for s in segs if s.no_speech_prob <= 0.85).strip()
        return "" if any(x in texto.lower() for x in ALUCINACOES) else texto

    def limpar(self):
        while not self.fila.empty():
            try:
                self.fila.get_nowait()
            except queue.Empty:
                break

    def escutar(self, espera_max=None, silencio_fim=0.85, fala_max=20.0):
        """Bloqueia até ouvir uma fala completa. Devolve o áudio (float32, 16 kHz) ou None se passar `espera_max`."""
        inicio = time.time()
        pre = []                 # quadros recentes antes da fala começar (não perde o começo da palavra)
        fala = []
        falando = False
        mudos = 0
        n_alto = 0
        while True:
            if espera_max is not None and not falando and time.time() - inicio > espera_max:
                return None
            try:
                q = self.fila.get(timeout=0.2)
            except queue.Empty:
                continue
            nivel = float(np.sqrt(np.mean(q * q)))
            alto = nivel > max(self.chao * 3.0, 0.012)
            if not falando:
                self.chao = 0.97 * self.chao + 0.03 * nivel     # acompanha o ruído de fundo
                pre.append(q)
                pre = pre[-10:]
                n_alto = n_alto + 1 if alto else 0
                if n_alto >= 3:
                    falando = True
                    fala = list(pre)
                    mudos = 0
            else:
                fala.append(q)
                mudos = 0 if alto else mudos + 1
                if mudos * QUADRO / TAXA >= silencio_fim or len(fala) * QUADRO / TAXA >= fala_max:
                    audio = np.concatenate(fala)
                    audio = audio[: len(audio) - int(mudos * QUADRO * 0.6)] if mudos else audio
                    return self.preparar(audio)

    def transcrever(self, audio, min_s=0.4):
        """Texto em português do áudio, ou '' se for ruído."""
        if audio is None or len(audio) < TAXA * min_s:
            return ""
        audio = self.preparar(audio, medir=False)
        with self._trava_whisper:                        # a vigia de interrupção e o laço principal compartilham o Whisper
            segs, _ = self.whisper.transcribe(
                audio, language="pt", beam_size=5, vad_filter=True,
                vad_parameters={"threshold": 0.35, "min_silence_duration_ms": 600, "speech_pad_ms": 300},
                condition_on_previous_text=False, initial_prompt=self.prompt, hotwords=(", ".join(self.nomes) or None),
                no_speech_threshold=0.6, temperature=(0.0, 0.2, 0.4))
            partes = []
            for s in segs:
                if s.no_speech_prob > 0.85 and s.avg_logprob < -1.0:
                    continue
                partes.append(s.text.strip())
        texto = " ".join(partes).strip()
        if any(a in texto.lower() for a in ALUCINACOES):
            return ""
        return texto
