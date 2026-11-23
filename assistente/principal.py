"""Klaus: assistente de voz local. Rode com:  python -m assistente.principal"""
import difflib
import random
import re
import threading
import time
import unicodedata
import webbrowser
from datetime import date

import os
import subprocess

from . import admin, clima, config, nomes, tarefas
from .cerebro import Cerebro, ROTAS_DADOS, erro_humano, periodo, rota_de_voz, rota_modo
from .fala import Falante, Frases
from .locutor import Locutor
from .ouvido import Ouvido
from .ponte import PORTA_HTTP, Ponte
from .visao import Visao
from .voz_clone import criar_voz

VARIANTES = {"klaus", "claus", "clauss", "klauss", "klaos", "claos", "clauz", "glaus", "claws", "clausi"}
SAUDACOES = {"oi", "ola", "ei", "hey", "bom", "boa", "dia", "tarde", "noite", "e", "ai", "fala", "tudo", "bem", "ta",
             "voce", "ou", "opa", "salve"}
DESPEDIDAS = ("tchau", "ate logo", "ate mais", "obrigado", "obrigada", "valeu", "so isso", "pode parar", "pode ir",
              "era isso", "nada nao", "deixa pra la")


def norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


PARAR = re.compile(r"^(klaus )?(pode |ja )?(parar|para|pare|chega|basta|silencio|cala|calma|espera|espere|aguarda|aguarde|cancela|cancele|cancelar|"
                   r"pausa|pausar|stop|quieto|quieta|fica quieto|fica quieta|ok chega)"
                   r"( de falar| de fala| com isso| por favor| ai| ja| agora| um pouco| um momento| um minuto| klaus| a boca| a bolsa| ja chega| chega)*$")


# Enquanto o Klaus fala ou trabalha, QUALQUER uma destas palavras o interrompe (inclusive "encerrar", "terminar"...). Sozinho, ocioso, "encerrar"
# continua valendo como pedido para fechar o Klaus; "encerrar o Klaus" também.
PARAR_OCUPADO = re.compile(PARAR.pattern.replace("(parar|para|pare|", "(encerra|encerrar|encerre|termina|terminar|termine|finaliza|finalizar|finalize|"
                                                 "interrompe|interromper|interrompa|xiu|shh|entendi|ja deu|para tudo|parar|para|pare|", 1))


def comando_interromper(texto):
    """True para falas curtas que mandam o Klaus calar a boca / largar o que está fazendo: "para", "pare", "chega", "cancela", "encerrar"..."""
    t = norm(texto).strip()
    return 0 < len(t.split()) <= 6 and bool(PARAR_OCUPADO.match(t))


def comando_parar(texto):
    """True para falas curtas de parada ("parar", "Klaus, para!", "chega", "silêncio", "cala a boca", "para de falar")."""
    t = norm(texto).strip()
    return 0 < len(t.split()) <= 6 and bool(PARAR.match(t))


SIM = re.compile(r"^(klaus )?(eu )?(sim|confirmo|confirmo a acao|confirmo sim|confirma|confirmar|confirme|confie|confirmado|pode|pode sim|pode fazer|pode executar|pode mandar|autorizo|autorizado|executa|executar|"
                 r"faz isso|faca isso|manda ver|isso mesmo|positivo|vai em frente|vai)( klaus| por favor| agora| ai| isso)*$")
NAO = re.compile(r"^(klaus )?(nao|nao precisa|cancela|cancele|cancelar|esquece|esqueca|deixa|deixa pra la|negativo|nem pensar|melhor nao)( klaus| por favor| isso)*$")


def resposta_confirmacao(texto):
    """"sim" / "nao" / None (falou outra coisa)."""
    t = norm(texto).strip()
    if 0 < len(t.split()) <= 5:
        if SIM.match(t):
            return "sim"
        if NAO.match(t):
            return "nao"
    return None


def eh_ativacao(palavra, alvos):
    p = norm(palavra)
    return bool(p) and (p in VARIANTES or any(difflib.SequenceMatcher(None, p, a).ratio() >= 0.8 for a in alvos))


def separar(texto, alvos):
    """(chamou, comando): `chamou` se disse "Klaus"; `comando` é o resto, sem saudação solta."""
    palavras = texto.split()
    chamou = any(eh_ativacao(p, alvos) for p in palavras)
    resto = [p for p in palavras if not eh_ativacao(p, alvos)]
    if all(norm(p) in SAUDACOES or not norm(p) for p in resto):
        return chamou, ""
    while resto and norm(resto[0]) in ("oi", "ola", "ei", "hey", "opa"):
        resto.pop(0)
    return chamou, " ".join(resto).strip(" ,.!?")


class Klaus_:
    def __init__(self):
        self.cfg = config.carregar()
        self.pessoa, self.conversa_ate, self.voz, self.visao = None, 0.0, None, None
        self.ao_encerrar = None          # o app define: fecha tudo (janela, bandeja, servidor de voz)
        self.agressivo = False           # dentro do modo sério: bravo, debochado e com palavrões
        self.modo = "normal"             # "normal" ou "serio" (cor do núcleo, tom da voz e jeito de responder)
        self._serio_auto = False         # True quando o modo sério foi ligado sozinho (varredura, ação de administrador)
        self.pendente = None             # ação que altera o computador, esperando a confirmação por voz
        self.cad_voz = None              # cadastro de voz em andamento (a pessoa está falando as frases)
        self.desconhecido = None         # voz/rosto não reconhecidos: {"fase": "perguntando"|"aguardando_voz", "tent": n, "comando": ...}
        self.oferta_voz = None           # nome de quem foi convidado a cadastrar a voz (espera o sim/não)
        self.quem_agora = None           # quem a VOZ desta fala identificou (só isso autoriza trocar cadastros e ações sensíveis)
        self._aviso_estouro = 0.0
        self.aguardando_rosto = None     # (expira em) "quem é essa pessoa?" foi perguntado e a próxima fala é o nome
        self._nao_perguntar_ate = 0.0    # depois de desistir, não pergunta "quem é você" de novo por um tempo
        self.atividade, self._t_trabalho = "", 0.0   # o que o Klaus está fazendo agora (indicador na interface)
        self._falas = 0                  # quantas falas saíram neste comando (rede de segurança: nunca terminar mudo)
        self.interrompido = False        # True depois de um "parar": o Klaus fica quieto até o próximo comando   # a interface pode conectar já no início
        print("[klaus] carregando interface, voz, ouvido e cérebro...")
        self.ponte = Ponte(ao_parar=self.parar_fala, ao_limpar=self.limpar, ao_ouvir=self.ouvir_agora,
                           ao_mudo=self.mudar_voz, ao_abrir=self.abrir_atalho, ao_tarefa=self.marcar_tarefa, ao_camera=self.ligar_camera, ao_modo=self.mudar_modo,
                           info=self.info, niveis=self.niveis, clima=self.clima_atual, tarefas=tarefas.todas)
        self.ponte.iniciar()
        self.ponte.carregando(6, 1, "Carregando a voz do Klaus...")
        t0 = time.time()
        if self.cfg.get("abrir_interface", False):
            webbrowser.open(f"http://127.0.0.1:{PORTA_HTTP}/klaus_hud.html")
        self.voz = criar_voz(self.cfg)
        print(f"[carga] voz {time.time() - t0:.1f}s")
        self.ponte.carregando(20, 2, "Carregando o ouvido (reconhecimento de fala)...")
        self.ouvido = Ouvido(self.cfg["whisper"], self.cfg["microfone"])
        self.ouvido.definir_nomes(self._nomes_conhecidos())
        print(f"[carga] ouvido {time.time() - t0:.1f}s")
        self.ponte.carregando(52, 3, "Carregando o reconhecimento de vozes...")
        self.falante = Falante(self.voz, self.ouvido, ao_mudar=self._falando)
        self.locutor = Locutor()
        self.ouvido.iniciar_interrupcao(self.interromper, comando_interromper)   # continua ouvindo "parar" enquanto fala
        self.voz_na_gpu = self.voz.taxa == 24000          # a clonada (XTTS) disputa a GPU com o LLM
        Frases.MINIMOS = (28, 45) if self.voz_na_gpu else (20, 40)
        print(f"[carga] locutor {time.time() - t0:.1f}s")
        self.ponte.carregando(62, 4, "Carregando o cérebro (modelo local)...")
        self.cerebro = Cerebro(self.cfg)
        print(f"[carga] cérebro {time.time() - t0:.1f}s")
        self.ponte.carregando(84, 5, "Carregando a visão (câmera, rostos e objetos)...")
        self.cerebro.ao_fundo = self.anunciar_fundo
        self.cerebro.ao_canto = lambda letra: None if self.interrompido else self.falante.cantar(letra)
        self.cerebro.ao_cadastro_voz = self.iniciar_cadastro_voz
        self.cerebro.ao_efeito_voz = self.efeito_voz
        self.cerebro.ao_atividade = self._atividade
        self.cerebro.ao_resolver_nome = self.resolver_nome
        self.cerebro.ao_pode_recadastrar = self.pode_recadastrar
        self.cerebro.ao_cadastrar_rosto = self.cadastrar_rosto_por_voz
        self.cerebro.ao_proposta = self.propor
        self.cerebro.ao_encerrar = self.encerrar_klaus
        self.cerebro.ao_camera = self.ligar_camera
        self.cerebro.ao_menus = lambda v: self.ponte.emitir(tipo="menus", valor=bool(v))
        self.cerebro.ao_tarefas = lambda: self.ponte.emitir(tipo="tarefas", lista=tarefas.todas())
        try:
            self.visao = Visao(self.cfg, ao_evento=lambda ev: self.ponte.emitir(tipo="visao", **ev))
        except Exception as e:
            print(f"[visão] indisponível: {e}")
            self.visao = None
        self.cerebro.visao, self.cerebro.ao_aviso = self.visao, self.aviso
        print(f"[carga] visão {time.time() - t0:.1f}s")
        self.ponte.carregando(96, 6, "Finalizando...")
        if self.visao:
            self.visao.ao_cadastro = lambda nome: self.dizer(f"Cadastrei o seu rosto, {nome}, para reconhecer você pela câmera.")
        self.pessoa = None
        self.conversa_ate = 0.0
        self.trava = threading.Lock()      # um assunto por vez (voz ou interface)
        self.ponte.carregando(100, 7, "Pronto.")
        self.ponte.emitir(tipo="info", **self.info())
        if self.visao and self.cfg.get("camera_continua", True) and not config.estado().get("olhos_fechados"):
            self.visao.acompanhar(True)                       # os olhos abrem junto com o Klaus e ficam observando o tempo todo
        if self.visao:
            threading.Thread(target=self._vigia_rostos, daemon=True).start()

    # ------------------------------------------------- trabalho longo: indicador e "ainda tô nessa"
    def _atividade(self, texto):
        self.atividade = texto
        if self._t_trabalho:
            self.ponte.emitir(tipo="trabalho", ativo=True, texto=texto[:1].upper() + texto[1:] + "...", inicio=int(self._t_trabalho * 1000))

    def _frase_ainda(self, n):
        a = self.atividade or "trabalhando nisso"
        if self.agressivo:
            opts = [f"Porra, ainda tô {a}, caralho. Já volto com o resultado.", "Calma, caralho, tô revirando tudo aqui. Já te falo."]
        elif self.modo == "serio":
            opts = [f"Ainda em andamento, senhor: estou {a}. Retorno assim que concluir.", "Segue em execução. Falta pouco, senhor.",
                    "Continuo nisso, senhor. Assim que terminar, informo o resultado."]
        else:
            opts = [f"Ainda tô nessa, tá? Tô {a}. Já volto com a resposta!", "Rá, essa tá dando trabalho, mas não desisti! Só mais um pouquinho.",
                    "Calma que eu tô no pé dela! Segue rolando aqui.", "Eita, demorou, né? Mas tô quase lá. Já te falo!"]
        return opts[n % len(opts)]

    def _vigia_trabalho(self, fim, ini):
        """Enquanto a tarefa roda, avisa por voz (sem atropelar outra fala) que continua trabalhando."""
        proximo, n = 20.0, 0
        while not fim.wait(1.0):
            dec = time.time() - ini
            if dec < proximo or self.interrompido or self.falante.mudo or (self.voz_na_gpu and self.cerebro.pensando):
                continue
            self.dizer(self._frase_ainda(n))
            n += 1
            proximo = dec + 25.0

    # ------------------------------------------------------------------------- nomes e permissões
    def _nomes_conhecidos(self):
        v = getattr(self, "visao", None)
        loc = getattr(self, "locutor", None)
        todos = list(self.cfg.get("pessoas", {})) + (list(v.rostos) if v else []) + (list(loc.vozes) if loc else [])
        return list(dict.fromkeys(todos))

    def resolver_nome(self, nome):
        """"Rafael", "Raphael" e "Rafaél" são a mesma pessoa cadastrada; nome novo fica como foi dito."""
        nome = (nome or "").strip()
        return nomes.resolver(nome, self._nomes_conhecidos()) or (nome[:1].upper() + nome[1:])

    def pode_recadastrar(self, nome, adicionar=False):
        """Cadastrar alguém NOVO qualquer um pode. Trocar o cadastro de quem já existe só um administrador, pela VOZ
        (acrescentar um jeito novo ao próprio rosto, como óculos, a própria pessoa pode, se a voz dela for reconhecida)."""
        existe = nome.lower() in {n.lower() for n in self._nomes_conhecidos()}
        q = (self.quem_agora or "").lower()
        return (not existe) or q in self.administradores() or (adicionar and q == nome.lower())

    def cadastrar_rosto_por_voz(self, nome, meu=False, adicionar=False):
        """"Cadastre o rosto dela": descobre QUEM é (pergunta, se faltar), pede para a pessoa chegar perto e grava só o rosto dela,
        mesmo com outra pessoa já reconhecida no quadro, de vários jeitos (de frente, de lado, com e sem óculos). Fica em dados/rostos.json."""
        v = self.visao
        if v is None:
            return "A câmera não está disponível."
        nome = self.resolver_nome(nome) if nome else ((self.pessoa or "") if meu else "")
        if not nome:
            self.aguardando_rosto = time.time() + 45
            faces = v.ultimo.get("faces", [])
            conhecidos = [f["nome"] for f in faces if f["nome"]]
            if conhecidos and any(not f["nome"] for f in faces):
                return f"Estou vendo {conhecidos[0]} e mais uma pessoa que eu não conheço. Quem é essa pessoa? Me diz o nome dela."
            return "Quem é essa pessoa? Me diz o nome dela."
        self.aguardando_rosto = None
        if not self.pode_recadastrar(nome, adicionar):
            return f"O rosto de {nome} já está cadastrado, e para trocá-lo eu preciso reconhecer a voz de um administrador."
        self.aviso(f"Certo! {nome}, chegue mais perto da câmera e olhe de frente para ela. Pode ficar de óculos ou sem, eu aprendo você de vários jeitos. "
                   "Vou pedir para virar um pouquinho a cabeça.")
        try:
            n = v.cadastrar(nome, ao_instrucao=self.aviso, adicionar=adicionar)
        except Exception as e:
            if type(e).__name__ == "ConflitoRosto":
                outro = e.args[0]
                return (f"Opa, esse rosto já está cadastrado como {outro}. Se quem está na câmera não é {outro}, o cadastro do {outro} está errado: "
                        f"peça para o {outro} recadastrar o rosto dele.")
            if isinstance(e, RuntimeError):
                return f"Não consegui: {e}."
            raise
        self.cfg.setdefault("pessoas", {}).setdefault(nome, {"tratamento": ""})     # passa a ser uma pessoa que o Klaus conhece (cumprimenta, etc.)
        config.salvar(self.cfg)
        self.ouvido.definir_nomes(self._nomes_conhecidos())
        self.ponte.emitir(tipo="info", **self.info())
        return (f"Pronto, {nome}! Guardei o seu rosto de vários ângulos, com e sem óculos, e fica salvo mesmo se o computador desligar. "
                "Agora reconheço você assim.")

    def _identificar_fala(self, audio, quem, sim, chamou, em_conversa, comando, texto):
        """Decide QUEM falou (pela voz, com a câmera de apoio) e liga o modo sério quando é um desconhecido. Devolve True se a fala foi consumida."""
        self.quem_agora = quem                                      # só a VOZ autoriza coisas sensíveis (administrador)
        longa = len(audio) >= 16000 * 1.2
        cam = self._pessoa_da_camera()
        melhor = getattr(self.locutor, "ultimo_melhor", (None, 0.0))
        # a voz QUASE bateu com alguém e a câmera vê só essa pessoa: é ela (resfriado, microfone ruim...), sem virar administrador por isso
        apoio = cam if (not quem and longa and melhor[0] and melhor[1] >= 0.28 and cam == melhor[0]) else None
        if quem:
            self.pessoa = quem
            if self.visao:
                self.visao.definir_voz(quem)
        elif apoio:
            self.pessoa = apoio
        elif self.locutor.vozes and longa:
            self.pessoa = None                  # fala longa que não bate com NINGUÉM: não herda a identidade de quem falava antes
        elif chamou and not em_conversa:
            self.pessoa = cam                   # fala curta demais para julgar: usa o rosto, se a câmera vê UMA pessoa conhecida
        self.ponte.emitir(tipo="usuario", texto=texto, pessoa=self.pessoa, similaridade=round(sim, 2))
        self.ponte.emitir(tipo="info", **self.info())
        if self.desconhecido and (quem or apoio):
            self._reconhecido(quem or apoio)                        # a voz (ou voz + câmera) bateu: acabou o modo sério
        elif self.desconhecido and self.desconhecido["fase"] == "perguntando" and not self.pendente:
            return bool(self._responder_quem_e(comando or texto))
        elif self._deve_perguntar(quem, sim, len(audio) / 16000):
            self._perguntar_quem(comando)
            return True
        return False

    # ---------------------------------------------------- voz desconhecida: modo sério e "quem é você?"
    NAO_NOME = {"cancela", "cancelar", "esquece", "deixa", "chega", "klaus", "oi", "ola", "ei", "sim", "nao", "bom", "boa", "dia", "tarde", "noite", "eu", "quem", "aqui", "um", "uma",
                "o", "a", "ok", "tchau", "obrigado", "obrigada", "valeu", "opa", "tudo", "bem"}

    def _extrair_nome(self, texto):
        """Primeiro nome dito em respostas como "Sou o Carlos", "Me chamo Ana" ou só "Pedro"."""
        t = texto.strip(" .!?,")
        m = re.search(r"(?:ela se chama|ele se chama|se chama|chama-se|o nome (?:dela|dele) (?:\u00e9|e)|(?:ela|ele|essa pessoa|esse cara) (?:\u00e9|e)(?: o| a)?|(?:\u00e9|e) (?:a|o)|me chamo|meu nome (?:\u00e9|e)|pode me chamar de|me chama de|eu sou(?: o| a)?|sou(?: o| a)?|aqui (?:\u00e9|e)(?: o| a)?)\s+([A-Za-z\u00c0-\u00ff]+)", t, re.I)
        nome = m.group(1) if m else None
        if not nome:
            ps = t.split()
            if 1 <= len(ps) <= 2 and all(p[:1].isupper() for p in ps):
                nome = ps[0]
        if not nome or norm(nome) in self.NAO_NOME:
            return None
        return nome[:1].upper() + nome[1:]

    def _nome_conhecido(self, nome):
        return nomes.resolver(nome, self.cfg.get("pessoas", {}))

    def _deve_perguntar(self, quem, sim, dur=9.0):
        """Há vozes cadastradas, esta não bate com nenhuma (e a fala é longa o bastante para julgar): é um desconhecido."""
        return (not quem and not self.pessoa and sim > 0.0 and dur >= 1.2 and bool(self.locutor.vozes) and not self.pendente and not self.cad_voz
                and not self.desconhecido and time.time() > self._nao_perguntar_ate)

    def _perguntar_quem(self, comando, motivo="voz"):
        """Desconhecido: liga o modo sério (núcleo vermelho, voz grave) e pergunta quem é. O pedido dele fica guardado para depois."""
        self.desconhecido = {"fase": "perguntando", "tent": 0, "comando": (comando or "").strip()}
        if not self.agressivo and self.modo != "serio":
            self.definir_modo("serio", auto=True)
        print("[klaus] voz desconhecida: modo sério e pergunta quem é")
        if motivo == "rosto":
            self._falar_agora("Que porra, tem um desconhecido na câmera. Quem caralho é você?" if self.agressivo
                              else "Atenção: há uma pessoa na câmera que eu não reconheço. Identifique-se, por favor: quem é você?")
        else:
            self._falar_agora("Que porra, não reconheci essa voz. Quem caralho é você?" if self.agressivo
                              else "Não reconheci a sua voz. Identifique-se, por favor: quem está falando?")

    def _reconhecido(self, nome):
        """Reconheceu a pessoa (pela voz ou pela câmera): sai do modo sério automático e volta ao normal."""
        self.desconhecido, self.oferta_voz = None, None
        self.restaurar_modo_auto()
        self.ponte.emitir(tipo="info", **self.info())
        self._falar_agora(f"Reconheci você, {nome}!" + (" Voltei ao modo normal, rá!" if self.modo == "normal" else ""))

    def _responder_quem_e(self, texto):
        """A fala é a resposta a "quem é você?". Devolve True se consumiu a fala."""
        d = self.desconhecido
        nome = self._extrair_nome(texto)
        if not nome:
            d["tent"] += 1
            if len(texto.split()) > 2:
                d["comando"] = texto
            if d["tent"] >= 2:                                  # não quer se identificar: segue como visitante, em modo normal
                self.desconhecido = None
                self._nao_perguntar_ate = time.time() + 600
                self.restaurar_modo_auto()
                self._falar_agora("Tudo bem, sigo te ajudando como visitante.")
                if d["comando"]:
                    self.processar(d["comando"])
                return True
            self._falar_agora("Antes de continuar, me diz só o seu nome, por favor.")
            return True
        conhecido = self._nome_conhecido(nome)
        if conhecido and conhecido.lower() in self.administradores() and self._pessoa_da_camera() != conhecido:
            # só dizer o nome NÃO vale como administrador: continua em modo sério até a voz bater (ou a câmera ver só o rosto dele)
            d["fase"] = "aguardando_voz"
            self._nao_perguntar_ate = time.time() + 600
            self._falar_agora(f"Reconheço o nome, {conhecido}, mas a voz não bate com a que eu guardei. Por segurança sigo no modo sério. "
                              "Fale de novo, pertinho do microfone, que eu confirmo.")
            return True
        self.desconhecido = None
        self.pessoa = conhecido or nome
        self.restaurar_modo_auto()                              # reconhecido: volta ao modo normal
        self.ponte.emitir(tipo="info", **self.info())
        if conhecido:
            self._falar_agora(f"Opa, {conhecido}! Agora sim, te achei. Só não reconheci a sua voz direito, então ações no computador ficam para quando eu te reconhecer de verdade.")
        else:
            self._falar_agora(f"Prazer, {nome}!")
        if d["comando"]:
            self.processar(d["comando"])
        if not conhecido:
            self.oferta_voz = nome
            self._falar_agora(f"Quer que eu guarde a sua voz, {nome}, para te reconhecer da próxima vez?")
        return True

    def _responder_oferta_voz(self, texto):
        """"Quer que eu guarde a sua voz?" -> sim/não. Devolve True se consumiu a fala."""
        r = resposta_confirmacao(texto)
        nome, self.oferta_voz = self.oferta_voz, None
        if r == "sim":
            self._falar_agora(self.iniciar_cadastro_voz(nome))
            return True
        if r == "nao":
            self._falar_agora("Tranquilo! Se mudar de ideia, é só dizer: cadastra minha voz.")
            return True
        return False

    # ------------------------------------------------------------- memória de vozes e rostos
    def efeito_voz(self, efeito):
        if not hasattr(self.voz, "tom"):
            return "Com essa voz eu não consigo mudar o timbre. Mas imito o jeito de falar mesmo assim!"
        tom, ritmo = {"fina": (1.28, 1.0), "grossa": (0.8, 1.0), "rapida": (1.0, 1.35), "lenta": (1.0, 0.82)}.get(efeito, (1.0, 1.0))
        self.voz.tom, self.voz.ritmo_extra = tom, ritmo
        return "Voz de volta ao normal." if efeito == "normal" else f"Voz {efeito} ligada."

    def _falar_agora(self, msg):
        with self.trava:
            self.dizer(msg)
            self.ponte.emitir(tipo="klaus", texto=msg)
            self.falante.esperar()
            self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
            self.ponte.definir_estado("LISTENING")

    def iniciar_cadastro_voz(self, nome, trat=""):
        """Começa a coletar a voz de `nome`: as próximas falas viram amostras (a lógica está em _coletar_voz). Fica salvo em dados/vozes.json."""
        nome = (nome or self.pessoa or "").strip()
        if not nome:
            return "Claro! Qual é o nome de quem vai cadastrar a voz?"
        nome = self.resolver_nome(nome)
        existente = nome.lower() in {n.lower() for n in self.locutor.vozes}
        if (existente or nome.lower() in self.administradores()) and self.locutor.vozes and (self.quem_agora or "").lower() not in self.administradores():
            admin.auditar("cadastrar_voz", nome, "NEGADO: quem pediu não é administrador", self.pessoa)
            return f"Para mexer na voz de {nome} eu preciso reconhecer a voz de um administrador, e não reconheci a sua."
        alvo = 6
        self.cad_voz = {"nome": nome, "trat": (trat or "").strip(), "audios": [], "alvo": alvo, "expira": time.time() + 240}
        self.conversa_ate = time.time() + 240
        return (f"Vamos lá, {nome}! Vou ouvir {alvo} falas suas. Fale normalmente umas duas frases por vez, tipo contar o que você fez hoje, "
                "e eu aviso quando acabar. Pode começar. Para cancelar, diga cancelar.")

    def _coletar_voz(self, audio, texto):
        """Recebe uma fala do cadastro de voz. Devolve True se a fala foi consumida."""
        cv = self.cad_voz
        if time.time() > cv["expira"]:
            self.cad_voz = None
            return False
        if re.search(r"\bcancel\w+|\besque[cç]\w+|\bdeixa pra l[aá]\b", texto.lower()) or comando_parar(texto):
            self.cad_voz = None
            self._falar_agora("Beleza, cancelei o cadastro da voz.")
            return True
        if len(audio) < 16000 * 1.8:
            self._falar_agora("Essa foi curtinha! Fala um pouquinho mais, umas duas frases.")
            return True
        cv["audios"].append(audio)
        n = len(cv["audios"])
        if n < cv["alvo"]:
            self._falar_agora(random.choice([f"Boa! {n} de {cv['alvo']}. Continua.", f"Isso! Já foram {n}. Mais uma.", f"Perfeito, {n} de {cv['alvo']}. Segue falando."]))
            return True
        self.cad_voz = None
        nome = cv["nome"]
        try:
            self.locutor.cadastrar(nome, cv["audios"])
            info = self.cfg.setdefault("pessoas", {}).setdefault(nome, {"tratamento": ""})
            if cv["trat"]:
                info["tratamento"] = cv["trat"]
            config.salvar(self.cfg)
            self.ouvido.definir_nomes(self._nomes_conhecidos())
            self.pessoa = nome
            if self.visao:
                self.visao.definir_voz(nome)      # o rosto de quem acabou de falar também é guardado sozinho
            self.ponte.emitir(tipo="info", **self.info())
            msg = (f"Pronto, {nome}! Guardei a sua voz no disco, então vou te reconhecer sempre, até depois de reiniciar o computador. "
                   + ("Fica na frente da câmera um instante que eu guardo o seu rosto também!" if self.visao and nome not in self.visao.rostos else "Agora você não escapa de mim, rá!"))
        except Exception as e:
            print(f"[cadastro de voz] erro: {e}")
            msg = erro_humano(self.modo == "serio", self.agressivo, " Não consegui guardar a voz.")
        self._falar_agora(msg)
        return True

    def _pessoa_da_camera(self):
        """Quem a câmera está vendo agora (só se for UMA pessoa conhecida)."""
        v = self.visao
        if not v or not v.acompanhando:
            return None
        pes = v.ultimo.get("pessoas", [])
        return pes[0] if len(pes) == 1 and pes[0] and pes[0] != "?" else None     # só vale com UM rosto no quadro, e conhecido

    def _vigia_rostos(self):
        """Ao ligar o PC (ou quando alguém chega), reconhece o rosto pela câmera e cumprimenta pelo nome."""
        visto, seguidos, ausencia = {}, {}, {}
        desc_seg, ult_aviso = 0, 0.0
        while True:
            time.sleep(1.0)
            try:
                v = self.visao
                if not v.acompanhando or not self.cfg.get("cumprimentar_pelo_rosto", True):
                    seguidos.clear()
                    continue
                agora = time.time()
                vistos = {n for n in v.ultimo.get("pessoas", []) if n and n != "?"}
                for n in list(seguidos):
                    if n not in vistos:
                        seguidos.pop(n)
                for n in vistos:
                    if n not in seguidos:
                        ausencia[n] = agora - visto.get(n, 0.0)          # há quanto tempo não via essa pessoa
                    seguidos[n] = seguidos.get(n, 0) + 1
                    visto[n] = agora
                    if seguidos[n] != 3 or ausencia.get(n, 0) < 1200:
                        continue
                    est = config.estado()
                    if agora - est.get("cumprimentou", {}).get(n, 0) < 1200:
                        continue
                    if self.trava.locked() or self.falante.mudo or self.cad_voz or self.desconhecido or agora < self.conversa_ate:
                        seguidos[n] = 0                                    # ocupado: tenta de novo daqui a pouco
                        continue
                    est.setdefault("cumprimentou", {})[n] = agora
                    config.salvar_estado(est)
                    self._cumprimentar_rosto(n)
                # --- olhos atentos: alguém que eu não conheço apareceu na frente da câmera (rosto grande e estável por ~6 s)
                faces = v.ultimo.get("faces", [])
                desc_seg = desc_seg + 1 if any(f["nome"] is None and f["tam"] >= 0.12 for f in faces) else 0
                ocioso = not (self.trava.locked() or self.falante.mudo or self.cad_voz or self.desconhecido or agora < self.conversa_ate or self.pendente)
                if desc_seg >= 6 and ocioso and self.cfg.get("avisar_desconhecido_na_camera", True) and agora - ult_aviso > 1800:
                    ult_aviso, desc_seg = agora, 0
                    self.interrompido = False
                    if vistos:      # o dono está lá com uma visita: só avisa, sem interrogar
                        with self.trava:
                            self.dizer("Opa, tem uma pessoa aí com você que eu ainda não conheço.")
                            self.falante.esperar()
                    else:           # um desconhecido sozinho na frente do computador: modo sério e pergunta quem é
                        self._perguntar_quem("", motivo="rosto")
            except Exception as e:
                print(f"[rostos] erro no vigia: {e}")

    def _cumprimentar_rosto(self, nome):
        trat = self.cfg.get("pessoas", {}).get(nome, {}).get("tratamento", "")
        p = periodo()
        if self.agressivo:
            texto = f"{p}, {nome}. Já te reconheci pela câmera, porra."
        elif self.modo == "serio":
            texto = f"{p}, {trat + ' ' if trat else ''}{nome}. Reconheci o senhor pela câmera." if trat == "senhor" else f"{p}, {nome}. Reconheci você pela câmera."
        else:
            texto = random.choice([f"{p}, {nome}! Te reconheci pela câmera. Que bom te ver!", f"Opa, {nome}! Vi você aí pela câmera, rá. {p}!",
                                   f"E aí, {nome}! {p}! Reconheci essa carinha de longe."])
        with self.trava:
            self.interrompido = False
            if not self.pessoa:
                self.pessoa = nome
            self.dizer(texto)
            self.ponte.emitir(tipo="klaus", texto=texto)
            self.ponte.emitir(tipo="info", **self.info())
            if self.pessoa == nome:
                self._clima_do_dia()
            self.falante.esperar()

    # -------------------------------------------------------------- interface
    def info(self):
        v = getattr(self, "voz", None)
        return {"pessoa": self.pessoa, "cidade": self.cfg.get("cidade", ""),
                "voz": "clone" if v is not None and v.taxa == 24000 else "vits",
                "pessoas": list(self.cfg.get("pessoas", {})), "atalhos": list(self.cfg.get("atalhos", {})),
                "modo": getattr(self, "modo", "normal"),
                "agressivo": bool(getattr(self, "agressivo", False)),
                "camera": bool(getattr(getattr(self, "visao", None), "acompanhando", False)),
                "camera_ok": getattr(self, "visao", None) is not None}

    def _falando(self, sim):
        self.ponte.definir_estado("SPEAKING" if sim else ("LISTENING" if time.time() < self.conversa_ate else "IDLE"))

    def definir_modo(self, modo, auto=False):
        """Troca o modo: núcleo vermelho, voz mais grave e respostas mais formais (ou volta ao normal)."""
        agressivo = modo == "agressivo"
        modo = "serio" if modo in ("serio", "agressivo") else "normal"
        if modo == self.modo and agressivo == self.agressivo:
            return
        self.agressivo = agressivo
        self.cerebro.agressivo = agressivo
        self.modo = modo
        self._serio_auto = bool(auto and modo == "serio")
        if self.voz is not None:
            self.voz.serio = modo == "serio"
        self.cerebro.serio = modo == "serio"
        self.ponte.emitir(tipo="info", **self.info())

    def mudar_modo(self, valor):                       # botão da interface
        self.definir_modo(valor)

    def restaurar_modo_auto(self):
        """Se o modo sério foi ligado sozinho (tarefa séria) e nada está pendente, volta ao normal."""
        if self._serio_auto and not self.pendente and not self.agressivo and not self.desconhecido:
            self.definir_modo("normal")

    def encerrar_klaus(self):
        """Fecha o Klaus depois de falar a despedida (espera a fala em andamento terminar)."""
        def sair():
            time.sleep(0.5)
            with self.trava:
                self.falante.esperar()
            admin.auditar("encerrar_klaus", "", "encerrado por voz", self.pessoa)
            if self.ao_encerrar:
                self.ao_encerrar()
            else:
                os._exit(0)
        threading.Thread(target=sair, daemon=True).start()

    def administradores(self):
        return [a.lower() for a in (self.cfg.get("administradores") or [])]

    def propor(self, nome, descricao, comando, ok=None):
        """Uma ação que ALTERA o computador: guarda, lê em voz alta e espera o "confirmo" do administrador (vale por 30 s)."""
        if (self.pessoa or "").lower() not in self.administradores():
            admin.auditar(nome, comando, "NEGADO: voz não é de administrador", self.pessoa)
            return f"Só {', '.join(self.cfg.get('administradores') or ['o administrador'])} pode autorizar ações no computador, e eu não reconheci a sua voz."
        self.pendente = {"nome": nome, "descricao": descricao, "comando": comando, "ok": ok, "expira": time.time() + 30}
        if self.modo != "serio":
            self.definir_modo("serio", auto=True)
        self.ponte.emitir(tipo="klaus_parcial", texto=f"[ação pendente] {comando}")
        resumo = re.sub(r"\s+", " ", comando)[:130] if nome == "executar_powershell" else ""
        return (f"Vou {descricao}. Isso altera o computador." + (f" O comando é: {resumo}." if resumo else "")
                + " Diga: eu confirmo, para eu executar, ou cancela.")

    def responder_confirmacao(self, r, quem, curto=False):
        """`quem` = quem a VOZ DESTA FALA identificou (não a pessoa guardada da conversa): só um administrador confirma."""
        if r == "sim" and not quem and curto and self.pendente:      # fala curta demais para reconhecer a voz: pede de novo, sem descartar a ação
            self.pendente["expira"] = time.time() + 30
            with self.trava:
                msg = "Essa fala foi curta demais para eu reconhecer a sua voz. Diga com calma: eu confirmo."
                self.dizer(msg)
                self.ponte.emitir(tipo="klaus", texto=msg)
                self.falante.esperar()
                self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
                self.ponte.definir_estado("LISTENING")
            return
        pend, self.pendente = self.pendente, None
        with self.trava:
            self.ponte.definir_estado("PROCESSING")
            if r == "nao":
                msg = "Cancelado. Não alterei nada."
            elif (quem or "").lower() not in self.administradores():
                admin.auditar(pend["nome"], pend["comando"], f"NEGADO: a voz da confirmação não é de administrador ({quem})", self.pessoa)
                msg = "Não reconheci a sua voz como administrador, então não executei."
            else:
                try:
                    codigo, saida = admin.executar_powershell(pend["comando"])
                    admin.auditar(pend["nome"], pend["comando"], f"código {codigo}: {saida[:400]}", self.pessoa)
                    msg = pend.get("ok") if codigo == 0 and pend.get("ok") else admin.resumo_saida(codigo, saida)
                except subprocess.TimeoutExpired:
                    admin.auditar(pend["nome"], pend["comando"], "tempo esgotado", self.pessoa)
                    msg = "A ação demorou demais e foi interrompida."
                except Exception as e:
                    admin.auditar(pend["nome"], pend["comando"], f"erro: {e}", self.pessoa)
                    msg = erro_humano(self.modo == "serio", self.agressivo, f" Foi {type(e).__name__}.")
            self.dizer(msg)
            self.ponte.emitir(tipo="klaus", texto=msg)
            self.falante.esperar()
            self.restaurar_modo_auto()
            self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
            self.ponte.definir_estado("LISTENING")

    def anunciar_fundo(self, texto):
        """Uma tarefa em segundo plano terminou: espera o Klaus ficar livre e fala o resultado."""
        def falar():
            t = time.time()
            while time.time() - t < 120 and (self.trava.locked() or self.falante.mudo):
                time.sleep(1)
            self.interrompido = False
            self.dizer(texto)
            self.ponte.emitir(tipo="klaus", texto=texto)
        threading.Thread(target=falar, daemon=True).start()

    def aviso(self, texto):
        self.dizer(texto)
        self.falante.esperar()

    def ligar_camera(self, ligar):
        if self.visao is None:
            return
        self.visao.acompanhar(ligar)
        est = config.estado()                                  # "só para de olhar se eu pedir": o pedido vale também depois de reiniciar
        est["olhos_fechados"] = not ligar
        config.salvar_estado(est)
        self.ponte.emitir(tipo="info", **self.info())

    def niveis(self):
        ouvido, falante = getattr(self, "ouvido", None), getattr(self, "falante", None)
        return (ouvido.nivel if ouvido else 0.0), (falante.repro.nivel if falante else 0.0), (falante.repro.zcr if falante else 0.2)

    def clima_atual(self):
        if not self.cfg.get("cidade"):
            return None
        return clima.dados(self.cfg["cidade"], self.cfg.get("pais", "BR"))

    def abrir_atalho(self, nome):
        cmd = self.cfg.get("atalhos", {}).get(nome)   # só abre o que está na lista do config.json
        if cmd:
            os.startfile(cmd)

    def marcar_tarefa(self, id_, feito):
        tarefas.marcar(id_, feito)
        self.ponte.emitir(tipo="tarefas", lista=tarefas.todas())

    def parar_fala(self):
        self.interromper()

    def interromper(self):
        """Para na hora: corta a voz, cancela a resposta do modelo e volta a ouvir."""
        self.interrompido = True
        self.cerebro.cancelar()
        self.falante.parar()
        self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
        self.ponte.emitir(tipo="klaus_parcial", texto="(interrompido)")
        self.ponte.emitir(tipo="trabalho", ativo=False)
        self.ponte.definir_estado("LISTENING")

    def mudar_voz(self, calado):
        self.falante.calado = calado
        if calado:
            self.falante.parar()

    def limpar(self):
        self.cerebro.esquecer()

    def ouvir_agora(self):
        """Botão do microfone: escuta sem precisar dizer "Klaus"."""
        self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
        self.ponte.definir_estado("LISTENING")

    # ------------------------------------------------------------------ falas
    def dizer(self, texto):
        if self.interrompido:            # depois de um "parar" ele fica quieto até o próximo comando
            return
        self._falas += 1
        print(f"Klaus: {texto}")
        self.ponte.emitir(tipo="klaus_parcial", texto=texto)
        self.falante.dizer(texto)

    RESMUNGOS = ["Puta que pariu, de novo isso?", "Que merda, hein.", "Caralho, nem me deixam em paz.", "Porra, tá bom, eu faço.",
                 "Que saco, senhor.", "Ah, que inferno.", "Puta merda, lá vamos nós.", "Droga de vida, mas tá."]
    RESMUNGOS_FIM = ["Pronto, essa porra tá feita.", "Tá aí, caralho.", "Feito, e sem agradecer, hein.", "Satisfeito agora, porra?"]

    def revoltado(self, texto, fim=False):
        """No modo agressivo, até as respostas diretas (hora, clima, saudação) vêm com mau humor e palavrão. A tarefa é feita do mesmo jeito."""
        if not self.agressivo:
            return texto
        import random
        lista = self.RESMUNGOS_FIM if fim else self.RESMUNGOS
        return f"{random.choice(lista)} {texto}" if not fim else f"{texto} {random.choice(lista)}"

    def saudar(self):
        p = periodo()
        info = self.cfg["pessoas"].get(self.pessoa, {}) if self.pessoa else {}
        if self.pessoa:
            trat = info.get("tratamento", "")
            texto = (f"{p}, {trat + ' ' if trat else ''}{self.pessoa}. " +
                     ("Às suas ordens." if self.modo == "serio" else "Bora fazer o quê hoje? Rá rá." if trat == "senhor" else "E aí, o que vamos aprontar hoje?"))
        elif config.vozes():
            texto = f"{p}! Não reconheci a sua voz, mas posso ajudar. O que você precisa?"
        else:
            texto = f"{p}, senhor. O que vamos fazer hoje?"
        if self.agressivo:
            import random
            texto = random.choice([f"{p}... que bosta de hora pra me acordar, {trat if self.pessoa and trat else 'senhor'}. Mas fala logo, porra, o que você quer?",
                                   f"{p}, caralho. Já vai começar a me encher o saco? Fala aí, que eu faço, mas reclamando.",
                                   f"Puta que pariu, {p.lower()}. Que que é agora, {trat if self.pessoa and trat else 'senhor'}? Manda logo essa merda."])
        self.dizer(texto)
        self.ponte.emitir(tipo="klaus", texto=texto)
        self._clima_do_dia()

    def _clima_do_dia(self):
        """Na primeira saudação do dia de cada pessoa, conta o clima (se tiver cidade e internet)."""
        if not self.cfg.get("cidade"):
            return
        est = config.estado()
        chave = f"{date.today()}:{self.pessoa or 'x'}"
        if est.get("clima") == chave:
            return
        try:
            r = clima.resumo(self.cfg["cidade"], self.cfg.get("pais", "BR"))
            self.dizer(r)
            self.ponte.emitir(tipo="klaus", texto=r)
            est["clima"] = chave
            config.salvar_estado(est)
        except Exception as e:
            print(f"[clima] indisponível: {e}")

    def processar(self, comando):
        """Roda o comando mostrando na interface que está trabalhando e, se demorar, avisando por voz que continua nessa."""
        ini = time.time()
        self.ouvido.ocupado = True                            # enquanto trabalha, continua ouvindo "para"/"chega"/"cancela"
        self._t_trabalho, self.atividade = ini, "pensando na resposta"
        fim = threading.Event()
        self.ponte.emitir(tipo="trabalho", ativo=True, texto="Pensando na resposta...", inicio=int(ini * 1000))
        threading.Thread(target=self._vigia_trabalho, args=(fim, ini), daemon=True).start()
        try:
            self._processar(comando)
        finally:
            fim.set()
            self.ouvido.ocupado = False
            self._t_trabalho = 0.0
            self.cerebro.pensando = False
            self.ponte.emitir(tipo="trabalho", ativo=False)

    def _processar(self, comando):
        """Pergunta ao cérebro e fala a resposta (serializado: voz e interface não se atropelam).
        Garante o retorno: se o Klaus disse "vou verificar", ele volta com o resultado (ou diz que não conseguiu)."""
        with self.trava:
            self.ponte.definir_estado("PROCESSING")
            self._falas = 0
            fila = []

            def liberar():          # voz na GPU: espera o LLM parar para ter a placa só para ela (sem cortes)
                while fila:
                    self.dizer(fila.pop(0))
            fala = fila.append if self.voz_na_gpu else self.dizer     # voz na CPU: fala cada frase assim que ela fica pronta
            novo_modo = rota_modo(comando)
            if novo_modo:                                   # "modo sério" / "modo normal" por voz
                self.definir_modo(novo_modo)
                msg = ("Modo agressivo ligado. Que porra vamos destruir agora?" if novo_modo == "agressivo"
                       else "Modo sério ativado. Pode falar, senhor, que eu resolvo." if novo_modo == "serio" else "Opa, voltei ao modo normal! Rá rá, já tava ficando sério demais. Bora?")
                self.dizer(msg)
                self.ponte.emitir(tipo="klaus", texto=msg)
                self.falante.esperar()
                self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
                self.ponte.definir_estado("LISTENING")
                return
            rota = rota_de_voz(comando)
            if rota is None:
                rota = self.cerebro.rota_contextual(comando)         # "e o próximo jogo?": continua o assunto da conversa
            if rota and rota[0] in ("diagnostico_computador", "varredura_profunda") and self.modo != "serio":
                self.definir_modo("serio", auto=True)       # varredura de segurança é assunto sério: voz e núcleo mudam sozinhos
            if rota and rota[0] in ("ver_ambiente", "varredura_ambiente", "quem_esta_na_camera", "cadastrar_rosto") and self.visao is None:
                rota = None
            try:
                if rota and rota[0] == "pesquisar_web":             # pesquisa: pesquisa, resume e fala (sem "aguarde" solto)
                    resposta = self.cerebro.responder_pesquisa(self.pessoa, comando, rota[1]["consulta"], fala, liberar)
                    liberar()
                    self.ponte.emitir(tipo="klaus", texto=resposta)
                elif rota and rota[0] in ROTAS_DADOS:               # futebol e notícias: dados reais, falados
                    resposta = self.cerebro.responder_dados(self.pessoa, comando, rota, fala, liberar)
                    liberar()
                    self.ponte.emitir(tipo="klaus", texto=resposta)
                elif rota:                                          # clima, hora e câmera: executa direto
                    res = self.revoltado(self.cerebro.executar_rota(self.pessoa, comando, rota[0], rota[1]))
                    self.dizer(res)
                    self.ponte.emitir(tipo="klaus", texto=res)
                else:
                    resposta = self.cerebro.responder(self.pessoa, comando, fala, liberar)
                    liberar()
                    self.ponte.emitir(tipo="klaus", texto=resposta)
            except Exception as e:
                print(f"[cérebro] erro: {e}")
                msg = erro_humano(self.modo == "serio", self.agressivo)
                self.dizer(msg)
                self.ponte.emitir(tipo="klaus", texto=msg)
            if self._falas == 0 and not self.interrompido:          # rede de segurança: nunca termina em silêncio
                msg = erro_humano(self.modo == "serio", self.agressivo)
                self.dizer(msg)
                self.ponte.emitir(tipo="klaus", texto=msg)
            self.falante.esperar()
            self.restaurar_modo_auto()
            self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
            self.ponte.definir_estado("LISTENING")

    # ------------------------------------------------------------------ laço
    def correr(self):
        print("[klaus] pronto. Diga \"Klaus\".")
        self.ponte.definir_estado("IDLE")
        while True:
            em_conversa = time.time() < self.conversa_ate
            audio = self.ouvido.escutar(espera_max=(self.conversa_ate - time.time()) if em_conversa else None)
            if audio is None:            # a conversa esfriou
                self.conversa_ate, self.pessoa = 0.0, None
                self.cerebro.esfriou()                         # esfriou, mas o assunto continua na memória por 20 minutos
                self.ponte.definir_estado("IDLE")
                self.ponte.emitir(tipo="info", **self.info())
                print("[klaus] voltou a esperar pelo nome.")
                self.desconhecido, self.oferta_voz = None, None
                self.restaurar_modo_auto()
                if hasattr(self.voz, "tom"):
                    self.voz.tom, self.voz.ritmo_extra = 1.0, 1.0       # acabou a imitação
                continue
            texto = self.ouvido.transcrever(audio)
            if not texto:
                continue
            texto = nomes.corrigir(texto, self._nomes_conhecidos())    # "Raphael" -> "Rafael"
            if self.cad_voz and self._coletar_voz(audio, texto):     # cadastro de voz em andamento: a fala vira amostra
                continue
            if self.ouvido.clipou and time.time() > self._aviso_estouro:
                self._aviso_estouro = time.time() + 600
                print("[ouvido] o microfone está estourando (clipping)")
                self._falar_agora("Rá, você tá estourando o microfone! Abaixa o volume de entrada do microfone no Windows, ou fala um pouco mais longe, que eu entendo melhor.")
            chamou, comando = separar(texto, self.cfg["palavra_de_ativacao"])
            if not em_conversa and not chamou:
                continue
            if comando and comando_parar(comando) and not self.falante.mudo:   # "parar" sem nada falando: só segue ouvindo
                self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
                self.ponte.definir_estado("LISTENING")
                continue
            self.interrompido = False                                          # um comando novo: pode voltar a falar
            quem, sim = self.locutor.identificar(audio, self.cfg["limiar_voz"])
            print(f"[ouvi] \"{texto}\"  voz={quem or '?'} ({sim:.2f})")
            if self._identificar_fala(audio, quem, sim, chamou, em_conversa, comando, texto):
                continue
            if self.oferta_voz and not self.desconhecido and self._responder_oferta_voz(comando or texto):
                continue
            if self.aguardando_rosto:                               # "quem é essa pessoa?": a fala é o nome de quem vai ser cadastrado
                if time.time() > self.aguardando_rosto:
                    self.aguardando_rosto = None
                elif re.search(r"cancel|esquec|deixa|nao precisa", norm(comando or texto)):
                    self.aguardando_rosto = None
                    self._falar_agora("Beleza, deixei pra lá o cadastro.")
                    continue
                else:
                    nome_r = self._extrair_nome(comando or texto)
                    if nome_r:
                        self.aguardando_rosto = None
                        self.processar(f"cadastre o rosto de {nome_r}")
                        continue

            if self.pendente:                                       # há uma ação esperando confirmação
                if time.time() > self.pendente["expira"]:
                    self.pendente = None
                else:
                    r = resposta_confirmacao(comando or texto)
                    if r:
                        self.responder_confirmacao(r, quem, curto=(sim == 0.0))
                        continue
                    self.pendente = None                            # falou outra coisa: a ação pendente é descartada
                    self.restaurar_modo_auto()
            if chamou and not comando:
                self.ponte.definir_estado("PROCESSING")
                self.saudar()
                self.falante.esperar()
            elif any(norm(comando).startswith(d) for d in DESPEDIDAS):
                adeus = "Finalmente, porra. Tchau." if self.agressivo else "Às ordens." if self.cfg["pessoas"].get(self.pessoa, {}).get("tratamento") == "senhor" else "Até mais!"
                self.dizer(adeus)
                self.ponte.emitir(tipo="klaus", texto=adeus)
                self.falante.esperar()
                self.conversa_ate, self.pessoa = 0.0, None
                self.cerebro.esquecer()
                self.ponte.definir_estado("IDLE")
                continue
            else:
                self.processar(comando or texto)
                continue
            self.conversa_ate = time.time() + self.cfg["segundos_de_conversa"]
            self.ponte.definir_estado("LISTENING")


def _instancia_unica():
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8767))
    except OSError:
        print("[klaus] já existe um Klaus rodando. Feche a outra janela antes de abrir de novo.")
        raise SystemExit(1)
    return s   # mantém a porta presa enquanto este processo viver


def main():
    trava = _instancia_unica()  # noqa: F841
    Klaus_().correr()


if __name__ == "__main__":
    main()
