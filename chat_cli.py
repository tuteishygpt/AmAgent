"""CLI для лакальнага чат-ўзаемадзеяння з Amedis агентам."""

from __future__ import annotations

import os
from dotenv import load_dotenv
import argparse
import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from google.adk import Runner
from google.adk.artifacts.in_memory_artifact_service import (
    InMemoryArtifactService,
)
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from agent import AgentSettings, build_agent

# Load environment variables from .env file
load_dotenv()
from gemini_token import (
    GEMINI_API_KEY_ENV,
    ensure_gemini_token,
    resolve_token_path,
)


_DEFAULT_LOG_PATH = Path("amedis_agent_errors.log")
_EXIT_COMMANDS = {"/exit", ":exit", "выход", "выхад", "quit"}
_ERROR_COMMAND = ":errors"
_FLASH_FAMILY_PREFIX = "gemini-2.5-flash"


def _validate_model_choice(value: str) -> str:
    cleaned = value.strip()
    if not cleaned.startswith(_FLASH_FAMILY_PREFIX):
        raise argparse.ArgumentTypeError(
            (
                "Падтрымліваюцца толькі мадэлі сям'і %s (атрымана: %s)"
                % (_FLASH_FAMILY_PREFIX, cleaned)
            )
        )
    return cleaned


def _configure_logging(log_path: Optional[Path]) -> Optional[Path]:
    """Настроіць лагаванне для кансолі і файла памылак."""

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO)
    root_logger.setLevel(logging.INFO)

    # Пераканаемся, што няма дубляваных stream-хэндлераў.
    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    if not has_stream_handler:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
        )
        root_logger.addHandler(console_handler)

    if not log_path:
        return None

    log_path = log_path.expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(file_handler)
    logging.captureWarnings(True)
    root_logger.info("Памылкі будуць захоўвацца ў %s", log_path)
    return log_path


async def _ensure_session(
    session_service: InMemorySessionService,
    *,
    app_name: str,
    user_id: str,
    session_id: Optional[str] = None,
):
    """Атрымлівае існы або стварае новы сеанс для дыялогу."""

    if session_id:
        session = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        if session:
            return session
    return await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )


def _iter_agent_events(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    message: str,
) -> Iterable[str]:
    """Пераўтварае падзеі runner у чалавекочытальны тэкст."""

    content = types.Content(
        role="user", parts=[types.Part(text=message.strip())]
    )

    response_buffer: deque[str] = deque()

    for event in runner.run(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if event.error_code:
            logging.error(
                "Памылка падчас выканання агента: %s - %s",
                event.error_code,
                event.error_message,
            )
            continue

        if not event.content or not event.content.parts:
            continue

        text_parts = [
            part.text for part in event.content.parts if getattr(part, "text", None)
        ]
        if text_parts:
            response_buffer.extend(text_parts)
            yield "\n".join(text_parts)

    if not response_buffer:
        yield "(Агент не вярнуў адказ.)"


def _show_error_logs(log_path: Optional[Path], *, limit: int = 20) -> None:
    """Друкуе апошнія радкі з файла памылак."""

    if not log_path:
        print("Файл для памылак не зададзены. Выкарыстайце --log-file.")
        return
    if not log_path.exists():
        print("Пакуль няма запісаных памылак.")
        return

    with log_path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()

    tail = lines[-limit:]
    if not tail:
        print("Пакуль няма запісаных памылак.")
        return

    print("\n--- Апошнія памылкі агента ---")
    for line in tail:
        print(line.rstrip())
    print("--- Канец лагу ---\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Камандная радок для дыялогу з Amedis агентам",
    )
    parser.add_argument(
        "--user-id",
        default="local-user",
        help="Ідэнтыфікатар карыстальніка ў рамках Runner",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Неабавязковы ідэнтыфікатар сеансу для аднаўлення гісторыі",
    )
    parser.add_argument(
        "--model",
        default=None,
        type=_validate_model_choice,
        help="Назва мадэлі Gemini з сям'і gemini-2.5-flash",
    )
    parser.add_argument(
        "--flash-lite",
        nargs="?",
        const="gemini-2.5-flash-lite",
        default=None,
        metavar="MODEL",
        help=(
            "Хуткае пераключэнне на gemini-2.5-flash-lite або іншы яе варыянт. "
            "Без аргумента выкарыстоўваецца gemini-2.5-flash-lite."
        ),
    )
    parser.add_argument(
        "--gemini-token",
        default=None,
        help="API токен для доступу да мадэляў Gemini",
    )
    parser.add_argument(
        "--save-gemini-token",
        action="store_true",
        help="Захаваць перададзены або існы Gemini токен на дыску",
    )
    parser.add_argument(
        "--gemini-token-path",
        type=Path,
        default=None,
        help="Карыстальніцкі шлях для файла з Gemini токенам",
    )
    parser.add_argument(
        "--agent-name",
        default=None,
        help="Назва агента (па змаўчанні amedis_online_agent)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Базавы URL backend Amedis",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=_DEFAULT_LOG_PATH,
        help="Шлях да файла, куды запісваць памылкі",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Не ствараць файл з памылкамі",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    log_path = None if args.no_log else _configure_logging(args.log_file)

    token_path = resolve_token_path(args.gemini_token_path)
    gemini_token, token_source = ensure_gemini_token(
        args.gemini_token,
        persist=args.save_gemini_token,
        path=token_path,
    )
    if gemini_token:
        if token_source == "cli":
            logging.info(
                "Выкарыстоўваецца Gemini API токен, перададзены праз CLI%s.",
                " і захаваны" if args.save_gemini_token else "",
            )
            if args.save_gemini_token:
                logging.info("Токен запісаны ў %s", token_path)
        elif token_source == "env":
            logging.info(
                "Выкарыстоўваецца Gemini API токен з %s%s.",
                GEMINI_API_KEY_ENV,
                " (захаваны на дыску)" if args.save_gemini_token else "",
            )
            if args.save_gemini_token:
                logging.info("Токен запісаны ў %s", token_path)
        else:
            logging.info("Выкарыстоўваецца Gemini API токен з файла %s", token_path)
    else:
        logging.warning(
            "Gemini API токен не знойдзены. Усталюйце %s або выкарыстайце --gemini-token.",
            GEMINI_API_KEY_ENV,
        )
        if args.save_gemini_token:
            logging.warning(
                "Флаг --save-gemini-token ігнаруецца, бо токен не знойдзены",
            )

    settings = AgentSettings()
    model_choice: Optional[str] = args.model
    if args.flash_lite is not None:
        model_choice = _validate_model_choice(args.flash_lite)
    if model_choice:
        settings.model = model_choice
    if args.agent_name:
        settings.name = args.agent_name
    if args.base_url:
        settings.base_url = args.base_url

    agent = build_agent(settings)

    session_service = InMemorySessionService()
    memory_service = InMemoryMemoryService()
    artifact_service = InMemoryArtifactService()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session = loop.run_until_complete(
            _ensure_session(
                session_service,
                app_name=settings.name,
                user_id=args.user_id,
                session_id=args.session_id,
            )
        )
    finally:
        loop.close()

    runner = Runner(
        app_name=settings.name,
        agent=agent,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )

    print("Прывітанне! Уводзьце паведамленні для агента. Для выхаду друкуйце /exit.")
    print(
        "Каб паглядзець апошнія памылкі, выкарыстайце каманду :errors."
    )

    session_id = session.id
    while True:
        try:
            user_message = input("Вы: ").strip()
        except EOFError:
            print()
            break

        if not user_message:
            continue

        lower = user_message.lower()
        if lower in _EXIT_COMMANDS:
            break
        if user_message.strip().lower() == _ERROR_COMMAND:
            _show_error_logs(log_path)
            continue

        try:
            for reply in _iter_agent_events(
                runner,
                user_id=args.user_id,
                session_id=session_id,
                message=user_message,
            ):
                if reply:
                    print(f"Агент: {reply}")
        except Exception as exc:  # pragma: no cover - інтэрактыўны safeguard
            logging.exception("Непрадбачаная памылка: %s", exc)
            print(
                "Адбылася памылка, падрабязнасці глядзіце ў лагу: ",
                log_path or "(лог адключаны)",
            )

    print("Да пабачэння!")


if __name__ == "__main__":
    main()

