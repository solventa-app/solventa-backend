FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --system --no-create-home solventa
USER solventa

# Cloud Run inyecta PORT; en local se usa el puerto por defecto del servicio.
ENV PORT=8007
EXPOSE 8007
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
