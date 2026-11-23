"""Configuração e dados pessoais (ficam só nesta máquina, em dados/)."""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
DADOS.mkdir(exist_ok=True)
ARQ_CONFIG = DADOS / "config.json"
ARQ_VOZES = DADOS / "vozes.json"
ARQ_ESTADO = DADOS / "estado.json"

PADRAO = {
    "cidade": "",                 # ex.: "São Paulo" (usada no clima)
    "pais": "BR",
    "modelo_llm": "qwen2.5:7b",   # modelo do Ollama
    "whisper": "small",           # tiny | base | small | medium | large-v3
    "microfone": None,            # índice do sounddevice; None = padrão do Windows
    "palavra_de_ativacao": ["klaus"],
    "segundos_de_conversa": 25,   # depois de responder, continua ouvindo sem precisar dizer "Klaus"
    "limiar_voz": 0.55,           # similaridade mínima (0 a 1) para reconhecer uma pessoa
    "voz": "vits",                # "vits" = voz do Klaus do app (rápida, CPU) | "clone" = sua voz clonada (XTTS, mais lenta)
    "objetos_modelo": "modelos/yolo11m.pt",   # YOLO11: "modelos/yolo11m.pt" (preciso) ou "yolo11n.pt" (mais leve; baixa sozinho)
    "objetos_confianca": 0.5,     # limiar mínimo de confiança (0 a 1)
    "objetos_classes": None,      # None = todas; ou uma lista, ex.: ["person", "cell phone", "laptop"]
    "camera_indice": None,        # None = procura sozinho a primeira webcam que funcione
    "camera_continua": True,      # câmera ligada: os olhos do Klaus ficam vendo, seguindo e reconhecendo (False = só quando pedir)
    "auto_cadastro_rosto": False, # DESLIGADO: gravar rostos sozinho já misturou pessoas. Rostos só entram por "cadastre o rosto do <nome>"
    "rosto_limiar": 0.5,          # similaridade mínima (0 a 1) para dizer que um rosto é de alguém cadastrado; abaixo disso é "desconhecido"
    "modelo_visao": "gemma3:4b",  # modelo do Ollama que descreve o ambiente
    "atalhos": {                  # botões da interface (nome -> o que abrir)
        "Explorer": "explorer.exe", "Chrome": "chrome", "Spotify": "spotify:", "Discord": "discord:",
        "Steam": "steam://open/main", "VS Code": "code", "YouTube": "https://www.youtube.com",
    },
    "administradores": [],   # quem pode AUTORIZAR (por voz) ações que alteram o computador
    "pessoas": {},                # nome -> {"tratamento": "senhor"}  (preenchido pelo cadastro)
    "programas": {                # nome falado -> comando (só o que está aqui pode ser aberto)
        "bloco de notas": "notepad.exe",
        "calculadora": "calc.exe",
        "explorador de arquivos": "explorer.exe",
        "navegador": "https://www.google.com",
    },
}


def _bak(arq):
    return arq.with_name(arq.name + ".bak")


def ler_json(arq, padrao):
    """Lê o JSON; se o arquivo estiver corrompido (PC desligado no meio de uma gravação), usa a cópia de segurança."""
    for a in (arq, _bak(arq)):
        try:
            return json.loads(a.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
    return padrao


def gravar_json(arq, obj, **kw):
    """Grava de forma atômica: escreve num temporário, guarda a versão boa anterior como .bak e troca o arquivo."""
    import os
    tmp = arq.with_name(arq.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **kw), encoding="utf-8")
    try:
        json.loads(arq.read_text(encoding="utf-8-sig"))      # só vira backup se o atual estiver íntegro
        import shutil
        shutil.copy2(arq, _bak(arq))
    except Exception:
        pass
    os.replace(tmp, arq)


_ler = ler_json


def carregar():
    cfg = dict(PADRAO)
    cfg.update(_ler(ARQ_CONFIG, {}))
    return cfg


def salvar(cfg):
    gravar_json(ARQ_CONFIG, cfg, indent=2)


def vozes():
    return _ler(ARQ_VOZES, {})


def salvar_vozes(v):
    gravar_json(ARQ_VOZES, v)


def estado():
    return _ler(ARQ_ESTADO, {})


def salvar_estado(e):
    gravar_json(ARQ_ESTADO, e)
