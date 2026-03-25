
import hmac
import json
import math
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from CoolProp.CoolProp import PropsSI
    HAS_CP = True
except Exception:
    HAS_CP = False

# ================= CONSTANTS =================
APP_VERSION = "1.2.0-plain-cloud-multiuser"
P_ATM = 101325.0
R_DA = 287.055
CP_DA = 1006.0
CP_V = 1860.0
H_LV0 = 2501000.0
INCH = 0.0254
MM = 1e-3
GRAVITY = 9.80665
WATER = "Water"
REPO_ROOT = Path(__file__).resolve().parent
CALIBRATION_FILE = REPO_ROOT / "calibration" / "calibration_data.json"
CALIBRATION_BACKUP_DIR = REPO_ROOT / "calibration" / "backup"
APP_SETTINGS_FILE = REPO_ROOT / "calibration" / "app_settings.json"
EXAMPLES_DIR = REPO_ROOT / "examples"
TEST_DATA_DIR = REPO_ROOT / "test_data"

DEFAULT_APP_SETTINGS = {
    "app_title": "Steam Air Heater Coil Designer",
    "app_subtitle": "AHU reheat coil model for dry sensible heating after a cooling coil",
    "saved_designs_root": "saved_designs",
    "allow_user_json_upload": True,
    "max_saved_designs_per_user": 200,
}

DEFAULT_CALIBRATION = {
    "version": "1.1",
    "created": "2026-01-01T00:00:00",
    "air_side": {
        "dp_correction": {
            "multiplier": 1.0,
            "Re_exponent": 0.0,
            "FPI_factor": 0.0,
            "row_factor": 0.0,
        },
        "h_correction": {
            "multiplier": 1.0,
            "near_saturation_sensitivity": 0.0,
        },
    },
    "steam_side": {
        "h_correction": {
            "single_phase": 1.0,
            "condensation": 1.0,
            "subcooled_condensate": 1.0,
        },
        "dp_correction": {
            "single_phase": 1.0,
            "condensation_region": 1.0,
            "header": 1.0,
        },
    },
    "validation_stats": {
        "r_squared": 0.0,
        "n_tests": 0,
        "last_calibrated": None,
        "status": "ACTIVE - editable by admin only",
    },
    "calibration_notes": [
        "Master calibration file for the steam reheat coil app.",
        "Only admin users should change these multipliers.",
        "Keep downloaded backups before applying measured-data tuning.",
    ],
}

INPUT_DEFAULTS = {
    "run_mode": "Rating: given steam flow",
    "face_w": 1.2,
    "face_h": 0.85,
    "st_mm": 22.0,
    "sl_mm": 25.4,
    "rows": 2,
    "do_mm": 9.53,
    "tw_mm": 0.30,
    "fpi": 10.0,
    "tf_mm": 0.12,
    "fin_mat": "Aluminum",
    "tube_mat": "Copper",
    "circuits": 20,
    "sigma_free": 0.55,
    "fin_type": "Wavy (no louvers)",
    "louver_angle_deg": 40.0,
    "louver_gap_mm": 2.0,
    "louver_cuts_per_row": 8,
    "h_mult_wavy": 1.15,
    "dp_mult_wavy": 1.20,
    "rfo": 0.0002,
    "rfi": 0.0001,
    "flow_mode": "Volume flow (m3/h)",
    "v_face_input": 1.70,
    "vdot_m3h": 6250.0,
    "air_mode": "Dry Bulb + RH",
    "t_air_in_c": 13.5,
    "rh_air_in": 95.0,
    "wb_air_in": 12.8,
    "target_t_air_out_c": 18.0,
    "pressure_basis": "bar(g)",
    "p_steam_input": 2.0,
    "inlet_state": "Saturated dry steam",
    "max_subcool_k": 0.0,
    "x_inlet": 0.98,
    "superheat_k": 5.0,
    "mdot_steam_kg_h": 80.0,
    "header_length_m": 0.85,
    "header_in_diam_in": 1.0,
    "header_out_diam_in": 1.5,
    "save_design_name": "",
}


def K(t_c: float) -> float:
    return t_c + 273.15


def _deep_copy_jsonable(data: Any) -> Any:
    return json.loads(json.dumps(data))


def _merge_defaults(defaults: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    result = _deep_copy_jsonable(defaults)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = value
    return result


def ensure_repo_dirs(settings: Optional[Dict[str, Any]] = None) -> None:
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    effective_settings = settings or DEFAULT_APP_SETTINGS
    (REPO_ROOT / effective_settings.get("saved_designs_root", "saved_designs")).mkdir(parents=True, exist_ok=True)


def load_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return _deep_copy_jsonable(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _merge_defaults(default, data)
    except Exception:
        pass
    return _deep_copy_jsonable(default)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_calibration_data() -> Dict[str, Any]:
    ensure_repo_dirs()
    if not CALIBRATION_FILE.exists():
        atomic_write_json(CALIBRATION_FILE, DEFAULT_CALIBRATION)
    return load_json_file(CALIBRATION_FILE, DEFAULT_CALIBRATION)


def save_calibration_data(payload: Dict[str, Any], make_backup: bool = True) -> None:
    ensure_repo_dirs()
    if make_backup and CALIBRATION_FILE.exists():
        backup = CALIBRATION_BACKUP_DIR / f"calibration_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup.write_text(CALIBRATION_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    atomic_write_json(CALIBRATION_FILE, payload)


def load_app_settings() -> Dict[str, Any]:
    ensure_repo_dirs()
    if not APP_SETTINGS_FILE.exists():
        atomic_write_json(APP_SETTINGS_FILE, DEFAULT_APP_SETTINGS)
    return load_json_file(APP_SETTINGS_FILE, DEFAULT_APP_SETTINGS)


def save_app_settings(payload: Dict[str, Any], make_backup: bool = True) -> None:
    ensure_repo_dirs(payload)
    if make_backup and APP_SETTINGS_FILE.exists():
        backup = CALIBRATION_BACKUP_DIR / f"app_settings_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup.write_text(APP_SETTINGS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    atomic_write_json(APP_SETTINGS_FILE, payload)


def saved_designs_root(settings: Dict[str, Any]) -> Path:
    return REPO_ROOT / settings.get("saved_designs_root", "saved_designs")


def sanitize_filename(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "design"


def user_design_dir(username: str, settings: Dict[str, Any]) -> Path:
    path = saved_designs_root(settings) / sanitize_filename(username)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_design_files(username: str, settings: Dict[str, Any]) -> list[Path]:
    path = user_design_dir(username, settings)
    return sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def list_example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.json"))


def save_design_record(username: str, settings: Dict[str, Any], record: Dict[str, Any], design_name: str) -> Path:
    root = user_design_dir(username, settings)
    max_files = int(settings.get("max_saved_designs_per_user", 200))
    existing = list_design_files(username, settings)
    while len(existing) >= max_files and existing:
        existing[-1].unlink(missing_ok=True)
        existing = list_design_files(username, settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_{sanitize_filename(design_name)}.json"
    path = root / filename
    atomic_write_json(path, record)
    return path


def delete_design_file(path: Path, username: str, settings: Dict[str, Any]) -> None:
    allowed_root = user_design_dir(username, settings).resolve()
    resolved = path.resolve()
    if allowed_root in resolved.parents and resolved.exists() and resolved.suffix.lower() == ".json":
        resolved.unlink()


def verify_password(password: str, stored_value: str) -> bool:
    """
    Preferred secrets format for this cloud-only version:
        [auth.users.someuser]
        password = "MyPassword123"
        role = "user"
        enabled = true

    Backward compatibility retained for:
    - plain:MyPassword123
    - legacy pbkdf2_sha256$... strings from older app versions
    """
    stored_value = str(stored_value or "").strip()
    if not stored_value:
        return False

    if stored_value.startswith("plain:"):
        stored_value = stored_value.split(":", 1)[1]

    if stored_value.startswith("pbkdf2_sha256$"):
        try:
            import hashlib

            _, iter_text, salt, hash_hex = stored_value.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iter_text))
            trial = f"pbkdf2_sha256${int(iter_text)}${salt}${dk.hex()}"
            return hmac.compare_digest(trial, stored_value)
        except Exception:
            return False

    return hmac.compare_digest(password, stored_value)


def load_auth_users() -> Dict[str, Dict[str, Any]]:
    try:
        auth_cfg = st.secrets.get("auth", {})
        users_cfg = auth_cfg.get("users", {})
    except Exception:
        users_cfg = {}

    parsed: Dict[str, Dict[str, Any]] = {}
    for raw_username, raw_cfg in dict(users_cfg).items():
        username = str(raw_username).strip()
        if not username:
            continue

        password_value = ""
        role = "user"
        enabled = True

        if hasattr(raw_cfg, "items"):
            raw_dict = dict(raw_cfg)
            if "password" in raw_dict:
                password_value = str(raw_dict.get("password", "")).strip()
            elif "password_hash" in raw_dict:
                password_value = str(raw_dict.get("password_hash", "")).strip()
            else:
                password_value = ""
            role = str(raw_dict.get("role", "user")).strip().lower() or "user"
            enabled = bool(raw_dict.get("enabled", True))
        else:
            password_value = str(raw_cfg).strip()

        parsed[username] = {
            "password": password_value,
            "role": role,
            "enabled": enabled,
        }
    return parsed


def logout() -> None:
    for key in [
        "authenticated",
        "auth_username",
        "auth_role",
        "login_username",
        "login_password",
        "login_error",
    ]:
        st.session_state.pop(key, None)


def authenticate_user(app_title: str) -> bool:
    users = load_auth_users()
    if not users:
        st.error("No user accounts found in Streamlit secrets. Add [auth.users.<username>] entries before running the app.")
        st.stop()

    if st.session_state.get("authenticated", False):
        return True

    st.session_state.setdefault("login_error", "")

    col1, col2, col3 = st.columns([1, 1.4, 1])
    with col2:
        st.title(app_title)
        st.caption("Multi-user cloud login required. Usernames and passwords are read from Streamlit Cloud Secrets.")
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login", use_container_width=True):
            user_cfg = users.get(username)
            if not user_cfg or not user_cfg.get("enabled", True):
                st.session_state["login_error"] = "Unknown or disabled user account."
            elif verify_password(password, user_cfg.get("password", "")):
                st.session_state["authenticated"] = True
                st.session_state["auth_username"] = username
                st.session_state["auth_role"] = user_cfg.get("role", "user")
                st.session_state.pop("login_password", None)
                st.session_state["login_error"] = ""
                st.rerun()
            else:
                st.session_state["login_error"] = "Incorrect username or password."

        if st.session_state.get("login_error"):
            st.error(st.session_state["login_error"])
    return False


def init_session_state() -> None:
    for key, value in INPUT_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    st.session_state.setdefault("current_summary", None)
    st.session_state.setdefault("current_rows_csv", None)
    st.session_state.setdefault("current_rows_df", None)
    st.session_state.setdefault("current_design_record", None)
    st.session_state.setdefault("selected_design_ref", "")
    st.session_state.setdefault("uploaded_design_text", "")


def current_input_payload() -> Dict[str, Any]:
    payload = {key: st.session_state.get(key, default) for key, default in INPUT_DEFAULTS.items()}
    payload["wb_air_in"] = min(float(payload["wb_air_in"]), float(payload["t_air_in_c"]))
    payload["target_t_air_out_c"] = max(float(payload["target_t_air_out_c"]), float(payload["t_air_in_c"]))
    if payload["flow_mode"] == "Face velocity (m/s)":
        payload["vdot_m3h"] = float(payload["v_face_input"]) * float(payload["face_w"]) * float(payload["face_h"]) * 3600.0
    else:
        payload["v_face_input"] = float(payload["vdot_m3h"]) / 3600.0 / max(float(payload["face_w"]) * float(payload["face_h"]), 1e-9)
    return payload


def build_design_record(username: str) -> Dict[str, Any]:
    return {
        "meta": {
            "owner": username,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "app": "steam_air_heater_coil",
            "version": APP_VERSION,
        },
        "inputs": current_input_payload(),
        "last_summary": st.session_state.get("current_summary"),
    }


def apply_input_payload(inputs: Dict[str, Any]) -> None:
    for key in INPUT_DEFAULTS:
        if key in inputs:
            st.session_state[key] = inputs[key]
    st.session_state["wb_air_in"] = min(float(st.session_state.get("wb_air_in", 0.0)), float(st.session_state.get("t_air_in_c", 0.0)))
    st.session_state["target_t_air_out_c"] = max(float(st.session_state.get("target_t_air_out_c", 0.0)), float(st.session_state.get("t_air_in_c", 0.0)))


def extract_inputs_from_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload.get("inputs"), dict):
        return payload["inputs"]
    geometry = payload.get("geometry", {}) if isinstance(payload.get("geometry"), dict) else {}
    air_side = payload.get("air_side", {}) if isinstance(payload.get("air_side"), dict) else {}
    steam_side = payload.get("steam_side", {}) if isinstance(payload.get("steam_side"), dict) else {}
    advanced = payload.get("advanced", {}) if isinstance(payload.get("advanced"), dict) else {}
    mapped = {
        "face_w": geometry.get("face_width_m", INPUT_DEFAULTS["face_w"]),
        "face_h": geometry.get("face_height_m", INPUT_DEFAULTS["face_h"]),
        "rows": geometry.get("rows", INPUT_DEFAULTS["rows"]),
        "st_mm": geometry.get("row_pitch_mm", INPUT_DEFAULTS["st_mm"]),
        "sl_mm": geometry.get("longitudinal_pitch_mm", INPUT_DEFAULTS["sl_mm"]),
        "do_mm": geometry.get("tube_od_mm", INPUT_DEFAULTS["do_mm"]),
        "tw_mm": geometry.get("tube_thickness_mm", INPUT_DEFAULTS["tw_mm"]),
        "fpi": geometry.get("fpi", INPUT_DEFAULTS["fpi"]),
        "tf_mm": geometry.get("fin_thickness_mm", INPUT_DEFAULTS["tf_mm"]),
        "circuits": geometry.get("circuits", INPUT_DEFAULTS["circuits"]),
        "fin_mat": geometry.get("fin_material", INPUT_DEFAULTS["fin_mat"]),
        "tube_mat": geometry.get("tube_material", INPUT_DEFAULTS["tube_mat"]),
        "sigma_free": geometry.get("sigma_free_area", INPUT_DEFAULTS["sigma_free"]),
        "flow_mode": air_side.get("flow_input_mode", INPUT_DEFAULTS["flow_mode"]),
        "vdot_m3h": air_side.get("volume_flow_m3h", INPUT_DEFAULTS["vdot_m3h"]),
        "v_face_input": air_side.get("face_velocity_ms", INPUT_DEFAULTS["v_face_input"]),
        "air_mode": "Dry Bulb + RH" if "rh_in_pct" in air_side else INPUT_DEFAULTS["air_mode"],
        "t_air_in_c": air_side.get("db_in_c", INPUT_DEFAULTS["t_air_in_c"]),
        "rh_air_in": air_side.get("rh_in_pct", INPUT_DEFAULTS["rh_air_in"]),
        "wb_air_in": air_side.get("wb_in_c", INPUT_DEFAULTS["wb_air_in"]),
        "target_t_air_out_c": air_side.get("target_db_out_c", INPUT_DEFAULTS["target_t_air_out_c"]),
        "pressure_basis": steam_side.get("pressure_basis", INPUT_DEFAULTS["pressure_basis"]),
        "p_steam_input": steam_side.get("steam_pressure", INPUT_DEFAULTS["p_steam_input"]),
        "inlet_state": steam_side.get("inlet_state", INPUT_DEFAULTS["inlet_state"]),
        "x_inlet": steam_side.get("wet_steam_quality_x", INPUT_DEFAULTS["x_inlet"]),
        "superheat_k": steam_side.get("superheat_k", INPUT_DEFAULTS["superheat_k"]),
        "mdot_steam_kg_h": steam_side.get("steam_mass_flow_kgh", INPUT_DEFAULTS["mdot_steam_kg_h"]),
        "max_subcool_k": steam_side.get("max_subcool_k", INPUT_DEFAULTS["max_subcool_k"]),
        "header_in_diam_in": steam_side.get("header_inlet_diameter_in", INPUT_DEFAULTS["header_in_diam_in"]),
        "header_out_diam_in": steam_side.get("header_outlet_diameter_in", INPUT_DEFAULTS["header_out_diam_in"]),
        "header_length_m": steam_side.get("header_length_m", INPUT_DEFAULTS["header_length_m"]),
        "fin_type": advanced.get("fin_type", INPUT_DEFAULTS["fin_type"]),
        "louver_angle_deg": advanced.get("louver_angle_deg", INPUT_DEFAULTS["louver_angle_deg"]),
        "louver_cuts_per_row": advanced.get("louver_cuts_per_row", INPUT_DEFAULTS["louver_cuts_per_row"]),
        "louver_gap_mm": advanced.get("louver_gap_mm", INPUT_DEFAULTS["louver_gap_mm"]),
        "h_mult_wavy": advanced.get("h_mult_wavy", INPUT_DEFAULTS["h_mult_wavy"]),
        "dp_mult_wavy": advanced.get("dp_mult_wavy", INPUT_DEFAULTS["dp_mult_wavy"]),
        "rfo": advanced.get("air_side_fouling", INPUT_DEFAULTS["rfo"]),
        "rfi": advanced.get("tube_side_fouling", INPUT_DEFAULTS["rfi"]),
    }
    return mapped

# ================= PSYCHROMETRICS =================
def psat_water_pa(t_c: float) -> float:
    return 611.21 * math.exp((18.678 - t_c / 234.5) * (t_c / (257.14 + t_c)))


def w_from_t_rh(t_c: float, rh_pct: float, p: float = P_ATM) -> float:
    rh = max(min(rh_pct, 100.0), 0.1) / 100.0
    p_sat = psat_water_pa(t_c)
    p_v = rh * p_sat
    return 0.62198 * p_v / max(p - p_v, 1.0)


def w_from_t_wb(tdb_c: float, twb_c: float, p: float = P_ATM) -> float:
    w_sat_wb = w_from_t_rh(twb_c, 100.0, p)
    h_fg_wb = 2501000.0 - 2369.0 * twb_c
    numer = (w_sat_wb * (h_fg_wb + CP_V * twb_c) - CP_DA * (tdb_c - twb_c))
    denom = h_fg_wb + CP_V * tdb_c
    return max(0.0, numer / max(denom, 1e-9))


def h_moist_j_per_kg_da(t_c: float, w: float) -> float:
    return 1000.0 * 1.006 * t_c + w * (H_LV0 + 1000.0 * 1.86 * t_c)


def cp_moist_j_per_kgk(t_c: float, w: float) -> float:
    return CP_DA + w * CP_V


def rho_moist_kg_m3(t_c: float, w: float, p: float = P_ATM) -> float:
    return p / (R_DA * K(t_c) * (1.0 + 1.6078 * w))


def rh_from_t_w(t_c: float, w: float, p: float = P_ATM) -> float:
    p_v = w * p / (0.62198 + w)
    p_sat = psat_water_pa(t_c)
    return max(0.1, min(100.0, 100.0 * p_v / max(p_sat, 1e-9)))


def wb_from_t_w(tdb_c: float, w_target: float, p: float = P_ATM) -> float:
    lo, hi = -20.0, tdb_c
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        w_mid = w_from_t_wb(tdb_c, mid, p)
        if w_mid > w_target:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def dew_point_from_t_w(t_c: float, w: float, p: float = P_ATM) -> float:
    p_v = w * p / (0.62198 + w)
    guess = t_c
    for _ in range(40):
        p_sat = psat_water_pa(guess)
        err = p_sat - p_v
        if abs(err) < 0.1:
            break
        dp_dt = p_sat * (
            18.678 / (257.14 + guess)
            - (18.678 - guess / 234.5) * guess / (257.14 + guess) ** 2
        )
        guess -= err / max(dp_dt, 1e-6)
    return guess


def t_from_h_w(h_j_per_kg_da: float, w: float) -> float:
    denom = 1000.0 * (1.006 + 1.86 * w)
    return (h_j_per_kg_da - w * H_LV0) / max(denom, 1e-9)


# ================= GEOMETRY =================
def geometry_areas(face_w: float, face_h: float, rows: int, st: float, do: float, tf: float, fpi: float, sl: float) -> Dict[str, float]:
    face_area = face_w * face_h
    fin_pitch = (1.0 / max(fpi, 1e-9)) * INCH
    fins = max(int(math.floor(face_h / max(fin_pitch, 1e-9))), 1)
    tubes_per_row = max(int(math.floor(face_w / max(sl, 1e-9))), 1)
    n_tubes = tubes_per_row * rows
    l_tube = face_w
    depth = st * rows

    a_holes_one_fin = n_tubes * math.pi * (do / 2.0) ** 2
    a_fin_one = max(2.0 * (face_w * depth - a_holes_one_fin), 0.0)
    a_fin_total = a_fin_one * fins

    exposed_frac = max((fin_pitch - tf) / max(fin_pitch, 1e-9), 0.0)
    a_bare = n_tubes * (math.pi * do * l_tube) * exposed_frac
    ao = a_fin_total + a_bare
    arow = ao / max(rows, 1)

    fin_blockage = min(tf / max(fin_pitch, 1e-9), 0.95)
    tube_blockage = min(a_holes_one_fin / max(face_area, 1e-9), 0.5)
    amin = max(face_area * (1.0 - fin_blockage - tube_blockage), 1e-4)

    di = max(do - 2.0 * tf * 0.0, 1e-6)  # placeholder not used; true Di handled separately

    return {
        "face_area": face_area,
        "fin_pitch": fin_pitch,
        "fins": fins,
        "tubes_per_row": tubes_per_row,
        "N_tubes": n_tubes,
        "L_tube": l_tube,
        "depth": depth,
        "A_fin": a_fin_total,
        "A_bare": a_bare,
        "Ao": ao,
        "Arow": arow,
        "Amin": amin,
        "r_inner": do / 2.0,
        "r_outer": min(st, sl) / 2.0,
        "Di_placeholder": di,
    }


# ================= AIRSIDE =================
def mu_air_pas(t_c: float) -> float:
    t_k = K(t_c)
    return 1.716e-5 * (t_k / 273.15) ** 1.5 * (273.15 + 110.4) / (t_k + 110.4)


def k_air_w_mk(t_c: float) -> float:
    return 0.024 + (0.027 - 0.024) * (t_c / 40.0)


def airside_compact_htc_dp(
    mdot_air: float,
    face_w: float,
    face_h: float,
    full_depth: float,
    row_depth: float,
    fin_pitch: float,
    tf: float,
    t_air_c: float,
    w_air: float,
    do: float,
    n_tubes_face: int,
    sigma_free_area: Optional[float],
    fin_type: str,
    louver_angle_deg: float,
    louver_cuts_per_row: int,
    louver_gap_mm: float,
    h_mult_wavy: float,
    dp_mult_wavy: float,
) -> Tuple[float, float, Dict[str, float]]:
    rho = rho_moist_kg_m3(t_air_c, w_air)
    afr = face_w * face_h
    sigma_fin = max((fin_pitch - tf) / max(fin_pitch, 1e-9), 0.05)
    aopen_fin = afr * sigma_fin
    atube_block = float(n_tubes_face) * math.pi * (float(do) / 2.0) ** 2
    amin_geom = max(aopen_fin - atube_block, 1e-4)

    if sigma_free_area is not None:
        sigma_eff = max(min(float(sigma_free_area), 0.95), 0.20)
        amin = max(afr * sigma_eff, 1e-4)
    else:
        amin = amin_geom

    vmax = mdot_air / max(rho * amin, 1e-9)
    mu = mu_air_pas(t_air_c)
    k_air = k_air_w_mk(t_air_c)
    cp = cp_moist_j_per_kgk(t_air_c, w_air)
    pr = cp * mu / max(k_air, 1e-12)

    s_gap = max(fin_pitch - tf, 1e-6)
    dh = 2.0 * s_gap
    g_air = mdot_air / amin
    re_dh = g_air * dh / max(mu, 1e-12)

    if fin_type == "Wavy (no louvers)":
        if re_dh < 2300.0:
            nu = 7.54
            f_d = 96.0 / max(re_dh, 1e-9)
        else:
            nu = 0.023 * (re_dh ** 0.8) * (pr ** 0.4)
            f_d = 0.3164 * (re_dh ** -0.25)

        h_air = nu * k_air / max(dh, 1e-9) * h_mult_wavy
        dp_core = f_d * (full_depth / max(dh, 1e-9)) * (rho * vmax * vmax / 2.0) * dp_mult_wavy
        dp_minor = 1.5 * (rho * vmax * vmax / 2.0)
        dp_air = dp_core + dp_minor
        meta = {"model": "wavy", "Re_Dh": re_dh, "Dh": dh, "Vmax": vmax, "Amin": amin}
        return h_air, dp_air, meta

    theta = max(min(float(louver_angle_deg), 89.0), 1.0) * math.pi / 180.0
    h_l = max(float(louver_gap_mm), 0.2) / 1000.0
    p_l = max(row_depth / max(int(louver_cuts_per_row), 1), 1e-5)
    re_lp = rho * vmax * p_l / max(mu, 1e-12)

    if re_dh < 2300.0:
        nu0 = 7.54
        f_d0 = 96.0 / max(re_dh, 1e-9)
    else:
        nu0 = 0.023 * (re_dh ** 0.8) * (pr ** 0.4)
        f_d0 = 0.3164 * (re_dh ** -0.25)

    h0 = nu0 * k_air / max(dh, 1e-9) * h_mult_wavy
    re_ref = 500.0
    phi = max(0.2, min(re_lp / re_ref, 20.0))
    eh = max(1.05, min(1.0 + 1.6 * (phi ** 0.25) * (math.sin(theta) ** 0.5), 3.0))
    edp = max(1.10, min(1.0 + 2.8 * (phi ** 0.30) * (math.sin(theta) ** 0.70), 8.0))

    h_air = h0 * eh
    dp0_core = f_d0 * (full_depth / max(dh, 1e-9)) * (rho * vmax * vmax / 2.0) * dp_mult_wavy
    dp0_minor = 1.5 * (rho * vmax * vmax / 2.0)
    dp_air = (dp0_core + dp0_minor) * edp

    meta = {
        "model": "louver_enhanced",
        "Re_Dh": re_dh,
        "Re_Lp": re_lp,
        "Dh": dh,
        "Vmax": vmax,
        "Amin": amin,
        "eh": eh,
        "edp": edp,
        "p_l_mm": p_l * 1000.0,
        "h_l_mm": h_l * 1000.0,
    }
    return h_air, dp_air, meta


# ================= FIN EFFICIENCY =================
def schmidt_fin_efficiency(r_outer: float, r_inner: float, h_air: float, k_fin: float, t_fin: float) -> float:
    if r_outer <= r_inner or t_fin <= 0.0 or k_fin <= 0.0:
        return 1.0
    phi = (r_outer / r_inner - 1.0) * (1.0 + 0.35 * math.log(r_outer / r_inner))
    m = math.sqrt(2.0 * h_air / max(k_fin * t_fin, 1e-12))
    lc = r_outer - r_inner
    x = m * lc * phi
    if x < 0.01:
        return 1.0 - x * x / 3.0
    return math.tanh(x) / max(x, 1e-12)


# ================= PRESSURE DROP HELPERS =================
def f_churchill(re: float, e_over_d: float) -> float:
    re = max(re, 1e-9)
    if re < 2300.0:
        return 64.0 / re
    a = (2.457 * math.log(1.0 / (((7.0 / re) ** 0.9) + 0.27 * e_over_d))) ** 16
    b = (37530.0 / re) ** 16
    return 8.0 * (((8.0 / re) ** 12) + 1.0 / ((a + b) ** 1.5)) ** (1.0 / 12.0)



def smooth_h_gnielinski(re: float, pr: float, d_h: float, k_fluid: float, roughness: float = 1.5e-6) -> Tuple[float, float]:
    re = max(re, 1e-9)
    if re < 2300.0:
        nu = 3.66
        f = 64.0 / re
    else:
        f = f_churchill(re, roughness / max(d_h, 1e-12))
        numerator = (f / 8.0) * (re - 1000.0) * pr
        denominator = 1.0 + 12.7 * math.sqrt(f / 8.0) * (pr ** (2.0 / 3.0) - 1.0)
        nu = numerator / max(denominator, 1e-9)
        nu = max(4.36, min(nu, 350.0))
    return nu * k_fluid / max(d_h, 1e-12), f



def dp_darcy(mdot: float, rho: float, mu: float, d_h: float, length: float, roughness: float = 1.5e-6) -> Tuple[float, float, float, float, float]:
    area = math.pi * d_h * d_h / 4.0
    g = mdot / max(area, 1e-12)
    v = g / max(rho, 1e-9)
    re = rho * v * d_h / max(mu, 1e-12)
    f = f_churchill(re, roughness / max(d_h, 1e-12))
    dp = f * (length / max(d_h, 1e-12)) * 0.5 * rho * v * v
    return dp, re, f, v, g



def header_pressure_drop(mdot: float, rho: float, mu: float, d: float, length: float, roughness: float = 1.5e-6) -> Tuple[float, float, float]:
    area = math.pi * d * d / 4.0
    v = mdot / max(rho * area, 1e-12)
    re = rho * v * d / max(mu, 1e-12)
    if re < 2300.0:
        f = 64.0 / max(re, 1e-9)
    else:
        f = (-1.8 * math.log10(((roughness / d) / 3.7) ** 1.11 + 6.9 / max(re, 1e-9))) ** -2
    dp = f * (length / max(d, 1e-12)) * 0.5 * rho * v * v
    dp += 1.5 * 0.5 * rho * v * v
    return v, re, dp



def dp_muller_steinhagen(x: float, dp_lo: float, dp_vo: float, rho_l: float, rho_v: float, g: float, d_h: float) -> float:
    x = max(min(x, 0.999), 0.001)
    dp_tp = (1.0 - x) ** (1.0 / 3.0) * dp_lo + x ** 3 * dp_vo
    slip = (rho_l / rho_v) ** (1.0 / 3.0)
    alpha = (x * rho_v) / max(x * rho_v + slip * (1.0 - x) * rho_l, 1e-12)
    alpha = max(0.01, min(alpha, 0.99))
    dp_acc = g * g * (
        x * x / (rho_v * alpha)
        + (1.0 - x) ** 2 / (rho_l * (1.0 - alpha))
        - 1.0 / rho_l
    )
    return dp_tp + max(dp_acc, 0.0)


# ================= STEAM / WATER PROPERTIES =================
def steam_inlet_enthalpy(p_abs_pa: float, inlet_state: str, x_inlet: float, superheat_k: float) -> float:
    tsat = PropsSI("T", "P", p_abs_pa, "Q", 0, WATER)
    if inlet_state == "Wet steam":
        return PropsSI("H", "P", p_abs_pa, "Q", max(min(x_inlet, 1.0), 0.0), WATER)
    if inlet_state == "Saturated dry steam":
        return PropsSI("H", "P", p_abs_pa, "Q", 1, WATER)
    return PropsSI("H", "P", p_abs_pa, "T", tsat + max(superheat_k, 0.0), WATER)



def steam_min_enthalpy(p_abs_pa: float, max_subcool_k: float) -> float:
    if max_subcool_k <= 0.0:
        return PropsSI("H", "P", p_abs_pa, "Q", 0, WATER)
    tsat = PropsSI("T", "P", p_abs_pa, "Q", 0, WATER)
    t_out = max(tsat - max_subcool_k, 273.16 + 0.1)
    return PropsSI("H", "P", p_abs_pa, "T", t_out, WATER)



def steam_state_from_hp(h: float, p_abs_pa: float) -> Dict[str, float]:
    tsat = PropsSI("T", "P", p_abs_pa, "Q", 0, WATER)
    hf = PropsSI("H", "P", p_abs_pa, "Q", 0, WATER)
    hg = PropsSI("H", "P", p_abs_pa, "Q", 1, WATER)
    hfg = hg - hf
    p_crit = PropsSI("PCRIT", WATER)

    rho_l = PropsSI("D", "P", p_abs_pa, "Q", 0, WATER)
    rho_v = PropsSI("D", "P", p_abs_pa, "Q", 1, WATER)
    mu_l = PropsSI("V", "P", p_abs_pa, "Q", 0, WATER)
    mu_v = PropsSI("V", "P", p_abs_pa, "Q", 1, WATER)
    cp_l = PropsSI("C", "P", p_abs_pa, "Q", 0, WATER)
    cp_v = PropsSI("C", "P", p_abs_pa, "Q", 1, WATER)
    k_l = PropsSI("L", "P", p_abs_pa, "Q", 0, WATER)
    k_v = PropsSI("L", "P", p_abs_pa, "Q", 1, WATER)

    tol = 500.0
    if h > hg + tol:
        t = PropsSI("T", "P", p_abs_pa, "H", h, WATER)
        return {
            "phase": "Superheated steam",
            "T": t,
            "x": None,
            "rho": PropsSI("D", "P", p_abs_pa, "H", h, WATER),
            "mu": PropsSI("V", "P", p_abs_pa, "H", h, WATER),
            "cp": PropsSI("C", "P", p_abs_pa, "H", h, WATER),
            "k": PropsSI("L", "P", p_abs_pa, "H", h, WATER),
            "Tsat": tsat,
            "hf": hf,
            "hg": hg,
            "hfg": hfg,
            "rho_l": rho_l,
            "rho_v": rho_v,
            "mu_l": mu_l,
            "mu_v": mu_v,
            "cp_l": cp_l,
            "cp_v": cp_v,
            "k_l": k_l,
            "k_v": k_v,
            "Pcrit": p_crit,
        }
    if h < hf - tol:
        t = PropsSI("T", "P", p_abs_pa, "H", h, WATER)
        return {
            "phase": "Subcooled condensate",
            "T": t,
            "x": 0.0,
            "rho": PropsSI("D", "P", p_abs_pa, "H", h, WATER),
            "mu": PropsSI("V", "P", p_abs_pa, "H", h, WATER),
            "cp": PropsSI("C", "P", p_abs_pa, "H", h, WATER),
            "k": PropsSI("L", "P", p_abs_pa, "H", h, WATER),
            "Tsat": tsat,
            "hf": hf,
            "hg": hg,
            "hfg": hfg,
            "rho_l": rho_l,
            "rho_v": rho_v,
            "mu_l": mu_l,
            "mu_v": mu_v,
            "cp_l": cp_l,
            "cp_v": cp_v,
            "k_l": k_l,
            "k_v": k_v,
            "Pcrit": p_crit,
        }

    x = max(0.0, min(1.0, (h - hf) / max(hfg, 1e-9)))
    return {
        "phase": "Condensing two-phase",
        "T": tsat,
        "x": x,
        "rho": 1.0 / max(x / rho_v + (1.0 - x) / rho_l, 1e-12),
        "mu": None,
        "cp": None,
        "k": None,
        "Tsat": tsat,
        "hf": hf,
        "hg": hg,
        "hfg": hfg,
        "rho_l": rho_l,
        "rho_v": rho_v,
        "mu_l": mu_l,
        "mu_v": mu_v,
        "cp_l": cp_l,
        "cp_v": cp_v,
        "k_l": k_l,
        "k_v": k_v,
        "Pcrit": p_crit,
    }





def steam_row_side(
    mdot_steam_circuit: float,
    p_abs_pa: float,
    h_bulk: float,
    d_i: float,
    length_row_circuit: float,
    flow_area: float,
    h_mult_single_phase: float = 1.0,
    h_mult_condensing: float = 1.0,
    h_mult_subcooled: float = 1.0,
    dp_mult_single_phase: float = 1.0,
    dp_mult_condensing: float = 1.0,
) -> Dict[str, float]:
    state = steam_state_from_hp(h_bulk, p_abs_pa)
    area = max(flow_area, 1e-12)
    g = mdot_steam_circuit / area

    if state["phase"] == "Condensing two-phase":
        pr_l = state["cp_l"] * state["mu_l"] / max(state["k_l"], 1e-12)
        re_lo = g * d_i / max(state["mu_l"], 1e-12)
        h_lo, _ = smooth_h_gnielinski(re_lo, pr_l, d_i, state["k_l"])

        x = state["x"]
        pr_red = max(p_abs_pa / max(state["Pcrit"], 1e-9), 1e-6)
        enhancer = (1.0 - x) ** 0.8 + 3.8 * (x ** 0.76) * ((1.0 - x) ** 0.04) / (pr_red ** 0.38)
        h_i = max(1500.0, min(h_lo * max(enhancer, 1.0), 25000.0)) * max(h_mult_condensing, 1e-9)

        dp_lo, _, _, _, _ = dp_darcy(mdot_steam_circuit, state["rho_l"], state["mu_l"], d_i, length_row_circuit)
        dp_vo, _, _, _, _ = dp_darcy(mdot_steam_circuit, state["rho_v"], state["mu_v"], d_i, length_row_circuit)
        dp_tp = dp_muller_steinhagen(x, dp_lo, dp_vo, state["rho_l"], state["rho_v"], g, d_i) * max(dp_mult_condensing, 1e-9)
        v_mix = g / max(state["rho"], 1e-9)
        return {
            "phase": state["phase"],
            "T_ref_C": state["T"] - 273.15,
            "x": x,
            "h_i": h_i,
            "cp_ref": None,
            "dp_row_Pa": dp_tp,
            "v_ref": v_mix,
            "Re_ref": re_lo,
            "rho_ref": state["rho"],
        }

    rho = state["rho"]
    mu = state["mu"]
    cp = state["cp"]
    k_fluid = state["k"]
    v = mdot_steam_circuit / max(rho * area, 1e-12)
    re = rho * v * d_i / max(mu, 1e-12)
    pr = cp * mu / max(k_fluid, 1e-12)
    phase_h_mult = h_mult_subcooled if state["phase"] == "Subcooled condensate" else h_mult_single_phase
    h_i, _ = smooth_h_gnielinski(re, pr, d_i, k_fluid)
    h_i *= max(phase_h_mult, 1e-9)
    dp_row, _, _, _, _ = dp_darcy(mdot_steam_circuit, rho, mu, d_i, length_row_circuit)
    dp_row *= max(dp_mult_single_phase, 1e-9)
    return {
        "phase": state["phase"],
        "T_ref_C": state["T"] - 273.15,
        "x": state["x"],
        "h_i": h_i,
        "cp_ref": cp,
        "dp_row_Pa": dp_row,
        "v_ref": v,
        "Re_ref": re,
        "rho_ref": rho,
    }


# ================= SIMULATION =================
def eps_crossflow_unmixed(nt_u: float, c_r: float) -> float:
    c_r = max(min(c_r, 0.999999), 1e-9)
    return 1.0 - math.exp((math.exp(-c_r * nt_u) - 1.0) / c_r)





def simulate_steam_coil(
    face_w: float,
    face_h: float,
    rows: int,
    st: float,
    sl: float,
    do: float,
    tw: float,
    tf: float,
    fpi: float,
    fin_k: float,
    tube_k: float,
    circuits: int,
    vdot_m3_s: float,
    t_air_in_c: float,
    w_air_in: float,
    p_steam_abs_pa: float,
    inlet_state: str,
    x_inlet: float,
    superheat_k: float,
    mdot_steam_total: float,
    max_subcool_k: float,
    sigma_free_area: float,
    fin_type: str,
    louver_angle_deg: float,
    louver_gap_mm: float,
    louver_cuts_per_row: int,
    h_mult_wavy: float,
    dp_mult_wavy: float,
    rfo: float,
    rfi: float,
    header_in_diam_in: float,
    header_out_diam_in: float,
    header_length_m: float,
    target_t_air_out_c: Optional[float] = None,
    calibration: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    if not HAS_CP:
        raise RuntimeError("CoolProp is not installed. Add CoolProp to requirements.txt.")
    if mdot_steam_total <= 0.0:
        raise ValueError("Steam mass flow must be greater than zero.")

    calibration = calibration or {}
    air_cal = calibration.get("air_side", {})
    steam_cal = calibration.get("steam_side", {})
    air_h_cal = float(air_cal.get("h_correction", {}).get("multiplier", 1.0))
    air_dp_cal = float(air_cal.get("dp_correction", {}).get("multiplier", 1.0))
    steam_h_single = float(steam_cal.get("h_correction", {}).get("single_phase", 1.0))
    steam_h_cond = float(steam_cal.get("h_correction", {}).get("condensation", 1.0))
    steam_h_sub = float(steam_cal.get("h_correction", {}).get("subcooled_condensate", 1.0))
    steam_dp_single = float(steam_cal.get("dp_correction", {}).get("single_phase", 1.0))
    steam_dp_cond = float(steam_cal.get("dp_correction", {}).get("condensation_region", 1.0))
    header_dp_mult = float(steam_cal.get("dp_correction", {}).get("header", 1.0))

    geom = geometry_areas(face_w, face_h, rows, st, do, tf, fpi, sl)
    di = max(do - 2.0 * tw, 1e-6)
    flow_area = math.pi * di * di / 4.0
    ao_per_m = math.pi * do
    ai_per_m = math.pi * di
    ao_ai = ao_per_m / max(ai_per_m, 1e-12)
    r_wall_per_ao = math.log(do / max(di, 1e-12)) / (2.0 * math.pi * tube_k * ao_per_m)

    rho_air_in = rho_moist_kg_m3(t_air_in_c, w_air_in)
    mdot_air_total = rho_air_in * vdot_m3_s
    mdot_da = mdot_air_total / (1.0 + w_air_in)
    v_face = vdot_m3_s / max(geom["face_area"], 1e-12)

    eff_air_h_mult = h_mult_wavy * air_h_cal
    eff_air_dp_mult = dp_mult_wavy * air_dp_cal

    h_air_row, dp_air_total, air_meta = airside_compact_htc_dp(
        mdot_air=mdot_air_total,
        face_w=face_w,
        face_h=face_h,
        full_depth=geom["depth"],
        row_depth=st,
        fin_pitch=geom["fin_pitch"],
        tf=tf,
        t_air_c=t_air_in_c,
        w_air=w_air_in,
        do=do,
        n_tubes_face=geom["tubes_per_row"],
        sigma_free_area=sigma_free_area,
        fin_type=fin_type,
        louver_angle_deg=louver_angle_deg,
        louver_cuts_per_row=louver_cuts_per_row,
        louver_gap_mm=louver_gap_mm,
        h_mult_wavy=eff_air_h_mult,
        dp_mult_wavy=eff_air_dp_mult,
    )

    eta_f = schmidt_fin_efficiency(geom["r_outer"], geom["r_inner"], h_air_row, fin_k, tf)
    eta_o = 1.0 - (geom["A_fin"] / max(geom["Ao"], 1e-12)) * (1.0 - eta_f)

    def uo(h_i: float, h_o: float, eta_overall: float) -> float:
        inv_u = (
            1.0 / max(eta_overall * h_o, 1e-12)
            + rfo
            + ao_ai * (1.0 / max(h_i, 1e-12) + rfi)
            + r_wall_per_ao
        )
        return 1.0 / max(inv_u, 1e-12)

    mdot_steam_circuit = mdot_steam_total / max(circuits, 1)
    l_total_circuit = (geom["tubes_per_row"] * rows / max(circuits, 1)) * geom["L_tube"]
    l_row_circuit = l_total_circuit / max(rows, 1)

    h_steam = steam_inlet_enthalpy(p_steam_abs_pa, inlet_state, x_inlet, superheat_k)
    t_air = t_air_in_c
    h_air = h_moist_j_per_kg_da(t_air, w_air_in)
    p_steam = p_steam_abs_pa

    q_total = 0.0
    dp_steam_core = 0.0
    rows_needed_to_target = None
    row_logs = []

    for row in range(1, rows + 1):
        h_air_local, _, _ = airside_compact_htc_dp(
            mdot_air=mdot_air_total,
            face_w=face_w,
            face_h=face_h,
            full_depth=st,
            row_depth=st,
            fin_pitch=geom["fin_pitch"],
            tf=tf,
            t_air_c=t_air,
            w_air=w_air_in,
            do=do,
            n_tubes_face=geom["tubes_per_row"],
            sigma_free_area=sigma_free_area,
            fin_type=fin_type,
            louver_angle_deg=louver_angle_deg,
            louver_cuts_per_row=louver_cuts_per_row,
            louver_gap_mm=louver_gap_mm,
            h_mult_wavy=eff_air_h_mult,
            dp_mult_wavy=eff_air_dp_mult,
        )
        eta_f_local = schmidt_fin_efficiency(geom["r_outer"], geom["r_inner"], h_air_local, fin_k, tf)
        eta_o_local = 1.0 - (geom["A_fin"] / max(geom["Ao"], 1e-12)) * (1.0 - eta_f_local)

        steam_side = steam_row_side(
            mdot_steam_circuit=mdot_steam_circuit,
            p_abs_pa=p_steam,
            h_bulk=h_steam,
            d_i=di,
            length_row_circuit=l_row_circuit,
            flow_area=flow_area,
            h_mult_single_phase=steam_h_single,
            h_mult_condensing=steam_h_cond,
            h_mult_subcooled=steam_h_sub,
            dp_mult_single_phase=steam_dp_single,
            dp_mult_condensing=steam_dp_cond,
        )

        u_row = uo(steam_side["h_i"], h_air_local, eta_o_local)
        ua_row = u_row * geom["Arow"]
        c_air = mdot_da * cp_moist_j_per_kgk(t_air, w_air_in)
        delta_t_in = max(steam_side["T_ref_C"] - t_air, 0.0)

        if steam_side["phase"] == "Condensing two-phase":
            ntu = ua_row / max(c_air, 1e-12)
            eps = 1.0 - math.exp(-ntu)
            q_potential = eps * c_air * delta_t_in
        else:
            c_ref = mdot_steam_total * max(steam_side["cp_ref"], 1e-12)
            c_min = min(c_air, c_ref)
            c_max = max(c_air, c_ref)
            c_r = c_min / max(c_max, 1e-12)
            ntu = ua_row / max(c_min, 1e-12)
            eps = eps_crossflow_unmixed(ntu, c_r)
            q_potential = eps * c_min * delta_t_in

        h_min_allowed = steam_min_enthalpy(p_steam, max_subcool_k)
        q_available = max((h_steam - h_min_allowed) * mdot_steam_total, 0.0)
        q_row = min(q_potential, q_available)

        if q_row > 0.0:
            q_total += q_row
            h_air += q_row / max(mdot_da, 1e-12)
            t_air = t_from_h_w(h_air, w_air_in)
            h_steam -= q_row / max(mdot_steam_total, 1e-12)

        dp_steam_core += steam_side["dp_row_Pa"]
        p_steam = max(p_steam - steam_side["dp_row_Pa"], 5.0e4)
        rh_air = rh_from_t_w(t_air, w_air_in)

        if rows_needed_to_target is None and target_t_air_out_c is not None and t_air >= target_t_air_out_c - 1e-6:
            rows_needed_to_target = row

        row_logs.append(
            {
                "row": row,
                "Q_row_kW": q_row / 1000.0,
                "air_out_DB_C": t_air,
                "air_out_RH_pct": rh_air,
                "air_out_WB_C": wb_from_t_w(t_air, w_air_in),
                "steam_pressure_bar_abs": p_steam / 1e5,
                "steam_ref_T_C": steam_side["T_ref_C"],
                "steam_phase": steam_side["phase"],
                "steam_quality_x": steam_side["x"] if steam_side["x"] is not None else float("nan"),
                "tube_h_i_W_m2K": steam_side["h_i"],
                "air_h_o_W_m2K": h_air_local,
                "Uo_W_m2K": u_row,
                "tube_dp_row_Pa": steam_side["dp_row_Pa"],
                "tube_velocity_m_s": steam_side["v_ref"],
                "tube_Re": steam_side["Re_ref"],
            }
        )

    df_rows = pd.DataFrame(row_logs)

    state_in = steam_state_from_hp(steam_inlet_enthalpy(p_steam_abs_pa, inlet_state, x_inlet, superheat_k), p_steam_abs_pa)
    state_out = steam_state_from_hp(h_steam, max(p_steam, 5.0e4))

    inlet_hdr_phase = state_in if state_in["phase"] != "Condensing two-phase" else steam_state_from_hp(state_in["hg"], p_steam_abs_pa)
    outlet_hdr_phase = state_out if state_out["phase"] != "Condensing two-phase" else steam_state_from_hp(state_out["hf"], max(p_steam, 5.0e4))

    inlet_hdr_mu = inlet_hdr_phase["mu"] if inlet_hdr_phase["mu"] is not None else inlet_hdr_phase["mu_v"]
    inlet_hdr_rho = inlet_hdr_phase["rho"] if inlet_hdr_phase["rho"] is not None else inlet_hdr_phase["rho_v"]
    outlet_hdr_mu = outlet_hdr_phase["mu"] if outlet_hdr_phase["mu"] is not None else outlet_hdr_phase["mu_l"]
    outlet_hdr_rho = outlet_hdr_phase["rho"] if outlet_hdr_phase["rho"] is not None else outlet_hdr_phase["rho_l"]

    d_in_hdr = header_in_diam_in * INCH
    d_out_hdr = header_out_diam_in * INCH
    v_hdr_in, re_hdr_in, dp_hdr_in = header_pressure_drop(mdot_steam_total, inlet_hdr_rho, inlet_hdr_mu, d_in_hdr, header_length_m)
    v_hdr_out, re_hdr_out, dp_hdr_out = header_pressure_drop(mdot_steam_total, outlet_hdr_rho, outlet_hdr_mu, d_out_hdr, header_length_m)
    dp_hdr_in *= max(header_dp_mult, 1e-9)
    dp_hdr_out *= max(header_dp_mult, 1e-9)

    dp_steam_total = dp_steam_core + dp_hdr_in + dp_hdr_out
    t_out_c = t_air
    rh_out = rh_from_t_w(t_out_c, w_air_in)
    wb_out = wb_from_t_w(t_out_c, w_air_in)
    dp_out = dew_point_from_t_w(t_out_c, w_air_in)
    h_air_out = h_moist_j_per_kg_da(t_out_c, w_air_in)

    if state_out["phase"] == "Superheated steam":
        condensate_frac_out = 0.0
    elif state_out["phase"] == "Condensing two-phase":
        condensate_frac_out = 1.0 - max(min(state_out["x"], 1.0), 0.0)
    else:
        condensate_frac_out = 1.0

    if target_t_air_out_c is not None:
        h_air_target = h_moist_j_per_kg_da(target_t_air_out_c, w_air_in)
        q_required = max((h_air_target - h_moist_j_per_kg_da(t_air_in_c, w_air_in)) * mdot_da, 0.0)
    else:
        q_required = None

    summary = {
        "Air_in_DB_C": t_air_in_c,
        "Air_in_RH_pct": rh_from_t_w(t_air_in_c, w_air_in),
        "Air_in_WB_C": wb_from_t_w(t_air_in_c, w_air_in),
        "Air_in_DP_C": dew_point_from_t_w(t_air_in_c, w_air_in),
        "Air_out_DB_C": t_out_c,
        "Air_out_RH_pct": rh_out,
        "Air_out_WB_C": wb_out,
        "Air_out_DP_C": dp_out,
        "Humidity_ratio_in_kgkg": w_air_in,
        "Humidity_ratio_out_kgkg": w_air_in,
        "RH_drop_points": max(rh_from_t_w(t_air_in_c, w_air_in) - rh_out, 0.0),
        "Q_total_kW": q_total / 1000.0,
        "Q_sensible_kW": q_total / 1000.0,
        "Q_latent_kW": 0.0,
        "Steam_mdot_kg_s": mdot_steam_total,
        "Steam_mdot_kg_h": mdot_steam_total * 3600.0,
        "Steam_inlet_state": inlet_state,
        "Steam_inlet_pressure_bar_abs": p_steam_abs_pa / 1e5,
        "Steam_inlet_Tsat_C": state_in["Tsat"] - 273.15,
        "Steam_outlet_phase": state_out["phase"],
        "Steam_outlet_quality_x": state_out["x"] if state_out["x"] is not None else None,
        "Steam_outlet_pressure_bar_abs": p_steam / 1e5,
        "Steam_core_dp_kPa": dp_steam_core / 1000.0,
        "Steam_header_dp_kPa": (dp_hdr_in + dp_hdr_out) / 1000.0,
        "Steam_total_dp_kPa": dp_steam_total / 1000.0,
        "Air_dp_Pa": dp_air_total,
        "Rows_available": rows,
        "Rows_needed_to_target": rows_needed_to_target,
        "Target_met": bool(target_t_air_out_c is None or t_out_c >= target_t_air_out_c - 1e-6),
        "Condensate_fraction_out": condensate_frac_out,
        "Condensate_liquid_rate_kg_h": condensate_frac_out * mdot_steam_total * 3600.0,
        "Steam_energy_used_kJ_per_kg": (steam_inlet_enthalpy(p_steam_abs_pa, inlet_state, x_inlet, superheat_k) - h_steam) / 1000.0,
        "Face_velocity_m_s": v_face,
        "Air_volume_flow_m3_s": vdot_m3_s,
        "Air_volume_flow_m3_h": vdot_m3_s * 3600.0,
        "Air_mass_flow_kg_s": mdot_air_total,
        "Dry_air_mass_flow_kg_s": mdot_da,
        "Tubes_per_row": geom["tubes_per_row"],
        "Total_tubes": geom["N_tubes"],
        "Tube_length_per_row_m": geom["L_tube"],
        "Total_airside_area_m2": geom["Ao"],
        "Fin_area_m2": geom["A_fin"],
        "Bare_tube_area_m2": geom["A_bare"],
        "Amin_m2": geom["Amin"],
        "Tube_ID_mm": di / MM,
        "Tube_OD_mm": do / MM,
        "Circuits": circuits,
        "Inlet_header_velocity_m_s": v_hdr_in,
        "Outlet_header_velocity_m_s": v_hdr_out,
        "Inlet_header_Re": re_hdr_in,
        "Outlet_header_Re": re_hdr_out,
        "Air_model": air_meta["model"],
        "Calibration_air_h_multiplier": air_h_cal,
        "Calibration_air_dp_multiplier": air_dp_cal,
        "Calibration_steam_h_single_phase": steam_h_single,
        "Calibration_steam_h_condensing": steam_h_cond,
        "Calibration_steam_h_subcooled": steam_h_sub,
        "Calibration_steam_dp_single_phase": steam_dp_single,
        "Calibration_steam_dp_condensing": steam_dp_cond,
        "Calibration_header_dp_multiplier": header_dp_mult,
    }
    if q_required is not None:
        summary["Q_required_kW"] = q_required / 1000.0
    return df_rows, summary, geom


def solve_steam_flow_for_target(**kwargs) -> Tuple[float, pd.DataFrame, Dict[str, float], Dict[str, float], bool]:
    target_t_air_out_c = kwargs["target_t_air_out_c"]
    p_steam_abs_pa = kwargs["p_steam_abs_pa"]
    inlet_state = kwargs["inlet_state"]
    x_inlet = kwargs["x_inlet"]
    superheat_k = kwargs["superheat_k"]
    max_subcool_k = kwargs["max_subcool_k"]
    t_air_in_c = kwargs["t_air_in_c"]
    w_air_in = kwargs["w_air_in"]
    vdot_m3_s = kwargs["vdot_m3_s"]

    rho_air_in = rho_moist_kg_m3(t_air_in_c, w_air_in)
    mdot_air_total = rho_air_in * vdot_m3_s
    mdot_da = mdot_air_total / (1.0 + w_air_in)
    h_air_in = h_moist_j_per_kg_da(t_air_in_c, w_air_in)
    h_air_target = h_moist_j_per_kg_da(target_t_air_out_c, w_air_in)
    q_required = max((h_air_target - h_air_in) * mdot_da, 0.0)

    h_in = steam_inlet_enthalpy(p_steam_abs_pa, inlet_state, x_inlet, superheat_k)
    h_min = steam_min_enthalpy(p_steam_abs_pa, max_subcool_k)
    delta_h = max(h_in - h_min, 1.0e4)
    m_energy = q_required / delta_h if q_required > 0.0 else 1.0e-4

    low = 1.0e-5
    high = max(3.0 * m_energy, 0.01)
    best_df, best_summary, best_geom = None, None, None

    met = False
    for _ in range(20):
        df_h, summary_h, geom_h = simulate_steam_coil(mdot_steam_total=high, **kwargs)
        best_df, best_summary, best_geom = df_h, summary_h, geom_h
        if summary_h["Air_out_DB_C"] >= target_t_air_out_c - 1e-4:
            met = True
            break
        high *= 2.0
        if high > 10.0:
            break

    if not met:
        return high, best_df, best_summary, best_geom, False

    for _ in range(35):
        mid = 0.5 * (low + high)
        df_m, summary_m, geom_m = simulate_steam_coil(mdot_steam_total=mid, **kwargs)
        if summary_m["Air_out_DB_C"] >= target_t_air_out_c:
            high = mid
            best_df, best_summary, best_geom = df_m, summary_m, geom_m
        else:
            low = mid

    return high, best_df, best_summary, best_geom, True



# ================= STREAMLIT UI =================
def main() -> None:
    st.set_page_config(page_title="Steam Air Heater Coil Designer", page_icon="♨️", layout="wide")
    ensure_repo_dirs()
    init_session_state()
    bootstrap_settings = load_app_settings()

    if not authenticate_user(bootstrap_settings.get("app_title", "Steam Air Heater Coil Designer")):
        st.stop()

    username = st.session_state.get("auth_username", "")
    role = st.session_state.get("auth_role", "user").lower()
    is_admin = role == "admin"
    app_settings = load_app_settings()
    calibration = load_calibration_data()
    ensure_repo_dirs(app_settings)

    st.title(app_settings.get("app_title", "Steam Air Heater Coil Designer"))
    st.caption(app_settings.get("app_subtitle", "AHU reheat coil model for dry sensible heating after a cooling coil"))

    with st.expander("Cloud Secrets format for users", expanded=False):
        st.code(
            """[auth.users.admin]
password = \"AdminStrongPassword123\"
role = \"admin\"
enabled = true

[auth.users.engineer1]
password = \"Engineer1Password123\"
role = \"user\"
enabled = true

[auth.users.engineer2]
password = \"Engineer2Password123\"
role = \"user\"
enabled = true
""",
            language="toml",
        )
        st.caption("Paste the real TOML into Streamlit Community Cloud → App settings → Secrets. Do not store real passwords in GitHub.")

    with st.sidebar:
        st.subheader("User session")
        st.write(f"**User:** {username}")
        st.write(f"**Role:** {role}")
        if st.button("Logout", use_container_width=True):
            logout()
            st.rerun()

        st.markdown("---")
        st.header("Run mode")
        st.radio(
            "Select calculation mode",
            ["Rating: given steam flow", "Sizing: find steam flow for target leaving DB"],
            key="run_mode",
        )
        st.caption("Humidity ratio remains constant through the steam reheat coil in this model.")

        st.markdown("---")
        st.header("Design library")
        st.text_input("Design name for save", key="save_design_name", placeholder="ahu_reheat_case_01")
        save_col, dl_col = st.columns(2)
        with save_col:
            if st.button("Save current", use_container_width=True):
                record = build_design_record(username)
                path = save_design_record(username, app_settings, record, st.session_state.get("save_design_name") or "design")
                st.success(f"Saved {path.name}")
        with dl_col:
            st.download_button(
                "Download JSON",
                data=json.dumps(build_design_record(username), indent=2),
                file_name=f"{sanitize_filename(username)}_current_design.json",
                mime="application/json",
                use_container_width=True,
            )

        saved_files = list_design_files(username, app_settings)
        example_files = list_example_files()
        design_options: list[tuple[str, Path]] = []
        design_options.extend([(f"Saved: {p.name}", p) for p in saved_files])
        design_options.extend([(f"Example: {p.name}", p) for p in example_files])
        if design_options:
            labels = [label for label, _ in design_options]
            selected_label = st.selectbox("Available design JSON files", labels, key="selected_design_ref")
            selected_path = dict(design_options)[selected_label]
            load_col, del_col = st.columns(2)
            with load_col:
                if st.button("Load selected", use_container_width=True):
                    try:
                        payload = json.loads(selected_path.read_text(encoding="utf-8"))
                        apply_input_payload(extract_inputs_from_record(payload))
                        st.success(f"Loaded {selected_path.name}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not load design: {exc}")
            with del_col:
                can_delete = selected_label.startswith("Saved:")
                if st.button("Delete saved", use_container_width=True, disabled=not can_delete):
                    delete_design_file(selected_path, username, app_settings)
                    st.success(f"Deleted {selected_path.name}")
                    st.rerun()
        else:
            st.info("No saved designs yet. Save one or load an example.")

        if app_settings.get("allow_user_json_upload", True):
            uploaded_design = st.file_uploader("Upload design JSON", type=["json"])
            if uploaded_design is not None and st.button("Load uploaded JSON", use_container_width=True):
                try:
                    payload = json.loads(uploaded_design.getvalue().decode("utf-8"))
                    apply_input_payload(extract_inputs_from_record(payload))
                    st.success("Uploaded design loaded into the session.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not load uploaded JSON: {exc}")

        if is_admin:
            st.markdown("---")
            st.caption("Admin users can edit calibration and app settings in the Admin tab.")

    mat_k = {
        "Copper": 380.0,
        "Aluminum": 205.0,
        "Steel": 50.0,
        "CuNi 90/10": 29.0,
    }

    tabs = st.tabs(["Inputs", "Results"] + (["Admin"] if is_admin else []))
    tab_inputs = tabs[0]
    tab_results = tabs[1]
    tab_admin = tabs[2] if is_admin else None

    with tab_inputs:
        st.subheader("Coil geometry")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.number_input("Face width (m)", min_value=0.2, max_value=4.0, step=0.01, key="face_w")
        with c2:
            st.number_input("Face height (m)", min_value=0.2, max_value=4.0, step=0.01, key="face_h")
        with c3:
            st.number_input("Row pitch St (mm)", min_value=10.0, max_value=60.0, step=0.01, key="st_mm")
        with c4:
            st.number_input("Longitudinal pitch Sl (mm)", min_value=10.0, max_value=60.0, step=0.01, key="sl_mm")

        c5, c6, c7, c8 = st.columns(4)
        with c5:
            st.number_input("Number of rows", min_value=1, max_value=20, step=1, key="rows")
        with c6:
            st.number_input("Tube OD (mm)", min_value=5.0, max_value=25.0, step=0.01, key="do_mm")
        with c7:
            st.number_input("Tube wall thickness (mm)", min_value=0.15, max_value=2.0, step=0.01, key="tw_mm")
        with c8:
            st.number_input("FPI (1/in)", min_value=4.0, max_value=24.0, step=0.5, key="fpi")

        c9, c10, c11, c12 = st.columns(4)
        with c9:
            st.number_input("Fin thickness (mm)", min_value=0.06, max_value=0.30, step=0.01, key="tf_mm")
        with c10:
            st.selectbox("Fin material", ["Aluminum", "Copper", "Steel"], key="fin_mat")
        with c11:
            st.selectbox("Tube material", ["Copper", "Aluminum", "Steel", "CuNi 90/10"], key="tube_mat")
        with c12:
            st.number_input("Circuits", min_value=1, max_value=64, step=1, key="circuits")

        st.slider("Air free-flow area ratio sigma", min_value=0.20, max_value=0.95, step=0.01, key="sigma_free")

        with st.expander("Air-side / fin options", expanded=False):
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                st.selectbox("Fin type", ["Wavy (no louvers)", "Wavy + Louvers"], key="fin_type")
            with a2:
                st.number_input("Louver angle (deg)", min_value=0.0, max_value=60.0, step=0.1, key="louver_angle_deg")
            with a3:
                st.number_input("Louver gap (mm)", min_value=0.5, max_value=5.0, step=0.1, key="louver_gap_mm")
            with a4:
                st.number_input("Louvers per row", min_value=1, max_value=40, step=1, key="louver_cuts_per_row")

            a5, a6, a7, a8 = st.columns(4)
            with a5:
                st.number_input("Air h multiplier", min_value=0.5, max_value=3.0, step=0.01, key="h_mult_wavy")
            with a6:
                st.number_input("Air dp multiplier", min_value=0.5, max_value=5.0, step=0.01, key="dp_mult_wavy")
            with a7:
                st.number_input("Air-side fouling (m2 K/W)", min_value=0.0, max_value=0.002, step=0.00005, format="%.5f", key="rfo")
            with a8:
                st.number_input("Tube-side fouling (m2 K/W)", min_value=0.0, max_value=0.002, step=0.00005, format="%.5f", key="rfi")

        st.subheader("Air side")
        st.radio("Air flow input mode", ["Face velocity (m/s)", "Volume flow (m3/h)"], horizontal=True, key="flow_mode")
        if st.session_state["flow_mode"] == "Face velocity (m/s)":
            st.number_input("Face velocity (m/s)", min_value=0.2, max_value=6.0, step=0.1, key="v_face_input")
            vdot = st.session_state["v_face_input"] * st.session_state["face_w"] * st.session_state["face_h"]
            vdot_m3h = vdot * 3600.0
        else:
            st.number_input("Air volume flow (m3/h)", min_value=500.0, max_value=100000.0, step=10.0, key="vdot_m3h")
            vdot = st.session_state["vdot_m3h"] / 3600.0
            vdot_m3h = st.session_state["vdot_m3h"]
        v_face_input = vdot / max(st.session_state["face_w"] * st.session_state["face_h"], 1e-9)
        st.info(f"Face velocity = {v_face_input:.2f} m/s, volume flow = {vdot:.3f} m3/s ({vdot_m3h:.0f} m3/h)")

        st.radio("Air inlet input method", ["Dry Bulb + RH", "Dry Bulb + Wet Bulb"], horizontal=True, key="air_mode")
        d1, d2, d3 = st.columns(3)
        with d1:
            st.number_input("Air inlet DB (C)", min_value=0.0, max_value=55.0, step=0.1, key="t_air_in_c")
        with d2:
            if st.session_state["air_mode"] == "Dry Bulb + RH":
                st.number_input("Air inlet RH (%)", min_value=5.0, max_value=100.0, step=0.5, key="rh_air_in")
                w_air_in = w_from_t_rh(st.session_state["t_air_in_c"], st.session_state["rh_air_in"])
                wb_air_in = wb_from_t_w(st.session_state["t_air_in_c"], w_air_in)
                st.caption(f"Calculated inlet WB = {wb_air_in:.2f} C")
            else:
                max_wb = float(st.session_state["t_air_in_c"])
                if st.session_state["wb_air_in"] > max_wb:
                    st.session_state["wb_air_in"] = max_wb
                st.number_input("Air inlet WB (C)", min_value=-10.0, max_value=max_wb, step=0.1, key="wb_air_in")
                wb_air_in = st.session_state["wb_air_in"]
                w_air_in = w_from_t_wb(st.session_state["t_air_in_c"], wb_air_in)
                rh_air_in = rh_from_t_w(st.session_state["t_air_in_c"], w_air_in)
                st.caption(f"Calculated inlet RH = {rh_air_in:.2f} %")
        with d3:
            dp_air_in = dew_point_from_t_w(st.session_state["t_air_in_c"], w_air_in)
            st.metric("Air inlet dew point", f"{dp_air_in:.2f} C")

        if st.session_state["run_mode"] == "Sizing: find steam flow for target leaving DB":
            st.number_input(
                "Target leaving DB after reheat (C)",
                min_value=float(st.session_state["t_air_in_c"]),
                max_value=60.0,
                step=0.1,
                key="target_t_air_out_c",
            )
            target_rh = rh_from_t_w(st.session_state["target_t_air_out_c"], w_air_in)
            st.caption(f"If achieved, leaving RH will be approximately {target_rh:.1f} % at constant humidity ratio.")
        else:
            st.number_input(
                "Reference target leaving DB for comparison (C)",
                min_value=float(st.session_state["t_air_in_c"]),
                max_value=60.0,
                step=0.1,
                key="target_t_air_out_c",
            )

        st.subheader("Steam side")
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.selectbox("Steam pressure basis", ["bar(g)", "bar(abs)"], key="pressure_basis")
        with s2:
            st.number_input("Steam pressure", min_value=0.1, max_value=20.0, step=0.1, key="p_steam_input")
        with s3:
            st.selectbox("Steam inlet state", ["Saturated dry steam", "Wet steam", "Superheated steam"], key="inlet_state")
        with s4:
            st.number_input("Allowable outlet condensate subcooling (K)", min_value=0.0, max_value=30.0, step=0.5, key="max_subcool_k")

        s5, s6, s7, s8 = st.columns(4)
        with s5:
            st.number_input("Wet steam inlet quality x", min_value=0.50, max_value=1.00, step=0.01, key="x_inlet")
        with s6:
            st.number_input("Steam superheat (K)", min_value=0.0, max_value=80.0, step=0.5, key="superheat_k")
        with s7:
            if st.session_state["run_mode"] == "Rating: given steam flow":
                st.number_input("Steam mass flow (kg/h)", min_value=1.0, max_value=50000.0, step=1.0, key="mdot_steam_kg_h")
            else:
                st.metric("Steam mass flow", "Solved by app")
        with s8:
            st.number_input("Header length (m)", min_value=0.10, max_value=20.0, step=0.10, key="header_length_m")

        s9, s10 = st.columns(2)
        with s9:
            st.selectbox("Inlet header OD (inch)", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0], key="header_in_diam_in")
        with s10:
            st.selectbox("Outlet header OD (inch)", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0], key="header_out_diam_in")

        do = st.session_state["do_mm"] * MM
        tw = st.session_state["tw_mm"] * MM
        tf = st.session_state["tf_mm"] * MM
        st_pitch = st.session_state["st_mm"] * MM
        sl_pitch = st.session_state["sl_mm"] * MM
        fin_k = mat_k[st.session_state["fin_mat"]]
        tube_k = mat_k[st.session_state["tube_mat"]]
        p_steam_abs_pa = (st.session_state["p_steam_input"] + 1.01325) * 1e5 if st.session_state["pressure_basis"] == "bar(g)" else st.session_state["p_steam_input"] * 1e5

        if HAS_CP:
            tsat_supply_c = PropsSI("T", "P", p_steam_abs_pa, "Q", 0, WATER) - 273.15
            st.info(f"Steam saturation temperature at supply pressure = {tsat_supply_c:.2f} C")
        else:
            st.warning("CoolProp not available in this environment, so steam saturation temperature preview is disabled.")

        run_clicked = st.button("Run steam coil analysis", type="primary", use_container_width=True)
        st.caption("Results below stay attached to your own session. Other logged-in users get separate session state.")

    if run_clicked:
        try:
            common_kwargs = dict(
                face_w=st.session_state["face_w"],
                face_h=st.session_state["face_h"],
                rows=int(st.session_state["rows"]),
                st=st_pitch,
                sl=sl_pitch,
                do=do,
                tw=tw,
                tf=tf,
                fpi=float(st.session_state["fpi"]),
                fin_k=fin_k,
                tube_k=tube_k,
                circuits=int(st.session_state["circuits"]),
                vdot_m3_s=vdot,
                t_air_in_c=st.session_state["t_air_in_c"],
                w_air_in=w_air_in,
                p_steam_abs_pa=p_steam_abs_pa,
                inlet_state=st.session_state["inlet_state"],
                x_inlet=st.session_state["x_inlet"],
                superheat_k=st.session_state["superheat_k"],
                max_subcool_k=st.session_state["max_subcool_k"],
                sigma_free_area=st.session_state["sigma_free"],
                fin_type=st.session_state["fin_type"],
                louver_angle_deg=st.session_state["louver_angle_deg"],
                louver_gap_mm=st.session_state["louver_gap_mm"],
                louver_cuts_per_row=int(st.session_state["louver_cuts_per_row"]),
                h_mult_wavy=st.session_state["h_mult_wavy"],
                dp_mult_wavy=st.session_state["dp_mult_wavy"],
                rfo=st.session_state["rfo"],
                rfi=st.session_state["rfi"],
                header_in_diam_in=st.session_state["header_in_diam_in"],
                header_out_diam_in=st.session_state["header_out_diam_in"],
                header_length_m=st.session_state["header_length_m"],
                target_t_air_out_c=st.session_state["target_t_air_out_c"],
                calibration=calibration,
            )

            if st.session_state["run_mode"] == "Rating: given steam flow":
                df_rows, summary, geom = simulate_steam_coil(mdot_steam_total=st.session_state["mdot_steam_kg_h"] / 3600.0, **common_kwargs)
                solved_mdot_kg_h = summary["Steam_mdot_kg_h"]
                solved = True
            else:
                solved_mdot_kg_s, df_rows, summary, geom, solved = solve_steam_flow_for_target(**common_kwargs)
                solved_mdot_kg_h = solved_mdot_kg_s * 3600.0
                summary["Steam_mdot_kg_s"] = solved_mdot_kg_s
                summary["Steam_mdot_kg_h"] = solved_mdot_kg_h

            st.session_state["current_summary"] = summary
            st.session_state["current_rows_df"] = df_rows
            st.session_state["current_rows_csv"] = df_rows.to_csv(index=False).encode("utf-8")
            st.session_state["current_design_record"] = build_design_record(username)
            st.session_state["current_design_record"]["last_summary"] = summary
            st.session_state["current_solved"] = solved
            st.success("Analysis complete")
        except Exception as exc:
            st.session_state["current_summary"] = None
            st.session_state["current_rows_df"] = None
            st.session_state["current_rows_csv"] = None
            st.error(f"Simulation error: {exc}")
            st.code(traceback.format_exc())

    with tab_results:
        summary = st.session_state.get("current_summary")
        df_rows = st.session_state.get("current_rows_df")
        csv_data = st.session_state.get("current_rows_csv")
        if not summary or df_rows is None:
            st.info("Enter inputs and run the analysis. Results shown here belong only to your current user session.")
            with st.expander("Model assumptions", expanded=True):
                st.markdown(
                    """
                    1. Air is reheated sensibly only, so humidity ratio stays constant through the steam coil.
                    2. Steam side is modeled row by row and can pass through superheated, condensing, and subcooled regions.
                    3. The internal condensing heat transfer uses a Shah-style enhancement over liquid-only Gnielinski heat transfer.
                    4. Air-side geometry, fin efficiency, free-area treatment, and louver options follow the same style as your DX coil app.
                    5. Master calibration multipliers are read from `calibration/calibration_data.json` and only admin can edit them.
                    6. Per-user saved designs are written under the saved-designs root defined in app settings.
                    """
                )
        else:
            solved_mdot_kg_h = summary.get("Steam_mdot_kg_h", 0.0)
            target_t_air_out_c = st.session_state.get("target_t_air_out_c")
            solved = bool(st.session_state.get("current_solved", True))

            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric("Duty", f"{summary['Q_total_kW']:.2f} kW")
                if "Q_required_kW" in summary:
                    st.metric("Required duty", f"{summary['Q_required_kW']:.2f} kW")
            with m2:
                st.metric("Leaving air DB", f"{summary['Air_out_DB_C']:.2f} C")
                st.metric("Leaving air RH", f"{summary['Air_out_RH_pct']:.1f} %")
            with m3:
                st.metric("Steam flow", f"{solved_mdot_kg_h:.1f} kg/h")
                st.metric("Condensate liquid rate", f"{summary['Condensate_liquid_rate_kg_h']:.1f} kg/h")
            with m4:
                st.metric("Steam total dp", f"{summary['Steam_total_dp_kPa']:.2f} kPa")
                st.metric("Air dp", f"{summary['Air_dp_Pa']:.1f} Pa")

            m5, m6, m7, m8 = st.columns(4)
            with m5:
                st.metric("Inlet RH", f"{summary['Air_in_RH_pct']:.1f} %")
            with m6:
                st.metric("RH reduction", f"{summary['RH_drop_points']:.1f} points")
            with m7:
                st.metric("Rows available", f"{summary['Rows_available']}")
            with m8:
                txt = "Not reached" if summary["Rows_needed_to_target"] is None else str(summary["Rows_needed_to_target"])
                st.metric("Rows to target", txt)

            if st.session_state["run_mode"] == "Sizing: find steam flow for target leaving DB":
                if solved:
                    st.success(f"Target leaving DB of {target_t_air_out_c:.2f} C can be met. Required steam flow is about {solved_mdot_kg_h:.1f} kg/h.")
                else:
                    st.warning(f"Even at high steam flow, the coil did not reach the target leaving DB of {target_t_air_out_c:.2f} C. The current coil appears UA-limited.")
            else:
                if summary["Air_out_DB_C"] >= target_t_air_out_c - 1e-6:
                    st.success(f"Reference target leaving DB of {target_t_air_out_c:.2f} C is achieved in rating mode.")
                else:
                    st.warning(f"Reference target leaving DB of {target_t_air_out_c:.2f} C is not achieved in rating mode.")

            st.subheader("Air summary")
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                st.metric("Leaving WB", f"{summary['Air_out_WB_C']:.2f} C")
            with a2:
                st.metric("Leaving dew point", f"{summary['Air_out_DP_C']:.2f} C")
            with a3:
                st.metric("Humidity ratio in", f"{summary['Humidity_ratio_in_kgkg']:.5f} kg/kg")
            with a4:
                st.metric("Humidity ratio out", f"{summary['Humidity_ratio_out_kgkg']:.5f} kg/kg")

            st.subheader("Steam / condensate summary")
            b1, b2, b3, b4 = st.columns(4)
            with b1:
                st.metric("Steam supply pressure", f"{summary['Steam_inlet_pressure_bar_abs']:.3f} bar abs")
            with b2:
                st.metric("Supply saturation temperature", f"{summary['Steam_inlet_Tsat_C']:.2f} C")
            with b3:
                st.metric("Steam outlet phase", summary['Steam_outlet_phase'])
            with b4:
                outlet_q = summary['Steam_outlet_quality_x']
                st.metric("Steam outlet quality x", "-" if outlet_q is None else f"{outlet_q:.3f}")

            st.subheader("Geometry summary")
            g1, g2, g3, g4 = st.columns(4)
            with g1:
                st.metric("Tubes per row", f"{summary['Tubes_per_row']}")
            with g2:
                st.metric("Total tubes", f"{summary['Total_tubes']}")
            with g3:
                st.metric("Total air-side area", f"{summary['Total_airside_area_m2']:.2f} m2")
            with g4:
                st.metric("Amin", f"{summary['Amin_m2']:.4f} m2")

            st.subheader("Calibration applied in this run")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Air h cal", f"{summary['Calibration_air_h_multiplier']:.3f}")
            with c2:
                st.metric("Air dp cal", f"{summary['Calibration_air_dp_multiplier']:.3f}")
            with c3:
                st.metric("Steam h condensing", f"{summary['Calibration_steam_h_condensing']:.3f}")
            with c4:
                st.metric("Header dp cal", f"{summary['Calibration_header_dp_multiplier']:.3f}")

            st.subheader("Row-by-row results")
            st.dataframe(df_rows.round(4), use_container_width=True, height=420)

            st.subheader("Downloads")
            st.download_button(
                "Download row results CSV",
                data=csv_data,
                file_name=f"{sanitize_filename(username)}_steam_coil_rows_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            st.download_button(
                "Download summary JSON",
                data=json.dumps(summary, indent=2),
                file_name=f"{sanitize_filename(username)}_steam_coil_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )

    if is_admin and tab_admin is not None:
        with tab_admin:
            st.subheader("Admin controls")
            st.info("User accounts are managed in Streamlit Cloud Secrets. This page controls only master calibration and app settings.")
            cal_tab, set_tab = st.tabs(["Calibration", "Settings"])
            with cal_tab:
                st.markdown("### Master calibration multipliers")
                c1, c2, c3 = st.columns(3)
                with c1:
                    air_h_cal = st.number_input("Air h multiplier", min_value=0.5, max_value=2.0, value=float(calibration['air_side']['h_correction']['multiplier']), step=0.01)
                    air_dp_cal = st.number_input("Air dp multiplier", min_value=0.5, max_value=3.0, value=float(calibration['air_side']['dp_correction']['multiplier']), step=0.01)
                with c2:
                    steam_h_single = st.number_input("Steam h single phase", min_value=0.5, max_value=2.0, value=float(calibration['steam_side']['h_correction']['single_phase']), step=0.01)
                    steam_h_cond = st.number_input("Steam h condensing", min_value=0.5, max_value=2.0, value=float(calibration['steam_side']['h_correction']['condensation']), step=0.01)
                    steam_h_sub = st.number_input("Steam h subcooled", min_value=0.5, max_value=2.0, value=float(calibration['steam_side']['h_correction']['subcooled_condensate']), step=0.01)
                with c3:
                    steam_dp_single = st.number_input("Steam dp single phase", min_value=0.5, max_value=3.0, value=float(calibration['steam_side']['dp_correction']['single_phase']), step=0.01)
                    steam_dp_cond = st.number_input("Steam dp condensing", min_value=0.5, max_value=3.0, value=float(calibration['steam_side']['dp_correction']['condensation_region']), step=0.01)
                    steam_dp_header = st.number_input("Header dp multiplier", min_value=0.5, max_value=3.0, value=float(calibration['steam_side']['dp_correction']['header']), step=0.01)
                cal_notes = st.text_area("Calibration notes", value="\n".join(calibration.get('calibration_notes', [])), height=120)
                if st.button("Save calibration", type="primary"):
                    calibration['air_side']['h_correction']['multiplier'] = air_h_cal
                    calibration['air_side']['dp_correction']['multiplier'] = air_dp_cal
                    calibration['steam_side']['h_correction']['single_phase'] = steam_h_single
                    calibration['steam_side']['h_correction']['condensation'] = steam_h_cond
                    calibration['steam_side']['h_correction']['subcooled_condensate'] = steam_h_sub
                    calibration['steam_side']['dp_correction']['single_phase'] = steam_dp_single
                    calibration['steam_side']['dp_correction']['condensation_region'] = steam_dp_cond
                    calibration['steam_side']['dp_correction']['header'] = steam_dp_header
                    calibration['validation_stats']['last_calibrated'] = datetime.now().isoformat(timespec='seconds')
                    calibration['calibration_notes'] = [line for line in cal_notes.splitlines() if line.strip()]
                    save_calibration_data(calibration, make_backup=True)
                    st.success("Calibration saved. A backup copy was written to calibration/backup.")
                    st.rerun()
                st.download_button(
                    "Download calibration JSON",
                    data=json.dumps(calibration, indent=2),
                    file_name=f"steam_coil_calibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                )
            with set_tab:
                st.markdown("### App settings")
                app_title = st.text_input("App title", value=app_settings.get('app_title', DEFAULT_APP_SETTINGS['app_title']))
                app_subtitle = st.text_input("App subtitle", value=app_settings.get('app_subtitle', DEFAULT_APP_SETTINGS['app_subtitle']))
                saved_root = st.text_input("Saved designs root folder", value=app_settings.get('saved_designs_root', DEFAULT_APP_SETTINGS['saved_designs_root']))
                allow_upload = st.checkbox("Allow users to upload design JSON", value=bool(app_settings.get('allow_user_json_upload', True)))
                max_saved = st.number_input("Max saved designs per user", min_value=5, max_value=1000, value=int(app_settings.get('max_saved_designs_per_user', 200)), step=5)
                if st.button("Save app settings"):
                    new_settings = {
                        'app_title': app_title,
                        'app_subtitle': app_subtitle,
                        'saved_designs_root': sanitize_filename(saved_root) or 'saved_designs',
                        'allow_user_json_upload': bool(allow_upload),
                        'max_saved_designs_per_user': int(max_saved),
                    }
                    save_app_settings(new_settings, make_backup=True)
                    st.success("App settings saved. A backup copy was written to calibration/backup.")
                    st.rerun()
                st.download_button(
                    "Download app settings JSON",
                    data=json.dumps(app_settings, indent=2),
                    file_name=f"steam_coil_app_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                )


if __name__ == "__main__":
    main()
