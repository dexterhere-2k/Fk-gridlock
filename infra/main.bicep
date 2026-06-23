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

var rgName = 'rg-${appName}-${environment}'
var acrName = 'cr${appName}${uniqueString(subscription().subscriptionId)}'
var acaEnvName = 'cae-${appName}-${environment}'
var acaName = 'ca-${appName}-${environment}'
var storageName = 'st${appName}${uniqueString(subscription().subscriptionId)}'
var fileShareName = 'artifacts'
var logAnalyticsName = 'log-${appName}-${environment}'
var kvName = 'kv-${appName}${uniqueString(subscription().subscriptionId)}'
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
module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.1.0' = {
  name: 'logAnalyticsDeploy'
  scope: rg
  params: {
    name: logAnalyticsName
    location: location
    tags: tags
    sku: 'PerGB2018'
  }
}

// ============================================================================
// Container Registry
// ============================================================================
module acr 'br/public:avm/res/container-registry/registry:0.5.1' = {
  name: 'acrDeploy'
  scope: rg
  params: {
    name: acrName
    location: location
    tags: tags
    sku: 'Basic'
    adminUserEnabled: true
  }
}

// ============================================================================
// User-Assigned Managed Identity
// ============================================================================
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// ============================================================================
// Key Vault (for Mappls secrets)
// ============================================================================
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { name: 'standard', family: 'A' }
    enableRbacAuthorization: true
  }
}

// ============================================================================
// Storage Account + File Share for artifacts persistence
// ============================================================================
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  tags: tags
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  name: '${storageName}/default/${fileShareName}'
  properties: {
    shareQuota: 5
    accessTier: 'Hot'
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
        customerId: logAnalytics.outputs.workspaceCustomerId
        sharedKey: logAnalytics.outputs.workspacePrimarySharedKey
      }
    }
  }
}

// ============================================================================
// Container App
// ============================================================================
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
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
    managedEnvironmentId: containerAppEnv.id
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
          server: acr.outputs.loginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'mappls-rest-key'
          value: mapplsRestKey
        }
        {
          name: 'mappls-client-id'
          value: mapplsClientId
        }
        {
          name: 'mappls-client-secret'
          value: mapplsClientSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'gridlock'
          image: '${acr.outputs.loginServer}/gridlock:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            { name: 'PORT', value: '80' }
            { name: 'PYTHONPATH', value: '/app' }
            { name: 'GRIDLOCK_LOG_LEVEL', value: 'info' }
            {
              name: 'MAPPLS_REST_KEY'
              secretRef: 'mappls-rest-key'
            }
            {
              name: 'MAPPLS_CLIENT_ID'
              secretRef: 'mappls-client-id'
            }
            {
              name: 'MAPPLS_CLIENT_SECRET'
              secretRef: 'mappls-client-secret'
            }
            {
              name: 'GRIDLOCK_LEDGER_PATH'
              value: '/app/artifacts/ledger.sqlite3'
            }
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
              httpGet: { path: '/api/health', port: 80 }
              initialDelaySeconds: 15
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 12
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
              httpGet: { path: '/healthz', port: 80 }
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
        rules: [
          {
            name: 'http-scale-rule'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

// ============================================================================
// Role assignments — ACA identity can pull from ACR
// ============================================================================
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.outputs.id, managedIdentity.id, 'AcrPull')
  scope: acr.outputs
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalType: 'ServicePrincipal'
  }
}

// Grant ACA identity access to storage account (for Azure Files mount)
resource storageContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, managedIdentity.id, 'StorageFileDataPrivilegedContributor')
  scope: storageAccount
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b8eda974-7b85-4f76-af95-8a5ca8d8c081')
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Outputs
// ============================================================================
output resourceGroupName string = rg.name
output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
output storageAccountName string = storageAccount.name
output fileShareName string = fileShareName
output keyVaultName string = keyVault.name
