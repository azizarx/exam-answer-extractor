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

# Never delete BUILD_DIR itself: on prod it is the bindfs source for
# /opt/aimarker/frontend/dist. rm -rf on the directory breaks the mount
# (Transport endpoint is not connected) and nginx starts returning 500.
mkdir -p "$BUILD_DIR"
# Clear previous artifacts in-place (including hidden files).
find "$BUILD_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

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
