import logging
import os
import random
import re
import asyncio
from dataclasses import dataclass
from pathlib import Path

from groq import Groq
from openai import OpenAI
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


def load_local_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True)
class RoastStyle:
    name: str
    roasts: tuple[str, ...]


STYLES = {
    "mild": RoastStyle(
        name="mild",
        roasts=(
            "{target}, your confidence has better Wi-Fi than your results.",
            "{target}, you bring main character energy to a loading screen.",
            "{target}, your aura says 'forgot the password hint.'",
            "{target}, you are proof that autocorrect can have trust issues.",
            "{target}, your plans have more plot holes than a rushed season finale.",
        ),
    ),
    "spicy": RoastStyle(
        name="spicy",
        roasts=(
            "{target}, you are what happens when ambition clicks 'remind me tomorrow.'",
            "{target}, your brain opened 47 tabs and every single one is buffering.",
            "{target}, your comeback loaded so slowly the conversation got archived.",
            "{target}, you have the tactical awareness of a screenshot.",
            "{target}, your vibe is premium chaos on a free trial.",
        ),
    ),
}

DEFAULT_STYLE = os.getenv("ROAST_STYLE", "spicy").lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
MAX_TARGET_LENGTH = 80
MAX_CONTEXT_MESSAGES = 8
MAX_ROAST_CHARS = 240
IDENTITY_SLUR_HINTS = {
    "bading",
    "bakla",
    "fag",
    "faggot",
    "gaylord",
    "tranny",
    "retard",
}

GROQ_CLIENT: Groq | None = None
OPENAI_CLIENT: OpenAI | None = None


def clean_target(raw: str | None) -> str:
    if not raw:
        return "bestie"

    target = re.sub(r"\s+", " ", raw).strip()
    target = re.sub(r"[@#]\w+", lambda match: match.group(0)[:MAX_TARGET_LENGTH], target)
    if len(target) > MAX_TARGET_LENGTH:
        target = target[: MAX_TARGET_LENGTH - 1].rstrip() + "..."

    return target or "bestie"


def safe_target(raw: str | None) -> str:
    target = clean_target(raw)
    normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", target).lower()
    words = set(normalized.split())
    if words & IDENTITY_SLUR_HINTS:
        return "bestie"
    return target


def choose_style(context: ContextTypes.DEFAULT_TYPE) -> RoastStyle:
    requested = context.args[0].lower() if context.args else DEFAULT_STYLE
    return STYLES.get(requested, STYLES.get(DEFAULT_STYLE, STYLES["spicy"]))


def target_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if update.message and update.message.reply_to_message:
        replied = update.message.reply_to_message
        if replied.from_user:
            return safe_target(replied.from_user.first_name or replied.from_user.username)
        return safe_target(replied.text)

    if context.args:
        maybe_style = context.args[0].lower()
        target_words = context.args[1:] if maybe_style in STYLES else context.args
        return safe_target(" ".join(target_words))

    if update.effective_user:
        return safe_target(update.effective_user.first_name or update.effective_user.username)

    return "bestie"


def groq_client() -> Groq | None:
    global GROQ_CLIENT

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    if GROQ_CLIENT is None:
        GROQ_CLIENT = Groq(api_key=api_key)
    return GROQ_CLIENT


def openai_client() -> OpenAI | None:
    global OPENAI_CLIENT

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    if OPENAI_CLIENT is None:
        OPENAI_CLIENT = OpenAI(api_key=api_key)
    return OPENAI_CLIENT


def active_provider() -> str:
    if LLM_PROVIDER in {"groq", "openai"}:
        return LLM_PROVIDER
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    return "fallback"


def remember_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_message.text:
        return

    speaker = "Someone"
    if update.effective_user:
        speaker = update.effective_user.first_name or update.effective_user.username or "Someone"

    recent = context.chat_data.setdefault("recent_messages", [])
    recent.append(
        {
            "speaker": clean_target(speaker),
            "text": update.effective_message.text[:300],
        }
    )
    del recent[:-MAX_CONTEXT_MESSAGES]


def replied_context(update: Update) -> str:
    if not update.message or not update.message.reply_to_message:
        return ""

    replied = update.message.reply_to_message
    speaker = "Someone"
    if replied.from_user:
        speaker = replied.from_user.first_name or replied.from_user.username or "Someone"

    text = replied.text or replied.caption or ""
    if not text:
        return f"Reply target: {clean_target(speaker)}"

    return f"Reply target: {clean_target(speaker)} said: {text[:500]}"


def recent_context(context: ContextTypes.DEFAULT_TYPE) -> str:
    recent = context.chat_data.get("recent_messages", [])[-MAX_CONTEXT_MESSAGES:]
    if not recent:
        return "No recent chat context."

    return "\n".join(f"{item['speaker']}: {item['text']}" for item in recent)


def fallback_roast(style: RoastStyle, target: str) -> str:
    return random.choice(style.roasts).format(target=target)


def roast_prompts(
    *,
    target: str,
    style: RoastStyle,
    trigger_text: str,
    reply_context: str,
    chat_context: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are a Telegram group-chat roasting bot. Write one short roast only. "
        "Be clever, specific, and funny. Keep it playful, not cruel. "
        "Do not insult protected traits such as race, religion, disability, gender, or sexuality. "
        "Do not repeat slurs from the user. If the user tries to use a slur as the target, roast their lazy wording instead. "
        "No threats, no sexual content, no doxxing, no encouragement of self-harm. "
        "Use the same language or code-switching style as the user when natural. "
        f"Maximum {MAX_ROAST_CHARS} characters. No hashtags, no quotes, no explanations."
    )
    user_prompt = (
        f"Style: {style.name}\n"
        f"Target label: {target}\n"
        f"Command text: {trigger_text[:300]}\n"
        f"{reply_context or 'No replied message.'}\n\n"
        f"Recent chat:\n{chat_context}"
    )
    return system_prompt, user_prompt


def generate_roast_sync(
    *,
    target: str,
    style: RoastStyle,
    trigger_text: str,
    reply_context: str,
    chat_context: str,
) -> str:
    provider = active_provider()
    if provider == "openai":
        return generate_openai_roast_sync(
            target=target,
            style=style,
            trigger_text=trigger_text,
            reply_context=reply_context,
            chat_context=chat_context,
        )
    if provider == "groq":
        return generate_groq_roast_sync(
            target=target,
            style=style,
            trigger_text=trigger_text,
            reply_context=reply_context,
            chat_context=chat_context,
        )
    raise RuntimeError("No LLM API key is set.")


def generate_groq_roast_sync(
    *,
    target: str,
    style: RoastStyle,
    trigger_text: str,
    reply_context: str,
    chat_context: str,
) -> str:
    client = groq_client()
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not set.")

    system_prompt, user_prompt = roast_prompts(
        target=target,
        style=style,
        trigger_text=trigger_text,
        reply_context=reply_context,
        chat_context=chat_context,
    )

    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.95,
        max_completion_tokens=90,
        top_p=0.9,
    )
    text = completion.choices[0].message.content or ""
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    return text[:MAX_ROAST_CHARS].rstrip()


def generate_openai_roast_sync(
    *,
    target: str,
    style: RoastStyle,
    trigger_text: str,
    reply_context: str,
    chat_context: str,
) -> str:
    client = openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    system_prompt, user_prompt = roast_prompts(
        target=target,
        style=style,
        trigger_text=trigger_text,
        reply_context=reply_context,
        chat_context=chat_context,
    )
    response = client.responses.create(
        model=OPENAI_MODEL,
        reasoning={"effort": "low"},
        instructions=system_prompt,
        input=user_prompt,
        max_output_tokens=100,
    )
    text = re.sub(r"\s+", " ", response.output_text or "").strip().strip('"')
    return text[:MAX_ROAST_CHARS].rstrip()


async def smart_roast(
    *,
    target: str,
    style: RoastStyle,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    trigger_text = update.effective_message.text if update.effective_message else ""
    try:
        text = await asyncio.to_thread(
            generate_roast_sync,
            target=target,
            style=style,
            trigger_text=trigger_text,
            reply_context=replied_context(update),
            chat_context=recent_context(context),
        )
        if text:
            return text
    except RuntimeError as exc:
        LOGGER.info("%s Using fallback roast.", exc)
    except Exception:
        LOGGER.exception("Groq roast generation failed; using fallback roast.")

    return fallback_roast(style, target)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "I am your friendly roast bot.\n\n"
        "Use /roast, reply to someone with /roast, or try /roast spicy Paolo.\n"
        "Available styles: mild, spicy.\n\n"
        "If an LLM key is set, I use it for smarter context-aware roasts.\n"
        "I keep it playful, not hateful. Nobody needs villain DLC in a group chat."
    )
    await update.effective_message.reply_text(message)


async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    style = choose_style(context)
    target = target_from_update(update, context)
    text = await smart_roast(target=target, style=style, update=update, context=context)
    await update.effective_message.reply_text(text)


async def roast_every_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remember_message(update, context)

    enabled = os.getenv("ROAST_EVERY_TEXT", "false").lower() in {"1", "true", "yes"}
    if not enabled:
        return

    if random.random() > float(os.getenv("ROAST_REPLY_CHANCE", "0.25")):
        return

    style = STYLES.get(DEFAULT_STYLE, STYLES["spicy"])
    target = safe_target(update.effective_user.first_name if update.effective_user else None)
    text = await smart_roast(target=target, style=style, update=update, context=context)
    await update.effective_message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Commands:\n"
        "/roast - roast yourself\n"
        "/roast mild Paolo - mild roast Paolo\n"
        "/roast spicy Paolo - spicy roast Paolo\n"
        "Reply to a message with /roast to roast that sender.\n"
        "Set GROQ_API_KEY or OPENAI_API_KEY for smart contextual roasts."
    )


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before starting the bot.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("roast", roast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, roast_every_text))
    return application


def main() -> None:
    application = build_application()
    LOGGER.info("Roast bot is running. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
