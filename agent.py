"""High level conversational agent for the Amedis online booking backend."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


SYSTEM_PROMPT = (
    "Ты — віртуальны рэгістратар медцэнтра Amedis. Ты дапамагаеш карыстальніку "
    "знайсці ўрача, выбраць слот, стварыць або адмяніць запіс. Пытайся толькі "
    "тое, што патрэбна для наступнага кроку."
)


@dataclass
class SlotSelection:
    startAt: str
    endAt: Optional[str]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentState:
    base_url: str = amedis_client.BASE_URL_DEFAULT
    token: Optional[str] = None
    patient_id: Optional[str] = None
    insurer: Optional[str] = None
    direction_id: Optional[str] = None
    doctor_id: Optional[str] = None
    service_id: Optional[str] = None
    description: Optional[str] = None
    slot: Optional[SlotSelection] = None
    goal: Optional[str] = None
    stage: Optional[str] = None
    last_directions: List[Dict[str, Any]] = field(default_factory=list)
    last_doctors: List[Dict[str, Any]] = field(default_factory=list)
    last_services: List[Dict[str, Any]] = field(default_factory=list)
    last_slots: List[Dict[str, Any]] = field(default_factory=list)
    last_records: List[Dict[str, Any]] = field(default_factory=list)

    def reset_flow(self) -> None:
        self.direction_id = None
        self.doctor_id = None
        self.service_id = None
        self.description = None
        self.slot = None
        self.goal = None
        self.stage = None
        self.last_directions.clear()
        self.last_doctors.clear()
        self.last_services.clear()
        self.last_slots.clear()


class AmedisOnlineAgent:
    """Conversational policy wrapper that orchestrates tools."""

    def __init__(self, tools: Optional[List[Any]] = None, base_url: Optional[str] = None):
        self.system_prompt = SYSTEM_PROMPT
        self.state = AgentState(base_url=base_url or amedis_client.BASE_URL_DEFAULT)
        self.tools: Dict[str, Any] = {}
        if tools:
            for tool in tools:
                self.tools[tool.name] = tool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.state = AgentState(base_url=self.state.base_url)

    def handle_user_message(self, message: str) -> str:
        message = message.strip()
        if not message:
            return "Калі ласка, паведаміце, чым я магу дапамагчы."

        har_response = self._maybe_handle_har(message)
        if har_response:
            return har_response

        if not self.state.token:
            token = self._extract_token(message)
            if token:
                self.state.token = token
                return (
                    "Дзякуй, токен атрыманы і захаваны. Цяпер патрэбны patientAPIId, "
                    "каб працаваць з вашымі запісамі."
                )
            return (
                "Перш за ўсё мне патрэбны ваш токен доступу пацыента. "
                "Адпраўце яго ў выглядзе 'token=...'."
            )

        if not self.state.patient_id:
            patient_id = self._extract_patient_id(message)
            if patient_id:
                self.state.patient_id = patient_id
                return (
                    "Адзначыла ваш patientAPIId. Калі ласка, паведаміце страхоўшчыка "
                    "(Ins_name), каб працягнуць."
                )
            return (
                "Калі ласка, паведамiце patientAPIId. Можна проста напісаць "
                "лічбу або фразу 'patient=12345'."
            )

        if not self.state.insurer:
            insurer = self._extract_insurer(message)
            if insurer:
                self.state.insurer = insurer
            else:
                return (
                    "Мне патрэбная назва страхоўшчыка (Ins_name). Напішыце, напрыклад, "
                    "'страхоўка = Белдзяржстрах'."
                )

        lower = message.lower()
        if self._is_cancel_request(lower):
            return self._handle_cancel(message)
        if self._is_list_request(lower):
            return self._handle_list_records()
        if self._is_create_request(lower):
            self._begin_create_flow()

        if self.state.goal == "create_record":
            return self._handle_create_flow(message)

        if "напрамк" in lower or "спецыяль" in lower:
            return self._handle_show_directions()
        if "доктар" in lower:
            return self._handle_show_doctors()
        if "паслуг" in lower:
            return self._handle_show_services()
        if "слот" in lower or "расклад" in lower:
            return self._handle_show_slots()

        return (
            "Я гатовая дапамагчы: можна папрасіць паказаць напрамкі, доктараў, "
            "паслугі, вольныя слоты, стварыць або адмяніць запіс."
        )

    # ------------------------------------------------------------------
    # HAR support
    # ------------------------------------------------------------------

    def _maybe_handle_har(self, message: str) -> Optional[str]:
        har_path = None
        match = re.search(r"([\w./\\-]+\.har)", message, flags=re.IGNORECASE)
        if match:
            har_path = match.group(1)
        if not har_path:
            return None
        tool = self.tools.get("har_autofill")
        if not tool:
            return (
                "Я не магу апрацаваць HAR без адпаведнага інструмента. "
                "Калі ласка, перадайце patientAPIId уручную."
            )
        output = tool.call(HarAutofillInput(har_path=har_path))
        response_parts = ["Прайшла па HAR-файле."]
        if output.patient_ids:
            self.state.patient_id = output.patient_ids[0]
            response_parts.append(
                f"Знойдзены patientAPIId: {', '.join(output.patient_ids)}"
            )
        if output.insurer_guess and not self.state.insurer:
            self.state.insurer = output.insurer_guess
            response_parts.append(
                f"Магчымы страхоўшчык: {output.insurer_guess}"
            )
        if output.record_fields:
            response_parts.append(
                "Палі формы /record/create: " + ", ".join(output.record_fields)
            )
        return "\n".join(response_parts)

    # ------------------------------------------------------------------
    # High level intent helpers
    # ------------------------------------------------------------------

    def _handle_list_records(self) -> str:
        tool = self.tools.get("list_records")
        if not tool:
            return "Інструмент для спісу запісаў недаступны."
        try:
            output = tool.call(
                ListRecordsInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    patient_id=self.state.patient_id or "",
                )
            )
        except Exception as exc:
            return (
                "Не атрымалася атрымаць спіс запісаў. Магчыма, токен састарэў "
                f"або backend недаступны ({exc})."
            )
        self.state.last_records = [record.dict() for record in output.records]
        if not output.records:
            return "У вашым асабістым кабінеце няма будучых запісаў."
        lines = ["Вашы запісы:"]
        for record in output.records:
            lines.append(
                f"• №{record.recordId}: {record.startAt or '?'} — {record.status or 'статус невядомы'}"
            )
        lines.append(
            "Калі патрэбна адмяніць запіс, напішыце 'адмяні №<id>'."
        )
        return "\n".join(lines)

    def _handle_cancel(self, message: str) -> str:
        record_id = self._extract_record_id(message)
        if not record_id:
            return "Калі ласка, пазначце нумар запісу, які трэба адмяніць."
        tool = self.tools.get("cancel_record")
        if not tool:
            return "Інструмент для адмены запісу недаступны."
        try:
            output = tool.call(
                CancelRecordInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    record_id=record_id,
                    cancel_status="CAN",
                )
            )
        except Exception as exc:
            return (
                "Не атрымалася адмяніць запіс. Праверце, ці існуе такі нумар, "
                f"і паспрабуйце зноў ({exc})."
            )
        if output.status_code != 200:
            return (
                "Backend не пацвердзіў адмену. Магчыма, запіс ужо завершаны "
                "або статус нельга змяніць."
            )
        return f"Запіс №{record_id} адзначаны як адменены."

    def _handle_show_directions(self) -> str:
        tool = self.tools.get("directions")
        if not tool:
            return "Інструмент напрамкаў недаступны."
        try:
            output = tool.call(
                DirectionsInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                )
            )
        except Exception as exc:
            return f"Не атрымалася атрымаць напрамкі: {exc}"
        self.state.last_directions = [item.dict() for item in output.directions]
        if not output.directions:
            return "Напрамкі не знойдзены. Праверце токен або паспрабуйце пазней."
        lines = ["Даступныя напрамкі:"]
        for item in output.directions:
            label = f"{item.id} — {item.name}" if item.name else str(item.id)
            lines.append(f"• {label}")
        return "\n".join(lines)

    def _handle_show_doctors(self) -> str:
        if not self.state.direction_id and not self.state.last_directions:
            hint = self._handle_show_directions()
            return hint + "\nПасля абярыце ID напрамку." if hint else hint
        tool = self.tools.get("doctors")
        if not tool:
            return "Інструмент доктараў недаступны."
        direction_id = self.state.direction_id or self._guess_direction_from_last()
        try:
            output = tool.call(
                DoctorsInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    direction_id=direction_id,
                )
            )
        except Exception as exc:
            return f"Не атрымалася атрымаць доктараў: {exc}"
        self.state.last_doctors = [item.dict() for item in output.doctors]
        if not output.doctors:
            return "Па гэтым напрамку доктары не знойдзены."
        lines = ["Даступныя доктары:"]
        for item in output.doctors:
            lines.append(f"• {item.id} — {item.name or 'без імя'}")
        return "\n".join(lines)

    def _handle_show_services(self) -> str:
        if not self.state.direction_id and not self.state.last_directions:
            hint = self._handle_show_directions()
            return hint + "\nАбярыце напрамак, каб паглядзець паслугі." if hint else hint
        tool = self.tools.get("services")
        if not tool:
            return "Інструмент паслуг недаступны."
        direction_id = self.state.direction_id or self._guess_direction_from_last()
        try:
            output = tool.call(
                ServicesInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    direction_id=direction_id,
                )
            )
        except Exception as exc:
            return f"Не атрымалася атрымаць паслугі: {exc}"
        self.state.last_services = [item.dict() for item in output.services]
        if not output.services:
            return "Паслугі для гэтага напрамку не знойдзены."
        lines = ["Даступныя паслугі:"]
        for item in output.services:
            duration = f" ({item.duration_minutes} хв)" if item.duration_minutes else ""
            lines.append(f"• {item.id} — {item.name or 'без назвы'}{duration}")
        return "\n".join(lines)

    def _handle_show_slots(self) -> str:
        if not self.state.doctor_id or not self.state.service_id:
            return "Спачатку выберыце доктара і паслугу."
        if not self.state.last_slots:
            return "Патрэбна паведаміць перыяд дат, каб паказаць слоты."
        lines = ["Апошнія атрыманыя слоты:"]
        for idx, slot in enumerate(self.state.last_slots, start=1):
            start = slot.get("startAt")
            end = slot.get("endAt")
            label = f"{start}"
            if end:
                label += f" — {end}"
            lines.append(f"{idx}. {label}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Create flow orchestration
    # ------------------------------------------------------------------

    def _begin_create_flow(self) -> None:
        self.state.goal = "create_record"
        self.state.stage = "choose_direction"
        self.state.direction_id = None
        self.state.doctor_id = None
        self.state.service_id = None
        self.state.slot = None
        self.state.description = None
        self.state.last_slots.clear()

    def _handle_create_flow(self, message: str) -> str:
        lower = message.lower()
        if self.state.stage == "choose_direction":
            selected = self._match_id_from_message(message, self.state.last_directions)
            if selected:
                self.state.direction_id = selected
                self.state.stage = "choose_doctor"
                return (
                    "Напрамак зафіксаваны. Цяпер абярыце доктара.\n"
                    + self._handle_show_doctors()
                )
            response = self._handle_show_directions()
            return response + "\nАдпраўце ID патрэбнага напрамку."

        if self.state.stage == "choose_doctor":
            selected = self._match_id_from_message(message, self.state.last_doctors)
            if selected:
                self.state.doctor_id = selected
                self.state.stage = "choose_service"
                return (
                    "Доктар выбраны. Далей патрэбная паслуга.\n"
                    + self._handle_show_services()
                )
            response = self._handle_show_doctors()
            return response + "\nАдпраўце ID патрэбнага доктара."

        if self.state.stage == "choose_service":
            selected = self._match_id_from_message(message, self.state.last_services)
            if selected:
                self.state.service_id = selected
                self.state.stage = "date_range"
                return (
                    "Паслуга зафіксаваная. Пакажыце дыяпазон дат у фармаце "
                    "ДД.ММ.ГГГГ - ДД.ММ.ГГГГ, каб праверыць слоты."
                )
            response = self._handle_show_services()
            return response + "\nАдпраўце ID патрэбнай паслугі."

        if self.state.stage == "date_range":
            date_start, date_end = self._extract_date_range(lower)
            if date_start and date_end:
                slots_text = self._fetch_slots(date_start, date_end)
                if "не знойдзены" in slots_text.lower():
                    return slots_text
                self.state.stage = "pick_slot"
                return (
                    slots_text
                    + "\nВыберыце слот: можна напісаць нумар са спісу або дату і час."
                )
            return (
                "Не атрымалася распазнаць даты. Прыклад: 'з 01.06.2024 па 07.06.2024'."
            )

        if self.state.stage == "pick_slot":
            slot = self._match_slot_from_message(message)
            if slot:
                self.state.slot = slot
                self.state.stage = "confirm"
                return (
                    "Слот зафіксаваны. Пацвердзіце, калі гатовыя стварыць запіс. "
                    "Можна дадаць каментар (напрыклад, 'каментар: паўторны прыём')."
                )
            return "Не знайшла такі слот. Напішыце нумар або дакладны час."

        if self.state.stage == "confirm":
            insurer = self._extract_insurer(message)
            if insurer:
                self.state.insurer = insurer
            description = self._extract_description(message)
            if description:
                self.state.description = description
            if self._is_confirmation(lower):
                return self._finalize_record()
            if "адмена" in lower or "не" in lower:
                self.state.reset_flow()
                return "Добра, скасавала працэс запісу."
            return "Калі гатовы, скажыце 'так' для пацвярджэння або 'адмена'."

        return "Не магу працягнуць. Пачнём нанова: напішыце, што хочаце запісацца."

    def _fetch_slots(self, date_start: str, date_end: str) -> str:
        tool = self.tools.get("schedule")
        if not tool:
            return "Інструмент для раскладу недаступны."
        try:
            output = tool.call(
                ScheduleInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    doctor_id=self.state.doctor_id or "",
                    service_id=self.state.service_id,
                    date_start=date_start,
                    date_end=date_end,
                )
            )
        except Exception as exc:
            return f"Не атрымалася атрымаць слоты: {exc}"
        self.state.last_slots = [item.dict() for item in output.slots]
        if not output.slots:
            return "Вольныя слоты не знойдзены ў гэтым дыяпазоне."
        lines = ["Знойдзеныя слоты:"]
        for idx, slot in enumerate(output.slots, start=1):
            label = f"{slot.startAt}"
            if slot.endAt:
                label += f" — {slot.endAt}"
            lines.append(f"{idx}. {label}")
        return "\n".join(lines)

    def _finalize_record(self) -> str:
        if not (self.state.slot and self.state.slot.startAt):
            return "Не выбраны слот для запісу."
        tool = self.tools.get("create_record")
        if not tool:
            return "Інструмент стварэння запісу недаступны."
        extra = self._build_extra_from_slot(self.state.slot)
        try:
            output = tool.call(
                CreateRecordInput(
                    base_url=self.state.base_url,
                    token=self.state.token or "",
                    doctor_id=self.state.doctor_id or "",
                    patient_id=self.state.patient_id or "",
                    startAt=self.state.slot.startAt,
                    endAt=self.state.slot.endAt,
                    description=self.state.description or "",
                    insurer=self.state.insurer or "",
                    extra=extra,
                )
            )
        except Exception as exc:
            return f"Не атрымалася стварыць запіс: {exc}"
        if output.status_code != 200 or output.error:
            return (
                "Backend не пацвердзіў запіс. Праверце выбраныя даныя і паспрабуйце зноў."
            )
        record_id = (
            output.data.get("recordId") if output.data and isinstance(output.data, dict) else None
        )
        self.state.reset_flow()
        if record_id:
            return f"Гатова! Запіс створаны. Нумар: {record_id}."
        return "Гатова! Запіс створаны, але backend не вярнуў нумар."

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _is_cancel_request(self, lower: str) -> bool:
        return "адмяні" in lower or "скасуй" in lower

    def _is_list_request(self, lower: str) -> bool:
        return ("якія" in lower and "запіс" in lower) or (
            "пакажы" in lower and "запіс" in lower
        )

    def _is_create_request(self, lower: str) -> bool:
        return "запісац" in lower or "ствары запіс" in lower or "запіс да" in lower

    def _extract_token(self, message: str) -> Optional[str]:
        match = re.search(r"token\s*[=:]\s*([\w.-]+)", message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if len(message) > 20 and re.fullmatch(r"[A-Za-z0-9._-]+", message):
            return message
        return None

    def _extract_patient_id(self, message: str) -> Optional[str]:
        match = re.search(r"patient\s*[=:]\s*(\d+)", message, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d{3,})\b", message)
        if match:
            return match.group(1)
        return None

    def _extract_insurer(self, message: str) -> Optional[str]:
        match = re.search(
            r"(?:ins_name|insurer|страх[а-яё]+)\s*[=:]\s*(.+)",
            message,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return None

    def _extract_record_id(self, message: str) -> Optional[str]:
        match = re.search(r"№\s*(\d+)", message)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d{3,})\b", message)
        if match:
            return match.group(1)
        return None

    def _extract_description(self, message: str) -> Optional[str]:
        match = re.search(r"(?:каментар|comment)\s*[:=]\s*(.+)", message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_date_range(self, lower: str) -> tuple[Optional[str], Optional[str]]:
        dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", lower)
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], dates[0]
        return None, None

    def _is_confirmation(self, lower: str) -> bool:
        return any(word in lower for word in ["так", "пацвярджаю", "ок", "добра"])

    def _match_id_from_message(
        self, message: str, items: List[Dict[str, Any]]
    ) -> Optional[str]:
        if not items:
            return None
        ids = {str(item.get("id")) for item in items if item.get("id")}
        match = re.search(r"\b(\d+)\b", message)
        if match and match.group(1) in ids:
            return match.group(1)
        for item in items:
            name = str(item.get("name") or "").lower()
            if name and name in message.lower():
                return str(item.get("id"))
        return None

    def _match_slot_from_message(self, message: str) -> Optional[SlotSelection]:
        if not self.state.last_slots:
            return None
        match = re.search(r"\b(\d+)\b", message)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(self.state.last_slots):
                raw = self.state.last_slots[idx]
                return SlotSelection(
                    startAt=raw.get("startAt"),
                    endAt=raw.get("endAt"),
                    raw=raw.get("raw", {}),
                )
        for raw in self.state.last_slots:
            start = str(raw.get("startAt") or "").lower()
            if start and start in message.lower():
                return SlotSelection(
                    startAt=raw.get("startAt"),
                    endAt=raw.get("endAt"),
                    raw=raw.get("raw", {}),
                )
        return None

    def _guess_direction_from_last(self) -> Optional[str]:
        if self.state.last_directions:
            return str(self.state.last_directions[0].get("id"))
        return None

    def _build_extra_from_slot(self, slot: SlotSelection) -> Dict[str, Any]:
        raw = slot.raw or {}
        extra: Dict[str, Any] = {}
        for key in [
            "officeId",
            "cabinetId",
            "serviceId",
            "directionId",
            "office",
            "cabinet",
        ]:
            if key in raw and raw[key] not in (None, ""):
                extra[key] = raw[key]
        if self.state.service_id and "serviceId" not in extra:
            extra["serviceId"] = self.state.service_id
        if self.state.direction_id and "directionId" not in extra:
            extra["directionId"] = self.state.direction_id
        return extra


def build_agent() -> AmedisOnlineAgent:
    tools = [
        DirectionsTool(),
        DoctorsTool(),
        ServicesTool(),
        ScheduleTool(),
        CreateRecordTool(),
        ListRecordsTool(),
        CancelRecordTool(),
        HarAutofillTool(),
    ]
    return AmedisOnlineAgent(tools=tools)


agent = build_agent()
