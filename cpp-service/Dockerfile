FROM gcc:13-bookworm AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake pkg-config librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src ./src

RUN cmake -S src -B build -DCMAKE_BUILD_TYPE=Release -DREQUIRE_KAFKA=ON \
    && cmake --build build --config Release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends librdkafka1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=build /app/build/aegis-cpp-service .

EXPOSE 8082

ENTRYPOINT ["./aegis-cpp-service"]
