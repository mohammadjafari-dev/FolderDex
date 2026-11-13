# چک‌لیست انتشار — FolderDex

زبان: [فارسی](RELEASING.fa.md) · [English](RELEASING.md)

هر بار که یک zip قابل‌حمل منتشر می‌شود، همه‌ی مراحل زیر را به‌ترتیب انجام بدهید.
این چک‌لیست برای این است که هیچ انتشاری با لینک مخزنِ کد که کار نمی‌کند،
فایل مجوزِ ناقص یا فایل اجراییِ آزمایش‌نشده بیرون نرود.

## ۱. مخزن عمومی و در دسترس

- مخزن باید روی
  <https://github.com/mohammadjafari-dev/FolderDex> **عمومی (public)** باشد.
- `BUILD.bat` همین URL را داخل `SOURCE.txt` بسته می‌نویسد (پیشنهاد سورسِ GPL).
  پیش از هر ساختی، URL را در **یک نشست تازه‌ی مرورگر (خارج از حساب)** باز کنید و
  مطمئن شوید باز می‌شود.
- ورک‌فلوی `tests` باید برای کامیتِ انتشار سبز باشد — بخشِ **Actions**:
  pytest روی windows-latest (پایتون 3.11/3.12/3.13) و ubuntu-latest (3.12)،
  به‌علاوه‌ی jobِ `repo-integrity`.

- مخزن باید واقعاً **محتوای کدِ همین نسخه را داشته باشد** — صفحه باید درختِ
  فایل‌ها و `LICENSE` و `README.md` را نشان بدهد و تگِ `v<VERSION>` همان‌جا
  وجود داشته باشد. مخزنی خالی (یا فقط redirectِ تغییرِ نام بدونِ تاریخچه)
  **پیشنهاد سورسِ GPL معتبر نیست** — اول انتقال را کامل کن، بعد بساز.

## ۲. ساخت روی ویندوز

- رایانه‌ی ساخت: ویندوز ۱۰/۱۱ x64 با پایتون 3.11 یا 3.12 یا 3.13 x64 (فقط
  رایانه‌ی ساخت به این‌ها نیاز دارد — بسته‌ی نهایی هیچ چیزی نصب نمی‌خواهد).
- اجرا:

  ```bat
  BUILD.bat
  ```

  فقط اگر می‌خواهید `ffmpeg.exe` هم همراه باشد `/with-ffmpeg` را اضافه کنید —
  این گزینه به `ffmpeg-LICENSE.txt` کنار exe روی PATH نیاز دارد و بدون آن ساخت
  متوقف می‌شود (ببینید [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) §6).
  بیلدِ `/with-ffmpeg` یعنی توزیعِ FFmpeg: انتشارِ عمومی‌اش نیازمندِ
  **سورسِ متناظرِ دقیق و تنظیماتِ ساختِ همان FFmpeg** است که باید کنارِ
  انتشار عرضه شود. تا وقتی این‌ها مهیا نیست، بیلدِ `/with-ffmpeg` فقط
  خصوصی/تستی بماند — بیلدِ پیش‌فرض بدونِ FFmpeg است و تأثیری نمی‌گیرد.

- نتیجه: `dist\FolderDex\` (one-dir) و `FolderDex-win64-portable.zip`.

## ۳. بررسی محتوای بسته

فایل zip را باز کنید و بودنِ همه‌ی این‌ها را تأیید کنید:

- [ ] `FolderDex.exe` (+ پوشه‌ی `_internal\` زمان اجرا)
- [ ] `LICENSE`
- [ ] `THIRD_PARTY_NOTICES.md`
- [ ] `SOURCE.txt` — بازش کنید؛ URL داخلش باید همان مخزنِ عمومی باشد
- [ ] `licenses\` — حداقل:
      `Qt-PySide6-LGPL-3.0.txt`، `Qt-PySide6-LGPL-note.txt`، `Python-PSF-2.0.txt`،
      `Python-LICENSE.txt`، `Pillow-MIT-CMU.txt`، `py7zr-LGPL-2.1.txt`،
      `pycryptodomex-BSD-2.txt`، `rarfile-ISC.txt`،
      `pypdfium2-Apache-2.0.txt`، `README.txt` — به‌علاوه‌ی `licenses\Pillow\LICENSE`
      و `licenses\py7zr\`، `licenses\rarfile\`، `licenses\pypdfium2\`
      وقتی از build venv ساخته شده باشد
- [ ] `bin\7zip-License.txt` و `bin\README-7zip.txt` (SHA-256 مربوط به
      `7za.exe` داخل همان README نوشته شده) — این هش را یک‌بار با دانلودِ
      رسمیِ 7-Zip هم تطبیق بدهید، نه فقط با مقدارِ ثبت‌شده
- [ ] `README.md` و `README.fa.md`

## ۴. آزمایش اولیه بسته

- zip را در یک پوشه‌ی **قابل‌نوشتن** (هرگز Program Files) باز کنید و یک‌بار
  `FolderDex.exe` را اجرا کنید.
- یک پوشه‌ی اصلی معرفی کنید، یک اسکن بگیرید، صفحه‌ی جزئیات یک عنوان را باز کنید
  و تمیز خارج شوید.
- نسخه‌ی بدون امضای دیجیتال روی هر رایانه‌ی تازه یک‌بار پیام **SmartScreen**
  نشان می‌دهد («More info» و بعد «Run anyway»). برای فایل اجراییِ بدون امضا
  طبیعی است و باید در یادداشت‌های انتشار بیاید.

## ۵. هش، تگ، انتشار

- مقدار SHA-256 فایل zip را محاسبه کنید و همان عدد را در یادداشت‌های انتشار بگذارید:

  ```powershell
  Get-FileHash .\FolderDex-win64-portable.zip -Algorithm SHA256
  ```

- پیش از ایجاد تگ نسخه، سطرِ `[<VERSION>] - <date>` در ابتدای
  [CHANGELOG.fa.md](../CHANGELOG.fa.md) و [CHANGELOG.md](../CHANGELOG.md) را
  روی **تاریخِ واقعیِ انتشار** بگذارید — تگ، zip و هر دو CHANGELOG باید یک روز
  را بگویند و تگ باید روی همان کامیتِ ساخته‌شدنِ zip بنشیند.

- تگ نسخه را ایجاد کنید:

  ```bat
  git tag -a v<VERSION> -m "FolderDex <VERSION>"
  git push origin v<VERSION>
  ```

- برای همان تگ روی گیت‌هاب Release بسازید و `FolderDex-win64-portable.zip` را
  آپلود کنید. یادداشت‌های انتشار: خلاصه‌ی نسخه از [CHANGELOG.fa.md](../CHANGELOG.fa.md)،
  SHA-256 مرحله‌ی بالا، و یادداشتِ یک‌بارِ SmartScreen.

## ۶. بعد از انتشار

- صفحه‌ی مخزن را دوباره باز کنید: README درست رندر می‌شود، لینک‌ها باز می‌شوند و
  بخش اسکرین‌شات‌ها بعد از اینکه مالک شش PNG را در `screenshots/` کامیت کرد
  همان عکس‌ها را نشان می‌دهد.
- تا وقتی zip منتشرشده‌ای وجود دارد، مخزن باید عمومی بماند.
