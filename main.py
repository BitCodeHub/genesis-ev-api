#!/usr/bin/env python3
"""
Genesis EV Route Planning API — Unified Render Service
Endpoints:
  GET  /health
  POST /route/plan
  GET  /chargers/nearby?lat=X&lon=Y&radius=10
  GET  /weather?lat=X&lon=Y

Physics-based consumption model (no PyTorch).
Weather: Open-Meteo (no key).
Chargers: NREL AFDC DEMO_KEY + OSM Overpass fallback.
Routing: OSRM public API (router.project-osrm.org).
"""

import os
import math
import time
import heapq
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 7900))

# ---------------------------------------------------------------------------
# Seeded DCFC corridor data (known stations along key US EV routes)
# These ensure routing works even when APIs are rate-limited
# ---------------------------------------------------------------------------
CORRIDOR_SEED_CHARGERS = [
    # LA Basin
    {"id": "seed-ea-ontario", "name": "Electrify America - Ontario Mills", "lat": 34.0571, "lon": -117.5509, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Ontario", "state": "CA", "status": "E", "max_power_kw": 150},
    {"id": "seed-ea-fontana", "name": "Electrify America - Fontana", "lat": 34.0571, "lon": -117.1786, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Fontana", "state": "CA", "status": "E", "max_power_kw": 150},
    {"id": "seed-tesla-beaumont", "name": "Tesla Supercharger - Beaumont", "lat": 33.9469, "lon": -116.9772, "ev_network": "Tesla", "ev_dc_fast_num": 8, "ev_level2_evse_num": 0, "city": "Beaumont", "state": "CA", "status": "E", "max_power_kw": 250},
    {"id": "seed-evgo-cabazon", "name": "EVgo - Cabazon", "lat": 33.9212, "lon": -116.7813, "ev_network": "EVgo", "ev_dc_fast_num": 2, "ev_level2_evse_num": 0, "city": "Cabazon", "state": "CA", "status": "E", "max_power_kw": 100},
    # Barstow area (critical I-15 midpoint)
    {"id": "seed-ea-barstow", "name": "Electrify America - Barstow Station", "lat": 34.8957, "lon": -117.0373, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Barstow", "state": "CA", "status": "E", "max_power_kw": 150},
    {"id": "seed-evgo-barstow", "name": "EVgo - Barstow", "lat": 34.8967, "lon": -117.0212, "ev_network": "EVgo", "ev_dc_fast_num": 2, "ev_level2_evse_num": 0, "city": "Barstow", "state": "CA", "status": "E", "max_power_kw": 100},
    {"id": "seed-tesla-barstow", "name": "Tesla Supercharger - Barstow", "lat": 34.8900, "lon": -116.9987, "ev_network": "Tesla", "ev_dc_fast_num": 8, "ev_level2_evse_num": 0, "city": "Barstow", "state": "CA", "status": "E", "max_power_kw": 250},
    {"id": "seed-caltrans-i15", "name": "CalTrans I-15 Charging", "lat": 35.0318, "lon": -116.4692, "ev_network": "CalTrans", "ev_dc_fast_num": 2, "ev_level2_evse_num": 0, "city": "Lenwood", "state": "CA", "status": "E", "max_power_kw": 50},
    # Baker (last CA stop)
    {"id": "seed-ea-baker", "name": "Electrify America - Baker", "lat": 35.2631, "lon": -116.0708, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Baker", "state": "CA", "status": "E", "max_power_kw": 350},
    {"id": "seed-evgo-baker", "name": "EVgo - Baker", "lat": 35.2628, "lon": -116.0714, "ev_network": "EVgo", "ev_dc_fast_num": 2, "ev_level2_evse_num": 0, "city": "Baker", "state": "CA", "status": "E", "max_power_kw": 100},
    # Primm / NV border
    {"id": "seed-tesla-primm", "name": "Tesla Supercharger - Primm NV", "lat": 35.6094, "lon": -115.3912, "ev_network": "Tesla", "ev_dc_fast_num": 8, "ev_level2_evse_num": 0, "city": "Primm", "state": "NV", "status": "E", "max_power_kw": 250},
    {"id": "seed-ea-jean", "name": "Electrify America - Henderson NV", "lat": 35.8761, "lon": -115.3345, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Henderson", "state": "NV", "status": "E", "max_power_kw": 350},
    # Las Vegas
    {"id": "seed-tesla-lv", "name": "Tesla Supercharger - Las Vegas", "lat": 36.1147, "lon": -115.1728, "ev_network": "Tesla", "ev_dc_fast_num": 20, "ev_level2_evse_num": 0, "city": "Las Vegas", "state": "NV", "status": "E", "max_power_kw": 250},
    {"id": "seed-evgo-lv", "name": "EVgo - Las Vegas", "lat": 36.0984, "lon": -115.1663, "ev_network": "EVgo", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Las Vegas", "state": "NV", "status": "E", "max_power_kw": 350},
    {"id": "seed-ea-lv", "name": "Electrify America - Las Vegas", "lat": 36.2167, "lon": -115.2578, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Las Vegas", "state": "NV", "status": "E", "max_power_kw": 350},
    # San Diego → LA corridor (I-5/I-405)
    {"id": "seed-ea-sdmission", "name": "Electrify America - San Diego Mission Valley", "lat": 32.7741, "lon": -117.1194, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "San Diego", "state": "CA", "status": "E", "max_power_kw": 150},
    {"id": "seed-tesla-irvine", "name": "Tesla Supercharger - Irvine", "lat": 33.6846, "lon": -117.8266, "ev_network": "Tesla", "ev_dc_fast_num": 12, "ev_level2_evse_num": 0, "city": "Irvine", "state": "CA", "status": "E", "max_power_kw": 250},
    # SF Bay Area
    {"id": "seed-tesla-sf", "name": "Tesla Supercharger - San Francisco", "lat": 37.7749, "lon": -122.4194, "ev_network": "Tesla", "ev_dc_fast_num": 20, "ev_level2_evse_num": 0, "city": "San Francisco", "state": "CA", "status": "E", "max_power_kw": 250},
    {"id": "seed-ea-hayward", "name": "Electrify America - Hayward", "lat": 37.6688, "lon": -122.0808, "ev_network": "Electrify America", "ev_dc_fast_num": 4, "ev_level2_evse_num": 0, "city": "Hayward", "state": "CA", "status": "E", "max_power_kw": 150},
]

# ---------------------------------------------------------------------------
# Vehicle specs
# ---------------------------------------------------------------------------
GV60_SPECS = {
    "gv60": {
        "battery_kwh": 77.4,
        "usable_kwh": 74.0,
        "max_range_km": 400,
        "max_dc_kw": 240,
        "charging_curve": [   # (soc_pct, max_kw)
            (0, 240), (10, 240), (20, 235), (30, 220),
            (50, 180), (70, 120), (80, 80), (90, 50), (100, 11),
        ],
        "mass_kg": 2205,
        "drag_coeff": 0.29,
        "frontal_area_m2": 2.8,
        "regen_efficiency": 0.75,
        "drivetrain_efficiency": 0.90,
    },
    "gv70e": {
        "battery_kwh": 77.4,
        "usable_kwh": 74.0,
        "max_range_km": 390,
        "max_dc_kw": 240,
        "charging_curve": [
            (0, 240), (10, 240), (20, 235), (30, 220),
            (50, 180), (70, 120), (80, 80), (90, 50), (100, 11),
        ],
        "mass_kg": 2360,
        "drag_coeff": 0.31,
        "frontal_area_m2": 2.9,
        "regen_efficiency": 0.75,
        "drivetrain_efficiency": 0.90,
    },
    "g80e": {
        "battery_kwh": 87.2,
        "usable_kwh": 83.0,
        "max_range_km": 427,
        "max_dc_kw": 180,
        "charging_curve": [
            (0, 180), (10, 180), (20, 175), (30, 165),
            (50, 140), (70, 100), (80, 60), (90, 35), (100, 11),
        ],
        "mass_kg": 2385,
        "drag_coeff": 0.26,
        "frontal_area_m2": 2.7,
        "regen_efficiency": 0.75,
        "drivetrain_efficiency": 0.90,
    },
}

def _resolve_vehicle(name: str) -> str:
    n = name.lower().replace("-", "").replace(" ", "")
    if "gv70" in n:
        return "gv70e"
    if "g80" in n:
        return "g80e"
    return "gv60"

# ---------------------------------------------------------------------------
# Physics-based consumption model (pure Python, no ML)
# ---------------------------------------------------------------------------
def predict_consumption_kwh(
    dist_km: float,
    speed_kmh: float,
    grade_pct: float = 0,
    temp_c: float = 20,
    wind_speed_kmh: float = 0,
    vehicle: str = "gv60",
    elevation_gain_m: float = 0,
    elevation_loss_m: float = 0,
) -> float:
    """Return net energy consumption in kWh for a road segment."""
    if dist_km <= 0:
        return 0.0

    specs = GV60_SPECS.get(vehicle, GV60_SPECS["gv60"])
    speed_ms = max(speed_kmh, 1) / 3.6
    mass = specs["mass_kg"]
    Cd = specs["drag_coeff"]
    A = specs["frontal_area_m2"]
    rho_air = 1.225
    g = 9.81

    # Aerodynamic drag power (W)
    effective_speed_ms = speed_ms + (wind_speed_kmh / 3.6) * 0.5   # headwind assumption
    F_aero = 0.5 * rho_air * Cd * A * effective_speed_ms ** 2

    # Rolling resistance
    F_roll = 0.013 * mass * g

    # Grade
    grade_rad = math.atan(grade_pct / 100.0)
    F_grade = mass * g * math.sin(grade_rad)

    F_total = F_aero + F_roll + F_grade
    P_wheels = F_total * speed_ms   # W

    # Temperature penalty on battery / HVAC
    if temp_c < 0:
        temp_factor = 1.30
    elif temp_c < 10:
        temp_factor = 1.15
    elif temp_c > 35:
        temp_factor = 1.10
    else:
        temp_factor = 1.0

    # Battery power
    eff = specs["drivetrain_efficiency"]
    regen_eff = specs["regen_efficiency"]
    if P_wheels >= 0:
        P_battery = (P_wheels / eff) * temp_factor
    else:
        P_battery = P_wheels * regen_eff   # regenerative (negative = energy recovered)

    time_h = dist_km / max(speed_kmh, 1)
    energy_kwh = (P_battery / 1000.0) * time_h   # W → kW → kWh

    # Elevation correction using potential energy
    if elevation_gain_m > 0:
        pe_gain_kwh = (mass * g * elevation_gain_m) / (3_600_000 * eff)
        energy_kwh += pe_gain_kwh
    if elevation_loss_m > 0:
        pe_regen_kwh = (mass * g * elevation_loss_m * regen_eff) / 3_600_000
        energy_kwh -= pe_regen_kwh

    return max(energy_kwh, 0.0)

def get_charging_power_kw(soc_pct: float, vehicle: str = "gv60") -> float:
    specs = GV60_SPECS.get(vehicle, GV60_SPECS["gv60"])
    curve = specs["charging_curve"]
    for i in range(len(curve) - 1):
        s0, p0 = curve[i]
        s1, p1 = curve[i + 1]
        if s0 <= soc_pct <= s1:
            t = (soc_pct - s0) / (s1 - s0)
            return p0 + t * (p1 - p0)
    return curve[-1][1]

def estimate_charge_time_min(from_soc: float, to_soc: float, vehicle: str = "gv60",
                              charger_kw: float = 150) -> float:
    specs = GV60_SPECS.get(vehicle, GV60_SPECS["gv60"])
    battery_kwh = specs["usable_kwh"]
    kwh_needed = (to_soc - from_soc) / 100.0 * battery_kwh
    if kwh_needed <= 0:
        return 0.0
    steps = 20
    total_time_h = 0.0
    soc_step = (to_soc - from_soc) / steps
    for i in range(steps):
        mid_soc = from_soc + (i + 0.5) * soc_step
        power = min(get_charging_power_kw(mid_soc, vehicle), charger_kw)
        kwh_step = kwh_needed / steps
        total_time_h += kwh_step / max(power, 1)
    return total_time_h * 60

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    lon1, lon2 = math.radians(lon1), math.radians(lon2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

# ---------------------------------------------------------------------------
# External service calls
# ---------------------------------------------------------------------------
OSRM_BASE = "https://router.project-osrm.org"
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
NREL_BASE = "https://developer.nrel.gov/api/alt-fuel-stations/v1"
NREL_KEY = "DEMO_KEY"

def get_osrm_route(orig_lat, orig_lon, dest_lat, dest_lon):
    url = (
        f"{OSRM_BASE}/route/v1/driving/"
        f"{orig_lon},{orig_lat};{dest_lon},{dest_lat}"
    )
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            return None, f"OSRM: {data.get('message', 'error')}"
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]
        waypoints = [(c[1], c[0]) for c in coords]
        return {
            "distance_m": route["distance"],
            "duration_s": route["duration"],
            "waypoints": waypoints,
        }, None
    except Exception as e:
        return None, str(e)

def get_weather(lat, lon):
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,wind_direction_10m,weather_code,precipitation,relative_humidity_2m",
            "forecast_days": 1,
            "wind_speed_unit": "kmh",
            "temperature_unit": "celsius",
        }
        resp = requests.get(OPEN_METEO_BASE, params=params, timeout=10)
        resp.raise_for_status()
        cur = resp.json().get("current", {})
        wmo = cur.get("weather_code", 0)
        precip_map = {
            "none": [0,1,2,3],
            "fog": [45,48],
            "rain": [51,53,55,56,57,61,63,65,66,67,80,81,82],
            "snow": [71,73,75,77,85,86],
            "thunderstorm": [95,96,99],
        }
        precip_type = "none"
        for ptype, codes in precip_map.items():
            if wmo in codes:
                precip_type = ptype
                break
        return {
            "temp_c": cur.get("temperature_2m", 20),
            "wind_speed_kmh": cur.get("wind_speed_10m", 0),
            "wind_direction_deg": cur.get("wind_direction_10m", 0),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "precipitation_mm": cur.get("precipitation", 0),
            "precipitation_type": precip_type,
            "weather_code": wmo,
        }
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
        return {"temp_c": 20, "wind_speed_kmh": 0, "precipitation_type": "none"}

def get_chargers_nrel(lat, lon, radius_miles=10, limit=20):
    try:
        params = {
            "api_key": NREL_KEY,
            "latitude": lat,
            "longitude": lon,
            "radius": radius_miles,
            "fuel_type": "ELEC",
            "ev_connector_types": "J1772COMBO CHADEMO",
            "status": "E",
            "limit": limit,
        }
        resp = requests.get(f"{NREL_BASE}.json", params=params, timeout=15)
        if resp.status_code == 200:
            stations = resp.json().get("fuel_stations", [])
            return stations, "nrel"
        elif resp.status_code == 429:
            logger.warning("NREL rate limited")
    except Exception as e:
        logger.warning(f"NREL failed: {e}")
    return None, None

def get_chargers_nrel_route(waypoints, distance_miles=5):
    """Single NREL call for the whole route using nearby-route endpoint."""
    # Sample waypoints to build WKT
    step = max(1, len(waypoints) // 15)
    sampled = waypoints[::step]
    coords_str = ", ".join(f"{lon} {lat}" for lat, lon in sampled)
    wkt = f"LINESTRING({coords_str})"
    try:
        params = {
            "api_key": NREL_KEY,
            "route": wkt,
            "distance": distance_miles,
            "fuel_type": "ELEC",
            "ev_connector_types": "J1772COMBO CHADEMO",
            "status": "E",
            "limit": 100,
        }
        resp = requests.get(f"{NREL_BASE}/nearby-route.json", params=params, timeout=20)
        if resp.status_code == 200:
            stations = resp.json().get("alt_fuel_stations", resp.json().get("fuel_stations", []))
            logger.info(f"NREL route query returned {len(stations)} stations")
            return stations, "nrel"
        elif resp.status_code == 429:
            logger.warning("NREL rate limited on route query")
    except Exception as e:
        logger.warning(f"NREL route query failed: {e}")
    return None, None

def get_chargers_overpass(lat, lon, radius_m=16000):
    """OSM Overpass fallback for EV chargers."""
    query = f"""
[out:json][timeout:15];
node["amenity"="charging_station"](around:{radius_m},{lat},{lon});
out body;
"""
    try:
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=20,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        stations = []
        for el in elements:
            tags = el.get("tags", {})
            stations.append({
                "id": el.get("id"),
                "name": tags.get("name", "EV Charging Station"),
                "lat": el.get("lat"),
                "lon": el.get("lon"),
                "ev_network": tags.get("network", tags.get("operator", "Unknown")),
                "ev_connector_types": tags.get("socket:type2", tags.get("socket:chademo", "")),
                "ev_dc_fast_num": 1 if tags.get("socket:chademo") or tags.get("socket:type2_combo") else 0,
                "ev_level2_evse_num": 1 if tags.get("socket:type2") else 0,
                "access_days_time": tags.get("opening_hours", "24/7"),
                "status": "E",
                "distance_km": haversine_km(lat, lon, el.get("lat",0), el.get("lon",0)),
            })
        stations.sort(key=lambda x: x.get("distance_km", 999))
        return stations, "overpass_osm"
    except Exception as e:
        logger.warning(f"Overpass failed: {e}")
        return [], "overpass_osm"

def _normalize_nrel_station(s, ref_lat=None, ref_lon=None):
    slat = s.get("latitude") or s.get("lat")
    slon = s.get("longitude") or s.get("lon")
    dist = None
    if ref_lat and slat and slon:
        dist = round(haversine_km(ref_lat, ref_lon, float(slat), float(slon)), 2)
    return {
        "id": s.get("id"),
        "name": s.get("station_name", s.get("name", "EV Station")),
        "street": s.get("street_address", s.get("street")),
        "city": s.get("city"),
        "state": s.get("state"),
        "lat": slat,
        "lon": slon,
        "ev_network": s.get("ev_network"),
        "ev_connector_types": s.get("ev_connector_types", []),
        "ev_dc_fast_num": s.get("ev_dc_fast_num") or 0,
        "ev_level2_evse_num": s.get("ev_level2_evse_num") or 0,
        "ev_level1_evse_num": s.get("ev_level1_evse_num") or 0,
        "access_days_time": s.get("access_days_time"),
        "phone": s.get("station_phone"),
        "status": s.get("status_code", "E"),
        "distance_km": dist or s.get("distance_km"),
    }

# ---------------------------------------------------------------------------
# Route planner (Dijkstra over charger stops)
# ---------------------------------------------------------------------------
def plan_route(orig_lat, orig_lon, dest_lat, dest_lon, start_soc_pct,
               vehicle="gv60", min_soc=10, target_charge_soc=80, avg_speed_kmh=105):

    specs = GV60_SPECS.get(vehicle, GV60_SPECS["gv60"])
    battery_kwh = specs["usable_kwh"]

    # 1. OSRM route
    route, err = get_osrm_route(orig_lat, orig_lon, dest_lat, dest_lon)
    if not route:
        return None, f"Routing failed: {err}"

    waypoints = route["waypoints"]
    total_dist_km = route["distance_m"] / 1000.0
    osrm_drive_min = route["duration_s"] / 60.0

    # 2. Weather
    wx_orig = get_weather(orig_lat, orig_lon)
    wx_dest = get_weather(dest_lat, dest_lon)
    avg_temp = (wx_orig["temp_c"] + wx_dest["temp_c"]) / 2
    avg_wind = (wx_orig["wind_speed_kmh"] + wx_dest["wind_speed_kmh"]) / 2

    # 3. Consumption estimate
    total_kwh = predict_consumption_kwh(
        total_dist_km, avg_speed_kmh, grade_pct=0.5,   # modest grade for typical roads
        temp_c=avg_temp, wind_speed_kmh=avg_wind, vehicle=vehicle,
    )
    energy_per_km = total_kwh / total_dist_km if total_dist_km > 0 else 0.20

    # 4. Chargers near route — seed data + NREL + Overpass
    charger_set = {}

    # Always start with seeded corridor chargers (reliable, no API dependency)
    for s in CORRIDOR_SEED_CHARGERS:
        sid = s.get("id")
        charger_set[sid] = _normalize_nrel_station(s)

    # Supplement with NREL nearby-route (single call)
    stations, source = get_chargers_nrel_route(waypoints, distance_miles=5)
    if stations:
        for s in stations:
            sid = s.get("id") or s.get("station_name")
            if sid and sid not in charger_set:
                norm = _normalize_nrel_station(s)
                if (norm.get("ev_dc_fast_num") or 0) > 0:
                    charger_set[sid] = norm
    else:
        # NREL unavailable — try Overpass at key midpoints
        mid_wp = waypoints[len(waypoints) // 2]
        osm_stations, _ = get_chargers_overpass(mid_wp[0], mid_wp[1], radius_m=60000)
        for s in (osm_stations or []):
            sid = s.get("id")
            if sid and sid not in charger_set and (s.get("ev_dc_fast_num") or 0) > 0:
                charger_set[sid] = _normalize_nrel_station(s)

    logger.info(f"Total charger candidates: {len(charger_set)}")

    # Filter to DCFC only and within generous corridor bounding box
    min_lat = min(orig_lat, dest_lat) - 1.5
    max_lat = max(orig_lat, dest_lat) + 1.5
    min_lon = min(orig_lon, dest_lon) - 1.5
    max_lon = max(orig_lon, dest_lon) + 1.5

    dc_chargers = []
    for s in charger_set.values():
        if not s.get("lat") or not s.get("lon"):
            continue
        try:
            clat, clon = float(s["lat"]), float(s["lon"])
        except (TypeError, ValueError):
            continue
        if not (min_lat <= clat <= max_lat and min_lon <= clon <= max_lon):
            continue
        # Accept if DCFC count > 0 OR if OSM source (ev_dc_fast_num may be 1 by default)
        if (s.get("ev_dc_fast_num") or 0) > 0:
            dc_chargers.append(s)

    logger.info(f"Route {total_dist_km:.0f}km | {total_kwh:.1f}kWh | {len(dc_chargers)} DCFC chargers found")

    # 5. Build Dijkstra nodes
    def dist_along_route(clat, clon):
        min_d = float("inf")
        best_idx = 0
        for i, (wlat, wlon) in enumerate(waypoints):
            d = haversine_km(clat, clon, wlat, wlon)
            if d < min_d:
                min_d = d
                best_idx = i
        return (best_idx / max(len(waypoints)-1, 1)) * total_dist_km

    nodes = [{"type": "origin", "lat": orig_lat, "lon": orig_lon, "d": 0.0}]
    for s in dc_chargers:
        clat, clon = float(s["lat"]), float(s["lon"])
        d = dist_along_route(clat, clon)
        nodes.append({"type": "charger", "lat": clat, "lon": clon, "d": d, "charger": s})
    nodes.append({"type": "destination", "lat": dest_lat, "lon": dest_lon, "d": total_dist_km})
    nodes.sort(key=lambda x: x["d"])

    n = len(nodes)

    # Dijkstra: (total_time_min, counter, node_idx, soc_pct, path)
    # counter breaks ties so dicts in path are never compared
    _ctr = [0]
    def push(heap, t, ni, soc, path):
        _ctr[0] += 1
        heapq.heappush(heap, (t, _ctr[0], ni, soc, path))

    heap = []
    push(heap, 0.0, 0, float(start_soc_pct), [])
    visited = {}
    best_result = None
    best_time = float("inf")

    while heap:
        t, _, ci, soc, path = heapq.heappop(heap)
        if t > best_time:
            break
        key = (ci, round(soc, 1))
        if key in visited and visited[key] <= t:
            continue
        visited[key] = t

        cur = nodes[ci]
        cur_path = path + [{
            "type": cur["type"],
            "lat": cur["lat"],
            "lon": cur["lon"],
            "arrival_soc_pct": round(soc, 1),
            "time_elapsed_min": round(t, 1),
            "dist_from_origin_km": round(cur["d"], 1),
            "charger": cur.get("charger"),
        }]

        if ci == n - 1:
            if soc >= min_soc and t < best_time:
                best_time = t
                best_result = cur_path
            continue

        for ni in range(ci + 1, n):
            nxt = nodes[ni]
            seg_km = nxt["d"] - cur["d"]
            if seg_km <= 0:
                continue
            seg_kwh = energy_per_km * seg_km
            seg_soc_used = (seg_kwh / battery_kwh) * 100
            arr_soc = soc - seg_soc_used

            if arr_soc < 0:
                break   # can't reach further nodes either

            drive_min = (seg_km / avg_speed_kmh) * 60

            if nxt["type"] == "destination":
                if arr_soc >= min_soc:
                    push(heap, t + drive_min, ni, arr_soc, cur_path)
            else:
                # Option A: pass through without charging (if enough SOC)
                if arr_soc >= target_charge_soc:
                    push(heap, t + drive_min, ni, arr_soc, cur_path)
                # Option B: charge to target_charge_soc
                if arr_soc < target_charge_soc:
                    ch_min = estimate_charge_time_min(
                        max(arr_soc, 0), target_charge_soc, vehicle,
                        charger_kw=min(
                            nxt.get("charger", {}).get("max_power_kw", 150) or 150,
                            specs["max_dc_kw"]
                        )
                    )
                    push(heap, t + drive_min + ch_min, ni, target_charge_soc, cur_path)

    if not best_result:
        # Direct drive fallback
        arr_soc = start_soc_pct - (energy_per_km * total_dist_km / battery_kwh * 100)
        if arr_soc >= min_soc:
            best_result = [
                {"type": "origin", "lat": orig_lat, "lon": orig_lon, "arrival_soc_pct": start_soc_pct,
                 "time_elapsed_min": 0, "dist_from_origin_km": 0, "charger": None},
                {"type": "destination", "lat": dest_lat, "lon": dest_lon, "arrival_soc_pct": round(arr_soc, 1),
                 "time_elapsed_min": round(osrm_drive_min, 1), "dist_from_origin_km": round(total_dist_km, 1), "charger": None},
            ]
            best_time = osrm_drive_min
        else:
            return None, "No viable route — battery insufficient and no reachable DCFC chargers found"

    stops = [s for s in best_result if s["type"] == "charger"]

    return {
        "origin": {"lat": orig_lat, "lon": orig_lon},
        "destination": {"lat": dest_lat, "lon": dest_lon},
        "vehicle": vehicle,
        "start_soc_pct": start_soc_pct,
        "route": {
            "distance_km": round(total_dist_km, 1),
            "total_time_min": round(best_time, 1),
            "total_time_formatted": f"{int(best_time//60)}h {int(best_time%60):02d}m",
            "drive_time_min": round(osrm_drive_min, 1),
            "charging_stops": len(stops),
        },
        "energy": {
            "estimated_kwh": round(total_kwh, 2),
            "kwh_per_100km": round(energy_per_km * 100, 1),
            "avg_temp_c": round(avg_temp, 1),
            "avg_wind_kmh": round(avg_wind, 1),
        },
        "stops": best_result,
        "charging_stops": stops,
        "weather": {"origin": wx_orig, "destination": wx_dest},
        "planned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, None

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "genesis-ev-api",
        "version": "1.0.0",
        "built_by": "Elim 🦋",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

@app.route("/")
def index():
    return jsonify({
        "service": "Genesis EV Route Planning API",
        "version": "1.0.0",
        "endpoints": {
            "GET /health": "Service health",
            "POST /route/plan": "Plan EV route with charging stops",
            "GET /chargers/nearby": "Find nearby EV chargers (?lat=X&lon=Y&radius=10)",
            "GET /weather": "Current weather (?lat=X&lon=Y)",
        },
        "example_route": {
            "url": "POST /route/plan",
            "body": {
                "origin_lat": 33.77, "origin_lon": -118.19,
                "dest_lat": 36.17, "dest_lon": -115.14,
                "soc_pct": 80, "vehicle_model": "gv60"
            }
        }
    })

@app.route("/route/plan", methods=["POST"])
def route_plan():
    data = request.json or {}

    orig_lat = data.get("origin_lat") or data.get("orig_lat")
    orig_lon = data.get("origin_lon") or data.get("orig_lon")
    dest_lat = data.get("dest_lat") or data.get("destination_lat")
    dest_lon = data.get("dest_lon") or data.get("destination_lon")
    soc = float(data.get("soc_pct", data.get("start_soc_pct", 80)))
    vehicle = _resolve_vehicle(data.get("vehicle_model", data.get("vehicle", "gv60")))

    if not all([orig_lat, orig_lon, dest_lat, dest_lon]):
        return jsonify({
            "error": "Required fields: origin_lat, origin_lon, dest_lat, dest_lon",
        }), 400

    try:
        t0 = time.time()
        result, err = plan_route(
            float(orig_lat), float(orig_lon),
            float(dest_lat), float(dest_lon),
            soc, vehicle=vehicle,
        )
        if err:
            return jsonify({"error": err}), 500
        result["planning_time_s"] = round(time.time() - t0, 2)
        return jsonify(result)
    except Exception as e:
        logger.exception(f"Route plan error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/chargers/nearby")
def chargers_nearby():
    try:
        lat = float(request.args.get("lat", 0))
        lon = float(request.args.get("lon", 0))
        radius = float(request.args.get("radius", 10))
    except Exception:
        return jsonify({"error": "lat, lon, radius must be numbers"}), 400

    if not lat or not lon:
        return jsonify({"error": "lat and lon required"}), 400

    stations, source = get_chargers_nrel(lat, lon, radius_miles=radius)
    if stations is None:
        radius_m = radius * 1609.34
        stations, source = get_chargers_overpass(lat, lon, radius_m=int(radius_m))

    normalized = [_normalize_nrel_station(s, lat, lon) for s in stations]
    normalized.sort(key=lambda x: x.get("distance_km") or 999)

    return jsonify({
        "lat": lat, "lon": lon, "radius_miles": radius,
        "count": len(normalized),
        "source": source,
        "stations": normalized,
    })

@app.route("/weather")
def weather_endpoint():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if not lat or not lon:
        return jsonify({"error": "lat and lon required"}), 400
    try:
        result = get_weather(float(lat), float(lon))
        result["lat"] = float(lat)
        result["lon"] = float(lon)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Starting Genesis EV API on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
