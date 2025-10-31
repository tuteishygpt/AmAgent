"""ADK tool wrappers around the low-level Amedis client."""

from __future__ import annotations

import os
import re
import json
import calendar
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

import amedis_client

# ---------------------------------------------------------------------------
# Local routing KB (JSON) and simple resolver
# ---------------------------------------------------------------------------

# 1) Load routing knowledge (routing JSON) into process memory
ROUTING_JSON = "amedis_routing.json"
USE_LOCAL_KB: bool = str(os.getenv("AMEDIS_USE_LOCAL_KB", "0")).strip().lower() in {"1", "true", "yes", "y"}
KB: Dict[str, Any] | None = None
try:
    with open(ROUTING_JSON, "r", encoding="utf-8") as f:
        KB = json.load(f)
except Exception:
    KB = None  # Fallback to remote API when KB is unavailable

# Quick references if KB is present
ENT: Dict[str, Any] = KB.get("entities", {}) if KB else {}
IDX: Dict[str, Any] = KB.get("index", {}) if KB else {}


class Resolver:
    """Simple resolver for the new KB structure (entities/index)."""

    def __init__(self, kb: Dict[str, Any]):
        self.kb = kb
        self.ent = kb.get("entities", {})
        self.idx = kb.get("index", {})

        # Precompute lowercase name -> id maps
        self.service_name2id: Dict[str, str] = {
            (v.get("service_name") or "").strip().lower(): sid
            for sid, v in self.ent.get("services", {}).items()
            if isinstance(v, dict) and isinstance(v.get("service_name"), str)
        }
        self.direction_name2id: Dict[str, str] = {
            (v.get("direction_name") or "").strip().lower(): did
            for did, v in self.ent.get("directions", {}).items()
            if isinstance(v, dict) and isinstance(v.get("direction_name"), str)
        }
        self.doctor_name2id: Dict[str, str] = {
            (v.get("doctor_name") or "").strip().lower(): did
            for did, v in self.ent.get("doctors", {}).items()
            if isinstance(v, dict) and isinstance(v.get("doctor_name"), str)
        }

    def normalize(self, text: str) -> Optional[Dict[str, str]]:
        t = (text or "").strip().lower()
        if not t:
            return None

        # Allow direct IDs
        if t in self.ent.get("services", {}):
            return {"kind": "service", "id": t}
        if t in self.ent.get("directions", {}):
            return {"kind": "direction", "id": t}
        if t in self.ent.get("doctors", {}):
            return {"kind": "doctor", "id": t}

        # Match by names
        if t in self.service_name2id:
            return {"kind": "service", "id": self.service_name2id[t]}
        if t in self.direction_name2id:
            return {"kind": "direction", "id": self.direction_name2id[t]}
        if t in self.doctor_name2id:
            return {"kind": "doctor", "id": self.doctor_name2id[t]}
        return None

    def doctors_for_service(self, service_id: str) -> List[str]:
        return self.idx.get("by_service", {}).get(service_id, {}).get("doctors", [])

    def services_for_direction(self, direction_id: str) -> List[str]:
        return self.idx.get("by_direction", {}).get(direction_id, {}).get("services", [])


RESOLVER: Resolver | None = Resolver(KB) if KB else None

# 3) Function-style tools helpers
def resolve_entities(query: str) -> dict:
    """
    Resolve a free-form user phrase (service/direction/doctor) to canonical IDs from the routing JSON.
    Args:
      query: Natural language like "Удаление папиллом" / "Дерматология" / "Иванов"
    Returns:
      { status: "success"|"not_found", entities: [{kind,id}], hints?: {doctors,services,directions} }
    """
    if not RESOLVER or not USE_LOCAL_KB:
        return {"status": "not_found", "entities": []}
    ent = RESOLVER.normalize(query)
    if not ent:
        return {"status": "not_found", "entities": []}

    hints: Dict[str, List[str]] = {}
    if ent["kind"] == "service":
        hints["doctors"] = RESOLVER.doctors_for_service(ent["id"])  # type: ignore[arg-type]
        # Also provide directions that include this service
        hints["directions"] = IDX.get("by_service", {}).get(ent["id"], {}).get("directions", [])
    elif ent["kind"] == "direction":
        hints["services"] = RESOLVER.services_for_direction(ent["id"])  # type: ignore[arg-type]
    elif ent["kind"] == "doctor":
        hints["services"] = IDX.get("doctor_to_services", {}).get(ent["id"], [])
        hints["directions"] = IDX.get("doctor_to_directions", {}).get(ent["id"], [])
    return {"status": "success", "entities": [ent], "hints": hints}


def check_availability(
    doctor_ids: List[str],
    duration_min: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """
    Check appointment availability for one or more doctors.
    Args:
      doctor_ids: canonical doctor IDs (e.g., "doc_ivanov")
      duration_min: service duration in minutes
      date_from/date_to: optional ISO dates to constrain the search
    Returns:
      {status: "success", slots: [{doctor_id, start, end}]}
    """
    # NOTE: Placeholder implementation. Integrate with calendar/API if available.
    if not doctor_ids:
        return {"status": "success", "slots": []}
    slots = [
        {
            "doctor_id": doctor_ids[0],
            "start": "2025-11-01T10:00:00+02:00",
            "end": "2025-11-01T10:20:00+02:00",
        }
    ]
    return {"status": "success", "slots": slots}


DEFAULT_GUEST_TOKEN = os.getenv(
    "AMEDIS_GUEST_TOKEN",
    "Q9j87S4FV12e86475e82V5d44S7c2c2bb_35",
)


class BaseToolInput(BaseModel):
    base_url: Optional[str] = Field(
        default=None, description="Базавы URL backend (па змаўчанні — з агента)"
    )
    token: Optional[str] = Field(
        default=DEFAULT_GUEST_TOKEN, description="Токен доступу (па змаўчанні — гасцявы)"
    )


class DirectionItem(BaseModel):
    id: str = Field(description="Ідэнтыфікатар напрамку")
    name: Optional[str] = Field(default=None, description="Назва напрамку")


class DirectionsInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)


class DirectionsOutput(BaseModel):
    endpoint_used: str = Field(description="Эндпоінт, які адказаў паспяхова")
    directions: List[DirectionItem] = Field(
        default_factory=list, description="Спіс даступных напрамкаў"
    )


class DirectionsTool:
    name = "directions"
    description = "Атрымаць спіс напрамкаў прыёму (спецыяльнасцяў)."

    def call(self, input: DirectionsInput) -> DirectionsOutput:
        # If local KB is available, serve directions from it.
        if KB and USE_LOCAL_KB:
            directions = ENT.get("directions", {})
            items = [
                DirectionItem(id=str(did), name=(meta or {}).get("direction_name"))
                for did, meta in directions.items()
                if did is not None
            ]
            return DirectionsOutput(endpoint_used="local_kb", directions=items)

        # Fallback to remote API when KB is not available
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        endpoint, rows, _ = amedis_client.discover_directions(base_url, token)
        items = [
            DirectionItem(id=str(row.get("id")), name=row.get("name"))
            for row in rows
            if row.get("id") is not None
        ]
        return DirectionsOutput(endpoint_used=endpoint, directions=items)


class DoctorItem(BaseModel):
    id: str = Field(description="Ідэнтыфікатар доктара")
    name: Optional[str] = Field(default=None, description="Імя/прозвішча доктара")
    raw: Dict[str, Any] | None = Field(
        default=None, description="Сыры адказ backend для дадзенага доктара"
    )


class DoctorsInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    direction_id: Optional[str] = Field(
        default=None, description="Ідэнтыфікатар напрамку"
    )


class DoctorsOutput(BaseModel):
    doctors: List[DoctorItem] = Field(
        default_factory=list, description="Спіс дактароў для абранага напрамку"
    )


class DoctorsTool:
    name = "doctors"
    description = "Атрымаць спіс доктараў у межах напрамку."

    def call(self, input: DoctorsInput) -> DoctorsOutput:
        # Prefer local KB if present
        if KB and USE_LOCAL_KB:
            doctors_map: Dict[str, Any] = ENT.get("doctors", {})
            doctor_ids: List[str]
            if input.direction_id and RESOLVER:
                # Resolve all services for the given direction, then doctors per service
                services = RESOLVER.services_for_direction(str(input.direction_id))
                agg: List[str] = []
                for sid in services:
                    agg.extend(RESOLVER.doctors_for_service(sid))
                # Deduplicate while preserving order
                seen = set()
                doctor_ids = [d for d in agg if not (d in seen or seen.add(d))]
            else:
                doctor_ids = list(doctors_map.keys())

            doctors = [
                DoctorItem(
                    id=str(did),
                    name=(doctors_map.get(did) or {}).get("doctor_name"),
                    raw=doctors_map.get(did),
                )
                for did in doctor_ids
                if did is not None
            ]
            return DoctorsOutput(doctors=doctors)

        # Fallback to remote API
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        rows = amedis_client.get_doctors(base_url, token, input.direction_id)
        doctors = [
            DoctorItem(id=str(row.get("id")), name=row.get("name"), raw=row.get("raw"))
            for row in rows
            if row.get("id") is not None
        ]
        return DoctorsOutput(doctors=doctors)


class ServiceItem(BaseModel):
    id: str = Field(description="ServiceId паслугі")
    name: Optional[str] = Field(default=None, description="Назва паслугі")
    duration_minutes: Optional[int] = Field(
        default=None, description="Працягласць у хвілінах"
    )
    raw: Dict[str, Any] | None = Field(
        default=None, description="Сыры адказ backend для дадзенай паслугі"
    )


class ServicesInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    direction_id: Optional[str] = Field(
        default=None, description="Ідэнтыфікатар напрамку"
    )


class ServicesOutput(BaseModel):
    services: List[ServiceItem] = Field(
        default_factory=list, description="Спіс паслуг і іх працягласць"
    )


class ServicesTool:
    name = "services"
    description = "Паказаць спіс паслуг для напрамку і іх працягласць."

    def call(self, input: ServicesInput) -> ServicesOutput:
        # Prefer local KB if present
        if KB and USE_LOCAL_KB:
            services_map: Dict[str, Any] = ENT.get("services", {})
            # If a direction is provided, filter services by it
            if input.direction_id and RESOLVER:
                service_ids = RESOLVER.services_for_direction(str(input.direction_id))
            else:
                service_ids = list(services_map.keys())

            def _duration_from_service(srv: Dict[str, Any]) -> Optional[int]:
                # Accept multiple possible keys, coerce to minutes
                if not isinstance(srv, dict):
                    return None
                value = srv.get("duration_min")
                if value is None:
                    value = srv.get("duration") or srv.get("duration_minutes")
                return _to_int_minutes(value)

            services = [
                ServiceItem(
                    id=str(sid),
                    name=(services_map.get(sid) or {}).get("service_name"),
                    duration_minutes=_duration_from_service(services_map.get(sid, {})),
                    raw=services_map.get(sid),
                )
                for sid in service_ids
                if sid is not None
            ]
            return ServicesOutput(services=services)

        # Fallback to remote API
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        rows = amedis_client.get_service_duration(
            base_url, token, input.direction_id
        )
        services = [
            ServiceItem(
                id=str(row.get("id")),
                name=row.get("name"),
                duration_minutes=_to_int_minutes(row.get("duration")),
                raw=row.get("raw"),
            )
            for row in rows
            if row.get("id") is not None
        ]
        return ServicesOutput(services=services)


def _to_int_minutes(value: Any) -> Optional[int]:
    """Helper to coerce raw duration values to integer minutes."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    try:
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        number = float(text)
        return int(round(number))
    except Exception:
        return None


class SlotItem(BaseModel):
    startAt: str = Field(description="Дата і час пачатку слоту")
    endAt: Optional[str] = Field(
        default=None, description="Дата і час заканчэння слоту, калі вядома"
    )
    raw: Dict[str, Any] | None = Field(
        default=None, description="Сыры адказ backend для слоту"
    )


class ScheduleInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    doctor_id: str = Field(description="Ідэнтыфікатар доктара")
    service_id: Optional[str] = Field(
        default=None, description="Ідэнтыфікатар паслугі"
    )
    date_start: str = Field(description="Дата пачатку (DD.MM.YYYY)")
    date_end: str = Field(description="Дата заканчэння (DD.MM.YYYY)")


class ScheduleOutput(BaseModel):
    slots: List[SlotItem] = Field(
        default_factory=list, description="Даступныя слоты доктара"
    )


class ScheduleTool:
    name = "schedule"
    description = "Атрымаць вольныя часавыя слоты для доктара і паслугі."

    def call(self, input: ScheduleInput) -> ScheduleOutput:
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        start_norm, end_norm = _normalize_date_range(input.date_start, input.date_end)
        rows = amedis_client.get_schedule(
            base_url,
            token,
            input.doctor_id,
            start_norm,
            end_norm,
            input.service_id,
        )
        slots = [
            SlotItem(
                startAt=row.get("startAt"),
                endAt=row.get("endAt"),
                raw=row.get("raw"),
            )
            for row in rows
            if row.get("startAt")
        ]
        return ScheduleOutput(slots=slots)


def _normalize_date_range(date_start: str, date_end: str) -> Tuple[str, str]:
    s = (date_start or "").strip().lower()
    e = (date_end or "").strip().lower()

    phrases_next = {"наступны месяц", "следующий месяц", "next month"}
    phrases_this = {"гэты месяц", "текущий месяц", "this month"}

    if s in phrases_next or e in phrases_next:
        today = date.today()
        y = today.year + (1 if today.month == 12 else 0)
        m = 1 if today.month == 12 else today.month + 1
        first = date(y, m, 1)
        last_day = calendar.monthrange(y, m)[1]
        last = date(y, m, last_day)
        return first.strftime("%d.%m.%Y"), last.strftime("%d.%m.%Y")

    if s in phrases_this or e in phrases_this:
        today = date.today()
        first = date(today.year, today.month, 1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        last = date(today.year, today.month, last_day)
        return first.strftime("%d.%m.%Y"), last.strftime("%d.%m.%Y")

    if _is_year_month(s) and (not e or _is_year_month(e)):
        y, m = map(int, s.split("-"))
        first = date(y, m, 1)
        last_day = calendar.monthrange(y, m)[1]
        last = date(y, m, last_day)
        return first.strftime("%d.%m.%Y"), last.strftime("%d.%m.%Y")

    ds = _to_ddmmyyyy(s) or s
    de = _to_ddmmyyyy(e) or e
    return ds, de


def _is_year_month(text: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}", text))


def _to_ddmmyyyy(text: str) -> Optional[str]:
    if not text:
        return None
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = map(int, text.split("-"))
        try:
            dt = date(y, m, d)
        except ValueError:
            last_day = calendar.monthrange(y, m)[1]
            dt = date(y, m, min(d, last_day))
        return dt.strftime("%d.%m.%Y")
    return None


class CreateRecordInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    doctor_id: str = Field(description="Ідэнтыфікатар доктара")
    patient_id: str = Field(description="patientAPIId пацыента")
    startAt: str = Field(description="Дата і час пачатку слоту")
    endAt: Optional[str] = Field(
        default=None, description="Дата і час заканчэння слоту, калі вядома"
    )
    description: str = Field(
        default="", description="Кароткі каментар для рэгістрацыі"
    )
    insurer: str = Field(description="Назва страхоўшчыка (Ins_name)")
    extra: Dict[str, Any] | None = Field(
        default=None,
        description="Дадатковыя палі (officeId, cabinetId, serviceId і інш.)",
    )


class CreateRecordOutput(BaseModel):
    status_code: int = Field(description="HTTP статус адказу")
    data: Dict[str, Any] | None = Field(
        default=None, description="Удалая частка адказу backend"
    )
    error: Any | None = Field(
        default=None, description="Апісанне памылкі, калі ёсць"
    )
    sent: Dict[str, Any] = Field(
        default_factory=dict, description="Даныя, перададзеныя ў backend"
    )


class CreateRecordTool:
    name = "create_record"
    description = "Стварыць новы запіс да ўрача на аснове выбранага слоту."

    def call(self, input: CreateRecordInput) -> CreateRecordOutput:
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        result = amedis_client.create_record(
            base_url,
            token,
            input.doctor_id,
            input.patient_id,
            input.startAt,
            input.endAt,
            input.description,
            input.insurer,
            extra=input.extra,
        )
        return CreateRecordOutput(
            status_code=result.get("status_code", 0),
            data=result.get("data"),
            error=result.get("error"),
            sent=result.get("sent", {}),
        )


class ListRecordsInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    patient_id: str = Field(description="patientAPIId пацыента")


class PatientRecord(BaseModel):
    recordId: str = Field(description="Ідэнтыфікатар запісу")
    doctor: Optional[str] = Field(
        default=None, description="Імя або спецыялізацыя доктара"
    )
    startAt: Optional[str] = Field(
        default=None, description="Час пачатку прыёму"
    )
    endAt: Optional[str] = Field(
        default=None, description="Час заканчэння прыёму"
    )
    status: Optional[str] = Field(
        default=None, description="Статус запісу на баку backend"
    )
    raw: Dict[str, Any] | None = Field(
        default=None, description="Сыры адказ backend"
    )


class ListRecordsOutput(BaseModel):
    records: List[PatientRecord] = Field(
        default_factory=list, description="Спіс актыўных/будучых запісаў"
    )


class ListRecordsTool:
    name = "list_records"
    description = "Паказаць усе запісы пацыента."

    def call(self, input: ListRecordsInput) -> ListRecordsOutput:
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        rows = amedis_client.list_patient_records(
            base_url, token, input.patient_id
        )
        records = [
            PatientRecord(
                recordId=str(row.get("recordId")),
                doctor=row.get("doctor"),
                startAt=row.get("startAt"),
                endAt=row.get("endAt"),
                status=row.get("status"),
                raw=row.get("raw"),
            )
            for row in rows
            if row.get("recordId") is not None
        ]
        return ListRecordsOutput(records=records)


class CancelRecordInput(BaseToolInput):
    base_url: Optional[str] = Field(default=None)
    token: Optional[str] = Field(default=None)
    record_id: str = Field(description="Ідэнтыфікатар запісу")
    cancel_status: str = Field(
        default="CAN", description="Статус, на які трэба змяніць запіс"
    )


class CancelRecordOutput(BaseModel):
    status_code: int = Field(description="HTTP статус адказу")
    data: Dict[str, Any] | None = Field(
        default=None, description="Цела адказу backend"
    )
    sent: Dict[str, Any] = Field(
        default_factory=dict, description="Даныя, якія былі адпраўлены"
    )


class CancelRecordTool:
    name = "cancel_record"
    description = "Адмяніць існы запіс па recordId."

    def call(self, input: CancelRecordInput) -> CancelRecordOutput:
        base_url = getattr(input, "base_url", None) or amedis_client.BASE_URL_DEFAULT
        token = getattr(input, "token", DEFAULT_GUEST_TOKEN)
        result = amedis_client.cancel_record(
            base_url, token, input.record_id, input.cancel_status
        )
        return CancelRecordOutput(
            status_code=result.get("status_code", 0),
            data=result.get("data"),
            sent=result.get("sent", {}),
        )


class HarAutofillInput(BaseModel):
    har_path: str = Field(description="Шлях да HAR-файла")


class HarAutofillOutput(BaseModel):
    patient_ids: List[str] = Field(
        default_factory=list, description="Знойдзеныя patientAPIId"
    )
    insurer_guess: Optional[str] = Field(
        default=None, description="Магчымы Ins_name па змаўчанні"
    )
    record_fields: List[str] = Field(
        default_factory=list, description="Палі формы /record/create"
    )


class HarAutofillTool:
    name = "har_autofill"
    description = "Прайсці па HAR-файле і знайсці patientAPIId/Ins_name."

    def call(self, input: HarAutofillInput) -> HarAutofillOutput:
        data = amedis_client.parse_har_for_patient(input.har_path)
        return HarAutofillOutput(
            patient_ids=[str(pid) for pid in data.get("patient_ids", [])],
            insurer_guess=data.get("ins_name"),
            record_fields=list(data.get("record_fields", [])),
        )


__all__ = [
    "DirectionsTool",
    "DoctorsTool",
    "ServicesTool",
    "ScheduleTool",
    "CreateRecordTool",
    "ListRecordsTool",
    "CancelRecordTool",
    "HarAutofillTool",
    "DirectionsInput",
    "DoctorsInput",
    "ServicesInput",
    "ScheduleInput",
    "CreateRecordInput",
    "ListRecordsInput",
    "CancelRecordInput",
    "HarAutofillInput",
]
