# 🏛️ Institutional Memory

## 📊 Metadata
- **עדכון אחרון:** 2026-08-10
- **סה"כ תובנות:** 6

## התובנות

- `[2026-08-06] [auto-routing] [importance:5] [pattern]` - **תאימות לאחור ב-Python (API Break):** החלפת פרמטר מיקומי ראשון (Positional Argument) בבנאי של תשתית (כמו `RoutingAuditEngine`) שוברת קריאות קיימות. בעת שדרוג בנאים, חובה להשתמש ב-`*` כדי לאלץ שימוש ב-Keyword-only arguments עבור פרמטרים חדשים או לשמור על הסדר המקורי.
- `[2026-08-06] [auto-routing] [importance:5] [gotcha]` - **עקיפת מנגנון אבטחה (DEC-01):** יציאה מוקדמת מהקוד עבור כלים ללא דגלים (כמו `agy`) עקפה בטעות גם את אימות הזהות של הסוכן. בבדיקות ניתוב ואבטחה, יש להקפיד להפריד לחלוטין בין דילוג על בדיקת פרמטרים אופציונליים לבין בדיקת הזהות הקריטית שחייבת להתקיים תמיד.
- `[2026-08-06] [auto-routing] [importance:4] [gotcha]` - **סקירת קוד (Codex Review) מול Working Tree מלוכלך:** פקודת סקירת הקוד רצה גם על שינויים שלא במעקב (Untracked Files, כמו Golden Fixtures) או שרידים מקומיטים שבוטלו. חובה לוודא שסביבת העבודה מסודרת ונקייה לפני הרצת סקירה כדי למנוע חיובי-שווא (False positives).
- `[2026-08-08] [auto-routing] [importance:5] [gotcha]` - **macOS Sandbox & CLI Socket Binding (אימות אמפירי):** בסביבת IDE Sandbox ב-macOS, הרצת כלי CLI של מודלים (כמו `codex exec` או `claude -p`) מובילה לשגיאת הרשאות `bind: 127.0.0.1:0` (`Operation not permitted`). משתני סביבה כמו `TMPDIR=/tmp` או `GIT_OPTIONAL_LOCKS=0` אינם פותרים זאת. הפתרון הבלעדי והמחייב הינו הרצת ה-Tool עם `BypassSandbox: true` כפי שמוגדר בסעיף 4.7 ב-`protocol.md`.
- `[2026-08-08] [auto-routing] [importance:4] [pattern]` - **סנון חיובי-שווא בסקירת קוד (Code Review vs ADRs):** סוכני סקירת קוד שאינם מודעים להקשר השיחה עשויים לסמן מנגנוני תאימות לאחור (כמו Deprecated Facades) כריחות קוד. חובה להצליב ממצאי ביקורת מול ה-ADRs והחלטות ה-Grilling כדי לסנן אזהרות שווא.
- `[2026-08-08] [auto-routing] [importance:4] [pattern]` - **הפרדת ניתוח צעד בודד (`_analyze_step` & `StepAnalysis`):** ריכוז לוגיקת המדיניות בתוך פונקציה טהורה ומחזירת אובייקט `StepAnalysis` בלתי-משתנה מפשט את מנוע הביקורת ומאפשר בדיקות יחידה מבודדות ללא דליפת מצב.
