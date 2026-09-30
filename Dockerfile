# testNat — חיקוי של **הצד הנכנס** של whatsapp-cloudapi: /register, ה-JWT,
# ה-subject וה-inbound.py האמיתי. בלי ack, בלי דיווח ל-Manager ובלי Graph —
# ראה README. ולכן repo נפרד, לא tag על liorgr/whatsapp-cloudapi.
#
#   docker build -t liorgr/whatsapp-cloudapi-testnat:testNat testnat/
#
# ה-inbound.py מגיע מה-image של הטלפון עצמו, ולא מהקשר הבנייה. כך המיפוי
# שאתה רואה בדף הוא **בדיוק** הקוד שרץ בפרודקשן — אי אפשר שהם ייפרדו.

ARG CLOUDAPI_IMAGE=liorgr/whatsapp-cloudapi:latest
FROM ${CLOUDAPI_IMAGE} AS src

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LISTEN_PORT=8000 \
    MODE=observe

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# שער בנייה — אותה סיבה כמו ב-images האחרים: nkeys 0.2.0 עוברת import
# ונופלת רק בקריאה.
RUN python -c "\
import os, nkeys, nacl.signing, nats, fastapi; \
s = nkeys.encode_seed(os.urandom(32), nkeys.PREFIX_BYTE_USER); \
kp = nkeys.from_seed(s); kp.public_key; kp.wipe(); \
print('deps ok')"

COPY --from=src /app/inbound.py ./inbound.py
COPY app.py .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ['LISTEN_PORT']+'/health',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "python app.py"]
