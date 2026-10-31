#!/usr/bin/env python
import atexit
import contextlib
import hashlib
import mimetypes
import re
import secrets

import aiobotocore.session
import fastapi
import databases

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

# letters and digits only, so a file name is never more than one path segment; up to 9 so the longest name
# (22 characters, a dot and the extension) fits uploads.name
EXTENSION = re.compile(r"[A-Za-z0-9]{1,9}")

# every upload gets a name of its own and never changes, so browsers and Cloudflare may keep it for good
CACHE_CONTROL = "public, max-age=31536000, immutable"


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    global s3
    await database.connect()

    # the uploads, keyed by file name
    async with aiobotocore.session.get_session().create_client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
    ) as s3:
        yield

    await database.disconnect()


app = fastapi.FastAPI(lifespan=lifespan)


# one path segment: a slash, even percent-encoded, matches no route
@app.get("/{file_name}")
async def get_file(file_name: str, byte_range: str | None = fastapi.Header(None, alias="range")):
    # the bucket parses the range, and answers with the part (and its Content-Range) when it is satisfiable
    try:
        upload = await s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=file_name,
                                     **({"Range": byte_range} if byte_range else {}))
    except s3.exceptions.NoSuchKey:
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "File not found"},
        )
    except s3.exceptions.ClientError as error:
        if error.response["Error"]["Code"] != "InvalidRange":
            raise
        return fastapi.responses.JSONResponse(
            status_code=fastapi.status.HTTP_416_RANGE_NOT_SATISFIABLE,
            content={"error": "Range not satisfiable"},
        )

    headers = {
        "Content-Length": str(upload["ContentLength"]),
        "Accept-Ranges": "bytes",
        "Cache-Control": CACHE_CONTROL,
    }
    if "ContentRange" in upload:
        headers["Content-Range"] = upload["ContentRange"]

    async def contents():
        async with upload["Body"] as body:
            async for chunk in body.iter_chunks(1 << 16):
                yield chunk

    return fastapi.responses.StreamingResponse(contents(),
                                               status_code=206 if "ContentRange" in upload else 200,
                                               media_type=mimetypes.guess_type(file_name)[0] or "text/plain",
                                               headers=headers)


async def generate_file_name(extension: str) -> str:
    num_chars = secrets.randbelow(9) + 8  # 8-16 bytes; 11-22 chars
    while True:
        file_name = f"{secrets.token_urlsafe(num_chars)}.{extension}"
        try:
            await s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=file_name)
        except s3.exceptions.ClientError as error:
            if error.response["Error"]["Code"] == "404":
                return file_name
            raise


@app.post("/")
async def post_file(file: fastapi.UploadFile = fastapi.File(...),
                    token: str = fastapi.Header(...),
                    user_agent: str = fastapi.Header(...)):
    query = """\
        SELECT id, name, priv
          FROM users
         WHERE token_hash = :token_hash
           AND priv & 1
    """
    # stored as their SHA-256, so the table holds no token that works
    params = {"token_hash": hashlib.sha256(token.encode()).hexdigest()}
    user = await database.fetch_one(query, params)
    if user is None:
        logger.warning("Invalid token", user_agent=user_agent)
        return fastapi.responses.JSONResponse(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            content={"error": "Forbidden"},
        )

    _, dot, extension = file.filename.rpartition(".")
    if not dot or not EXTENSION.fullmatch(extension):
        return fastapi.responses.JSONResponse(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid file extension"},
        )

    file_name = await generate_file_name(extension)

    # streamed from the temporary file the upload was received into, rather than read into memory
    await s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=file_name, Body=file.file)

    file_size = file.size

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
