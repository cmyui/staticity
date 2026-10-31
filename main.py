#!/usr/bin/env python
import atexit
import mimetypes
import os.path
import secrets

import boto3
import fastapi
import fastapi.concurrency
import databases
import upsampling

import logger
import settings

logger.configure_logging(app_env=settings.APP_ENV,
                         log_level=settings.APP_LOG_LEVEL)

logger.overwrite_exception_hook()
atexit.register(logger.restore_exception_hook)


def dsn(scheme: str, user: str, password: str,
        host: str, port: int, database: str) -> str:
    return f"{scheme}://{user}:{password}@{host}:{port}/{database}"


database = databases.Database(dsn(scheme="mysql",
                                  user=settings.DB_USER,
                                  password=settings.DB_PASS,
                                  host=settings.DB_HOST,
                                  port=settings.DB_PORT,
                                  database=settings.DB_NAME))

# uploads not in the upload directory are in the bucket, keyed by file name
s3 = boto3.client("s3",
                  endpoint_url=settings.S3_ENDPOINT_URL,
                  region_name=settings.S3_REGION,
                  aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                  aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY)

app = fastapi.FastAPI()


@app.on_event("startup")
async def on_startup():
    await database.connect()


@app.on_event("shutdown")
async def on_shutdown():
    await database.disconnect()


# not async, so fastapi runs it in a thread while it waits on the bucket
@app.get("/{file_path:path}")
def get_file(file_path: str):
    file_path = os.path.abspath(os.path.join(settings.UPLOAD_DIR, file_path))

    # make sure the file is in the upload directory (fastapi does not protect us)
    if os.path.commonpath([file_path, settings.UPLOAD_DIR]) != settings.UPLOAD_DIR:
        return fastapi.responses.JSONResponse(status_code=fastapi.status.HTTP_404_NOT_FOUND,
                                              content={"error": "File not found"})

    if file_path == settings.UPLOAD_DIR:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "File not found"},
        )

    if os.path.exists(file_path):
        return fastapi.responses.FileResponse(file_path)

    key = os.path.relpath(file_path, settings.UPLOAD_DIR)
    try:
        upload = s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
    except s3.exceptions.NoSuchKey:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "File not found"},
        )

    # the media type FileResponse gives a file of that name
    return fastapi.responses.StreamingResponse(upload["Body"].iter_chunks(),
                                               media_type=mimetypes.guess_type(key)[0] or "text/plain",
                                               headers={"Content-Length": str(upload["ContentLength"])})


def file_exists(file_name: str) -> bool:
    if os.path.exists(os.path.join(settings.UPLOAD_DIR, file_name)):
        return True

    try:
        s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=file_name)
    except s3.exceptions.ClientError as error:
        if error.response["Error"]["Code"] == "404":
            return False
        raise

    return True


def generate_file_name(extension: str) -> str:
    num_chars = secrets.randbelow(9) + 8  # 8-16 bytes; 11-22 chars
    while True:
        file_name = f"{secrets.token_urlsafe(num_chars)}.{extension}"
        if not file_exists(file_name):
            return file_name


@app.post("/")
async def post_file(file: fastapi.UploadFile = fastapi.File(...),
                    token: str = fastapi.Header(...),
                    user_agent: str = fastapi.Header(...)):
    query = """\
        SELECT id, name, priv
          FROM users
         WHERE token = :token
           AND priv & 1
    """
    params = {"token": token}
    user = await database.fetch_one(query, params)
    if user is None:
        logger.warning("Invalid token", token=token, user_agent=user_agent)
        return fastapi.responses.JSONResponse(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            content={"error": "Forbidden"},
        )

    extension = file.filename.rsplit(".", maxsplit=1)[1]
    file_name = await fastapi.concurrency.run_in_threadpool(generate_file_name, extension)

    contents = await file.read()
    await fastapi.concurrency.run_in_threadpool(s3.put_object, Bucket=settings.S3_BUCKET_NAME,
                                                Key=file_name, Body=contents)

    #upsampling.upsample_image_file(file_path)

    file_size = len(contents)

    query = """\
        INSERT INTO uploads (name, user_id, size)
             VALUES (:name, :user_id, :size)
    """
    params = {
        "name": file_name,
        "user_id": user["id"],
        "size": file_size,
    }
    await database.execute(query, params)

    logger.info("File uploaded", file_name=file_name, file_size=file_size,
                user_id=user["id"], user_name=user["name"], user_agent=user_agent)

    return fastapi.Response(f"{settings.DOMAIN}/{file_name}".encode())
