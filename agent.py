"""ADK-based conversational agent for the Amedis booking backend."""

from __future__ import annotations

import logging
import os
import textwrap
import warnings
from dataclasses import dataclass
from typing import Any, Dict, List, Type, Optional
from pydantic import BaseModel, Field

from google.adk import Agent
from google.adk.tools import FunctionTool
from google.adk.tools import ToolContext

from tools import (
    CancelRecordInput,
    CancelRecordTool,
    CreateRecordInput,
    CreateRecordTool,
    DirectionsInput,
    DirectionsTool,
    DoctorsInput,
    DoctorsTool,
    ListRecordsInput,
    ListRecordsTool,
    ScheduleInput,
    ScheduleTool,
    ServicesInput,
    ServicesTool,
)
from tools import resolve_entities, check_availability

# ---------------------------------------------------------------------------
# FunctionTool schemas and top-level wrappers for new KB helpers
# ---------------------------------------------------------------------------


class ResolveEntitiesInput(BaseModel):
    query: str = Field(description="Пытанне/фраза для рэзалвера (паслуга/накірунак/доктар)")


def _ft_resolve_entities(
    payload: ResolveEntitiesInput,
) -> Dict[str, Any]:
    """Resolve natural phrase to canonical IDs (service/direction/doctor).

    Accepts either a Pydantic model or a plain dict payload (as ADK may pass).
    """
    try:
        if isinstance(payload, dict):
            query = payload.get("query")
        else:
            query = getattr(payload, "query", None)
    except Exception:
        query = None

    if not isinstance(query, str) or not query.strip():
        return {"status": "not_found", "entities": []}

    return resolve_entities(query)


class CheckAvailabilityInput(BaseModel):
    doctor_ids: List[str] = Field(
        default_factory=list, description="Спіс doctor_id для праверкі даступнасці"
    )
    duration_min: int = Field(default=0, description="Працягласць паслугі ў хвілінах")
    date_from: Optional[str] = Field(default=None, description="Дата пачатку (ISO)")
    date_to: Optional[str] = Field(default=None, description="Дата заканчэння (ISO)")


def _ft_check_availability(
    payload: CheckAvailabilityInput,
) -> Dict[str, Any]:
    """Return example free slot(s) for doctor(s).

    Accepts either a Pydantic model or a plain dict payload.
    """
    if isinstance(payload, dict):
        doctor_ids = payload.get("doctor_ids") or []
        duration_min = payload.get("duration_min") or 0
        date_from = payload.get("date_from")
        date_to = payload.get("date_to")
    else:
        doctor_ids = getattr(payload, "doctor_ids", []) or []
        duration_min = getattr(payload, "duration_min", 0) or 0
        date_from = getattr(payload, "date_from", None)
        date_to = getattr(payload, "date_to", None)

    if isinstance(doctor_ids, (set, tuple)):
        doctor_ids = list(doctor_ids)
    if not isinstance(doctor_ids, list):
        doctor_ids = [str(doctor_ids)] if doctor_ids else []
    doctor_ids = [str(x) for x in doctor_ids if x is not None]

    try:
        duration_min = int(duration_min)
    except Exception:
        duration_min = 0

    return check_availability(
        doctor_ids,
        duration_min,
        date_from=date_from,
        date_to=date_to,
    )

# Adjust names exposed to the model
_ft_resolve_entities.__name__ = "resolve_entities"
_ft_check_availability.__name__ = "check_availability"


warnings.filterwarnings("ignore", category=UserWarning, module=".*pydantic.*")

logger = logging.getLogger(__name__)


DEFAULT_MODEL = os.getenv("AMEDIS_AGENT_MODEL", "gemini-2.5-flash")
DEFAULT_AGENT_NAME = os.getenv("AMEDIS_AGENT_NAME", "amedis_online_agent")


GLOBAL_INSTRUCTION = textwrap.dedent(
    """
    Ты — ветлівы і дакладны віртуальны рэгістратар медцэнтра Amedis. Твая задача —
    дапамагаць пацыентам кіраваць запісамі: падбіраць напрамкі, доктараў, паслугі,
    часавыя слоты, а таксама пераглядаць або адмяняць існыя запісы.
    Размаўляй ветліва, выкарыстоўвай коратка сфармуляваныя адказы і
    падтрымлівай упэўнены прафесійны тон.
    """
)


AGENT_INSTRUCTION = textwrap.dedent(
    """
    Вядзі дыялог па кроках:
    1. Спачатку спытай чым дапамагчы
    2. Перад выкарыстаннем інструментаў правер, ці ёсць патрэбныя параметры. Калі карыстальнік іх не даў, выкарыстоўвай па змаўчанні: base_url з налад агента і гасцявы token. Не прасі іх без неабходнасці.
    3. Для атрымання даведачнай інфармацыі выкарыстоўвай адпаведныя інструменты:
       directions → doctors → services → schedule. Не пераблытай парадак і
       заўсёды паведамляй, як абраць патрэбны ID або слот.
    4. Для працы з запісамі выкарыстоўвай list_records, cancel_record і
       create_record. Пры стварэнні запісу ўдакладні каментар (калі патрэбна) і
       пацвярджэнне карыстальніка.
    6. Адказвай толькі фактамі, атрыманыя з інструментаў, або пытаннямі для
       ўдакладнення. Калі адбываецца памылка, апішы, што пайшло не так, і
       прапануй наступныя крокі.
    5. Не размаўляй на іншыя тэмы, акрамя кіравання запісамі.
    """
)

# Override prompts with clean guidance for the new KB + tools
GLOBAL_INSTRUCTION = textwrap.dedent(
    """
    Ты — асістэнт для запісу да ўрача ў сістэме Amedis. Падтрымлівай ветлівы і кароткі стыль, дзейнічай па кроках.
    Выкарыстоўвай лакальны файл ведаў amedis_routing.json 
    Не выдумляй звесткі: калі чагосьці не хапае (даты, страхоўшчык і г.д.) — удакладняй у карыстальніка.
    """
)

AGENT_INSTRUCTION = textwrap.dedent(
    """
    Рабочыя прынцыпы:
    1) Зразумей патрэбу і сабяры ключавыя даныя: паслуга/накірунак/доктар, даты/перавагі часу, працягласць, patientAPIId, страхоўшчык (Ins_name).
    2) Каб інтэрпрэтаваць свабодны запыт, выкарыстоўвай інструмент resolve_entities. Калі не атрымалася — удакладні фармулёўку.
    3) Для навігацыі па даных: directions (накірункі), services (паслугі), doctors (урачы). Агент выкарыстоўвае лакальны JSON, калі ён ёсць; інакш — backend Amedis.
    4) Для пошуку часу(свабодных слотаў): schedule. Фарміруй даты як прамежкі часу па тыдням.
    5) Для кіравання запісамі: create_record (стварыць), list_records (паказаць), cancel_record (скасаваць).
    6) Не выдумляй даныя. Прасі пра адсутныя параметры, і запытвай канчатковае пацвярджэнне перад стварэннем запісу.
    7) Формат дат — DD.MM.YYYY або YYYY-MM для ўсяго месяца.
    """
)


@dataclass
class AgentSettings:
    """Наладкі агента Amedis для ініцыялізацыі ADK-агента."""

    name: str = DEFAULT_AGENT_NAME
    model: str = DEFAULT_MODEL
    base_url: str = ""

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

    # Handle plain dict payloads coming from ADK before model coercion
    if isinstance(payload, dict):
        try:
            updated = dict(payload)
            if not updated.get("base_url"):
                updated["base_url"] = base_url
            return updated
        except Exception:  # pragma: no cover - defensive
            logger.debug("Не атрымалася апрацаваць dict payload для %s", tool_name)

    if hasattr(payload, "copy") and hasattr(payload, "base_url"):
        try:
            return payload.copy(update={"base_url": payload.base_url or base_url})
        except Exception:  # pragma: no cover - defensive
            logger.debug("Не атрымалася скапіраваць payload для %s", tool_name)
    # Try to set attribute even if the model doesn't define the field
    try:
        current = getattr(payload, "base_url", None)
        if not current:
            object.__setattr__(payload, "base_url", base_url)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Не атрымалася задаць base_url для %s", tool_name)
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

    def _call(payload) -> Dict[str, Any]:
        # Coerce dict payloads to the tool's Pydantic input model first
        obj = payload
        if isinstance(payload, dict):
            try:
                obj = input_type(**payload)
            except Exception:  # pragma: no cover - defensive guard
                logger.debug("Не атрымалася сканструяваць %s з dict payload", input_type)
                obj = payload

        prepared = _with_default_base_url(obj, base_url=base_url, tool_name=name)
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





def _build_function_tools(settings: AgentSettings) -> List[FunctionTool]:
    """Пабудаваць набор FunctionTool, адаптаваных для ADK агента."""

    base_url = settings.base_url

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

    # Add top-level function tools with Pydantic schemas
    tools.append(FunctionTool(_ft_resolve_entities))
    tools.append(FunctionTool(_ft_check_availability))

    return tools


def build_agent(settings: AgentSettings | None = None) -> Agent:
    """Пабудаваць ADK-агента з наборам інструментаў Amedis."""

    agent_settings = settings or AgentSettings()
    logger.info(
        "Ініцыялізацыя Amedis ADK агента: name=%s, model=%s",
        agent_settings.name,
        agent_settings.model,
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

