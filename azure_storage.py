"""
azure_storage.py — Azure Blob Storage integration
Handles upload, download, and listing of application forms and bottle label images.
"""

import os
import uuid
import logging
from io import BytesIO
from typing import Optional

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import AzureError

logger = logging.getLogger(__name__)


class AzureBlobService:
    def __init__(self):
        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not conn_str:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING is not set.")
        self.client = BlobServiceClient.from_connection_string(conn_str)
        self.container_applications = os.getenv("AZURE_BLOB_CONTAINER_APPLICATIONS", "alcohol-applications")
        self.container_labels = os.getenv("AZURE_BLOB_CONTAINER_LABELS", "alcohol-labels")
        self.container_results = os.getenv("AZURE_BLOB_CONTAINER_RESULTS", "alcohol-results")
        self._ensure_containers()

    def _ensure_containers(self):
        """Create containers if they don't exist."""
        for name in [self.container_applications, self.container_labels, self.container_results]:
            try:
                self.client.create_container(name)
                logger.info(f"Created container: {name}")
            except Exception:
                pass  # Already exists

    def upload_file(self, file_bytes: bytes, filename: str, container: str, batch_id: str) -> str:
        """Upload a file to blob storage. Returns the blob name."""
        blob_name = f"{batch_id}/{filename}"
        try:
            container_client = self.client.get_container_client(container)
            container_client.upload_blob(name=blob_name, data=file_bytes, overwrite=True)
            logger.info(f"Uploaded {blob_name} to {container}")
            return blob_name
        except AzureError as e:
            logger.error(f"Failed to upload {filename}: {e}")
            raise

    def download_file(self, blob_name: str, container: str) -> bytes:
        """Download a blob and return its bytes."""
        try:
            blob_client = self.client.get_blob_client(container=container, blob=blob_name)
            stream = blob_client.download_blob()
            return stream.readall()
        except AzureError as e:
            logger.error(f"Failed to download {blob_name}: {e}")
            raise

    def upload_application(self, file_bytes: bytes, filename: str, batch_id: str) -> str:
        return self.upload_file(file_bytes, filename, self.container_applications, batch_id)

    def upload_label(self, file_bytes: bytes, filename: str, batch_id: str) -> str:
        return self.upload_file(file_bytes, filename, self.container_labels, batch_id)

    def upload_result(self, file_bytes: bytes, filename: str, batch_id: str) -> str:
        return self.upload_file(file_bytes, filename, self.container_results, batch_id)

    def download_application(self, blob_name: str) -> bytes:
        return self.download_file(blob_name, self.container_applications)

    def download_label(self, blob_name: str) -> bytes:
        return self.download_file(blob_name, self.container_labels)

    def list_batch_files(self, batch_id: str, container: str) -> list:
        """List all blobs in a batch prefix."""
        try:
            container_client = self.client.get_container_client(container)
            blobs = container_client.list_blobs(name_starts_with=f"{batch_id}/")
            return [b.name for b in blobs]
        except AzureError as e:
            logger.error(f"Failed to list blobs for batch {batch_id}: {e}")
            return []

    def get_blob_url(self, blob_name: str, container: str) -> str:
        """Get the public URL for a blob (requires public access or SAS token in production)."""
        account_name = self.client.account_name
        return f"https://{account_name}.blob.core.windows.net/{container}/{blob_name}"
