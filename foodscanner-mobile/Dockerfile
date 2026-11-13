# Stage 1: Build Expo Web bundle
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies first for layer caching
COPY package*.json ./
RUN npm ci

# Copy mobile source code
COPY . .

# Pass backend API base URL relative to gateway (default: /api)
ARG EXPO_PUBLIC_API_BASE_URL=/api
ENV EXPO_PUBLIC_API_BASE_URL=${EXPO_PUBLIC_API_BASE_URL}

# Export static web bundle
RUN npx expo export --platform web

# Stage 2: Serve via NGINX Alpine
FROM nginx:1.27-alpine AS runner

WORKDIR /usr/share/nginx/html

# Clean default assets and copy compiled Expo distribution
RUN rm -rf ./*
COPY --from=builder /app/dist .

# Copy internal SPA server configuration
COPY nginx-web.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:80/index.html || exit 1

CMD ["nginx", "-g", "daemon off;"]
