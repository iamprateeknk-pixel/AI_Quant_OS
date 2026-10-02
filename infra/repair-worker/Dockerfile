# Trusted dependency installation only. No candidate, project or verifier source.
FROM docker.io/library/python@sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5 AS dependencies
COPY requirements.lock /requirements.lock
RUN --network=none --mount=type=bind,source=wheels,target=/wheels,readonly \
    /usr/local/bin/python -I -B -m pip --isolated --disable-pip-version-check install \
    --no-index --no-cache-dir --no-compile --no-deps --require-hashes \
    --only-binary=:all: --find-links=/wheels --target=/opt/repair/site-packages \
    -r /requirements.lock

FROM docker.io/library/python@sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5
COPY --from=dependencies /opt/repair/site-packages/ /usr/local/lib/python3.11/site-packages/
ENV PYTHONDONTWRITEBYTECODE=1
LABEL ai.quant.repair.state="IMAGE_BUILD_ONLY" \
      ai.quant.repair.execution-verified="false" \
      ai.quant.repair.promotion-allowed="false"
USER 65534:65534
WORKDIR /scratch
ENTRYPOINT ["/usr/bin/env"]
CMD ["/usr/local/bin/python", "-I", "-B"]
