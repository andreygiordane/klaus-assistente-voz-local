"""Pesquisa na web (DuckDuckGo + Wikipédia, sem chave). Só a consulta sai da máquina; o resumo é feito pelo LLM local."""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; KlausAssistente/1.0)"}
_ATUAL = re.compile(r"\b(hoje|agora|ultim\w+|noticias?|placar|resultado|cotacao|cotação|preco|preço|quanto custa|quanto vale|jogo)\b", re.I)


def _texto_da_pagina(url, limite=3000):
    try:
        r = requests.get(url, timeout=5, headers=UA)
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", ""):
            return ""
        html = re.sub(r"(?is)<(script|style|noscript|nav|footer|header|aside|form).*?</\1>", " ", r.text)
        txt = re.sub(r"(?s)<[^>]+>", " ", html)
        txt = re.sub(r"&[a-z#0-9]+;", " ", txt)
        return re.sub(r"\s+", " ", txt).strip()[:limite]
    except Exception:
        return ""


def wikipedia(tema, idioma="pt"):
    """Introdução do melhor artigo da Wikipédia sobre o tema ("" se não achar)."""
    try:
        base = f"https://{idioma}.wikipedia.org/w/api.php"
        r = requests.get(base, params={"action": "query", "list": "search", "srsearch": tema, "srlimit": 1, "format": "json"}, headers=UA, timeout=5)
        achou = r.json()["query"]["search"]
        if not achou:
            return ""
        titulo = achou[0]["title"]
        r = requests.get(base, params={"action": "query", "prop": "extracts", "explaintext": 1, "exintro": 1, "titles": titulo,
                                       "redirects": 1, "format": "json"}, headers=UA, timeout=5)
        txt = next(iter(r.json()["query"]["pages"].values())).get("extract", "").strip()
        return f"{titulo}: {txt[:2800]}" if txt else ""
    except Exception:
        return ""


def pesquisar(consulta, resultados=6, ler_paginas=3, wiki=False):
    """Devolve texto com os melhores resultados: trechos, o começo das primeiras páginas (lidas em paralelo) e, se wiki, a Wikipédia."""
    from ddgs import DDGS
    usar_wiki = wiki and not _ATUAL.search(consulta)
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_wiki = pool.submit(wikipedia, consulta) if usar_wiki else None
        achados = []
        for kw in ({"region": "br-pt"}, {}, {"backend": "yahoo"}):   # o serviço é instável: tenta alternativas
            try:
                achados = list(DDGS().text(consulta, max_results=resultados, **kw))
            except Exception:
                achados = []
            if achados:
                break
        paginas = [pool.submit(_texto_da_pagina, a["href"]) if i < ler_paginas and a.get("href") else None for i, a in enumerate(achados)]
        partes = []
        enc = f_wiki.result() if f_wiki else ""
        if enc:
            partes.append(f"[Wikipédia] {enc}")
        for i, a in enumerate(achados):
            bloco = f"[{i + 1}] {a.get('title', '')}\n{a.get('body', '')}"
            pg = paginas[i].result() if paginas[i] else ""
            if pg:
                bloco += f"\nConteúdo da página: {pg}"
            partes.append(bloco)
    if not partes:
        return "Nenhum resultado encontrado."
    return "\n\n".join(partes)


def calcular(expr):
    """Conta exata (sem eval): + - * / ** % //, parênteses, raiz, pi, 'x% de y'."""
    import ast
    import math
    import operator
    expr = (expr or "").lower().replace("^", "**").replace("×", "*").replace("÷", "/").replace(",", ".")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*de\s*(\d+(?:\.\d+)?)", r"(\1/100*\2)", expr)
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
           ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
    fun = {"sqrt": math.sqrt, "raiz": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10, "ln": math.log, "abs": abs, "round": round}
    const = {"pi": math.pi, "e": math.e}

    def av(n):
        if isinstance(n, ast.Expression):
            return av(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = av(n.left), av(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 1000:
                raise ValueError("expoente grande demais")
            return ops[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](av(n.operand))
        if isinstance(n, ast.Name) and n.id in const:
            return const[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in fun and not n.keywords:
            return fun[n.func.id](*[av(x) for x in n.args])
        raise ValueError("expressão não permitida")

    try:
        r = av(ast.parse(expr.strip(), mode="eval"))
        r = round(r, 6)
        texto = str(int(r)) if r == int(r) else str(r).replace(".", " vírgula ")
        return f"O resultado é {texto}."
    except ZeroDivisionError:
        return "Rá, dividir por zero? Nem eu consigo!"
    except Exception:
        return "Não consegui entender essa conta."
