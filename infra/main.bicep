targetScope = 'subscription'

@description('Azure region for all resources')
param location string = 'centralindia'

@description('Short name prefix')
param appName string = 'gridlock'

@description('Environment tag')
param environment string = 'prod'

@description('Container image tag to deploy')
param imageTag string = 'latest'

@description('Whether to deploy the Container App (set to false if the image does not exist yet)')
param deployContainerApp bool = true

@secure()
param mapplsRestKey string = ''

@secure()
param mapplsClientId string = ''

@secure()
param mapplsClientSecret string = ''

@description('Whether to create role assignments (requires User Access Administrator / Owner permissions)')
param createRoleAssignments bool = true

// ============================================================================
// Resource Group
// ============================================================================
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${appName}-${environment}'
  location: location
}

// ============================================================================
// Deploy all resources inside the RG
// ============================================================================
module resources 'resources.bicep' = {
  scope: rg
  name: 'resources'
  params: {
    location: location
    appName: appName
    environment: environment
    imageTag: imageTag
    deployContainerApp: deployContainerApp
    mapplsRestKey: mapplsRestKey
    mapplsClientId: mapplsClientId
    mapplsClientSecret: mapplsClientSecret
    createRoleAssignments: createRoleAssignments
  }
}

// ============================================================================
// Outputs
// ============================================================================
output resourceGroupName string = rg.name
output acrLoginServer string = resources.outputs.acrLoginServer
output acrName string = resources.outputs.acrName
output containerAppFqdn string = resources.outputs.containerAppFqdn
output containerAppName string = resources.outputs.containerAppName
output storageAccountName string = resources.outputs.storageAccountName
output fileShareName string = resources.outputs.fileShareName
