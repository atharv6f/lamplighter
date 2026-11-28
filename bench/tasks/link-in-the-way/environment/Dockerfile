FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3
WORKDIR /app
COPY files/ /app/
COPY --chmod=644 outside/report.txt /opt/reports/2026-03.txt
RUN /app/setup/make_link.sh
