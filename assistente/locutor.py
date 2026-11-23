"""Reconhece QUEM está falando pela voz (embedding de falante, 100% local, ONNX via sherpa-onnx)."""
import numpy as np

from . import config

MODELO = config.RAIZ / "modelos" / "speaker.onnx"
TAXA = 16000


class Locutor:
    def __init__(self):
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODELO), num_threads=2, provider="cpu")
        self.ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.vozes = {n: [np.array(e, dtype=np.float32) for e in embs] for n, embs in config.vozes().items()}
        self.ultimo_melhor = (None, 0.0)         # a pessoa mais parecida na última fala, mesmo abaixo do limiar (a câmera pode confirmar)

    def embedding(self, audio):
        s = self.ext.create_stream()
        s.accept_waveform(sample_rate=TAXA, waveform=np.asarray(audio, dtype=np.float32))
        s.input_finished()
        e = np.array(self.ext.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def cadastrar(self, nome, audios):
        embs = [self.embedding(a) for a in audios]
        self.vozes[nome] = embs
        config.salvar_vozes({n: [e.tolist() for e in v] for n, v in self.vozes.items()})

    def identificar(self, audio, minimo=0.55):
        """(nome, similaridade) da pessoa cadastrada mais parecida, ou (None, melhor_similaridade)."""
        self.ultimo_melhor = (None, 0.0)
        if not self.vozes or len(audio) < TAXA * 0.6:
            return None, 0.0
        e = self.embedding(audio)
        placar = {}
        for nome, embs in self.vozes.items():
            sims = sorted((float(e @ x) for x in embs), reverse=True)
            placar[nome] = float(np.mean(sims[:3]))   # média das 3 melhores amostras
        ordem = sorted(placar.items(), key=lambda kv: kv[1], reverse=True)
        melhor, sim = ordem[0]
        self.ultimo_melhor = (melhor, sim)
        folga = sim - ordem[1][1] if len(ordem) > 1 else 1.0
        if sim >= minimo and folga >= 0.04:
            return melhor, sim
        return None, sim
