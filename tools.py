"""ADK tool wrappers around the low-level Amedis client."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import amedis_client


DEFAULT_GUEST_TOKEN = os.getenv(
    "AMEDIS_GUEST_TOKEN",
    "Q9j87S4FV12e86475e82V5d44S7c2c2bb_35",
)


class BaseToolInput(BaseModel):
    base_url: Optional[str] = Field(
        default=None,
        description=(
            "Неабавязковае перазаданнне базавага URL backend. Калі не пазначана, "
            "будзе выкарыстаны URL па змаўчанні."
        ),
    )


class DirectionItem(BaseModel):
    id: str = Field(description="Ідэнтыфікатар напрамку")
    name: Optional[str] = Field(default=None, description="Назва напрамку")


class DirectionsInput(BaseToolInput):
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )


class DirectionsOutput(BaseModel):
    endpoint_used: str = Field(description="Эндпоінт, які адказаў паспяхова")
    directions: List[DirectionItem] = Field(
        default_factory=list, description="Спіс даступных напрамкаў"
    )


class DirectionsTool:
    name = "directions"
    description = "Атрымаць спіс напрамкаў прыёму (спецыяльнасцяў)."

    def call(self, input: DirectionsInput) -> DirectionsOutput:
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        endpoint, rows, _ = amedis_client.discover_directions(base_url, input.token)
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
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        rows = amedis_client.get_doctors(base_url, input.token, input.direction_id)
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
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        rows = amedis_client.get_service_duration(
            base_url, input.token, input.direction_id
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
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        rows = amedis_client.get_schedule(
            base_url,
            input.token,
            input.doctor_id,
            input.date_start,
            input.date_end,
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


class CreateRecordInput(BaseToolInput):
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        result = amedis_client.create_record(
            base_url,
            input.token,
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
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        rows = amedis_client.list_patient_records(
            base_url, input.token, input.patient_id
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
    token: str = Field(
        default=DEFAULT_GUEST_TOKEN,
        description=(
            "Токен доступу пацыента. Па змаўчанні выкарыстоўваецца госцевы токен "
            "да аўтарызацыі."
        ),
    )
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
        base_url = input.base_url or amedis_client.BASE_URL_DEFAULT
        result = amedis_client.cancel_record(
            base_url, input.token, input.record_id, input.cancel_status
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
