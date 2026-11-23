"""Lista de tarefas do Klaus (por voz). Fica em dados/tarefas.json."""
import json
import time
import unicodedata

from . import config

ARQ = config.DADOS / "tarefas.json"


def _norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def todas():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:
        return []


def _salvar(ts):
    ARQ.write_text(json.dumps(ts, ensure_ascii=False, indent=1), encoding="utf-8")


def adicionar(texto):
    ts = todas()
    ts.append({"id": int(time.time() * 1000), "texto": texto.strip(), "feito": False})
    _salvar(ts)
    return ts


def marcar(id_ou_texto, feito=True):
    """Marca por id (clique na interface) ou por trecho do texto (voz). Devolve a tarefa marcada ou None."""
    ts = todas()
    alvo = None
    for t in ts:
        if t["id"] == id_ou_texto or (isinstance(id_ou_texto, str) and _norm(id_ou_texto) in _norm(t["texto"])):
            alvo = t
            break
    if alvo:
        alvo["feito"] = feito
        _salvar(ts)
    return alvo


def remover(id_ou_texto):
    ts = todas()
    novo = [t for t in ts if not (t["id"] == id_ou_texto or (isinstance(id_ou_texto, str) and _norm(id_ou_texto) in _norm(t["texto"])))]
    if len(novo) != len(ts):
        _salvar(novo)
        return True
    return False
