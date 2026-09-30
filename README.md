# testNat

רואים מה באמת עובר ב-NATS, ומה ה-Manager **היה** מקבל — בלי להפריע לאף טלפון.

המיפוי בדף הוא לא חיקוי: ה-`inbound.py` מגיע ב-build מה-image של הטלפון
עצמו, ולכן אי אפשר שהם ייפרדו.

---

## מה הוא מדמה מ-whatsapp-cloudapi, ומה לא

`liorgr/whatsapp-cloudapi-testnat:testNat` — השם אומר את זה: זה חיקוי של
קונטיינר הטלפון, לא רכיב של whqueue. אבל רק **חצי** ממנו, וההפרדה הזו היא
כל הרעיון:

| | whatsapp-cloudapi | testNat |
|---|---|---|
| `/register` מול whqueue | כן | **כן — זהה** |
| חיבור ל-NATS עם ה-JWT | כן | **כן — זהה** |
| מיפוי ב-`inbound.py` | כן | **כן — אותו קובץ מה-image** |
| קריאה מה-stream | pull + durable + ack | **מנוי core — עותק** |
| דיווח ל-Manager | POST ל-webhook | לא. מציג בדף |
| שליחה ל-Graph | כן | לא |
| רישום webhook / override | כן | לא |
| כתיבה ל-`cloudapi_registry` | כן | לא |

### ה-`inbound.py` הוא אותו קובץ, ואפשר להוכיח

```dockerfile
FROM ${CLOUDAPI_IMAGE} AS src
...
COPY --from=src /app/inbound.py ./inbound.py
```

אין עותק ואין fork — הקובץ נמשך מה-image של הטלפון ב-build. וכדי שלא תצטרך
להאמין ל-Dockerfile, שני ה-`/health` מציגים את ה-sha שלו:

```bash
curl -s http://<טלפון>:8000/health  | jq -r .inbound_sha
curl -s http://<testnat>:9380/health | jq -r .inbound_sha
```

הפרש = testNat נבנה מול tag אחר, ומה שהוא מציג אינו מה שהטלפון עושה. אז
`docker build` מחדש. ה-sha מופיע גם בשורת הצ'יפים בדף.

הצד הנכנס — `/register`, ה-JWT, ה-subject, המיפוי — הוא הקוד האמיתי. הצד
היוצא נחתך בכוונה: קונטיינר שני שעושה ack היה **גונב** הודעות מהטלפון, וכזה
שמדווח ל-Manager היה יוצר הודעות אמיתיות ב-`messages` ומפעיל תרחישים.

לכן זה לא תחליף לטלפון ולא אמור להיפרס כאחד. הוא בכוונה ב-repo נפרד ולא
tag על `liorgr/whatsapp-cloudapi` — שם היה אפשר להצמיד אותו בטעות לטלפון
דרך `providers.tag`, ואז מספר אמיתי היה עולה בלי דיווח ל-Manager בכלל.

---

## המספר המתואם

זה המספר שאתה מקליד **בשני מקומות**, ולא במקום שלישי:

| איפה | מה |
|---|---|
| ה-env של testNat | `PHONE_NUMBER_ID=111111` |
| סביבת ה-Postman | `testnat_pnid = 111111` |

`testnat_pnid` ולא `phone_number_id`, כי `phone_number_id` הוא הטלפון
האמיתי שתיקיות 1–7 עובדות מולו. ההפרדה היא מה שמאפשר להריץ את כל האוסף
ברצף — פרודקשן בתיקיות 1–7, טלפון הבדיקה בתיקייה 8. אם `testnat_pnid` ריק
הוא נופל חזרה ל-`phone_number_id`, לנוחות כשרק תיקייה 8 מעניינת.

הוא מה שמטא שמה ב-`metadata.phone_number_id`, והוא מה שקובע את ה-subject:
`wa.in.111111`. שאר המזהים הם צנרת.

שני הקצוות נבדקים, כי טעות הקלדה כאן לא נראית כמו טעות הקלדה — היא נראית
כמו דף ריק, ואת זה מחפשים שעה במקום הלא נכון:

| | |
|---|---|
| ב-`/register` | testNat שולח את המספר. whqueue לא סומך עליו — הוא לוקח את ה-pnid מה-DB ומשווה. אי-התאמה → **409**, ו-testNat לא עולה |
| אחרי `/register` | testNat משווה את ה-subject שחזר מול ה-env. אי-התאמה → **לא מתחבר בכלל**, והדף אומר במה ההבדל |
| לפני ההזרקה | בקשה 8.1 בפוסטמן משווה את `phone_number_id` שלה מול `/health` של testNat |

---

## שני מצבים, וההבדל ביניהם קריטי

| | `MODE=observe` (ברירת מחדל) | `MODE=consume` |
|---|---|---|
| איך | מנוי **core** ל-subject | pull subscribe מ-JetStream |
| מה קורה לטלפון האמיתי | מקבל הכול כרגיל | **מפסיד את ההודעה** |
| נוגע ב-consumer/durable/ack | לא | כן |
| היסטוריה | רק מה שמתפרסם בזמן שהוא מחובר | כן, מה שמחכה בתור |
| בטוח מול פרודקשן | **כן** | רק על טלפון בדיקה ייעודי |

זו הסיבה ש-`observe` הוא ברירת המחדל. ה-stream הוא work-queue: הודעה נמסרת
פעם אחת, ולכן צרכן שני על אותו מספר הוא **גנב**, לא צופה. מנוי core מקבל
עותק של הפרסום ולא נוגע במצב בכלל.

עם השורה מ-`sql/2026-09-30_testnat_phone.sql` אין ממי לגנוב — אף קונטיינר
לא מאזין ל-`wa.in.111111` — ולכן שם גם `consume` בטוח.

---

## הקמה

### 1. שורת הטלפון

```sql
-- sql/2026-09-30_testnat_phone.sql — מלא <user_id> ו-<waba_id> והרץ
```

### 2. בנייה

**מקומית, ובלי Docker Hub.** ה-image הזה לא נדחף לשום מקום — הוא קיים רק על
ה-node שבנה אותו, ולכן גם `deploy.sh`, ה-CI/CD ו-`providers.tag` לא רואים
אותו בכלל.

```bash
./testnat/build.sh                                 # ה-image שכבר על המכונה
./testnat/build.sh --from-container wa_a1b2c3       # ה-image שטלפון **רץ** עליו
./testnat/build.sh --image liorgr/whatsapp-cloudapi:v3.0.1
./testnat/build.sh --allow-pull                     # רק אם הוא לא מקומי
```

`--from-container` הוא הצורה הנכונה כשיש טלפון חי. `latest` יכול להיות חדש
ממה שהטלפון בפועל מריץ, ואז testNat היה מציג מיפוי שהטלפון לא מריץ — וזה
בדיוק סוג הטעות שכלי בדיקה לא אמור לייצר.

מה שהסקריפט עושה כדי שלא תהיה גישה לרשת:

| | |
|---|---|
| מעביר ל-`FROM` את ה-**image ID** המקומי, לא tag | ל-ID אין מקבילה ב-registry, אז אין מה לפתור |
| `DOCKER_BUILDKIT=0` | ה-builder הקלאסי לוקח מה שמקומי. BuildKit נוטה לפתור tag מול ה-registry גם כשה-image כאן |
| בלי `--allow-pull` — **נכשל** אם ה-image לא מקומי | ומדפיס מה כן קיים, במקום למשוך בשקט |

אחרי הבנייה הוא משווה את ה-sha של `inbound.py` בין ה-image של הטלפון לזה
שנבנה. הפרש = תקלה, והסקריפט נכשל.

ידנית, אם אתה מעדיף:

```bash
ID=$(docker image inspect liorgr/whatsapp-cloudapi:latest --format '{{.Id}}')
DOCKER_BUILDKIT=0 docker build --build-arg "CLOUDAPI_IMAGE=$ID" \
  -t liorgr/whatsapp-cloudapi-testnat:testNat testnat/
```

הבנייה **חייבת** לרוץ על מכונה שיש עליה את ה-image של הטלפון — הוא המקור
ל-`inbound.py`. על ה-nodes שלך הוא שם ממילא, כי שם רצים הטלפונים.

### 3. הרצה — על ה-node שהוא worker

```bash
docker run -d --name testnat --network whqueue-net -p 9380:8000 \
  -e WHQUEUE_BASE_URL=http://whqueue:8080 \
  -e PHONE_ID=aaaaaaaa-0000-0000-0000-00000000000e \
  -e PHONE_NUMBER_ID=111111 \
  -e WHQUEUE_MASTER_SECRET="$(sudo cat /opt/whqueue/secrets/master_secret)" \
  -e MODE=observe \
  liorgr/whatsapp-cloudapi-testnat:testNat
```

`--network whqueue-net` הוא לא קישוט: בלעדיו אין `whqueue` ואין `nats-1`, והכול
ייפול על "whqueue לא נגיש". זו בדיוק הסיבה שהרשת היא `attachable` — קונטיינר
שנוצר ב-`docker run` יכול להצטרף אליה כמו קונטיינרי הטלפונים.

הקונטיינר **לא** חלק מה-stack: אין לו service, הוא לא נפרס עם `stack deploy`,
ו-`docker rm -f testnat` מסיים אותו בלי להשאיר עקבות. `deploy.sh` לא נוגע בו.

| | |
|---|---|
| הדף | `http://<ip של ה-node>:9380` |
| JSON | `/events` · `/health` |
| לוגים | `docker logs -f testnat` — שורה לכל אירוע ממופה |

במקום `-p` אפשר גם בלי פרסום בכלל, ואז `docker exec testnat python -c ...`
או `docker logs`. אם ה-node חשוף לאינטרנט — **בלי `-p`**. אין כאן אימות.

---

## מול Postman

תיקייה **8 · testNat — צפייה ב-NATS** באוסף. ארבע בקשות:

| | |
|---|---|
| 8.1 | snapshot + **אימות שהמספר מתואם** בין הפוסטמן ל-testNat |
| 8.2 | הזרקה חתומה ל-`{{base_url}}/wa`, בדיוק כמו מטא |
| 8.3 | ש-`seen` זז |
| 8.4 | מה ה-Manager היה מקבל — ושזו ההודעה שהרגע הוזרקה ולא אירוע ישן |

בסביבה צריך `testnat_url` (`http://<ip>:9380`) ו-`testnat_pnid` **זהה**
ל-`PHONE_NUMBER_ID` של הקונטיינר.

האוסף כולו עובר עם שניהם למעלה בו-זמנית — 100 assertions, 0 נכשלו, פעמיים
ברצף: תיקיות 1–7 מול הטלפון האמיתי, תיקייה 8 מול 111111.

---

## כשלא רואים כלום

בסדר הזה, כי זה סדר השכיחות:

| | |
|---|---|
| 8.1 נכשל | המספר לא מתואם. `curl <testnat>/health` ותשווה ל-`phone_number_id` בסביבה |
| `error` בדף | `/register` נכשל — הטקסט אומר מה: 401 master secret · 404 אין שורה · 409 המספר לא של הטלפון הזה |
| `connected: false` | לא על `whqueue-net`, או ש-`nats-1` למטה |
| הכול ירוק ו-`seen: 0` | `MODE=consume` וטלפון אמיתי חטף את ההודעה. עבור ל-`observe` |
| ההודעה הגיעה אבל `mapped` מכיל `error` | ה-`inbound.py` נפל על המבנה הזה — זה באג אמיתי, והוא היה נופל גם בפרודקשן |

---

## מה זה **לא**

לא תחליף ל-`smoke_v3.py`. הוא מאמת את הפריסה מקצה לקצה — סודות, הרשאות,
שהקונטיינר האמיתי צרך ודיווח ל-Manager. testNat מראה לך **מה עובר**, וזה
דבר אחר: כשהזרימה עובדת אבל התוכן לא כמו שציפית, זה הכלי.
