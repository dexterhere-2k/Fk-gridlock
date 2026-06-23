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

// ============================================================================
// Container App
// ============================================================================
module containerApp 'br/public:avm/res/app/container-app:0.4.0' = {
  name: 'containerAppDeploy'
  scope: rg
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
              path: '/healthz'
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
    ingress: {
      external: true
      targetPort: 80
      transport: 'auto'
      allowInsecure: false
    }
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
// Role Assignments
// ============================================================================
// Grant ACA identity access to ACR (AcrPull)
module acrPullRole 'br/public:avm/res/authorization/role-assignment:0.2.0' = {
  name: 'acrPullRoleDeploy'
  scope: rg
  params: {
    principalId: managedIdentity.outputs.principalId
    roleDefinitionIdOrName: '/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d'
    principalType: 'ServicePrincipal'
  }
}

// Grant ACA identity access to storage account (StorageFileDataPrivilegedContributor)
module storageContributorRole 'br/public:avm/res/authorization/role-assignment:0.2.0' = {
  name: 'storageContributorRoleDeploy'
  scope: rg
  params: {
    principalId: managedIdentity.outputs.principalId
    roleDefinitionIdOrName: '/providers/Microsoft.Authorization/roleDefinitions/b8eda974-7b85-4f76-af95-8a5ca8d8c081'
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Outputs
// ============================================================================
output resourceGroupName string = rg.name
output acrLoginServer string = acr.outputs.loginServer
output acrName string = acr.outputs.name
output containerAppFqdn string = containerApp.outputs.fqdn
output containerAppName string = containerApp.outputs.name
output storageAccountName string = storageAccount.outputs.name
output fileShareName string = fileShareName
output keyVaultName string = keyVault.outputs.name
