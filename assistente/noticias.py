"""Notícias do dia em português (Google News, RSS gratuito, sem chave): principais manchetes, por seção ou por assunto.
O Klaus lê as manchetes como elas são (com a fonte), sem inventar nada."""
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

BASE = "https://news.google.com/rss"
PARAMS = "hl=pt-BR&gl=BR&ceid=BR:pt-419"
UA = {"User-Agent": "Mozilla/5.0 (compatible; KlausAssistente/1.0)"}
SECOES = {"mundo": "WORLD", "internacional": "WORLD", "brasil": "NATION", "nacional": "NATION", "economia": "BUSINESS", "negocios": "BUSINESS",
          "dinheiro": "BUSINESS", "tecnologia": "TECHNOLOGY", "esporte": "SPORTS", "esportes": "SPORTS", "entretenimento": "ENTERTAINMENT",
          "famosos": "ENTERTAINMENT", "ciencia": "SCIENCE", "saude": "HEALTH"}
ORDINAIS = ["Primeiro", "Segundo", "Terceiro", "Quarto", "Quinto", "Sexto", "Sétimo", "Oitavo"]
GENERICOS = {"", "dia", "hoje", "agora", "geral", "principais", "do dia", "de hoje", "mais importantes", "importantes", "atualidade", "mundo todo"}


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def secao_em(texto):
    """"tecnologia" / "economia" / "esportes"... se a fala citar uma seção do jornal (ou None)."""
    t = " " + _norm(texto) + " "
    for nome in sorted(SECOES, key=len, reverse=True):
        if f" {nome} " in t:
            return nome
    return None


def _baixar(url):
    r = requests.get(url, headers=UA, timeout=8)
    r.raise_for_status()
    itens = []
    for it in ET.fromstring(r.content).findall(".//item"):
        titulo = (it.findtext("title") or "").strip()
        fonte = (it.findtext("source") or "").strip()
        if fonte and titulo.endswith(" - " + fonte):
            titulo = titulo[: -len(fonte) - 3].strip()
        try:
            quando = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            quando = None
        if len(titulo.split()) >= 4:                 # descarta "títulos" vazios como "Notícias"
            itens.append({"titulo": titulo.replace("“", '"').replace("”", '"'), "fonte": fonte or "uma fonte", "quando": quando, "link": it.findtext("link") or ""})
    return itens


def _parecido(a, b):
    pa = {p for p in _norm(a).split() if len(p) > 3}
    pb = {p for p in _norm(b).split() if len(p) > 3}
    return bool(pa and pb) and len(pa & pb) / min(len(pa), len(pb)) >= 0.6


def manchetes(tema=None, quantidade=5, horas=36, pular=0):
    """As manchetes mais importantes (na ordem de destaque do Google News), sem repetir o mesmo fato e sem encher de uma fonte só."""
    t = _norm(tema)
    sec = secao_em(t) if t else None
    if t in GENERICOS:
        url = f"{BASE}?{PARAMS}"
    elif sec:
        url = f"{BASE}/headlines/section/topic/{SECOES[sec]}?{PARAMS}"
    else:
        url = f"{BASE}/search?q={quote(tema + ' when:2d')}&{PARAMS}"
    itens = _baixar(url)
    agora = datetime.now(timezone.utc)
    recentes = [i for i in itens if i["quando"] and agora - i["quando"] <= timedelta(hours=horas)] or itens
    escolhidos, por_fonte = [], {}
    for i in recentes:
        if any(_parecido(i["titulo"], e["titulo"]) for e in escolhidos) or por_fonte.get(i["fonte"], 0) >= 2:
            continue
        escolhidos.append(i)
        por_fonte[i["fonte"]] = por_fonte.get(i["fonte"], 0) + 1
    return escolhidos[pular:pular + quantidade]


def frases(itens, primeiro=0):
    """Uma frase por manchete, pronta para falar: "Primeiro: ..., segundo a BBC."."""
    return [f"{ORDINAIS[(primeiro + k) % len(ORDINAIS)]}: {i['titulo'].rstrip('.')}, segundo {i['fonte']}." for k, i in enumerate(itens)]


def fatos(itens):
    """Texto com as manchetes (e há quanto tempo saíram) para dar de base ao modelo."""
    agora = datetime.now(timezone.utc)
    linhas = []
    for n, i in enumerate(itens, 1):
        h = f", há {int((agora - i['quando']).total_seconds() // 3600)} h" if i["quando"] else ""
        linhas.append(f"{n}) {i['titulo']} ({i['fonte']}{h})")
    return "\n".join(linhas)
