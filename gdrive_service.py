
import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from flask import current_app, url_for, session
from utils import get_db_connection

# Scopes required for Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/drive.metadata.readonly']

def get_drive_service(user):
    """
    Returns a build('drive', 'v3', credentials=creds) service object
    if user has valid tokens. Returns None otherwise.
    """
    if not user.google_token:
        return None
    
    try:
        token_info = json.loads(user.google_token)
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        current_app.logger.error(f"Error building Drive service: {e}")
        return None

def create_flow(redirect_uri):
    """Creates an OAuth2 Flow object."""
    # We need a client_secret.json. 
    # For now, we assume it's in the root or config. 
    # Or we can construct it from env vars if we had them.
    # User needs to provide this. I'll check if it exists.
    
    client_secrets_file = os.path.join(current_app.root_path, 'client_secret.json')
    if not os.path.exists(client_secrets_file):
        raise FileNotFoundError("client_secret.json not found. Please upload it to the root directory.")
        
    flow = Flow.from_client_secrets_file(
        client_secrets_file,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    return flow

def list_drive_files(service, folder_id='root', page_token=None):
    """Lists files in a specific Drive folder."""
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            pageSize=50,
            pageToken=page_token,
            fields="nextPageToken, files(id, name, mimeType, iconLink, webViewLink, size, modifiedTime)",
            orderBy="folder,name"
        ).execute()
        return results.get('files', []), results.get('nextPageToken')
    except Exception as e:
        current_app.logger.error(f"Drive API List Error: {e}")
        return [], None

def get_file_metadata(service, file_id):
    try:
        return service.files().get(fileId=file_id, fields="id, name, mimeType, size").execute()
    except Exception as e:
        return None

def download_file_to_stream(service, file_id, stream):
    """Downloads file content to a writeable stream."""
    from googleapiclient.http import MediaIoBaseDownload
    
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(stream, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
