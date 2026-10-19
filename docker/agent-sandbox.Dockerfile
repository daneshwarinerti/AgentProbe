FROM python:3.11-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin agentprobe

USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["python"]
