FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# APP_MODULE picks the service from the same image: app.main:app (scorecard, default)
# or app.growth_main:app (standalone growth-backtest dashboard)
CMD ["sh", "-c", "uvicorn ${APP_MODULE:-app.main:app} --host 0.0.0.0 --port 8080"]
