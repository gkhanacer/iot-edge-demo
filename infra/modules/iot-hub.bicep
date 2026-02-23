@description('Name of the IoT Hub.')
param iotHubName string

@description('Azure region.')
param location string

@description('SKU name. S1 is sufficient for most IoT workloads.')
@allowed(['S1', 'S2', 'S3'])
param skuName string = 'S1'

@description('Number of IoT Hub units.')
param skuCapacity int = 1

@description('Resource ID of the Log Analytics workspace for diagnostic settings.')
param workspaceId string

resource iotHub 'Microsoft.Devices/IotHubs@2023-06-30' = {
  name: iotHubName
  location: location
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  properties: {
    // Consumer groups allow multiple independent readers
    eventHubEndpoints: {
      events: {
        retentionTimeInDays: 1
        partitionCount: 4
      }
    }
    routing: {
      fallbackRoute: {
        name: '$fallback'
        source: 'DeviceMessages'
        condition: 'true'
        endpointNames: ['events']
        isEnabled: true
      }
    }
    messagingEndpoints: {
      fileNotifications: {
        lockDurationAsIso8601: 'PT1M'
        ttlAsIso8601: 'PT1H'
        maxDeliveryCount: 10
      }
    }
    enableFileUploadNotifications: false
    cloudToDevice: {
      maxDeliveryCount: 10
      defaultTtlAsIso8601: 'PT1H'
      feedback: {
        lockDurationAsIso8601: 'PT1M'
        ttlAsIso8601: 'PT1H'
        maxDeliveryCount: 10
      }
    }
  }
}

// Dedicated consumer group for telemetry processing
resource telemetryConsumerGroup 'Microsoft.Devices/IotHubs/eventHubEndpoints/ConsumerGroups@2023-06-30' = {
  name: '${iotHubName}/built-in-endpoint/telemetry-processor'
  dependsOn: [iotHub]
}

// Diagnostic settings → Log Analytics
resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'iot-hub-diagnostics'
  scope: iotHub
  properties: {
    workspaceId: workspaceId
    logs: [
      { category: 'Connections'; enabled: true }
      { category: 'DeviceTelemetry'; enabled: true }
      { category: 'C2DCommands'; enabled: true }
      { category: 'DirectMethods'; enabled: true }
      { category: 'TwinQueries'; enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics'; enabled: true }
    ]
  }
}

output iotHubId string = iotHub.id
output iotHubName string = iotHub.name
output iotHubHostName string = iotHub.properties.hostName
output eventHubCompatibleEndpoint string = iotHub.properties.eventHubEndpoints.events.endpoint
