# Deployment — HKU Teacher-student Co-learning (SOLO) Bot

This guide covers running the bot on a shared machine with Docker so that
teachers and students can use it from their own browsers.

## 1. Prerequisites

- Docker Engine + Docker Compose plugin (`docker compose version` should work).
- A Qwen / DashScope API key (used for answering and quiz generation).

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and set at least:

- `DASHSCOPE_API_KEY` — your LLM API key.
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — the first teacher account (created on
  first startup). Change the password from the default.
- `STUDENT_REGISTER_CODE` — optional; the code students use to self-register.
  Leave blank to auto-generate one (view/rotate it later in the Teacher Console).
- `RAG_REQUIRE_AUTH=1` — keep enabled so every request requires login.

## 3. Build & run

```bash
docker compose up -d --build
```

The first build downloads Python + ML dependencies and can take several minutes.
Check health:

```bash
curl http://localhost:8000/health
docker compose logs -f solo-bot
```

## 4. Use

Open `http://<server-ip>:8000/` in a browser:

- **Landing page** → choose Teacher or Student.
- **Teacher** signs in with the admin account, then:
  - manages the shared **Course Knowledge Base** (upload materials/questions),
  - creates student accounts or shares the **registration code**,
  - **exports** student questions and/or quiz scores as Excel or CSV
    (quiz checked by default; full chat / answer transcripts are not exported).
- **Students** register with the course code (or an account created by the
  teacher) and can ask questions, upload temporary session files, and generate
  quizzes. Sessions are **owned per user** (another student cannot open yours).
  The knowledge base is **read-only** for students.

## 5. Data & persistence

Everything is stored under host-mounted volumes (see `docker-compose.yml`):

| Path            | Contents                                              |
| --------------- | ----------------------------------------------------- |
| `./.data`       | users, login tokens, sessions, KB metadata, quizzes, Chroma vectors |
| `./.uploads`    | uploaded course materials and session attachments     |
| `hf-cache`      | embedding model cache (named Docker volume)           |

Back up `./.data` and `./.uploads` to preserve all state.

## 6. Updating

```bash
git pull
docker compose up -d --build
```

Persisted data in the volumes is retained across rebuilds.

## 7. Notes & operations

- **Single worker only.** Chroma/SQLite state is process-local; do not scale to
  multiple workers/replicas.
- **HTTPS.** For internet exposure, put a reverse proxy (Caddy/Nginx) in front
  for TLS. On a LAN, `http://<ip>:8000` is sufficient.
- **Secret rotation.** The previously hardcoded API key has been removed from
  the code. If that key was ever committed, rotate it in the DashScope console.
- **GPU (optional).** The image installs CPU PyTorch (embeddings run fine on
  CPU). To use a GPU, switch to a CUDA base image, install the matching Torch
  build, and run with the NVIDIA container runtime.
