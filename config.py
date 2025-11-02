import os

class Config:
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024 * 4096
    UPLOAD_FOLDER = 'uploads'
    PROCESSED_FOLDER = 'processed'
    OUTPUT_FOLDER = 'output'
    DATABASE = 'database.db'
    NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
    NIM_API_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1"
    NIM_HEADERS = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    MODEL_MAX_WIDTH = 500
    MODEL_MAX_HEIGHT = 500
    NVIDIA_NIM_AVAILABLE = bool(NVIDIA_API_KEY)
