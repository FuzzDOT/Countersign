# ──────────────────────────────────────────────────────────────────────────────
# COUNTERSIGN frontend
#
# Stages:
#   deps  — node_modules only (cached; busts on lockfile change)
#   dev   — vite dev server with HMR
#   build — produces /app/dist, copied into a volume by the prod overlay
#   serve — optional standalone nginx, if you'd rather not serve from FastAPI
# ──────────────────────────────────────────────────────────────────────────────

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile exists, `npm install` on first ever run.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi


FROM deps AS dev
WORKDIR /app
COPY . .
EXPOSE 5173
# --host so the port is reachable from outside the container.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]


FROM deps AS build
WORKDIR /app
COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD wget -qO- http://localhost/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
