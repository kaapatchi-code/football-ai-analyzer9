# تحديث FOOTBALL AI ANALYZER إلى Highlightly

## 1) إعداد Render
1. افتح خدمة `football-ai-analyzer` في Render.
2. افتح **Environment**.
3. أضف متغيراً باسم `HIGHLIGHTLY_API_KEY` وضع فيه مفتاح Highlightly (لا ترسله لأحد).
4. أضف `HIGHLIGHTLY_BASE_URL` بالقيمة `https://soccer.highlightly.net`.
5. احذف المتغير القديم `API_FOOTBALL_KEY` بعد إضافة الجديد.
6. اضغط Save Changes وانتظر انتهاء Deploy.

## 2) رفع الملفات إلى GitHub
استبدل ملفات المشروع بالنسخة المحدّثة، ثم ارفع التغييرات إلى نفس مستودع GitHub. إذا كان Render مربوطاً بالمستودع، سيبدأ النشر تلقائياً.

## 3) اختبار الخادم
افتح:
`https://football-ai-analyzer-5s7c.onrender.com/api/status`
ويفترض أن ترى `"provider":"Highlightly"` و `"api_configured":true`.

## 4) تحديث Netlify
ارفع محتويات مجلد `site` إلى Netlify إذا لم يكن النشر التلقائي مفعلاً. ملف `site/config.js` مضبوط على رابط Render الحالي.

## ملاحظات مهمة عن البيانات
- النسخة الحالية تعتمد على آخر خمس مباريات مكتملة لكل فريق، لأن هذا هو نطاق endpoint `last-five-games`.
- إحصائيات الشوط الأول تستنتج من أحداث المباراة عندما تكون متاحة.
- الركنيات والبطاقات وxG تظهر فقط إذا كانت ضمن تغطية تفاصيل المباراة.
- الإصابات وodds غير مفعّلة في هذا الربط الأولي؛ لا تعرض الواجهة قيماً مخمّنة لها.
- يتم تخزين الاستجابات مؤقتاً لتقليل استهلاك حصة API.
