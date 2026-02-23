@description('Short name used as a prefix/suffix for all resources.')
param projectName string = 'energy-edge'

@description('Environment: dev | staging | prod')
@allowed(['dev', 'staging', 'prod'])
param env string = 'dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Alert notification email address.')
param alertEmailAddress string

// ── Naming conventions ───────────────────────────────────────────────────────
var suffix = '${projectName}-${env}'
var iotHubName = 'iothub-${suffix}'
var acrName = replace('acr${projectName}${env}', '-', '')  // ACR names: alphanumeric only
var workspaceName = 'log-${suffix}'

// ── Log Analytics (deployed first — others depend on it) ─────────────────────
module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    workspaceName: workspaceName
    location: location
    retentionDays: env == 'prod' ? 90 : 30
  }
}

// ── IoT Hub ───────────────────────────────────────────────────────────────────
module iotHub 'modules/iot-hub.bicep' = {
  name: 'iotHub'
  params: {
    iotHubName: iotHubName
    location: location
    skuName: env == 'prod' ? 'S2' : 'S1'
    skuCapacity: 1
    workspaceId: logAnalytics.outputs.workspaceId
  }
}

// ── Azure Container Registry ─────────────────────────────────────────────────
module acr 'modules/container-registry.bicep' = {
  name: 'containerRegistry'
  params: {
    registryName: acrName
    location: location
    skuName: env == 'prod' ? 'Standard' : 'Basic'
  }
}

// ── Azure Monitor Alerts ─────────────────────────────────────────────────────
module monitor 'modules/monitor.bicep' = {
  name: 'monitor'
  params: {
    iotHubId: iotHub.outputs.iotHubId
    workspaceId: logAnalytics.outputs.workspaceId
    alertEmailAddress: alertEmailAddress
  }
}

// ── Outputs ──────────────────────────────────────────────────────────────────
output iotHubName string = iotHub.outputs.iotHubName
output iotHubHostName string = iotHub.outputs.iotHubHostName
output acrLoginServer string = acr.outputs.acrLoginServer
output logAnalyticsWorkspaceId string = logAnalytics.outputs.workspaceId
