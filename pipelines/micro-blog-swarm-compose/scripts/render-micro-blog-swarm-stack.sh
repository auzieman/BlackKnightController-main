#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  render-micro-blog-swarm-stack.sh SOURCE_DIR OUTPUT_FILE REGISTRY_HOST IMAGE_TAG [PUBLISH_UI_PORT] [PUBLISH_API_PORT]

Example:
  render-micro-blog-swarm-stack.sh /home/auzieman/Projects/micro-blog /tmp/micro-blog.stack.yml swarm1.lab.auzietek.com:5001 candidate 18081 18080

This renders a Swarm-safe stack file from the micro-blog Compose intent.
It intentionally replaces local build directives with registry image refs.
USAGE
}

if [[ $# -lt 4 ]]; then
  usage
  exit 2
fi

source_dir="$1"
output_file="$2"
registry_host="$3"
image_tag="$4"
publish_ui_port="${5:-18081}"
publish_api_port="${6:-18080}"

if [[ ! -d "${source_dir}" ]]; then
  echo "Source directory not found: ${source_dir}" >&2
  exit 1
fi

for required in \
  "${source_dir}/src/api/Dockerfile" \
  "${source_dir}/src/worker/Dockerfile" \
  "${source_dir}/src/projection/Dockerfile" \
  "${source_dir}/src/ui/Dockerfile" \
  "${source_dir}/collector/otel-collector-local.yaml"; do
  if [[ ! -f "${required}" ]]; then
    echo "Required file missing: ${required}" >&2
    exit 1
  fi
done

mkdir -p "$(dirname "${output_file}")"

cat > "${output_file}" <<YAML
networks:
  app_net:
    driver: overlay
    attachable: true

volumes:
  rabbitmq-data:
  redis-data:
  postgres-data:

configs:
  otel_collector_local:
    file: ./collector/otel-collector-local.yaml

services:
  rabbitmq:
    image: rabbitmq:3.12-management
    environment:
      RABBITMQ_DEFAULT_USER: "\${RABBITMQ_DEFAULT_USER:-guest}"
      RABBITMQ_DEFAULT_PASS: "\${RABBITMQ_DEFAULT_PASS:-guest}"
    volumes:
      - rabbitmq-data:/var/lib/rabbitmq
    networks:
      - app_net
    deploy:
      placement:
        constraints:
          - node.labels.bkc.workload == app

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis-data:/data
    networks:
      - app_net
    deploy:
      placement:
        constraints:
          - node.labels.bkc.workload == app

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: "microblog"
      POSTGRES_USER: "blog"
      POSTGRES_PASSWORD: "\${POSTGRES_PASSWORD:-Str0ngP@ssword!}"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - app_net
    deploy:
      placement:
        constraints:
          - node.labels.bkc.workload == app

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.103.0
    command: ["--config=/etc/otel-collector.yaml"]
    configs:
      - source: otel_collector_local
        target: /etc/otel-collector.yaml
    ports:
      - "4317:4317"
      - "4318:4318"
      - "9464:9464"
    networks:
      app_net:
        aliases:
          - otel-collector
    deploy:
      placement:
        constraints:
          - node.labels.bkc.workload == app

  blog-api:
    image: ${registry_host}/micro-blog/blog-api:${image_tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=\${POSTGRES_PASSWORD:-Str0ngP@ssword!} host=postgres port=5432"
      REDIS_URL: "redis://redis:6379/0"
      RABBITMQ_URL: "amqp://\${RABBITMQ_DEFAULT_USER:-guest}:\${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://\${OTEL_COLLECTOR_SERVICE:-otel-collector}:4318"
      OTEL_ENVIRONMENT: "\${OTEL_ENVIRONMENT:-lab}"
      SERVICE_NAMESPACE: "microblog"
      ADMIN_EMAIL: "\${ADMIN_EMAIL:-admin@example.invalid}"
      CONTENT_IMPORT_ROOT: "/content"
      CONTENT_PUBLIC_BASE: "/content-files"
      AUTO_IMPORT_FILESYSTEM_ON_BOOT: "\${AUTO_IMPORT_FILESYSTEM_ON_BOOT:-false}"
    volumes:
      - /srv/micro-blog/content:/content
    ports:
      - "${publish_api_port}:8080"
    networks:
      - app_net
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.bkc.workload == app

  blog-worker:
    image: ${registry_host}/micro-blog/blog-worker:${image_tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=\${POSTGRES_PASSWORD:-Str0ngP@ssword!} host=postgres port=5432"
      RABBITMQ_URL: "amqp://\${RABBITMQ_DEFAULT_USER:-guest}:\${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://\${OTEL_COLLECTOR_SERVICE:-otel-collector}:4318"
      OTEL_ENVIRONMENT: "\${OTEL_ENVIRONMENT:-lab}"
      SERVICE_NAMESPACE: "microblog"
    networks:
      - app_net
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.bkc.workload == app

  blog-projection:
    image: ${registry_host}/micro-blog/blog-projection:${image_tag}
    env_file: .env
    environment:
      DATABASE_URL: "dbname=microblog user=blog password=\${POSTGRES_PASSWORD:-Str0ngP@ssword!} host=postgres port=5432"
      REDIS_URL: "redis://redis:6379/0"
      RABBITMQ_URL: "amqp://\${RABBITMQ_DEFAULT_USER:-guest}:\${RABBITMQ_DEFAULT_PASS:-guest}@rabbitmq:5672/%2F"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://\${OTEL_COLLECTOR_SERVICE:-otel-collector}:4318"
      OTEL_ENVIRONMENT: "\${OTEL_ENVIRONMENT:-lab}"
      SERVICE_NAMESPACE: "microblog"
    networks:
      - app_net
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.bkc.workload == app

  blog-ui:
    image: ${registry_host}/micro-blog/blog-ui:${image_tag}
    env_file: .env
    environment:
      BLOG_API_BASE_URL: "http://blog-api:8080"
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://\${OTEL_COLLECTOR_SERVICE:-otel-collector}:4318"
      OTEL_ENVIRONMENT: "\${OTEL_ENVIRONMENT:-lab}"
      SERVICE_NAMESPACE: "microblog"
      ADMIN_EMAIL: "\${ADMIN_EMAIL:-admin@example.invalid}"
      ADMIN_ACCESS_CODE: "\${ADMIN_ACCESS_CODE:-local-admin}"
      FLASK_SECRET_KEY: "\${FLASK_SECRET_KEY:-change-me-before-deploy}"
      GOOGLE_CLIENT_ID: "\${GOOGLE_CLIENT_ID:-}"
      GOOGLE_CLIENT_SECRET: "\${GOOGLE_CLIENT_SECRET:-}"
      DEFAULT_THEME_VARIANT: "\${DEFAULT_THEME_VARIANT:-midnight}"
      CONTENT_IMPORT_ROOT: "/content"
    volumes:
      - /srv/micro-blog/content:/content:ro
    ports:
      - "${publish_ui_port}:8080"
    networks:
      - app_net
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.bkc.workload == app
YAML

echo "Rendered ${output_file}"
