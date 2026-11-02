# Browser tests need a glibc platform supported by the lockfile's Playwright.
# No package version changes: install the browser revision from npm ci only.
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
RUN ./node_modules/.bin/playwright install --with-deps chromium
COPY apps/web/ .
RUN npm run typecheck && npm run test && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
