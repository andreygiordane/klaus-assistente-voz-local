"""Extrai as listas de palavras do FonemasPt.kt (app Lê+) para assistente/dados_fonemas.py."""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
txt = SRC.read_text(encoding="utf-8")


def corpo(nome):
    m = re.search(r"val\s+" + nome + r"\s*=\s*(?:setOf|listOf|mapOf|arrayOf)\(", txt)
    i = m.end()
    nivel = 1
    j = i
    em_str = False
    while nivel:
        c = txt[j]
        if c == '"' and txt[j - 1] != "\\":
            em_str = not em_str
        elif not em_str:
            if c == "(":
                nivel += 1
            elif c == ")":
                nivel -= 1
        j += 1
    bloco = txt[i : j - 1]
    # remove comentários // fora de strings (as linhas de comentário começam com //)
    return "\n".join(l for l in bloco.splitlines() if not l.strip().startswith("//"))


def strings(nome):
    return re.findall(r'"([^"]*)"', corpo(nome))


def pares(nome):
    return re.findall(r'"([^"]*)"\s+to\s+"([^"]*)"', corpo(nome))


listas = ["atonas", "encliticos", "sujeitos", "conjuncoesPausa", "palavrasEnfase", "algarismos",
          "xComoKs", "xComoZ", "abertas", "verbosAbertos", "fechadas"]
mapas = ["abreviacoes", "consoantesDuplasArcaicas", "grafiasMudas", "quGuPronunciados"]

with OUT.open("w", encoding="utf-8") as f:
    f.write("# Gerado por tools/extrair_dados.py a partir de FonemasPt.kt. Não edite à mão.\n")
    for n in listas:
        f.write(f"{n.upper()} = {strings(n)!r}\n")
    for n in mapas:
        f.write(f"{n.upper()} = {pares(n)!r}\n")
print("ok")
