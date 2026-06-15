# azure-deploy.md — Deployment Guide for Azure App Service

## Prerequisites
- Azure CLI installed and logged in (`az login`)
- An Azure subscription
- Tesseract OCR installed on the App Service (via startup or custom Docker image)

---

## Step 1: Create Azure Resources

```bash
# Variables — edit these
RESOURCE_GROUP="rg-alcohol-label-checker"
LOCATION="eastus"
APP_NAME="alcohol-label-checker"           # Must be globally unique
STORAGE_ACCOUNT="alcohollabelstorage"      # Lowercase, 3-24 chars
SQL_SERVER="alcohol-label-sql"
SQL_DB="AlcoholLabelDB"
SQL_ADMIN="sqladmin"
SQL_PASSWORD="YourStrongPass123!"          # Change this
APP_SERVICE_PLAN="asp-alcohol-label"

# Resource Group
az group create --name $RESOURCE_GROUP --location $LOCATION

# App Service Plan (Linux, B2 or higher for batch workloads)
az appservice plan create \
  --name $APP_SERVICE_PLAN \
  --resource-group $RESOURCE_GROUP \
  --sku B2 \
  --is-linux

# Web App (Python 3.12)
az webapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --plan $APP_SERVICE_PLAN \
  --runtime "PYTHON:3.12"

# Storage Account + Containers
az storage account create \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS

STORAGE_CONN=$(az storage account show-connection-string \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --query connectionString -o tsv)

az storage container create --name alcohol-applications --connection-string "$STORAGE_CONN"
az storage container create --name alcohol-labels       --connection-string "$STORAGE_CONN"
az storage container create --name alcohol-results      --connection-string "$STORAGE_CONN"

# Azure SQL Server + Database
az sql server create \
  --name $SQL_SERVER \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --admin-user $SQL_ADMIN \
  --admin-password "$SQL_PASSWORD"

az sql db create \
  --name $SQL_DB \
  --server $SQL_SERVER \
  --resource-group $RESOURCE_GROUP \
  --service-objective S2

# Allow Azure services to access SQL
az sql server firewall-rule create \
  --server $SQL_SERVER \
  --resource-group $RESOURCE_GROUP \
  --name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

---

## Step 2: Configure App Settings (Environment Variables)

```bash
az webapp config appsettings set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --settings \
    ANTHROPIC_API_KEY="your_anthropic_api_key" \
    AZURE_STORAGE_CONNECTION_STRING="$STORAGE_CONN" \
    AZURE_BLOB_CONTAINER_APPLICATIONS="alcohol-applications" \
    AZURE_BLOB_CONTAINER_LABELS="alcohol-labels" \
    AZURE_BLOB_CONTAINER_RESULTS="alcohol-results" \
    AZURE_SQL_SERVER="${SQL_SERVER}.database.windows.net" \
    AZURE_SQL_DATABASE="$SQL_DB" \
    AZURE_SQL_USERNAME="$SQL_ADMIN" \
    AZURE_SQL_PASSWORD="$SQL_PASSWORD" \
    "AZURE_SQL_DRIVER={ODBC Driver 18 for SQL Server}" \
    FLASK_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
    MAX_BATCH_SIZE="300" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"
```

---

## Step 3: Install Tesseract on App Service

Add a startup script at `.azure/startup.sh` or set the startup command:

```bash
az webapp config set \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --startup-file "apt-get install -y tesseract-ocr && gunicorn --bind=0.0.0.0:8000 --workers=4 --timeout=300 app:app"
```

Or use a custom Docker image (recommended for production):
- Base: `python:3.12-slim`
- Install: `tesseract-ocr libgl1`
- Copy app and `pip install -r requirements.txt`

---

## Step 4: Deploy Application

```bash
# Option A: Deploy from local folder (zip deploy)
cd /path/to/alcohol_label_matcher
zip -r app.zip . --exclude "*.env" --exclude "__pycache__/*" --exclude "uploads/*"
az webapp deployment source config-zip \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --src app.zip

# Option B: GitHub Actions CI/CD (recommended)
# Download publish profile and add to GitHub Secrets as AZURE_WEBAPP_PUBLISH_PROFILE
az webapp deployment list-publishing-profiles \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --xml > publish_profile.xml
```

---

## Step 5: Verify

```bash
# Get the app URL
echo "https://${APP_NAME}.azurewebsites.net"

# Check health endpoint
curl "https://${APP_NAME}.azurewebsites.net/health"

# Stream live logs
az webapp log tail --name $APP_NAME --resource-group $RESOURCE_GROUP
```

---

## Architecture Summary

```
Browser / API Client
        │
        ▼
Azure App Service (Flask, Python 3.12, Gunicorn 4 workers)
        │
        ├─► Azure Blob Storage
        │     ├── alcohol-applications/   (application form images)
        │     ├── alcohol-labels/         (bottle label images)
        │     └── alcohol-results/        (Excel compliance reports)
        │
        ├─► Azure SQL Database (AlcoholLabelDB)
        │     ├── batches          (batch metadata + summary)
        │     └── match_results    (per-pair field compliance results)
        │
        └─► Anthropic Claude Vision API (claude-sonnet-4-6)
              (OCR extraction + compliance field matching)
```

## Scaling Notes
- For > 300 pairs or high concurrency, upgrade App Service Plan to P2v3 and increase `--workers`.
- For very large batches, consider Azure Service Bus + background worker (Azure Container Apps).
- Enable Azure SQL auto-pause (serverless tier) for cost savings in low-traffic environments.
