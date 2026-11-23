"""Conversor de português do Brasil para fonemas (IPA) do Klaus.

Port fiel do FonemasPt.kt do app Lê+ (mesmas regras, mesmas listas), para o modelo falar no PC
exatamente como fala no app. Se o Kotlin mudar, rode tools/extrair_dados.py de novo e revise este arquivo.
"""
import re
import unicodedata

from . import dados_fonemas as D

FORTE = "ˈ"
ATONAS = set(D.ATONAS)
ENCLITICOS = set(D.ENCLITICOS)
SUJEITOS = set(D.SUJEITOS)
PALAVRAS_ENFASE = set(D.PALAVRASENFASE)
ABREVIACOES = dict(D.ABREVIACOES)
ABERTAS = set(D.ABERTAS)
VERBOS_ABERTOS = set(D.VERBOSABERTOS)
FECHADAS = set(D.FECHADAS)

VOGAIS = "aeiouáéíóúâêôãõàü"
ACENTOS = "áéíóúâêô"
FRONTAIS = "eiéíêy"


def at(w, i):
    return w[i] if 0 <= i < len(w) else None


def vogal(c):
    return c is not None and c in VOGAIS


def sonora(c):
    c = c.lower()
    return c in VOGAIS or c in "bdgjlmnrvz"


def em(c, conj):
    """Equivalente a `c in "..."` do Kotlin, seguro para None."""
    return c is not None and c in conj


# ---------------------------------------------------------------- API pública

def tem_enfase(texto):
    return tem_maiuscula_enfase(texto) or any(
        m.group(0).lower() in PALAVRAS_ENFASE for m in re.finditer(r"[^\W\d_]+", texto))


def tem_maiuscula_enfase(texto):
    return any(m.group(0).isupper() for m in re.finditer(r"(?<![^\W\d_])[^\W\d_]{2,}(?![^\W\d_])", texto))


def frases(texto):
    """Fonemas do texto, uma entrada por frase (forma decomposta NFD que o modelo recebe)."""
    texto = _pre_processar(texto)
    saida = []
    atual = []  # lista de pedaços; texto atual = "".join(atual)
    partes = [m.group(0) for m in re.finditer(r"(?:[^\W_]|['’\-])+|[.!?…,;:—–\-]+", texto)]
    anterior = ""

    def atual_str():
        return "".join(atual)

    for i, p in enumerate(partes):
        if p[0].isalnum():
            p_low = p.lower()
            seg = partes[i + 1] if i + 1 < len(partes) else None
            seguinte = seg if seg is not None and seg[0].isalnum() else None
            fala = _palavra(p, anterior, seguinte)
            if fala:
                s = atual_str()
                if s and s[-1] != " ":
                    atual.append(" ")
                atual.append(fala)
            anterior = p_low
        else:
            t = _pontuacao(p)
            if not atual_str():
                continue
            atual.append(t)
            if t in ".?!":
                saida.append(atual_str().strip())
                atual.clear()
            else:
                atual.append(" ")
            anterior = ""
    resto = atual_str()
    if resto.strip():
        saida.append(resto.rstrip())
    return [unicodedata.normalize("NFD", s) for s in saida]


# ------------------------------------------------------------ pré-processamento

def _pre_processar(texto):
    def abrev(m):
        return ABREVIACOES.get(m.group(0).lower(), m.group(0))

    texto = re.sub(r"\b([A-Za-zÀ-ú]{2,5})\.", abrev, texto)
    texto = re.sub(r"\b\d{1,7}\b", lambda m: _numero_por_extenso(int(m.group(0))), texto)
    return texto


def _pontuacao(p):
    if "?" in p:
        return "?"
    if "!" in p:
        return "!"
    if "." in p or "…" in p:
        return "."
    if ";" in p:
        return ";"
    if ":" in p:
        return ":"
    return ","


# --------------------------------------------------------------------- palavra

def _palavra(original, anterior, seguinte):
    w0 = original.lower().replace("’", "'")
    if any(c.isdigit() for c in w0):
        if w0.isascii() and w0.isdigit():
            return " ".join(_palavra(x, "", None) for x in _numero_por_extenso(int(w0)).split(" ") if x.strip())
        partes = []
        for c in (c for c in w0 if c.isalnum()):
            if c.isdigit():
                partes.append(_palavra(D.ALGARISMOS[int(c)], "", None))
        return " ".join(x for x in partes if x)

    pedacos = [x for x in w0.split("-") if x]
    if not pedacos:
        return ""
    prox = seguinte.lower() if seguinte is not None else None

    if len(pedacos) > 1:
        out = []
        for k, p in enumerate(pedacos):
            clitico = k > 0 and (p in ENCLITICOS or p in ("lhe", "lhes"))
            som = _sons(_norm_grafia("".join(c for c in p if c.isalpha())),
                        atona=clitico, verbal=False,
                        proxima=prox if k == len(pedacos) - 1 else None,
                        colada=k < len(pedacos) - 1)
            out.append(" " + som if (k > 0 and not clitico) else som)
        return "".join(out)

    if w0.startswith("d'") and len(w0) > 2:
        resto = "".join(c for c in w0[2:] if c.isalpha())
        if resto and not resto.startswith(("est", "ess", "aquel", "aquil", "el")) and resto not in {
                "ali", "aqui", "aí", "ai", "isso", "isto", "outro", "outra", "outros", "outras"}:
            pref = "dʒ" if resto[0] in "ií" else "d"
            return pref + _sons(_norm_grafia(resto), atona=False, verbal=False, proxima=prox, colada=False)

    limpa = _norm_grafia("".join(c for c in w0 if c.isalpha()))
    if not limpa:
        return ""
    liga = prox is not None and prox[0] in VOGAIS
    prox_comeca_com_a = prox in ("a", "as", "à", "às")

    if limpa == "à":
        return "aː"
    if limpa == "às":
        return "aːz" if prox is not None and sonora(prox[0]) else "aːs"
    if limpa == "a":
        return "" if prox_comeca_com_a else "a"
    if limpa == "as":
        return "az" if prox is not None and sonora(prox[0]) else "as"
    if limpa == "o":
        return "u" if liga else "ʊ"
    if limpa == "os":
        return "ʊz" if prox is not None and sonora(prox[0]) else "ʊs"
    if limpa == "muito":
        return "mwˈiŋtw" if liga else "mwˈiŋtʊ"
    if limpa == "muita":
        return "mwˈiŋtæ"
    if limpa == "muitos":
        return "mwˈiŋtʊz" if prox is not None and sonora(prox[0]) else "mwˈiŋtʊs"
    if limpa == "muitas":
        return "mwˈiŋtæz" if prox is not None and sonora(prox[0]) else "mwˈiŋtæs"

    eh_maiuscula = sum(c.isalpha() for c in original) >= 2 and all(
        (not c.isalpha()) or c.isupper() for c in original)

    if len(limpa) > 7 and limpa.endswith("mente") and limpa not in {
            "somente", "comente", "semente", "lemente", "demente", "clemente", "veemente"}:
        base_adj = limpa[:-len("mente")]
        marcador = "ˌ" if (eh_maiuscula or limpa in PALAVRAS_ENFASE) else ""
        som_base = _sons(base_adj, atona=False, verbal=False, proxima=None, colada=True).replace(FORTE, marcador)
        som_mente = _sons("mente", atona=False, verbal=False, proxima=prox, colada=False)
        return som_base + som_mente

    eh_enfase = eh_maiuscula or limpa in PALAVRAS_ENFASE
    return _sons(limpa, atona=(not eh_enfase and limpa in ATONAS), verbal=anterior in SUJEITOS,
                 proxima=prox, colada=False)


def _norm_grafia(w0):
    if len(w0) < 2:
        return w0
    if w0 in ("eh", "éh", "he"):
        return "é"
    w = w0
    if "ph" in w:
        w = w.replace("ph", "f")
    for dup, simp in D.CONSOANTESDUPLASARCAICAS:
        if dup in w:
            w = w.replace(dup, simp)
    for ant, br in D.GRAFIASMUDAS:
        if ant in w:
            w = w.replace(ant, br)
    w = {"acto": "ato", "actos": "atos", "actor": "ator", "actores": "atores", "actriz": "atriz",
         "tecto": "teto", "tectos": "tetos", "somno": "sono", "hymno": "hino", "hymnos": "hinos"}.get(w, w)
    for sem, com in D.QUGUPRONUNCIADOS:
        if sem in w:
            w = w.replace(sem, com)
    return w


# ------------------------------------------------------------------- sílabas

def _nucleos(w):
    out = []
    i = 0
    n = len(w)
    while i < n:
        if not vogal(w[i]):
            i += 1
            continue
        if w[i] == "u" and i > 0 and w[i - 1] in "qg" and em(at(w, i + 1) or " ", "eiéíê"):
            i += 1
            continue
        fim = i + 1
        a = w[i]
        b = at(w, i + 1)
        if b is not None and vogal(b):
            c2, c3 = at(w, i + 2), at(w, i + 3)
            if a + b in ("ão", "õe", "ãe"):
                ditongo = True
            elif a in "aeoáéóâêô" and b in "iu":
                ditongo = (not (c2 is not None and c2 in "mnrlz" and i + 3 >= n)
                           and not (c2 == "n" and not vogal(c3))
                           and not (c2 == "n" and c3 == "h")
                           and not (i == 1 and w.startswith("reun"))
                           and not (w.startswith("proib") or w.startswith("coib")))
            elif a == "i" and b == "u":
                ditongo = True
            elif a == "u" and b == "i":
                ditongo = not (c2 == "m" or c2 == "n" or (c2 is not None and c2 in "rlz" and i + 3 >= n))
            elif a in ("u", "ü") and i > 0 and w[i - 1] in "qg":
                ditongo = True
            else:
                ditongo = False
            if ditongo:
                fim = i + 2
        out.append((i, fim))
        i = fim
    return out


def _forte(w, ns):
    if not ns:
        return -1
    if w in ("porque", "porquê"):
        return len(ns) - 1
    if w == "onu":
        return 0
    for k, (ini, fim) in enumerate(ns):
        if any(w[j] in ACENTOS for j in range(ini, fim)):
            return k
    for k, (ini, fim) in enumerate(ns):
        if any(w[j] in "ãõ" for j in range(ini, fim)):
            return k
    if len(ns) == 1:
        return 0
    paroxitona = w.endswith(("a", "e", "o", "as", "es", "os", "am", "em", "ens"))
    return len(ns) - 2 if paroxitona else len(ns) - 1


def _abre(w, verbal):
    if verbal and w in VERBOS_ABERTOS:
        return True
    if w in FECHADAS:
        return False
    if w in ABERTAS:
        return True
    return w.endswith(("el", "ela", "elas", "ola", "olas", "ol", "osa", "osas", "osos", "eram"))


def _sons(w, atona, verbal, proxima, colada):
    if not w:
        return ""
    ns = _nucleos(w)
    tonica = -1 if atona else _forte(w, ns)
    aberta = _abre(w, verbal) if tonica >= 0 else False
    sb = []
    por_inicio = {ini: (ini, fim) for ini, fim in ns}
    i = 0
    idx_n = 0
    n = len(w)
    while i < n:
        nuc = por_inicio.get(i)
        if nuc is not None:
            e_forte = idx_n == tonica
            ultimo = idx_n == len(ns) - 1
            pos_tonica = 0 <= tonica < idx_n
            i = _vogal_som(w, nuc, sb, e_forte, ultimo, pos_tonica, aberta, atona, colada, proxima)
            idx_n += 1
            continue
        i = _consoante(w, i, sb, proxima, colada)
    return "".join(sb)


def _vogal_som(w, nuc, sb, e_forte, ultimo, pos_tonica, aberta, atona, colada, proxima):
    ini, fim = nuc
    g = w[ini:fim]
    depois = at(w, fim)
    depois2 = at(w, fim + 1)
    marca = FORTE if e_forte else ""
    if len(g) == 2 and g[0] in ("u", "ü") and ini > 0 and w[ini - 1] in "qg":
        sb.append("w")
        return _vogal_simples(w, g[1], fim - 1, fim, sb, e_forte, ultimo, pos_tonica, aberta, atona, colada, proxima)
    if g == "ão":
        sb.append(marca + "ɐ̃ʊ̃"); return fim
    if g in ("ãe", "õe"):
        sb.append(marca + ("ɐ̃ɪ̃" if g == "ãe" else "õɪ̃")); return fim
    if g in ("ai", "ái", "âi"):
        sb.append(marca + "aɪ"); return fim
    if g in ("au", "áu", "âu"):
        sb.append(marca + "aʊ"); return fim
    if g in ("ei", "éi"):
        sb.append(marca + ("ɛɪ" if (g == "éi" or (e_forte and aberta)) else "eɪ")); return fim
    if g == "êi":
        sb.append(marca + "eɪ"); return fim
    if g in ("eu", "éu", "êu"):
        sb.append(marca + ("ɛʊ" if g == "éu" else "eʊ")); return fim
    if g in ("oi", "ói", "ôi"):
        sb.append(marca + ("ɔɪ" if (g == "ói" or (e_forte and aberta)) else "oɪ")); return fim
    if g in ("ou", "ôu", "óu"):
        sb.append(marca + "ow"); return fim
    if g in ("iu", "íu"):
        sb.append(marca + "iʊ"); return fim
    if g in ("ui", "úi"):
        sb.append(marca + "uɪ"); return fim
    if g == "a" and depois == "m" and depois2 is None:
        sb.append(marca + "ɐ̃ʊ̃"); return fim + 1
    if g in ("e", "ê", "é") and depois in ("m", "n") and (
            depois2 is None or (depois2 == "s" and at(w, fim + 2) is None)):
        sb.append(marca + "eɪŋ"); return fim + 1
    return _vogal_simples(w, g[0], ini, fim, sb, e_forte, ultimo, pos_tonica, aberta, atona, colada, proxima)


def _vogal_simples(w, v, ini, fim_in, sb, e_forte, ultimo, pos_tonica, aberta, atona, colada, proxima):
    fim = ini + 1 if fim_in <= ini else fim_in
    d = at(w, fim)
    d2 = at(w, fim + 1)
    marca = FORTE if e_forte else ""
    base = {"á": "a", "à": "a", "â": "a", "ã": "a", "é": "e", "ê": "e", "í": "i",
            "ó": "o", "ô": "o", "õ": "o", "ú": "u", "ü": "u"}.get(v, v)
    nasal_fecha = d in ("m", "n") and d2 is not None and not vogal(d2) and d2 != "h"
    nasal_fim = d in ("m", "n") and d2 is None
    nasal_plural = d in ("n", "m") and d2 == "s" and at(w, fim + 2) is None
    if nasal_fecha or nasal_fim or nasal_plural:
        labial = d2 in ("p", "b")
        sb.append(marca)
        sb.append({"a": "ɐ̃", "e": "e", "i": "i", "o": "o"}.get(base, "ũ"))
        sb.append("m" if labial else "ŋ")
        return fim + 1
    antes_nasal_het = d in ("m", "n") and (vogal(d2) or d2 == "h")
    nasal_leve = base == "a" and ((e_forte and antes_nasal_het) or (d == "n" and d2 == "h"))
    ultima_letra = fim == len(w) or (fim == len(w) - 1 and w[-1] == "s")
    liga = fim == len(w) and proxima is not None and proxima[0].lower() in VOGAIS and not colada
    proxima_vogal = vogal(d)
    if not e_forte and base == "i" and pos_tonica and proxima_vogal:
        som = "j"
    elif not e_forte and base == "u" and proxima_vogal:
        som = "w"
    elif v == "ã":
        som = "ɐ̃"
    elif v == "õ":
        som = "õ"
    elif nasal_leve:
        som = "ɐ̃"
    elif v == "é":
        som = "ɛ"
    elif v == "ó":
        som = "ɔ"
    elif v in ("ê", "ô"):
        som = base
    elif e_forte and base in ("e", "o") and antes_nasal_het:
        som = base
    elif e_forte and base == "e":
        som = "ɛ" if aberta else "e"
    elif e_forte and base == "o":
        som = "ɔ" if aberta else "o"
    elif e_forte:
        som = base
    elif base == "a" and ultima_letra and not atona:
        som = "æ"
    elif base == "e" and ultima_letra and not atona:
        som = "y"
    elif base == "o" and ultima_letra and not atona:
        som = "w" if liga else "ʊ"
    elif base == "e" and atona and w == "e":
        som = "i"
    elif base == "o" and atona and ultima_letra:
        som = "w" if liga else "ʊ"
    elif base == "e" and atona and ultima_letra:
        som = "y"
    elif base == "a" and atona and w.endswith("a") and ultima_letra and len(w) > 2:
        som = "æ"
    elif base == "a" and pos_tonica:
        som = "æ"
    else:
        som = base
    sb.append(marca + som)
    return fim


def _frontal_c(w, i):
    return em(at(w, i + 2), "eiéíê")


def _consoante(w, i, sb, proxima, colada):
    c = w[i]
    p = at(w, i + 1)
    a = at(w, i - 1)
    n = len(w)
    frontal = em(p, FRONTAIS)
    soa_i = p is not None and (p in "iíy" or (p == "e" and (i + 2 == n or (i + 3 == n and w[-1] == "s"))))

    if c in "bfkpv":
        if c != a:
            sb.append(c)
        return i + 1
    if c == "w":
        sb.append("w"); return i + 1
    if c == "y":
        sb.append("i"); return i + 1
    if c == "ç":
        sb.append("s"); return i + 1
    if c == "j":
        sb.append("ʒ"); return i + 1
    if c == "h":
        return i + 1
    if c == "q":
        sb.append("k")
        return i + 2 if (p == "u" and em(at(w, i + 2), "eiéíê")) else i + 1
    if c == "c":
        if p == "h":
            sb.append("ʃ"); return i + 2
        sb.append("s" if frontal else "k")
        return i + 1
    if c == "g":
        if frontal:
            sb.append("ʒ"); return i + 1
        sb.append("ɡ")
        return i + 2 if (p == "u" and em(at(w, i + 2), "eiéíê")) else i + 1
    if c == "t":
        if p == "h":
            p2 = at(w, i + 2)
            th_soa_i = p2 is not None and (p2 in "iíy" or (p2 == "e" and (i + 3 == n or (i + 4 == n and w[-1] == "s"))))
            sb.append("tʃ" if th_soa_i else "t")
            return i + 2
        sb.append("tʃ" if soa_i else "t")
        return i + 1
    if c == "d":
        sb.append("dʒ" if soa_i else "d")
        return i + 1
    if c == "l":
        if p == "h":
            sb.append("lj"); return i + 2
        if p == "l":
            return i + 1
        if p is None or not vogal(p):
            sb.append("w" if em(a, "oóôuúü") else "ʊ")
        else:
            sb.append("l")
        return i + 1
    if c == "m":
        if p is None:
            sb.append("ŋ")
        elif c != a:
            sb.append("m")
        return i + 1
    if c == "n":
        if p == "h":
            sb.append("ɲ"); return i + 2
        if p is None:
            sb.append("ŋ")
        elif c != a:
            sb.append("n")
        return i + 1
    if c == "r":
        if p == "r":
            sb.append("x"); return i + 2
        forte_r = i == 0 or em(a, "nls")
        if forte_r:
            sb.append("x")
        elif p is None:
            sb.append("ɾ" if (proxima is not None and proxima[0] in VOGAIS) else "r")
        elif vogal(p):
            sb.append("ɾ")
        else:
            sb.append("r")
        return i + 1
    if c == "s":
        if p == "s":
            sb.append("s"); return i + 2
        if p == "c" and _frontal_c(w, i):
            sb.append("s"); return i + 2
        if p == "ç":
            sb.append("s"); return i + 2
        entre_vogais = vogal(a) and vogal(p)
        fim = p is None
        if entre_vogais:
            sb.append("z")
        elif i == 4 and w.startswith("trans") and vogal(p):
            sb.append("z")
        elif p is not None and p in "bdgjlmnrvz":
            sb.append("z")
        elif fim and proxima is not None and not colada and sonora(proxima[0]):
            sb.append("z")
        else:
            sb.append("s")
        return i + 1
    if c == "x":
        if p == "c" and _frontal_c(w, i):
            sb.append("s"); return i + 2
        e_antes = em(a, "eéê")
        if any(k in w for k in ("próxim", "proxim", "máxim", "maxim", "auxíli", "auxili")) or \
                w.startswith(("trouxe", "sintax")):
            som = "s"
        elif e_antes and i == 1 and (vogal(p) or p == "h"):
            som = "z"
        elif e_antes and p is not None and not vogal(p):
            som = "z" if p in "bdgjlmnrvz" else "s"
        elif e_antes and vogal(p) and any(w.startswith(k) for k in D.XCOMOZ):
            som = "z"
        elif any(k in w for k in D.XCOMOKS):
            som = "ks"
        elif i == 0 or a == "n" or (
                (em(a, "ai") and p is not None and vogal(p) and at(w, i - 2) is not None)
                and not w.startswith("ma")):
            som = "ʃ"
        elif p is None:
            som = "ks"
        else:
            som = "ʃ"
        sb.append(som)
        return i + 1
    if c == "z":
        if p is None:
            sb.append("z" if (proxima is not None and not colada and sonora(proxima[0])) else "s")
        else:
            sb.append("z")
        return i + 1
    return i + 1


# --------------------------------------------------------------------- números

def _numero_por_extenso(n):
    if n == 0:
        return "zero"
    if n < 0:
        return "menos " + _numero_por_extenso(-n)
    unidades = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove"]
    dez_a_dezenove = ["dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
    dezenas = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
    centenas = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos",
                "oitocentos", "novecentos"]

    def ate999(v):
        if v == 0:
            return ""
        if v == 100:
            return "cem"
        c, r = divmod(v, 100)
        d, u = divmod(r, 10)
        partes = []
        if c > 0:
            partes.append(centenas[c])
        if 10 <= r <= 19:
            partes.append(dez_a_dezenove[r - 10])
        else:
            if d > 0:
                partes.append(dezenas[d])
            if u > 0:
                partes.append(unidades[u])
        return " e ".join(partes)

    milhoes = n // 1_000_000
    milhares = (n % 1_000_000) // 1_000
    resto = n % 1_000
    blocos = []
    if milhoes > 0:
        blocos.append("um milhão" if milhoes == 1 else f"{ate999(milhoes)} milhões")
    if milhares > 0:
        blocos.append("mil" if milhares == 1 else f"{ate999(milhares)} mil")
    if resto > 0:
        usar_e = bool(blocos) and (resto < 100 or resto % 100 == 0)
        blocos.append(f"e {ate999(resto)}" if usar_e else ate999(resto))
    return " ".join(blocos)
