services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - ${dictionary.runtime_mount}/redis:/data

  bkc:
    build:
      context: ${dictionary.target_root}/source
      dockerfile: Dockerfile
    restart: unless-stopped
    ports:
      - "${dictionary.bkc_public_port}:5000"
    env_file:
      - ${dictionary.env_path}
    depends_on:
      - redis
    volumes:
      - ${dictionary.runtime_mount}/dictionaries:/app/dictionaries
      - ${dictionary.runtime_mount}/file_templates:/app/file_templates
      - ${dictionary.runtime_mount}/keys:/app/keys
      - ${dictionary.runtime_mount}/pipelines:/app/runtime/pipelines

  bkc-worker:
    build:
      context: ${dictionary.target_root}/source
      dockerfile: Dockerfile
    restart: unless-stopped
    command: ["python", "bkc_worker.py"]
    env_file:
      - ${dictionary.env_path}
    depends_on:
      - redis
      - bkc
    volumes:
      - ${dictionary.runtime_mount}/dictionaries:/app/dictionaries
      - ${dictionary.runtime_mount}/file_templates:/app/file_templates
      - ${dictionary.runtime_mount}/keys:/app/keys
      - ${dictionary.runtime_mount}/pipelines:/app/runtime/pipelines
