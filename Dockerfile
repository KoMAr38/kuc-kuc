FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
# Exactly the library versions the tests ran with (constraints.txt), never whatever is newest today.
RUN pip install -c constraints.txt .

# No default CMD: each Cloud Run service and job passes its own command at deploy.
