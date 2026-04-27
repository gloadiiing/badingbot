# Telegram Roasting Bot

A playful Telegram bot that roasts on command without crossing into hateful or targeted abuse.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Create a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

4. Set your token with either PowerShell:

   ```powershell
   $env:TELEGRAM_BOT_TOKEN="123456:your-token"
   ```

   Or copy `.env.example` to `.env` and replace the token there.

   To enable smarter roasts, also add a Groq or OpenAI API key:

   ```powershell
   $env:GROQ_API_KEY="gsk_your-key"
   $env:OPENAI_API_KEY="sk-your-key"
   ```

5. Run the bot:

   ```powershell
   python bot.py
   ```

## Commands

- `/start` - show a quick intro
- `/help` - show usage
- `/roast` - roast yourself
- `/roast mild Paolo` - mild roast Paolo
- `/roast spicy Paolo` - spicy roast Paolo

You can also reply to someone else's message with `/roast`.

## Smart Roasts With Groq or OpenAI

When an LLM API key is set, `/roast` asks a model for a short contextual roast. By default, `LLM_PROVIDER=auto` prefers OpenAI when `OPENAI_API_KEY` is present, otherwise Groq when `GROQ_API_KEY` is present.

```env
LLM_PROVIDER=auto
GROQ_MODEL=llama-3.3-70b-versatile
OPENAI_MODEL=gpt-5.4-mini
```

Set `LLM_PROVIDER=groq` or `LLM_PROVIDER=openai` to force one provider. The bot keeps a small rolling memory of recent visible chat messages so replies can feel less repetitive. If the LLM is unavailable, it falls back to the built-in roast list.

## Deploy on Railway

1. Open [Railway](https://railway.com/) and create a new project.
2. Choose **Deploy from GitHub repo** and select this repository.
3. Add these service variables in Railway:

   ```env
   TELEGRAM_BOT_TOKEN=your-telegram-token
   GROQ_API_KEY=your-groq-key
   OPENAI_API_KEY=your-openai-key
   LLM_PROVIDER=auto
   GROQ_MODEL=llama-3.3-70b-versatile
   OPENAI_MODEL=gpt-5.4-mini
   ROAST_STYLE=spicy
   ROAST_EVERY_TEXT=false
   ROAST_REPLY_CHANCE=0.25
   LOG_LEVEL=INFO
   ```

4. Deploy the service.

Railway uses `railpack.json` to start the bot with `python bot.py`.

## Group Chat Notes

If the bot does not see normal group messages, open [@BotFather](https://t.me/BotFather), choose your bot, and check the bot privacy setting. Commands like `/roast` still work with privacy enabled.

To let the bot occasionally roast normal text messages, set:

```powershell
$env:ROAST_EVERY_TEXT="true"
$env:ROAST_REPLY_CHANCE="0.25"
```
