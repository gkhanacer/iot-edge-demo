# Energy Edge Controller

A Python-based Azure IoT Edge platform that manages and steers decentralized energy assets — solar inverters, battery storage systems, and industrial boilers. Each asset runs as an independent IoT Edge module. A central controller aggregates telemetry, applies grid-balancing logic, and reports to Azure IoT Hub.

> Built to demonstrate production-ready IoT edge software engineering: testable Python, modular Docker architecture, Azure IoT Edge module communication, CI/CD via Azure DevOps, and infrastructure-as-code with Bicep.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design, including:
- Module communication patterns (message routing, direct methods, module twin)
- Data flow diagrams
- Infrastructure overview

```
Azure IoT Hub ◄──── controller-module ──── edgeHub ──── solar-module
                                                    └─── battery-module
                                                    └─── boiler-module
                           │
                    telemetry-module (Prometheus /metrics)
```

---

## Modules

| Module | Role | Key commands |
|--------|------|-------------|
| `solar-module` | Solar inverter driver + simulator | `start`, `stop`, `set_output`, `reset` |
| `battery-module` | Battery storage driver | `start_charging`, `start_discharging`, `stop`, `reset` |
| `boiler-module` | Industrial boiler driver | `start`, `stop`, `set_temperature`, `reset` |
| `controller-module` | Telemetry aggregation, grid balancing, cloud reporting | — |
| `telemetry-module` | Prometheus metrics exporter | `GET /metrics`, `GET /healthz` |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Azure CLI (for infra deployment)
- Azure IoT Edge runtime (for production deployment)

### Run locally

```bash
cd deployment
docker compose up --build
```

This starts all modules connected via a local Mosquitto MQTT broker.
Prometheus metrics are available at `http://localhost:9090/metrics`.

### Run tests

```bash
# Install shared package
pip install -e shared/

# Run tests for a single module
cd modules/solar-module
pip install -r requirements-dev.txt
pytest tests/ --cov=src --cov-report=term-missing

# Run all modules' tests from repo root
for module in solar-module battery-module boiler-module controller-module; do
  echo "=== $module ==="
  PYTHONPATH=modules/$module pytest modules/$module/tests -v
done
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
Build & Push (parallel)         → docker build + ACR push
  │
  ▼
Deploy Infrastructure (main)    → az deployment group create (Bicep)
  │
  ▼
Deploy to Edge (main)           → AzureIoTEdge@2 task
```

**Required pipeline variables:**

| Variable | Description |
|----------|-------------|
| `AZURE_SERVICE_CONNECTION` | Azure DevOps service connection name |
| `DOCKER_SERVICE_CONNECTION` | ACR service connection name |
| `ACR_LOGIN_SERVER` | ACR hostname (e.g. `acrenergeyedgedev.azurecr.io`) |
| `RESOURCE_GROUP` | Azure resource group name |
| `IOT_HUB_NAME` | IoT Hub name |
| `EDGE_DEVICE_ID` | Registered IoT Edge device ID |
| `ENVIRONMENT` | `dev` / `staging` / `prod` |

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
│   ├── solar-module/        # Solar inverter: state machine + irradiance simulator
│   ├── battery-module/      # Battery BESS: charge/discharge state machine
│   ├── boiler-module/       # Industrial boiler: temperature control
│   ├── controller-module/   # Orchestrator: aggregation, balancing, cloud reporting
│   └── telemetry-module/    # Prometheus exporter
├── shared/
│   └── iot_edge_base/       # Shared base classes installed in each module
├── deployment/
│   ├── deployment.template.json   # IoT Edge deployment manifest
│   ├── docker-compose.yml         # Local development
│   └── mosquitto.conf
├── infra/                         # Bicep infrastructure
│   ├── main.bicep
│   └── modules/
├── pipelines/                     # Azure DevOps pipeline templates
├── azure-pipelines.yml
├── ARCHITECTURE.md
└── README.md
```
