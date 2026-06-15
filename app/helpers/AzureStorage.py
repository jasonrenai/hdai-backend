import os
from azure.storage.blob import BlobServiceClient
from app.helpers.Utilities import Utils
import urllib.parse
import time
import re
import mimetypes
import urllib
from azure.storage.blob import ContentSettings


class AzureBlobUploader:
    def __init__(self):
        try:
            self.__connection_string = os.environ['AZURE_STORAGE_CONNECTION_STRING']
            self.__container_name = os.environ['AZURE_STORAGE_CONTAINER']
        except KeyError:
            raise Exception("AZURE_STORAGE_CONNECTION_STRING and AZURE_STORAGE_CONTAINER must be set.")

        self.__blob_service_client = BlobServiceClient.from_connection_string(self.__connection_string)
        self.__container_client = self.__blob_service_client.get_container_client(self.__container_name)
        self.__generate_random_hex_string = Utils.generate_hex_string
        
    def _blob_name_from_url(self, file_url: str) -> str:
        """Resolve blob name from a full Azure blob URL (validates account + container)."""
        parsed_url = urllib.parse.urlparse((file_url or "").strip())
        account_name = self.__blob_service_client.account_name
        expected_host = f"{account_name}.blob.core.windows.net"
        if parsed_url.netloc.lower() != expected_host.lower():
            raise ValueError("File URL is not from the configured Azure storage account")
        path = urllib.parse.unquote(parsed_url.path.lstrip("/"))
        parts = path.split("/", 1)
        if len(parts) != 2 or parts[0] != self.__container_name:
            raise ValueError("File URL does not match the configured storage container")
        return parts[1]

    def download_blob_from_url(self, file_url: str) -> bytes:
        """Download blob bytes from a full Azure blob URL."""
        blob_name = self._blob_name_from_url(file_url)
        blob_client = self.__blob_service_client.get_blob_client(
            container=self.__container_name,
            blob=blob_name,
        )
        return blob_client.download_blob().readall()

    def delete_file(self, file_url: str):
        """Delete file from Azure Blob Storage using full file URL."""
        blob_name = self._blob_name_from_url(file_url)
        blob_client = self.__blob_service_client.get_blob_client(container=self.__container_name, blob=blob_name)
        blob_client.delete_blob()

    def copy_and_upload_to_azure_blob(self, image_url, container_name='temp', folder_name=None, file_type=".png"):
        container_client = self.__blob_service_client.get_container_client(container_name)

        destination_blob_name = self.__generate_random_hex_string() + file_type
        if folder_name:
            destination_blob_name = f"{folder_name}/{destination_blob_name}"

        copied_blob = self.__blob_service_client.get_blob_client(container_name, destination_blob_name)

        copied_blob.start_copy_from_url(image_url)

        for _ in range(60):
            props = copied_blob.get_blob_properties()
            status = props.copy.status
            if status == "success":
                copied_blob_url = f"https://{self.__blob_service_client.account_name}.blob.core.windows.net/{container_name}/{destination_blob_name}"
                return copied_blob_url
            time.sleep(1)

        props = copied_blob.get_blob_properties()
        copy_id = props.copy.id
        copied_blob.abort_copy(copy_id)
        return None

    def upload_file_to_azure_blob(self, file_path, folder_name=None, file_type=".png"):
        container_name=self.__container_name
        container_client = self.__blob_service_client.get_container_client(container_name)

        file_extension = os.path.splitext(file_path)[1]

        random_filename = self.__generate_random_hex_string()
        sanitized_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', random_filename + file_extension)

        if folder_name:
            folder_name = re.sub(r'[^a-zA-Z0-9/_-]', '_', folder_name)
            destination_blob_name = f"{folder_name}/{sanitized_filename}"
        else:
            destination_blob_name = sanitized_filename
            
        destination_blob_name = urllib.parse.quote(destination_blob_name, safe="/")

        blob_client = self.__blob_service_client.get_blob_client(container_name, destination_blob_name)

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = 'application/octet-stream'  

        content_settings = ContentSettings(content_type=mime_type)

        try:
            with open(file_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True, content_settings=content_settings)
            uploaded_blob_url = f"https://{self.__blob_service_client.account_name}.blob.core.windows.net/{container_name}/{destination_blob_name}"
            return uploaded_blob_url
        except Exception as e:
            print(f"Failed to upload {file_path} to Azure Blob Storage. Error: {str(e)}")
            return None
        
        
    def clear_folder(self, operator_id: str, folder_name: str):
        """
        Delete all blobs under a folder for a specific operator.
        operator_id: e.g., '12345'
        folder_name: e.g., 'Airbnb/listingId'
        """
        full_prefix = f"{operator_id}/{folder_name}".rstrip("/") + "/"
        blobs_to_delete = self.__container_client.list_blobs(name_starts_with=full_prefix)
        
        deleted_any = False
        for blob in blobs_to_delete:
            blob_client = self.__container_client.get_blob_client(blob)
            blob_client.delete_blob()
            deleted_any = True
        print(f"Deleted  blobs from {full_prefix}")

        return deleted_any  # True if something was deleted

    def upload_file_to_operator_folder(self, file_path: str, operator_id: str, folder_name: str = None, file_type=".png"):
        """
        Uploads a file after clearing existing operator folder if present.
        """
        if folder_name:
            self.clear_folder(operator_id, folder_name)
            full_folder_path = f"{operator_id}/{folder_name}"
        else:
            full_folder_path = operator_id

        return self.upload_file_to_azure_blob(file_path, folder_name=full_folder_path, file_type=file_type)
    
    def upload_excel_file_to_azure_blob(self, file_path, folder_name=None, file_name=None, file_type=".png"):
            container_name=self.__container_name
            container_client = self.__blob_service_client.get_container_client(container_name)

            file_extension = os.path.splitext(file_path)[1]

            if not file_name:
                random_filename = self.__generate_random_hex_string()
            random_filename=file_name
            sanitized_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', random_filename + file_extension)

            if folder_name:
                folder_name = re.sub(r'[^a-zA-Z0-9/_-]', '_', folder_name)
                destination_blob_name = f"{folder_name}/{sanitized_filename}"
            else:
                destination_blob_name = sanitized_filename
                
            destination_blob_name = urllib.parse.quote(destination_blob_name, safe="/")

            blob_client = self.__blob_service_client.get_blob_client(container_name, destination_blob_name)

            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'  

            content_settings = ContentSettings(content_type=mime_type)

            try:
                with open(file_path, "rb") as data:
                    blob_client.upload_blob(data, overwrite=True, content_settings=content_settings)
                uploaded_blob_url = f"https://{self.__blob_service_client.account_name}.blob.core.windows.net/{container_name}/{destination_blob_name}"
                return uploaded_blob_url
            except Exception as e:
                print(f"Failed to upload {file_path} to Azure Blob Storage. Error: {str(e)}")
                return None
            