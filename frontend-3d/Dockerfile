# 第一階段：建置 React（tsc -b 會檢查 dev/ 與 vite.config.ts，所以整個目錄都要進來）
FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# 第二階段：使用 Caddy 提供網站
FROM caddy:2-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy
