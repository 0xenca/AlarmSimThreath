FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATABASE_URL=sqlite:////app/data/alarmsim.db
EXPOSE 8080 7100/tcp 7100/udp
VOLUME ["/app/data"]
CMD ["python", "-m", "alarmsim", "run"]
