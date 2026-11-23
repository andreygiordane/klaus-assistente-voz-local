"""Nomes de pessoas. O reconhecimento de fala escreve "Rafael", "Raphael" ou "Rafaél" para a mesma pessoa:
aqui todas as grafias parecidas viram UMA pessoa cadastrada."""
import difflib
import re
import unicodedata


def _base(n):
    t = unicodedata.normalize("NFD", (n or "").lower())
    return re.sub(r"[^a-z]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def chave(n):
    """Chave fonética simples: Rafael e Raphael dão ambos "rafael"."""
    t = _base(n).replace("y", "i").replace("w", "u").replace("ph", "f")
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"[eiouh]+$", "", t) if len(t) > 3 else t


def resolver(nome, conhecidos, fuzzy=True):
    """O nome cadastrado que corresponde a `nome` (mesma grafia, mesma chave fonética ou muito parecido), ou None."""
    conhecidos = list(conhecidos)
    b = _base(nome)
    if not b:
        return None
    for c in conhecidos:
        if _base(c) == b:
            return c
    k = chave(nome)
    por_chave = [c for c in conhecidos if chave(c) == k]
    if len(por_chave) == 1:
        return por_chave[0]
    if fuzzy:
        m = difflib.get_close_matches(b, [_base(c) for c in conhecidos], n=1, cutoff=0.84)
        if m:
            return next(c for c in conhecidos if _base(c) == m[0])
    return None


def corrigir(texto, conhecidos):
    """Troca, no texto ouvido, as grafias parecidas pelo nome cadastrado ("o Raphael" -> "o Rafael")."""
    conhecidos = list(conhecidos)
    if not conhecidos:
        return texto

    def troca(m):
        return resolver(m.group(0), conhecidos, fuzzy=False) or m.group(0)

    return re.sub(r"[A-ZÀ-Ú][A-Za-zÀ-ÿ]{2,}", troca, texto)
