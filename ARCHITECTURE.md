# Energy Edge Controller — Architecture

## Overview

An Azure IoT Edge–based platform that manages and steers decentralized energy assets (solar inverters, batteries, industrial boilers) from an edge device. Each asset type runs as an independent IoT Edge module. A central controller module aggregates telemetry, makes grid-balancing decisions, and reports to Azure IoT Hub in the cloud.

---

## System Architecture

```
┌─────────────────────────────────── AZURE (Cloud) ──────────────────────────────────┐
│                                                                                      │
│  ┌───────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐ │
│  │     Azure IoT Hub      │───▶│    Azure Monitor /   │    │  Azure Container     │ │
│  │                        │    │    Log Analytics     │    │  Registry (ACR)      │ │
│  │  - Device/Module Twin  │    │                      │    │  - Module images     │ │
│  │  - D2C telemetry       │    │  - Metrics & Logs    │    └──────────────────────┘ │
│  │  - Direct methods      │    │  - Alerts            │                             │
│  └───────────┬────────────┘    └──────────────────────┘                             │
│              │ MQTT/AMQP over TLS ($upstream)                                        │
└──────────────┼───────────────────────────────────────────────────────────────────── ┘
               │
┌──────────────┼────────────────── IoT EDGE DEVICE ──────────────────────────────────┐
│              │                                                                       │
│  ┌───────────▼───────────────────────────────────────────────────────────────────┐ │
│  │                              edgeHub (system module)                           │ │
│  │                                                                                │ │
│  │  Routes:                                                                       │ │
│  │    solar-module/outputs/telemetry   ──▶ controller-module/inputs/telemetry    │ │
│  │    battery-module/outputs/telemetry ──▶ controller-module/inputs/telemetry    │ │
│  │    boiler-module/outputs/telemetry  ──▶ controller-module/inputs/telemetry    │ │
│  │    controller-module/outputs/cloud  ──▶ $upstream                             │ │
│  └────────┬──────────────┬──────────────┬────────────────────────────────────────┘ │
│           │              │              │                                            │
│  ┌────────▼───┐  ┌───────▼────┐  ┌─────▼──────┐  ┌─────────────────────────────┐ │
│  │solar-module│  │battery-    │  │boiler-     │  │    controller-module          │ │
│  │            │  │module      │  │module      │  │                               │ │
│  │ Inverter   │  │ Storage    │  │ Boiler     │  │  - Aggregates telemetry       │ │
│  │ Driver +   │  │ Driver +   │  │ Driver +   │  │  - Grid balance decisions     │ │
│  │ Irradiance │  │ SoC Sim    │  │ Temp Sim   │  │  - Issues direct methods      │ │
│  │ Simulator  │  │            │  │            │  │  - Reports to IoT Hub         │ │
│  └────────────┘  └────────────┘  └────────────┘  └───────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │                         telemetry-module                                        │ │
│  │   Receives aggregated telemetry → forwards to Azure Monitor                     │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Module Communication

Azure IoT Edge modules communicate via **three mechanisms**, each serving a different purpose:

### 1. Message Routing (edgeHub) — Telemetry / Events

Used for fire-and-forget telemetry from asset modules to the controller. edgeHub acts as a local message broker; the controller receives telemetry from all three asset modules on the same `telemetry` input.

```
solar-module   ──▶ output: "telemetry"
battery-module ──▶ output: "telemetry"  ──▶ edgeHub ──▶ controller-module input: "telemetry"
boiler-module  ──▶ output: "telemetry"
                                                    │
                                             controller-module ──▶ output: "cloud"
                                                                      │
                                                               edgeHub ($upstream)
                                                                      │
                                                               Azure IoT Hub
```

**Sender (asset module):**
```python
await client.send_message_to_output(telemetry.to_dict(), output_name="telemetry")
```

**Receiver (controller-module):**
```python
async def handle_asset_telemetry(data: dict, input_name: str) -> None:
    await registry.update(data)

client.on_message(handle_asset_telemetry)
```

---

### 2. Direct Methods — Commands (Request/Response)

Used by the controller to send commands to asset modules. Synchronous: the caller blocks until a response arrives or the timeout elapses.

```
controller-module
  └─▶ invoke_method("battery-module", "start_charging", {"power_kw": 50.0})
        └─▶ battery-module handles → 200 {"status": "ok"}
```

**Caller (controller-module):**
```python
response = await client.invoke_method(
    target_module_id="battery-module",
    method_name="start_charging",
    payload={"power_kw": 50.0},
)
```

**Handler (asset module) — dispatch dict pattern:**
```python
# Each method maps to a typed handler. No if/elif chain needed.
def _build_dispatch(battery: BatteryStorage) -> dict:
    async def _start_charging(payload: dict) -> None:
        parsed = StartChargingPayload.model_validate(payload)   # ← boundary validation
        power_kw = parsed.power_kw or battery.max_power_kw
        await battery.start_charging(power_kw)
    return {"start_charging": _start_charging, ...}

# Generic handle_method — same in every module
async def handle_method(request: DirectMethodRequest) -> DirectMethodResponse:
    handler = dispatch.get(request.name)
    if handler is None:
        return DirectMethodResponse(request.request_id, 404, {...})
    try:
        await handler(request.payload)
        return DirectMethodResponse(request.request_id, 200, {"status": "ok"})
    except ValidationError as exc:                              # invalid payload
        return DirectMethodResponse(request.request_id, 400, {"error": exc.errors()})
    except RuntimeError as exc:                                 # wrong state
        return DirectMethodResponse(request.request_id, 409, {"error": str(exc)})
    except Exception as exc:
        return DirectMethodResponse(request.request_id, 500, {"error": str(exc)})
```

**Available commands per module:**

| Module | Method | Payload | Constraints |
|--------|--------|---------|------------|
| `solar-module` | `start` | — | |
| `solar-module` | `stop` | — | |
| `solar-module` | `set_output` | `{"target_kw": float}` | `target_kw ≥ 0` |
| `solar-module` | `reset` | — | |
| `battery-module` | `start` | — | |
| `battery-module` | `stop` | — | |
| `battery-module` | `set_idle` | — | |
| `battery-module` | `start_charging` | `{"power_kw": float}` (optional) | `power_kw > 0` if provided |
| `battery-module` | `start_discharging` | `{"power_kw": float}` (optional) | `power_kw > 0` if provided |
| `battery-module` | `reset` | — | |
| `boiler-module` | `start` | — | |
| `boiler-module` | `stop` | — | |
| `boiler-module` | `set_temperature` | `{"target_celsius": float}` | `40 ≤ target_celsius ≤ 120` |
| `boiler-module` | `reset` | — | |

**Response codes:**

| Code | Meaning |
|------|---------|
| `200` | Command accepted and executed |
| `400` | Invalid payload — Pydantic `ValidationError` (wrong type, out-of-range value) |
| `404` | Unknown method name |
| `409` | Valid payload but illegal in current state (e.g. charging while not RUNNING) |
| `500` | Unexpected error in handler |

---

### 3. Module Twin — Configuration / Desired State

Used for persistent configuration. The cloud (or operator) updates desired properties; the module reacts and reports back in reported properties.

```
IoT Hub (desired properties)
  └─▶ {"max_power_kw": 80.0}
        └─▶ solar-module twin patch handler
              └─▶ inverter.max_power_kw = 80.0
                    └─▶ reports back: {"state": "RUNNING", "power_output_kw": 78.2}
```

**Python:**
```python
async def handle_twin_update(patch: dict) -> None:
    if "max_power_kw" in patch:
        inverter.max_power_kw = float(patch["max_power_kw"])

client.on_twin_update(handle_twin_update)
```

---

## Asset State Machine

All asset modules implement the same lifecycle state machine via `BaseAsset`. Subclasses implement `_on_start`, `_on_stop`, and `_on_fault` hooks — they never manipulate `_state` directly.

```
            start()
  IDLE ──────────────▶ STARTING
   ▲                       │
   │                       │ (startup delay complete)
   │ reset()               ▼
   │              ┌──── RUNNING ────┐
   │              │                 │
   │         fault(code)        stop()
   │              │                 │
   │              ▼                 ▼
   └────────── FAULT            STOPPING ──▶ IDLE
```

Battery additionally tracks a **mode** (sub-state) independently of the lifecycle state:

```
BatteryStorage._state  →  IDLE | STARTING | RUNNING | STOPPING | FAULT  (from BaseAsset)
BatteryStorage._mode   →  IDLE | CHARGING | DISCHARGING                  (battery-specific)
```

`start_charging()` and `start_discharging()` require `_state == RUNNING`. The mode is reset to IDLE on `stop()` and `set_idle()`.

---

## Telemetry Message Format

Every telemetry message includes a `message_id` (UUID v4) generated at publish time. The same ID is preserved across retry attempts, enabling downstream deduplication.

**Asset module telemetry (solar example):**
```json
{
  "asset_id": "solar-01",
  "asset_type": "solar_inverter",
  "state": "RUNNING",
  "power_output_kw": 45.9,
  "irradiance_w_m2": 850.0,
  "efficiency": 0.1766,
  "temperature_c": 42.0,
  "fault_code": null,
  "timestamp": "2026-03-01T07:54:34.370Z",
  "message_id": "3f8a1c2d-4e5b-6789-abcd-ef0123456789"
}
```

**Controller aggregated telemetry (sent to `$upstream`):**
```json
{
  "device_id": "edge-device-01",
  "timestamp": "2026-03-01T07:54:44.318Z",
  "total_generation_kw": 45.9,
  "total_consumption_kw": 50.0,
  "grid_balance_kw": -4.1,
  "asset_count": 3,
  "assets": {
    "solar-01":   {"state": "RUNNING",  "power_kw": 45.9,  "asset_type": "solar_inverter"},
    "battery-01": {"state": "RUNNING",  "power_kw": -50.0, "asset_type": "battery_storage"},
    "boiler-01":  {"state": "IDLE",     "power_kw": 0.0,   "asset_type": "industrial_boiler"}
  },
  "alerts": [],
  "message_id": "7a2b3c4d-5e6f-7890-bcde-f01234567890"
}
```

Grid balance convention: positive = surplus (generation > consumption), negative = deficit.

---

## Grid Balancing Logic

The controller runs a rule-based balancing loop every `reporting_interval_s` seconds:

| Condition | Action |
|-----------|--------|
| `grid_balance > surplus_threshold_kw` | Command battery to charge at `min(surplus, 50 kW)` |
| `grid_balance < -surplus_threshold_kw` | Command battery to discharge at `min(deficit, 50 kW)` |
| Any asset in `FAULT` state | Log critical alert (escalation is external) |

Alert codes in aggregated telemetry: `GRID_SURPLUS`, `GRID_DEFICIT`, `ASSET_FAULT`.

---

## Engineering Design

### Reliability — Retry with Exponential Backoff

All network calls (telemetry publish, twin update) are wrapped with `with_retry()` from `shared/iot_edge_base/retry.py`.

**Strategy:** Full jitter — `delay = random.uniform(0, min(max_delay, base * 2^attempt))`

This is the AWS-recommended approach to prevent **thundering herd**: when many edge devices reconnect simultaneously, randomized delays spread the load over time instead of creating synchronized retry storms.

```python
await with_retry(
    lambda: client.send_message_to_output(telemetry.to_dict(), output_name="telemetry"),
    max_attempts=3,
    base_delay_s=1.0,
    max_delay_s=30.0,
    label="solar.send_telemetry",
)
```

Retries are **idempotent**:
- `update_reported_properties` — Azure IoT Hub twin is a state store (last-write-wins merge); sending the same patch twice has no side effects
- `send_message_to_output` — each message carries a stable `message_id` (UUID v4); downstream consumers can use it for deduplication

### Runtime Type Safety — Pydantic

Two layers of Pydantic validation protect system boundaries:

**1. Configuration (`pydantic-settings` BaseSettings)**

Each module declares its configuration as a `BaseSettings` class. Environment variables are read and validated at startup — a misconfigured deployment fails immediately with a precise error instead of silently misbehaving at runtime.

```python
class BatteryConfig(BaseSettings):
    asset_id: str = "battery-01"
    capacity_kwh: float = Field(500.0, gt=0)
    initial_soc: float = Field(0.5, ge=0.0, le=1.0)   # 0–100% SoC
    telemetry_interval_s: int = Field(10, ge=1)

cfg = BatteryConfig()  # reads env vars, validates constraints
```

**2. DirectMethod payloads (Pydantic BaseModel)**

Payload schemas are defined in `src/schemas.py` in each module. Validation happens at the `handle_method` boundary, before any asset driver code runs.

```python
# modules/battery-module/src/schemas.py
class StartChargingPayload(BaseModel):
    power_kw: float | None = Field(None, gt=0.0)  # optional — defaults to max_power_kw

# main.py dispatch handler
parsed = StartChargingPayload.model_validate(payload)
# ValidationError → 400 with structured error detail
# RuntimeError (wrong state) → 409
```

### SOLID Design Principles

| Principle | Implementation |
|-----------|----------------|
| **SRP** | Config, payload schemas, telemetry, and state machine are separate classes/files |
| **OCP** | Dispatch dict in `_build_dispatch()` — adding a method means adding one entry, no existing code changes |
| **LSP** | `BatteryStorage` never overrides `BaseAsset.state`; CHARGING/DISCHARGING live in `.mode` (sub-state) |
| **DIP** | All env var reading is isolated in `Config` classes; `BaseEdgeClient` is an abstract interface with Azure and local MQTT implementations |

---

## Infrastructure (Azure — Bicep)

```
infra/
├── main.bicep              # Orchestrates all modules
├── main.bicepparam         # Environment parameters
└── modules/
    ├── iot-hub.bicep        # S1/S2 IoT Hub + consumer groups
    ├── container-registry.bicep  # ACR for module images
    ├── log-analytics.bicep  # Log Analytics Workspace (30/90 day retention)
    └── monitor.bicep        # Diagnostic settings + email alerts
```

**Deployed resources:**

| Resource | Purpose |
|----------|---------|
| Azure IoT Hub (S1/S2) | Device management, telemetry ingestion, direct methods |
| Azure Container Registry | Store Docker images for edge modules |
| Log Analytics Workspace | Central log aggregation |
| Azure Monitor Alerts | Alert on asset faults, IoT Hub errors |

---

## Repository Structure

```
energy-edge-controller/
├── modules/
│   ├── solar-module/
│   │   ├── main.py              # Entry point: lifecycle, handlers, telemetry loop
│   │   ├── src/
│   │   │   ├── config.py        # SolarConfig — pydantic-settings BaseSettings
│   │   │   ├── inverter.py      # SolarInverter state machine + physics simulation
│   │   │   ├── schemas.py       # SetOutputPayload — boundary validation
│   │   │   └── simulator.py     # Irradiance model
│   │   ├── tests/
│   │   │   └── unit/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── battery-module/
│   │   ├── main.py
│   │   ├── src/
│   │   │   ├── config.py        # BatteryConfig
│   │   │   ├── battery.py       # BatteryStorage: state machine + SoC simulation
│   │   │   └── schemas.py       # StartChargingPayload, StartDischargingPayload
│   │   └── tests/
│   │       └── unit/
│   │           ├── test_battery.py
│   │           └── test_retry.py
│   ├── boiler-module/
│   │   ├── main.py
│   │   └── src/
│   │       ├── config.py        # BoilerConfig
│   │       ├── boiler.py        # Boiler: temperature control + PID-like simulation
│   │       └── schemas.py       # SetTemperaturePayload (ge=40, le=120)
│   ├── controller-module/
│   │   ├── main.py
│   │   └── src/
│   │       ├── config.py        # ControllerConfig (reads IOTEDGE_DEVICEID)
│   │       ├── aggregator.py    # Telemetry aggregation + grid alerts
│   │       ├── dispatcher.py    # Command dispatch to asset modules
│   │       └── registry.py      # In-memory asset state registry
│   └── telemetry-module/
├── shared/
│   └── iot_edge_base/           # Shared package — installed in every module via pip
│       ├── asset.py             # BaseAsset, AssetState — lifecycle state machine
│       ├── client.py            # BaseEdgeClient: AzureEdgeClient + LocalMqttEdgeClient
│       ├── retry.py             # with_retry() — exponential backoff with full jitter
│       └── telemetry.py         # BaseTelemetry
├── deployment/
│   ├── deployment.template.json # IoT Edge deployment manifest
│   ├── docker-compose.yml       # Local dev — Mosquitto MQTT broker + all modules
│   └── mosquitto.conf
├── infra/
│   ├── main.bicep
│   ├── main.bicepparam
│   └── modules/
├── pipelines/
│   ├── templates/
│   │   ├── build-module.yml
│   │   └── test-module.yml
│   └── stages/
│       ├── test.yml
│       ├── build.yml
│       └── deploy.yml
├── azure-pipelines.yml
├── ARCHITECTURE.md
├── CICD.md
└── README.md
```

---

## CI/CD Pipeline (Azure DevOps)

See [CICD.md](CICD.md) for the full pipeline documentation.

```
PR / push to main
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Test Stage  │────▶│  Build Stage │────▶│  Deploy Stage   │
│  (always)   │     │              │     │  (main only)    │
│             │     │              │     │                 │
│ pytest      │     │ az acr build │     │ az iot edge     │
│ coverage    │     │ (no local    │     │ set-modules     │
│ ruff lint   │     │  Docker      │     │                 │
│ (parallel   │     │  daemon)     │     │ Manual approval │
│  per module)│     │ (parallel    │     │ for prod        │
│             │     │  per module) │     │                 │
└─────────────┘     └──────────────┘     └─────────────────┘
```
