#!/bin/sh
# Hand-written stdlib reference for the blackbox grader tests: serves on $HOST:$PORT,
# reads the instance from $INSTANCE_JSON (falling back to ./instance.json).
exec python3 "$(dirname "$0")/server.py"
