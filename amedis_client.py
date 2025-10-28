"""Low-level client for the Amedis online backend.

This module contains a curl-backed HTTP layer that keeps TLS workarounds
required to talk to https://online.amedis.by:4422.  The functions exposed
here are thin wrappers around the original reference implementation and are
kept deliberately straightforward so they can be reused by tools or tests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL_DEFAULT = "https://online.amedis.by:4422"

ENDPOINTS = {
    "directions_candidates": [
        "/directions",
        "/direction",
        "/directions/all",
        "/direction/all",
        "/getDirections",
        "/records/directions",
    ],
    "doctors": "/doctors",
    "schedule": "/doctors/schedule",
    "service_duration": "/serviceduration",
    "record_create": "/record/create",
    "record_change_status": "/record/change-status",
    "patient_records": "/patient/recordsbyid",
}


# ---------------------------------------------------------------------------
# Curl-based HTTP layer (TLS workaround)
# ---------------------------------------------------------------------------


def _curl_cmd_base(timeout: int = 25) -> List[str]:
    """Build the base curl command with TLS flags required by the backend."""

    return [
        "curl",
        "--silent",
        "--show-error",
        "--http1.1",
        "--insecure",  # ⚠️ skip certificate validation (required by backend)
        "--tlsv1.0",  # allow legacy TLS version
        "--max-time",
        str(timeout),
        "--ciphers",
        "DEFAULT:@SECLEVEL=1",
    ]


def _run_curl(cmd: List[str]) -> "ResponseShim":
    """Execute a curl command and return a simple response shim."""

    full_cmd = cmd[:]
    if "-i" not in full_cmd and "--include" not in full_cmd:
        full_cmd.insert(1, "-i")
    out = subprocess.check_output(full_cmd, text=True)
    parts = out.split("\r\n\r\n")
    if len(parts) < 2:
        parts = out.split("\n\n")
    if len(parts) >= 2:
        raw_headers = "\r\n\r\n".join(parts[:-1])
        body = parts[-1]
    else:
        raw_headers = ""
        body = out

    status_code = 0
    for line in raw_headers.splitlines():
        line = line.strip()
        if line.startswith("HTTP/"):
            try:
                status_code = int(line.split()[1])
            except Exception:
                pass
    return ResponseShim(status_code=status_code or 200, text=body)


@dataclass
class ResponseShim:
    """Minimal response object compatible with the original implementation."""

    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def read_token_from_file(path: str) -> str:
    """Read an access token from a text file."""

    if not os.path.exists(path):
        raise FileNotFoundError(f"Token file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        raise ValueError("Token file is empty")
    return token


def _build_url(
    base_url: str, path: str, params: Optional[Dict[str, Any]] = None
) -> str:
    url = base_url.rstrip("/") + (path if path.startswith("/") else "/" + path)
    if params:
        qs = urlencode(params, doseq=True)
        url = f"{url}?{qs}"
    return url


def _api_get(
    base_url: str, path: str, params: Dict[str, Any], timeout: int = 20
) -> ResponseShim:
    url = _build_url(base_url, path, params)
    cmd = _curl_cmd_base(timeout) + [url]
    return _run_curl(cmd)


def _api_post_form(
    base_url: str, path: str, data: Dict[str, Any], timeout: int = 20
) -> ResponseShim:
    url = _build_url(base_url, path, None)
    form = urlencode(data, doseq=True)
    cmd = _curl_cmd_base(timeout) + [
        "-X",
        "POST",
        "-H",
        "Content-Type: application/x-www-form-urlencoded",
        "--data",
        form,
        url,
    ]
    return _run_curl(cmd)


def _safe_json(resp: ResponseShim) -> Any:
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except Exception:
            return {"raw": resp.text}


# ---------------------------------------------------------------------------
# Directions
# ---------------------------------------------------------------------------


def discover_directions(base_url: str, token: str) -> Tuple[str, List[Dict[str, Any]], str]:
    """Try multiple endpoints to fetch directions list.

    Returns a tuple of (endpoint_used, normalized_directions, status_message).
    """

    for endpoint in ENDPOINTS["directions_candidates"]:
        try:
            response = _api_get(base_url, endpoint, params={"token": token})
            if response.status_code == 200:
                data = _safe_json(response)
                rows = _normalize_directions(data)
                if rows:
                    return endpoint, rows, f"OK via {endpoint}"
        except Exception:
            continue
    return "", [], (
        "Не атрымалася аўтаматычна атрымаць спіс напрамкаў. "
        "Увядзіце ID напрамку ўручную."
    )


def _normalize_directions(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        rows: List[Dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                direction = {
                    "id": item.get("id")
                    or item.get("idDirection")
                    or item.get("Id")
                    or item.get("ID"),
                    "name": item.get("name")
                    or item.get("title")
                    or item.get("Name")
                    or item.get("Title")
                    or item.get("direction"),
                }
                if direction["id"] is not None:
                    rows.append(direction)
        return rows
    if isinstance(data, dict):
        for key in ["directions", "items", "data", "result"]:
            arr = data.get(key)
            if isinstance(arr, list):
                return _normalize_directions(arr)
    return []


# ---------------------------------------------------------------------------
# Doctors
# ---------------------------------------------------------------------------


def get_doctors(
    base_url: str, token: str, id_direction: Optional[str]
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"token": token}
    if id_direction:
        params["idDirection"] = id_direction
    response = _api_get(base_url, ENDPOINTS["doctors"], params=params)
    if response.status_code != 200:
        raise RuntimeError(
            f"Doctors error {response.status_code}: {response.text[:400]}"
        )
    data = _safe_json(response)
    return _normalize_doctors(data)


def _normalize_doctors(data: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(data, list):
        iterable: Iterable[Any] = data
    elif isinstance(data, dict):
        iterable = (
            data.get("data")
            or data.get("items")
            or data.get("result")
            or data.get("doctors")
            or []
        )
    else:
        iterable = []

    for item in iterable:
        if isinstance(item, dict):
            out.append(
                {
                    "id": item.get("id")
                    or item.get("Id")
                    or item.get("doctorId")
                    or item.get("ID"),
                    "name": item.get("name")
                    or item.get("fio")
                    or item.get("FIO")
                    or item.get("fullName")
                    or "",
                    "raw": item,
                }
            )

    seen = set()
    unique: List[Dict[str, Any]] = []
    for doctor in out:
        doc_id = doctor.get("id")
        if doc_id and doc_id not in seen:
            unique.append(doctor)
            seen.add(doc_id)
    return unique


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def get_service_duration(
    base_url: str, token: str, id_direction: Optional[str]
) -> List[Dict[str, Any]]:
    if not id_direction:
        return []
    response = _api_get(
        base_url,
        ENDPOINTS["service_duration"],
        params={"token": token, "idDirection": id_direction},
    )
    if response.status_code != 200:
        return []
    data = _safe_json(response)
    return _normalize_services(data)


def _normalize_services(data: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    iterable: Iterable[Any] = []
    if isinstance(data, list):
        iterable = data
    elif isinstance(data, dict):
        for key in ["services", "data", "items", "result"]:
            if isinstance(data.get(key), list):
                iterable = data[key]
                break
    for item in iterable:
        if isinstance(item, dict):
            out.append(
                {
                    "id": item.get("id")
                    or item.get("serviceId")
                    or item.get("Id"),
                    "name": item.get("name")
                    or item.get("serviceName")
                    or item.get("Name")
                    or item.get("researchText")
                    or "",
                    "duration": item.get("duration")
                    or item.get("Duration")
                    or item.get("timePriemMinutes"),
                    "raw": item,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def get_schedule(
    base_url: str,
    token: str,
    doctor_id: str,
    start_date: str,
    end_date: str,
    service_id: Optional[str],
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "token": token,
        "doctorIds": str(doctor_id),
        "startDate": start_date,
        "endDate": end_date,
    }
    if service_id:
        params["serviceId"] = str(service_id)
    response = _api_get(base_url, ENDPOINTS["schedule"], params=params)
    if response.status_code != 200:
        raise RuntimeError(
            f"Schedule error {response.status_code}: {response.text[:400]}"
        )
    data = _safe_json(response)
    return _normalize_slots(data)


def _normalize_slots(data: Any) -> List[Dict[str, Any]]:
    slots: List[Dict[str, Any]] = []

    def add_slot(start: str, end: Optional[str] = None, raw: Any = None) -> None:
        slots.append({"startAt": start, "endAt": end, "raw": raw})

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for _, dates in item.items():
                if not isinstance(dates, list):
                    continue
                for block in dates:
                    if not isinstance(block, dict):
                        continue
                    meta = {k: v for k, v in block.items() if not isinstance(v, list)}
                    for date_str, day_slots in block.items():
                        if not isinstance(day_slots, list):
                            continue
                        for slot in day_slots:
                            if not isinstance(slot, dict):
                                continue
                            start = (
                                slot.get("startAt")
                                or slot.get("start")
                                or slot.get("time")
                            )
                            end = slot.get("endAt") or slot.get("end")
                            if not start:
                                continue
                            if isinstance(start, str) and len(start) <= 5 and ":" in start:
                                start_full = f"{date_str} {start}"
                            else:
                                start_full = start
                            if isinstance(end, str) and len(end) <= 5 and ":" in end:
                                end_full = f"{date_str} {end}"
                            else:
                                end_full = end
                            raw = {"date": date_str, **meta, **slot}
                            add_slot(start_full, end_full, raw)
        if slots:
            return slots

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                for day in value:
                    if isinstance(day, dict):
                        date = day.get("date") or day.get("Date")
                        times = day.get("times") or day.get("Times") or []
                        if isinstance(times, list) and date:
                            for time_item in times:
                                if isinstance(time_item, str):
                                    add_slot(
                                        f"{date} {time_item}",
                                        None,
                                        {"date": date, "time": time_item},
                                    )
        if slots:
            return slots

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                start = item.get("startAt") or item.get("start") or item.get("time")
                end = item.get("endAt") or item.get("end")
                if start:
                    add_slot(start, end, item)

    return slots


# ---------------------------------------------------------------------------
# Record management
# ---------------------------------------------------------------------------


def create_record(
    base_url: str,
    token: str,
    doctor_id: str,
    patient_id: str,
    start_at: str,
    end_at: Optional[str],
    description: str,
    insurer: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "token": token,
        "doctor": str(doctor_id),
        "patient": str(patient_id),
        "startAt": start_at,
        "endAt": end_at or "",
        "description": description,
        "Ins_name": insurer,
    }
    if extra:
        for key, value in extra.items():
            if value is not None and value != "":
                data[key] = value
    response = _api_post_form(base_url, ENDPOINTS["record_create"], data=data)
    if response.status_code != 200:
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:800]
        return {
            "status_code": response.status_code,
            "error": payload,
            "sent": data,
        }
    return {
        "status_code": response.status_code,
        "data": _safe_json(response),
        "sent": data,
    }


def list_patient_records(
    base_url: str, token: str, patient_api_id: str
) -> List[Dict[str, Any]]:
    response = _api_get(
        base_url,
        ENDPOINTS["patient_records"],
        params={"token": token, "patientAPIId": str(patient_api_id)},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Patient records error {response.status_code}: {response.text[:400]}"
        )
    data = _safe_json(response)
    return _normalize_records(data)


def _normalize_records(data: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict) and isinstance(
            data[0].get("records"), list
        ):
            iterable = data[0]["records"]
        else:
            iterable = data
    elif isinstance(data, dict):
        iterable = (
            data.get("records")
            or data.get("items")
            or data.get("data")
            or data.get("result")
            or []
        )
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "recordId": item.get("id")
                or item.get("recordId")
                or item.get("Id"),
                "doctor": item.get("doctorName")
                or item.get("doctor")
                or item.get("Doctor"),
                "startAt": item.get("startAt")
                or item.get("date")
                or item.get("start"),
                "endAt": item.get("endAt") or item.get("end"),
                "status": item.get("status")
                or item.get("Status")
                or item.get("status_pac"),
                "raw": item,
            }
        )
    return items


def cancel_record(
    base_url: str,
    token: str,
    record_id: str,
    cancel_status: str = "CAN",
) -> Dict[str, Any]:
    data = {
        "token": token,
        "recordId": str(record_id),
        "status": cancel_status,
    }
    response = _api_post_form(
        base_url, ENDPOINTS["record_change_status"], data=data
    )
    return {
        "status_code": response.status_code,
        "data": _safe_json(response),
        "sent": data,
    }


# ---------------------------------------------------------------------------
# HAR helpers
# ---------------------------------------------------------------------------


def parse_har_for_patient(har_path: str) -> Dict[str, Any]:
    """Extract patient identifiers and record fields from a HAR dump."""

    result: Dict[str, Any] = {
        "patient_ids": [],
        "ins_name": None,
        "record_fields": [],
    }
    path = Path(har_path)
    if not path.exists():
        return result
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return result

    patient_ids = set()
    ins_name: Optional[str] = None
    fields_seen: Optional[List[str]] = None

    for entry in data.get("log", {}).get("entries", []):
        request = entry.get("request", {}) or {}
        url = request.get("url", "") or ""
        method = request.get("method", "")
        query = urlparse(url).query
        params = parse_qs(query)
        if "patientAPIId" in params:
            for value in params["patientAPIId"]:
                if value:
                    patient_ids.add(value)
        body = (request.get("postData", {}) or {}).get("text", "") or ""
        match = re.search(r"patientAPIId=([0-9]+)", body)
        if match:
            patient_ids.add(match.group(1))
        if url.endswith("/record/create") and method == "POST":
            form = parse_qs(body)
            flattened = {
                key: (value[0] if isinstance(value, list) and value else "")
                for key, value in form.items()
            }
            fields_seen = list(flattened.keys())
            if flattened.get("Ins_name"):
                ins_name = flattened.get("Ins_name")

    result["patient_ids"] = sorted(patient_ids)
    result["ins_name"] = ins_name
    result["record_fields"] = fields_seen or []
    return result


__all__ = [
    "BASE_URL_DEFAULT",
    "ENDPOINTS",
    "discover_directions",
    "get_doctors",
    "get_service_duration",
    "get_schedule",
    "create_record",
    "list_patient_records",
    "cancel_record",
    "parse_har_for_patient",
    "read_token_from_file",
]
