"""Detecção de objetos pela webcam (YOLOX, 80 classes COCO, local) com nomes em português."""
import numpy as np

from . import config

MODELO = config.RAIZ / "modelos" / "yolox.onnx"
COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light", "fire hydrant",
        "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
        "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
        "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
        "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
        "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
        "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"]
# (singular, plural, gênero)
PT = {
    "person": ("pessoa", "pessoas", "f"), "bicycle": ("bicicleta", "bicicletas", "f"), "car": ("carro", "carros", "m"),
    "motorcycle": ("moto", "motos", "f"), "airplane": ("avião", "aviões", "m"), "bus": ("ônibus", "ônibus", "m"),
    "train": ("trem", "trens", "m"), "truck": ("caminhão", "caminhões", "m"), "boat": ("barco", "barcos", "m"),
    "traffic light": ("semáforo", "semáforos", "m"), "fire hydrant": ("hidrante", "hidrantes", "m"),
    "stop sign": ("placa de pare", "placas de pare", "f"), "parking meter": ("parquímetro", "parquímetros", "m"),
    "bench": ("banco", "bancos", "m"), "bird": ("pássaro", "pássaros", "m"), "cat": ("gato", "gatos", "m"),
    "dog": ("cachorro", "cachorros", "m"), "horse": ("cavalo", "cavalos", "m"), "sheep": ("ovelha", "ovelhas", "f"),
    "cow": ("vaca", "vacas", "f"), "elephant": ("elefante", "elefantes", "m"), "bear": ("urso", "ursos", "m"),
    "zebra": ("zebra", "zebras", "f"), "giraffe": ("girafa", "girafas", "f"), "backpack": ("mochila", "mochilas", "f"),
    "umbrella": ("guarda-chuva", "guarda-chuvas", "m"), "handbag": ("bolsa", "bolsas", "f"), "tie": ("gravata", "gravatas", "f"),
    "suitcase": ("mala", "malas", "f"), "frisbee": ("frisbee", "frisbees", "m"), "skis": ("esqui", "esquis", "m"),
    "snowboard": ("snowboard", "snowboards", "m"), "sports ball": ("bola", "bolas", "f"), "kite": ("pipa", "pipas", "f"),
    "baseball bat": ("taco de beisebol", "tacos de beisebol", "m"), "baseball glove": ("luva de beisebol", "luvas de beisebol", "f"),
    "skateboard": ("skate", "skates", "m"), "surfboard": ("prancha de surfe", "pranchas de surfe", "f"),
    "tennis racket": ("raquete de tênis", "raquetes de tênis", "f"), "bottle": ("garrafa", "garrafas", "f"),
    "wine glass": ("taça", "taças", "f"), "cup": ("copo", "copos", "m"), "fork": ("garfo", "garfos", "m"),
    "knife": ("faca", "facas", "f"), "spoon": ("colher", "colheres", "f"), "bowl": ("tigela", "tigelas", "f"),
    "banana": ("banana", "bananas", "f"), "apple": ("maçã", "maçãs", "f"), "sandwich": ("sanduíche", "sanduíches", "m"),
    "orange": ("laranja", "laranjas", "f"), "broccoli": ("brócolis", "brócolis", "m"), "carrot": ("cenoura", "cenouras", "f"),
    "hot dog": ("cachorro-quente", "cachorros-quentes", "m"), "pizza": ("pizza", "pizzas", "f"), "donut": ("rosquinha", "rosquinhas", "f"),
    "cake": ("bolo", "bolos", "m"), "chair": ("cadeira", "cadeiras", "f"), "couch": ("sofá", "sofás", "m"),
    "potted plant": ("planta em vaso", "plantas em vaso", "f"), "bed": ("cama", "camas", "f"), "dining table": ("mesa", "mesas", "f"),
    "toilet": ("vaso sanitário", "vasos sanitários", "m"), "tv": ("tela", "telas", "f"), "laptop": ("notebook", "notebooks", "m"),
    "mouse": ("mouse", "mouses", "m"), "remote": ("controle remoto", "controles remotos", "m"), "keyboard": ("teclado", "teclados", "m"),
    "cell phone": ("celular", "celulares", "m"), "microwave": ("micro-ondas", "micro-ondas", "m"), "oven": ("forno", "fornos", "m"),
    "toaster": ("torradeira", "torradeiras", "f"), "sink": ("pia", "pias", "f"), "refrigerator": ("geladeira", "geladeiras", "f"),
    "book": ("livro", "livros", "m"), "clock": ("relógio", "relógios", "m"), "vase": ("vaso", "vasos", "m"),
    "scissors": ("tesoura", "tesouras", "f"), "teddy bear": ("urso de pelúcia", "ursos de pelúcia", "m"),
    "hair drier": ("secador de cabelo", "secadores de cabelo", "m"), "toothbrush": ("escova de dentes", "escovas de dentes", "f"),
}
NUM_M = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"]
NUM_F = ["", "uma", "duas", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"]


def frase_objetos(contagem):
    """{'person': 1, 'tv': 2} -> 'uma pessoa e duas telas'."""
    partes = []
    for nome, n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))[:9]:
        sing, plu, gen = PT.get(nome, (nome, nome, "m"))
        if n == 1:
            partes.append(("uma " if gen == "f" else "um ") + sing)
        else:
            num = (NUM_F if gen == "f" else NUM_M)[n] if n <= 10 else str(n)
            partes.append(f"{num} {plu}")
    if not partes:
        return ""
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]


class Detector:
    def __init__(self):
        import cv2
        import onnxruntime as ort
        self.cv2 = cv2
        self.sess = ort.InferenceSession(str(MODELO), providers=["CPUExecutionProvider"])
        grades, passos = [], []
        for s in (8, 16, 32):
            n = 640 // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grades.append(np.stack((xv, yv), 2).reshape(-1, 2))
            passos.append(np.full((n * n, 1), s))
        self.grade, self.passo = np.concatenate(grades).astype(np.float32), np.concatenate(passos).astype(np.float32)

    def detectar(self, bgr, limiar=0.40, nms=0.5):
        """Lista de objetos: {id, nome, pt, score, cx, cy, w, h} (posições de 0 a 1 na imagem original)."""
        cv2 = self.cv2
        h, w = bgr.shape[:2]
        r = min(640 / h, 640 / w)
        redim = cv2.resize(bgr, (int(w * r), int(h * r)), interpolation=cv2.INTER_LINEAR)
        tela = np.full((640, 640, 3), 114, dtype=np.uint8)
        tela[:redim.shape[0], :redim.shape[1]] = redim
        saida = self.sess.run(None, {"images": tela.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        xy = (saida[:, :2] + self.grade) * self.passo
        wh = np.exp(saida[:, 2:4]) * self.passo
        scores = saida[:, 4:5] * saida[:, 5:]
        cls = scores.argmax(1)
        conf = scores[np.arange(len(cls)), cls]
        ok = conf > limiar
        if not ok.any():
            return []
        xy, wh, cls, conf = xy[ok], wh[ok], cls[ok], conf[ok]
        caixas = np.concatenate([xy - wh / 2, wh], axis=1) / r
        manter = cv2.dnn.NMSBoxesBatched([[float(a), float(b), float(c), float(d)] for a, b, c, d in caixas],
                                         [float(c) for c in conf], [int(c) for c in cls], limiar, nms)
        out = []
        for i in np.array(manter).flatten():
            x, y, bw, bh = caixas[i]
            nome = COCO[int(cls[i])]
            out.append({"id": int(cls[i]), "nome": nome, "pt": PT[nome][0], "score": round(float(conf[i]), 2),
                        "cx": float((x + bw / 2) / w), "cy": float((y + bh / 2) / h), "w": float(bw / w), "h": float(bh / h)})
        return out
