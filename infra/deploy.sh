#!/usr/bin/env bash
# GridLock — one-command Azure deployment.
#
# Prerequisites:
#   1. az CLI installed and logged in:  az login
#   2. Bicep installed:                 az bicep install
#   3. Set subscription:                az account set -s <subscription-id>
#
# Usage:
#   ./deploy.sh              # deploy with defaults (prod, centralindia)
#   ./deploy.sh dev          # deploy dev environment
#   ./deploy.sh prod eastus  # deploy prod to East US
set -euo pipefail

ENVIRONMENT="${1:-prod}"
LOCATION="${2:-centralindia}"
APP_NAME="gridlock"
DEPLOYMENT_NAME="gridlock-${ENVIRONMENT}-$(date +%Y%m%d-%H%M%S)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BICEP_FILE="${SCRIPT_DIR}/main.bicep"
PARAMS_FILE="${SCRIPT_DIR}/main.parameters.json"

echo "=== GridLock Azure Deployment ==="
echo "  Environment:  ${ENVIRONMENT}"
echo "  Location:     ${LOCATION}"
echo "  Deployment:   ${DEPLOYMENT_NAME}"
echo ""

# Validate Bicep
echo "[1/3] Validating Bicep template..."
az deployment sub validate \
  --location "${LOCATION}" \
  --template-file "${BICEP_FILE}" \
  --parameters \
    appName="${APP_NAME}" \
    environment="${ENVIRONMENT}" \
    location="${LOCATION}" \
    imageTag=latest \
    mapplsRestKey="${MAPPLS_REST_KEY:-}" \
    mapplsClientId="${MAPPLS_CLIENT_ID:-}" \
    mapplsClientSecret="${MAPPLS_CLIENT_SECRET:-}" \
  --query "properties.provisioningState" \
  -o tsv

# Deploy
echo ""
echo "[2/3] Deploying resources (this takes ~5-10 minutes)..."
az deployment sub create \
  --name "${DEPLOYMENT_NAME}" \
  --location "${LOCATION}" \
  --template-file "${BICEP_FILE}" \
  --parameters \
    appName="${APP_NAME}" \
    environment="${ENVIRONMENT}" \
    location="${LOCATION}" \
    imageTag=latest \
    mapplsRestKey="${MAPPLS_REST_KEY:-}" \
    mapplsClientId="${MAPPLS_CLIENT_ID:-}" \
    mapplsClientSecret="${MAPPLS_CLIENT_SECRET:-}" \
  --query "properties.outputs" \
  -o json > /tmp/gridlock-outputs.json

echo ""
echo "[3/3] Deployment outputs:"
cat /tmp/gridlock-outputs.json | python3 -m json.tool

# Extract key outputs
ACR_NAME=$(cat /tmp/gridlock-outputs.json | python3 -c "import sys,json; print(json.load(sys.stdin)['acrName']['value'])")
ACR_SERVER=$(cat /tmp/gridlock-outputs.json | python3 -c "import sys,json; print(json.load(sys.stdin)['acrLoginServer']['value'])")
ACA_FQDN=$(cat /tmp/gridlock-outputs.json | python3 -c "import sys,json; print(json.load(sys.stdin)['containerAppFqdn']['value'])")
ACA_NAME=$(cat /tmp/gridlock-outputs.json | python3 -c "import sys,json; print(json.load(sys.stdin)['containerAppName']['value'])")
RG_NAME=$(cat /tmp/gridlock-outputs.json | python3 -c "import sys,json; print(json.load(sys.stdin)['resourceGroupName']['value'])")

echo ""
echo "=========================================="
echo "  Deployment complete!"
echo "=========================================="
echo ""
echo "  Resource Group:     ${RG_NAME}"
echo "  Container Registry: ${ACR_SERVER}"
echo "  Container App FQDN: https://${ACA_FQDN}"
echo ""
echo "  Next steps:"
echo "  1. Push your image:"
echo "     az acr login -n ${ACR_NAME}"
echo "     docker build --target runtime -t ${ACR_SERVER}/gridlock:latest ."
echo "     docker push ${ACR_SERVER}/gridlock:latest"
echo ""
echo "  2. Trigger the Container App revision:"
echo "     az containerapp update -n ${ACA_NAME} -g ${RG_NAME} --image ${ACR_SERVER}/gridlock:latest"
echo ""
echo "  3. (Optional) Set up GitHub Actions OIDC for automated deploys."
