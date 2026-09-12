# NAPSAM

> "Ne yapsam ya?" diye takılan insana, tek dokunuşla, bütçesine uyan,
> **şu an ve bulunduğu yerde** yapabileceği tek bir somut fikir veren uygulama.

Başarı tanımı: kullanıcı açar, 10 saniyede fikri alır, **uygulamayı kapatır**
ve o şeyi yapar. Bu üründe uzun oturum bir başarı değil, bir hatadır.

---

## Hızlı başlangıç

```bash
uv venv --python 3.13
uv pip install -r requirements.txt
cp .env.example .env          # DJANGO_SECRET_KEY üret, DEBUG=True yap
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed
.venv/bin/python manage.py runserver
```

`DATABASE_URL` boşsa lokalde SQLite kullanılır — hızlı geliştirme için yeterli.

Gizli anahtar üretmek için:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

---

## Mimari

```
Katman 1 — SEED HAVUZU            trafiğin ~%85'i
  core/fixtures/suggestions.json  elle yazılmış, tam etiketli öneriler
  core/engine.py                  filtreleme + puanlama
  Sıfır maliyet, sıfır halüsinasyon, internet gerektirmez.

Katman 2 — AI (Gemini)            yalnızca serbest metin ("Yaz") modu
  core/ai.py                      anahtar sadece sunucuda
  İmza tabanlı önbellek (7 gün) + günlük kota (5 çağrı)
  Her başarısızlıkta sessizce Katman 1'e düşer.
```

AI bir süstür, çekirdek değil. `GEMINI_API_KEY` tanımsızken uygulama
eksiksiz çalışır.

### Dosya haritası

| Dosya | İş |
|---|---|
| `core/models.py` | Veri modeli. Şehir/ilçe alanı **bilinçli olarak yok**. |
| `core/engine.py` | Öneri motoru: sert filtreler + yumuşak puanlama. |
| `core/validators.py` | İçerik denetimi. Konum bağımsızlığı burada zorlanır. |
| `core/ai.py` | Gemini katmanı ve çıktı son-denetimi. |
| `core/textmode.py` | Serbest metin → bağlam (AI olmadan da çalışır). |
| `core/chips.py` | Kelime seti; günün saatine göre değişir. |
| `core/middleware.py` | Anonim kimlik. Veritabanına yazmaz. |
| `static/css/napsam.css` | Tasarım jetonları, iki tema. |

---

## İki temel kısıt

### 1. Konum bağımsızlığı

Bir öneri Türkiye'nin **her yerinde** yapılabilir olmak zorundadır.
Şehir, ilçe, semt, cadde veya işletme ismi geçen öneri havuza **giremez**.

| ❌ | ✅ |
|---|---|
| "Kadıköy sahilinde yürü" | "En yakın su kenarına yürü" |
| "Vapura bin" | "Toplu taşımada son durağa kadar git" |
| "Ankara Kalesi'ne çık" | "Bulunduğun yerin en yüksek noktasına çık" |

Bu kural `core/validators.py` tarafından hem seed içeriğine hem de AI
çıktısına uygulanır. Denetim **Türkçe büyük/küçük harf tuzağına** karşı
metni ASCII'ye katlayarak yapar: `"İ".lower()` Python'da `i` + birleşik
nokta üretir, `"I".lower()` ise `ı` değil `i` verir. İkisi de blok
listesini sessizce delerdi.

### 2. Bütçe tutarları koda gömülü değil

Enflasyonla 6 ayda eskidiği için kademeler **net asgari ücrete endeksli
oranlar** olarak saklanır (`BudgetTier.ratio_min/ratio_max`) ve tutar
çalışma anında hesaplanır.

Asgari ücret değiştiğinde tek satır güncellemek yeter — kod dağıtımı gerekmez:

```
/yonetim/ → Uzaktan ayarlar → net_minimum_wage
```

---

## İçerik ekleme

Öneriler `core/fixtures/suggestions.json` içinde. Ekledikten sonra:

```bash
.venv/bin/python manage.py seed --check   # yazmaz, sadece denetler
.venv/bin/python manage.py seed
```

Her öneri şu kuralları geçmek zorundadır:

- `title` ≤ 6 kelime, emir kipi
- `hook` tek cümle
- `steps` 2 veya 3 madde
- `fallback` **zorunlu** — "plan tutmazsa ne yapılır?" cevapsız kalamaz
- `place: mekanli` ise `venue_type` dolu, işletme ismi **yok**
- şehir/ilçe/işletme ismi yok, koçvari dil yok, İngilizce devşirme yok

Kural ihlali olan hiçbir şey yazılmaz; komut hata verip çıkar.

---

## Test

```bash
.venv/bin/python manage.py test core
```

81 test. Hiçbiri ağa çıkmaz (AI çağrıları taklit edilir).

Testler içerik kurallarını da kilitler: seed havuzundaki bir öneri konum
bağımsızlık testini geçmiyorsa test kırılır.

---

## Dağıtım (Vercel + Supabase)

Vercel, Django'yu `manage.py` üzerinden otomatik algılar; `WSGI_APPLICATION`
ayarından giriş noktasını bulur ve `STATIC_ROOT` tanımlıysa `collectstatic`
komutunu **kendisi çalıştırır**. Ayrı bir `api/index.py` sarmalayıcısı
gerekmez.

### 1. Veritabanı şemasını Supabase'e uygula

Supabase panelinden bağlantı dizesini al:

```
Project Settings → Database → Connection string → Transaction pooler
```

`.env` dosyandaki `DATABASE_URL` satırına yapıştır (port **6543**), sonra:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed
```

> Sunucusuz ortamda kalıcı bağlantı tutulamaz. `settings.py` bunu bilir:
> `CONN_MAX_AGE=0`, `DISABLE_SERVER_SIDE_CURSORS=True` ve psycopg'nin
> otomatik prepared statement'ları kapalı (`prepare_threshold=None`).

### 2. Vercel ortam değişkenleri

| Değişken | Değer |
|---|---|
| `DJANGO_SECRET_KEY` | Yeni üretilmiş, lokalden **farklı** |
| `DJANGO_DEBUG` | `False` |
| `DATABASE_URL` | Supabase transaction pooler dizesi |
| `GEMINI_API_KEY` | (opsiyonel) boşsa AI katmanı kapalı |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |

`ALLOWED_HOSTS` elle ayarlanmaz; `VERCEL_URL` ve `*.vercel.app` otomatik eklenir.

### 3. Dağıt

```bash
vercel deploy
```

---

## Bilinçli olarak yapılmayanlar

Bunlar eksik değil, karar:

- Onboarding, hoş geldin ekranı, tanıtım slaytı
- Zorunlu üyelik (uygulama anonim çalışır)
- Keşfet / haber akışı — sonsuz kaydırma bu ürünün tam karşıtı
- Streak, rozet, seviye, puan, günlük giriş ödülü
- İstatistik paneli, tema mağazası
- Konum izni ve konum saklama

---

## Gizlilik

- Ana ekranı açıp çıkan bir ziyaretçi için **tek bir veritabanı satırı bile
  yazılmaz**. Bu bir test tarafından kilitlenmiştir.
- Kayıt ancak kaydet / geç / yapıyorum eylemlerinde oluşur.
- Çerezde yalnızca rastgele bir UUID vardır; kim olduğunu söylemez.
- Konum hiçbir aşamada istenmez, gönderilmez, saklanmaz — AI'ya giden
  bağlamda da yoktur.
