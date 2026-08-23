# Production-Ready Spotify-like Music App Backend

A production-grade, asynchronous backend for a Spotify-like music application built with **FastAPI**, **Firebase Authentication**, **Supabase PostgreSQL (Async SQLAlchemy)**, **Redis (Caching & Pub/Sub)**, **WebSockets (Multi-device playback synchronization)**, an intelligent **Recommendation Engine**, and an integrated **Gaana audio streaming & AES decryption catalog**.

---

## 1. Architecture Overview

```mermaid
flowchart TD
    Client[Client / Flutter Mobile / Next.js Web] -->|HTTPS Requests + Bearer Token| FastAPI[FastAPI App - app/main.py]
    Client <-->|WebSockets: /ws/player/{device_id}| WSServer[WebSocket Connection Manager]

    subgraph "Authentication & Security"
        FastAPI --> AuthMiddleware[Firebase Auth Middleware]
        AuthMiddleware --> FirebaseAdmin[Firebase Admin SDK - verify_id_token]
    end

    subgraph "Application Core & Services"
        FastAPI --> AuthService[Auth Service]
        FastAPI --> UserService[User & Analytics Service]
        FastAPI --> DeviceService[Device & Session Manager]
        FastAPI --> PlaybackService[Playback & Player Service]
        FastAPI --> HistoryService[Listening & Search History Service]
        FastAPI --> LibraryService[Library: Likes / Saved / Follows]
        FastAPI --> PlaylistService[Playlist & Reorder Service]
        FastAPI --> RecService[Recommendation Engine]
        FastAPI --> CatalogService[GaanaPy Catalog & Stream Decryption]
    end

    subgraph "Data & Realtime Layer"
        AuthService & UserService & DeviceService & PlaybackService & HistoryService & LibraryService & PlaylistService --> SupabaseDB[(Supabase PostgreSQL / asyncpg)]
        PlaybackService & WSServer <--> RedisPubSub[(Redis Cache & Pub/Sub)]
        CatalogService --> GaanaAPI[(Gaana Live Audio Streams / AES-CBC)]
    end
```

---

## 2. Database Design & ER Diagram

```mermaid
erDiagram
    users ||--o{ user_preferences : has
    users ||--o{ devices : owns
    users ||--o{ user_sessions : has
    users ||--o{ playlists : creates
    users ||--o| current_playback : tracks
    users ||--o{ playback_events : logs
    users ||--o{ listening_history : records
    users ||--o{ search_history : logs
    users ||--o{ liked_songs : likes
    users ||--o{ saved_albums : saves
    users ||--o{ followed_artists : follows
    users ||--o| user_behavior_profiles : computes

    playlists ||--o{ playlist_songs : contains
    songs ||--o{ playlist_songs : included_in
    artists ||--o{ songs : produces
    albums ||--o{ songs : contains
    artists ||--o{ albums : releases
    songs ||--o{ liked_songs : liked_by
    albums ||--o{ saved_albums : saved_by
    artists ||--o{ followed_artists : followed_by
```

---

## 3. Key Features

### 🔐 Firebase Authentication & Profile Synchronization
- **Token Verification**: Validates Firebase Bearer ID tokens on every authenticated request via `firebase-admin`.
- **Identity Isolation**: Firebase handles identity exclusively; passwords are never stored in PostgreSQL.
- **Auto-Sync**: Automatically synchronizes user email, display name, and avatar, initializing preferences and playback states on first sign-in.
- **Privacy Controls & GDPR**: Account deletion purges both PostgreSQL records and Firebase Auth users.

### 📱 Device Management & Remote Sessions
- **Device Registry**: Tracks connected devices (`mobile`, `tablet`, `desktop`, `web`, `tv`), OS version, app version, and push tokens.
- **Online Detection**: Real-time heartbeat endpoint (`POST /api/devices/{device_id}/heartbeat`).
- **Remote Logout**: Ability to revoke remote sessions and remove individual devices.

### 🎵 Playback & Multi-Device Real-Time Sync
- **State Machine**: Understands `playing`, `paused`, `stopped`, and `buffering` states, track queues, shuffle, repeat, and volume.
- **Telemetry Event Log**: Granular event ingestion (`PLAY`, `PAUSE`, `RESUME`, `SEEK`, `SKIP`, `NEXT`, `PREVIOUS`, `STOP`, `BUFFER_START`, `BUFFER_END`, `COMPLETE`).
- **WebSocket Synchronization**: Real-time state broadcasting across all active devices on `/ws/player/{device_id}` backed by Redis Pub/Sub.

### 📊 Listening History & 30s/50% Rule
- A track only counts as a meaningful listen if **`duration >= 30 seconds`** OR **`completion >= 50%`**.
- Casual skips and partial listens are cataloged separately without skewing recommendation affinity.

### 🧠 Recommendation Engine & Dynamic Mixes
- **Multi-Factor Affinity Scoring Formula**:
  $$\text{Score} = \text{ArtistAffinity} + \text{GenreAffinity} + \text{LanguageAffinity} + \text{MoodAffinity} + \text{ListenFreq} + \text{CompletionRate} + \text{LikeScore} - \text{SkipPenalty} - \text{RepetitionPenalty}$$
- **Curated Home Mixes**:
  - `Made For You`
  - `Recently Played`
  - `Because You Listened To <Favorite Artist>`
  - `Your Daily Mix`
  - `Discover Weekly`
  - `Trending Now` & `New Releases`
  - `Mood Mix` (Chill, Workout, Focus, Party, etc.)
  - `Language Mix`

### 🔓 Stream Decryption & Music Catalog
- Integrated [Gaana](https://gaana.com) scraping and AES-CBC stream URL decryption engine.
- Instant access to multi-bitrate master `.m3u8` streams (`16k`, `64k`, `128k`, `320k`).

---

## 4. API Endpoints Reference

All responses follow the unified response structure:
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "timestamp": "2026-08-23T10:00:00Z"
}
```

### Authentication (`/api/auth`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/auth/me` | Get authenticated user profile |
| `POST` | `/api/auth/sync` | Sync profile metadata from Firebase |
| `DELETE` | `/api/auth/account` | Permanently delete account and data |

### Users & Preferences (`/api/users`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/users/preferences` | Retrieve user preferences |
| `PATCH` | `/api/users/preferences` | Update audio quality, crossfade, languages, explicit filters |
| `GET` | `/api/users/analytics` | Retrieve listening behavior and top analytics |

### Devices (`/api/devices`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/devices` | List all registered devices |
| `POST` | `/api/devices/register` | Register/update a device and create session |
| `POST` | `/api/devices/{device_id}/heartbeat` | Send online status heartbeat |
| `DELETE` | `/api/devices/{device_id}` | Remote logout/remove device |

### Player & Playback (`/api/player`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/player/current` | Get active playback state |
| `POST` | `/api/player/play` | Start track playback |
| `POST` | `/api/player/pause` | Pause track playback |
| `POST` | `/api/player/resume` | Resume playback |
| `POST` | `/api/player/seek` | Seek to position |
| `POST` | `/api/player/next` | Skip to next track in queue |
| `POST` | `/api/player/previous` | Return to start / previous track |
| `POST` | `/api/player/stop` | Stop playback |
| `POST` | `/api/player/sync` | Synchronize state across devices |
| `POST` | `/api/player/events` | Ingest playback telemetry events |

### Library & Favorites (`/api/library` & `/api/songs`)
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/songs/{song_id}/like` | Like a song |
| `DELETE` | `/api/songs/{song_id}/like` | Unlike a song |
| `GET` | `/api/library/liked` | Get liked songs collection |
| `POST` | `/api/albums/{album_id}/save` | Save album to library |
| `DELETE` | `/api/albums/{album_id}/save` | Unsave album |
| `GET` | `/api/library/albums` | Get saved albums |
| `POST` | `/api/artists/{artist_id}/follow` | Follow artist |
| `DELETE` | `/api/artists/{artist_id}/follow` | Unfollow artist |
| `GET` | `/api/library/artists` | Get followed artists |

### Playlists (`/api/playlists`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/playlists` | List user's playlists |
| `POST` | `/api/playlists` | Create new playlist |
| `GET` | `/api/playlists/{playlist_id}` | Get playlist details and tracklist |
| `PATCH` | `/api/playlists/{playlist_id}` | Update playlist title/description |
| `DELETE` | `/api/playlists/{playlist_id}` | Delete playlist |
| `POST` | `/api/playlists/{playlist_id}/songs` | Add song to playlist |
| `DELETE` | `/api/playlists/{playlist_id}/songs/{song_id}` | Remove song from playlist |
| `PATCH` | `/api/playlists/{playlist_id}/reorder` | Transactionally reorder playlist tracks |

### Recommendations (`/api/recommendations`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/recommendations/home` | Personalized home feed with dynamic mixes |
| `GET` | `/api/recommendations/similar-song/{song_id}` | Similar tracks based on artist & genre |
| `GET` | `/api/recommendations/similar-artist/{artist_id}` | Similar artists |
| `GET` | `/api/recommendations/mood/{mood}` | Mood-tailored playlist (Chill, Workout, etc.) |

### Search & History (`/api/search` & `/api/history`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/search` | Search songs, albums, and artists |
| `GET` | `/api/search/history` | Get recent search queries |
| `DELETE` | `/api/search/history` | Clear search query history |
| `GET` | `/api/history` | Paginated listening history |
| `GET` | `/api/history/recent` | Recent listens |
| `DELETE` | `/api/history` | Clear listening history |

### WebSockets (`/ws/player/{device_id}`)
Connect via `ws://localhost:8000/ws/player/{device_id}?token={firebase_id_token}` to receive real-time player updates and dispatch playback actions.

---

## 5. Local Setup & Development

### Prerequisites
- Python 3.10+
- (Optional) Docker & Docker Compose

### Step 1: Clone and Configure
```bash
git clone https://github.com/ZingyTomato/GaanaPy
cd GaanaPy

cp .env.example .env
```

### Step 2: Virtual Environment & Dependencies
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### Step 3: Run FastAPI Server
```bash
python -m uvicorn app.main:app --reload --port 8000
```
- Interactive Swagger API documentation: 👉 **`http://127.0.0.1:8000/docs`**
- Redoc API documentation: 👉 **`http://127.0.0.1:8000/redoc`**

---

## 6. Docker & Docker Compose Deployment

Run the complete multi-service stack (FastAPI Backend + PostgreSQL + Redis):

```bash
docker-compose up --build -d
```

Check status:
```bash
docker-compose ps
```

---

## 7. Running the Automated Test Suite

The test suite contains **73 comprehensive tests** covering authentication, device management, player transitions, listening thresholds, library management, playlist reordering, recommendation scoring, and WebSockets:

```bash
python -m pytest -v
```

---

## 8. Render Deployment Guide 🚀

### Method A: One-Click Blueprint (`render.yaml`)
1. Push this repository to GitHub or GitLab.
2. Log into [Render](https://dashboard.render.com).
3. Click **New +** ➔ **Blueprint**.
4. Connect this repository — Render will automatically detect [`render.yaml`](file:///c:/Users/Seban/Videos/GaanaPy/render.yaml) and configure the service.
5. In the Render Dashboard environment settings, supply:
   - `DATABASE_URL`: `postgresql+asyncpg://postgres:[PASSWORD]@[HOST]:5432/postgres` (from Supabase)
   - `FIREBASE_PRIVATE_KEY`: Your Firebase service account private key string

### Method B: Manual Web Service Setup on Render
1. Click **New +** ➔ **Web Service**.
2. Connect your GitHub repository.
3. Configure settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
4. Add Environment Variables:
   - `DATABASE_URL`: `postgresql+asyncpg://postgres:[PASSWORD]@[HOST]:5432/postgres`
   - `FIREBASE_PROJECT_ID`: `personal-songs`
   - `FIREBASE_CLIENT_EMAIL`: `firebase-adminsdk-fbsvc@personal-songs.iam.gserviceaccount.com`
   - `FIREBASE_PRIVATE_KEY`: `-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n`
   - `APP_ENV`: `production`
   - `DEBUG`: `False`
5. Click **Create Web Service** to trigger automatic deployment.

   - Build using the provided [Dockerfile](file:///c:/Users/Seban/Videos/GaanaPy/Dockerfile).
