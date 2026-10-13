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
- No email or password-reset-by-email feature: forgotten passwords currently require an administrator recovery workflow. Browser push notifications are not included.
- The 500 MB Railway volume also holds files, so file storage is limited; back up the volume regularly. Uploaded files are not virus-scanned, so users should only download files they trust. For a large public community, add malware scanning, user quotas, and S3 object storage.
- Restrict deployment and protect TLS certificates; see `COOKIE_SECURE` above. Add production observability, abuse controls, and backups before operating a large public community.

## Automated verification

```bash
pip install pytest httpx
pytest -q
```

Tests cover signup, owner role, login, failed logins, channels, WebSocket message delivery and typing events, DMs, profile updates, avatar validation, password changes, logout, post publication/deletion, message/post attachments, private-file access rules, file-type/size limits, and owner moderation (ban/unban).


## Voice channels and screen streaming

- Join **Lounge**, **Gaming**, or an owner-created voice room from the left sidebar.
- A microphone permission prompt appears on join. If it is declined, listen-only mode is available. Use **Mute**, **Call view**, and **Leave** in the voice dock. Voice stays connected while switching text channels.
- **Share screen** opens the browser's trusted display/window/tab picker. Sharing stops immediately when the browser's sharing indicator is stopped, the user clicks **Stop sharing**, or they leave. Remote streams appear in Call view. No recording or file storage of live media is performed.
- Only the verified owner can create and delete voice rooms; users who are banned lose any active voice connection.
- A voice room supports up to **8** simultaneous users. Group calls use peer-to-peer WebRTC mesh, so streaming and audio load increase as more users join.
- `WEBRTC_ICE_SERVERS` (optional): JSON array passed to WebRTC RTCPeerConnection, e.g. `[{"urls":"stun:stun.l.google.com:19302"}]`. Default Google STUN servers are configured automatically. **Reliable cross-network calls require your own TURN relay**, especially behind symmetric NAT / restrictive corporate or mobile networks. Use temporary, restricted TURN credentials. Without TURN, some calls will fail; WebRTC does not guarantee connectivity over STUN alone.
- Railway deployment must use **one application replica**: room membership and signaling are in memory. If scaling to multiple replicas, replace in-memory membership and signaling with shared infrastructure. Requires HTTPS for microphone and screen capture on a public domain; tab/window/display picker support depends on browser and operating system. Screen sharing captures video only; desktop/system audio is not included.

## Voice call view and join/leave sounds

- Joining a voice room opens a full-size Discord-style call screen with participant avatar tiles, room name, connected status, and a control toolbar (mute, share screen, disconnect). The screen stays independent of the fixed channel sidebar.
- Use **Text chat** or **Hide call** to return to text channels without leaving voice. **Call view** returns to the call.
- Two short, locally synthesized tone cues play when users join/leave the room. No external sound files are downloaded. A **speaker** toggle in the call header disables/enables cues; the preference persists in the browser's local storage.
- On mobile the call screen adjusts to a narrow viewport with controls reachable at the bottom.
- Media connectivity still depends on WebRTC and network conditions; a TURN relay is recommended for reliable public calls.

### Frontend preview test

```bash
python tests/browser_voice_v7_mock.py
```

This Playwright preview checks the layout and UI behavior using simulated WebSocket/media inputs. It is not a substitute for testing audio/video across real devices and networks.

## Mr H Media v8: social features

This upgrade is **additive**. `app.py` installs `v8.py` on startup, upgrading the existing SQLite database in `/data` without dropping accounts or historical messages. Keep the existing single Railway replica and `/data` volume.

- **Roles:** Owner, Admin, Moderator, VIP and Member defaults; owner/admin can create roles with custom names, colors and explicit permissions; authorized users can assign them from a member's profile. The Owner role cannot be granted through the API. Permissions include `manage_channels`, `moderate`, `ban_users`, `manage_emoji`, `manage_roles`, and `manage_posts`.
- **Friends:** requests, approval, removal and blocking; blocking also prevents new one-to-one messages. New group chats can include up to eight participants and allow members to exchange messages and files. Group voice rooms are private to group members. Existing public voice rooms and screen sharing remain available.
- **Reactions and GIFs:** react to messages with emoji, upload custom emoji as an authorized moderator, and share GIFs in chat using the attachment button. GIF uploads retain their animation; searchable external GIF catalogs are not bundled.
- **Notifications:** unread markers for channels, DMs and groups, mention/friend/group alerts, account notification preferences and opt-in desktop notifications while the website is open. This is **not** offline mobile push notification support.
- **Profiles:** custom banners, status text, role badges, profile colors and animated GIF avatars. Roles appear as colored name badges.
- **Moderation:** owner/admin bans, role-scoped timeouts, public message removal, channel slowmode, audit log, and signup anti-raid limiting. Moderators cannot inspect or delete someone else's private DMs.
- **Threads & pins:** select a message's **Thread** button to view its nested replies, use **Reply** to continue the conversation, pin/unpin public messages with moderation permission and search public channel history.

### Verification

```bash
python -m pytest -q
python tests/browser_v8_mock.py
```

The Playwright UI smoke test uses mocked browser networking. Real WebRTC connections across devices still need separate testing; configure `WEBRTC_ICE_SERVERS` with working TURN credentials if public calls must connect reliably across restrictive networks.
