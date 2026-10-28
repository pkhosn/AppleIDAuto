FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home appuser && mkdir -p /data/artifacts && chown -R appuser:appuser /srv/app /data
USER appuser
CMD ["sh", "-c", "case \"$ACCOUNT_DRIVER_TOKEN\" in ''|change-me|123456) exit 78;; esac; [ ${#ACCOUNT_DRIVER_TOKEN} -ge 32 ] || exit 78; exec uvicorn app.main:app --host 0.0.0.0 --port 3940"]
