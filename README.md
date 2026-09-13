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
  core/fixtures/suggestions.json  500 elle yazılmış, tam etiketli öneri
  core/engine.py                  filtreleme + puanlama
  Sıfır maliyet, sıfır halüsinasyon, internet gerektirmez.

Katman 2 — AI (Gemini)            yalnızca serbest metin ("Yaz") modu
  core/ai.py                      anahtar sadece sunucuda
  İmza tabanlı önbellek (7 gün) + günlük kota (5 çağrı)
  Her başarısızlıkta sessizce Katman 1'e düşer.
```

AI bir süstür, çekirdek değil. `GEMINI_API_KEY` tanımsızken uygulama
eksiksiz çalışır.

### AI katmanını açma / kapama

Katman tek bir ortam değişkenine bağlıdır; kod değişikliği gerekmez.

```bash
# Kapat (uygulama seed havuzuyla tam çalışmaya devam eder)
npx vercel@latest env rm GEMINI_API_KEY production --yes

# Sonradan aç
printf '%s' "ANAHTAR" | npx vercel@latest env add GEMINI_API_KEY production
npx vercel@latest deploy --prod --yes
```

`/healthz/` ucundaki `aiEnabled` alanı o an hangi modda olduğunu söyler.
Kapalıyken "Yaz" modu, seed havuzunda anahtar kelime eşleştirmesi yapan
`core/textmode.py` ile çalışır — yani metin girişi hiçbir zaman ölü bir
özellik olmaz.

### AI maliyet koruması

API anahtarı proje sahibinin, çağrıyı yapan ise ziyaretçidir. Bu yüzden üç
ayrı günlük tavan var ve **en dar olanı** geçerlidir:

| Kapsam | Varsayılan | Neden |
|---|---|---|
| `global` | 200/gün | **Asıl koruma.** Kaç kişi ne yaparsa yapsın maliyeti üstten kilitler. |
| `ip:<özet>` | 15/gün | Tek bir kaynağın tavanı tek başına tüketmesini zorlaştırır. |
| `anon:<uuid>` | 5/gün | Normal kullanıcıda devreye giren en nazik sınır. |

İkisi de `RemoteConfig` üzerinden, panelden değiştirilebilir — kod dağıtımı
gerekmez (`ai_daily_global_limit`, `ai_daily_ip_limit`).

Ölçülen maliyet: çağrı başına 922 girdi + 270 çıktı token. `flash-lite`
ücretli fiyatlarıyla ~$0.00095. Global tavan 200 iken günlük üst sınır
~$0.19.

Çerez tabanlı sayaç **tek başına koruma değildir** — çerez silinebilir.
Global tavan bu yüzden var. IP, `X-Forwarded-For`'un ilk girdisinden değil
platformun kendi başlığından okunur; ilk girdi istemci tarafından
uydurulabilir.

En güçlü koruma uygulamada değil Google tarafındadır: **AI Studio'da
faturalandırma kapalıysa** kota bitince `429` döner ve hiçbir ücret
tahakkuk etmez. Kod 429'u zaten sessizce yutup seed havuzuna düşer.

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

### 1b. Kullanıcının açık sinyali gevşetilmez

İki kural motoru yönetir:

- **Seçilen kelime sert filtredir.** "kahve" seçildiyse yalnızca o etikete
  sahip öneriler havuza girer. Hiçbiri tutmazsa kısıt düşer ve kullanıcı boş
  ekran yerine alakasız olmayan bir kart görür.
- **Bütçe kademesi bir tercihtir, sadece tavan değil.** "İyi" seçildiyse
  501-1.500 TL bandındaki öneriler varsa yalnızca onlar gösterilir; o bantta
  hiç öneri yoksa filtre kendiliğinden geri çekilir. Tavan her koşulda sert
  kalır — bütçe asla aşılmaz.

Bu ikisi başlangıçta yumuşak puandı ve ölçüldüğünde kırıktı: "Bol" seçen
kullanıcıya %82 oranında bedava öneri, "kahve" seçene %25 isabet dönüyordu.
Regresyon testleri `BudgetIntentTests` ve `KeywordIntentTests` altında.

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
- `seasonality` yalnızca 1-12 arası ay numarası; etiket yazılırsa öneri
  havuzda görünür ama hiç gösterilmez
- `companions` "friends" içeriyorsa "arkadaşımla" etiketi zorunlu; çip
  etikete bakar, eksik etiket öneriyi o seçimden gizler
- başlığı mevcut bir başlığa %72'den fazla benzeyen ya da ilk adımı
  birebir aynı olan öneri reddedilir (yakın tekrar tespiti)

### Havuz dağılımı

Bütçe kademeleri, "bedava varsayılan" ilkesine göre dengelendi:

| Kademe | Öneri |
|---|---|
| Bedava | 250 |
| Az (~1-150 TL) | 100 |
| Orta (~150-500 TL) | 75 |
| İyi (~500-1.500 TL) | 45 |
| Bol (1.500 TL+) | 30 |

14 kategorinin her birinde 31 ile 42 arasında öneri var. Yeni içerik
eklerken kategori yerine kullanıcının doğrudan seçebildiği **kelime ×
kademe** kombinasyonlarındaki boşluklara bakmak daha isabetli sonuç verir.

Kural ihlali olan hiçbir şey yazılmaz; komut hata verip çıkar.

---

## Test

```bash
.venv/bin/python manage.py test core
```

116 test. Hiçbiri ağa çıkmaz (AI çağrıları taklit edilir).

Testler içerik kurallarını da kilitler: seed havuzundaki bir öneri konum
bağımsızlık testini geçmiyorsa test kırılır.

---

## Dağıtım (Vercel + Supabase)

Vercel, Django'yu `manage.py` üzerinden otomatik algılar; `WSGI_APPLICATION`
ayarından giriş noktasını bulur ve `STATIC_ROOT` tanımlıysa `collectstatic`
komutunu **kendisi çalıştırır**. Ayrı bir `api/index.py` sarmalayıcısı
gerekmez.

### 1. Şema

Şema Supabase projesine **zaten uygulandı** (`napsam`, `oihloudoojbxeobxlwhp`).
`django_migrations` kayıtları da yazıldığı için `migrate` komutu tekrar
çalıştırıldığında hiçbir şeyi yeniden uygulamaz.

### 2. Bağlantı dizesi

Supabase şifreyi yalnızca proje oluşturulurken bir kez gösterir. Elinde
yoksa yenile:

```
Project Settings → Database → Reset database password
```

Sonra `.env` içindeki `DATABASE_URL` satırına yapıştır ve seed verisini yükle:

```bash
.venv/bin/python manage.py migrate      # no-op olmalı
.venv/bin/python manage.py seed
```

**Hangi bağlantı nerede:**

| Kullanım | Havuz | Port |
|---|---|---|
| `migrate` / `seed` (lokalden) | Session pooler | 5432 |
| Vercel çalışma anı | Transaction pooler | **6543** |

Session pooler tam bir oturum verir; şema işlemleri için doğru olan odur.
Transaction pooler her işlemden sonra bağlantıyı havuza iade eder — sunucusuz
için gerekli, DDL için uygun değil.

> `settings.py` transaction pooler'ı bilir: `CONN_MAX_AGE=0`,
> `DISABLE_SERVER_SIDE_CURSORS=True` ve psycopg'nin otomatik prepared
> statement'ları kapalı (`prepare_threshold=None`). Bunlar olmadan pgbouncer
> transaction modunda sorgular kırılır.

> **Seed'i her zaman session pooler (5432) ile çalıştır.** `.env` Vercel ile
> aynı transaction pooler'ı (6543) gösterse bile, uzun yazma işlemleri o
> havuzda kırılgan. Ölçülen olay: 6543 üzerinden seed işlemin ortasında
> "idle in transaction / ClientRead" durumunda dakikalarca asılı kaldı; aynı
> iş 5432 üzerinden 29 sn'de bitti. Portu yalnızca o komut için değiştirmek:
>
> ```bash
> DATABASE_URL=$(python -c "import pathlib,re;u=next(l.split('=',1)[1] for l in pathlib.Path('.env').read_text().splitlines() if l.startswith('DATABASE_URL='));print(re.sub(r':(\d+)/',':5432/',u,count=1))") .venv/bin/python manage.py seed
> ```
>
> `settings.py` ayrıca `connect_timeout=10` ve TCP keepalive ayarlar: havuz
> adresi birden fazla IP'ye çözülüyor, biri yanıt vermezse bağlantı eskiden
> dakikalarca bekliyordu; kopan bir bağlantı da hata vermek yerine asılı
> kalıyordu.

### 3. Veri erişimi: yalnızca Django

`public` şemasındaki tüm tablolarda RLS açık ve **hiç policy yok**; ayrıca
`anon` ve `authenticated` rollerinin yetkileri geri alındı. Yani tablolar
PostgREST üzerinden (anon anahtarıyla) dışarıya tamamen kapalı.

Django `postgres` rolüyle, yani tablo sahibi olarak bağlandığı için RLS'i
varsayılan olarak baypas eder; uygulama bundan etkilenmez.

Bu bilinçli bir karar: Supabase burada saf Postgres olarak kullanılıyor,
BaaS olarak değil. İleride Supabase istemci kütüphanelerini kullanmak
istersen bu kilidi gevşetmen ve gerçek policy'ler yazman gerekir.

### 4. Vercel ortam değişkenleri

| Değişken | Değer |
|---|---|
| `DJANGO_SECRET_KEY` | Yeni üretilmiş, lokalden **farklı** |
| `DJANGO_DEBUG` | `False` |
| `DATABASE_URL` | Supabase transaction pooler dizesi |
| `GEMINI_API_KEY` | (opsiyonel) boşsa AI katmanı kapalı |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |

`ALLOWED_HOSTS` elle ayarlanmaz; `VERCEL_URL` ve `*.vercel.app` otomatik eklenir.

### 5. Dağıt

```bash
npx vercel@latest login
npx vercel@latest link --yes --project napsam
npx vercel@latest deploy --prod --yes
```

Canlı: **https://napsam.vercel.app**

Ortam değişkenleri CLI ile yazılır, değerler kabuğa basılmaz:

```bash
printf '%s' "$DEGER" | npx vercel@latest env add DEGISKEN_ADI production
```

`.vercelignore`, `.env` ve `db.sqlite3` gibi dosyaların pakete girmesini
engeller — bu dosya olmadan CLI `.gitignore`'a bakar, ona güvenmek yerine
sırları açıkça dışarıda bırakmak daha güvenli.

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
