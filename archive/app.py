# Robust TLS workaround via curl + Gradio UI for online.amedis.by:4422
# -------------------------------------------------------------------
# Як запускаць:
#   1) pip install -q gradio
#   2) пакладзіце токен у файл (напрыклад, token.txt)
#   3) python this_file.py  або:
#      from this_file import launch_gradio; launch_gradio().launch()

from __future__ import annotations
import json
import os
import sys
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Try to import Gradio
try:
    import gradio as gr  # type: ignore
except Exception:
    gr = None  # UI не даступны

# -----------------------
# Configuration defaults
# -----------------------
BASE_URL_DEFAULT = "https://online.amedis.by:4422"
ENDPOINTS = {
    "directions_candidates": [
        "/directions", "/direction", "/directions/all", "/direction/all",
        "/getDirections", "/records/directions",
    ],
    "doctors": "/doctors",
    "schedule": "/doctors/schedule",
    "service_duration": "/serviceduration",
    "record_create": "/record/create",
    "record_change_status": "/record/change-status",
    "patient_records": "/patient/recordsbyid",
}

# -----------------------
# Curl-based HTTP layer
# -----------------------

def _curl_cmd_base(timeout: int = 25) -> List[str]:
    # Тыя ж самыя флажкі, што дапамаглі раней:
    # --http1.1, --insecure, --tlsv1.0, --ciphers DEFAULT:@SECLEVEL=1
    return [
        "curl",
        "--silent", "--show-error",
        "--http1.1",
        "--insecure",                # ⚠️ без праверкі сертыфікатаў
        "--tlsv1.0",                 # дазвол старога TLS
        "--max-time", str(timeout),
        "--ciphers", "DEFAULT:@SECLEVEL=1",
    ]

def _run_curl(cmd: List[str]) -> "ResponseShim":
    # Выканаць curl і вярнуць шым з палямі status_code/text/json()
    # Дадаем -i каб атрымаць статус + загалоўкі і выдзеліць код адказу.
    full_cmd = cmd[:]
    if "-i" not in full_cmd and "--include" not in full_cmd:
        full_cmd.insert(1, "-i")
    # print("CMD:", " ".join(full_cmd))
    out = subprocess.check_output(full_cmd, text=True)
    # Раздзяляем headers/body
    # curl -i можа вярнуць некалькі блокаў загалоўкаў (redirect), бяром апошні блок
    parts = out.split("\r\n\r\n")
    if len(parts) < 2:
        parts = out.split("\n\n")
    if len(parts) >= 2:
        raw_headers = "\r\n\r\n".join(parts[:-1])
        body = parts[-1]
    else:
        raw_headers = ""
        body = out
    # Вызначым апошні статус-код
    status_code = 0
    for line in raw_headers.splitlines():
        line = line.strip()
        if line.startswith("HTTP/"):
            try:
                status_code = int(line.split()[1])
            except Exception:
                pass
    return ResponseShim(status_code=status_code or 200, text=body)

class ResponseShim:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)

# -------- HAR helpers (optional) --------
import re
from urllib.parse import urlparse, parse_qs, urlencode
from pathlib import Path

def parse_har_for_patient(har_path: str) -> Dict[str, Any]:
    """Выняць patientAPIId і прыкладныя палі для /record/create з HAR-файла."""
    out = {"patient_ids": [], "ins_name": None, "record_fields": []}
    p = Path(har_path)
    if not p.exists():
        return out
    try:
        har = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    patient_ids = set()
    ins_name = None
    fields_seen = None
    for e in har.get("log", {}).get("entries", []):
        req = e.get("request", {}) or {}
        url = req.get("url", "") or ""
        method = req.get("method", "")
        q = urlparse(url).query
        qs = parse_qs(q)
        if "patientAPIId" in qs:
            for v in qs["patientAPIId"]:
                if v:
                    patient_ids.add(v)
        body = (req.get("postData", {}) or {}).get("text", "") or ""
        m = re.search(r"patientAPIId=([0-9]+)", body)
        if m:
            patient_ids.add(m.group(1))
        if url.endswith("/record/create") and method == "POST":
            f = parse_qs(body)
            flat = {k: (v[0] if isinstance(v, list) and v else "") for k, v in f.items()}
            fields_seen = list(flat.keys())
            if flat.get("Ins_name"):
                ins_name = flat.get("Ins_name")
    out["patient_ids"] = sorted(patient_ids)
    out["ins_name"] = ins_name
    out["record_fields"] = fields_seen or []
    return out

# -----------------------
# Helpers
# -----------------------

def read_token_from_file(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Token file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise ValueError("Token file is empty")
    return token

def _build_url(base_url: str, path: str, params: Dict[str, Any] | None = None) -> str:
    url = base_url.rstrip("/") + (path if path.startswith("/") else "/" + path)
    if params:
        qs = urlencode(params, doseq=True)
        url = f"{url}?{qs}"
    return url

# --------- CURL-backed API ---------

def api_get(base_url: str, path: str, params: Dict[str, Any], timeout=20) -> ResponseShim:
    url = _build_url(base_url, path, params)
    cmd = _curl_cmd_base(timeout) + [url]
    return _run_curl(cmd)

def api_post_form(base_url: str, path: str, data: Dict[str, Any], timeout=20) -> ResponseShim:
    url = _build_url(base_url, path, None)
    form = urlencode(data, doseq=True)
    cmd = _curl_cmd_base(timeout) + [
        "-X", "POST",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "--data", form,
        url,
    ]
    return _run_curl(cmd)

def safe_json(resp: ResponseShim) -> Any:
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except Exception:
            return {"raw": resp.text}

# -----------------------
# API operations
# -----------------------

def discover_directions(base_url: str, token: str) -> Tuple[str, List[Dict[str, Any]], str]:
    """Паспрабаваць некалькі магчымых эндпоінтаў для спіса напрамкаў."""
    for ep in ENDPOINTS["directions_candidates"]:
        try:
            r = api_get(base_url, ep, params={"token": token})
            if r.status_code == 200:
                data = safe_json(r)
                rows = normalize_directions(data)
                if rows:
                    return ep, rows, f"OK via {ep}"
        except Exception:
            continue
    return "", [], "Не атрымалася аўтаматычна атрымаць спіс напрамкаў. Увядзіце ID напрамку ўручную."

def normalize_directions(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        rows = []
        for x in data:
            if isinstance(x, dict):
                d = {
                    "id": x.get("id") or x.get("idDirection") or x.get("Id") or x.get("ID"),
                    "name": x.get("name") or x.get("title") or x.get("Name") or x.get("Title") or x.get("direction"),
                }
                if d["id"] is not None:
                    rows.append(d)
        return rows
    if isinstance(data, dict):
        for key in ["directions", "items", "data", "result"]:
            arr = data.get(key)
            if isinstance(arr, list):
                return normalize_directions(arr)
    return []

def get_doctors(base_url: str, token: str, id_direction: Optional[str]) -> List[Dict[str, Any]]:
    params = {"token": token}
    if id_direction:
        params["idDirection"] = id_direction
    r = api_get(base_url, ENDPOINTS["doctors"], params=params)
    if r.status_code != 200:
        raise RuntimeError(f"Doctors error {r.status_code}: {r.text[:400]}")
    data = safe_json(r)
    return normalize_doctors(data)

def normalize_doctors(data: Any) -> List[Dict[str, Any]]:
    out = []
    if isinstance(data, list):
        iterable = data
    elif isinstance(data, dict):
        iterable = data.get("data") or data.get("items") or data.get("result") or data.get("doctors") or []
    else:
        iterable = []
    for x in iterable:
        if isinstance(x, dict):
            out.append({
                "id": x.get("id") or x.get("Id") or x.get("doctorId") or x.get("ID"),
                "name": x.get("name") or x.get("fio") or x.get("FIO") or x.get("fullName") or "",
                "raw": x,
            })
    # Deduplicate by id
    seen = set()
    uniq = []
    for d in out:
        if d["id"] and d["id"] not in seen:
            uniq.append(d)
            seen.add(d["id"])
    return uniq

def get_service_duration(base_url: str, token: str, id_direction: Optional[str]) -> List[Dict[str, Any]]:
    if not id_direction:
        return []
    r = api_get(base_url, ENDPOINTS["service_duration"], params={"token": token, "idDirection": id_direction})
    if r.status_code != 200:
        return []
    data = safe_json(r)
    return normalize_services(data)

def normalize_services(data: Any) -> List[Dict[str, Any]]:
    out = []
    iterable = []
    if isinstance(data, list):
        iterable = data
    elif isinstance(data, dict):
        for key in ["services", "data", "items", "result"]:
            if isinstance(data.get(key), list):
                iterable = data[key]
                break
    for x in iterable:
        if isinstance(x, dict):
            out.append({
                "id": x.get("id") or x.get("serviceId") or x.get("Id"),
                "name": x.get("name") or x.get("serviceName") or x.get("Name") or x.get("researchText") or "",
                "duration": x.get("duration") or x.get("Duration") or x.get("timePriemMinutes"),
                "raw": x,
            })
    return out

def get_schedule(base_url: str, token: str, doctor_id: str, start_date: str, end_date: str, service_id: Optional[str]) -> List[Dict[str, Any]]:
    params = {
        "token": token,
        "doctorIds": str(doctor_id),
        "startDate": start_date,  # DD.MM.YYYY
        "endDate": end_date,      # DD.MM.YYYY
    }
    if service_id:
        params["serviceId"] = str(service_id)
    r = api_get(base_url, ENDPOINTS["schedule"], params=params)
    if r.status_code != 200:
        raise RuntimeError(f"Schedule error {r.status_code}: {r.text[:400]}")
    data = safe_json(r)
    return normalize_slots(data)

def normalize_slots(data: Any) -> List[Dict[str, Any]]:
    """Уніфікацыя розных форматаў раскладу ў плоскі спіс слотаў."""
    slots: List[Dict[str, Any]] = []

    def add_slot(start: str, end: Optional[str] = None, raw: Any = None) -> None:
        slots.append({"startAt": start, "endAt": end, "raw": raw})

    # Варыянт 1: nested-спісы (як у цябе)
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
                        for s in day_slots:
                            if not isinstance(s, dict):
                                continue
                            st = s.get("startAt") or s.get("start") or s.get("time")
                            en = s.get("endAt") or s.get("end")
                            if not st:
                                continue
                            if isinstance(st, str) and len(st) <= 5 and ":" in st:
                                st_full = f"{date_str} {st}"
                            else:
                                st_full = st
                            if isinstance(en, str) and len(en) <= 5 and ":" in en:
                                en_full = f"{date_str} {en}"
                            else:
                                en_full = en
                            raw = {"date": date_str, **meta, **s}
                            add_slot(st_full, en_full, raw)
        if slots:
            return slots

    # Варыянт 2: dict {docId: [ {date, times:[..]} ]}
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                for day in v:
                    if isinstance(day, dict):
                        date = day.get("date") or day.get("Date")
                        times = day.get("times") or day.get("Times") or []
                        if isinstance(times, list) and date:
                            for t in times:
                                if isinstance(t, str):
                                    add_slot(f"{date} {t}", None, {"date": date, "time": t})
        if slots:
            return slots

    # Варыянт 3: ужо плоскі спіс
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                start = x.get("startAt") or x.get("start") or x.get("time")
                end   = x.get("endAt") or x.get("end")
                if start:
                    add_slot(start, end, x)

    return slots

def create_record(base_url: str, token: str, doctor_id: str, patient_id: str, start_at: str, end_at: Optional[str], description: str, insurer: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = {
        "token": token,
        "doctor": str(doctor_id),
        "patient": str(patient_id),
        "startAt": start_at,  # 'DD.MM.YYYY HH:MM'
        "endAt": end_at or "",
        "description": description,
        "Ins_name": insurer,
    }
    if extra:
        for k, v in extra.items():
            if v is not None and v != "":
                data[k] = v
    r = api_post_form(base_url, ENDPOINTS["record_create"], data=data)
    if r.status_code != 200:
        try:
            payload = r.json()
        except Exception:
            payload = r.text[:800]
        return {"status_code": r.status_code, "error": payload, "sent": data}
    return {"status_code": r.status_code, "data": safe_json(r), "sent": data}

def list_patient_records(base_url: str, token: str, patient_api_id: str) -> List[Dict[str, Any]]:
    r = api_get(base_url, ENDPOINTS["patient_records"], params={"token": token, "patientAPIId": str(patient_api_id)})
    if r.status_code != 200:
        raise RuntimeError(f"Patient records error {r.status_code}: {r.text[:400]}")
    data = safe_json(r)
    return normalize_records(data)

def normalize_records(data: Any) -> List[Dict[str, Any]]:
    """Устойлівая нармалізацыя /patient/recordsbyid."""
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict) and isinstance(data[0].get("records"), list):
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
    for x in iterable:
        if not isinstance(x, dict):
            continue
        items.append({
            "recordId": x.get("id") or x.get("recordId") or x.get("Id"),
            "doctor": x.get("doctorName") or x.get("doctor") or x.get("Doctor"),
            "startAt": x.get("startAt") or x.get("date") or x.get("start"),
            "endAt": x.get("endAt") or x.get("end"),
            "status": x.get("status") or x.get("Status") or x.get("status_pac"),
            "raw": x,
        })
    return items

def cancel_record(base_url: str, token: str, record_id: str,
                  cancel_status: str = "CAN") -> Dict[str, Any]:
    """Змена статусу запісу. НЕ перадаём patient/patientAPIId — backend іх не чакае."""
    data = {
        "token": token,
        "recordId": str(record_id),
        "status": cancel_status,   # напр. CAN / DEL / CNL / FIL
    }
    r = api_post_form(base_url, ENDPOINTS["record_change_status"], data=data)
    return {"status_code": r.status_code, "data": safe_json(r), "sent": data}

# -----------------------
# Gradio UI
# -----------------------

def _in_notebook() -> bool:
    """Дэтэкцыя Jupyter/Colab для аўта-запуску UI."""
    try:
        from IPython import get_ipython  # type: ignore
        ip = get_ipython()
        if not ip:
            return False
        if 'google.colab' in sys.modules:
            return True
        return hasattr(ip, 'kernel')
    except Exception:
        return False

def launch_gradio():
    if gr is None:
        raise RuntimeError("Gradio is not installed. In Colab run: pip install -q gradio")

    with gr.Blocks(title="AmedisMed Client (curl)") as demo:
        gr.Markdown("# AmedisMed — кліент для API (curl workaround)\nУводзіце токен, абірайце напрамак/доктара/слоты, стварайце і адмяняйце запісы.")

        with gr.Row():
            base_url = gr.Textbox(value=BASE_URL_DEFAULT, label="Base URL")
            token_file = gr.Textbox(value="token.txt", label="Шлях да файла з токенам")
            load_btn = gr.Button("Загрузіць токен і напрамкі")
        with gr.Row():
            har_file = gr.Textbox(value="/content/amedismed_full.by.har", label="Шлях да HAR (неабавязкова)")
            har_btn = gr.Button("Аўта-выяўленне з HAR")

        token_state = gr.State("")
        doctor_map_state = gr.State("{}")
        service_map_state = gr.State("{}")
        slot_map_state = gr.State("{}")
        records_map_state = gr.State("{}")
        patient_autofill_state = gr.State("")
        insurer_autofill_state = gr.State("")
        info = gr.Textbox(label="Інфо", interactive=False)

        with gr.Tab("1) Напрамкі і дактары"):
            directions = gr.Dropdown(label="Напрамак (id)", choices=[])
            dirs_table = gr.Dataframe(headers=["id", "name"], interactive=False)
            fetch_doctors_btn = gr.Button("Атрымаць дактароў")
            doctors = gr.Dropdown(label="Доктар", choices=[])
            doctors_json = gr.Code(label="Doctors JSON", interactive=False)

            fetch_services_btn = gr.Button("Атрымаць паслугі/працягласць")
            services = gr.Dropdown(label="Паслуга/ServiceId", choices=[])
            services_json = gr.Code(label="Services JSON", interactive=False)

        with gr.Tab("2) Слоты і запіс"):
            with gr.Row():
                start_date = gr.Textbox(label="Пачатак (DD.MM.YYYY)", value=(datetime.now().strftime("%d.%m.%Y")))
                end_date = gr.Textbox(label="Канец (DD.MM.YYYY)", value=((datetime.now()+timedelta(days=7)).strftime("%d.%m.%Y")))
            fetch_slots_btn = gr.Button("Атрымаць слоты")
            slots = gr.Dropdown(label="Слот", choices=[])
            slots_json = gr.Code(label="Slots JSON", interactive=False)
            with gr.Row():
                patient_id_tb = gr.Textbox(label="Patient ID (patientAPIId)", value="44213")
                description_tb = gr.Textbox(label="Апісанне", value="")
                insurer_tb = gr.Textbox(label="Страхоўшчык (Ins_name)", value="")
            create_btn = gr.Button("Стварыць запіс")
            create_result = gr.Code(label="Вынік стварэння")

        with gr.Tab("3) Кабінет і адмена"):
            refresh_records_btn = gr.Button("Паказаць мае запісы")
            records = gr.Dropdown(label="Мае запісы", choices=[])
            cancel_status_tb = gr.Textbox(label="Код статусу адмены", value="CAN")
            cancel_btn = gr.Button("Адмяніць абраны запіс")
            cancel_result = gr.Code(label="Вынік адмены")

        # --- Handlers ---
        def ui_load_token(path, base_url):
            try:
                token = read_token_from_file(path)
                used_ep, dirs, msg = discover_directions(base_url, token)
                dirs_table = [[d.get("id"), d.get("name")] for d in dirs] if dirs else []
                return token, gr.update(value=f"{msg}\nEndpoint: {used_ep or '-'}"), gr.update(choices=[str(d.get("id")) for d in dirs], value=(str(dirs[0]['id']) if dirs else None)), dirs_table
            except Exception as e:
                return "", gr.update(value=f"Памылка: {e}"), gr.update(choices=[]), []

        load_btn.click(ui_load_token, inputs=[token_file, base_url], outputs=[token_state, info, directions, dirs_table])

        def ui_har_autofill(har_path):
            data = parse_har_for_patient(har_path)
            msg = []
            if data.get("patient_ids"):
                msg.append(f"Знойдзены patientAPIId: {', '.join(map(str, data['patient_ids']))}")
            if data.get("ins_name"):
                msg.append(f"Ins_name па змаўчанні: {data['ins_name']}")
            if data.get("record_fields"):
                msg.append(f"Палі /record/create: {', '.join(data['record_fields'])}")
            pid = data.get("patient_ids", [""])[0] if data.get("patient_ids") else ""
            ins = data.get("ins_name") or ""
            info_txt = "\n".join(msg) if msg else "HAR не даў дадзеных. Праверце шлях."
            return (gr.update(value=pid or "44213"), gr.update(value=ins), info_txt, pid, ins)

        har_btn.click(ui_har_autofill, inputs=[har_file], outputs=[patient_id_tb, insurer_tb, info, patient_autofill_state, insurer_autofill_state])

        def ui_fetch_doctors(base, token, direction_id):
            try:
                docs = get_doctors(base, token, direction_id or None)
                choices = [f"{d['id']} — {d['name']}".strip() for d in docs]
                id_map = {choices[i]: str(docs[i]['id']) for i in range(len(docs))}
                return gr.update(choices=choices, value=(choices[0] if choices else None)), json.dumps(docs, ensure_ascii=False, indent=2), json.dumps(id_map, ensure_ascii=False)
            except Exception as e:
                return gr.update(choices=[], value=None), f"Памылка: {e}", "{}"

        fetch_doctors_btn.click(ui_fetch_doctors, inputs=[base_url, token_state, directions], outputs=[doctors, doctors_json, doctor_map_state])

        def ui_fetch_services(base, token, direction_id):
            try:
                services_list = get_service_duration(base, token, direction_id or None)
                svc_choices = [f"{s['id']} — {s['name']} ({s.get('duration','?')} мiн)" if s.get('name') else str(s['id']) for s in services_list]
                svc_map = {svc_choices[i]: str(services_list[i]['id']) for i in range(len(services_list))}
                return gr.update(choices=svc_choices, value=(svc_choices[0] if svc_choices else None)), json.dumps(services_list, ensure_ascii=False, indent=2), json.dumps(svc_map, ensure_ascii=False)
            except Exception as e:
                return gr.update(choices=[], value=None), f"Памылка: {e}", "{}"

        fetch_services_btn.click(ui_fetch_services, inputs=[base_url, token_state, directions], outputs=[services, services_json, service_map_state])

        def ui_fetch_slots(base, token, doctor_choice, doctor_map_json, sdate, edate, service_choice, svc_map_json):
            try:
                try:
                    doctor_map = json.loads(doctor_map_json or "{}")
                except Exception:
                    doctor_map = {}
                doctor_id = doctor_map.get(doctor_choice) or doctor_choice or ""
                try:
                    svc_map = json.loads(svc_map_json or "{}")
                except Exception:
                    svc_map = {}
                service_id = svc_map.get(service_choice)
                if not doctor_id:
                    raise ValueError("Не абраны доктар")
                if not service_id:
                    raise ValueError("Абярыце паслугу (serviceId) перад пошукам слотаў")
                slots_list = get_schedule(base, token, doctor_id, sdate, edate, service_id)
                labels = []
                for s in slots_list:
                    label = s.get("startAt") or json.dumps(s.get("raw", {}), ensure_ascii=False)
                    if s.get("endAt"):
                        label += f" — {s['endAt']}"
                    labels.append(label)
                mapping = {labels[i]: slots_list[i] for i in range(len(slots_list))}
                return gr.update(choices=labels, value=(labels[0] if labels else None)), json.dumps(slots_list, ensure_ascii=False, indent=2), json.dumps(mapping, ensure_ascii=False)
            except Exception as e:
                return gr.update(choices=[], value=None), f"Памылка: {e}", "{}"

        fetch_slots_btn.click(ui_fetch_slots, inputs=[base_url, token_state, doctors, doctor_map_state, start_date, end_date, services, service_map_state], outputs=[slots, slots_json, slot_map_state])

        def ui_create_record(base, token, doctor_choice, doctor_map_json, patient_id, slot_choice, slot_map_json, desc, insurer, service_choice, svc_map_json):
            try:
                try:
                    doctor_map = json.loads(doctor_map_json or "{}")
                except Exception:
                    doctor_map = {}
                doctor_id = doctor_map.get(doctor_choice) or doctor_choice or ""
                if not doctor_id:
                    raise ValueError("Не абраны доктар")
                if not patient_id:
                    raise ValueError("Пацыент ID абавязковы")
                slot_map = json.loads(slot_map_json or "{}")
                slot = slot_map.get(slot_choice) or {}
                start_at = slot.get("startAt") or ""
                end_at = slot.get("endAt") or ""
                if not start_at:
                    raise ValueError("Не абраны слот")
                extra: Dict[str, Any] = {}
                raw = slot.get("raw") or {}
                for key in ["officeId", "cabinetId", "serviceId", "directionId", "office", "cabinet"]:
                    if raw.get(key) is not None:
                        extra[key] = raw.get(key)
                try:
                    svc_map = json.loads(svc_map_json or "{}")
                    chosen_service_id = svc_map.get(service_choice)
                    if chosen_service_id and not extra.get("serviceId"):
                        extra["serviceId"] = chosen_service_id
                except Exception:
                    pass
                if end_at and len(end_at) <= 5 and ":" in end_at:
                    date_part = start_at.split(" ")[0]
                    end_at = f"{date_part} {end_at}"
                res = create_record(base, token, doctor_id, patient_id, start_at, end_at, desc or "", insurer or "", extra=extra)
                return json.dumps(res, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"Памылка: {e}"

        create_btn.click(ui_create_record, inputs=[base_url, token_state, doctors, doctor_map_state, patient_id_tb, slots, slot_map_state, description_tb, insurer_tb, services, service_map_state], outputs=[create_result])

        def ui_list_records(base, token, patient_api_id):
            try:
                items = list_patient_records(base, token, patient_api_id)
                if not items:
                    return gr.update(choices=[]), json.dumps(items, ensure_ascii=False, indent=2)
                labels = [f"{it.get('recordId')} — {it.get('startAt')} — {it.get('status')}" for it in items]
                mapping = {labels[i]: str(items[i].get("recordId")) for i in range(len(items))}
                return gr.update(choices=labels, value=labels[0]), json.dumps(mapping, ensure_ascii=False)
            except Exception as e:
                return gr.update(choices=[]), f"Памылка: {e}"

        refresh_records_btn.click(ui_list_records, inputs=[base_url, token_state, patient_id_tb], outputs=[records, records_map_state])

        def ui_cancel_record(base, token, record_label, mapping_json, cancel_status):
            try:
                mapping = json.loads(mapping_json or "{}")
                record_id = mapping.get(record_label) or record_label
                if not record_id:
                    raise ValueError("Абярыце запіс для адмены")
                res = cancel_record(base, token, record_id, cancel_status or "CAN")
                return json.dumps(res, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"Памылка: {e}"

        # patient_id не патрэбны для change-status
        cancel_btn.click(
            ui_cancel_record,
            inputs=[base_url, token_state, records, records_map_state, cancel_status_tb],
            outputs=[cancel_result]
        )

    return demo

# -----------------------
# Entrypoint
# -----------------------
if __name__ == "__main__":
    if gr is None:
        raise RuntimeError("Gradio is not installed. Install it with: pip install gradio")
    demo = launch_gradio()
    share = os.environ.get("AUTO_SHARE", "0") == "1"
    if os.environ.get("AUTO_LAUNCH", "1") == "1" and (lambda: (__import__("IPython"), True) if 'IPython' in sys.modules else False)():
        demo.launch(share=share)
    else:
        demo.launch(share=share)
