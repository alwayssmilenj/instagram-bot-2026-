"""Dynamic PIL image canvas generator for memes and aesthetic quote cards."""
from __future__ import annotations

import functools
import logging
import math
import os
import random
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
    """Dynamic PIL image canvas generator for memes, aesthetic quote cards, profile RPG trading cards, and ship visuals."""

    @staticmethod
    @functools.lru_cache(maxsize=32)
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
        text = text.strip()[:240] or "Be the chaos you want to see in the chat."
        author = author.strip()[:40] or "Ineffa"

        width, height = 900, 500
        image = Image.new("RGB", (width, height), color=(12, 14, 22))
        draw = ImageDraw.Draw(image)

        # Premium Dark Violet Gradient
        for y in range(height):
            r = int(12 + (y / height) * 22)
            g = int(14 + (y / height) * 18)
            b = int(24 + (y / height) * 45)
            draw.line([(0, y), (width - 1, y)], fill=(r, g, b))

        # Glassmorphic border
        draw.rectangle([(24, 24), (width - 24, height - 24)], outline=(70, 85, 130), width=2)
        draw.rectangle([(28, 28), (width - 28, height - 28)], outline=(40, 50, 80), width=1)

        font_quote = self._load_font(52)
        font_body = self._load_font(22)
        font_author = self._load_font(18)
        font_brand = self._load_font(13)

        # Decorative quote marks
        draw.text((55, 45), "\u201c", fill=(100, 130, 200), anchor="lt", font=font_quote)

        # Wrap quote text safely
        lines = textwrap.wrap(text, width=42, break_long_words=True) or [text]

        y_offset = 135
        for line in lines[:5]:
            draw.text((75, y_offset), line, fill=(245, 245, 250), font=font_body)
            y_offset += 42

        # Author attribution & watermark
        draw.text((width - 70, height - 85), f"\u2014 {author}", fill=(130, 190, 255), anchor="rt", font=font_author)
        draw.text((width // 2, height - 42), "⚡ INEFFA QUOTE ENGINE \u2022 KNIGHTBOT", fill=(80, 100, 140), anchor="mm", font=font_brand)

        work_dir = self._temp_dir()
        output_path = work_dir / "quote.jpg"
        try:
            image.save(output_path, "JPEG", quality=92)
            return CanvasDownload(path=output_path, title=f"Quote: {text[:50]}", work_dir=work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def create_profile_card(
        self,
        username: str,
        xp: int = 120,
        level: int = 2,
        rank: int = 1,
        aura_tier: str = "Grand Luminary",
        aura_points: int = 1500,
        messages_count: int = 42,
        title: str = "Vanguard Luminary",
        badges: list[str] | None = None,
    ) -> CanvasDownload:
        """Generate high-resolution dark RPG profile trading card."""
        username = str(username).lstrip("@")[:24] or "Wanderer"
        badges = badges or ["\u26a1 Active Chatter", "\ud83d\udee1\ufe0f Verified"]

        width, height = 960, 560
        image = Image.new("RGB", (width, height), color=(10, 12, 18))
        draw = ImageDraw.Draw(image)

        # 1. Background Cyberpunk/Sci-Fi Gradient
        for y in range(height):
            factor = y / height
            r = int(10 + factor * 25)
            g = int(12 + factor * 20)
            b = int(22 + factor * 50)
            draw.line([(0, y), (width - 1, y)], fill=(r, g, b))

        # 2. Outer Glowing Neon Border
        draw.rectangle([(16, 16), (width - 16, height - 16)], outline=(75, 110, 180), width=2)
        draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(40, 60, 100), width=1)

        # 3. Avatar Placeholder Circle with Glowing Ring
        av_cx, av_cy, av_r = 130, 140, 75
        draw.ellipse([(av_cx - av_r - 6, av_cy - av_r - 6), (av_cx + av_r + 6, av_cy + av_r + 6)], fill=(20, 30, 50), outline=(100, 160, 255), width=3)
        draw.ellipse([(av_cx - av_r, av_cy - av_r), (av_cx + av_r, av_cy + av_r)], fill=(30, 45, 75))
        
        # Avatar Initial
        initial = (username[0] if username else "?").upper()
        font_av = self._load_font(56)
        draw.text((av_cx, av_cy), initial, fill=(200, 230, 255), anchor="mm", font=font_av)

        # 4. Header: Username & Title
        font_title_name = self._load_font(28)
        font_sub = self._load_font(18)
        font_label = self._load_font(14)
        font_stat_val = self._load_font(22)

        draw.text((240, 85), f"@{username}", fill=(255, 255, 255), font=font_title_name)
        draw.text((240, 130), f"\ud83c\udfc6 {title} \u2022 {aura_tier}", fill=(130, 195, 255), font=font_sub)
        draw.text((240, 165), f"\u2728 Aura: {aura_points:+,} pts", fill=(180, 220, 150), font=font_sub)

        # 5. Stats Cards Row (Rank, Level, Messages, XP)
        stats = [
            ("RANK", f"#{rank}", (255, 215, 0)),
            ("LEVEL", f"Lv. {level}", (100, 220, 255)),
            ("CHATS", f"{messages_count:,}", (255, 150, 180)),
            ("TOTAL XP", f"{xp:,}", (170, 255, 180)),
        ]

        card_w, card_h = 205, 105
        start_x, start_y = 60, 255

        for idx, (lbl, val, color) in enumerate(stats):
            cx = start_x + idx * (card_w + 16)
            cy = start_y
            draw.rectangle([(cx, cy), (cx + card_w, cy + card_h)], fill=(18, 25, 42), outline=(50, 70, 110), width=1)
            draw.text((cx + card_w // 2, cy + 30), lbl, fill=(120, 140, 175), anchor="mm", font=font_label)
            draw.text((cx + card_w // 2, cy + 70), val, fill=color, anchor="mm", font=font_stat_val)

        # 6. XP Progress Bar to Next Level
        xp_cur_level = (level - 1) ** 2 * 100
        xp_next_level = level ** 2 * 100
        xp_span = max(1, xp_next_level - xp_cur_level)
        progress = min(1.0, max(0.0, (xp - xp_cur_level) / xp_span))

        bar_x, bar_y, bar_w, bar_h = 60, 400, 840, 26
        draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=(15, 20, 32), outline=(45, 60, 95), width=1)
        fill_w = int(bar_w * progress)
        if fill_w > 0:
            for bx in range(fill_w):
                fr = bx / bar_w
                r_c = int(70 + fr * 50)
                g_c = int(140 + fr * 80)
                b_c = int(240 - fr * 20)
                draw.line([(bar_x + bx, bar_y + 1), (bar_x + bx, bar_y + bar_h - 1)], fill=(r_c, g_c, b_c))

        draw.text((bar_x + 10, bar_y - 20), "LEVEL PROGRESSION", fill=(120, 150, 190), font=font_label)
        draw.text((bar_x + bar_w - 10, bar_y - 20), f"{int(progress * 100)}% ({xp:,} / {xp_next_level:,} XP)", fill=(160, 200, 255), anchor="ra", font=font_label)

        # 7. Badges Footer
        badge_str = "  \u2022  ".join(badges[:3])
        draw.text((width // 2, height - 55), f"\ud83c\udf96\ufe0f BADGES: {badge_str}", fill=(140, 165, 205), anchor="mm", font=font_label)
        draw.text((width // 2, height - 25), "INEFFA RPG SYSTEM \u2022 VERIFIED PROFILE CARD", fill=(70, 90, 130), anchor="mm", font=font_label)

        work_dir = self._temp_dir()
        output_path = work_dir / f"profile_{username}.jpg"
        try:
            image.save(output_path, "JPEG", quality=94)
            return CanvasDownload(path=output_path, title=f"Profile Card: @{username}", work_dir=work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def create_ship_card(
        self,
        user1: str,
        user2: str,
        score: int,
        title: str = "Soulmates",
        verdict: str = "A match made in heaven!",
    ) -> CanvasDownload:
        """Generate high-resolution romance / compatibility ship card."""
        u1 = str(user1).lstrip("@")[:18] or "User1"
        u2 = str(user2).lstrip("@")[:18] or "User2"
        score = max(0, min(100, score))

        width, height = 900, 520
        image = Image.new("RGB", (width, height), color=(18, 10, 20))
        draw = ImageDraw.Draw(image)

        # Romance Rose-Violet Gradient Background
        for y in range(height):
            factor = y / height
            r = int(22 + factor * 35)
            g = int(10 + factor * 15)
            b = int(24 + factor * 40)
            draw.line([(0, y), (width - 1, y)], fill=(r, g, b))

        # Neon Pink-Violet Borders
        draw.rectangle([(16, 16), (width - 16, height - 16)], outline=(160, 60, 120), width=2)
        draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(80, 30, 60), width=1)

        font_header = self._load_font(26)
        font_user = self._load_font(20)
        font_score = self._load_font(48)
        font_body = self._load_font(18)
        font_small = self._load_font(14)

        # Header Title
        draw.text((width // 2, 50), "\ud83d\udc96 INEFFA MATCHMAKER COMPATIBILITY \ud83d\udc96", fill=(255, 180, 220), anchor="mm", font=font_header)

        # User 1 Avatar Ring
        c1_x, c1_y, c_r = 180, 170, 65
        draw.ellipse([(c1_x - c_r - 4, c1_y - c_r - 4), (c1_x + c_r + 4, c1_y + c_r + 4)], fill=(40, 15, 35), outline=(255, 100, 180), width=3)
        draw.ellipse([(c1_x - c_r, c1_y - c_r), (c1_x + c_r, c1_y + c_r)], fill=(50, 20, 45))
        draw.text((c1_x, c1_y), (u1[0] if u1 else "?").upper(), fill=(255, 200, 230), anchor="mm", font=font_score)
        draw.text((c1_x, c1_y + c_r + 24), f"@{u1}", fill=(255, 220, 240), anchor="mm", font=font_user)

        # User 2 Avatar Ring
        c2_x, c2_y = width - 180, 170
        draw.ellipse([(c2_x - c_r - 4, c2_y - c_r - 4), (c2_x + c_r + 4, c2_y + c_r + 4)], fill=(40, 15, 35), outline=(255, 100, 180), width=3)
        draw.ellipse([(c2_x - c_r, c2_y - c_r), (c2_x + c_r, c2_y + c_r)], fill=(50, 20, 45))
        draw.text((c2_x, c2_y), (u2[0] if u2 else "?").upper(), fill=(255, 200, 230), anchor="mm", font=font_score)
        draw.text((c2_x, c2_y + c_r + 24), f"@{u2}", fill=(255, 220, 240), anchor="mm", font=font_user)

        # Central Match Percentage & Heart
        heart_symbol = "\u2764\ufe0f" if score >= 70 else "\ud83d\udc96" if score >= 40 else "\ud83d\udc94"
        draw.text((width // 2, 150), f"{heart_symbol} {score}%", fill=(255, 120, 190), anchor="mm", font=font_score)
        draw.text((width // 2, 215), f"\u2728 {title} \u2728", fill=(255, 220, 140), anchor="mm", font=font_user)

        # Progress Heart Meter
        bar_x, bar_y, bar_w, bar_h = 100, 310, 700, 24
        draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=(30, 12, 28), outline=(90, 35, 75), width=1)
        fill_w = int(bar_w * (score / 100.0))
        if fill_w > 0:
            for bx in range(fill_w):
                fr = bx / bar_w
                r_c = int(220 + fr * 35)
                g_c = int(60 + fr * 40)
                b_c = int(140 + fr * 60)
                draw.line([(bar_x + bx, bar_y + 1), (bar_x + bx, bar_y + bar_h - 1)], fill=(r_c, g_c, b_c))

        # Verdict Section
        draw.rectangle([(100, 360), (width - 100, 445)], fill=(30, 14, 30), outline=(100, 40, 85), width=1)
        draw.text((width // 2, 402), f"\ud83d\udcac Verdict: {verdict}", fill=(250, 230, 245), anchor="mm", font=font_body)

        draw.text((width // 2, height - 35), "⚡ INEFFA MATCHMAKER \u2022 POWERED BY AI CHEMISTRY", fill=(140, 70, 110), anchor="mm", font=font_small)

        work_dir = self._temp_dir()
        output_path = work_dir / f"ship_{u1}_{u2}.jpg"
        try:
            image.save(output_path, "JPEG", quality=94)
            return CanvasDownload(path=output_path, title=f"Ship: @{u1} + @{u2}", work_dir=work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

