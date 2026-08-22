"""GitHub projects and repository explorer service with caching and live search."""
from __future__ import annotations

import functools
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("jinshi_mds")


@dataclass(frozen=True)
class GitHubProject:
    name: str
    category: str
    description: str
    repo: str


# Pre-curated catalog of 36 top open-source projects across key software domains
FEATURED_PROJECTS: list[GitHubProject] = [
    # AI & Machine Learning
    GitHubProject("Ollama", "AI/ML", "Get up and running with Llama 3, Mistral, and LLMs locally", "ollama/ollama"),
    GitHubProject("Transformers", "AI/ML", "State-of-the-art Machine Learning for PyTorch, TensorFlow & JAX", "huggingface/transformers"),
    GitHubProject("LangChain", "AI/ML", "Build context-aware reasoning applications with LLMs", "langchain-ai/langchain"),
    GitHubProject("vLLM", "AI/ML", "High-throughput and memory-efficient LLM serving engine", "vllm-project/vllm"),
    GitHubProject("AutoGPT", "AI/ML", "An accessible vision of auto-autonomous AI agents", "Significant-Gravitas/AutoGPT"),

    # Web & Fullstack
    GitHubProject("FastAPI", "Web", "High performance Python web framework built for APIs", "fastapi/fastapi"),
    GitHubProject("Next.js", "Web", "The React Framework for the Web by Vercel", "vercel/next.js"),
    GitHubProject("Flask", "Web", "The Python micro framework for web applications", "pallets/flask"),
    GitHubProject("Django", "Web", "The Web framework for perfectionists with deadlines", "django/django"),
    GitHubProject("Vue.js", "Web", "The Progressive JavaScript Framework", "vuejs/core"),

    # DevOps & Cloud Infrastructure
    GitHubProject("Docker Engine", "DevOps", "The open-source containerization platform", "moby/moby"),
    GitHubProject("Kubernetes", "DevOps", "Production-Grade Container Scheduling and Management", "kubernetes/kubernetes"),
    GitHubProject("Terraform", "DevOps", "Infrastructure as Code automation tool", "hashicorp/terraform"),
    GitHubProject("Ansible", "DevOps", "Radically simple IT automation system", "ansible/ansible"),
    GitHubProject("Prometheus", "DevOps", "Systems monitoring and alerting toolkit", "prometheus/prometheus"),

    # Databases & Storage
    GitHubProject("Redis", "Database", "In-memory data structure store used as a database, cache, and message broker", "redis/redis"),
    GitHubProject("PostgreSQL", "Database", "World's most advanced open source relational database", "postgres/postgres"),
    GitHubProject("DuckDB", "Database", "High-performance analytical in-process SQL database management system", "duckdb/duckdb"),
    GitHubProject("Meilisearch", "Database", "Lightning-fast, hyper-relevant, and typo-tolerant search engine", "meilisearch/meilisearch"),

    # Python Core & Utilities
    GitHubProject("Rich", "Python", "Rich text and beautiful formatting in the terminal", "Textualize/rich"),
    GitHubProject("Pydantic", "Python", "Data validation using Python type hints", "pydantic/pydantic"),
    GitHubProject("Requests", "Python", "Simple, elegant HTTP library for Python", "psf/requests"),
    GitHubProject("Black", "Python", "The uncompromising Python code formatter", "psf/black"),
    GitHubProject("Playwright", "Python", "Fast and reliable end-to-end testing for modern web apps", "microsoft/playwright-python"),

    # Systems, Kernel & Tools
    GitHubProject("Linux Kernel", "Systems", "The Unix-like operating system kernel created by Linus Torvalds", "torvalds/linux"),
    GitHubProject("Ripgrep", "Systems", "Fast line-oriented search tool combining grep with rg", "BurntSushi/ripgrep"),
    GitHubProject("Neovim", "Systems", "Vim-fork focused on extensibility and usability", "neovim/neovim"),
    GitHubProject("FFmpeg", "Systems", "Cross-platform solution to record, convert and stream audio/video", "FFmpeg/FFmpeg"),
    GitHubProject("htop", "Systems", "Interactive process viewer for Unix systems", "htop-dev/htop"),
    GitHubProject("yt-dlp", "Systems", "Feature-rich command-line audio/video downloader", "yt-dlp/yt-dlp"),

    # Cybersecurity & Hacking
    GitHubProject("Metasploit", "Security", "Penetration testing framework and vulnerability scanner", "rapid7/metasploit-framework"),
    GitHubProject("Nmap", "Security", "Network exploration tool and security / port scanner", "nmap/nmap"),
    GitHubProject("Wireshark", "Security", "World's foremost network protocol analyzer", "wireshark/wireshark"),

    # GameDev & 3D Graphics
    GitHubProject("Godot Engine", "GameDev", "Free, open-source 2D and 3D game engine", "godotengine/godot"),
    GitHubProject("Blender", "GameDev", "Free and open 3D creation suite", "blender/blender"),
]


class GitHubService:
    """GitHub API integration, cached repository details, trending, and catalog explorer."""

    @staticmethod
    def list_projects(category: str | None = None) -> str:
        projects = FEATURED_PROJECTS
        if category:
            filtered = [p for p in projects if p.category.lower() == category.lower()]
            if filtered:
                projects = filtered

        lines = [f"🐙 FEATURED OPEN-SOURCE PROJECTS ({len(projects)} Total)\n"]
        current_cat = ""
        for idx, p in enumerate(projects, 1):
            if p.category != current_cat:
                current_cat = p.category
                lines.append(f"\n📂 [{current_cat}]")
            lines.append(f"• {p.name} ({p.repo}) — {p.description}")
        lines.append("\n💡 Tip: Use .github <owner/repo> for live stars, or .ghsearch <query> to search!")
        return "\n".join(lines)

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def get_repo_info(repo_path: str) -> str:
        repo_path = repo_path.strip().lstrip("@").lstrip("https://github.com/").strip("/")
        if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", repo_path):
            return "Usage: .github <owner/repo> (e.g. .github pallets/flask)"

        url = f"https://api.github.com/repos/{repo_path}"
        req = Request(url, headers={"User-Agent": "IneffaBot/1.0", "Accept": "application/vnd.github.v3+json"})
        try:
            with urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            name = data.get("full_name", repo_path)
            desc = data.get("description", "No description provided.")
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)
            issues = data.get("open_issues_count", 0)
            lang = data.get("language", "Unknown")
            html_url = data.get("html_url", f"https://github.com/{repo_path}")
            license_name = data.get("license", {}).get("name", "N/A") if isinstance(data.get("license"), dict) else "N/A"

            return (
                f"🐙 GitHub Repository: {name}\n\n"
                f"📝 {desc}\n"
                f"⭐ Stars: {stars:,} | 🔀 Forks: {forks:,} | 🛠 Language: {lang}\n"
                f"⚠️ Open Issues: {issues:,} | 📜 License: {license_name}\n"
                f"🔗 {html_url}"
            )
        except Exception as error:
            LOGGER.warning("GitHub lookup failed for %s: %s", repo_path, error)
            return f"❌ GitHub lookup failed for '{repo_path}'. Make sure it exists as owner/repo."

    @staticmethod
    @functools.lru_cache(maxsize=64)
    def search_repositories(query: str, limit: int = 5) -> str:
        clean_q = query.strip()
        if not clean_q:
            return "Usage: .ghsearch <search keywords>"

        encoded = urllib.parse.quote_plus(clean_q)
        url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&order=desc&per_page={limit}"
        req = Request(url, headers={"User-Agent": "IneffaBot/1.0", "Accept": "application/vnd.github.v3+json"})
        try:
            with urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data.get("items", [])
            if not items:
                return f"🔍 No GitHub repositories found matching '{clean_q}'."

            lines = [f"🔍 GitHub Search Results for \"{clean_q}\":\n"]
            for idx, item in enumerate(items[:limit], 1):
                name = item.get("full_name", "")
                desc = item.get("description", "No description") or "No description"
                stars = item.get("stargazers_count", 0)
                lang = item.get("language", "Unknown") or "Unknown"
                url_str = item.get("html_url", "")
                lines.append(f"{idx}. **{name}** (⭐ {stars:,} | 🛠 {lang})\n   _{desc[:90]}_\n   🔗 {url_str}\n")

            return "\n".join(lines).strip()
        except Exception as err:
            LOGGER.warning("GitHub search failed for %s: %s", clean_q, err)
            return f"❌ GitHub search failed for '{clean_q}'."

    @staticmethod
    @functools.lru_cache(maxsize=32)
    def get_trending(language: str | None = None) -> str:
        lang_filter = f"+language:{language.strip()}" if language and language.strip() else ""
        url = f"https://api.github.com/search/repositories?q=stars:>1000{lang_filter}&sort=stars&order=desc&per_page=6"
        req = Request(url, headers={"User-Agent": "IneffaBot/1.0", "Accept": "application/vnd.github.v3+json"})
        try:
            with urlopen(req, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            items = data.get("items", [])
            if not items:
                return "🌟 No trending repositories found right now."

            lang_title = f" [{language.title()}]" if language else ""
            lines = [f"🌟 Top Starred GitHub Repositories{lang_title}:\n"]
            for idx, item in enumerate(items[:6], 1):
                name = item.get("full_name", "")
                desc = item.get("description", "No description") or "No description"
                stars = item.get("stargazers_count", 0)
                lang = item.get("language", "Unknown") or "Unknown"
                url_str = item.get("html_url", "")
                lines.append(f"{idx}. **{name}** (⭐ {stars:,} | 🛠 {lang})\n   _{desc[:90]}_\n   🔗 {url_str}\n")

            return "\n".join(lines).strip()
        except Exception as err:
            LOGGER.warning("GitHub trending failed: %s", err)
            return "❌ Failed to fetch trending repositories right now."
