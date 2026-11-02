# =============================================================================
# MinIO (the MLflow artifact store), built here from MinIO's own release binary.
#
# MinIO stopped publishing its free community images on 23 October 2025 and removed
# the existing ones from Docker Hub and Quay (docs/SOURCES.md, 115): the digest this
# project used to pull could no longer be pulled on a new machine (VPS test, 1 November
# 2026). The release binary is still published (SOURCES 116).
#
# The Ansible prerequisites role prepares the build directory (outside the repository):
# it downloads minio.linux-amd64.<release> from the GitHub release with get_url, which
# refuses a file whose SHA-256 is not ansible/group_vars/all.yml's minio_sha256; it adds
# this file, minio/passwd, minio/group and the empty directories below; then it builds
# devsecmlops-minio:<release> and checks that the image reports that release.
#
# FROM scratch: the binary is statically linked, so the image holds nothing else: no
# shell, no package manager, no base image to pin or patch. It runs as the unprivileged
# uid 10002 (minio_uid in all.yml; the old image ran as root, so the role gives the
# existing data volume to that uid once).
# =============================================================================
FROM scratch

ARG MINIO_RELEASE
ARG MINIO_SHA256
LABEL org.opencontainers.image.title="minio" \
      org.opencontainers.image.version="${MINIO_RELEASE}" \
      org.opencontainers.image.source="https://github.com/minio/minio/releases/tag/${MINIO_RELEASE}" \
      devsecmlops.minio.binary-sha256="${MINIO_SHA256}"

# /etc/passwd and /etc/group name the user; rootfs/tmp is created mode 1777 by the role
COPY rootfs/ /
COPY --chown=10002:10002 data/ /data/
COPY --chown=10002:10002 home/ /home/minio/
COPY minio /usr/bin/minio

USER 10002:10002
ENV HOME=/home/minio
EXPOSE 9000 9001
ENTRYPOINT ["/usr/bin/minio"]
