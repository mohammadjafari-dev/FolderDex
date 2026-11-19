# چک‌لیست انتشار — FolderDex

زبان: [فارسی](RELEASING.fa.md) · [English](RELEASING.md)

هر بار که یک بسته‌ی فشرده‌ی قابل‌حمل (zip) منتشر می‌شود، همه‌ی مراحل زیر را به‌ترتیب انجام بدهید.
این چک‌لیست برای این است که هیچ انتشاری با لینک مخزنِ کد که کار نمی‌کند،
فایل مجوزِ ناقص یا فایل اجراییِ آزمایش‌نشده بیرون نرود.

## ۱. مخزن عمومی و در دسترس

- مخزن باید روی
  <https://github.com/mohammadjafari-dev/FolderDex> **در حالت عمومی (Public)** قرار داشته باشد.
- `BUILD.bat` همین URL را داخل `SOURCE.txt` بسته می‌نویسد (عرضه‌ی کد منبع طبق GPL).
  پیش از هر ساختی، URL را در **یک نشست تازه‌ی مرورگر (خارج از حساب)** باز کنید و
  مطمئن شوید باز می‌شود.
- ورک‌فلوی `tests` باید برای کامیتِ انتشار سبز باشد — بخشِ **Actions**:
  pytest روی windows-latest (پایتون 3.11/3.12/3.13) و ubuntu-latest (3.12)،
  به‌علاوه‌ی jobِ `repo-integrity`.

- مخزن باید واقعاً **محتوای کدِ همین نسخه را داشته باشد** — صفحه باید درختِ
  فایل‌ها و `LICENSE` و `README.md` را نشان بدهد و تگِ `v<VERSION>` همان‌جا
  وجود داشته باشد (در مرحله‌ی ۲ پایین ساخته و push می‌شود — قبل از هر ساخت).
  یک مخزنِ خالی (یا فقط redirectِ تغییرِ نام بدونِ تاریخچه)
  **عرضه‌ی معتبرِ کد منبع طبق GPL نیست** — اول انتقال را کامل کنید، بعد بسازید.

## ۲. ایجاد تگ روی کامیتِ انتشار (قبل از ساخت)

`SOURCE.txt` داخل بسته، تگِ `v<VERSION>` و SHAِ کامیتِ دقیق را به‌عنوان
عرضه‌ی کد منبع طبق GPL می‌نویسد — پس تگ باید **قبل از** ساخت ساخته و push شده
باشد:

- سطرِ `[<VERSION>] - <date>` در ابتدای
  [CHANGELOG.fa.md](../CHANGELOG.fa.md) و [CHANGELOG.md](../CHANGELOG.md) را
  روی **تاریخِ واقعیِ انتشار** بگذارید — تگ، zip و هر دو CHANGELOG باید یک روز
  را بگویند.
- کامیت و push کنید، بعد دقیقاً همان کامیت را تگ بزنید:

  ```bat
  git tag -a v<VERSION> -m "FolderDex <VERSION>"
  git push origin main v<VERSION>
  ```

## ۳. ساخت روی ویندوز

- رایانه‌ی ساخت: ویندوز ۱۰/۱۱ x64 با پایتون 3.11 یا 3.12 یا 3.13 x64 (فقط
  رایانه‌ی ساخت به این‌ها نیاز دارد — بسته‌ی نهایی به نصبِ هیچ پیش‌نیازی احتیاج ندارد).
- از **روی همان کامیتِ تگ‌خورده** بسازید — `git log --oneline -1` باید کامیتِ
  زیرِ تگ را نشان بدهد؛ SHAِ همان کامیت عیناً در `SOURCE.txt` می‌نشیند.
- اجرا:

  ```bat
  BUILD.bat
  ```

  فقط اگر می‌خواهید `ffmpeg.exe` هم همراه باشد `/with-ffmpeg` را اضافه کنید —
  این گزینه به `ffmpeg-LICENSE.txt` کنار exe روی PATH نیاز دارد و بدون آن ساخت
  متوقف می‌شود (ببینید [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) §7).
  ساختِ `/with-ffmpeg` یعنی توزیعِ FFmpeg: انتشارِ عمومی‌اش نیازمندِ
  **کدِ منبعِ متناظر و تنظیماتِ ساختِ همان FFmpeg** است که باید کنارِ
  انتشار عرضه شود. تا وقتی این‌ها مهیا نیست، ساختِ `/with-ffmpeg` فقط
  خصوصی/تستی بماند — ساختِ پیش‌فرض بدونِ FFmpeg است و مشمول این الزام نیست.

- نتیجه: `dist\FolderDex\` (one-dir) و `FolderDex-win64-portable.zip`.

## ۴. بررسی محتوای بسته

فایل zip را باز کنید و بودنِ همه‌ی این‌ها را تأیید کنید:

- [ ] `FolderDex.exe` (+ پوشه‌ی `_internal\` زمان اجرا)
- [ ] `LICENSE`
- [ ] `THIRD_PARTY_NOTICES.md`
- [ ] `SOURCE.txt` — آن را باز کنید؛ URL داخلش باید همان مخزنِ عمومی باشد و
      کامیتِ دقیقِ ساخت و تگِ `v<VERSION>` که در آن نوشته شده باید پیش از
      توزیع به همان مخزن **push** شده باشند (لینکِ کامیت را در یک نشستِ تازه‌ی
      مرورگر باز کنید — عرضه‌ی کد منبعی که 404 بدهد عرضه‌ی کد منبع نیست)
- [ ] `licenses\` — حداقل:
      `Qt-PySide6-LGPL-3.0.txt`، `Qt-PySide6-LGPL-note.txt`، `Python-PSF-2.0.txt`،
      `Python-LICENSE.txt`، `Pillow-MIT-CMU.txt`، `py7zr-LGPL-2.1.txt`،
      `pycryptodomex-BSD-2.txt`، `rarfile-ISC.txt`،
      `pypdfium2-Apache-2.0.txt`، `PyInstaller-COPYING.txt`، `README.txt` —
      به‌علاوه‌ی `licenses\Pillow\LICENSE` و پوشه‌های مجوزِ دقیقِ همین نسخه برای
      دوازده بسته‌ی اصلی: `licenses\py7zr\`،
      `licenses\rarfile\`، `licenses\pypdfium2\`، `licenses\pycryptodomex\`،
      `licenses\pyppmd\`، `licenses\pybcj\`، `licenses\inflate64\`،
      `licenses\multivolumefile\`، `licenses\texttable\`، `licenses\brotli\`،
      `licenses\psutil\`، `licenses\backports_zstd\`
      (متن‌های دقیقِ نسخه‌ی همین ساخت — اگر هرکدام غایب باشد BUILD.bat با خطا
      متوقف می‌شود)
- [ ] فایل‌های مجوزِ 7-Zip یعنی `7zip-License.txt` و `README-7zip.txt` کنارِ
      `7za.exe` همراه — داخلِ بسته در `_internal\bin\` قرار دارند
      (SHA-256 مربوط به
      `7za.exe` داخل همان README نوشته شده) — این هش را یک‌بار با دانلودِ
      رسمیِ 7-Zip هم تطبیق بدهید، نه فقط با مقدارِ ثبت‌شده
- [ ] `README.md` و `README.fa.md`
- [ ] اجزای شخص ثالثی که **واقعاً** داخل `_internal\PySide6\Qt\` بسته شده‌اند
      باید در صفحه‌ی مجوزهای Qt که در
      `licenses\Qt-PySide6-LGPL-note.txt` به آن ارجاع شده پوشش داده شده باشند — یکی از اجزای
      واقعاً همراه را با https://doc.qt.io/qt-6/licenses-used-in-qt.html
      تطبیق دهید

## ۵. آزمایش اولیه‌ی بسته

- zip را در یک پوشه‌ی **قابل‌نوشتن** (هرگز Program Files) باز کنید و یک‌بار
  `FolderDex.exe` را اجرا کنید.
- یک پوشه‌ی اصلی معرفی کنید، یک اسکن بگیرید، صفحه‌ی جزئیات یک عنوان را باز کنید
  و تمیز خارج شوید.
- نسخه‌ی بدون امضای دیجیتال روی هر رایانه‌ی تازه یک‌بار پیام **SmartScreen**
  نشان می‌دهد («More info» و بعد «Run anyway»). برای فایل اجراییِ بدون امضا
  طبیعی است و باید در یادداشت‌های انتشار بیاید.

## ۶. هش و انتشار

- مقدار SHA-256 فایل zip را محاسبه کنید و همان عدد را در یادداشت‌های انتشار بگذارید:

  ```powershell
  Get-FileHash .\FolderDex-win64-portable.zip -Algorithm SHA256
  ```

- برای همان تگ روی گیت‌هاب Release بسازید و `FolderDex-win64-portable.zip` را
  آپلود کنید. یادداشت‌های انتشار: خلاصه‌ی نسخه از [CHANGELOG.fa.md](../CHANGELOG.fa.md)،
  SHA-256 مرحله‌ی بالا، و یادداشتِ یک‌بارِ SmartScreen.

## ۷. بعد از انتشار

- صفحه‌ی مخزن را دوباره باز کنید: README درست رندر می‌شود و همه‌ی لینک‌ها باز
  می‌شوند. وقتی بعداً اسکرین‌شات‌های خودتان (انگلیسی و فارسی) را در
  `screenshots/` گذاشتید و در هر دو README جاسازی کردید، پیش از push دوباره
  `pytest tests/test_repo_integrity.py` را اجرا کنید.
- تا زمانی که نسخه‌ی منتشرشده‌ای در دسترس است، مخزن باید عمومی بماند.
