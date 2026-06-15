# startup.sh — Azure App Service startup command
# Set this as the startup command in Azure App Service configuration:
# gunicorn --bind=0.0.0.0:8000 --workers=4 --timeout=300 app:app

gunicorn --bind=0.0.0.0:8000 --workers=4 --timeout=300 --log-level=info app:app
