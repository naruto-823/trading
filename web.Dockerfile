FROM caddy:2.10-alpine

COPY Caddyfile /etc/caddy/Caddyfile
# The deployment workflow builds the frontend during project checks. Copying the
# verified artifact keeps production VPS builds tiny and avoids a second Vite build.
COPY frontend/dist /srv

EXPOSE 80 443
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1:2019/config/ || exit 1
