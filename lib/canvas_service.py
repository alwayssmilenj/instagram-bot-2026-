"""Dynamic PIL image canvas generator for memes and aesthetic quote cards."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

import settings

LOGGER = logging.getLogger("jinshi_mds")


@dataclass
class CanvasDownload:
    path: Path
    title: str
    work_dir: Path | None = None

    def cleanup(self) -> None:
        try:
            if self.work_dir and self.work_dir.exists():
                shutil.rmtree(self.work_dir, ignore_errors=True)
            elif self.path.exists():
                self.path.unlink(missing_ok=True)
        except OSError:
            pass


class CanvasService:
    """Generate dynamic memes and typography quote cards."""

    @staticmethod
    def _load_font(size: int = 16) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if size >= 18 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if size >= 18 else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "DejaVuSans.ttf",
            "arial.ttf",
        ]
        for candidate in font_candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _temp_dir(self) -> Path:
        temp_root = settings.BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(dir=temp_root))

    def create_meme(self, top_text: str, bottom_text: str = "") -> CanvasDownload:
        top_text = top_text.strip().upper()[:80]
        bottom_text = bottom_text.strip().upper()[:80]
        if not top_text and not bottom_text:
            top_text = "WHEN YOU USE INEFFA"
            bottom_text = "EVERYTHING IS FAST 💀"

        width, height = 800, 600
        image = Image.new("RGB", (width, height), color=(18, 22, 32))
        draw = ImageDraw.Draw(image)

        # Draw dark gradient background texture
        for y in range(height):
            r = int(18 + (y / height) * 20)
            g = int(22 + (y / height) * 25)
            b = int(32 + (y / height) * 45)
            draw.line([(0, y), (width - 1, y)], fill=(r, g, b))

        # Inner card border
        draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(60, 80, 120), width=3)

        font_large = self._load_font(24)
        font_medium = self._load_font(18)

        # Render Top Text
        if top_text:
            draw.text((width // 2, 70), top_text, fill=(255, 255, 255), anchor="mm", font=font_large)

        # Central Ineffa Icon Banner
        draw.rectangle([(200, 220), (600, 380)], fill=(30, 40, 60), outline=(100, 150, 255), width=2)
        draw.text((width // 2, height // 2), "✨ INEFFA MEME CANVAS ✨", fill=(140, 200, 255), anchor="mm", font=font_medium)

        # Render Bottom Text
        if bottom_text:
            draw.text((width // 2, height - 80), bottom_text, fill=(255, 255, 255), anchor="mm", font=font_large)

        work_dir = self._temp_dir()
        output_path = work_dir / "meme.jpg"
        try:
            image.save(output_path, "JPEG", quality=92)
            return CanvasDownload(path=output_path, title=f"Meme: {top_text}", work_dir=work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def create_quote_card(self, text: str, author: str = "Ineffa") -> CanvasDownload:
        text = text.strip()[:200] or "Be the chaos you want to see in the chat."
        author = author.strip()[:40] or "Ineffa"

        width, height = 900, 500
        image = Image.new("RGB", (width, height), color=(12, 14, 20))
        draw = ImageDraw.Draw(image)

        # Gradient background
        for y in range(height):
            r = int(12 + (y / height) * 15)
            g = int(14 + (y / height) * 20)
            b = int(20 + (y / height) * 35)
            draw.line([(0, y), (width - 1, y)], fill=(r, g, b))

        font_quote = self._load_font(40)
        font_body = self._load_font(20)
        font_author = self._load_font(18)

        # Decorative quote marks
        draw.text((60, 50), "\"", fill=(70, 90, 140), anchor="lt", font=font_quote)

        # Wrap quote text safely
        lines = textwrap.wrap(text, width=42, break_long_words=True) or [text]

        y_offset = 140
        for line in lines[:5]:
            draw.text((80, y_offset), line, fill=(240, 240, 245), font=font_body)
            y_offset += 40

        # Author attribution
        draw.text((width - 100, height - 80), f"- {author}", fill=(120, 180, 255), anchor="rt", font=font_author)

        work_dir = self._temp_dir()
        output_path = work_dir / "quote.jpg"
        try:
            image.save(output_path, "JPEG", quality=92)
            return CanvasDownload(path=output_path, title=f"Quote: {text[:50]}", work_dir=work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
