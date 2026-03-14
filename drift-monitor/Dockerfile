FROM hashicorp/terraform:1.9 AS terraform

FROM google/cloud-sdk:slim

COPY --from=terraform /bin/terraform /usr/local/bin/terraform

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY check.py ./

ENTRYPOINT ["python3", "check.py"]
