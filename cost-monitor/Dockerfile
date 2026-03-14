FROM google/cloud-sdk:slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      kubectl \
      google-cloud-cli-gke-gcloud-auth-plugin \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY check.py ./

ENTRYPOINT ["python3", "check.py"]
