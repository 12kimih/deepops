# DeepOps-owned Slurm exporter image, built locally by the
# prometheus-slurm-exporter role (no registry pull, no external source).
#
# The exporter source lives in this build context (see ../exporter). The
# container bind-mounts the host's Slurm client binaries and libraries, so
# the runtime stage provides a current glibc plus the system libraries those
# binaries link that are not part of the Slurm install prefix.
# Both stages are pinned by digest, so a rebuild uses exactly these images.
ARG SLURM_EXPORTER_BASE_IMAGE=ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8
ARG SLURM_EXPORTER_BUILD_IMAGE=golang:1.24@sha256:d2d2bc1c84f7e60d7d2438a3836ae7d0c847f4888464e7ec9ba3a1339a1ee804

FROM ${SLURM_EXPORTER_BUILD_IMAGE} AS build
COPY exporter/ /src/
RUN cd /src && CGO_ENABLED=0 go build -buildvcs=false -o /deepops-slurm-exporter .

FROM ${SLURM_EXPORTER_BASE_IMAGE}
# The slurm system user must resolve inside the container or the mounted
# Slurm client tools refuse to parse slurm.conf (SlurmUser lookup).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmunge2 \
        libjson-c5 \
        libyaml-0-2 \
        liblz4-1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --shell /usr/sbin/nologin slurm
COPY --from=build /deepops-slurm-exporter /usr/local/bin/deepops-slurm-exporter
ENTRYPOINT ["/usr/local/bin/deepops-slurm-exporter"]
