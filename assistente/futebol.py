"""Futebol com dados de verdade (API pública da ESPN, sem chave) + notícias do time (Google News):
posição na tabela, últimos resultados, próximo jogo e o que está sendo noticiado."""
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from . import noticias

UA = {"User-Agent": "Mozilla/5.0 (compatible; KlausAssistente/1.0)"}
SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
V2 = "https://site.api.espn.com/apis/v2/sports/soccer"
BRT = timezone(timedelta(hours=-3))
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

# nome oficial -> (liga da ESPN, apelidos). Só os apelidos "ambíguos" exigem que a frase fale de futebol (jogo, time, tabela...).
TIMES = {
    "Corinthians": ("bra.1", ["corinthians", "timao", "coringao"]),
    "Flamengo": ("bra.1", ["flamengo", "mengao", "fla", "rubro negro"]),
    "Palmeiras": ("bra.1", ["palmeiras", "verdao", "porco"]),
    "Santos": ("bra.1", ["santos", "peixe", "santos fc"]),
    "São Paulo": ("bra.1", ["sao paulo", "tricolor paulista", "spfc"]),
    "Atlético-MG": ("bra.1", ["atletico mineiro", "atletico mg", "galo"]),
    "Cruzeiro": ("bra.1", ["cruzeiro", "raposa"]),
    "Vasco": ("bra.1", ["vasco", "vasco da gama"]),
    "Botafogo": ("bra.1", ["botafogo", "fogao", "glorioso"]),
    "Fluminense": ("bra.1", ["fluminense", "flu", "tricolor carioca"]),
    "Grêmio": ("bra.1", ["gremio"]),
    "Internacional": ("bra.1", ["internacional", "colorado", "inter de porto alegre"]),
    "Bahia": ("bra.1", ["bahia"]),
    "Fortaleza": ("bra.1", ["fortaleza"]),
    "Ceará": ("bra.1", ["ceara"]),
    "Sport": ("bra.1", ["sport recife", "sport"]),
    "Vitória": ("bra.1", ["vitoria"]),
    "Red Bull Bragantino": ("bra.1", ["bragantino", "massa bruta"]),
    "Juventude": ("bra.1", ["juventude"]),
    "Mirassol": ("bra.1", ["mirassol"]),
    "Athletico-PR": ("bra.1", ["athletico", "athletico paranaense", "furacao"]),
    "Coritiba": ("bra.1", ["coritiba", "coxa"]),
    "Chapecoense": ("bra.1", ["chapecoense", "chape"]),
    "Remo": ("bra.1", ["remo"]),
    "Real Madrid": ("esp.1", ["real madrid"]), "Barcelona": ("esp.1", ["barcelona", "barca"]), "Atlético Madrid": ("esp.1", ["atletico de madrid", "atletico madrid"]),
    "Manchester City": ("eng.1", ["manchester city"]), "Manchester United": ("eng.1", ["manchester united"]), "Liverpool": ("eng.1", ["liverpool"]),
    "Arsenal": ("eng.1", ["arsenal"]), "Chelsea": ("eng.1", ["chelsea"]),
    "Bayern Munich": ("ger.1", ["bayern", "bayern de munique"]), "Borussia Dortmund": ("ger.1", ["dortmund", "borussia dortmund"]),
    "Paris Saint-Germain": ("fra.1", ["psg", "paris saint germain"]),
    "Juventus": ("ita.1", ["juventus", "juve"]), "AC Milan": ("ita.1", ["milan", "ac milan"]), "Inter Milan": ("ita.1", ["inter de milao"]), "Napoli": ("ita.1", ["napoli"]),
}
AMBIGUOS = {"santos", "sport", "vitoria", "bahia", "ceara", "fortaleza", "cruzeiro", "remo", "fla", "flu", "galo", "porco", "peixe", "raposa", "chape", "coxa", "milan", "juve"}
# palavras que mostram que a pergunta é sobre futebol / sobre o time
CUES = (r" (situacao|como (esta|anda|ta|foi|vai|esta indo)|noticias?|novidades?|jogo|jogou|joga|jogam|resultado|placar|tabela|posicao|classificacao|colocacao|"
        r"pontos|ultimo|proximo|rodada|elenco|tecnico|treinador|contrat\w+|lesao|lesionad\w+|escalacao|gols?|ganhou|perdeu|empatou|vai jogar|quando|rebaix\w+|"
        r"libertadores|brasileirao|momento|fase|time|futebol|campeonato|sumula|desfalques?) ")
CUE_FORTE = r" (jogo|jogos|joga|jogou|jogam|time|tabela|brasileirao|futebol|campeonato|rodada|tecnico|gol|gols|classificacao|elenco|libertadores) "
_ALIAS = sorted(((a, nome) for nome, (_, apelidos) in TIMES.items() for a in apelidos), key=lambda x: -len(x[0]))
_cache = {}


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def time_em(texto):
    """O nome oficial do time citado na fala (ou None). Apelidos ambíguos ("santos", "sport"...) só valem se a frase falar de futebol."""
    t = " " + re.sub(r"\s+", " ", _norm(texto)) + " "
    for alias, nome in _ALIAS:
        if f" {alias} " in t:
            if alias in AMBIGUOS and not re.search(CUE_FORTE, t):
                continue
            if alias == "inter" or (alias == "internacional" and " milao " in t):
                continue
            return nome
    if " inter " in t and " milao " not in t and re.search(CUE_FORTE, t):
        return "Internacional"
    return None


def _json(url, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=8)
    r.raise_for_status()
    return r.json()


def _times_da_liga(liga):
    c = _cache.get(("times", liga))
    if c and time.time() - c[0] < 43200:
        return c[1]
    j = _json(f"{SITE}/{liga}/teams")
    times = [t["team"] for t in j["sports"][0]["leagues"][0]["teams"]]
    _cache[("times", liga)] = (time.time(), times)
    return times


def achar_time(nome):
    """{id, nome, liga} do time na ESPN, ou None."""
    liga = TIMES.get(nome, ("bra.1", []))[0]
    alvo = _norm(nome).strip()
    for t in _times_da_liga(liga):
        n = _norm(t["displayName"]).strip()
        if n == alvo or alvo in n or n in alvo:
            return {"id": t["id"], "nome": t["displayName"], "liga": liga}
    return None


def _tabela(liga):
    c = _cache.get(("tabela", liga))
    if c and time.time() - c[0] < 300:
        return c[1]
    j = _json(f"{V2}/{liga}/standings")
    linhas = []
    for e in j["children"][0]["standings"]["entries"]:
        st = {s["name"]: s.get("displayValue", "") for s in e["stats"]}
        linhas.append({"time": e["team"]["displayName"], "id": e["team"]["id"], "pos": int(st.get("rank") or 0), "pts": int(st.get("points") or 0),
                       "jogos": int(st.get("gamesPlayed") or 0), "v": int(st.get("wins") or 0), "e": int(st.get("ties") or 0), "d": int(st.get("losses") or 0),
                       "saldo": st.get("pointDifferential", "")})
    linhas.sort(key=lambda x: x["pos"])
    _cache[("tabela", liga)] = (time.time(), linhas)
    return linhas


def _data(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BRT)


def _quando(d):
    hoje = datetime.now(BRT).date()
    if d.date() == hoje:
        dia = "hoje"
    elif d.date() == hoje + timedelta(days=1):
        dia = "amanhã"
    else:
        dia = f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]}"
    return f"{dia}, às {d.hour} horas" + (f" e {d.minute} minutos" if d.minute else "")


def _jogos(liga, tid, nome_time):
    j = _json(f"{SITE}/{liga}/teams/{tid}/schedule")
    feitos, proximos = [], []
    for ev in sorted(j.get("events", []), key=lambda e: e["date"]):
        comp = ev["competitions"][0]
        meu = next(c for c in comp["competitors"] if str(c["team"]["id"]) == str(tid))
        rival = next(c for c in comp["competitors"] if c is not meu)
        d = _data(ev["date"])
        base = {"rival": rival["team"]["displayName"], "casa": meu.get("homeAway") == "home", "data": d}
        if comp["status"]["type"]["completed"]:
            def pt(c):
                s = c.get("score")
                return int(float(s["value"] if isinstance(s, dict) else s))
            gm, gr = pt(meu), pt(rival)
            feitos.append({**base, "gm": gm, "gr": gr, "res": "V" if gm > gr else "D" if gm < gr else "E"})
        elif d > datetime.now(BRT) - timedelta(hours=3):
            proximos.append(base)
    if not proximos:                         # o calendário só traz o que já foi jogado: o próximo jogo vem no cadastro do time
        try:
            for ev in _json(f"{SITE}/{liga}/teams/{tid}")["team"].get("nextEvent") or []:
                comp = ev["competitions"][0]
                meu = next(c for c in comp["competitors"] if str(c["team"]["id"]) == str(tid))
                rival = next(c for c in comp["competitors"] if c is not meu)
                proximos.append({"rival": rival["team"]["displayName"], "casa": meu.get("homeAway") == "home", "data": _data(ev["date"])})
        except Exception as e:
            print(f"[futebol] próximo jogo: {e}")
    return feitos, proximos


def situacao(nome):
    """Dados do time: tabela, últimos 5 jogos, próximo jogo e manchetes. Devolve dict (ou None se a ESPN não o conhecer)."""
    t = achar_time(nome)
    if not t:
        return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_tab = pool.submit(_tabela, t["liga"]) if t["liga"] in ("bra.1", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1") else None
        f_jog = pool.submit(_jogos, t["liga"], t["id"], t["nome"])
        f_not = pool.submit(noticias.manchetes, nome, 3, 48)
        d = {"time": t["nome"], "liga": t["liga"], "tabela": None, "feitos": [], "proximos": [], "noticias": []}
        for chave, f in (("tabela", f_tab), ("jogos", f_jog), ("noticias", f_not)):
            try:
                r = f.result() if f else None
            except Exception as e:
                print(f"[futebol] {chave}: {e}")
                continue
            if chave == "tabela" and r:
                d["classificacao"] = r
                d["tabela"] = next((x for x in r if str(x["id"]) == str(t["id"])), None)
            elif chave == "jogos" and r:
                d["feitos"], d["proximos"] = r
            elif chave == "noticias":
                d["noticias"] = r or []
    return d


def fatos(d):
    """Os fatos do time em texto, para o modelo narrar (e para ler direto se o modelo falhar)."""
    linhas = [f"TIME: {d['time']}."]
    tb = d.get("tabela")
    if tb:
        liga = "Brasileirão" if d["liga"] == "bra.1" else "campeonato nacional"
        resto = {"bra.1": 38}.get(d["liga"])
        txt = f"TABELA ({liga}): {tb['pos']}º lugar, {tb['pts']} pontos em {tb['jogos']} jogos ({tb['v']} vitórias, {tb['e']} empates, {tb['d']} derrotas)"
        if resto:
            txt += f"; faltam {max(resto - tb['jogos'], 0)} rodadas"
        if d["liga"] == "bra.1":
            cls = d["classificacao"]
            if tb["pos"] <= 6:
                txt += "; está na zona de classificação para a Libertadores"
            elif tb["pos"] <= 12:
                txt += "; está na zona da Sul-Americana"
            if tb["pos"] <= 16 and len(cls) >= 17:
                txt += f"; está {tb['pts'] - cls[16]['pts']} pontos acima da zona de rebaixamento"
            elif tb["pos"] >= 17 and len(cls) >= 16:
                txt += f"; está na zona de rebaixamento, a {cls[15]['pts'] - tb['pts']} pontos de sair dela"
            if tb["pos"] != 1:
                txt += f"; o líder é o {cls[0]['time']} com {cls[0]['pts']} pontos"
        linhas.append(txt + ".")
    if d["feitos"]:
        ult = d["feitos"][-5:][::-1]
        nomes = {"V": "vitória", "E": "empate", "D": "derrota"}
        linhas.append("ÚLTIMOS JOGOS (do mais recente): " + "; ".join(
            f"{nomes[j['res']]} por {j['gm']} a {j['gr']} {'em casa' if j['casa'] else 'fora'} contra o {j['rival']} ({j['data'].day}/{j['data'].month})" for j in ult)
            + f". Sequência: {''.join(j['res'] for j in ult)}.")
    if d["proximos"]:
        p = d["proximos"][0]
        linhas.append(f"PRÓXIMO JOGO: contra o {p['rival']}, {'em casa' if p['casa'] else 'fora de casa'}, {_quando(p['data'])}.")
    else:
        linhas.append("PRÓXIMO JOGO: não há jogo marcado nos dados que tenho.")
    if d["noticias"]:
        linhas.append("NOTÍCIAS RECENTES: " + " | ".join(f"{n['titulo']} ({n['fonte']})" for n in d["noticias"]))
    return "\n".join(linhas)


def fala_basica(d):
    """Resposta falada simples, sem modelo (reserva)."""
    tb, partes = d.get("tabela"), []
    if tb:
        partes.append(f"O {d['time']} é o {tb['pos']}º colocado, com {tb['pts']} pontos em {tb['jogos']} jogos.")
    if d["feitos"]:
        j = d["feitos"][-1]
        partes.append(f"No último jogo, {'vitória' if j['res'] == 'V' else 'derrota' if j['res'] == 'D' else 'empate'} por {j['gm']} a {j['gr']} contra o {j['rival']}.")
    if d["proximos"]:
        p = d["proximos"][0]
        partes.append(f"O próximo jogo é contra o {p['rival']}, {_quando(p['data'])}.")
    if d["noticias"]:
        partes.append(f"Nas notícias: {d['noticias'][0]['titulo'].rstrip('.')}, segundo {d['noticias'][0]['fonte']}.")
    return " ".join(partes) or f"Não consegui dados do {d['time']} agora."


def tabela_falada(liga="bra.1", n=5):
    cls = _tabela(liga)
    top = ", ".join(f"{x['pos']}º {x['time']} com {x['pts']} pontos" for x in cls[:n])
    z4 = ", ".join(f"{x['time']} ({x['pts']})" for x in cls[-4:])
    rod = max((x["jogos"] for x in cls), default=0)
    return f"Brasileirão, depois de {rod} rodadas. Na frente: {top}. Na zona de rebaixamento: {z4}."


def jogos_hoje():
    """Jogos das principais competições na data de hoje (horário de Brasília)."""
    hoje = datetime.now(BRT)
    dia = hoje.strftime("%Y%m%d")
    saidas = []
    for liga, nome in (("bra.1", "Brasileirão"), ("conmebol.libertadores", "Libertadores"), ("uefa.champions", "Champions League"),
                       ("eng.1", "Premier League"), ("esp.1", "La Liga")):
        try:
            for ev in _json(f"{SITE}/{liga}/scoreboard", dates=dia).get("events", []):
                comp = ev["competitions"][0]
                cs = {c["homeAway"]: c for c in comp["competitors"]}
                casa, fora = cs["home"]["team"]["displayName"], cs["away"]["team"]["displayName"]
                st = comp["status"]["type"]
                if st["completed"] or st["state"] == "in":
                    g = lambda c: (c["score"]["displayValue"] if isinstance(c.get("score"), dict) else c.get("score", "0"))
                    saidas.append(f"{casa} {g(cs['home'])} a {g(cs['away'])} {fora}" + (" (final)" if st["completed"] else " (rolando agora)") + f", {nome}")
                else:
                    saidas.append(f"{casa} x {fora}, às {_data(ev['date']).hour}h, {nome}")
        except Exception as e:
            print(f"[futebol] {liga}: {e}")
    return saidas
