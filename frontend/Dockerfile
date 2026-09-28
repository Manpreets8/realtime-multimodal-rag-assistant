# Frontend image: the production build of the React app, served by nginx, which also
# reverse-proxies /api (including the chat WebSocket) to the backend.

# --- build ------------------------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Same-origin API: the browser talks to nginx, which forwards /api to the backend.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --- runtime: unprivileged nginx (listens on 8080, runs as uid 101) ----------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
# Rendered to /etc/nginx/conf.d/default.conf at start-up by the image's envsubst step.
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
ENV TRUSTED_PROXY_CIDR=127.0.0.1/32
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
