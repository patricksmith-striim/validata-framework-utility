# syntax=docker/dockerfile:1.4
# Validata, built the way this framework builds Striim (../striim/images/striim/Dockerfile): ubuntu:22.04, JDK 17 and
# `dpkg -i` of the release's .deb packages, bind-mounted from deps/ (fetched by ../../download-dependencies.sh) so
# their 0.9 GB never lands in an image layer. amd64 only: Validata publishes no arm64 package, so compose sets the
# platform (emulated on Apple Silicon). The license is not baked in: files/entrypoint.sh writes it into
# conf/startUp.properties from the container's environment.
FROM amd64/ubuntu:22.04
ARG VALIDATA_VERSION
LABEL labs.validata.packaging="deb" labs.validata.version="${VALIDATA_VERSION}"
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive

# openjdk-17: the packages' install scripts look for /usr/lib/jvm/*java-17*-o* (Validata needs 17 or later).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless ca-certificates curl netcat-openbsd procps \
    && rm -rf /var/lib/apt/lists/*

# validata-dbms (the Derby historian, /var/validata) first, then validata-node (/opt/validata). Their scripts create
# the validata user and group and own both folders.
RUN --mount=type=bind,source=deps,target=/deps \
    missing=""; \
    for f in validata-dbms-${VALIDATA_VERSION}-Linux.deb validata-node-${VALIDATA_VERSION}-Linux.deb; do \
      [ -s "/deps/$f" ] || missing="${missing} $f"; \
    done; \
    if [ -n "$missing" ]; then \
      echo "ERROR: missing build dependencies under scripts/live/services/validata/images/validata/deps/:${missing}" >&2; \
      echo "Run  scripts/live/services/validata/download-dependencies.sh  (one-time, ~0.9 GB) to fetch them." >&2; \
      exit 1; \
    fi; \
    dpkg -i /deps/validata-dbms-${VALIDATA_VERSION}-Linux.deb && \
    dpkg -i /deps/validata-node-${VALIDATA_VERSION}-Linux.deb

ENV VALIDATA_HOME=/opt/validata
COPY files/entrypoint.sh /usr/local/bin/validata-entrypoint.sh
RUN chmod +x /usr/local/bin/validata-entrypoint.sh

USER validata
WORKDIR /opt/validata
EXPOSE 1527 9080 9081
ENTRYPOINT ["/usr/local/bin/validata-entrypoint.sh"]
