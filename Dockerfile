# syntax=docker/dockerfile:1
#
# Single-container deployment: Xvfb (virtual display) + FreeRDP (connects
# out to the Windows machine running 1C) + the Telegram bot / Gemini
# pipeline, all in one image. The RDP session is rendered onto the virtual
# display; the existing, unmodified pyautogui-based low-level input code in
# src/win_1c_bot.py drives it exactly as if it were a real local screen.
#
# Base is ubuntu:24.04, not python:3.13-slim: pyautogui's `mouseinfo`
# dependency hard-requires a real `tkinter` binding at import time, and
# apt's python3-tk only matches the *same* interpreter build it was
# compiled against. python:X-slim images bundle a from-source CPython with
# no matching apt package, which breaks the import outright (confirmed via
# on-machine testing while building this image); Ubuntu's own python3 +
# python3-tk are built together, so this actually imports.
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-tk build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-tk \
        xvfb x11-utils xauth scrot \
        freerdp3-x11 \
        x11vnc novnc websockify \
        fonts-dejavu-core \
        tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY src/ src/
COPY main.py .
COPY data/templates/ data/templates/
COPY docker/entrypoint.py /entrypoint.py


# Fixed UID/GID (not auto-assigned) so the host-side ./data bind mount can
# be chowned to match ahead of time — see README "First run" section.
#
# ubuntu:24.04 ships a default "ubuntu" user/group already sitting at
# uid/gid 1000 (added upstream for cloud-init). Without removing it first,
# `groupadd --gid 1000` collides with it and fails with exit code 4 ("GID
# not unique") - confirmed by hitting exactly that error building this
# image. `|| true` on the removal so this stays a no-op if a future base
# image drops that default user.
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && (groupdel ubuntu 2>/dev/null || true) \
    && groupadd --gid 1000 automation \
    && useradd --uid 1000 --gid automation --create-home --shell /usr/sbin/nologin automation \
    && mkdir -p /app/data \
    && chown -R automation:automation /app

USER automation

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import pathlib,sys,time; p=pathlib.Path('/app/data/.health'); sys.exit(0 if p.exists() and time.time()-float(p.read_text())<90 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/entrypoint.py"]
