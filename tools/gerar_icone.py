"""Gera interface/klaus.ico e interface/klaus.png (ícone do app: K metálico num círculo com anel ciano)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

RAIZ = Path(__file__).resolve().parent.parent
T = 1024                                     # desenha grande e reduz (bordas suaves)
K = [(10, 6), (23, 6), (23, 28), (44, 6), (61, 6), (35, 33), (62, 58), (44, 58), (23, 38), (23, 58), (10, 58)]


def desenhar():
    img = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # fundo: disco azul-marinho com degradê radial
    fundo = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fundo)
    for r in range(T // 2, 0, -4):
        t = r / (T / 2)
        cor = (int(2 + 18 * (1 - t)), int(10 + 52 * (1 - t)), int(24 + 105 * (1 - t)), 255)
        fd.ellipse((T / 2 - r, T / 2 - r, T / 2 + r, T / 2 + r), fill=cor)
    mascara = Image.new("L", (T, T), 0)
    ImageDraw.Draw(mascara).ellipse((24, 24, T - 24, T - 24), fill=255)
    img.paste(fundo, (0, 0), mascara)
    # anel ciano com brilho
    brilho = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    bd = ImageDraw.Draw(brilho)
    bd.ellipse((70, 70, T - 70, T - 70), outline=(47, 212, 255, 255), width=46)
    img = Image.alpha_composite(img, brilho.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(img)
    d.ellipse((70, 70, T - 70, T - 70), outline=(110, 232, 255, 255), width=26)
    d.ellipse((150, 150, T - 150, T - 150), outline=(47, 212, 255, 120), width=8)
    # K prateado
    esc, ox, oy = 11.6, T / 2 - 36 * 11.6, T / 2 - 32 * 11.6
    pts = [(ox + x * esc, oy + y * esc) for x, y in K]
    sombra = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).polygon(pts, fill=(47, 212, 255, 255))
    img = Image.alpha_composite(img, sombra.filter(ImageFilter.GaussianBlur(22)))
    prata = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    pd = ImageDraw.Draw(prata)
    for k in range(T):                                    # degradê vertical prateado
        t = k / T
        c = int(235 - 70 * abs(t - 0.45) / 0.55)
        pd.line((0, k, T, k), fill=(c, min(255, c + 12), min(255, c + 22), 255))
    forma = Image.new("L", (T, T), 0)
    ImageDraw.Draw(forma).polygon(pts, fill=255)
    img.paste(prata, (0, 0), forma)
    return img


if __name__ == "__main__":
    grande = desenhar()
    (RAIZ / "interface").mkdir(exist_ok=True)
    grande.resize((256, 256), Image.LANCZOS).save(RAIZ / "interface" / "klaus.png")
    grande.save(RAIZ / "interface" / "klaus.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ícone gerado")
