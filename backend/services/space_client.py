"""
DigitalOcean Spaces Client Service
Handles file upload/download operations with S3-compatible storage
"""
import boto3
from botocore.exceptions import ClientError
from typing import Optional, BinaryIO
import logging
from backend.config import get_settings

logger = logging.getLogger(__name__)


class SpacesClient:
    """Client for interacting with DigitalOcean Spaces (S3-compatible)"""
    
    def __init__(self):
        settings = get_settings()
        
        session = boto3.session.Session()
        self.client = session.client(
            's3',
            region_name=settings.spaces_region,
            endpoint_url=settings.spaces_endpoint,
            aws_access_key_id=settings.spaces_key,
            aws_secret_access_key=settings.spaces_secret
        )
        self.bucket = settings.spaces_bucket
        logger.info(f"Initialized SpacesClient for bucket: {self.bucket}")
    
    def upload_pdf(self, file_obj: BinaryIO, filename: str) -> dict:
        """
        Upload a PDF file to Spaces
        
        Args:
            file_obj: File object to upload
            filename: Name for the file in Spaces
            
        Returns:
            dict with status and file details
        """
        try:
            key = f"pdfs/{filename}"
            self.client.upload_fileobj(
                file_obj,
                self.bucket,
                key,
                ExtraArgs={'ContentType': 'application/pdf'}
            )
            
            url = f"{get_settings().spaces_endpoint}/{self.bucket}/{key}"
            logger.info(f"Successfully uploaded PDF: {filename}")
            
            return {
                "status": "success",
                "filename": filename,
                "key": key,
                "url": url
            }
        except ClientError as e:
            logger.error(f"Failed to upload PDF {filename}: {str(e)}")
            raise Exception(f"Upload failed: {str(e)}")
    
    def upload_json(self, json_data: str, filename: str) -> dict:
        """
        Upload JSON data to Spaces
        
        Args:
            json_data: JSON string to upload
            filename: Name for the file in Spaces
            
        Returns:
            dict with status and file details
        """
        try:
            key = f"results/{filename}"
            self.client.put_object(
                Body=json_data.encode('utf-8'),
                Bucket=self.bucket,
                Key=key,
                ContentType='application/json'
            )
            
            url = f"{get_settings().spaces_endpoint}/{self.bucket}/{key}"
            logger.info(f"Successfully uploaded JSON: {filename}")
            
            return {
                "status": "success",
                "filename": filename,
                "key": key,
                "url": url
            }
        except ClientError as e:
            logger.error(f"Failed to upload JSON {filename}: {str(e)}")
            raise Exception(f"Upload failed: {str(e)}")
    
    def download_pdf(self, key: str, local_path: str) -> bool:
        """
        Download a PDF from Spaces to local filesystem
        
        Args:
            key: Object key in Spaces
            local_path: Local path to save the file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.download_file(self.bucket, key, local_path)
            logger.info(f"Successfully downloaded PDF from {key} to {local_path}")
            return True
        except ClientError as e:
            logger.error(f"Failed to download PDF from {key}: {str(e)}")
            return False
    
    def download_json(self, key: str) -> Optional[str]:
        """
        Download JSON data from Spaces
        
        Args:
            key: Object key in Spaces
            
        Returns:
            JSON string if successful, None otherwise
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            json_data = response['Body'].read().decode('utf-8')
            logger.info(f"Successfully downloaded JSON from {key}")
            return json_data
        except ClientError as e:
            logger.error(f"Failed to download JSON from {key}: {str(e)}")
            return None
    
    def list_files(self, prefix: str = "") -> list:
        """
        List files in Spaces with optional prefix filter
        
        Args:
            prefix: Prefix to filter objects
            
        Returns:
            List of object keys
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix
            )
            
            if 'Contents' not in response:
                return []
            
            files = [obj['Key'] for obj in response['Contents']]
            logger.info(f"Listed {len(files)} files with prefix: {prefix}")
            return files
        except ClientError as e:
            logger.error(f"Failed to list files: {str(e)}")
            return []
    
    def delete_file(self, key: str) -> bool:
        """
        Delete a file from Spaces
        
        Args:
            key: Object key to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Successfully deleted file: {key}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file {key}: {str(e)}")
            return False
    
    def get_file_url(self, key: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate a presigned URL for temporary file access
        
        Args:
            key: Object key
            expiration: URL expiration time in seconds (default 1 hour)
            
        Returns:
            Presigned URL string if successful, None otherwise
        """
        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': key},
                ExpiresIn=expiration
            )
            logger.info(f"Generated presigned URL for {key}")
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL for {key}: {str(e)}")
            return None


# Singleton instance
_spaces_client = None


def get_spaces_client() -> SpacesClient:
    """Get or create SpacesClient singleton instance"""
    global _spaces_client
    if _spaces_client is None:
        _spaces_client = SpacesClient()
    return _spaces_client
