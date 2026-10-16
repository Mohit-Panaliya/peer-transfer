# Multi-stage build. Native compile on the host arch (VPS is linux/aarch64,
# rust:bookworm images are multi-arch, so this Just Works on ARM64).
FROM rust:1-bookworm AS build
WORKDIR /app
COPY Cargo.toml ./
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl  && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/peer-relay /usr/local/bin/peer-relay
ENV RELAY_PORT=8787 RELAY_DB=/data/relay.db RELAY_DEFAULT_TTL=3600 RELAY_MAX_TTL=86400
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD curl -sf http://127.0.0.1:8787/health || exit 1
CMD ["peer-relay"]
