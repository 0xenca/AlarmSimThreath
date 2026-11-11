FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATABASE_URL=sqlite:////app/data/alarmhub.db
EXPOSE 8080 7000/tcp 7000/udp
VOLUME ["/app/data"]
CMD ["python", "-m", "alarmhub", "run"]
