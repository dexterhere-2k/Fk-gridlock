targetScope = 'subscription'

@description('Azure region for all resources')
param location string = 'westeurope'

@description('Short name prefix for resources (3-10 chars, lowercase alphanumeric)')
param appName string = 'gridlock'

@description('Environment tag (dev/staging/prod)')
param environment string = 'prod'

@description('Container image tag to deploy')
param imageTag string = 'latest'

@description('Mappls REST API key (optional — leave empty to use baked-in cache fallback)')
@secure()
param mapplsRestKey string = ''

@description('Mappls OAuth2 client ID')
@secure()
param mapplsClientId string = ''

@description('Mappls OAuth2 client secret')
@secure()
param mapplsClientSecret string = ''

@description('Whether to deploy the Container App (set to false if the image does not exist yet)')
param deployContainerApp bool = true

var rgName = 'rg-${appName}-${environment}'
var acrName = 'cr${appName}${uniqueString(subscription().subscriptionId)}'
var acaEnvName = 'cae-${appName}-${environment}'
var acaName = 'ca-${appName}-${environment}'
var storageName = 'st${appName}${uniqueString(subscription().subscriptionId)}'
var fileShareName = 'artifacts'
var logAnalyticsName = 'log-${appName}-${environment}'
var identityName = 'id-${appName}-${environment}'
var tags = {
  app: appName
  environment: environment
  managedBy: 'bicep'
}

// ============================================================================
// Resource Group
// ============================================================================
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

// ============================================================================
// Log Analytics Workspace (for Container Apps monitoring)
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ============================================================================
// User-Assigned Managed Identity
// ============================================================================
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: identityName
  location: location
  tags: tags
}

// ============================================================================
// Container Registry
// ============================================================================
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// AcrPull role assignment for the managed identity
resource acrRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, managedIdentity.properties.principalId, '7f951dcc-6d99-4c4e-93d4-3c4c3c3c3c3c')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dcc-6d99-4c4e-93d4-3c4c3c3c3c3c')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Storage Account + File Share for artifacts persistence
// ============================================================================
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: storageAccount
  name: fileShareName
  properties: {
    shareQuota: 5
    accessTier: 'Hot'
  }
}

// Storage File Data Privileged Contributor role for the managed identity
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(storageAccount.id, managedIdentity.properties.principalId, '6a3b2337-3c6a-4e6a-8c6a-6a3b23373c6a')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '6a3b2337-3c6a-4e6a-8c6a-6a3b23373c6a')
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Container Apps Environment
// ============================================================================
resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: acaEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
  dependsOn: [
    logAnalytics
  ]
}

// ============================================================================
// Storage config for the Container Apps Environment (Azure File mount)
// ============================================================================
resource envStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: containerAppEnv
  name: storageName
  properties: {
    azureFile: {
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
  dependsOn: [
    storageAccount
    fileShare
    containerAppEnv
  ]
}

// ============================================================================
// Container App
// ============================================================================
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = if (deployContainerApp) {
  name: acaName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        { name: 'mappls-rest-key', value: mapplsRestKey }
        { name: 'mappls-client-id', value: mapplsClientId }
        { name: 'mappls-client-secret', value: mapplsClientSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'gridlock'
          image: '${acr.properties.loginServer}/gridlock:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'PORT', value: '80' }
            { name: 'PYTHONPATH', value: '/app' }
            { name: 'GRIDLOCK_LOG_LEVEL', value: 'info' }
            { name: 'MAPPLS_REST_KEY', secretRef: 'mappls-rest-key' }
            { name: 'MAPPLS_CLIENT_ID', secretRef: 'mappls-client-id' }
            { name: 'MAPPLS_CLIENT_SECRET', secretRef: 'mappls-client-secret' }
            { name: 'GRIDLOCK_LEDGER_PATH', value: '/app/artifacts/ledger.sqlite3' }
          ]
          volumeMounts: [
            {
              volumeName: 'artifacts-volume'
              mountPath: '/app/artifacts'
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/api/health'
                port: 80
              }
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 80
              }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/health'
                port: 80
              }
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'artifacts-volume'
          storageType: 'AzureFile'
          storageName: storageName
          mountOptions: 'uid=0,gid=0,file_mode=0755,dir_mode=0755'
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
  dependsOn: [
    envStorage
    acr
    acrRoleAssignment
  ]
}

// ============================================================================
// Outputs
// ============================================================================
output resourceGroupName string = rg.name
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output containerAppFqdn string = deployContainerApp ? containerApp.properties.configuration.ingress.fqdn : ''
output containerAppName string = deployContainerApp ? containerApp.name : ''
output storageAccountName string = storageAccount.name
output fileShareName string = fileShareName