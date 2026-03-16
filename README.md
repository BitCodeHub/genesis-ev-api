# Genesis EV Route Planning API

Production REST API for Genesis EV route planning with charging stop optimization.
Deployed on Render — no tunnels, always on.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/route/plan` | Plan route with optimal charging stops |
| GET | `/chargers/nearby` | Find nearby EV chargers |
| GET | `/weather` | Current weather at a location |

## Route Plan Example

```bash
curl -X POST https://genesis-ev-api.onrender.com/route/plan \
  -H "Content-Type: application/json" \
  -d '{
    "origin_lat": 33.77,
    "origin_lon": -118.19,
    "dest_lat": 36.17,
    "dest_lon": -115.14,
    "soc_pct": 80,
    "vehicle_model": "gv60"
  }'
```

## Design

- **Routing:** OSRM public API (router.project-osrm.org)
- **Chargers:** NREL AFDC with OSM Overpass fallback
- **Weather:** Open-Meteo (no API key required)
- **Consumption:** Physics-based model (pure Python, no ML dependencies)
- **Optimizer:** Dijkstra over charging stop graph

## Supported Vehicles

- `gv60` — Genesis GV60 (default)
- `gv70e` — Genesis GV70 Electrified
- `g80e` — Genesis G80 Electrified

## Built by

Elim 🦋 — Lumen AI Solutions
