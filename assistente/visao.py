"""Visão do Klaus pela webcam: detectar e reconhecer rostos (OpenCV YuNet + SFace, local) e descrever o ambiente
(modelo de visão no Ollama, local). A câmera só fica ligada enquanto é usada (ou no modo "acompanhar")."""
import base64
import json
import threading
import time

from pathlib import Path

import numpy as np
import requests

from . import config

MODELOS = config.RAIZ / "modelos"
ARQ_ROSTOS = config.DADOS / "rostos.json"
LIMIAR = 0.363          # similaridade de cosseno recomendada pelo OpenCV para "mesma pessoa" (SFace)


class ConflitoRosto(Exception):
    """O rosto que está sendo cadastrado já é de outra pessoa cadastrada (args: nome da outra, similaridade)."""


class Visao:
    def __init__(self, cfg, ao_evento=None):
        import cv2
        self.cv2, self.cfg, self.ao_evento = cv2, cfg, ao_evento
        self.det = cv2.FaceDetectorYN.create(str(MODELOS / "yunet.onnx"), "", (320, 320), 0.80, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(str(MODELOS / "sface.onnx"), "")
        self.cam = None
        self.trava = threading.RLock()
        self.acompanhando = False
        self.olhando = False                 # True enquanto descreve o ambiente (os olhos 'focam')
        self.q_ultimo, self.q_t = None, 0.0   # último quadro lido (a câmera fica aberta no modo contínuo)
        self.voz_atual = (None, 0.0)          # quem a VOZ identificou por último (associa voz e rosto)
        self._auto = {}                       # amostras de rosto para o cadastro automático
        self.ao_cadastro = None               # chamado quando um rosto é cadastrado sozinho
        self.objetos, self.varrendo = [], False
        self._ult_nome = {}
        self._ult_aprendizado = 0.0
        self.quer_olhar = False               # a vontade do usuário: True = olhos abertos (o laço reabre a câmera sozinho se ela falhar)
        self.t_cena = 0.0                     # quando chegou o último quadro de verdade
        self._hist = {}                       # últimos nomes reconhecidos por rosto (votação: 2 de 3, para não confundir ninguém)
        self.detector, self.detector_tipo = None, None
        try:
            from .object_detector import ObjectDetector
            modelo = cfg.get("objetos_modelo", "modelos/yolo11m.pt")
            if not Path(modelo).is_absolute() and (config.RAIZ / modelo).exists():
                modelo = str(config.RAIZ / modelo)
            self.detector = ObjectDetector(modelo, confianca=cfg.get("objetos_confianca", 0.5), classes=cfg.get("objetos_classes"))
            self.detector_tipo = "yolo11"
            print(f"[visão] objetos: YOLO11 em {'GPU' if self.detector.dispositivo == 0 else 'CPU'}")
        except Exception as e:
            print(f"[visão] YOLO11 indisponível ({e}); tentando o detector reserva")
            try:
                from .objetos import Detector
                self.detector, self.detector_tipo = Detector(), "yolox"
            except Exception as e2:
                print(f"[visão] detector de objetos indisponível: {e2}")
        self.rostos = self._ler()
        self.ultimo = {"pessoas": [], "rostos": 0, "x": None, "y": None, "ambiente": ""}

    # ------------------------------------------------------------ cadastro em disco
    def _ler(self):
        try:
            return {n: [np.array(e, dtype=np.float32) for e in v] for n, v in config.ler_json(ARQ_ROSTOS, {}).items()}
        except Exception:
            return {}

    def _salvar(self):
        config.gravar_json(ARQ_ROSTOS, {n: [e.tolist() for e in v] for n, v in self.rostos.items()})

    # --------------------------------------------------------------------- câmera
    def _abrir(self):
        if self.cam is not None and self.cam.isOpened():
            return
        cv2 = self.cv2
        indices = [self.cfg["camera_indice"]] if self.cfg.get("camera_indice") is not None else [0, 1, 2, 3]
        for i in indices:
            cam = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cam.isOpened():
                cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                ok, quadro = cam.read()
                if ok and quadro is not None and quadro.std() > 1:       # ignora câmeras virtuais "pretas"
                    self.cam = cam
                    print(f"[visão] câmera {i} aberta ({quadro.shape[1]}x{quadro.shape[0]})")
                    return
            cam.release()
        raise RuntimeError("não encontrei uma webcam disponível")

    def fechar(self):
        with self.trava:
            if self.cam is not None:
                self.cam.release()
                self.cam = None

    def quadro(self, aquecer=0):
        with self.trava:
            self._abrir()
            for _ in range(aquecer):          # a câmera ajusta a exposição nos primeiros quadros
                self.cam.read()
            ok, q = self.cam.read()
            if not ok or q is None:
                self.fechar()
                raise RuntimeError("a câmera não entregou imagem")
            self.q_ultimo, self.q_t = q, time.time()
            return q

    def quadro_recente(self, max_idade=1.5, aquecer=4):
        """Usa o quadro que o modo contínuo acabou de ler (sem reabrir a câmera); senão capta um novo."""
        if self.acompanhando and self.q_ultimo is not None and time.time() - self.q_t < max_idade:
            return self.q_ultimo.copy()
        return self.quadro(aquecer=aquecer)

    def definir_voz(self, nome):
        """O Klaus avisa quem acabou de falar (pela voz): é assim que o rosto é associado ao nome."""
        self.voz_atual = (nome, time.time())

    # ------------------------------------------------------------------- rostos
    def detectar(self, quadro):
        h, w = quadro.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(quadro)
        return [] if faces is None else list(faces)

    def _quem(self, quadro, face):
        feat = self.rec.feature(self.rec.alignCrop(quadro, face)).flatten()
        feat = feat / (np.linalg.norm(feat) + 1e-9)
        nome, sim = self._decidir(feat, face)
        return nome, sim, feat

    def _decidir(self, feat, face=None):
        """Quem é este rosto? Só responde um nome se a semelhança passar do limiar, se for MUITO mais parecido com essa pessoa
        do que com as outras e se o rosto estiver nítido (pontuação do detector e tamanho). Na dúvida: desconhecido."""
        placar = {}
        for nome, embs in self.rostos.items():
            s = sorted((float(feat @ e) for e in embs), reverse=True)
            placar[nome] = float(np.mean(s[:3]))
        if not placar:
            return None, 0.0
        ordem = sorted(placar.items(), key=lambda kv: -kv[1])
        melhor, sim = ordem[0]
        folga = sim - ordem[1][1] if len(ordem) > 1 else 1.0
        nitido = True
        if face is not None and len(face) > 14:
            nitido = float(face[14]) >= 0.85 and float(face[2]) >= 60
        ok = sim >= self.cfg.get("rosto_limiar", 0.5) and folga >= 0.08 and nitido
        return (melhor if ok else None), sim

    def analisar(self, quadro, reconhecer=True, com_feat=False):
        """Lista de rostos (do maior para o menor): {nome, sim, cx, cy (0..1), tam}."""
        h, w = quadro.shape[:2]
        out = []
        for f in self.detectar(quadro):
            x, y, fw, fh = f[:4]
            nome, sim, feat = None, 0.0, None
            if reconhecer:
                nome, sim, feat = self._quem(quadro, f)
            r = {"nome": nome, "sim": round(sim, 2), "cx": float((x + fw / 2) / w), "cy": float((y + fh / 2) / h), "tam": float(fw / w)}
            if com_feat:
                r["_feat"] = feat
            out.append(r)
        return sorted(out, key=lambda r: -r["tam"])

    def _suavizar(self, gente):
        """Só dá o nome quando 2 dos últimos 3 reconhecimentos concordam (um quadro ruim não troca ninguém de lugar)."""
        from collections import Counter, deque
        for i, g in enumerate(gente):
            h = self._hist.setdefault(i, deque(maxlen=3))
            h.append(g["nome"])
            nome, k = Counter(h).most_common(1)[0]
            g["nome"] = nome if (nome and k >= 2) else None
            if g["nome"] != self._ult_nome.get(i):                     # log de calibração: ajuste "rosto_limiar" no config.json se precisar
                self._ult_nome[i] = g["nome"]
                print(f"[visão] rosto {i + 1}: {g['nome'] or 'desconhecido'} (similaridade {g['sim']})")
        for i in list(self._hist):
            if i >= len(gente):
                self._hist.pop(i)
        return gente

    def _ler_gente(self, quadros=3):
        """Lê alguns quadros e VOTA em quem está na câmera (o nome só vale se todos/2 de 3 concordam). Devolve (gente, último quadro)."""
        from collections import Counter
        q = self.quadro_recente()
        lidos = [self.analisar(q)]
        for _ in range(quadros - 1):
            time.sleep(0.3)
            q = self.quadro()
            lidos.append(self.analisar(q))
        n = sorted(len(g) for g in lidos)[len(lidos) // 2]
        gente = []
        for r in range(n):
            ref = dict(next(g[r] for g in lidos if len(g) > r))
            nome, k = Counter(g[r]["nome"] for g in lidos if len(g) > r).most_common(1)[0]
            ref["nome"] = nome if (nome and k >= 2) else None
            gente.append(ref)
        self._publicar(gente)
        return gente, q

    @staticmethod
    def _posicao(cx):
        return "no lado esquerdo da imagem" if cx < 0.37 else "no lado direito da imagem" if cx > 0.63 else "no centro da imagem"

    def _frase_pessoas(self, gente):
        """"Alex" / "Alex, no lado esquerdo da imagem, e uma pessoa que não reconheço, no lado direito da imagem"."""
        if not gente:
            return "ninguém"
        partes = []
        for g in sorted(gente, key=lambda g: g["cx"]):
            nome = g["nome"] or "uma pessoa que não reconheço"
            partes.append(f"{nome}, {self._posicao(g['cx'])}" if len(gente) > 1 else nome)
        return partes[0] if len(partes) == 1 else ", e ".join([", ".join(partes[:-1]), partes[-1]]) if len(partes) > 2 else " e ".join(partes)

    def _objetos_sem_pessoa(self, q):
        from .objetos import frase_objetos
        c = self.contar(self.detectar_objetos(q))
        c.pop("person", None)
        return frase_objetos(c)

    def quem_esta(self):
        """Frase falável: quem está na frente da câmera (pelo rosto) e que objetos há por perto."""
        gente, q = self._ler_gente(3)
        if not gente:
            return "Não estou vendo ninguém na câmera."
        frase = "Estou vendo " + self._frase_pessoas(gente) + "."
        desconhecidos = [g for g in gente if not g["nome"]]
        if desconhecidos:
            frase += (" Ainda não conheço essa pessoa" if len(desconhecidos) == 1 else " Ainda não conheço essas pessoas") + \
                     ": se quiser, diga o nome e peça para eu cadastrar o rosto."
        try:
            objs = self._objetos_sem_pessoa(q)
        except Exception:
            objs = ""
        if objs:
            frase += f" Também vejo {objs}."
        return frase

    def _alvo_cadastro(self, q, nome):
        """Entre TODOS os rostos do quadro, escolhe o de quem está sendo cadastrado: o MAIOR (o que está mais perto) que não
        pertence a outra pessoa já cadastrada. Devolve (embedding, estado) com estado: ok | ninguem | so_conhecidos | ambiguo | longe."""
        fs = self.detectar(q)
        if not fs:
            return None, "ninguem"
        cands = []
        for f in fs:
            feat = self.rec.feature(self.rec.alignCrop(q, f)).flatten()
            feat = feat / (np.linalg.norm(feat) + 1e-9)
            dono, _ = self._decidir(feat, f)
            if dono is not None and dono.lower() != nome.lower():
                self._dono_visto = dono
                continue                                   # esse rosto é de outra pessoa que eu já conheço: não é o alvo
            cands.append((f, feat))
        if not cands:
            return None, "so_conhecidos"
        cands.sort(key=lambda c: -float(c[0][2]))
        f, feat = cands[0]
        if len(cands) > 1 and float(cands[1][0][2]) > 0.75 * float(f[2]):
            return None, "ambiguo"                         # dois desconhecidos do mesmo tamanho: quem vai ser cadastrado?
        if float(f[14]) < 0.9 or float(f[2]) < 80:
            return None, "longe"
        return feat, "ok"

    def cadastrar(self, nome, ao_instrucao=None, adicionar=False, quadros_por_fase=5):
        """Cadastra o rosto de `nome` de vários jeitos: de frente, virando a cabeça e com/sem óculos (para reconhecer nos dois casos).
        Funciona com outras pessoas no quadro: o alvo é o rosto desconhecido mais perto da câmera (a pessoa precisa se aproximar).
        `ao_instrucao(texto)` fala as instruções entre as etapas. adicionar=True acrescenta jeitos novos em vez de substituir."""
        avisos = {
            "ninguem": f"Não estou vendo ninguém. {nome}, chegue mais perto da câmera.",
            "so_conhecidos": f"Só vejo pessoas que eu já conheço. {nome}, apareça na frente da câmera.",
            "ambiguo": f"{nome}, chegue mais perto da câmera, bem na frente das outras pessoas, para eu saber quem é você.",
            "longe": f"{nome}, chegue um pouco mais perto da câmera e olhe de frente.",
        }
        fases = [("", 22.0), ("Agora vire a cabeça bem devagar, um pouquinho para cada lado.", 4.5),
                 ("Por fim, se você usa óculos, coloque ou tire eles e olhe de novo para a câmera. Se não usa, é só continuar olhando.", 4.5)]
        embs, n_primeira, ult_aviso, dicas, estado = [], 0, 0.0, 0, "ok"
        self._dono_visto = None
        self.quadro(aquecer=4)
        for i, (instr, limite) in enumerate(fases):
            if instr and ao_instrucao:
                ao_instrucao(instr)
            t0, n_fase = time.time(), 0
            while time.time() - t0 < limite and n_fase < quadros_por_fase:
                feat, estado = self._alvo_cadastro(self.quadro(), nome)
                if feat is None:
                    if ao_instrucao and dicas < 3 and time.time() - ult_aviso > 7.0 and time.time() - t0 > 2.0:
                        ult_aviso, dicas = time.time(), dicas + 1
                        ao_instrucao(avisos[estado])
                elif not embs or float(feat @ embs[-1]) < 0.985:         # ignora quadros idênticos
                    embs.append(feat)
                    n_fase += 1
                    n_primeira += (i == 0)
                time.sleep(0.4)
        if len(embs) < 8 or n_primeira < 3:
            if estado == "so_conhecidos" and self._dono_visto:
                raise ConflitoRosto(self._dono_visto, 1.0)          # só havia o rosto de alguém que já está cadastrado
            raise RuntimeError(f"não consegui ver o rosto de {nome} bem, de frente e perto da câmera; chegue mais perto, com boa luz, e tente de novo")
        for outro, oe in self.rostos.items():
            if outro.lower() == nome.lower():
                continue
            s = float(np.mean([max(float(a @ b) for b in oe) for a in embs]))
            if s >= 0.45:
                raise ConflitoRosto(outro, s)
        chave = next((n for n in self.rostos if n.lower() == nome.lower()), nome)
        if adicionar and chave in self.rostos:
            self.rostos[chave] = (self.rostos[chave] + embs)[-40:]
        else:
            self.rostos[chave] = embs
        self._hist.clear()
        self._salvar()
        return len(embs)

    def _aprender_variacao(self, gente):
        """Aprende o rosto de quem já é conhecido com aparências novas (óculos, barba, boné): só quando a VOZ confirma que é a mesma
        pessoa, há um único rosto no quadro, ele foi reconhecido (sem muita folga) e ainda não tenho nada parecido guardado."""
        nome, t = self.voz_atual
        if not self.cfg.get("aprender_rosto_com_voz", True) or not nome or time.time() - t > 15 or nome not in self.rostos:
            return
        if len(gente) != 1 or gente[0]["nome"] != nome or gente[0].get("_feat") is None:
            return
        if not (0.5 <= gente[0]["sim"] <= 0.72) or time.time() - self._ult_aprendizado < 5.0:
            return
        feat = gente[0]["_feat"]
        if max(float(feat @ e) for e in self.rostos[nome]) > 0.9:
            return
        self.rostos[nome].append(feat)
        if len(self.rostos[nome]) > 40:
            del self.rostos[nome][8]                       # mantém os 8 primeiros (o cadastro original) e renova o resto
        self._ult_aprendizado = time.time()
        self._salvar()
        print(f"[visão] aprendi um jeito novo do rosto de {nome} (óculos, luz, ângulo)")

    # ------------------------------------------------------------------ objetos
    def _realcar(self, q):
        """Sala escura: realça o contraste antes de detectar."""
        if q.mean() >= 95:
            return q
        cv2 = self.cv2
        l, a, b = cv2.split(cv2.cvtColor(q, cv2.COLOR_BGR2LAB))
        l = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    def detectar_objetos(self, q):
        if self.detector is None:
            return []
        q = self._realcar(q)
        if self.detector_tipo == "yolox":
            return self.detector.detectar(q, limiar=0.35)
        from .objetos import PT
        h, w = q.shape[:2]
        saida = []
        for o in self.detector.detect(q):            # YOLO11: {classe, confianca, bbox=[x1,y1,x2,y2]} -> formato interno (0 a 1)
            x1, y1, x2, y2 = o["bbox"]
            saida.append({"nome": o["classe"], "pt": PT.get(o["classe"], (o["classe"],))[0], "score": o["confianca"],
                          "cx": (x1 + x2) / 2 / w, "cy": (y1 + y2) / 2 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h})
        return saida

    @staticmethod
    def contar(objs):
        c = {}
        for o in objs:
            c[o["nome"]] = c.get(o["nome"], 0) + 1
        return c

    def varredura(self, ao_fala=None, quadros=7, intervalo=0.7):
        """Varre o ambiente: a cabeça gira, vários quadros passam pelo detector e pelo reconhecimento de rostos,
        o Klaus diz o que identificou e, por fim, o modelo de visão descreve o lugar."""
        from .objetos import frase_objetos
        self.olhando = self.varrendo = True
        self._publicar(self.ultimo.get("_gente", []))
        try:
            if ao_fala:
                ao_fala("Certo, iniciando a varredura do ambiente.")
            agg, nomes, max_rostos, ultimo = {}, set(), 0, None
            self.quadro_recente(aquecer=4)
            for i in range(quadros):
                q = self.quadro_recente(max_idade=0.3, aquecer=0)
                ultimo = q
                for k, n in self.contar(self.detectar_objetos(q)).items():
                    agg[k] = max(agg.get(k, 0), n)
                gente = self.analisar(q)
                max_rostos = max(max_rostos, len(gente))
                nomes |= {g["nome"] for g in gente if g["nome"]}
                time.sleep(intervalo)
            agg["person"] = max(agg.get("person", 0), max_rostos)
            if not agg["person"]:
                agg.pop("person")
            frase = frase_objetos(agg)
            self.ultimo["lista_varredura"] = frase
            if ao_fala:
                ao_fala(f"Identifiquei {frase}." if frase else "Não identifiquei objetos conhecidos no campo da câmera.")
                if nomes:
                    ao_fala("Reconheço " + " e ".join(sorted(nomes)) + ".")
            contexto = f" Objetos já detectados automaticamente: {frase}." if frase else ""
            try:
                return self._descrever_quadro(ultimo, "Descreva o ambiente em no máximo duas frases curtas: que tipo de lugar parece ser, "
                                                       "como está a iluminação e o que mais se destaca." + contexto)
            except Exception as e:
                print(f"[visão] descrição do ambiente falhou: {e}")
                return "Essa foi a varredura do ambiente."
        finally:
            self.olhando = self.varrendo = False
            self._publicar(self.ultimo.get("_gente", []))

    # ------------------------------------------------------------------ ambiente
    def descrever(self, pergunta=None):
        """Descrição em português do que a câmera vê, pelo modelo de visão local."""
        self.olhando = True
        self._publicar(self.ultimo.get("_gente", []))
        try:
            gente, q = self._ler_gente(2)
            objs = self._objetos_sem_pessoa(q)
            fatos = (f"FATOS CONFIRMADOS pelo sistema de reconhecimento (confie neles): pessoas na câmera: {self._frase_pessoas(gente)}."
                     + (f" Objetos detectados: {objs}." if objs else ""))
            instr = f"Pergunta do usuário: {pergunta or 'Descreva o que está acontecendo na frente da câmera.'} {fatos}"
            try:
                return self._descrever_quadro(q, instr)
            except Exception as e:
                print(f"[visão] o modelo de visão não respondeu: {e}")
                base = ("Estou vendo " + self._frase_pessoas(gente)) if gente else "Não estou vendo ninguém"
                return base + (f", com {objs} por perto" if objs else "") + ". Só não consegui analisar o que está acontecendo em detalhe agora."
        finally:
            self.olhando = False
            self._publicar(self.ultimo.get("_gente", []))

    def _descrever_quadro(self, q, instrucao):
        ok, jpg = self.cv2.imencode(".jpg", self._realcar(q), [self.cv2.IMWRITE_JPEG_QUALITY, 82])
        img = base64.b64encode(jpg.tobytes()).decode()
        return self._perguntar(img, instrucao)

    def _perguntar(self, img, instrucao):
        r = requests.post("http://localhost:11434/api/chat", json={
            "model": self.cfg.get("modelo_visao", "gemma3:4b"), "stream": False, "keep_alive": "2m",
            "options": {"temperature": 0.3, "num_predict": 200},
            "messages": [{"role": "user", "images": [img], "content":
                          "Você é a visão de um assistente de voz; a imagem vem da webcam do computador do usuário. Responda SEMPRE em português do Brasil, "
                          "em no máximo 3 frases curtas, em texto corrido, sem listas, sem markdown e sem emojis. Responda DIRETAMENTE à pergunta do usuário. "
                          "Diga o que as pessoas estão FAZENDO (digitando, olhando a tela, falando ao telefone, segurando ou usando algo, postura), o lugar e os objetos. "
                          "Sobre quem as pessoas são, use SOMENTE os nomes dos FATOS CONFIRMADOS e nunca tente adivinhar identidade pelo rosto. "
                          "Não avalie aparência, corpo, idade ou atratividade e não faça julgamentos; roupas e cores só se perguntarem. "
                          "Não invente o que não aparece; se não der para ver, diga que não dá. "
                          f"{instrucao}"}]}, timeout=180)
        r.raise_for_status()
        txt = r.json()["message"]["content"].strip()
        self.ultimo["ambiente"] = txt
        return txt

    # --------------------------------------------------------------- acompanhamento
    def _publicar(self, gente):
        self.ultimo["_gente"] = gente
        self.ultimo.update(pessoas=[g["nome"] or "?" for g in gente], rostos=len(gente), olhando=self.olhando,
                           x=gente[0]["cx"] if gente else None, y=gente[0]["cy"] if gente else None, varrendo=self.varrendo,
                           objetos=[{"nome": o["pt"], "cx": round(o["cx"], 3), "cy": round(o["cy"], 3), "w": round(o["w"], 3), "h": round(o["h"], 3)}
                                    for o in self.objetos[:24]],
                           lista=[{"nome": n, "n": c} for n, c in self._lista_objetos()],
                           faces=[{"nome": g["nome"], "cx": round(g["cx"], 3), "cy": round(g["cy"], 3), "tam": round(g["tam"], 3)} for g in gente])
        if self.ao_evento:
            self.ao_evento({k: v for k, v in self.ultimo.items() if not k.startswith("_")})

    def _lista_objetos(self):
        from .objetos import PT
        c = {}
        for o in self.objetos:
            c[o["nome"]] = c.get(o["nome"], 0) + 1
        return [(PT[n][1] if k > 1 else PT[n][0], k) for n, k in sorted(c.items(), key=lambda kv: -kv[1])[:8]]

    def acompanhar(self, ligar):
        """Modo contínuo: câmera ligada, rosto seguido ~3x por segundo e reconhecimento a cada ~1,5 s."""
        self.quer_olhar = ligar
        if ligar == self.acompanhando:
            return
        self.acompanhando = ligar
        if ligar:
            threading.Thread(target=self._laco, daemon=True, name="olhos").start()
        else:
            self._publicar([])
            self.fechar()

    def contexto(self):
        """O que os olhos do Klaus enxergam AGORA, em texto, para o modelo de linguagem saber sem chamar ferramenta."""
        if not self.quer_olhar:
            return ("OLHOS: sua webcam está DESLIGADA agora, a pedido do usuário; você não está vendo nada. "
                    "Se perguntarem o que você vê, diga que está de olhos fechados e ofereça abri-los. ")
        if time.time() - self.t_cena > 10:
            return ("OLHOS: sua webcam deveria estar ligada, mas está sem imagem agora (outro programa pode estar usando a câmera); "
                    "você não sabe o que há na frente dela e está tentando reabrir. ")
        u = self.ultimo
        faces = u.get("faces", [])
        if faces:
            nomes = [f["nome"] or "uma pessoa que você não conhece" for f in faces]
            gente = "você vê " + ", ".join(nomes)
        else:
            gente = "não há ninguém na frente da câmera"
        objs = ", ".join(f"{x['n']} {x['nome']}" for x in u.get("lista", []))
        return (f"OLHOS (a sua webcam é o seu olho, ligada o tempo todo; isto é o que você enxerga AGORA): {gente}"
                + (f"; objetos à vista: {objs}" if objs else "") + ". Use isso com naturalidade quando fizer sentido (quem chegou, o que está na mesa), "
                "mas não afirme nada que não esteja aqui, e para ver a cena em detalhe chame ver_ambiente. ")

    def _cadastro_automatico(self, gente):
        """Se a VOZ acabou de identificar alguém que ainda não tem rosto guardado e só há um rosto desconhecido
        na câmera, grava esse rosto sob o nome da pessoa (some quando a câmera está desligada)."""
        nome, t = self.voz_atual
        if not self.cfg.get("auto_cadastro_rosto", False) or not nome or time.time() - t > 15 or nome in self.rostos:
            self._auto.clear() if not nome or nome in self.rostos else None
            return
        if len(gente) != 1 or gente[0]["nome"] is not None or gente[0].get("_feat") is None:
            return
        amostras = self._auto.setdefault(nome, [])
        amostras.append(gente[0]["_feat"])
        if len(amostras) >= 7:
            self.rostos[nome] = amostras
            self._auto.pop(nome, None)
            self._salvar()
            print(f"[visão] rosto de {nome} cadastrado automaticamente (a voz o identificou)")
            if self.ao_cadastro:
                self.ao_cadastro(nome)

    def _laco(self):
        """Os olhos: lê a câmera sem parar. Se ela falhar (outro programa usando, cabo, suspensão), NÃO desiste: reabre sozinha,
        e só para quando o usuário pede."""
        ultimo_rec, gente, ultimo_obj, falhas = 0.0, [], 0.0, 0
        try:
            while self.acompanhando:
                try:
                    q = self.quadro()
                    agora = time.time()
                    if agora - ultimo_rec > 0.8:
                        gente, ultimo_rec = self._suavizar(self.analisar(q, com_feat=True)), agora
                        self._cadastro_automatico(gente)
                        self._aprender_variacao(gente)
                    else:       # só segue o rosto (rápido), mantendo os nomes já reconhecidos
                        novos = self.analisar(q, reconhecer=False)
                        for i, n in enumerate(novos):
                            if i < len(gente):
                                n["nome"], n["sim"] = gente[i]["nome"], gente[i]["sim"]
                        gente = novos
                    if agora - ultimo_obj > 2.0 and self.detector is not None:
                        self.objetos, ultimo_obj = self.detectar_objetos(q), agora
                    self.t_cena = agora
                    if falhas:
                        print("[visão] a câmera voltou")
                        falhas = 0
                    self._publicar(gente)
                    time.sleep(0.25)
                except Exception as e:
                    falhas += 1
                    if falhas == 1 or falhas % 20 == 0:
                        print(f"[visão] sem imagem da câmera ({e}); tentando reabrir")
                    gente, self.objetos = [], []
                    self._publicar([])
                    self.fechar()
                    time.sleep(min(1.5 * falhas, 15.0))
        finally:
            self.fechar()
