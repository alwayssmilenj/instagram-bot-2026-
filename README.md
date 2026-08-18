# 👑 KnightBot Instagram — Ineffa Autonomous AI & Utilities Daemon

> **A Next-Generation, Production-Grade Instagram Direct & Group Chat Autonomous Bot.**  
> Powered by Ineffa AI Persona, 70+ Built-in High-Speed Utilities, Multi-Modal Vision & Audio DSP, Full Group Moderation Engine, and Sovereign Owner Operations.

---

## ⚡ Key Highlights

- 🧠 **Autonomous AI Intelligence**: Real-time natural language reasoning with dynamic emotion escalation (`chill`, `playful`, `sarcastic`, `protective_rage`), 3-tier hierarchical memory, and sovereign owner protection.
- 🛠️ **70+ Offline Utilities**: AST math evaluator (`sin`, `cos`, `tan`, `log`, `sqrt`, `factorial`), dice expressions (`2d20+5`), cryptographic hashes (`MD5`, `SHA256`, `SHA512`), world timezones, unit converters, and cipher suites.
- 🛡️ **Autonomous Group Moderation**: Anti-link, anti-spam, emoji/leetspeak-resistant bad word filters, warning systems, and dual-layer member removal (Headless Playwright Chromium + Private API fallback).
- 🎵 **Media & Entertainment**: Direct voice audio music downloads (`.song`), country photo feeds (`.pies`), high-aura stickers (`.sticker`), tabletop RPGs, and casino slot machines.
- 🔒 **Zero Hardcoded Secrets**: 100% environment-backed configuration via `.env`.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Python 3.10+** (Tested on Python 3.11 / 3.12 / 3.14)
- **FFmpeg** (for audio/voice note conversion)
- **Node/Playwright** (for headless browser automation)

### 2. Installation & Setup
```bash
# Clone the repository
git clone https://github.com/alwayssmilenj/instagram-bot-2026-.git
cd instagram-bot-2026-

# Run the automated environment setup
./setup.sh
```

### 3. Configure `.env`
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
nano .env
```

```env
# Instagram Account
IG_USERNAME=your_bot_username
IG_PASSWORD=your_bot_password

# Owner Authorization
OWNER_USERNAME=jinshi_1
OWNER_USERNAMES=jinshi_1,jinshi

# AI Model Provider (Local Ollama, NVIDIA, Groq, OpenRouter, or Gemini)
AI_BASE_URL=http://127.0.0.1:11434
AI_MODEL=ineffa:latest
```

---

## 🌐 Private 24/7 Hosting Options

### Option A: Local 24/7 Linux Daemon (Recommended for Personal Machines)
Runs privately in the background on your Linux machine with auto-restart on boot:
```bash
# Install and enable 24/7 background systemd service
./host_locally.sh

# Control the bot anytime
./botctl.sh status    # Check live status
./botctl.sh logs      # Stream live logs
./botctl.sh restart   # Restart bot
./botctl.sh stop      # Stop bot
```

### Option B: Docker & Docker Compose
```bash
# Build and launch in background
docker compose up -d

# View container logs
docker compose logs -f
```

### Option C: Cloud Hosting (Render / Railway / Koyeb)
1. Push your repository to your private GitHub.
2. Link your private repository to **Render**, **Railway**, or **Koyeb**.
3. Add your `.env` variables in the Cloud Provider's Dashboard.
4. Deploy using the included `Dockerfile` or `Procfile` / `render.yaml`.

---

## 📜 Full Command Reference

| Category | Commands |
| :--- | :--- |
| **Core** | `.ping`, `.alive`, `.whoami`, `.id`, `.owner`, `.ai <prompt>`, `.teach <fact>`, `.echo <text>` |
| **Games** | `.rps <move>`, `.slots`, `.roll [NdS]`, `.coin`, `.choose a\|b`, `.random [min] [max]`, `.truth`, `.dare`, `.8ball <q>` |
| **Social & Fun** | `.ship @u1 @u2`, `.insult @u`, `.compliment @u`, `.flirt @u`, `.character @u`, `.quote`, `.fact`, `.joke`, `.shayari`, `.anime` |
| **Math & Numbers** | `.calc <expr>`, `.average`, `.median`, `.sum`, `.min`, `.max`, `.gcd`, `.lcm`, `.prime`, `.factorial` |
| **Ciphers & Code** | `.hash [algo] <t>`, `.password [len]`, `.uuid`, `.base64`, `.unbase64`, `.hex`, `.unhex`, `.binary`, `.unbinary`, `.morse`, `.unmorse`, `.rot13`, `.caesar` |
| **Unit & Info** | `.weather <city>`, `.news`, `.github <target>`, `.translate <lang> <t>`, `.time <zone>`, `.temperature`, `.bmi`, `.age` |
| **Media & Audio**| `.song <title>`, `.pies <country>`, `.sticker <emotion>`, `.tts <lang> <text>` |
| **Moderation** | `.kick @u`, `.remove @u`, `.warn @u`, `.warnings`, `.clearwarn`, `.ban`, `.unban`, `.antilink`, `.antibadword`, `.antispam`, `.mute` |
| **Group Admin** | `.groupinfo`, `.members`, `.admins`, `.rules`, `.setrules`, `.tagall`, `.setting <name> <val>` |
| **Owner (Sovereign)** | `.admin`, `.health`, `.stats`, `.dbstats`, `.vacuum`, `.broadcast <msg>`, `.reports`, `.resolve <id>`, `.restart` |

---

## 🧪 Testing & Validation
Run the 121-point automated unit test suite:
```bash
.venv/bin/python3 -m unittest test_bot.py
```

---

## 🛡️ Security & Privacy Notice
- All session keys, cookies, databases, and `.env` credentials are strictly ignored in `.gitignore`.
- Bot operations respect rate limits and exponential backoff to ensure account safety.
