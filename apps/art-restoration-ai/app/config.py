import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
WORK_DIR = Path(os.getenv("WORK_DIR", "./work")).resolve()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "250000000"))
MAX_LOCAL_ENGINE_PIXELS = int(os.getenv("MAX_LOCAL_ENGINE_PIXELS", "20000000"))
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
APP_ENV = os.getenv("APP_ENV", "development")
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID", "")
