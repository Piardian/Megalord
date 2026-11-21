# Altı motor için araştırma araçları

`megalord.research.source_book.import_sources()` mevcut altı kaynağı ortak
gözlem alanlarına dönüştürür. Motor çalıştırmaz veya broker'a bağlanmaz.
Kaynaklar farklı nitelikte olduğundan yerel sonuçları otomatik olarak net
portföy sonucu saymaz. `net_performance_eligible` ilk gözlem katmanında
bilinçli olarak kapalıdır; maliyet sertifikasyonu ayrıca yapılmalıdır.

Kapalı, kaynaklı ve maliyet bazları açıklanmış işlemler `MeasuredTrade` ile
temsil edilir. `ReplayTrade` bunlara kararlı, benzersiz bir kimlik ekler.
Maliyet bazını doğrulama sorumluluğu veri üreticisindedir: gross R gerçek
alış/satış dolumlarından türetilmişse spread/kayma yeniden düşülmemelidir.

`replay(trades, initial_equity=10000, risk_fraction=.005,
max_open_risk_fraction=.01, cost_multiplier=1.0)` giriş anındaki kapalı
sermayeden risk ayırır; o işlemin parasal ilk riski sonradan değişmez.
Motor sayısı arttığında ortak açık risk tavanı değişmez. Eşzamanlı girişte
kimlik sırası kullanılır; gelecekte kazanmış olan öne alınmaz. Bu bağlama
duyarlı portföy ölçümü, aynı anda açılan işlemlerde kararlı sıra duyarlılığı
ayrıca araştırılmadan kesin bir optimum tahsis sonucu sayılmamalıdır.

`comparison_family(trades, window_start=..., window_end=..., **replay_options)`
13 sabit senaryoyu üretir. Altı motorun da ölçülmüş girdisi ve açık UTC
dönemi zorunludur. Dönem dışındaki pozisyonlar hata verir; kullanıcı bunları
sessizce sansürlemek yerine dönem sınırı/açık pozisyon politikasını deney
öncesinde belirlemelidir. Kapsam ve zaman penceresinin eşitliği, kaynakların
tamlığını veya seçim etkisinin giderildiğini kendi başına kanıtlamaz.

Bu araç mevcut canlı arbiter veya firewall'ın parity kopyası değildir.
Günlük zarar tavanı, para birimi kümeleri, kısmi kapamalarda sermaye
serbestleştirme, açık pozisyonların anlık değeri ve broker dolum simülasyonu
bu nötr kapalı-sonuç replay'inde yoktur. Çıktı her zaman `live_approved=False`
taşır. 0.5% / 1% varsayılanları çevrimdışı karşılaştırma girdisidir; canlı
risk tavsiyesi veya gerçek hesap ayar değişikliği değildir.

Kanıt paketi: `audit/megalord-integration-2026-10-22/SONUC.md`.
