import json
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import amedis_client as client


def make_response(payload: Any, status: int = 200) -> client.ResponseShim:
    return client.ResponseShim(status_code=status, text=json.dumps(payload))


def test_curl_cmd_base_contains_tls_flags():
    cmd = client._curl_cmd_base()
    assert "--insecure" in cmd
    assert "--tlsv1.0" in cmd
    # On Windows curl uses Schannel backend; OpenSSL cipher strings are invalid.
    if sys.platform == "win32":
        assert "DEFAULT:@SECLEVEL=1" not in " ".join(cmd)
    else:
        assert "DEFAULT:@SECLEVEL=1" in cmd


def test_discover_directions_selects_first_success(monkeypatch):
    calls = []

    def fake_api_get(base_url: str, path: str, params: dict, timeout: int = 20):
        calls.append((base_url, path, params))
        if len(calls) == 1:
            return make_response({"error": "fail"}, status=500)
        return make_response([
            {"id": "1", "name": "Тэрапія"},
            {"Id": "2", "Title": "Хірургія"},
        ])

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    endpoint, directions, message = client.discover_directions(
        client.BASE_URL_DEFAULT, token="abc"
    )

    assert endpoint == client.ENDPOINTS["directions_candidates"][1]
    assert [d["id"] for d in directions] == ["1", "2"]
    assert "OK" in message
    assert len(calls) == 2


def test_get_doctors_normalizes_response(monkeypatch):
    def fake_api_get(base_url: str, path: str, params: dict, timeout: int = 20):
        assert path == client.ENDPOINTS["doctors"]
        return make_response({"data": [{"doctorId": "77", "fio": "Доктар Х"}]})

    monkeypatch.setattr(client, "_api_get", fake_api_get)
    doctors = client.get_doctors(client.BASE_URL_DEFAULT, token="abc", id_direction="5")
    assert doctors == [
        {"id": "77", "name": "Доктар Х", "raw": {"doctorId": "77", "fio": "Доктар Х"}}
    ]


def test_get_service_duration_handles_nested_dict(monkeypatch):
    def fake_api_get(base_url: str, path: str, params: dict, timeout: int = 20):
        assert params["idDirection"] == "5"
        return make_response(
            {
                "services": [
                    {
                        "serviceId": "12",
                        "serviceName": "Кансультацыя",
                        "timePriemMinutes": 30,
                    }
                ]
            }
        )

    monkeypatch.setattr(client, "_api_get", fake_api_get)
    services = client.get_service_duration(
        client.BASE_URL_DEFAULT, token="abc", id_direction="5"
    )
    assert services == [
        {
            "id": "12",
            "name": "Кансультацыя",
            "duration": 30,
            "raw": {
                "serviceId": "12",
                "serviceName": "Кансультацыя",
                "timePriemMinutes": 30,
            },
        }
    ]


def test_get_schedule_normalizes_slots(monkeypatch):
    def fake_api_get(base_url: str, path: str, params: dict, timeout: int = 20):
        assert params["doctorIds"] == "42"
        assert params["serviceId"] == "12"
        return make_response(
            [
                {
                    "someKey": [
                        {
                            "2023-10-01": [
                                {"startAt": "09:00", "endAt": "09:30", "extra": 1}
                            ],
                            "officeId": "11",
                        }
                    ]
                }
            ]
        )

    monkeypatch.setattr(client, "_api_get", fake_api_get)
    slots = client.get_schedule(
        client.BASE_URL_DEFAULT,
        token="abc",
        doctor_id="42",
        start_date="01.10.2023",
        end_date="07.10.2023",
        service_id="12",
    )
    assert slots == [
        {
            "startAt": "2023-10-01 09:00",
            "endAt": "2023-10-01 09:30",
            "raw": {
                "date": "2023-10-01",
                "officeId": "11",
                "startAt": "09:00",
                "endAt": "09:30",
                "extra": 1,
            },
        }
    ]


