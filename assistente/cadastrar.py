"""Cadastro de vozes. Rode:  python -m assistente.cadastrar
Cada pessoa lê algumas frases; só o "mapa" numérico da voz é guardado (não o áudio)."""
import sys
import time

import numpy as np
import sounddevice as sd

from . import config
from .locutor import Locutor, TAXA

FRASES = [
    "Klaus, bom dia. Qual é a previsão do tempo para hoje?",
    "Oi, Klaus. Pesquise as últimas notícias sobre tecnologia para mim.",
    "Hoje eu quero organizar minhas tarefas e abrir alguns programas no computador.",
    "Boa noite, Klaus. Me diga que horas são e que dia é hoje.",
    "O rápido cavalo marrom pulou sobre a cerca, e depois correu pelo campo inteiro.",
    "Klaus, abra a calculadora e depois me lembre de beber água.",
    "Eu gosto de conversar com você enquanto trabalho, é bem prático.",
    "Tudo bem por aí? Vamos começar o dia com uma boa xícara de café.",
]


def gravar(segundos, microfone):
    x = sd.rec(int(segundos * TAXA), samplerate=TAXA, channels=1, dtype="float32", device=microfone)
    sd.wait()
    return x[:, 0]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.carregar()
    nome = input("Nome de quem vai cadastrar a voz (como o Klaus deve falar): ").strip()
    if not nome:
        return
    trat = input(f"Como o Klaus deve tratar {nome}? (ex.: senhor, ou deixe vazio): ").strip()
    if not cfg.get("cidade"):
        cfg["cidade"] = input("Sua cidade (para o clima, ex.: São Paulo): ").strip()
    print(f"\nVou gravar {len(FRASES)} frases de 6 segundos. Fale naturalmente, perto do microfone.\n")
    loc = Locutor()
    audios = []
    for i, f in enumerate(FRASES, 1):
        print(f"[{i}/{len(FRASES)}] Leia: \"{f}\"")
        input("   Enter para gravar... ")
        for n in (3, 2, 1):
            print(f"   {n}...", end="\r")
            time.sleep(0.6)
        print("   GRAVANDO     ")
        a = gravar(6, cfg["microfone"])
        nivel = float(np.sqrt(np.mean(a ** 2)))
        if nivel < 0.005:
            print("   (muito baixo, fale mais perto)")
        audios.append(a)
    loc.cadastrar(nome, audios)
    cfg["pessoas"][nome] = {"tratamento": trat}
    config.salvar(cfg)
    # confere: cada amostra contra o resto
    sims = [float(loc.embedding(a) @ np.mean([loc.embedding(b) for j, b in enumerate(audios) if j != i], axis=0)
                  / (np.linalg.norm(np.mean([loc.embedding(b) for j, b in enumerate(audios) if j != i], axis=0)) + 1e-9))
            for i, a in enumerate(audios)]
    print(f"\nPronto, {nome} cadastrado(a). Consistência da voz: {np.mean(sims):.2f} (acima de 0,6 é bom).")


if __name__ == "__main__":
    main()
