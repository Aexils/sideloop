# ── Build zsign ───────────────────────────────────────────────────────────────
FROM debian:trixie-slim AS zsign
RUN apt-get update && apt-get install -y --no-install-recommends \
        git g++ make pkg-config libssl-dev libz-dev ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/zhlynn/zsign.git /zsign
WORKDIR /zsign/build/linux
RUN make && cp ../../bin/zsign /usr/local/bin/zsign

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim-trixie
# libssl : zsign runtime. openssl : cert/CSR/p12/extraction du profil.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl3 openssl git && rm -rf /var/lib/apt/lists/*

COPY --from=zsign /usr/local/bin/zsign /usr/local/bin/zsign

WORKDIR /app

# grandslam avec notre gsa.py vendorisé (sms_second_factor + authenticate→spd).
RUN git clone --depth 1 https://github.com/JJTech0130/grandslam.git /opt/grandslam
COPY tools/apple_auth/gsa.py /opt/grandslam/src/grandslam/gsa.py
RUN pip install --no-cache-dir /opt/grandslam requests

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# CronJob : `sideloop-refresh`. API/upload : `sideloop-api` (port 8000).
EXPOSE 8000
CMD ["sideloop-api"]
