# Mr H Media

Dark, Discord-inspired community chat site. **Real accounts, permanent message history, real-time WebSocket chat, private messages, editable profiles, and avatar uploads.** This is a full-stack website, not a static design mock-up.

## Features

- Register and log in with username and password (secure scrypt password hashing)
- HTTP-only login cookie, persisted session, log out, and account password change
- Channels: `#general`, `#introductions`, `#creations`, `#help`
- Realtime group chat and 1-to-1 direct messages (WebSockets)
- See who's online, typing indicators, message history, and older messages
- User search, public profiles, editable display name, username and bio
- Custom profile images (PNG/JPG/WEBP/GIF; auto-optimized to square-friendly 256px PNG)
- Responsive dark theme for desktop and mobile
- Verified **OWNER** role for the provisioned `Mr_H` account; only the owner can create or remove channels (except `#general`)
- **Attachments:** upload/download files (up to 8 MB) in channels, DMs, and posts; images preview inline. Private DM downloads are restricted to DM participants. Unsafe content types download as attachments instead of running in the browser.
- **Community posts:** members can publish posts with optional file uploads; authors can delete their posts, and the owner can moderate any post.
- **Moderation:** owner-only user list, ban and unban with optional reason; banning revokes the user's sessions and closes their active WebSocket connections.
- SQLite persistence: survives restarts when the data directory is persisted

## Local setup

Requires **Python 3.10+**. For a one-click local start, run `start-windows.bat` on Windows or `./start-mac-linux.sh` on macOS/Linux (it installs dependencies automatically). Alternatively, inside the project folder:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Click **Create account**, choose a username and password (8+ characters), then start chatting. You can open two browsers or use a private browsing window to test messages between accounts.

## Provision the owner account

Before **first launch**, set both environment variables:

```bash
# macOS/Linux example
export OWNER_USERNAME=Mr_H
export OWNER_PASSWORD='put-a-long-unique-secret-here'
uvicorn app:app --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
$env:OWNER_USERNAME = 'Mr_H'
$env:OWNER_PASSWORD = 'put-a-long-unique-secret-here'
uvicorn app:app --host 127.0.0.1 --port 8000
```

The server automatically **creates** the owner with a verified owner badge. **If the site has already started**, run `python admin.py setup-owner --username Mr_H` on the server to configure or recover the owner interactively (promoting an existing account requires the explicit `PROMOTE` confirmation). For safety, an existing user **cannot** receive owner privileges just by matching the environment username. If someone already claimed `Mr_H`, use a fresh database (before public launch) or manually provision it in a controlled migration. `OWNER_PASSWORD` is **not** a recurring password reset; change the password under **Settings → Security** after login.

## Deploy with Docker / Railway / VPS

Docker:

```bash
docker build -t mr-h-media .
docker run -p 8000:8000 -v mrh-data:/data \
  -e OWNER_USERNAME=Mr_H -e OWNER_PASSWORD='your-long-secret' \
  mr-h-media
```

Visit http://localhost:8000. `COOKIE_SECURE=1` is enabled in Docker, so **login cookies will only work over HTTPS**. For plain local HTTP testing with Docker, add `-e COOKIE_SECURE=0`. Keep it **1 for public HTTPS**.

For Railway, deploy this repository with its Dockerfile, set `OWNER_USERNAME`, `OWNER_PASSWORD`, and `COOKIE_SECURE=1`, attach a **persistent volume mounted at `/data`**, and enable a public HTTPS domain. Keep **one replica / one Uvicorn worker**: the WebSocket broadcasting layer is process-local. Deploying without a volume will erase accounts and messages on container replacement. Set HTTPS at the reverse proxy.

## Data & operations

- `DATA_DIR` defaults to `./data`; SQLite database is `media.sqlite3`, with profile pictures at `uploads/`.
- The site is not using any third-party auth provider. Passwords are not stored in plaintext.
- To back up your deployment, back up the mounted data volume (including SQLite WAL/shm files or use SQLite's online backup API).
- Requires sticky single-process WebSocket messaging; for multi-replica scale, use a message broker such as Redis and move persistent data to PostgreSQL.
- No email or password-reset-by-email feature: forgotten passwords currently require an administrator recovery workflow. Voice calls and push notifications are not included.
- The 500 MB Railway volume also holds files, so file storage is limited; back up the volume regularly. Uploaded files are not virus-scanned, so users should only download files they trust. For a large public community, add malware scanning, user quotas, and S3 object storage.
- Restrict deployment and protect TLS certificates; see `COOKIE_SECURE` above. Add production observability, abuse controls, and backups before operating a large public community.

## Automated verification

```bash
pip install pytest httpx
pytest -q
```

Tests cover signup, owner role, login, failed logins, channels, WebSocket message delivery and typing events, DMs, profile updates, avatar validation, password changes, logout, post publication/deletion, message/post attachments, private-file access rules, file-type/size limits, and owner moderation (ban/unban).
