"""Gera assistente/pronuncias/tecnologia.tsv a partir de dicionario/*.txt e valida contra o vocabulário do modelo.

Tipos no TSV:  A = qualquer caixa (inclusive MAIÚSCULAS)   S = só em MAIÚSCULAS (siglas e palavras curtas ambíguas)
               E = caixa exata (ex.: "GHz")
Uso: python tools/gerar_tecnologia.py
"""
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from assistente import fonemas  # noqa: E402

SAIDA = RAIZ / "assistente" / "pronuncias" / "tecnologia.tsv"

# Palavras do português (ou ambíguas) que NUNCA devem ser trocadas
PORTUGUES = {"sem", "opera", "cachê", "cupom", "tráfego", "perfil", "compartilhar", "inscrito", "funil", "afiliado",
             "influenciador", "x", "99", "pesos", "inferência", "latência", "protótipo", "emulador", "investidor",
             "unicórnio", "teclado", "variable", "go", "set", "map", "list", "case", "cases", "call", "safari",
             "home", "max", "mint", "fish", "arch", "word", "project", "access", "office", "forms", "earth",
             "translate", "keep", "sheets", "docs", "teams", "meet", "photos", "pro", "less", "rest", "source",
             "code", "error", "status", "include", "import", "export", "service", "string", "class", "tree",
             "heap", "image", "pod", "pods", "link", "store", "shop", "cart", "sale", "live", "post", "feed"}
# Curtas (≤3 letras) que podem valer em minúsculas
CURTAS_OK = {"app", "bug", "git", "web", "dev", "log", "bit", "hub", "pop", "tab", "fan", "mic", "gif", "pix", "bot",
             "pip", "npm", "vim", "zip", "rar", "tor", "vlc", "obs", "bun", "nas", "ssd", "hdd", "usb", "pdf"}
# Palavras que quero mesmo que sejam inglês comum (sobrescrevem PORTUGUES)
FORCAR = {"link", "post", "feed", "live", "store", "shop", "home", "service", "image", "code", "source",
          "class", "string", "error", "status", "include", "import", "export", "heap", "tree", "pod", "pods", "cart"}
PORTUGUES -= FORCAR

LETRAS = {"a": "á", "b": "bê", "c": "cê", "d": "dê", "e": "é", "f": "éfe", "g": "gê", "h": "agá", "i": "í",
          "j": "jóta", "k": "cá", "l": "éle", "m": "ême", "n": "ene", "o": "ó", "p": "pê", "q": "quê",
          "r": "érre", "s": "ésse", "t": "tê", "u": "ú", "v": "vê", "w": "dáblio", "x": "xis", "y": "ípsilon",
          "z": "zê"}
DIGITOS = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove"]


def soletrar(sigla):
    return " ".join(LETRAS.get(c.lower()) or DIGITOS[int(c)] for c in sigla if c.isalnum())


def tokens_modelo():
    t = (RAIZ / "voz" / "klaus" / "tokens.txt").read_text(encoding="utf-8").split("\n")
    return {l[0] for l in t if l.rstrip("\r")}


def chave_valida(k):
    return bool(re.fullmatch(r"[^\W_](?:[^\W_]|['’\- ])*", k))


def ler(arq):
    for n, linha in enumerate(arq.read_text(encoding="utf-8").split("\n"), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        k, _, v = linha.partition("=")
        yield n, k.strip(), v.strip()


V = "aeoáéóâêô"


def arrumar(falado):
    """Ajusta a grafia para o jeito do brasileiro falar e para o que o conversor de fonemas entende."""
    out = []
    for w in falado.split(" "):
        # "au/eu/ou" + n/m: o conversor quebraria o ditongo; "u" vira "w" ("cáunter" -> "cáwnter")
        w = re.sub("([" + V + "])u(?=[nm](?:[^aeiouáéíóúâêôãõ]|$))", lambda m: m.group(1) + "w", w)
        # ditongo com "i" + n/m/l/r/z no fim: acrescenta vogal ("dizáin" -> "dizáini")
        w = re.sub("([" + V + "]i[nmlrz])(s?)$", lambda m: m.group(1) + "i" + m.group(2), w)
        # consoante oclusiva seca no fim: o brasileiro fala com "i" ("internet" -> "internéti")
        if re.search("[tdpbk]s?$", w):
            w = re.sub("([tdpbk])(s?)$", lambda m: m.group(1) + "i" + m.group(2), w)
        elif w.endswith("c"):
            w = w[:-1] + "qui"
        elif w.endswith("g"):
            w += "ui"
        out.append(w)
    return " ".join(out)



def juntar(frases):
    return " ".join(frases)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ok = tokens_modelo()
    entradas = {}   # chave -> (falado, tipo)
    sem_plural = set()
    ignoradas = {"portugues": 0, "chave": 0, "igual": 0}

    for arq in sorted((RAIZ / "dicionario").glob("*.txt")):
        if arq.name == "acronimos.txt":
            continue
        verbos = arq.name.startswith("08")
        for n, k, v in ler(arq):
            if not v:
                continue
            k = k.lower().replace("’", "'")
            v = re.sub(r"\bdjê\b", "djêi", v)
            if k in PORTUGUES:
                ignoradas["portugues"] += 1
                continue
            if not chave_valida(k):
                ignoradas["chave"] += 1
                continue
            letras = sum(c.isalpha() for c in k)
            tipo = "A" if (letras > 3 or k in CURTAS_OK) else "S"
            if juntar(fonemas.frases(k)) == juntar(fonemas.frases(v)):
                ignoradas["igual"] += 1
                continue
            if tipo == "A" and not verbos:
                v = arrumar(v)
            entradas[k] = (v, tipo)
            if verbos:
                sem_plural.add(k)

    # siglas
    for n, k, v in ler(RAIZ / "dicionario" / "acronimos.txt"):
        if not chave_valida(k):
            ignoradas["chave"] += 1
            continue
        falado = v or soletrar(k)
        if any(c.islower() for c in k):
            entradas["\x00" + k] = (falado, "E")      # caixa exata
        else:
            entradas[k.lower()] = (falado, "S")

    # plurais automáticos (só termos em minúsculas, de uma palavra)
    for k, (v, tipo) in list(entradas.items()):
        if tipo != "A" or k in sem_plural or " " in k or k.endswith(("s", "ar", "er", "ir")) and False:
            continue
        if k.endswith(("s", "x", "z")) or len(k) <= 3:
            continue
        if k + "s" not in entradas and not v.endswith(("s", "x", "z")):
            entradas[k + "s"] = (v + "s", "A")

    problemas = []
    linhas = ["# Gerado por tools/gerar_tecnologia.py a partir de dicionario/*.txt. Não edite à mão."]
    for k, (v, tipo) in sorted(entradas.items()):
        fon = juntar(fonemas.frases(v))
        ruins = {c for c in unicodedata.normalize("NFD", fon) if c not in ok and c not in ".,?!;:"}
        if ruins:
            problemas.append((k, v, "".join(sorted(ruins))))
            continue
        linhas.append(f"{k.lstrip(chr(0))}\t{v}\t{tipo}")
    SAIDA.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"{len(linhas) - 1} entradas escritas em {SAIDA.name}")
    print("ignoradas:", ignoradas)
    if problemas:
        print(f"{len(problemas)} com símbolo fora do vocabulário do modelo:")
        for p in problemas[:40]:
            print("  ", p)


if __name__ == "__main__":
    main()
