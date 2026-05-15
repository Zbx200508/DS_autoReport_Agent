# Render Deployment

Internal demo deployment for the Hisense automated report workbench.

## Build Command

```bash
pip install -r requirements.txt
```

## Start Command

```bash
python -m uvicorn web_app.main:app --host 0.0.0.0 --port $PORT
```

## Environment Variables

```env
APP_ENV=production
APP_USERNAME=admin
APP_PASSWORD=
APP_SESSION_SECRET=
MCP_SERVER_URL=
MCP_AUTHORIZATION=
ARK_API_KEY=
OUTPUT_BASE_DIR=/var/data/outputs
```

Use a Render Persistent Disk mounted at `/var/data` so generated reports and
`report_registry.json` survive service restarts.

Do not commit `.env` files or real credentials.
