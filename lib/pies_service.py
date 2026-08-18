"""Fresh PIES country-image provider for Instagram photo DMs."""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from settings import BASE_DIR

VALID_COUNTRIES = ("india", "malaysia", "thailand", "china", "indonesia", "japan", "korea", "vietnam")


@dataclass
class PiesImage:
    path: Path
    country: str
    work_dir: Path
    cache_hit: bool = False

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


class PiesService:
    def fetch(self, country: str) -> PiesImage:
        clean_country = (country or "").strip().lower()
        if clean_country not in VALID_COUNTRIES:
            raise ValueError(f"Unsupported country. Supported: {', '.join(VALID_COUNTRIES)}")
        response = requests.get(
            f"https://api.shizo.top/pies/{clean_country}",
            params={"apikey": "shizo", "_": str(time.time_ns())},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Cache-Control": "no-cache",
            },
            timeout=15,
        )
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("PIES provider returned empty response body")
        ct = response.headers.get("content-type", "").lower()
        if not (ct.startswith("image/") or "octet-stream" in ct):
            raise RuntimeError(f"PIES provider did not return an image (received {ct})")
        if len(response.content) > 10 * 1024 * 1024:
            raise RuntimeError("PIES image exceeds 10 MB")

        temp_root = BASE_DIR / "temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="pies-", dir=temp_root))
        path = work_dir / f"pies-{clean_country}.jpg"
        try:
            with Image.open(BytesIO(response.content)) as img:
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    alpha = img.convert("RGBA")
                    bg = Image.new("RGB", alpha.size, (255, 255, 255))
                    bg.paste(alpha, mask=alpha.split()[3])
                    bg.save(path, "JPEG", quality=90, optimize=True)
                else:
                    img.convert("RGB").save(path, "JPEG", quality=90, optimize=True)
            return PiesImage(path, clean_country, work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
