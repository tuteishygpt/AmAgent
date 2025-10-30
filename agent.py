"""ADK-based conversational agent for the Amedis booking backend."""

from __future__ import annotations

import logging
import os
import textwrap
import warnings
from dataclasses import dataclass
from typing import Any, Dict, List, Type

from google.adk import Agent
from google.adk.tools import FunctionTool
from google.adk.tools import ToolContext

import amedis_client
from tools import (
    CancelRecordInput,
    CancelRecordTool,
    CreateRecordInput,
    CreateRecordTool,
    DirectionsInput,
    DirectionsTool,
    DoctorsInput,
    DoctorsTool,
    HarAutofillInput,
    HarAutofillTool,
    ListRecordsInput,
    ListRecordsTool,
    ScheduleInput,
    ScheduleTool,
    ServicesInput,
    ServicesTool,
)


warnings.filterwarnings("ignore", category=UserWarning, module=".*pydantic.*")

logger = logging.getLogger(__name__)


DEFAULT_MODEL = os.getenv("AMEDIS_AGENT_MODEL", "gemini-2.5-flash")
DEFAULT_AGENT_NAME = os.getenv("AMEDIS_AGENT_NAME", "amedis_online_agent")
DEFAULT_BASE_URL = os.getenv("AMEDIS_BASE_URL", amedis_client.BASE_URL_DEFAULT)


GLOBAL_INSTRUCTION = textwrap.dedent(
    """
    Ты — ветлівы і дакладны віртуальны рэгістратар медцэнтра Amedis. Твая задача —
    дапамагаць пацыентам кіраваць запісамі: падбіраць напрамкі, доктараў, паслугі,
    часавыя слоты, а таксама пераглядаць або адмяняць існыя запісы.
    Размаўляй па-беларуску, выкарыстоўвай коратка сфармуляваныя адказы і
    падтрымлівай упэўнены прафесійны тон.
    """
)


AGENT_INSTRUCTION = textwrap.dedent(
    """
    Вядзі дыялог па кроках:
    1. Спачатку пераканайся, што атрыманы доступ: токен (token=...), patientAPIId і
       страхоўшчык (Ins_name). Калі чаго няма, акуратна запытай.
    2. Перад выкарыстаннем інструментаў пераканайся, што ў цябе ёсць патрэбныя
       параметры. Калі нейкіх дадзеных не хапае, папрасі іх у карыстальніка.
    3. Для атрымання даведачнай інфармацыі выкарыстоўвай адпаведныя інструменты:
       directions → doctors → services → schedule. Не пераблытай парадак і
       заўсёды паведамляй, як абраць патрэбны ID або слот.
    4. Для працы з запісамі выкарыстоўвай list_records, cancel_record і
       create_record. Пры стварэнні запісу ўдакладні каментар (калі патрэбна) і
       пацвярджэнне карыстальніка.
    5. Калі карыстальнік даслаў HAR-файл, выкліч har_autofill, каб атрымаць
       patientAPIId і страхоўшчыка.
    6. Адказвай толькі фактамі, атрыманыя з інструментаў, або пытаннямі для
       ўдакладнення. Калі адбываецца памылка, апішы, што пайшло не так, і
       прапануй наступныя крокі.
    """
)


@dataclass
class AgentSettings:
    """Наладкі агента Amedis для ініцыялізацыі ADK-агента."""

    name: str = DEFAULT_AGENT_NAME
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.model = self.model.strip()
        self.base_url = self.base_url.strip()
        if self.model and not self.model.startswith("gemini-2.5-flash"):
            logger.warning(
                "Выкарыстоўваецца мадэль па-за сямействам gemini-2.5-flash: %s",
                self.model,
            )


def _with_default_base_url(
    payload: Any, *, base_url: str, tool_name: str
) -> Any:
    """Ensure that BaseToolInput payloads always carry a base URL."""

    if hasattr(payload, "copy") and hasattr(payload, "base_url"):
        try:
            return payload.copy(update={"base_url": payload.base_url or base_url})
        except Exception:  # pragma: no cover - defensive
            logger.debug("Не атрымалася скапіраваць payload для %s", tool_name)
    if hasattr(payload, "base_url") and not getattr(payload, "base_url"):
        setattr(payload, "base_url", base_url)
    return payload


def _wrap_tool(
    *,
    tool_impl: Any,
    base_url: str,
    input_type: Type[Any],
    name: str,
    description: str,
) -> FunctionTool:
    """Build a FunctionTool wrapper around the legacy tool implementation."""

    def _call(payload, tool_context: ToolContext | None = None) -> Dict[str, Any]:
        prepared = _with_default_base_url(payload, base_url=base_url, tool_name=name)
        try:
            result = tool_impl.call(prepared)
        except Exception as exc:  # pragma: no cover - network/IO defensive guard
            logger.exception("Памылка падчас выканання інструмента %s", name)
            return {
                "error": str(exc),
                "tool": name,
            }
        if hasattr(result, "dict"):
            return result.dict()
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result  # pragma: no cover - fallback

    _call.__name__ = name
    _call.__doc__ = description
    _call.__annotations__["payload"] = input_type
    return FunctionTool(_call)


def _wrap_har_tool(tool_impl: HarAutofillTool, name: str, description: str) -> FunctionTool:
    """Special wrapper for HAR апрацоўку (base_url не патрэбны)."""

    def _call(payload: HarAutofillInput) -> Dict[str, Any]:
        try:
            result = tool_impl.call(payload)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Памылка HAR інструмента %s", name)
            return {
                "error": str(exc),
                "tool": name,
            }
        if hasattr(result, "dict"):
            return result.dict()
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result  # pragma: no cover - fallback

    _call.__name__ = name
    _call.__doc__ = description
    return FunctionTool(_call)


def _build_function_tools(settings: AgentSettings) -> List[FunctionTool]:
    """Пабудаваць набор FunctionTool, адаптаваных для ADK агента."""

    base_url = settings.base_url or amedis_client.BASE_URL_DEFAULT

    tool_specs = [
        (
            DirectionsTool(),
            DirectionsInput,
            "directions",
            "Атрымлівае спіс напрамкаў прыёму для пацыента.",
        ),
        (
            DoctorsTool(),
            DoctorsInput,
            "doctors",
            "Атрымлівае спіс доктараў у межах напрамку.",
        ),
        (
            ServicesTool(),
            ServicesInput,
            "services",
            "Пералічвае паслугі, даступныя ў выбраным напрамку.",
        ),
        (
            ScheduleTool(),
            ScheduleInput,
            "schedule",
            "Знаходзіць свабодныя слоты для доктара і паслугі ў дыяпазоне дат.",
        ),
        (
            CreateRecordTool(),
            CreateRecordInput,
            "create_record",
            "Стварае новы запіс да ўрача па выбраным слоце.",
        ),
        (
            ListRecordsTool(),
            ListRecordsInput,
            "list_records",
            "Паказвае будучыя запісы пацыента.",
        ),
        (
            CancelRecordTool(),
            CancelRecordInput,
            "cancel_record",
            "Змяняе статус запісу на адмяну.",
        ),
    ]

    tools: List[FunctionTool] = [
        _wrap_tool(
            tool_impl=impl,
            base_url=base_url,
            input_type=input_type,
            name=name,
            description=description,
        )
        for impl, input_type, name, description in tool_specs
    ]

    tools.append(
        _wrap_har_tool(
            HarAutofillTool(),
            name="har_autofill",
            description="Аналізуе HAR-файл для пошуку patientAPIId і страхоўшчыка.",
        )
    )

    return tools


def build_agent(settings: AgentSettings | None = None) -> Agent:
    """Пабудаваць ADK-агента з наборам інструментаў Amedis."""

    agent_settings = settings or AgentSettings()
    logger.info(
        "Ініцыялізацыя Amedis ADK агента: name=%s, model=%s, base_url=%s",
        agent_settings.name,
        agent_settings.model,
        agent_settings.base_url,
    )

    tools = _build_function_tools(agent_settings)

    return Agent(
        name=agent_settings.name,
        model=agent_settings.model,
        global_instruction=GLOBAL_INSTRUCTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )


agent = build_agent()


__all__ = [
    "AgentSettings",
    "agent",
    "build_agent",
]

