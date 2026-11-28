FROM python:3.13-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3
WORKDIR /app
# The folder this makes gets the file's mode too, with no search bit, so
# only root can reach the file.
COPY --chmod=644 files/report.txt /opt/reports/report.txt
