"""Fast local anime-elf sticker image generator with an on-disk cache."""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import settings


@dataclass(frozen=True)
class StickerAsset:
    path: Path
    mood: str
    cache_hit: bool


class StickerService:
    MOODS = ("happy", "angry", "smug", "sleepy", "love", "shocked", "sad", "chaos")
    COLORS = {
        "happy": (255, 211, 232), "angry": (255, 174, 164), "smug": (203, 190, 255),
        "sleepy": (181, 211, 255), "love": (255, 169, 205), "shocked": (255, 225, 153),
        "sad": (177, 207, 235), "chaos": (193, 255, 184),
    }
    CAPTIONS = {
        "happy": "YAY!", "angry": "BONK!", "smug": "EZ.", "sleepy": "5 MORE MIN...",
        "love": "LOVE U", "shocked": "NANI?!", "sad": "PAIN.", "chaos": "HEHE >:3",
    }

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else settings.DATA_DIR / "anime-stickers"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.render_lock = threading.Lock()

    def render(self, mood: str = "random") -> StickerAsset:
        normalized = (mood or "random").lower().strip() or "random"
        if normalized == "random":
            normalized = secrets.choice(self.MOODS)
        if normalized not in self.MOODS:
            raise ValueError("Mood must be: " + ", ".join(self.MOODS))
        destination = self.cache_dir / f"ineffa-{normalized}.png"
        with self.render_lock:
            if destination.is_file() and destination.stat().st_size > 1000:
                return StickerAsset(destination, normalized, True)
            self._draw(destination, normalized)
            return StickerAsset(destination, normalized, False)

    @staticmethod
    def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        )
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                pass
        return ImageFont.load_default()

    def _draw(self, destination: Path, mood: str) -> None:
        image = Image.new("RGB", (512, 512), self.COLORS[mood])
        draw = ImageDraw.Draw(image)
        outline = (56, 35, 72)
        skin = (255, 224, 201)
        hair = (225, 241, 192)
        # Sticker border, elf ears, head, and mint hair.
        draw.rounded_rectangle((10, 10, 501, 501), radius=65, fill=self.COLORS[mood], outline=(255, 255, 255), width=18)
        draw.polygon(((105, 205), (18, 160), (112, 285)), fill=skin, outline=outline)
        draw.polygon(((407, 205), (494, 160), (400, 285)), fill=skin, outline=outline)
        draw.ellipse((95, 75, 417, 397), fill=skin, outline=outline, width=8)
        draw.pieslice((87, 45, 425, 292), 180, 360, fill=hair, outline=outline, width=7)
        draw.polygon(((105, 165), (170, 66), (201, 184), (258, 54), (291, 183), (350, 70), (407, 170)), fill=hair)
        # Eyes vary by mood while preserving the same recognizable Ineffa face.
        if mood == "sleepy":
            draw.arc((145, 203, 225, 257), 10, 170, fill=outline, width=9)
            draw.arc((287, 203, 367, 257), 10, 170, fill=outline, width=9)
        elif mood == "love":
            for center_x in (185, 327):
                draw.polygon(((center_x, 260), (center_x - 34, 226), (center_x - 25, 204), (center_x, 218), (center_x + 25, 204), (center_x + 34, 226)), fill=(235, 49, 101))
        elif mood == "shocked":
            draw.ellipse((154, 198, 217, 274), fill=(255, 255, 255), outline=outline, width=7)
            draw.ellipse((295, 198, 358, 274), fill=(255, 255, 255), outline=outline, width=7)
            draw.ellipse((177, 222, 197, 253), fill=outline)
            draw.ellipse((317, 222, 337, 253), fill=outline)
        else:
            eye_y = 219 if mood == "sad" else 208
            draw.ellipse((152, eye_y, 222, eye_y + 58), fill=(255, 255, 255), outline=outline, width=7)
            draw.ellipse((290, eye_y, 360, eye_y + 58), fill=(255, 255, 255), outline=outline, width=7)
            draw.ellipse((178, eye_y + 15, 201, eye_y + 45), fill=outline)
            draw.ellipse((316, eye_y + 15, 339, eye_y + 45), fill=outline)
            if mood in {"angry", "chaos"}:
                draw.line((151, 192, 221, 213), fill=outline, width=10)
                draw.line((291, 213, 361, 192), fill=outline, width=10)
        # Mood mouth and cheek marks.
        if mood in {"happy", "love", "chaos"}:
            draw.arc((205, 268, 307, 343), 5, 175, fill=outline, width=9)
        elif mood == "shocked":
            draw.ellipse((231, 287, 281, 341), fill=(111, 49, 75), outline=outline, width=6)
        elif mood in {"sad", "angry"}:
            draw.arc((217, 303, 295, 350), 190, 350, fill=outline, width=9)
        else:
            draw.line((225, 316, 287, 316), fill=outline, width=8)
        draw.line((125, 292, 166, 286), fill=(242, 137, 153), width=7)
        draw.line((346, 286, 387, 292), fill=(242, 137, 153), width=7)
        caption = self.CAPTIONS[mood]
        font = self._font(38)
        stroke_w = 3 if not isinstance(font, ImageFont.ImageFont) else 0
        box = draw.textbbox((0, 0), caption, font=font, stroke_width=stroke_w)
        x = (512 - (box[2] - box[0])) // 2
        if stroke_w > 0:
            draw.text((x, 426), caption, font=font, fill=outline, stroke_width=stroke_w, stroke_fill=(255, 255, 255))
        else:
            draw.text((x, 426), caption, font=font, fill=outline)

        temporary = destination.with_suffix(".tmp")
        try:
            image.save(temporary, format="PNG", optimize=True)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
