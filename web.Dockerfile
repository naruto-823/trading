FROM node:22-alpine AS build

WORKDIR /src
RUN corepack enable && corepack prepare pnpm@11.15.1 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/.pnpmrc.json ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

FROM caddy:2.10-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv

EXPOSE 80 443
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1:2019/config/ || exit 1
