#!/usr/bin/env bash

source .env

uvicorn main:app --host $APP_HOST --port $APP_PORT --reload
