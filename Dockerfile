# syntax=docker/dockerfile:1

# Base image for all build stages, containing necessary system dependencies and tools
FROM rustlang/rust:nightly-slim as base
RUN apt-get update && apt-get install -y libudev-dev pkg-config && rm -rf /var/lib/apt/lists/*
RUN cargo install cargo-chef

# Stage 1: Planner - Create a recipe for dependencies
FROM base as planner
WORKDIR /workspace
COPY . .
# Compute a recipe of dependencies
RUN cargo chef prepare --recipe-path recipe.json

# Stage 2: Cook - Build and cache dependencies
FROM base as cook
WORKDIR /workspace
# Copy the recipe from the planner stage
COPY --from=planner /workspace/recipe.json recipe.json
# Build our dependencies against the recipe
RUN cargo chef cook --release --recipe-path recipe.json

# Stage 3: Builder - Build the application
FROM base as builder
WORKDIR /workspace
COPY . .
# Copy over the cached dependencies
COPY --from=cook /workspace/target target
# Build the application
RUN cargo build --release

# Stage 4: Final Image
FROM debian:bookworm-slim
WORKDIR /app
# Install only necessary runtime dependencies, with the latest security patches
# of the base image (keeps the Trivy scan in CI clean of already-fixed CVEs)
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libudev-dev postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*
# Copy build artifacts from the builder stage
COPY --from=builder /workspace/target/release/server /app/server
COPY ./frontend /app/frontend
COPY ./migrations /app/migrations
COPY ./scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/app/server"]
