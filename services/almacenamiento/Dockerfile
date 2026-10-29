FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --system --no-create-home solventa
USER solventa

# Cloud Run inyecta PORT; en local se usa el puerto por defecto del servicio.
ENV PORT=8009
EXPOSE 8009
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
