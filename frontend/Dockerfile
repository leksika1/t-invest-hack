# syntax=docker/dockerfile:1
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Куда проксировать /api. По умолчанию — сервис backend из корневого docker-compose.yml;
# в Dokploy задаётся в frontend/docker-compose.yml через BACKEND_ALIAS
ENV BACKEND_UPSTREAM=http://backend:8000

# Шаблон: образ nginx подставляет переменные окружения (BACKEND_UPSTREAM) при старте
COPY <<'EOF' /etc/nginx/templates/default.conf.template
# Перед nginx стоит Traefik (Dokploy): адрес клиента берём из X-Forwarded-For,
# но верим ему только от прокси из частных сетей
set_real_ip_from 10.0.0.0/8;
set_real_ip_from 172.16.0.0/12;
set_real_ip_from 192.168.0.0/16;
set_real_ip_from 127.0.0.1;
real_ip_header X-Forwarded-For;
real_ip_recursive on;

server_tokens off;

server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # Stage 2: five PDF files of 30 MiB plus multipart overhead.
    client_max_body_size 160m;

    # Все стили SPA находятся в локальном CSS-файле; inline стили не нужны.
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;

    location /api/ {
        # Адрес через переменную и DNS Docker: nginx стартует, даже если бэкенд ещё не поднят
        resolver 127.0.0.11 valid=10s ipv6=off;
        set $backend_upstream ${BACKEND_UPSTREAM};
        proxy_pass $backend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        # Сбор карточки (документы + LLM) может идти долго
        proxy_read_timeout 300s;
    }

    # Сборка vite: имена с хешем — кэшируем надолго
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable" always;
        add_header X-Content-Type-Options "nosniff" always;
        try_files $uri =404;
    }

    # index.html (и SPA-маршруты) — всегда перепроверять, иначе после деплоя браузер держит старую сборку.
    # Именно expires, а не add_header: add_header в location отменил бы заголовки безопасности уровня server.
    location / {
        expires -1;
        try_files $uri $uri/ /index.html;
    }
}
EOF

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
