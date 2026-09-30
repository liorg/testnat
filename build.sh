#!/usr/bin/env bash
# בנייה **מקומית** של testNat. לא נוגע ב-Docker Hub לכתיבה, אף פעם.
#
#   ./build.sh                          מה-image של הטלפון שכבר על המכונה
#   ./build.sh --from-container wa_xyz   מה-image שקונטיינר טלפון **רץ** עליו
#   ./build.sh --image liorgr/whatsapp-cloudapi:v3.0.1
#   ./build.sh --allow-pull              רק אם ה-image לא מקומי
#
# ═══════════════════════════════════════════════════════════════════════════
# מה "מקומי" אומר כאן
# ───────────────────────────────────────────────────────────────────────────
# ה-Dockerfile מושך את inbound.py מה-image של הטלפון ב-`COPY --from`. ה-image
# הזה כבר על המכונה — ה-CI/CD דחף אותו ל-Hub, וה-node משך אותו כדי להריץ
# טלפונים. אין שום סיבה לגשת לרשת שוב.
#
# שני דברים מבטיחים את זה:
#
#   1. אנחנו מעבירים ל-FROM את ה-**image ID** המקומי, לא tag. ל-ID אין
#      מקבילה בregistry, ולכן אין מה לפתור מול הרשת.
#   2. DOCKER_BUILDKIT=0 — ה-builder הקלאסי משתמש במה שמקומי. BuildKit נוטה
#      לפתור tag מול ה-registry גם כשה-image כאן, וזה בדיוק מה שלא רוצים.
#
# בלי `--allow-pull` הסקריפט **נכשל** אם ה-image לא מקומי, במקום למשוך בשקט.
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

IMAGE="liorgr/whatsapp-cloudapi:latest"
TARGET="liorgr/whatsapp-cloudapi-testnat:testNat"
FROM_CONTAINER=""
ALLOW_PULL=0
DOCKER="docker"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --image)          IMAGE="$2";          shift 2 ;;
    --from-container) FROM_CONTAINER="$2"; shift 2 ;;
    --tag)            TARGET="$2";         shift 2 ;;
    --allow-pull)     ALLOW_PULL=1;        shift   ;;
    -h|--help)        sed -n '2,8s/^# \?//p' "$0"; exit 0 ;;
    *) echo "לא מוכר: $1" >&2; exit 2 ;;
  esac
done

# sudo רק אם צריך
if ! $DOCKER info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  else
    echo "✗ אין גישה ל-docker. הרץ עם sudo, או הוסף את המשתמש לקבוצת docker." >&2
    exit 1
  fi
fi

# ── 1. איזה image של הטלפון ──────────────────────────────────────────────
# העדיפות היא ל---from-container: זה ה-image שטלפון **באמת** רץ עליו עכשיו.
# `latest` יכול להיות חדש ממנו, ואז testNat היה מציג מיפוי שהטלפון לא מריץ.
if [ -n "$FROM_CONTAINER" ]; then
  IMAGE="$($DOCKER inspect -f '{{.Config.Image}}' "$FROM_CONTAINER" 2>/dev/null || true)"
  if [ -z "$IMAGE" ]; then
    echo "✗ אין קונטיינר בשם $FROM_CONTAINER" >&2
    echo "  רשימה:  $DOCKER ps --format '{{.Names}}  {{.Image}}'" >&2
    exit 1
  fi
  echo "· הקונטיינר $FROM_CONTAINER רץ על $IMAGE"
fi

# ── 2. הוא מקומי? ────────────────────────────────────────────────────────
if ! $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
  if [ "$ALLOW_PULL" = "1" ]; then
    echo "· $IMAGE לא מקומי — מושך (--allow-pull)"
    $DOCKER pull "$IMAGE"
  else
    echo "✗ $IMAGE לא על המכונה הזו." >&2
    echo "  ה-image של הטלפון הוא המקור ל-inbound.py, ובלעדיו אין מה לבנות." >&2
    echo "  אפשרויות:" >&2
    echo "    ./build.sh --from-container <שם של טלפון שרץ>   ← המדויק" >&2
    echo "    ./build.sh --allow-pull                          ← למשוך מה-Hub" >&2
    echo "  מה כן מקומי:" >&2
    $DOCKER images --filter reference='*whatsapp-cloudapi*' \
      --format '    {{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedSince}}' >&2 || true
    exit 1
  fi
fi

# ה-ID המקומי. זה מה שנכנס ל-FROM, וזו הסיבה שאין גישה לרשת.
SRC_ID="$($DOCKER image inspect "$IMAGE" --format '{{.Id}}')"

# ── 3. ה-sha של inbound.py במקור ─────────────────────────────────────────
# נמדד לפני הבנייה, כדי שיהיה עם מה להשוות אחריה. אותו חישוב בדיוק כמו
# ב-/health של שני הרכיבים: sha256 של הקובץ, 12 תווים ראשונים.
SRC_SHA="$($DOCKER run --rm --entrypoint sh "$SRC_ID" -c \
  'sha256sum /app/inbound.py' 2>/dev/null | cut -c1-12 || true)"
[ -n "$SRC_SHA" ] && echo "· inbound.py במקור: $SRC_SHA"

# ── 4. בנייה — בלי רשת בכלל ──────────────────────────────────────────────
echo "· בונה $TARGET מתוך $IMAGE ($(echo "$SRC_ID" | cut -c8-19))"
DOCKER_BUILDKIT=0 $DOCKER build \
  --build-arg "CLOUDAPI_IMAGE=$SRC_ID" \
  -t "$TARGET" \
  "$HERE"

# ── 5. אימות ─────────────────────────────────────────────────────────────
OUT_SHA="$($DOCKER run --rm --entrypoint sh "$TARGET" -c \
  'sha256sum /app/inbound.py' 2>/dev/null | cut -c1-12 || true)"

echo
if [ -n "$SRC_SHA" ] && [ "$SRC_SHA" != "$OUT_SHA" ]; then
  echo "✗ ה-inbound.py לא זהה: מקור $SRC_SHA · מה שנבנה $OUT_SHA" >&2
  echo "  זה לא אמור לקרות — ה-COPY --from אמור להיות עותק בייט-בבייט." >&2
  exit 1
fi
echo "✓ $TARGET נבנה · inbound.py = ${OUT_SHA:-?}"
echo
echo "  ה-image **לא** נדחף לשום מקום, וגם לא יידחף. הוא קיים רק על המכונה הזו."
echo "  להריץ:"
echo
echo "    $DOCKER run -d --name testnat --network whqueue-net -p 9380:8000 \\"
echo "      -e WHQUEUE_BASE_URL=http://whqueue:8080 \\"
echo "      -e PHONE_ID=<GUID של טלפון הבדיקה> \\"
echo "      -e PHONE_NUMBER_ID=111111 \\"
echo "      -e WHQUEUE_MASTER_SECRET=\"\$(sudo cat /opt/whqueue/secrets/master_secret)\" \\"
echo "      $TARGET"
echo
echo "  ואז השווה את ה-sha מול הטלפון עצמו:"
echo "    curl -s http://127.0.0.1:9380/health | grep -o '\"inbound_sha\":\"[^\"]*\"'"
