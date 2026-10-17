# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-bookworm-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG BACKEND_URL=http://127.0.0.1:8000
ENV BACKEND_URL=$BACKEND_URL \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
RUN mkdir -p public && npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim AS runner
RUN groupadd --system --gid 10001 nextjs \
    && useradd --system --uid 10001 --gid 10001 --create-home nextjs
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder --chown=10001:10001 /app/package.json ./package.json
COPY --from=builder --chown=10001:10001 /app/next.config.ts ./next.config.ts
COPY --from=builder --chown=10001:10001 /app/public ./public
COPY --from=builder --chown=10001:10001 /app/.next ./.next
COPY --from=builder --chown=10001:10001 /app/node_modules ./node_modules
COPY --from=deps --chown=10001:10001 /app/node_modules/typescript ./node_modules/typescript
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["sh", "-c", "exec ./node_modules/.bin/next start -H 0.0.0.0 -p ${PORT:-3000}"]
