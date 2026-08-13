#!/bin/bash

set -e

IMAGE_NAME="aimarker-frontend-build"
CONTAINER_NAME="aimarker-frontend-build-temp"
BUILD_DIR="/home/auto-deploy/do-not-delete/frontend/dist"

echo "==> Building Docker image..."

docker build -t "$IMAGE_NAME" .

echo "==> Removing old temporary container if exists..."

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

echo "==> Creating temporary container..."

docker create --name "$CONTAINER_NAME" "$IMAGE_NAME" >/dev/null

echo "==> Preparing build directory..."

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "==> Copying dist from Docker container..."

docker cp "$CONTAINER_NAME:/app/dist/." "$BUILD_DIR/"

echo "==> Removing temporary container..."

docker rm "$CONTAINER_NAME" >/dev/null

echo ""
echo "======================================"
echo "Frontend build completed successfully"
echo "Build copied to:"
echo "$BUILD_DIR"
echo "======================================"

ls -lah "$BUILD_DIR"
