"""Servidor local de voz clonada (XTTS-v2). Roda no ambiente .venv_tts:  python -m assistente.tts_server
POST /falar {"texto": "..."}  ->  PCM int16 mono 24 kHz em fluxo (toca enquanto ainda gera).  GET /ok  ->  "ok"."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["COQUI_TOS_AGREED"] = "1"   # licença não comercial CPML do XTTS-v2 (uso pessoal)
RAIZ = Path(__file__).resolve().parent.parent
REFS = sorted((RAIZ / "voz" / "clone").glob("ref*.wav"))
PORTA = int(os.environ.get("KLAUS_TTS_PORTA", "5005"))
TAXA = 24000
trava = threading.Lock()
M = {}


def carregar():
    import torch
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    from TTS.utils.manage import ModelManager
    caminho = Path(ModelManager().download_model("tts_models/multilingual/multi-dataset/xtts_v2")[0])
    if caminho.is_file():          # algumas versões devolvem o model.pth, não a pasta
        caminho = caminho.parent
    cfg = XttsConfig()
    cfg.load_json(str(Path(caminho) / "config.json"))
    modelo = Xtts.init_from_config(cfg)
    modelo.load_checkpoint(cfg, checkpoint_dir=str(caminho), eval=True, use_deepspeed=False)
    modelo.cuda()
    with torch.no_grad():           # a voz de referência é calculada em precisão total...
        cond, emb = modelo.get_conditioning_latents(audio_path=[str(r) for r in REFS], gpt_cond_len=30,
                                                    max_ref_length=60)
    if os.environ.get("KLAUS_TTS_FP16", "1") == "1":
        modelo.half()               # ...e só depois o modelo cai para meia precisão (cabe ao lado do LLM e do Whisper)
        cond, emb = cond.half(), emb.half()
    M.update(modelo=modelo, cond=cond, emb=emb, torch=torch)


def gerar(texto):
    torch = M["torch"]
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
        for pedaco in M["modelo"].inference_stream(
                texto, "pt", M["cond"], M["emb"], stream_chunk_size=20, overlap_wav_len=1024,
                temperature=0.6, top_k=40, top_p=0.85, repetition_penalty=5.0, enable_text_splitting=False, speed=1.0):
            yield (pedaco.float().clamp(-1, 1).cpu().numpy() * 32767).astype("<i2").tobytes()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok" if M else b"carregando")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        texto = json.loads(self.rfile.read(n))["texto"]
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        try:
            with trava:
                for b in gerar(texto):
                    self.wfile.write(b)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("[tts] carregando XTTS-v2...", flush=True)
    carregar()
    print("[tts] pronto", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
