# Build stage
FROM node:20-alpine AS build
WORKDIR /app

# Production API host (frontend is on aimarker.seamo-official.org)
ARG VITE_API_BASE_URL=https://aimarker-bk.seamo-official.org
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
