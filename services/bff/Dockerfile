FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN useradd --system --no-create-home solventa
USER solventa

# Cloud Run inyecta PORT; en local se usa el puerto por defecto del servicio.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
