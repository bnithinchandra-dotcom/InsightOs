FROM golang:1.24.8-alpine AS builder

RUN go install github.com/minio/minio@v0.0.0-20260212201848-7aac2a2c5b7c

FROM alpine:3.22

RUN apk add --no-cache ca-certificates curl

COPY --from=builder /go/bin/minio /usr/local/bin/minio

ENTRYPOINT ["minio"]
