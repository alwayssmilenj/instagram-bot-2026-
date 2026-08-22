"""Dynamic PIL image canvas generator for memes and aesthetic quote cards.

Optimized with pre-computed gradient backdrops, vectorized bar blitting, zero-allocation buffer reuse,
and explicit image handle disposal for minimal GC memory footprint and maximum rendering throughput.
"""
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

    _BACKDROP_SPECS = {
        "meme": (800, 600, (18, 22, 32), (38, 47, 77)),
        "quote": (900, 500, (12, 14, 24), (34, 32, 69)),
        "profile": (960, 560, (10, 12, 22), (35, 32, 72)),
        "ship": (900, 520, (22, 10, 24), (57, 25, 64)),
        "levelup": (1000, 560, (14, 10, 28), (48, 22, 85)),
        "achievement_common": (1000, 420, (16, 20, 28), (32, 40, 54)),
        "achievement_rare": (1000, 420, (10, 22, 45), (20, 50, 95)),
        "achievement_epic": (1000, 420, (26, 12, 42), (60, 24, 92)),
        "achievement_legendary": (1000, 420, (32, 20, 10), (75, 48, 18)),
        "achievement_mythic": (1000, 420, (35, 12, 38), (80, 22, 65)),
    }

    _backdrop_cache: dict[str, Image.Image] = {}

    @classmethod
    def _create_vertical_gradient(
        cls,
        width: int,
        height: int,
        start_rgb: tuple[int, int, int],
        end_rgb: tuple[int, int, int],
    ) -> Image.Image:
        """Vectorized C-level vertical gradient buffer construction."""
        r1, g1, b1 = start_rgb
        r2, g2, b2 = end_rgb
        raw = bytearray(height * 3)
        h_denom = max(1, height - 1)
        for y in range(height):
            factor = y / h_denom
            idx = y * 3
            raw[idx] = int(r1 + factor * (r2 - r1))
            raw[idx + 1] = int(g1 + factor * (g2 - g1))
            raw[idx + 2] = int(b1 + factor * (b2 - b1))

        col = Image.frombytes("RGB", (1, height), bytes(raw))
        grad = col.resize((width, height), resample=Image.Resampling.BILINEAR)
        col.close()
        return grad

    @classmethod
    def _get_backdrop(cls, kind: str) -> Image.Image:
        """Return a fast memory copy of a pre-rendered gradient background template."""
        if kind not in cls._backdrop_cache:
            spec = cls._BACKDROP_SPECS.get(kind)
            if spec:
                w, h, c1, c2 = spec
                cls._backdrop_cache[kind] = cls._create_vertical_gradient(w, h, c1, c2)
            else:
                cls._backdrop_cache[kind] = Image.new("RGB", (800, 600), color=(18, 22, 32))
        return cls._backdrop_cache[kind].copy()

    @staticmethod
    def _render_horizontal_gradient_bar(
        image: Image.Image,
        x: int,
        y: int,
        width: int,
        height: int,
        start_rgb: tuple[int, int, int],
        end_rgb: tuple[int, int, int],
    ) -> None:
        """Fast vectorized horizontal gradient blit directly into target canvas."""
        if width <= 0 or height <= 0:
            return
        r1, g1, b1 = start_rgb
        r2, g2, b2 = end_rgb
        raw = bytearray(width * 3)
        w_denom = max(1, width - 1)
        for i in range(width):
            factor = i / w_denom
            idx = i * 3
            raw[idx] = int(r1 + factor * (r2 - r1))
            raw[idx + 1] = int(g1 + factor * (g2 - g1))
            raw[idx + 2] = int(b1 + factor * (b2 - b1))

        row = Image.frombytes("RGB", (width, 1), bytes(raw))
        bar = row.resize((width, height), resample=Image.Resampling.BILINEAR)
        row.close()
        image.paste(bar, (x, y))
        bar.close()

    @staticmethod
    @functools.lru_cache(maxsize=64)
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
        image = self._get_backdrop("meme")
        try:
            draw = ImageDraw.Draw(image)

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
        finally:
            image.close()

    def create_quote_card(self, text: str, author: str = "Ineffa") -> CanvasDownload:
        text = text.strip()[:240] or "Be the chaos you want to see in the chat."
        author = author.strip()[:40] or "Ineffa"

        width, height = 900, 500
        image = self._get_backdrop("quote")
        try:
            draw = ImageDraw.Draw(image)

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
        finally:
            image.close()

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
        image = self._get_backdrop("profile")
        try:
            draw = ImageDraw.Draw(image)

            # Outer Glowing Neon Border
            draw.rectangle([(16, 16), (width - 16, height - 16)], outline=(75, 110, 180), width=2)
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(40, 60, 100), width=1)

            # Avatar Placeholder Circle with Glowing Ring
            av_cx, av_cy, av_r = 130, 140, 75
            draw.ellipse([(av_cx - av_r - 6, av_cy - av_r - 6), (av_cx + av_r + 6, av_cy + av_r + 6)], fill=(20, 30, 50), outline=(100, 160, 255), width=3)
            draw.ellipse([(av_cx - av_r, av_cy - av_r), (av_cx + av_r, av_cy + av_r)], fill=(30, 45, 75))

            # Avatar Initial
            initial = (username[0] if username else "?").upper()
            font_av = self._load_font(56)
            draw.text((av_cx, av_cy), initial, fill=(200, 230, 255), anchor="mm", font=font_av)

            # Header: Username & Title
            font_title_name = self._load_font(28)
            font_sub = self._load_font(18)
            font_label = self._load_font(14)
            font_stat_val = self._load_font(22)

            draw.text((240, 85), f"@{username}", fill=(255, 255, 255), font=font_title_name)
            draw.text((240, 130), f"\ud83c\udfc6 {title} \u2022 {aura_tier}", fill=(130, 195, 255), font=font_sub)
            draw.text((240, 165), f"\u2728 Aura: {aura_points:+,} pts", fill=(180, 220, 150), font=font_sub)

            # Stats Cards Row (Rank, Level, Messages, XP)
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

            # XP Progress Bar to Next Level
            xp_cur_level = (level - 1) ** 2 * 100
            xp_next_level = level ** 2 * 100
            xp_span = max(1, xp_next_level - xp_cur_level)
            progress = min(1.0, max(0.0, (xp - xp_cur_level) / xp_span))

            bar_x, bar_y, bar_w, bar_h = 60, 400, 840, 26
            draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=(15, 20, 32), outline=(45, 60, 95), width=1)
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                self._render_horizontal_gradient_bar(
                    image=image,
                    x=bar_x + 1,
                    y=bar_y + 1,
                    width=fill_w,
                    height=bar_h - 2,
                    start_rgb=(70, 140, 240),
                    end_rgb=(120, 220, 220),
                )

            draw.text((bar_x + 10, bar_y - 20), "LEVEL PROGRESSION", fill=(120, 150, 190), font=font_label)
            draw.text((bar_x + bar_w - 10, bar_y - 20), f"{int(progress * 100)}% ({xp:,} / {xp_next_level:,} XP)", fill=(160, 200, 255), anchor="ra", font=font_label)

            # Badges Footer
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
        finally:
            image.close()

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
        image = self._get_backdrop("ship")
        try:
            draw = ImageDraw.Draw(image)

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
                self._render_horizontal_gradient_bar(
                    image=image,
                    x=bar_x + 1,
                    y=bar_y + 1,
                    width=fill_w,
                    height=bar_h - 2,
                    start_rgb=(220, 60, 140),
                    end_rgb=(255, 100, 200),
                )

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
        finally:
            image.close()

    def create_levelup_card(
        self,
        username: str,
        old_level: int,
        new_level: int,
        xp: int = 0,
        rank_title: str = "Vanguard",
        perks_unlocked: list[str] | None = None,
    ) -> CanvasDownload:
        """Generate high-resolution celebratory RPG Level Up Card (Visual Canvas 3.0)."""
        username = str(username).lstrip("@")[:24] or "Wanderer"
        perks = perks_unlocked or [
            "✨ +25% Multiplier on Daily Chat XP",
            "🛡️ High Sovereign Aura Ring & Badge",
            "⚡ Priority Realtime AI Reasoning Access",
        ]

        width, height = 1000, 560
        image = self._get_backdrop("levelup")
        try:
            draw = ImageDraw.Draw(image)

            # 1. Dual Glowing Gold & Electric Neon Borders
            draw.rectangle([(16, 16), (width - 16, height - 16)], outline=(120, 160, 255), width=2)
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(255, 215, 80), width=1)

            font_banner = self._load_font(28)
            font_user = self._load_font(24)
            font_sub = self._load_font(17)
            font_huge_lvl = self._load_font(44)
            font_arrow = self._load_font(34)
            font_label = self._load_font(14)
            font_body = self._load_font(16)
            font_brand = self._load_font(13)

            # 2. Header Celebratory Banner
            draw.text((width // 2, 52), "✨ LEVEL UPGRADE COMPLETE ✨", fill=(255, 225, 140), anchor="mm", font=font_banner)

            # 3. Avatar Section
            av_cx, av_cy, av_r = 110, 145, 55
            draw.ellipse([(av_cx - av_r - 4, av_cy - av_r - 4), (av_cx + av_r + 4, av_cy + av_r + 4)], fill=(30, 20, 50), outline=(255, 215, 90), width=3)
            draw.ellipse([(av_cx - av_r, av_cy - av_r), (av_cx + av_r, av_cy + av_r)], fill=(45, 25, 75))
            initial = (username[0] if username else "?").upper()
            draw.text((av_cx, av_cy), initial, fill=(255, 240, 200), anchor="mm", font=font_huge_lvl)

            # User Info Header
            draw.text((190, 115), f"@{username}", fill=(255, 255, 255), font=font_user)
            draw.text((190, 150), f"🏅 Rank Title: {rank_title.upper()}", fill=(130, 200, 255), font=font_sub)
            draw.text((190, 175), f"⚡ Cumulative XP: {xp:,} XP", fill=(180, 240, 160), font=font_sub)

            # 4. Level Badges Progression (Old Level -> New Level)
            b_y1, b_y2 = 220, 320

            # Old Level Badge Box
            draw.rectangle([(90, b_y1), (380, b_y2)], fill=(22, 16, 38), outline=(90, 75, 130), width=1)
            draw.text((235, b_y1 + 25), "PREVIOUS TIER", fill=(140, 155, 185), anchor="mm", font=font_label)
            draw.text((235, b_y1 + 65), f"Lv. {old_level}", fill=(180, 195, 220), anchor="mm", font=font_huge_lvl)

            # Central Animated Arrow / Upgrade Icon
            draw.text((width // 2, b_y1 + 48), "➔", fill=(255, 215, 80), anchor="mm", font=font_arrow)
            draw.text((width // 2, b_y1 + 80), "LEVEL UP", fill=(255, 160, 100), anchor="mm", font=font_label)

            # New Level Badge Box (Glowing Gold / Cyan)
            draw.rectangle([(620, b_y1), (910, b_y2)], fill=(40, 22, 65), outline=(255, 215, 80), width=2)
            draw.text((765, b_y1 + 25), "NEW ACHIEVED TIER", fill=(255, 225, 140), anchor="mm", font=font_label)
            draw.text((765, b_y1 + 65), f"Lv. {new_level}", fill=(100, 240, 255), anchor="mm", font=font_huge_lvl)

            # 5. Perks Unlocked Container Box
            box_y1, box_y2 = 345, 465
            draw.rectangle([(90, box_y1), (width - 90, box_y2)], fill=(18, 14, 32), outline=(75, 60, 115), width=1)
            draw.text((115, box_y1 + 22), "🔓 UNLOCKED PERKS & PRIVILEGES:", fill=(255, 215, 120), font=font_label)

            perk_y = box_y1 + 46
            for p in perks[:3]:
                draw.text((120, perk_y), f"• {p}", fill=(240, 240, 255), font=font_body)
                perk_y += 24

            # 6. XP Progression Bar
            xp_cur_level = (new_level - 1) ** 2 * 100
            xp_next_level = new_level ** 2 * 100
            xp_span = max(1, xp_next_level - xp_cur_level)
            progress = min(1.0, max(0.0, (xp - xp_cur_level) / xp_span))

            bar_x, bar_y, bar_w, bar_h = 90, 485, 820, 16
            draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=(15, 12, 26), outline=(60, 45, 90), width=1)
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                self._render_horizontal_gradient_bar(
                    image=image,
                    x=bar_x + 1,
                    y=bar_y + 1,
                    width=fill_w,
                    height=bar_h - 2,
                    start_rgb=(255, 180, 50),
                    end_rgb=(100, 230, 255),
                )

            draw.text((bar_x, bar_y - 14), "NEXT LEVEL MILESTONE", fill=(140, 160, 195), font=font_brand)
            draw.text((bar_x + bar_w, bar_y - 14), f"{int(progress * 100)}% ({xp:,} / {xp_next_level:,} XP)", fill=(200, 230, 255), anchor="ra", font=font_brand)

            # 7. Watermark Footer
            draw.text((width // 2, height - 25), "⚡ INEFFA RPG SYSTEM \u2022 VISUAL CANVAS 3.0 CELEBRATION", fill=(95, 80, 135), anchor="mm", font=font_brand)

            work_dir = self._temp_dir()
            output_path = work_dir / f"levelup_{username}_{new_level}.jpg"
            try:
                image.save(output_path, "JPEG", quality=94)
                return CanvasDownload(path=output_path, title=f"Level Up: @{username} (Lv. {new_level})", work_dir=work_dir)
            except Exception:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise
        finally:
            image.close()

    def create_achievement_banner(
        self,
        username: str,
        achievement_title: str,
        achievement_desc: str,
        rarity: str = "LEGENDARY",
        icon: str = "🏆",
    ) -> CanvasDownload:
        """Generate ornate, high-resolution achievement unlock banner with rarity themes."""
        username = str(username).lstrip("@")[:24] or "Wanderer"
        achievement_title = str(achievement_title).strip()[:40] or "Master of the Arena"
        achievement_desc = str(achievement_desc).strip()[:140] or "Accomplished a legendary milestone in the realm of Ineffa."
        rarity_norm = str(rarity).strip().upper()
        if rarity_norm not in {"COMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"}:
            rarity_norm = "LEGENDARY"

        # Rarity Styling Configuration
        rarity_configs = {
            "COMMON": {
                "tag": "★ COMMON ACHIEVEMENT ★",
                "tag_bg": (35, 45, 60),
                "tag_txt": (210, 225, 245),
                "border1": (140, 160, 185),
                "border2": (80, 95, 120),
                "title_color": (230, 240, 255),
                "desc_color": (180, 195, 215),
                "glow_color": (160, 180, 205),
                "backdrop": "achievement_common",
            },
            "RARE": {
                "tag": "★★ RARE ACHIEVEMENT ★★",
                "tag_bg": (20, 45, 90),
                "tag_txt": (140, 220, 255),
                "border1": (60, 150, 255),
                "border2": (30, 80, 150),
                "title_color": (140, 220, 255),
                "desc_color": (180, 225, 255),
                "glow_color": (100, 190, 255),
                "backdrop": "achievement_rare",
            },
            "EPIC": {
                "tag": "★★★ EPIC ACHIEVEMENT ★★★",
                "tag_bg": (50, 20, 75),
                "tag_txt": (235, 170, 255),
                "border1": (190, 80, 255),
                "border2": (110, 40, 160),
                "title_color": (235, 170, 255),
                "desc_color": (220, 190, 245),
                "glow_color": (225, 140, 255),
                "backdrop": "achievement_epic",
            },
            "LEGENDARY": {
                "tag": "★★★★ LEGENDARY ACHIEVEMENT ★★★★",
                "tag_bg": (65, 40, 15),
                "tag_txt": (255, 235, 130),
                "border1": (255, 205, 50),
                "border2": (160, 120, 30),
                "title_color": (255, 225, 130),
                "desc_color": (250, 235, 200),
                "glow_color": (255, 215, 100),
                "backdrop": "achievement_legendary",
            },
            "MYTHIC": {
                "tag": "👑 MYTHIC GOD-TIER ACHIEVEMENT 👑",
                "tag_bg": (70, 20, 55),
                "tag_txt": (255, 200, 240),
                "border1": (255, 75, 160),
                "border2": (180, 40, 110),
                "title_color": (255, 180, 230),
                "desc_color": (250, 215, 240),
                "glow_color": (255, 130, 200),
                "backdrop": "achievement_mythic",
            },
        }

        cfg = rarity_configs[rarity_norm]
        width, height = 1000, 420
        image = self._get_backdrop(cfg["backdrop"])
        try:
            draw = ImageDraw.Draw(image)

            # 1. Ornate Dual Borders with Corner Accents
            draw.rectangle([(16, 16), (width - 16, height - 16)], outline=cfg["border1"], width=2)
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline=cfg["border2"], width=1)

            # Corner decorative accents
            accent_len = 24
            for cx, cy, dx, dy in [(24, 24, 1, 1), (width - 24, 24, -1, 1), (24, height - 24, 1, -1), (width - 24, height - 24, -1, -1)]:
                draw.line([(cx, cy), (cx + dx * accent_len, cy)], fill=cfg["border1"], width=2)
                draw.line([(cx, cy), (cx, cy + dy * accent_len)], fill=cfg["border1"], width=2)

            font_tag = self._load_font(13)
            font_title = self._load_font(26)
            font_desc = self._load_font(17)
            font_meta = self._load_font(14)
            font_emblem = self._load_font(52)
            font_brand = self._load_font(12)

            # 2. Left Emblem / Trophy Badge Ring
            em_cx, em_cy, em_r = 135, 195, 72
            draw.ellipse([(em_cx - em_r - 6, em_cy - em_r - 6), (em_cx + em_r + 6, em_cy + em_r + 6)], fill=(20, 15, 30), outline=cfg["border1"], width=3)
            draw.ellipse([(em_cx - em_r, em_cy - em_r), (em_cx + em_r, em_cy + em_r)], fill=cfg["tag_bg"])
            draw.text((em_cx, em_cy), icon if icon else "🏆", fill=cfg["tag_txt"], anchor="mm", font=font_emblem)

            # 3. Header Rarity Pill Badge
            badge_x1, badge_y1, badge_w, badge_h = 245, 52, 340, 28
            draw.rectangle([(badge_x1, badge_y1), (badge_x1 + badge_w, badge_y1 + badge_h)], fill=cfg["tag_bg"], outline=cfg["border1"], width=1)
            draw.text((badge_x1 + badge_w // 2, badge_y1 + badge_h // 2), cfg["tag"], fill=cfg["tag_txt"], anchor="mm", font=font_tag)

            # 4. Achievement Title
            draw.text((245, 118), achievement_title, fill=cfg["title_color"], font=font_title)

            # 5. Achievement Description
            desc_lines = textwrap.wrap(achievement_desc, width=54, break_long_words=True) or [achievement_desc]
            desc_y = 165
            for line in desc_lines[:3]:
                draw.text((245, desc_y), line, fill=cfg["desc_color"], font=font_desc)
                desc_y += 26

            # 6. Unlocked Meta Section
            draw.rectangle([(245, 275), (width - 60, 345)], fill=(18, 14, 28), outline=cfg["border2"], width=1)
            draw.text((265, 296), f"👤 Unlocked by: @{username}", fill=(240, 245, 255), font=font_meta)
            import datetime
            now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            draw.text((265, 322), f"📅 Certified Timestamp: {now_utc}", fill=(150, 170, 205), font=font_meta)

            # 7. Watermark Footer
            draw.text((width // 2, height - 25), "⚡ INEFFA VISUAL CANVAS 3.0 \u2022 OFFICIAL HALL OF FAME CERTIFIED", fill=(100, 95, 130), anchor="mm", font=font_brand)

            work_dir = self._temp_dir()
            safe_title = "".join(c if c.isalnum() else "_" for c in achievement_title)[:20]
            output_path = work_dir / f"achievement_{username}_{safe_title}.jpg"
            try:
                image.save(output_path, "JPEG", quality=94)
                return CanvasDownload(path=output_path, title=f"Achievement: {achievement_title}", work_dir=work_dir)
            except Exception:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise
        finally:
            image.close()

