FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached on subsequent builds
# unless requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# The data directory is where the CSV output lands. We create it here so
# the directory exists even if no volume is mounted, and declare it as a
# volume so Docker knows it's meant to be persisted across runs.
RUN mkdir -p data
VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "app.main"]
