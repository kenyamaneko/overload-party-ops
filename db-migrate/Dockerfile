# Stage 1: Build psqldef from source with dropped-column fix
# Upstream bug: LEFT JOIN information_schema.columns produces NULL column_name
# for dropped columns (attisdropped=true) because they exist in pg_attribute
# but not in information_schema. psqldef scans into Go string (not NullString).
# Fix: filter out dropped columns + COALESCE as safety net.
FROM golang:alpine AS builder
RUN apk add --no-cache git
WORKDIR /build
RUN git clone --depth 1 --branch v3.10.1 https://github.com/sqldef/sqldef.git .
RUN sed -i 's/AND f\.attnum > 0/AND f.attnum > 0 AND NOT f.attisdropped/' \
      database/postgres/database.go \
    && sed -i 's/s\.column_name,/COALESCE(s.column_name, f.attname) AS column_name,/' \
      database/postgres/database.go
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /psqldef ./cmd/psqldef/

# Stage 2: Final image
FROM postgres:16-alpine
RUN apk add --no-cache bash
COPY --from=builder /psqldef /usr/local/bin/psqldef

COPY sql/ /app/sql/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
