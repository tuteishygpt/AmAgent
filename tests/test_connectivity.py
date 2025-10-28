"""Integration-like connectivity tests against the live Amedis backend.

These tests are optional and require explicit environment variables with real
credentials and identifiers.  They are skipped by default so the suite can run
without network access, but when the necessary variables are provided they
exercise the curl-based client against the production endpoints to ensure the
TLS workaround layer and parsing logic function end-to-end.
"""

from __future__ import annotations

import os
from typing import Optional

import pytest

import amedis_client as client

TOKEN = os.environ.get("AMEDIS_TEST_TOKEN")
if not TOKEN:
    pytestmark = pytest.mark.skip(reason="AMEDIS_TEST_TOKEN not set; skipping live connectivity tests")

BASE_URL = os.environ.get("AMEDIS_BASE_URL", client.BASE_URL_DEFAULT)
DIRECTION_ID = os.environ.get("AMEDIS_TEST_DIRECTION")
DOCTOR_ID = os.environ.get("AMEDIS_TEST_DOCTOR")
SERVICE_ID = os.environ.get("AMEDIS_TEST_SERVICE")
DATE_START = os.environ.get("AMEDIS_TEST_DATE_START")
DATE_END = os.environ.get("AMEDIS_TEST_DATE_END")


def _require(var: Optional[str], name: str) -> str:
    if not var:
        pytest.skip(f"Set {name} to run this test")
    return var


def test_live_directions_connectivity():
    endpoint, directions, message = client.discover_directions(BASE_URL, token=TOKEN)
    assert endpoint, "expected one of the candidate endpoints to respond"
    assert isinstance(directions, list)
    assert message


def test_live_doctors_connectivity():
    direction = _require(DIRECTION_ID, "AMEDIS_TEST_DIRECTION")
    doctors = client.get_doctors(BASE_URL, token=TOKEN, id_direction=direction)
    assert isinstance(doctors, list)


def test_live_services_connectivity():
    direction = _require(DIRECTION_ID, "AMEDIS_TEST_DIRECTION")
    services = client.get_service_duration(BASE_URL, token=TOKEN, id_direction=direction)
    assert isinstance(services, list)


def test_live_schedule_connectivity():
    doctor = _require(DOCTOR_ID, "AMEDIS_TEST_DOCTOR")
    service = _require(SERVICE_ID, "AMEDIS_TEST_SERVICE")
    start = _require(DATE_START, "AMEDIS_TEST_DATE_START")
    end = _require(DATE_END, "AMEDIS_TEST_DATE_END")
    slots = client.get_schedule(
        BASE_URL,
        token=TOKEN,
        doctor_id=doctor,
        start_date=start,
        end_date=end,
        service_id=service,
    )
    assert isinstance(slots, list)
