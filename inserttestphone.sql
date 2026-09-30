-- 2026-09-30 — טלפון הבדיקה של testNat
--
-- שורה ב-phones שקיימת **רק** כדי ש-testNat יוכל לבקש credentials ב-
-- `/register`. אין לה קונטיינר, אין לה טוקן של מטא, ואין מספר אמיתי מאחוריה.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- למה בכלל צריך שורה
-- ───────────────────────────────────────────────────────────────────────────
-- `/register` מנפיק JWT שמאזין ל-subject אחד, והוא לוקח את ה-phone_number_id
-- **מה-DB** ולא מהבקשה — זו כל שכבת ההרשאות. בלי שורה אין מאיפה לגזור את
-- ה-subject, ולכן אין JWT.
--
-- שים לב שה-webhook עצמו **לא** צריך את השורה: whqueue מנתב לפי מה שבגוף
-- ה-JSON. לכן הזרקה ל-111111 מתפרסמת ל-wa.in.111111 בכל מקרה — רק המאזין
-- צריך רשות.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- למה מספר מומצא ולא מספר אמיתי
-- ───────────────────────────────────────────────────────────────────────────
-- ה-stream הוא work-queue: הודעה נמסרת **פעם אחת**. אם testNat היה רץ ב-
-- MODE=consume על מספר אמיתי, הוא היה גונב הודעות מהטלפון. עם מספר ייעודי
-- אין ממי לגנוב — אף קונטיינר לא מאזין שם — ולכן גם MODE=consume בטוח.
--
-- ב-MODE=observe (ברירת המחדל) זה לא משנה בכלל, כי מנוי core מקבל עותק.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── לפני הכול: מה חובה בטבלה אצלך ──────────────────────────────────────────
-- אם ל-phones יש עמודות NOT NULL שלא מופיעות כאן, ה-INSERT ייפול. תריץ קודם:
--
--   select column_name, data_type, column_default
--     from information_schema.columns
--    where table_schema = 'public' and table_name = 'phones'
--      and is_nullable = 'NO' and column_default is null
--    order by ordinal_position;
--
-- ותוסיף כל אחת מהן ל-INSERT.

-- ── הערכים ────────────────────────────────────────────────────────────────
-- שנה רק את שני אלה. ה-phone_number_id הוא **המספר המתואם** — אותו ערך
-- בדיוק ב-PHONE_NUMBER_ID של testNat וב-phone_number_id של סביבת ה-Postman.
--
--   phone_number_id  111111
--   user_id          המשתמש שלך — חייב להיות אמיתי אם יש FK

insert into public.phones (
  id, user_id, number, label, color,
  status, docker_status, created_at,
  provider, waba_id, phone_number_id
)
values (
  'aaaaaaaa-0000-0000-0000-00000000000e',      -- PHONE_ID של testNat
  'bcc282f3-bfa3-4afa-b506-79109a128ba8',                                  -- ← המשתמש שלך
  '+000000111111',                              -- מספר מומצא, לא לשליחה
  'testNat',
  '#6b7280',
  'active',
  'disabled',                                   -- אין קונטיינר, ולא צריך שיהיה
  now(),
  'cloudapi',
  '<waba_id>',                                  -- ה-WABA שלך, לנוחות בלבד
  '111111'                                      -- ← המספר המתואם
)
on conflict (id) do update
   set phone_number_id = excluded.phone_number_id,
       waba_id         = excluded.waba_id,
       provider        = excluded.provider;

-- ── אימות ────────────────────────────────────────────────────────────────
-- שלושת אלה חייבים לצאת: provider=cloudapi, phone_number_id=111111,
-- ואין עוד טלפון על אותו מספר (יש unique index, אז זה גם ייאכף).
select id, provider, phone_number_id, waba_id, status, docker_status
  from public.phones
 where phone_number_id = '111111';

-- ── להסרה ────────────────────────────────────────────────────────────────
-- delete from public.phones where id = 'aaaaaaaa-0000-0000-0000-00000000000e';
