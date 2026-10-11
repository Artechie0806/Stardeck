# Caddy with the app (built here) baked in. Build context: the repo root.
FROM node:22-alpine AS app
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=app /app/dist /srv/app
