@description('Resource ID of the IoT Hub to monitor.')
param iotHubId string

@description('Resource ID of the Log Analytics workspace.')
param workspaceId string

@description('Email address for alert notifications.')
param alertEmailAddress string

// Action group: sends email on alerts
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-energy-edge-ops'
  location: 'global'
  properties: {
    groupShortName: 'EdgeOps'
    enabled: true
    emailReceivers: [
      {
        name: 'OpsTeam'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

// Alert: IoT Hub connectivity failures
resource connectivityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-iot-hub-connectivity-failures'
  location: 'global'
  properties: {
    description: 'Fires when IoT Hub reports device connectivity failures'
    severity: 2
    enabled: true
    scopes: [iotHubId]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ConnectivityFailures'
          metricName: 'd2c.telemetry.ingress.allProtocol'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Alert: High D2C message count (anomaly detection placeholder)
resource telemetryVolumeAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-iot-hub-telemetry-drop'
  location: 'global'
  properties: {
    description: 'Fires when D2C telemetry volume drops to zero — potential edge disconnection'
    severity: 1
    enabled: true
    scopes: [iotHubId]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT30M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'TelemetryDrop'
          metricName: 'd2c.telemetry.ingress.success'
          operator: 'LessThan'
          threshold: 1
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output actionGroupId string = actionGroup.id
