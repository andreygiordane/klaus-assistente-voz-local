"""Guia de pronúncia: troca a grafia de termos estrangeiros/técnicos pela grafia FALADA antes de virar fonemas.

Port do GuiaPronuncia.kt do app Lê+. Lê todos os .tsv de assistente/pronuncias/ (escrito, falado, tipo):
  M = só vale com inicial maiúscula (nomes, marcas)    T = qualquer caixa, menos MAIÚSCULAS (termos do app)
  A = qualquer caixa, inclusive MAIÚSCULAS             S = só em MAIÚSCULAS ("GPU"); aceita plural ("GPUs")
  E = caixa exata ("GHz")
Nomes de várias palavras têm prioridade sobre palavras soltas.
"""
import re
from pathlib import Path

PASTA = Path(__file__).resolve().parent / "pronuncias"
# palavra = começa com letra; depois letras, dígitos, apóstrofo ou hífen ("s3", "wi-fi", "d'água")
_RE_PALAVRA = re.compile(r"[^\W\d_](?:[^\W_]|['’\-])*")

# Escritas que não são "palavras" para o tokenizador
_PRE = [
    (re.compile(r"\bC\+\+", re.I), "cê plus plus"),
    (re.compile(r"\bC#"), "cê chárpi"),
    (re.compile(r"\bF#"), "éfe chárpi"),
    (re.compile(r"\.NET\b", re.I), "dóti nétchi"),
    (re.compile(r"(?<=\w)\.js\b", re.I), " djêi ésse"),
    (re.compile(r"\b(\d+)\s?K\b"), r"\1 cá"),
    (re.compile(r"\b(\d)\s?G\b"), r"\1 gê"),
    (re.compile(r"\b(\d)\s?D\b"), r"\1 dê"),
    (re.compile(r"\bUX\s?/\s?UI\b", re.I), "iú équis iú ái"),
    (re.compile(r"\bTL;DR\b", re.I), "ti éle dê érre"),
]


def _carregar():
    entradas, exatas = {}, {}
    for arq in sorted(PASTA.glob("*.tsv")):
        for linha in arq.read_text(encoding="utf-8").split("\n"):
            linha = linha.rstrip("\r")
            if not linha or linha.startswith("#"):
                continue
            p = linha.split("\t")
            if len(p) >= 3 and p[0] and p[1]:
                (exatas if p[2] == "E" else entradas)[p[0]] = (p[1], p[2])
    return entradas, exatas


_ENTRADAS, _EXATAS = _carregar()
_MAIOR = max((k.count(" ") + 1 for k in _ENTRADAS), default=1)


def _tudo_maiuscula(s):
    return len(s) > 1 and any(c.isalpha() for c in s) and all((not c.isalpha()) or c.isupper() for c in s)


def aplicar(texto):
    """Devolve o texto com os termos do guia trocados pela grafia falada."""
    for padrao, troca in _PRE:
        texto = padrao.sub(troca, texto)
    palavras = list(_RE_PALAVRA.finditer(texto))
    saida = []
    ultimo = 0
    i = 0
    while i < len(palavras):
        achou = False
        for n in range(min(_MAIOR, len(palavras) - i), 0, -1):
            if n > 1 and any(
                    any(not c.isspace() for c in texto[palavras[i + k - 1].end():palavras[i + k].start()])
                    for k in range(1, n)):
                continue
            primeira = palavras[i].group(0)
            chave = " ".join(palavras[i + k].group(0).lower().replace("’", "'") for k in range(n))
            falado = tipo = None
            if n == 1 and primeira in _EXATAS:
                falado, tipo = _EXATAS[primeira]
                tipo = "A"
            else:
                e = _ENTRADAS.get(chave)
                plural = False
                if e is None and n == 1 and len(chave) > 2 and chave.endswith("s") and primeira.endswith("s") \
                        and _tudo_maiuscula(primeira[:-1]):
                    e = _ENTRADAS.get(chave[:-1])
                    plural = e is not None and e[1] in ("S", "A")
                    if not plural:
                        e = None
                if e is None:
                    continue
                falado, tipo = e
                if plural:
                    falado += "s"
                elif tipo == "S":
                    if not _tudo_maiuscula(primeira):
                        continue
                elif tipo == "M" and not primeira[0].isupper():
                    continue
                elif tipo in ("T", "M") and n == 1 and _tudo_maiuscula(primeira):
                    continue  # palavra gritada: lida como está
            if tipo in ("T", "M"):
                partes = falado.split(" ")
                nova = []
                for k, f in enumerate(partes):
                    escrita = palavras[i + min(k, n - 1)].group(0)
                    nova.append(f[:1].upper() + f[1:] if escrita[0].isupper() else f)
                falado = " ".join(nova)
            saida.append(texto[ultimo:palavras[i].start()])
            saida.append(falado)
            ultimo = palavras[i + n - 1].end()
            i += n
            achou = True
            break
        if not achou:
            i += 1
    saida.append(texto[ultimo:])
    return "".join(saida)
