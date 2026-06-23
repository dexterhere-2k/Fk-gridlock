// ============================================================================
// RG-scoped module — all resources live here
// ============================================================================
targetScope = 'resourceGroup'

param location string
param appName string
param environment string
param imageTag string
param deployContainerApp bool
param createRoleAssignments bool = true

@secure()
param mapplsRestKey string = ''

@secure()
param mapplsClientId string = ''

@secure()
param mapplsClientSecret string = ''

var acrName = 'cr${appName}${uniqueString(resourceGroup().name)}'
var acaEnvName = 'cae-${appName}-${environment}'
var acaName = 'ca-${appName}-${environment}'
var storageName = 'st${appName}${uniqueString(resourceGroup().name)}'
var fileShareName = 'artifacts'
var logAnalyticsName = 'log-${appName}-${environment}'
var identityName = 'id-${appName}-${environment}'

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

var actualMapplsRestKey = empty(mapplsRestKey) ? 'dummy' : mapplsRestKey
var actualMapplsClientId = empty(mapplsClientId) ? 'dummy' : mapplsClientId
var actualMapplsClientSecret = empty(mapplsClientSecret) ? 'dummy' : mapplsClientSecret

// ============================================================================
// Log Analytics
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ============================================================================
// Managed Identity
// ============================================================================
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-07-31-preview' = {
  name: identityName
  location: location
}

// ============================================================================
// Container Registry
// ============================================================================
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// ============================================================================
// Storage Account + File Share
// ============================================================================
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileServices 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  name: 'default'
  parent: storageAccount
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  name: fileShareName
  parent: fileServices
  properties: {
    shareQuota: 5
    accessTier: 'Hot'
  }
}

// ============================================================================
// Role Assignments
// ============================================================================
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createRoleAssignments) {
  scope: acr
  name: guid('acrpull', managedIdentity.id)
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage mounted via account key (not managed identity), no role assignment needed

// ============================================================================
// Container Apps Environment
// ============================================================================
resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: acaEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ============================================================================
// Storage config for ACA environment (Azure File mount)
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
}

// ============================================================================
// Container App
// ============================================================================
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = if (deployContainerApp) {
  name: acaName
  location: location
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
        { name: 'mappls-rest-key', value: actualMapplsRestKey }
        { name: 'mappls-client-id', value: actualMapplsClientId }
        { name: 'mappls-client-secret', value: actualMapplsClientSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'gridlock'
          image: '${acr.properties.loginServer}/gridlock:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1.0Gi' }
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
            { volumeName: 'artifacts-volume', mountPath: '/app/artifacts' }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/api/health', port: 80 }
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 10
            }
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 80 }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/api/health', port: 80 }
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
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

// ============================================================================
// Outputs
// ============================================================================
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output containerAppFqdn string = deployContainerApp ? containerApp.properties.configuration.ingress.fqdn : ''
output containerAppName string = deployContainerApp ? containerApp.name : ''
output storageAccountName string = storageAccount.name
output fileShareName string = fileShareName
