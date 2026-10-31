FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt

COPY app ./app
RUN useradd --system --no-create-home solventa
USER solventa

# Cloud Run inyecta PORT; en local se usa el puerto por defecto del servicio.
ENV PORT=8102
EXPOSE 8102
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
