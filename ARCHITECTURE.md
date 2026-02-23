# Energy Edge Controller — Architecture

## Overview

An Azure IoT Edge–based platform that manages and steers decentralized energy assets (solar inverters, batteries, industrial boilers) from an edge device. Each asset type runs as an independent IoT Edge module. A central controller module aggregates telemetry, makes control decisions, and reports to Azure IoT Hub in the cloud.

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
│  │  - C2D commands        │    │  - Alerts            │                             │
│  │  - Direct methods      │    │  - Dashboards        │                             │
│  └───────────┬────────────┘    └──────────────────────┘                             │
│              │ Diagnostic Settings (auto-forwarded)                                  │
└──────────────┼───────────────────────────────────────────────────────────────────── ┘
               │ MQTT/AMQP over TLS ($upstream)
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
│  │ Simulator  │  │ Simulator  │  │ Simulator  │  │  - Aggregates telemetry       │ │
│  │ + Driver   │  │ + Driver   │  │ + Driver   │  │  - Grid balance decisions     │ │
│  │            │  │            │  │            │  │  - Issues direct method calls │ │
│  │ Prometheus │  │ Prometheus │  │ Prometheus │  │  - Reports to IoT Hub         │ │
│  │ /metrics   │  │ /metrics   │  │ /metrics   │  │                               │ │
│  └────────────┘  └────────────┘  └────────────┘  └───────────────────────────────┘ │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐ │
│  │                         telemetry-module                                        │ │
│  │   Scrapes Prometheus /metrics from all modules → Azure Monitor agent            │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Module Communication

Azure IoT Edge modules communicate via **three mechanisms**, each serving a different purpose:

### 1. Message Routing (edgeHub) — Telemetry / Events

Used for fire-and-forget telemetry from asset modules to the controller. The edgeHub acts as a local message broker.

```
solar-module
  └─ output: "telemetry"
       └─▶ edgeHub routing
             └─▶ controller-module input: "telemetry"
```

**Python (sender — asset module):**
```python
msg = Message(json.dumps(telemetry_dict))
msg.content_type = "application/json"
await client.send_message_to_output(msg, "telemetry")
```

**Python (receiver — controller-module):**
```python
async def handle_input_message(message):
    data = json.loads(message.data)
    await aggregator.ingest(data)

client.on_message_received = handle_input_message
```

**deployment.template.json routing:**
```json
"solarToController": "FROM /messages/modules/solar-module/outputs/telemetry
                      INTO BrokeredEndpoint(\"/modules/controller-module/inputs/telemetry\")"
```

---

### 2. Direct Methods — Commands (Request/Response)

Used by the controller to send commands to asset modules. Has a timeout and a synchronous response.

```
controller-module
  └─▶ invoke_method("solar-module", "set_output", {"target_kw": 30})
        └─▶ solar-module handles and responds
              └─▶ {"status": "ok", "actual_kw": 30.0}
```

**Python (caller — controller-module):**
```python
response = await client.invoke_method(
    method_params={"methodName": "set_output", "payload": {"target_kw": 30.0}, "responseTimeoutInSeconds": 10},
    device_id=os.environ["IOTEDGE_DEVICEID"],
    module_id="solar-module",
)
```

**Python (handler — asset module):**
```python
async def method_handler(method_request):
    if method_request.name == "set_output":
        await inverter.set_output(method_request.payload["target_kw"])
        return MethodResponse.create_from_method_request(method_request, 200, {"status": "ok"})

client.on_method_request_received = method_handler
```

**Available commands per module:**

| Module | Method | Payload |
|--------|--------|---------|
| solar-module | `start` | — |
| solar-module | `stop` | — |
| solar-module | `set_output` | `{"target_kw": float}` |
| solar-module | `reset` | — |
| battery-module | `start_charging` | `{"power_kw": float}` |
| battery-module | `start_discharging` | `{"power_kw": float}` |
| battery-module | `stop` | — |
| boiler-module | `start` | — |
| boiler-module | `stop` | — |
| boiler-module | `set_temperature` | `{"target_celsius": float}` |

---

### 3. Module Twin — Configuration / Desired State

Used for persistent configuration. The cloud (or operator) updates desired properties; the module reacts and reports back in reported properties.

```
IoT Hub (desired properties)
  └─▶ {"max_output_kw": 80.0, "fault_threshold": 0.85}
        └─▶ solar-module twin patch handler
              └─▶ applies new config
                    └─▶ reports back: {"max_output_kw": 80.0, "state": "RUNNING"}
```

**Python:**
```python
async def twin_patch_handler(patch):
    if "max_output_kw" in patch:
        inverter.max_power_kw = float(patch["max_output_kw"])

client.on_twin_desired_properties_patch_received = twin_patch_handler
```

---

## Telemetry Message Format

All asset modules send JSON messages with this envelope:

```json
{
  "asset_id": "solar-01",
  "asset_type": "solar_inverter",
  "state": "RUNNING",
  "timestamp": "2024-06-01T12:00:00Z",
  "power_output_kw": 42.5,
  "irradiance_w_m2": 850.0,
  "efficiency": 0.178,
  "temperature_c": 42.0,
  "fault_code": null
}
```

The controller aggregates these and sends a rolled-up message to `$upstream`:

```json
{
  "device_id": "edge-device-01",
  "timestamp": "2024-06-01T12:00:00Z",
  "total_power_kw": 185.3,
  "assets": { ... },
  "grid_balance_kw": 12.5,
  "alerts": []
}
```

---

## Asset State Machine

All asset modules implement the same state machine pattern:

```
            start()
  IDLE ──────────────▶ STARTING
   ▲                       │
   │                       │ (startup complete)
   │ reset()               ▼
   │              ┌──── RUNNING
   │              │         │
   │         fault()    stop()
   │              │         │
   │              ▼         ▼
   └────────── FAULT     STOPPING ──▶ IDLE
```

---

## Infrastructure (Azure — Bicep)

```
infra/
├── main.bicep              # Orchestrates all modules
├── main.bicepparam         # Environment parameters
└── modules/
    ├── iot-hub.bicep        # S1 IoT Hub + consumer groups
    ├── container-registry.bicep  # ACR for module images
    ├── log-analytics.bicep  # Log Analytics Workspace
    └── monitor.bicep        # Diagnostic settings + alerts
```

**Deployed resources:**

| Resource | Purpose |
|----------|---------|
| Azure IoT Hub (S1) | Device management, telemetry ingestion, direct methods |
| Azure Container Registry | Store Docker images for edge modules |
| Log Analytics Workspace | Central log aggregation |
| Azure Monitor Alerts | Alert on asset faults, connectivity loss |

---

## Repository Structure

```
energy-edge-controller/
├── modules/
│   ├── solar-module/
│   │   ├── main.py
│   │   ├── src/
│   │   │   ├── inverter.py     # State machine + physics
│   │   │   └── simulator.py    # Irradiance model
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   └── integration/
│   │   ├── Dockerfile
│   │   └── requirements*.txt
│   ├── battery-module/
│   ├── boiler-module/
│   ├── controller-module/
│   │   └── src/
│   │       ├── aggregator.py   # Telemetry aggregation
│   │       ├── dispatcher.py   # Command dispatch
│   │       └── registry.py     # Asset state registry
│   └── telemetry-module/
├── shared/
│   └── iot_edge_base/          # Shared base classes (installed in each module)
│       ├── asset.py            # BaseAsset, AssetState
│       ├── client.py           # IoTClient abstraction (prod + local dev)
│       └── telemetry.py        # Base telemetry dataclass
├── deployment/
│   ├── deployment.template.json  # IoT Edge deployment manifest
│   └── docker-compose.yml        # Local development
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
└── README.md
```

---

## CI/CD Pipeline (Azure DevOps)

```
PR / push to main
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Test Stage  │────▶│  Build Stage │────▶│  Deploy Stage   │
│             │     │              │     │  (main only)    │
│ pytest      │     │ docker build │     │                 │
│ coverage    │     │ ACR push     │     │ AzureIoTEdge@2  │
│ lint        │     │             │     │ deployment      │
│ (parallel   │     │ (parallel   │     │ manifest deploy │
│  per module)│     │  per module)│     │                 │
└─────────────┘     └──────────────┘     └─────────────────┘
```

---

## Local Development

```bash
# Start all modules locally with a mock MQTT broker
docker compose -f deployment/docker-compose.yml up

# Run tests for a single module
cd modules/solar-module
pip install -r requirements-dev.txt
pytest tests/ --cov=src --cov-report=term-missing

# Deploy infrastructure
az deployment group create \
  --resource-group rg-energy-edge \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam
```
