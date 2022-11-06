import os

from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.environ["APP_ENV"]
if APP_ENV not in ("local", "staging", "production"):
    raise ValueError(
        "Invalid APP_ENV; must be one of 'local', 'staging', 'production'")

APP_LOG_LEVEL = int(os.environ["APP_LOG_LEVEL"])

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ["DB_PORT"])
DB_USER = os.environ["DB_USER"]
DB_PASS = os.environ["DB_PASS"]
DB_NAME = os.environ["DB_NAME"]

DOMAIN = os.environ["DOMAIN"]

UPLOAD_DIR = os.environ["UPLOAD_DIR"]
