import math
import json
import traceback
from datetime import datetime
from typing import Dict, Tuple, Optional

import pandas as pd
import streamlit as st

try:
    from CoolProp.CoolProp import PropsSI
    HAS_CP = True
except Exception:
    HAS_CP = False

# ================= CONSTANTS =================
P_ATM = 101325.0
R_DA = 287.055
CP_DA = 1006.0
CP_V = 1860.0
H_LV0 = 2501000.0
INCH = 0.0254
MM = 1e-3
GRAVITY = 9.80665
WATER = "Water"


def K(t_c: float) -> float:
    return t_c + 273.15


# ================= PASSWORD =================
def check_password() -> bool:
    try:
        required_password = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        required_password = ""

    if not required_password:
        return True

    if st.session_state.get("password_correct", False):
        return True

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("Steam Air Heater Coil Designer")
        st.caption("Password protected app")
        password = st.text_input("Enter password", type="password")
        if st.button("Login", use_container_width=True):
            if password == required_password:
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


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
        h_i = max(1500.0, min(h_lo * max(enhancer, 1.0), 25000.0))

        dp_lo, _, _, _, _ = dp_darcy(mdot_steam_circuit, state["rho_l"], state["mu_l"], d_i, length_row_circuit)
        dp_vo, _, _, _, _ = dp_darcy(mdot_steam_circuit, state["rho_v"], state["mu_v"], d_i, length_row_circuit)
        dp_tp = dp_muller_steinhagen(x, dp_lo, dp_vo, state["rho_l"], state["rho_v"], g, d_i)
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
    h_i, _ = smooth_h_gnielinski(re, pr, d_i, k_fluid)
    dp_row, _, _, _, _ = dp_darcy(mdot_steam_circuit, rho, mu, d_i, length_row_circuit)
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
) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    if not HAS_CP:
        raise RuntimeError("CoolProp is not installed. Add CoolProp to requirements.txt.")
    if mdot_steam_total <= 0.0:
        raise ValueError("Steam mass flow must be greater than zero.")

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

    h_air_row, dp_air_total, air_meta = airside_compact_htc_dp(
        mdot_air_total=mdot_air_total,
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
        h_mult_wavy=h_mult_wavy,
        dp_mult_wavy=dp_mult_wavy,
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
            mdot_air_total=mdot_air_total,
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
            h_mult_wavy=h_mult_wavy,
            dp_mult_wavy=dp_mult_wavy,
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
        q_required = max((h_air_target - h_air_out + q_total / max(mdot_da, 1e-12)) * mdot_da, 0.0)
        # Equivalent clearer form:
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
    }
    if q_required is not None:
        summary["Q_required_kW"] = q_required / 1000.0
        summary["Duty_margin_kW"] = summary["Q_total_kW"] - summary["Q_required_kW"]

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
    st.set_page_config(page_title="Steam Air Heater Coil Designer", page_icon="Steam", layout="wide")

    if not check_password():
        st.stop()

    st.title("Steam Air Heater Coil Designer")
    st.caption("AHU reheat coil model for dry sensible heating after a cooling coil")

    with st.sidebar:
        st.header("Run mode")
        run_mode = st.radio(
            "Select calculation mode",
            ["Rating: given steam flow", "Sizing: find steam flow for target leaving DB"],
            index=0,
        )
        st.markdown("---")
        st.markdown(
            "This app keeps humidity ratio constant across the steam reheat coil, which is the usual AHU post-cooling reheat case."
        )

    mat_k = {
        "Copper": 380.0,
        "Aluminum": 205.0,
        "Steel": 50.0,
        "CuNi 90/10": 29.0,
    }

    tab1, tab2 = st.tabs(["Inputs", "Results"])

    with tab1:
        st.subheader("Coil geometry")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            face_w = st.number_input("Face width (m)", min_value=0.2, max_value=4.0, value=1.2, step=0.01)
        with c2:
            face_h = st.number_input("Face height (m)", min_value=0.2, max_value=4.0, value=0.85, step=0.01)
        with c3:
            st_mm = st.number_input("Row pitch St (mm)", min_value=10.0, max_value=60.0, value=22.0, step=0.01)
        with c4:
            sl_mm = st.number_input("Longitudinal pitch Sl (mm)", min_value=10.0, max_value=60.0, value=25.4, step=0.01)

        c5, c6, c7, c8 = st.columns(4)
        with c5:
            rows = st.number_input("Number of rows", min_value=1, max_value=20, value=2, step=1)
        with c6:
            do_mm = st.number_input("Tube OD (mm)", min_value=5.0, max_value=25.0, value=9.53, step=0.01)
        with c7:
            tw_mm = st.number_input("Tube wall thickness (mm)", min_value=0.15, max_value=2.0, value=0.30, step=0.01)
        with c8:
            fpi = st.number_input("FPI (1/in)", min_value=4.0, max_value=24.0, value=10.0, step=0.5)

        c9, c10, c11, c12 = st.columns(4)
        with c9:
            tf_mm = st.number_input("Fin thickness (mm)", min_value=0.06, max_value=0.30, value=0.12, step=0.01)
        with c10:
            fin_mat = st.selectbox("Fin material", ["Aluminum", "Copper", "Steel"], index=0)
        with c11:
            tube_mat = st.selectbox("Tube material", ["Copper", "Aluminum", "Steel", "CuNi 90/10"], index=0)
        with c12:
            circuits = st.number_input("Circuits", min_value=1, max_value=64, value=20, step=1)

        sigma_free = st.slider("Air free-flow area ratio sigma", min_value=0.20, max_value=0.95, value=0.55, step=0.01)

        with st.expander("Air-side / fin options", expanded=False):
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                fin_type = st.selectbox("Fin type", ["Wavy (no louvers)", "Wavy + Louvers"], index=0)
            with a2:
                louver_angle_deg = st.number_input("Louver angle (deg)", min_value=0.0, max_value=60.0, value=40.0, step=0.1)
            with a3:
                louver_gap_mm = st.number_input("Louver gap (mm)", min_value=0.5, max_value=5.0, value=2.0, step=0.1)
            with a4:
                louver_cuts_per_row = st.number_input("Louvers per row", min_value=1, max_value=40, value=8, step=1)

            a5, a6, a7, a8 = st.columns(4)
            with a5:
                h_mult_wavy = st.number_input("Air h multiplier", min_value=0.5, max_value=3.0, value=1.15, step=0.01)
            with a6:
                dp_mult_wavy = st.number_input("Air dp multiplier", min_value=0.5, max_value=5.0, value=1.20, step=0.01)
            with a7:
                rfo = st.number_input("Air-side fouling (m2 K/W)", min_value=0.0, max_value=0.002, value=0.0002, step=0.00005, format="%.5f")
            with a8:
                rfi = st.number_input("Tube-side fouling (m2 K/W)", min_value=0.0, max_value=0.002, value=0.0001, step=0.00005, format="%.5f")

        st.subheader("Air side")
        flow_mode = st.radio("Air flow input mode", ["Face velocity (m/s)", "Volume flow (m3/h)"], horizontal=True)
        if flow_mode == "Face velocity (m/s)":
            v_face_input = st.number_input("Face velocity (m/s)", min_value=0.2, max_value=6.0, value=1.7, step=0.1)
            vdot = v_face_input * face_w * face_h
            vdot_m3h = vdot * 3600.0
        else:
            vdot_m3h = st.number_input("Air volume flow (m3/h)", min_value=500.0, max_value=100000.0, value=6250.0, step=10.0)
            vdot = vdot_m3h / 3600.0
            v_face_input = vdot / max(face_w * face_h, 1e-9)
        st.info(f"Face velocity = {v_face_input:.2f} m/s, volume flow = {vdot:.3f} m3/s ({vdot_m3h:.0f} m3/h)")

        air_mode = st.radio("Air inlet input method", ["Dry Bulb + RH", "Dry Bulb + Wet Bulb"], horizontal=True)
        d1, d2, d3 = st.columns(3)
        with d1:
            t_air_in_c = st.number_input("Air inlet DB (C)", min_value=0.0, max_value=55.0, value=13.5, step=0.1)
        with d2:
            if air_mode == "Dry Bulb + RH":
                rh_air_in = st.number_input("Air inlet RH (%)", min_value=5.0, max_value=100.0, value=95.0, step=0.5)
                w_air_in = w_from_t_rh(t_air_in_c, rh_air_in)
                wb_air_in = wb_from_t_w(t_air_in_c, w_air_in)
                st.caption(f"Calculated inlet WB = {wb_air_in:.2f} C")
            else:
                wb_air_in = st.number_input("Air inlet WB (C)", min_value=-10.0, max_value=float(t_air_in_c), value=12.8, step=0.1)
                w_air_in = w_from_t_wb(t_air_in_c, wb_air_in)
                rh_air_in = rh_from_t_w(t_air_in_c, w_air_in)
                st.caption(f"Calculated inlet RH = {rh_air_in:.2f} %")
        with d3:
            dp_air_in = dew_point_from_t_w(t_air_in_c, w_air_in)
            st.metric("Air inlet dew point", f"{dp_air_in:.2f} C")

        if run_mode == "Sizing: find steam flow for target leaving DB":
            target_t_air_out_c = st.number_input("Target leaving DB after reheat (C)", min_value=t_air_in_c, max_value=60.0, value=18.0, step=0.1)
            target_rh = rh_from_t_w(target_t_air_out_c, w_air_in)
            st.caption(f"If achieved, leaving RH will be approximately {target_rh:.1f} % at constant humidity ratio.")
        else:
            target_t_air_out_c = st.number_input("Reference target leaving DB for comparison (C)", min_value=t_air_in_c, max_value=60.0, value=18.0, step=0.1)

        st.subheader("Steam side")
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            pressure_basis = st.selectbox("Steam pressure basis", ["bar(g)", "bar(abs)"], index=0)
        with s2:
            p_steam_input = st.number_input("Steam pressure", min_value=0.1, max_value=20.0, value=2.0, step=0.1)
        with s3:
            inlet_state = st.selectbox("Steam inlet state", ["Saturated dry steam", "Wet steam", "Superheated steam"], index=0)
        with s4:
            max_subcool_k = st.number_input("Allowable outlet condensate subcooling (K)", min_value=0.0, max_value=30.0, value=0.0, step=0.5)

        s5, s6, s7, s8 = st.columns(4)
        with s5:
            x_inlet = st.number_input("Wet steam inlet quality x", min_value=0.50, max_value=1.00, value=0.98, step=0.01)
        with s6:
            superheat_k = st.number_input("Steam superheat (K)", min_value=0.0, max_value=80.0, value=5.0, step=0.5)
        with s7:
            if run_mode == "Rating: given steam flow":
                mdot_steam_kg_h = st.number_input("Steam mass flow (kg/h)", min_value=1.0, max_value=50000.0, value=80.0, step=1.0)
            else:
                mdot_steam_kg_h = None
                st.metric("Steam mass flow", "Solved by app")
        with s8:
            header_length_m = st.number_input("Header length (m)", min_value=0.10, max_value=20.0, value=float(face_h), step=0.10)

        s9, s10 = st.columns(2)
        with s9:
            header_in_diam_in = st.selectbox("Inlet header OD (inch)", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0], index=2)
        with s10:
            header_out_diam_in = st.selectbox("Outlet header OD (inch)", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0], index=4)

        do = do_mm * MM
        tw = tw_mm * MM
        tf = tf_mm * MM
        st_pitch = st_mm * MM
        sl_pitch = sl_mm * MM
        fin_k = mat_k[fin_mat]
        tube_k = mat_k[tube_mat]

        p_steam_abs_pa = (p_steam_input + 1.01325) * 1e5 if pressure_basis == "bar(g)" else p_steam_input * 1e5

        if HAS_CP:
            tsat_supply_c = PropsSI("T", "P", p_steam_abs_pa, "Q", 0, WATER) - 273.15
            st.info(f"Steam saturation temperature at supply pressure = {tsat_supply_c:.2f} C")
        else:
            st.warning("CoolProp not available in this environment, so steam saturation temperature preview is disabled.")

        run = st.button("Run steam coil analysis", type="primary", use_container_width=True)

    with tab2:
        if not run:
            st.info("Enter inputs and run the analysis.")
            with st.expander("Model assumptions", expanded=True):
                st.markdown(
                    """
                    1. Air is reheated sensibly only, so humidity ratio stays constant through the steam coil.
                    2. Steam side is modeled row by row and can pass through superheated, condensing, and subcooled regions.
                    3. The internal condensing heat transfer uses a Shah-style enhancement over liquid-only Gnielinski heat transfer.
                    4. Air-side geometry, fin efficiency, free-area treatment, and louver options follow the same style as your DX coil app.
                    5. Steam distribution maldistribution, control valve behavior, vacuum breakers, and trap sizing are not modeled yet.
                    """
                )
            return

        try:
            common_kwargs = dict(
                face_w=face_w,
                face_h=face_h,
                rows=int(rows),
                st=st_pitch,
                sl=sl_pitch,
                do=do,
                tw=tw,
                tf=tf,
                fpi=float(fpi),
                fin_k=fin_k,
                tube_k=tube_k,
                circuits=int(circuits),
                vdot_m3_s=vdot,
                t_air_in_c=t_air_in_c,
                w_air_in=w_air_in,
                p_steam_abs_pa=p_steam_abs_pa,
                inlet_state=inlet_state,
                x_inlet=x_inlet,
                superheat_k=superheat_k,
                max_subcool_k=max_subcool_k,
                sigma_free_area=sigma_free,
                fin_type=fin_type,
                louver_angle_deg=louver_angle_deg,
                louver_gap_mm=louver_gap_mm,
                louver_cuts_per_row=int(louver_cuts_per_row),
                h_mult_wavy=h_mult_wavy,
                dp_mult_wavy=dp_mult_wavy,
                rfo=rfo,
                rfi=rfi,
                header_in_diam_in=header_in_diam_in,
                header_out_diam_in=header_out_diam_in,
                header_length_m=header_length_m,
                target_t_air_out_c=target_t_air_out_c,
            )

            if run_mode == "Rating: given steam flow":
                df_rows, summary, geom = simulate_steam_coil(mdot_steam_total=mdot_steam_kg_h / 3600.0, **common_kwargs)
                solved_mdot_kg_h = summary["Steam_mdot_kg_h"]
                solved = True
            else:
                solved_mdot_kg_s, df_rows, summary, geom, solved = solve_steam_flow_for_target(**common_kwargs)
                solved_mdot_kg_h = solved_mdot_kg_s * 3600.0
                summary["Steam_mdot_kg_s"] = solved_mdot_kg_s
                summary["Steam_mdot_kg_h"] = solved_mdot_kg_h

            st.success("Analysis complete")

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
                if summary["Rows_needed_to_target"] is None:
                    txt = "Not reached" if target_t_air_out_c is not None else "-"
                else:
                    txt = str(summary["Rows_needed_to_target"])
                st.metric("Rows to target", txt)

            if run_mode == "Sizing: find steam flow for target leaving DB":
                if solved:
                    st.success(f"Target leaving DB of {target_t_air_out_c:.2f} C can be met. Required steam flow is about {solved_mdot_kg_h:.1f} kg/h.")
                else:
                    st.warning(
                        f"Even at high steam flow, the coil did not reach the target leaving DB of {target_t_air_out_c:.2f} C. This suggests the present coil face area / rows / fin geometry is UA-limited."
                    )
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

            st.subheader("Row-by-row results")
            st.dataframe(df_rows.round(4), use_container_width=True, height=420)

            st.subheader("Downloads")
            csv_data = df_rows.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download row results CSV",
                data=csv_data,
                file_name=f"steam_coil_rows_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            summary_json = json.dumps(summary, indent=2)
            st.download_button(
                "Download summary JSON",
                data=summary_json,
                file_name=f"steam_coil_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(f"Simulation error: {exc}")
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
