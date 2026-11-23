"""Memória do Klaus: fatos importantes guardados em disco (dados/memoria.json), por pessoa ou gerais.
Sobrevive a reinícios e ao PC desligado (gravação atômica com cópia de segurança)."""
import re
import threading
import unicodedata
from datetime import datetime

from . import config

ARQ = config.DADOS / "memoria.json"
MAX_TEXTO = 300
MAX_NO_PROMPT = 30
_trava = threading.Lock()
# o que o Klaus NÃO guarda, mesmo que peçam (segredos)
_SEGREDO = re.compile(r"\b(senha|password|passwd|token|cvv|cartao|cartão|pin|chave (pix|privada)|codigo de seguranca|código de segurança)\b", re.I)


def _norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).split()


def _chave(t):
    return " ".join(_norm(t))


def _ler():
    d = config.ler_json(ARQ, {"fatos": []})
    return d if isinstance(d, dict) and isinstance(d.get("fatos"), list) else {"fatos": []}


def lembrar(texto, quem="geral"):
    """Guarda um fato. Devolve (ok, mensagem). `quem` = nome da pessoa a quem o fato se refere, ou "geral"."""
    texto = re.sub(r"\s+", " ", (texto or "")).strip(" .,;:")
    if len(texto) < 3:
        return False, "Não entendi o que era para guardar."
    if _SEGREDO.search(texto):
        return False, "Isso parece um segredo, tipo senha ou cartão, e eu não guardo esse tipo de coisa por segurança."
    texto = texto[:MAX_TEXTO]
    quem = (quem or "geral").strip() or "geral"
    with _trava:
        d = _ler()
        for f in d["fatos"]:
            if f["quem"].lower() == quem.lower() and _chave(f["texto"]) == _chave(texto):
                return True, "Isso eu já sabia, mas reforcei aqui."
        d["fatos"].append({"quem": quem, "texto": texto, "quando": datetime.now().isoformat(timespec="seconds")})
        config.gravar_json(ARQ, d)
    return True, f"Guardado: {texto}."


def esquecer(trecho, quem=None):
    """Apaga os fatos que contêm as palavras de `trecho`. Devolve quantos foram apagados."""
    palavras = [p for p in _norm(trecho) if len(p) > 2]
    if not palavras:
        return 0
    with _trava:
        d = _ler()
        manter, apagados = [], 0
        for f in d["fatos"]:
            ch = _chave(f["texto"])
            dono_ok = quem is None or f["quem"].lower() in (quem.lower(), "geral")
            if dono_ok and all(p in ch for p in palavras):
                apagados += 1
            else:
                manter.append(f)
        if apagados:
            d["fatos"] = manter
            config.gravar_json(ARQ, d)
    return apagados


def fatos(pessoa=None):
    """Fatos gerais + os da pessoa (se houver). Sem pessoa, só os gerais."""
    out = []
    for f in _ler()["fatos"]:
        if f["quem"].lower() == "geral" or (pessoa and f["quem"].lower() == pessoa.lower()):
            out.append(f)
    return out


def contexto(pessoa=None):
    """Texto para o prompt do modelo: o que ele já sabe sobre a pessoa e sobre o mundo dela."""
    fs = fatos(pessoa)[-MAX_NO_PROMPT:]
    if not fs:
        return ""
    linhas = [(f"{f['texto']}" if f["quem"].lower() == "geral" else f"Sobre {f['quem']}: {f['texto']}") for f in fs]
    return "MEMÓRIA (coisas que você já sabe, guardadas em disco; use naturalmente e sem repetir tudo): " + " | ".join(linhas) + ". "


def resumo_falado(pessoa=None):
    fs = fatos(pessoa)
    if not fs:
        return "Ainda não tenho nada guardado." if not pessoa else f"Ainda não guardei nada sobre você, {pessoa}."
    itens = [f["texto"] for f in fs[-8:]]
    return f"Eu sei {len(fs)} coisa{'s' if len(fs) > 1 else ''}. As mais recentes: " + "; ".join(itens) + "."
