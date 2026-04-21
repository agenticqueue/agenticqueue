# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS builder

WORKDIR /workspace
ENV NEXT_TELEMETRY_DISABLED=1

RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json

RUN npm ci --workspace=apps/web --include-workspace-root

COPY apps/web ./apps/web

RUN npm run build --workspace=@agenticqueue/web

FROM node:22-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

RUN apk add --no-cache curl wget

COPY --from=builder --chown=node:node /workspace/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /workspace/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=node:node /workspace/apps/web/public ./apps/web/public

USER node

EXPOSE 3000

CMD ["node", "apps/web/server.js"]
