"""Live Web and Wikipedia search service for Ineffa."""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("jinshi_mds")


class SearchService:
    """Fetch live web answers and Wikipedia summaries."""

    @staticmethod
    def search_wiki(topic: str) -> str:
        topic = topic.strip()
        if not topic:
            return "Please provide a topic to search on Wikipedia."
        encoded = urllib.parse.quote(topic)
        url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&explaintext&titles={encoded}&format=json"
        req = Request(url, headers={"User-Agent": "IneffaBot/1.0"})
        try:
            with urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if page_id == "-1":
                    return f"No Wikipedia article found for '{topic}'."
                extract = page.get("extract", "").strip()
                if extract:
                    title = page.get("title", topic)
                    clean_text = " ".join(extract.split())
                    if len(clean_text) > 800:
                        clean_text = clean_text[:800].rsplit(".", 1)[0] + "."
                    return f"📖 Wikipedia: {title}\n\n{clean_text}"
            return f"No article details found for '{topic}'."
        except Exception as error:
            LOGGER.warning("Wikipedia lookup failed: %s", error)
            return f"❌ Wikipedia search failed: {str(error)[:140]}"

    @staticmethod
    def search_web(query: str) -> str:
        query = query.strip()
        if not query:
            return "Please provide a query to search."
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = Request(url, headers={"User-Agent": "IneffaBot/1.0"})
        try:
            with urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            abstract = data.get("AbstractText", "").strip()
            heading = data.get("Heading", query)
            source = data.get("AbstractSource", "Web Search")
            if abstract:
                return f"🔍 {heading} ({source})\n\n{abstract}"
            related = data.get("RelatedTopics", [])
            topics = []
            for item in related:
                if isinstance(item, dict) and "Text" in item:
                    topics.append(f"• {item['Text']}")
                if len(topics) >= 3:
                    break
            if topics:
                return f"🔍 Search results for '{query}':\n\n" + "\n".join(topics)
            return SearchService.search_wiki(query)
        except Exception as error:
            LOGGER.warning("Web search failed: %s", error)
            return SearchService.search_wiki(query)
