FROM golang:1.22-bullseye AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 go build -o /out/yt-transfer ./cmd/yttransfer

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip ffmpeg ca-certificates nodejs curl unzip \
    && pip3 install --break-system-packages --no-cache-dir https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.tar.gz \
    && pip3 install --break-system-packages --no-cache-dir biliup \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -q \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /out/yt-transfer /app/yt-transfer
ENTRYPOINT ["/app/yt-transfer"]
