#!/usr/bin/env python
import os.path
import secrets

import fastapi
import databases

import logger
import settings


def dsn(driver: str, user: str, password: str,
        host: str, port: int, database: str) -> str:
    return f"{driver}://{user}:{password}@{host}:{port}/{database}"


database = databases.Database(dsn(driver="mysql",
                                  user=settings.DB_USER,
                                  password=settings.DB_PASS,
                                  host=settings.DB_HOST,
                                  port=settings.DB_PORT,
                                  database=settings.DB_NAME))

app = fastapi.FastAPI()


@app.on_event("startup")
async def on_startup():
    logger.overwrite_exception_hook()
    await database.connect()


@app.on_event("shutdown")
async def on_shutdown():
    await database.disconnect()
    logger.restore_exception_hook()


@app.get("/{file_path:path}")
async def get_file(file_path: str):
    file_path = os.path.abspath(os.path.join(settings.UPLOAD_DIR, file_path))

    # make sure the file is in the upload directory (fastapi does not protect us)
    if os.path.commonpath([file_path, settings.UPLOAD_DIR]) != settings.UPLOAD_DIR:
        return fastapi.responses.JSONResponse(status_code=404,
                                              content={"error": "File not found"})

    if not os.path.exists(file_path):
        return fastapi.responses.JSONResponse(
            status_code=404,
            content={"error": "File not found"},
        )

    return fastapi.responses.FileResponse(file_path)


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
            status_code=403,
            content={"error": "Forbidden"},
        )

    extension = file.filename.rsplit(".", maxsplit=1)[1]

    num_chars = secrets.randbelow(9) + 8  # 8-16 bytes; 11-22 chars
    while True:
        file_name = f"{secrets.token_urlsafe(num_chars)}.{extension}"
        file_path = os.path.join(settings.UPLOAD_DIR, file_name)
        if not os.path.exists(file_path):
            break

    with open(file_path, "wb") as f:
        f.write(await file.read())

    query = """\
        INSERT INTO uploads (name, user_id, size)
             VALUES (:name, :user_id, :size)
    """
    params = {
        "name": file_name,
        "user_id": user["id"],
        "size": os.path.getsize(file_path),
    }
    await database.execute(query, params)

    logger.info("File uploaded", file_name=file_name, user_id=user["id"],
                user_name=user["name"], user_agent=user_agent)

    return fastapi.Response(f"{settings.DOMAIN}/{file_name}".encode())