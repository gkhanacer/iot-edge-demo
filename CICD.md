# CI/CD Pipeline

This project uses **Azure DevOps Pipelines** to test, build, provision infrastructure, and deploy IoT Edge modules.

---

## Pipeline Overview

```
                    ┌─────────────────────────────────┐
                    │           Test (always)          │
                    │  5 modules in parallel + lint    │
                    └──────────────┬──────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
              ▼                    ▼                     ▼
       Infra — Dev          Infra — Test          Infra — Prod
      (if deployInfraDev)  (if deployInfraTest)  (if deployInfraProd)
              │ independent         │ independent          │ independent
              └────────────────────┘─────────────────────┘
                                   │
                    ┌──────────────┘
                    ▼
             Build & Push (once)
             shared ACR, 5 modules parallel
                    │
         ┌──────────┴──────────┐
         │                     │
         ▼                     │
   Deploy — Dev          (waits for Dev)
  (if deployDevEdge)           │
         │                     ▼
         │               Deploy — Test
         │              (if deployTestEdge)
         │                     │
         │                     ▼ (waits for Test)
         │               Deploy — Prod
         └──────────────▶ (if deployProdEdge)
                          [manual approval: iot-edge-prod]
```

---

## Runtime Parameters

When clicking **Run pipeline**, the following options are shown:

### Infrastructure provisioning
Each environment is independent — provision any combination without affecting deployments.

| Parameter | Default | Description |
|---|---|---|
| `deployInfraDev` | `false` | Provision Azure resources for dev environment |
| `deployInfraTest` | `false` | Provision Azure resources for test environment |
| `deployInfraProd` | `false` | Provision Azure resources for prod environment |

### Edge deployments
Follow a promotion chain: dev → test → prod (each waits for the previous if selected).

| Parameter | Default | Description |
|---|---|---|
| `deployDevEdge` | `true` | Deploy to dev IoT Edge |
| `devDeployTarget` | `single` | `single` = specific device · `all` = all devices with `tags.env='dev'` |
| `devDeviceId` | `$(EDGE_DEVICE_ID_DEV)` | Device ID when target=single (overrideable at run time) |
| `deployTestEdge` | `false` | Deploy to test IoT Edge |
| `testDeployTarget` | `single` | |
| `testDeviceId` | `$(EDGE_DEVICE_ID_TEST)` | |
| `deployProdEdge` | `false` | Deploy to prod IoT Edge |
| `prodDeployTarget` | `single` | |
| `prodDeviceId` | `$(EDGE_DEVICE_ID_PROD)` | |

**Example scenarios:**

```
# Deploy to a specific dev device
deployDevEdge=true, devDeployTarget=single, devDeviceId=device-001

# Deploy to ALL dev devices (IoT Hub automatic deployment)
deployDevEdge=true, devDeployTarget=all

# Promote dev → test → prod (prod requires manual approval)
deployDevEdge=true, deployTestEdge=true, deployProdEdge=true

# Only provision prod infrastructure
deployInfraProd=true  (everything else unchecked)
```

---

## Stages

### Stage 1 — Test

**Files:** [`azure-pipelines.yml`](azure-pipelines.yml) · [`pipelines/templates/test-module.yml`](pipelines/templates/test-module.yml)

Always runs on every push and PR.

**TestModules** (matrix — 5 parallel jobs):
1. Install Python 3.12 + Poetry 1.8.3
2. `pip install` the shared `iot_edge_base` package
3. `poetry install` module dependencies
4. `pytest` with coverage — results published to Azure DevOps

**LintAndTypeCheck:**
- **Ruff** — fast linting across all modules and shared package
- **Mypy** — static type checking per module (non-blocking)

---

### Stage 2 — Infra (Dev / Test / Prod)

**Files:** [`infra/main.bicep`](infra/main.bicep) · [`infra/main.bicepparam`](infra/main.bicepparam)

Each environment is an **independent** stage — runs only when the corresponding `deployInfra*` parameter is checked. They all depend on Test passing, but not on each other or on edge deployments.

Azure resources deployed per environment:

| Resource | Name pattern | Notes |
|---|---|---|
| Resource Group | `$(RESOURCE_GROUP_DEV/TEST/PROD)` | Created with `az group create` |
| IoT Hub | `iothub-energy-edge-{env}` | S1 (dev/test) · S2 (prod) |
| Azure Container Registry | `acrenergy-edge{env}` | Basic (dev/test) · Standard (prod) |
| Log Analytics Workspace | `log-energy-edge-{env}` | 30 days (dev/test) · 90 days (prod) |
| Azure Monitor Alert | — | Email alert on IoT Hub errors |

Uses `deployment:` job type → deployment history is tracked per environment in Azure DevOps.

---

### Stage 3 — Build & Push

**File:** [`pipelines/templates/build-module.yml`](pipelines/templates/build-module.yml)

Runs **once**, builds all 5 modules in parallel. Images are pushed to the **shared `$(ACR_LOGIN_SERVER)`** and reused across all environments — no rebuild per env.

**Image tagging:**

| Branch | Tag | `latest` pushed? |
|---|---|---|
| `main` | `3.14` (from `VERSION` file) | Yes |
| any other | `3.14-abc12345` (version + 8-char SHA) | No |

Uses `az acr build` — build runs on ACR's cloud agent, no Docker daemon needed on the pipeline agent. Authenticates via the existing `azure-prod-sc` service connection.

---

### Stage 4 — Deploy (Dev / Test / Prod)

**File:** [`deployment/deployment.template.json`](deployment/deployment.template.json)

Deploy stages depend only on **Build** and the **previous environment's deploy** (for promotion chain). They do **not** depend on Infra stages — infrastructure is managed independently.

**Single device** (`target=single`):
```bash
az iot edge set-modules \
  --hub-name <IOT_HUB_NAME> \
  --device-id <devDeviceId> \
  --content deployment/deployment.template.json
```

**All devices** (`target=all`):
```bash
az iot edge deployment create \
  --hub-name <IOT_HUB_NAME> \
  --content deployment/deployment.template.json \
  --deployment-id "deploy-dev-<BuildId>" \
  --target-condition "tags.env='dev'" \
  --priority 10
```

The prod stage uses the **`iot-edge-prod`** Azure DevOps environment, which can be configured with a **manual approval gate** in Azure DevOps → Environments → iot-edge-prod → Approvals.

---

## Pipeline Variables

Set these in Azure DevOps → Pipelines → Variables:

| Variable | Example | Description |
|---|---|---|
| `AZURE_SERVICE_CONNECTION` | `azure-prod-sc` | ARM service connection (used by all Azure tasks) |
| `ACR_LOGIN_SERVER` | `acrenergy-edgedev.azurecr.io` | Shared ACR for all environments |
| `ALERT_EMAIL` | `ops@example.com` | Azure Monitor alert recipient |
| `RESOURCE_GROUP_DEV` | `rg-energy-edge-dev` | Dev resource group |
| `RESOURCE_GROUP_TEST` | `rg-energy-edge-test` | Test resource group |
| `RESOURCE_GROUP_PROD` | `rg-energy-edge-prod` | Prod resource group |
| `IOT_HUB_NAME_DEV` | `iothub-energy-edge-dev` | Dev IoT Hub |
| `IOT_HUB_NAME_TEST` | `iothub-energy-edge-test` | Test IoT Hub |
| `IOT_HUB_NAME_PROD` | `iothub-energy-edge-prod` | Prod IoT Hub |
| `EDGE_DEVICE_ID_DEV` | `device-dev-01` | Default device ID for dev (overrideable at run time) |
| `EDGE_DEVICE_ID_TEST` | `device-test-01` | Default device ID for test |
| `EDGE_DEVICE_ID_PROD` | `device-prod-01` | Default device ID for prod |

---

## Azure DevOps Environments

Three environments must be created in Azure DevOps → Pipelines → Environments:

| Environment | Approval |
|---|---|
| `iot-edge-dev` | None (auto) |
| `iot-edge-test` | None (auto) |
| `iot-edge-prod` | Manual approval recommended |

---

## Service Connection

A single Azure Resource Manager service connection **`azure-prod-sc`** is used for all Azure interactions (Bicep deploy, ACR build, IoT Edge deploy). Backed by a Service Principal with `Contributor` role on the subscription.

---

## Version File

[`VERSION`](VERSION) at the repo root controls the base image version. To bump the version, update this file and commit — the next build will use the new tag.

```
3.14
```

---

## Local Development

The full stack runs locally without Azure using Docker Compose + Mosquitto MQTT:

```bash
cd deployment
docker compose up --build
```

See [`deployment/docker-compose.yml`](deployment/docker-compose.yml).
