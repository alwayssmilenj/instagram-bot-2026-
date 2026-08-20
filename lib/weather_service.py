"""Worldwide Real-Time Weather Service for KnightBot."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import logging

LOGGER = logging.getLogger("knightbot.weather")

WEATHER_EMOJIS = {
    "sunny": "☀️", "clear": "☀️", "cloudy": "☁️", "overcast": "☁️",
    "partly cloudy": "⛅", "rain": "🌧️", "light rain": "🌦️", "heavy rain": "🌧️",
    "thunderstorm": "⛈️", "snow": "❄️", "mist": "🌫️", "fog": "🌫️", "windy": "💨",
}


class WeatherService:
    """Fetches real-time worldwide weather conditions and forecasts."""

    def get_weather(self, location: str) -> tuple[bool, str]:
        clean_loc = location.strip()
        if not clean_loc:
            return False, "⚠️ Usage: `.weather <city_name>` (e.g. `.weather Tokyo` or `.weather Mumbai`)"

        encoded_loc = urllib.parse.quote(clean_loc)
        url = f"https://wttr.in/{encoded_loc}?format=j1"

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) KnightBot/2026"}
            )
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))

            current = data.get("current_condition", [{}])[0]
            nearest = data.get("nearest_area", [{}])[0]

            city = nearest.get("areaName", [{}])[0].get("value", clean_loc.title())
            country = nearest.get("country", [{}])[0].get("value", "")
            temp_c = current.get("temp_C", "N/A")
            temp_f = current.get("temp_F", "N/A")
            feels_c = current.get("FeelsLikeC", "N/A")
            desc = current.get("weatherDesc", [{}])[0].get("value", "Clear")
            humidity = current.get("humidity", "N/A")
            wind_km = current.get("windspeedKmph", "N/A")
            uv = current.get("uvIndex", "N/A")

            # Pick emoji
            emoji = "🌤️"
            for key, em in WEATHER_EMOJIS.items():
                if key in desc.lower():
                    emoji = em
                    break

            loc_str = f"{city}, {country}" if country else city

            lines = [
                f"{emoji} **WEATHER FOR {loc_str.upper()}**",
                f"• Condition: {desc}",
                f"• Temperature: **{temp_c}°C** ({temp_f}°F) [Feels like {feels_c}°C]",
                f"• Humidity: {humidity}%",
                f"• Wind Speed: {wind_km} km/h",
                f"• UV Index: {uv}",
            ]
            return True, "\n".join(lines)

        except Exception as err:
            LOGGER.warning("Weather fetch error for %s: %s", clean_loc, err)
            return False, f"⚠️ Could not retrieve weather for \"{clean_loc}\". Please check the city name."
