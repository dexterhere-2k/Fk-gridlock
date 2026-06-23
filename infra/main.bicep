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
module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.3.0' = {
  name: 'logAnalyticsDeploy'
  scope: rg
  params: {
    name: logAnalyticsName
    location: location
    tags: tags
  }
}

// ============================================================================
// User-Assigned Managed Identity
// ============================================================================
module managedIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.2.0' = {
  name: 'managedIdentityDeploy'
  scope: rg
  params: {
    name: identityName
    location: location
    tags: tags
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
    acrSku: 'Basic'
    acrAdminUserEnabled: true
    roleAssignments: [
      {
        roleDefinitionIdOrName: 'AcrPull'
        principalId: managedIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

// ============================================================================
// Key Vault (for Mappls secrets)
// ============================================================================
module keyVault 'br/public:avm/res/key-vault/vault:0.6.1' = {
  name: 'keyVaultDeploy'
  scope: rg
  params: {
    name: kvName
    location: location
    tags: tags
    enableRbacAuthorization: true
  }
}

// ============================================================================
// Storage Account + File Share for artifacts persistence
// ============================================================================
module storageAccount 'br/public:avm/res/storage/storage-account:0.9.0' = {
  name: 'storageAccountDeploy'
  scope: rg
  params: {
    name: storageName
    location: location
    kind: 'StorageV2'
    skuName: 'Standard_LRS'
    tags: tags
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    fileServices: {
      shares: [
        {
          name: fileShareName
          shareQuota: 5
          accessTier: 'Hot'
        }
      ]
    }
    roleAssignments: [
      {
        roleDefinitionIdOrName: 'Storage File Data Privileged Contributor'
        principalId: managedIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
      }
    ]
  }
}

// ============================================================================
// Container Apps Environment
// ============================================================================
module containerAppEnv 'br/public:avm/res/app/managed-environment:0.4.0' = {
  name: 'containerAppEnvDeploy'
  scope: rg
  params: {
    name: acaEnvName
    location: location
    tags: tags
    logAnalyticsWorkspaceResourceId: logAnalytics.outputs.resourceId
  }
}

// Existing resource references for child storage configuration
resource storageAccountRes 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageName
  scope: rg
}

resource containerAppEnvRes 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: acaEnvName
  scope: rg
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: containerAppEnvRes
  name: storageName
  scope: rg
  properties: {
    azureFile: {
      accountName: storageAccount.outputs.name
      accountKey: storageAccountRes.listKeys().keys[0].value
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
}

// ============================================================================
// Container App
// ============================================================================
module containerApp 'br/public:avm/res/app/container-app:0.4.0' = if (deployContainerApp) {
  name: 'containerAppDeploy'
  scope: rg
  dependsOn: [
    envStorage
  ]
  params: {
    name: acaName
    location: location
    tags: tags
    environmentId: containerAppEnv.outputs.resourceId
    managedIdentities: {
      userAssignedResourceIds: [
        managedIdentity.outputs.resourceId
      ]
    }
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
    secrets: {
      'mappls-rest-key': mapplsRestKey
      'mappls-client-id': mapplsClientId
      'mappls-client-secret': mapplsClientSecret
    }
    ingressExternal: true
    ingressTargetPort: 80
    ingressTransport: 'auto'
    ingressAllowInsecure: false
    registries: [
      {
        server: acr.outputs.loginServer
        identity: managedIdentity.outputs.resourceId
      }
    ]
    volumes: [
      {
        name: 'artifacts-volume'
        storageType: 'AzureFile'
        storageName: storageAccount.outputs.name
        mountOptions: 'uid=0,gid=0,file_mode=0755,dir_mode=0755'
      }
    ]
    scaleMinReplicas: 0
    scaleMaxReplicas: 2
  }
}

// ============================================================================
// Outputs
// ============================================================================
output resourceGroupName string = rg.name
output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output containerAppFqdn string = deployContainerApp ? containerApp.outputs.fqdn : ''
output containerAppName string = deployContainerApp ? containerApp.outputs.name : ''
output storageAccountName string = storageAccount.outputs.name
output fileShareName string = fileShareName
output keyVaultName string = keyVault.outputs.name
