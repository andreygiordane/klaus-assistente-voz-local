"""Detecção de objetos local e offline com YOLO11 (biblioteca `ultralytics`).

Uso rápido:
    det = ObjectDetector()                       # carrega o modelo UMA vez (GPU NVIDIA se houver, senão CPU)
    for obj in det.detect(frame_bgr):            # frame do OpenCV / NumPy (BGR)
        print(obj["classe"], obj["confianca"], obj["bbox"])   # bbox = [x1, y1, x2, y2] em pixels

Sem travar a aplicação (GUI/webcam):
    assinc = DetectorAssincrono(det, fps=2).iniciar()
    assinc.enviar(frame)                         # não bloqueia: só guarda o frame mais recente
    objetos, quando = assinc.ultimo()            # último resultado pronto

Teste solto com a webcam (janela do OpenCV, tecla Q fecha):   python -m assistente.object_detector
Na primeira execução o ultralytics baixa os pesos (yolo11m.pt ~40 MB); depois funciona 100% offline.
"""
import logging
import threading
import time

import numpy as np

log = logging.getLogger("klaus.objetos")


class ErroDetector(RuntimeError):
    """Falha ao preparar o detector (biblioteca ausente, pesos que não carregam, classe inexistente...)."""


def escolher_dispositivo():
    """0 (primeira GPU NVIDIA) se o PyTorch enxerga CUDA; caso contrário "cpu"."""
    try:
        import torch
        if torch.cuda.is_available():
            return 0
    except Exception as e:                  # PyTorch ausente ou driver com problema: cai para a CPU
        log.warning("CUDA indisponível (%s); usando CPU", e)
    return "cpu"


class ObjectDetector:
    def __init__(self, modelo="yolo11m.pt", confianca=0.5, classes=None, imgsz=640, dispositivo=None, aquecer=True):
        """modelo: "yolo11n.pt" (mais rápido) ou "yolo11m.pt" (mais preciso) — ou o caminho de um .pt seu.
        confianca: limiar mínimo (0 a 1).  classes: lista de nomes (ex.: ["person", "cell phone"]) ou None para todas."""
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ErroDetector("a biblioteca 'ultralytics' não está instalada (pip install ultralytics)") from e
        self.confianca, self.imgsz = float(confianca), int(imgsz)
        self.dispositivo = escolher_dispositivo() if dispositivo is None else dispositivo
        self._trava = threading.Lock()          # o modelo não é thread-safe: uma inferência por vez
        try:
            self.modelo = YOLO(modelo)
        except Exception as e:
            raise ErroDetector(f"não consegui carregar o modelo '{modelo}': {e}") from e
        self.nomes = dict(self.modelo.names)    # {id: nome}
        self._ids = None
        self.definir_classes(classes)
        self._meia_precisao = self.dispositivo != "cpu"
        self.erros = 0
        log.info("YOLO11 '%s' pronto em %s", modelo, "GPU 0" if self.dispositivo == 0 else self.dispositivo)
        if aquecer:                             # a primeira inferência é lenta (inicializa CUDA); faz isso já na partida
            self.detect(np.zeros((480, 640, 3), np.uint8))

    # ---------------------------------------------------------------- configuração
    def definir_classes(self, classes):
        """Filtra por classes (nomes em inglês do COCO, ex.: "person", "cell phone"); None volta a detectar todas."""
        if not classes:
            self._ids = None
            return
        por_nome = {n.lower(): i for i, n in self.nomes.items()}
        ids, invalidas = [], []
        for c in classes:
            if isinstance(c, int) and c in self.nomes:
                ids.append(c)
            elif str(c).lower() in por_nome:
                ids.append(por_nome[str(c).lower()])
            else:
                invalidas.append(c)
        if invalidas:
            raise ErroDetector(f"classes desconhecidas: {invalidas}. Válidas: {sorted(por_nome)}")
        self._ids = ids

    # ------------------------------------------------------------------- detecção
    def detect(self, frame, confianca=None):
        """Detecta objetos em um frame (imagem NumPy/OpenCV, BGR).
        Devolve [{"classe": str, "confianca": float, "bbox": [x1, y1, x2, y2]}], do mais confiante para o menos.
        Nunca levanta exceção na inferência: em caso de erro devolve [] e registra no log."""
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.size == 0:
            return []
        limiar = self.confianca if confianca is None else float(confianca)
        for tentativa in (1, 2):
            try:
                with self._trava:
                    r = self.modelo.predict(frame, conf=limiar, classes=self._ids, device=self.dispositivo, imgsz=self.imgsz,
                                            half=self._meia_precisao, verbose=False)[0]
                if r.boxes is None or len(r.boxes) == 0:
                    return []
                xyxy = r.boxes.xyxy.cpu().numpy()
                conf = r.boxes.conf.cpu().numpy()
                cls = r.boxes.cls.cpu().numpy().astype(int)
                saida = [{"classe": self.nomes[int(c)], "confianca": round(float(s), 3), "bbox": [round(float(v), 1) for v in b]}
                         for b, s, c in zip(xyxy, conf, cls)]
                return sorted(saida, key=lambda o: -o["confianca"])
            except Exception as e:
                self.erros += 1
                if tentativa == 1 and self.dispositivo != "cpu" and "out of memory" in str(e).lower():
                    log.warning("GPU sem memória; mudando o detector para a CPU")
                    self.dispositivo, self._meia_precisao = "cpu", False
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    continue
                log.error("falha na detecção: %s", e)
                return []
        return []


class DetectorAssincrono:
    """Roda o detector numa thread própria, com FPS limitado, sem travar a thread principal (GUI/webcam).
    `enviar(frame)` é instantâneo (só guarda o frame mais recente; frames atrasados são descartados)."""

    def __init__(self, detector, fps=2.0, ao_resultado=None):
        self.detector, self.intervalo, self.ao_resultado = detector, 1.0 / max(fps, 0.1), ao_resultado
        self._frame, self._resultado, self._quando = None, [], 0.0
        self._trava, self._evento, self._ativo = threading.Lock(), threading.Event(), False

    def iniciar(self):
        if not self._ativo:
            self._ativo = True
            threading.Thread(target=self._laco, daemon=True, name="detector-objetos").start()
        return self

    def parar(self):
        self._ativo = False
        self._evento.set()

    def enviar(self, frame):
        with self._trava:
            self._frame = frame
        self._evento.set()

    def ultimo(self):
        """(lista de objetos, instante em que foi calculada)."""
        with self._trava:
            return list(self._resultado), self._quando

    def _laco(self):
        proximo = 0.0
        while self._ativo:
            self._evento.wait(timeout=0.5)
            self._evento.clear()
            with self._trava:
                frame, self._frame = self._frame, None
            if frame is None or time.time() < proximo:
                continue
            inicio = time.time()
            objetos = self.detector.detect(frame)
            with self._trava:
                self._resultado, self._quando = objetos, time.time()
            if self.ao_resultado:
                try:
                    self.ao_resultado(objetos)
                except Exception as e:
                    log.error("erro no ao_resultado: %s", e)
            proximo = inicio + self.intervalo


if __name__ == "__main__":      # teste solto com a webcam
    import cv2
    logging.basicConfig(level=logging.INFO)
    det = ObjectDetector()
    assinc = DetectorAssincrono(det, fps=5).iniciar()
    cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    print("Dispositivo:", "GPU" if det.dispositivo == 0 else "CPU", "- tecle Q na janela para sair")
    while True:
        ok, frame = cam.read()
        if not ok:
            break
        assinc.enviar(frame)
        for o in assinc.ultimo()[0]:
            x1, y1, x2, y2 = map(int, o["bbox"])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 2)
            cv2.putText(frame, f'{o["classe"]} {o["confianca"]:.2f}', (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)
        cv2.imshow("YOLO11", frame)
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
            break
    assinc.parar()
    cam.release()
    cv2.destroyAllWindows()
