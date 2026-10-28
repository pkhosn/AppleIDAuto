FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN useradd --create-home appuser && mkdir -p /data/recovery-escrow && \
    chown -R appuser:appuser /srv/app /data && chmod 700 /data/recovery-escrow
USER appuser
CMD ["sh", "-c", "case \"$AUTOMATION_TOKEN\" in ''|change-me|123456) exit 78;; esac; [ ${#AUTOMATION_TOKEN} -ge 32 ] && [ ${#PANEL_API_KEY} -ge 32 ] && [ ${#ACCOUNT_DRIVER_TOKEN} -ge 32 ] || exit 78; exec uvicorn app.main:app --host 0.0.0.0 --port 3939"]
