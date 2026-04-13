# Hearth Docker Compose Patch — Session J2

## Changes needed in F:\DockerDesktop\docker-compose.yml on Hearth

### 1. Paperless-ngx service — add volume mount + env vars

```yaml
  paperless-ngx:
    # ... existing config ...
    volumes:
      # ... existing volumes ...
      - L:/docker/paperless/scripts:/usr/src/paperless/scripts:ro
    environment:
      # ... existing env vars ...
      - PAPERLESS_POST_CONSUME_SCRIPT=/usr/src/paperless/scripts/bridge_to_kb.py
      - PAPERLESS_TOKEN=${PAPERLESS_TOKEN}
      - BIFROST_KB_URL=http://bifrost-kb:8100
```

### 2. Install httpx in paperless container

```bash
docker exec paperless-ngx pip install httpx
```

### 3. Create scripts directory on Hearth

```powershell
New-Item -ItemType Directory -Force -Path "L:\docker\paperless\scripts"
Copy-Item "bridge_to_kb.py" "L:\docker\paperless\scripts\bridge_to_kb.py"
```

### 4. Create Paperless tags via API

```bash
PAPERLESS_TOKEN="<token>"
for tag in personal client rfp; do
  curl -X POST "http://localhost:8000/api/tags/" \
    -H "Authorization: Token $PAPERLESS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$tag\"}"
done
```

### 5. Create inbox subfolders

```powershell
"personal","client","rfp-vault" | ForEach-Object {
    New-Item -ItemType Directory -Force -Path "F:\Documents\paperless-inbox\$_"
}
```

### 6. Recreate containers

```bash
cd F:\DockerDesktop
docker compose --profile ai up -d paperless-ngx
```

### 7. Test

```bash
# Drop a test file
cp test.pdf "F:\Documents\paperless-inbox\client\"
# Wait ~90 seconds for Paperless to consume
# Check bifrost-kb
curl http://localhost:8100/projects
```

## Block 2 — Cloud_AI Direct Watch Path

### bifrost-kb container mount addition:

```yaml
  bifrost-kb:
    # ... existing config ...
    volumes:
      # ... existing volumes ...
      - F:/Documents/Cloud_AI:/data/inbox-cloud-ai:ro
```

### pipeline.py patch (inside bifrost-kb container):

Add INBOX_PATHS env var support (comma-separated). Each inbox maps to project by folder name:
- `/data/inbox` → `default`
- `/data/inbox-cloud-ai` → `cloud-ai`

**READ pipeline.py FROM CONTAINER BEFORE PATCHING** — reranker + fact-tier code must be preserved.

### Rebuild

```bash
docker compose --profile ai up -d --build bifrost-kb
```

### Test

```bash
cp test.txt "F:\Documents\Cloud_AI\"
# Wait for watch cycle
curl http://localhost:8100/projects  # should show "cloud-ai"
```
