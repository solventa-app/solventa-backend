FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt

COPY app ./app
RUN useradd --system --no-create-home solventa
USER solventa

# Cloud Run inyecta PORT; en local se usa el puerto por defecto del servicio.
# Sin registro de accesos: el ticket viaja en la URL (un solo uso, 60 s) y no debe quedar en los logs.
ENV PORT=8011
EXPOSE 8011
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
