FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

WORKDIR /app
COPY benchmarks /app/benchmarks
COPY cluster_evaluation/__init__.py cluster_evaluation/pod_runner.py /app/cluster_evaluation/

RUN useradd --create-home --uid 10001 benchmark
USER 10001:10001

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "cluster_evaluation.pod_runner"]
