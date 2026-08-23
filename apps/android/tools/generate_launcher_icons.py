from pathlib import Path

from PIL import Image, ImageColor, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "assets" / "no-face-athena-foreground-source.png"
RES = ROOT / "android" / "app" / "src" / "main" / "res"
PAPER = ImageColor.getrgb("#F7F4EA")

DENSITIES = {
    "mdpi": (48, 108),
    "hdpi": (72, 162),
    "xhdpi": (96, 216),
    "xxhdpi": (144, 324),
    "xxxhdpi": (192, 432),
}


def scaled_layer(source: Image.Image, canvas_size: int, scale: float) -> Image.Image:
    side = round(canvas_size * scale)
    resized = source.resize((side, side), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    offset = ((canvas_size - side) // 2, (canvas_size - side) // 2)
    layer.alpha_composite(resized, offset)
    return layer


def save_legacy(source: Image.Image, path: Path, size: int, *, round_icon: bool) -> None:
    if round_icon:
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        paper = Image.new("RGBA", (size, size), (*PAPER, 255))
        canvas.paste(paper, (0, 0), mask)
    else:
        canvas = Image.new("RGBA", (size, size), (*PAPER, 255))

    canvas.alpha_composite(scaled_layer(source, size, 0.92))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, optimize=True)


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    alpha = source.getchannel("A").point(lambda value: 0 if value < 4 else value)
    source.putalpha(alpha)

    for density, (legacy_size, foreground_size) in DENSITIES.items():
        directory = RES / f"mipmap-{density}"
        directory.mkdir(parents=True, exist_ok=True)

        foreground = scaled_layer(source, foreground_size, 0.82)
        foreground.save(directory / "ic_launcher_foreground.png", optimize=True)
        save_legacy(source, directory / "ic_launcher.png", legacy_size, round_icon=False)
        save_legacy(source, directory / "ic_launcher_round.png", legacy_size, round_icon=True)


if __name__ == "__main__":
    main()
