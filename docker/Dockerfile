FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/docker/linux_compat:/app \
    DISPLAY=:99

ARG OK_SCRIPT_VERSION=2.0.8
ARG OK_WW_BUILD_VERSION=dev
ARG OK_WW_BUILD_REVISION=unknown

ENV OK_WW_BUILD_VERSION=${OK_WW_BUILD_VERSION} \
    OK_WW_BUILD_REVISION=${OK_WW_BUILD_REVISION}

LABEL org.opencontainers.image.version=${OK_WW_BUILD_VERSION} \
      org.opencontainers.image.revision=${OK_WW_BUILD_REVISION}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        gnupg \
        openbox \
        novnc \
        tini \
        tzdata \
        websockify \
        x11-utils \
        x11vnc \
        xvfb \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/requirements-cloud.txt /tmp/requirements-cloud.txt
COPY docker/patch_ok_script_wheel.py /tmp/patch_ok_script_wheel.py
RUN python -m pip download \
        --disable-pip-version-check \
        --no-deps \
        --only-binary=:all: \
        --dest /tmp/ok-script \
        "ok-script==${OK_SCRIPT_VERSION}" \
    && python /tmp/patch_ok_script_wheel.py \
        "/tmp/ok-script/ok_script-${OK_SCRIPT_VERSION}-py3-none-any.whl" \
        /tmp/ok-script-patched \
    && python -m pip install --no-cache-dir \
        "/tmp/ok-script-patched/ok_script-${OK_SCRIPT_VERSION}-py3-none-any.whl" \
        -r /tmp/requirements-cloud.txt \
    && python -m pip check \
    && rm -rf /tmp/ok-script /tmp/ok-script-patched

COPY . /app
RUN python /app/docker/smoke_test.py \
    && sed -i 's/\r$//' /app/docker/*.sh \
    && chmod +x /app/docker/entrypoint.sh \
    && useradd --create-home --uid 10001 cloud \
    && mkdir -p /data/cloud /run/ok-ww-update /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix \
    && chown -R cloud:cloud /data /app /run/ok-ww-update

USER cloud

EXPOSE 8765 15980
VOLUME ["/data/cloud"]

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
