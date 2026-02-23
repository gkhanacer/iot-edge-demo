@description('Name of the Azure Container Registry (alphanumeric, 5–50 chars).')
param registryName string

@description('Azure region.')
param location string

@description('SKU: Basic is sufficient for dev/test; Standard for production.')
@allowed(['Basic', 'Standard', 'Premium'])
param skuName string = 'Standard'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: skuName
  }
  properties: {
    adminUserEnabled: false   // Use managed identity or service principal
    publicNetworkAccess: 'Enabled'
  }
}

output acrId string = acr.id
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
