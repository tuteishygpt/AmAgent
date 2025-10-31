import pathlib
import sys

import pytest
import os

# Ensure project root is on sys.path for direct test invocation
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools as t


def test_directions_tool_returns_items(monkeypatch):
    def fake_discover(base_url: str, token: str):
        return "/directions", [{"id": "1", "name": "Тэрапія"}, {"id": "2", "name": "Хірургія"}], "OK"

    import amedis_client as client

    monkeypatch.setattr(client, "discover_directions", fake_discover)

    tool = t.DirectionsTool()
    out = tool.call(t.DirectionsInput())

    assert out.endpoint_used == "/directions"
    assert [d.id for d in out.directions] == ["1", "2"]
    assert [d.name for d in out.directions] == ["Тэрапія", "Хірургія"]


def test_doctors_tool_filters_and_maps(monkeypatch):
    def fake_get_doctors(base_url: str, token: str, id_direction: str):
        return [
            {"id": "77", "name": "Доктар Х", "raw": {"k": 1}},
            {"name": "Без ID"},  # павінен быць адфільтраваны
        ]

    import amedis_client as client

    monkeypatch.setattr(client, "get_doctors", fake_get_doctors)

    tool = t.DoctorsTool()
    out = tool.call(t.DoctorsInput(direction_id="5"))

    assert len(out.doctors) == 1
    doc = out.doctors[0]
    assert doc.id == "77"
    assert doc.name == "Доктар Х"
    assert isinstance(doc.raw, dict)


def test_services_tool_converts_duration(monkeypatch):
    def fake_get_services(base_url: str, token: str, id_direction: str):
        return [
            {"id": "12", "name": "Кансультацыя", "duration": "30", "raw": {"d": "30"}},
            {"id": "13", "name": "Працэдура", "duration": 45.6, "raw": {"d": 45.6}},
            {"id": "14", "name": "Іншае", "duration": None, "raw": {"d": None}},
        ]

    import amedis_client as client

    monkeypatch.setattr(client, "get_service_duration", fake_get_services)

    tool = t.ServicesTool()
    out = tool.call(t.ServicesInput(direction_id="5"))

    assert [(s.id, s.duration_minutes) for s in out.services] == [
        ("12", 30),
        ("13", 46),  # 45.6 -> 46 праз round
        ("14", None),
    ]


def test_schedule_tool_returns_slots(monkeypatch):
    def fake_get_schedule(base_url: str, token: str, doctor_id: str, start: str, end: str, service_id: str | None):
        assert doctor_id == "42"
        assert service_id == "12"
        return [
            {"startAt": "2023-10-01 09:00", "endAt": "2023-10-01 09:30", "raw": {"r": 1}},
            {"startAt": "2023-10-01 10:00"},
        ]

    import amedis_client as client

    monkeypatch.setattr(client, "get_schedule", fake_get_schedule)

    tool = t.ScheduleTool()
    out = tool.call(
        t.ScheduleInput(
            doctor_id="42",
            service_id="12",
            date_start="01.10.2023",
            date_end="07.10.2023",
        )
    )

    assert [s.startAt for s in out.slots] == [
        "2023-10-01 09:00",
        "2023-10-01 10:00",
    ]
    assert [s.endAt for s in out.slots] == [
        "2023-10-01 09:30",
        None,
    ]


def test_live_directions_tool_prints():
    token = os.environ.get("AMEDIS_TEST_TOKEN")
    if not token:
        pytest.skip("AMEDIS_TEST_TOKEN not set")
    base_url = os.environ.get("AMEDIS_BASE_URL")
    tool = t.DirectionsTool()
    out = tool.call(t.DirectionsInput(base_url=base_url, token=token))
    print(out.model_dump())


def test_live_doctors_tool_prints():
    token = os.environ.get("AMEDIS_TEST_TOKEN")
    direction = os.environ.get("AMEDIS_TEST_DIRECTION")
    if not token or not direction:
        pytest.skip("AMEDIS_TEST_TOKEN/AMEDIS_TEST_DIRECTION not set")
    base_url = os.environ.get("AMEDIS_BASE_URL")
    tool = t.DoctorsTool()
    out = tool.call(t.DoctorsInput(base_url=base_url, token=token, direction_id=direction))
    print([d.model_dump() for d in out.doctors])


def test_live_services_tool_prints():
    token = os.environ.get("AMEDIS_TEST_TOKEN")
    direction = os.environ.get("AMEDIS_TEST_DIRECTION")
    if not token or not direction:
        pytest.skip("AMEDIS_TEST_TOKEN/AMEDIS_TEST_DIRECTION not set")
    base_url = os.environ.get("AMEDIS_BASE_URL")
    tool = t.ServicesTool()
    out = tool.call(t.ServicesInput(base_url=base_url, token=token, direction_id=direction))
    print([s.model_dump() for s in out.services])


def test_live_schedule_tool_prints():
    token = os.environ.get("AMEDIS_TEST_TOKEN")
    doctor = os.environ.get("AMEDIS_TEST_DOCTOR")
    service = os.environ.get("AMEDIS_TEST_SERVICE")
    start = os.environ.get("AMEDIS_TEST_DATE_START")
    end = os.environ.get("AMEDIS_TEST_DATE_END")
    if not all([token, doctor, service, start, end]):
        pytest.skip("Missing one of AMEDIS_* envs for schedule")
    base_url = os.environ.get("AMEDIS_BASE_URL")
    tool = t.ScheduleTool()
    out = tool.call(
        t.ScheduleInput(
            base_url=base_url,
            token=token,
            doctor_id=doctor,
            service_id=service,
            date_start=start,
            date_end=end,
        )
    )
    print([s.model_dump() for s in out.slots])
