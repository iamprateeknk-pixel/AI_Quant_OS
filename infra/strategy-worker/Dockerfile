# Trusted stdlib harness only; never copy a candidate, SDK or dataset into the image.
FROM docker.io/library/python@sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5
COPY --chmod=0444 worker.py /opt/strategy/worker.py
LABEL ai.quant.strategy.state="WORKER_SOURCE_PREPARED" \
      ai.quant.strategy.execution-verified="false" \
      ai.quant.strategy.promotion-allowed="false"
USER 65534:65534
WORKDIR /scratch
ENTRYPOINT ["/usr/bin/env"]
CMD ["-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/scratch/home", "TMPDIR=/scratch", "/usr/local/bin/python", "-I", "-B", "/opt/strategy/worker.py"]
