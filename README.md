# Energy Edge Controller

A Python-based Azure IoT Edge platform that manages and steers decentralized energy assets — solar inverters, battery storage systems, and industrial boilers. Each asset runs as an independent IoT Edge module. A central controller aggregates telemetry, applies grid-balancing logic, and reports to Azure IoT Hub.

> Built to demonstrate production-ready IoT edge software engineering: testable Python, modular Docker architecture, Azure IoT Edge module communication, CI/CD via Azure DevOps, and infrastructure-as-code with Bicep.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design, including:
- Module communication patterns (message routing, direct methods, module twin)
- Data flow diagrams
- Engineering design decisions (SOLID, retry, runtime validation)
- Infrastructure overview

```
Azure IoT Hub ◄──── controller-module ──── edgeHub ──── solar-module
                                                    └─── battery-module
                                                    └─── boiler-module
                           │
                    telemetry-module (Azure Monitor metrics)
```

---

## Modules

| Module | Role | Direct method commands |
|--------|------|----------------------|
| `solar-module` | Solar inverter driver + irradiance simulator | `start`, `stop`, `set_output`, `reset` |
| `battery-module` | Battery BESS driver — charge/discharge state machine | `start`, `stop`, `set_idle`, `start_charging`, `start_discharging`, `reset` |
| `boiler-module` | Industrial boiler driver — temperature control | `start`, `stop`, `set_temperature`, `reset` |
| `controller-module` | Telemetry aggregation, grid balancing, cloud reporting | — |
| `telemetry-module` | Azure Monitor metrics exporter | — |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ with [Poetry](https://python-poetry.org/)
- Azure CLI (for infrastructure deployment)
- Azure IoT Edge runtime (for production deployment)

### Run locally

```bash
cd deployment
docker compose up --build -d
```

This starts all modules connected via a local Mosquitto MQTT broker. See **[Local Testing](#local-testing)** below for how to send commands and inspect results.

### Run tests

Tests use [Poetry](https://python-poetry.org/) for dependency management. Each module has its own isolated virtualenv.

```bash
# Run tests for a single module (e.g. battery)
cd modules/battery-module
EDGE_MODE=local poetry run pytest tests/ -v

# Run all modules from repo root
for module in solar-module battery-module boiler-module controller-module; do
  echo "=== $module ==="
  (cd modules/$module && EDGE_MODE=local poetry run pytest tests/ -v)
done
```

---

## Local Testing

The full stack runs locally using Docker Compose + Mosquitto MQTT. In local mode all IoT Hub communication (direct methods, twin updates, telemetry) is simulated over MQTT with the same topic conventions used by the Azure IoT Edge runtime.

### 1. Start the stack

```bash
cd deployment
docker compose up --build -d
```

Verify all containers are healthy:

```bash
docker compose ps
```

Expected output — all services `Up`:
```
NAME                             STATUS
deployment-mosquitto-1           Up
deployment-solar-module-1        Up
deployment-battery-module-1      Up
deployment-boiler-module-1       Up
deployment-controller-module-1   Up
deployment-telemetry-module-1    Up
```

### 2. Follow live logs

```bash
# All modules
docker compose logs -f

# Single module
docker compose logs -f solar-module
docker compose logs -f controller-module
```

After startup you will see each module publishing IDLE telemetry every 5 seconds and the controller reporting `generation_kw: 0, consumption_kw: 0, balance_kw: 0`.

### 3. Send commands via MQTT

Commands are sent as **direct method calls** over MQTT. The topic format is:

```
edge/{module-id}/methods/{method-name}
```

The payload is JSON. Include `_request_id` to correlate the response (which arrives on `edge/{module-id}/methods/response/{request_id}`).

Use `mosquitto_pub` inside the running Mosquitto container:

```bash
# Shortcut — publish a direct method
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/{module-id}/methods/{method-name}" \
  -m '{...payload...}'
```

#### Subscribe to responses (optional, in a separate terminal)

```bash
docker exec deployment-mosquitto-1 \
  mosquitto_sub -h localhost -t "edge/+/methods/response/#" -v
```

### 4. Full scenario: start assets and observe grid balance

Run the commands below in sequence. After each command a response arrives on the response topic within ~1 second.

```bash
# --- Start the solar inverter ---
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/solar-module/methods/start" \
  -m '{"_request_id":"req-1"}'
# → 200 {"status": "ok"}

# --- Start the battery ---
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/battery-module/methods/start" \
  -m '{"_request_id":"req-2"}'
# → 200 {"status": "ok"}

# --- Charge battery at 50 kW ---
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/battery-module/methods/start_charging" \
  -m '{"_request_id":"req-3","power_kw":50.0}'
# → 200 {"status": "ok"}

# --- Start the boiler ---
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/boiler-module/methods/start" \
  -m '{"_request_id":"req-4"}'
# → 200 {"status": "ok"}

# --- Set boiler target temperature ---
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/boiler-module/methods/set_temperature" \
  -m '{"_request_id":"req-5","target_celsius":85.0}'
# → 200 {"status": "ok"}
```

After ~10 seconds the controller log will show:

```json
{"generation_kw": 45.9, "consumption_kw": 50.0, "balance_kw": -4.1, "alerts": 0,
 "event": "Grid report", "level": "info"}
```

### 5. Test payload validation (400 responses)

Invalid payloads are rejected before reaching the asset driver. The response body contains structured Pydantic error detail.

```bash
# power_kw must be > 0
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/battery-module/methods/start_charging" \
  -m '{"_request_id":"req-bad-1","power_kw":-99.0}'
# → 400 {"error": [{"type":"greater_than","loc":["power_kw"],
#                   "msg":"Input should be greater than 0","input":-99.0}]}

# target_celsius must be in [40, 120]
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/boiler-module/methods/set_temperature" \
  -m '{"_request_id":"req-bad-2","target_celsius":200.0}'
# → 400 {"error": [{"type":"less_than_equal","loc":["target_celsius"],
#                   "msg":"Input should be less than or equal to 120","input":200.0}]}

# target_kw must be ≥ 0
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/solar-module/methods/set_output" \
  -m '{"_request_id":"req-bad-3","target_kw":-10.0}'
# → 400 {"error": [{"type":"greater_than_equal","loc":["target_kw"],
#                   "msg":"Input should be greater than or equal to 0","input":-10.0}]}
```

### 6. Test state-machine guards (409 responses)

Commands that are valid payload-wise but illegal in the current state return 409:

```bash
# Try to charge while battery is not RUNNING (e.g. after reset)
docker exec deployment-mosquitto-1 mosquitto_pub \
  -h localhost \
  -t "edge/battery-module/methods/start_charging" \
  -m '{"_request_id":"req-409","power_kw":50.0}'
# → 409 {"error": "Cannot start charging: battery is not RUNNING"}
```

### 7. Stop the stack

```bash
docker compose down
```

---

## Infrastructure (Azure)

Deploy all Azure resources with Bicep:

```bash
# Create resource group
az group create --name rg-energy-edge-dev --location westeurope

# Deploy
az deployment group create \
  --resource-group rg-energy-edge-dev \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam
```

**Deployed resources:**
- Azure IoT Hub (S1) with Log Analytics diagnostic settings
- Azure Container Registry
- Log Analytics Workspace
- Azure Monitor alerts (connectivity failure, telemetry drop)

---

## CI/CD (Azure DevOps)

The pipeline (`azure-pipelines.yml`) runs four stages:

```
push/PR
  │
  ▼
Test (parallel per module)      → pytest + coverage per module
  │
  ▼
Build & Push (parallel)         → az acr build + ACR push (no local Docker daemon needed)
  │
  ▼
Deploy Infrastructure (main)    → az deployment group create (Bicep)
  │
  ▼
Deploy to Edge (main)           → AzureIoTEdge@2 task
```

See [CICD.md](CICD.md) for full pipeline documentation.

**Required pipeline variables:**

| Variable | Description |
|----------|-------------|
| `AZURE_SERVICE_CONNECTION` | Azure DevOps service connection name |
| `ACR_LOGIN_SERVER` | ACR hostname (e.g. `acrenergeyedgedev.azurecr.io`) |
| `RESOURCE_GROUP` | Azure resource group name |
| `IOT_HUB_NAME` | IoT Hub name |
| `EDGE_DEVICE_ID` | Registered IoT Edge device ID |

---

## Module Communication

Three mechanisms are used (see [ARCHITECTURE.md](ARCHITECTURE.md) for details):

| Mechanism | Used for | Direction |
|-----------|----------|-----------|
| **Message Routing** (edgeHub) | Telemetry | asset → controller → cloud |
| **Direct Methods** | Commands | controller → asset |
| **Module Twin** | Configuration | cloud/operator → asset |

---

## Project Structure

```
energy-edge-controller/
├── modules/
│   ├── solar-module/
│   │   ├── main.py              # Entry point: lifecycle, handlers, telemetry loop
│   │   ├── src/
│   │   │   ├── config.py        # SolarConfig — pydantic-settings BaseSettings
│   │   │   ├── inverter.py      # SolarInverter state machine + physics
│   │   │   ├── schemas.py       # Pydantic payload schemas (SetOutputPayload)
│   │   │   └── simulator.py     # Irradiance model
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── battery-module/
│   │   ├── main.py
│   │   ├── src/
│   │   │   ├── config.py        # BatteryConfig
│   │   │   ├── battery.py       # BatteryStorage state machine + SoC simulation
│   │   │   └── schemas.py       # StartChargingPayload, StartDischargingPayload
│   │   └── tests/
│   ├── boiler-module/
│   │   ├── main.py
│   │   └── src/
│   │       ├── config.py        # BoilerConfig
│   │       ├── boiler.py        # Boiler state machine + temperature simulation
│   │       └── schemas.py       # SetTemperaturePayload
│   ├── controller-module/
│   │   ├── main.py
│   │   └── src/
│   │       ├── config.py        # ControllerConfig
│   │       ├── aggregator.py    # Telemetry aggregation + grid alerts
│   │       ├── dispatcher.py    # Command dispatch to asset modules
│   │       └── registry.py      # Asset state registry
│   └── telemetry-module/
├── shared/
│   └── iot_edge_base/           # Shared package installed in every module
│       ├── asset.py             # BaseAsset, AssetState — state machine base class
│       ├── client.py            # BaseEdgeClient (Azure + local MQTT implementations)
│       ├── retry.py             # with_retry() — exponential backoff with full jitter
│       └── telemetry.py         # BaseTelemetry
├── deployment/
│   ├── deployment.template.json # IoT Edge deployment manifest
│   ├── docker-compose.yml       # Local development
│   └── mosquitto.conf
├── infra/                       # Bicep infrastructure
│   ├── main.bicep
│   └── modules/
├── pipelines/                   # Azure DevOps pipeline templates
├── azure-pipelines.yml
├── ARCHITECTURE.md
├── CICD.md
└── README.md
```
