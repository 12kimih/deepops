# DeepOps-owned Slurm exporter image, built locally by the
# prometheus-slurm-exporter role (no registry pull, no external source).
#
# The exporter source lives in this build context (see ../exporter). The
# container bind-mounts the host's Slurm client binaries and libraries, so
# the runtime stage provides a current glibc plus the system libraries those
# binaries link that are not part of the Slurm install prefix.
# Both stages are pinned by digest, so a rebuild uses exactly these images.
# The hosts' Ubuntu release: the container runs the hosts' Slurm binaries, so it
# provides the libraries they were linked against on those hosts.
# upstream: registry 24\.04
ARG SLURM_EXPORTER_BASE_IMAGE=ubuntu:24.04@sha256:9c7f200c07e3d4a03e7249d2c16bf66a51120fb876aa2242cf31451412543289
ARG SLURM_EXPORTER_BUILD_IMAGE=golang:1.27@sha256:205c7636d74e0ffbe3d007162df7d74f859f5a8c8322dfb9f907977686c126cf

FROM ${SLURM_EXPORTER_BUILD_IMAGE} AS build
COPY exporter/ /src/
# Tested before it is built: an image never carries a parser its own tests reject.
RUN cd /src && go vet ./... && go test ./... \
    && CGO_ENABLED=0 go build -buildvcs=false -o /deepops-slurm-exporter .

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
