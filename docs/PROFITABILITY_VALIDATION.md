# Megalord: ileri kârlılık doğrulaması

**23 Ekim 20:56 İstanbul güncellemesi:** Kullanıcının seçimiyle mevcut The5ers hesabına emir göndermeden hesap geçmişi/masraf kaydı bağlantısı kuruldu. [Kolay açıklama ve bağlantı durumu](HESAP_BAGLANTISI.md). Bu, aşağıdaki ayrı demo dolumu ve tam net kâr kanıtı eksiklerini kendiliğinden çözmez.

23 Ekim 2026, **14:56:52 UTC / 17:56:52 İstanbul** itibarıyla ortak risk bütçesini kullanan ileri **gölge fiyat portföyü** etkin. Oturum: `audit/forward_portfolio/20261023T145652_833130Z/`. Kaynaklar: `audit/forward_sources/20261023T145622_952053Z/`. Güncel adresler ilgili `current.json` dosyalarındadır. Bu başlangıç, önceki yalnız kaynak toplama aşamasından ayrıdır; geçmiş sinyaller yeni işlem gibi doldurulmaz.

14:49'da açılan ilk portföy kesiti 14:51'de Trawma çalışma kopyası başka bir süreçte değişince sürüm kilidiyle durdu. Açık/kapanmış işlem yoktu; durum ve durma nedeni eski klasörde korundu. Sonrasında yerel motorlar kendi değişmez kod kopyalarından çalışacak şekilde yalıtıldı. İlk yalıtım kontrolündeki eksik Pragma `config/settings.py` bağımlılığı da kopyaya dahil edilip gerçek güncel mumlarla yeniden doğrulandı. Aradaki süre kesintisiz ileri test diye gösterilmez; etkin örneklemin başlangıcı üstteki yeni zamandır.

## Şu an yapılan ölçüm

Dört etkin yerel motorun yeni girişleri gerçek bid/ask ile kalite filtresi, arbiter, haber kontrolü ve risk firewall'ından geçirilir. Kaydedilen kararlar aynı girdilerle tekrar çalıştırılıp eşitliği kontrol edilir. İki kapalı motorun durumu ve mevcut sinyal/TP/SL/risk parametreleri değiştirilmedi.

Aynı anda açık pozisyonlar ortak portföyde tutulur. Klon kapasitesi, risk tahsisi, günlük zarar bütçesi ve motor kayıp serileri birlikte işler. Pozisyon çıkışları özgün ikiz emir ve koruma kurallarıyla takip edilir. Kontroller arasındaki tick dizisi sunucuda duran stop/TP modeline uygulanır; breakeven/trailing değişiklikleri yalnız gerçek takip kontrolünde uygulanır. Bağlantı kesintisinde geçmişte yapılmamış stop güncellemeleri geriye dönük uydurulmaz.

Kısmi çıkışlar bir kez gerçekleşmiş sayılır. Açık kalan miktar bid/ask ile işaretlenir; açık zararlar özkaynak düşüşünden gizlenmez. Sonuç **risk ağırlıklı fiyat-modeli R** birimindedir; nakit kâr veya gerçek broker dolumu değildir. Sanal sermaye veya gerçek lot miktarı uydurulmadı. Günlük risk ve kayıp serisi de giderler tamamlanana kadar fiyat-modeli sonucuna bağlıdır; sonradan yalnız komisyon düşmek bütün gerçek yürütme yolunu doğrulamaz.

İleri kontrol sonuçları: ilk portföy döngüleri sağlıklı, dört kaynak sağlıklı, başlangıçta **0 kapanmış / 0 açık model işlem**. Sıfır işlem, sıfır risk veya kanıtlanmış sıfır maliyet anlamına gelmez. Net sonuç `null` kalır. Gerçek emir ve Telegram gönderimi kapalıdır. Megalord üretim durum dosyasına dokunulmaz.

## Sonuçlar görülmeden sabitlenen karar kuralları

- Sabit inceleme ufku: **180 takvim günü**. İlk 30 gün yalnız altyapı/kapsam kontrolüdür; erken kâr görülünce başarı ilan edilmez.
- En az **100 broker dolumu ve hesap hareketiyle doğrulanmış kapanış** gerekir. Model kapanışları bu sayıya otomatik dahil edilmez. 180 gün sonunda sayı veya veri yeterli değilse sonuç “yetersiz kanıt”dır; sırf olumlu sonuç çıkana kadar uzatılmaz.
- Komisyon, spread, kayma, swap/funding, dönüşüm ve işletme giderleri açıkça mutabık olmalıdır. Spread dolum fiyatına zaten dahilse ikinci kez düşülmez. Bilinmeyen bileşen sıfır sayılamaz. Giderleri sonradan avantajlı seçmek yoktur.
- Aynı portföy için bütün giderler sonrası sonuç ve işlem giderlerinin 1,5 kat olduğu stres senaryosu incelenir. Önceki “yalnız spread 1,5 kat” sonucu bunun yerine geçmez. Risk/filtre kararları maliyetlerden etkileniyorsa stres yürütme yolu da yeniden değerlendirilmelidir.
- Başlangıca bağlı 180 adet ardışık 24 saatlik özkaynak değişimi gerekir; işlem olmayan dönemler atılmaz. Nakit ve açık pozisyonlar birlikte hesaba girer. İlk 175 günün 25 tam haftalık bloklarıyla 20.000 örneklemli, sabit tohumlu bootstrap %95 belirsizlik aralığı hesaplanır. Son beş gün toplam PnL'ye dahildir; ayrıca bağımsız tam hafta sayılmaz. Bu yöntem haftalar arası her bağımlılığı ortadan kaldırmaz; bağımsız incelemede dayanıklılığı değerlendirilmelidir.
- Hem taban hem stres sonucunun pozitif olması ve haftalık ortalama aralığının alt ucunun sıfır üstünde kalması gerekir. Bu, tek başına “kanıtlandı/canlı aç” düğmesi değildir: kanıtın kaynağı, risk ihlalleri, kârın tek gün/motora bağımlılığı ve demo–canlı farkı ayrıca incelenir.

Bu sayılar strateji optimizasyonu değildir; kanıt inceleme eşikleridir ve her stratejide istatistiksel yeterliliği garanti etmez. `net_review.py` eksik maliyet, eksik dönem, eksik dolum veya erken başarıyla olumlu net karar üretmeyi reddeder. Sentetik olumlu veri bile yalnız bağımsız denetime aday sonucu verir; `live_approved` veya `net_edge_proven` otomatik true olmaz.

## Net kazancı doğrulamak için hâlâ gereken gerçek kanıt

**Bu Megalord oturumuna bağlı, doğrulanmış demo dolumları ve tüm giderlerin nakit mutabakatı henüz yok.** Mevcut terminal fiyat kaynağı olarak kullanılıyor; demo hesabı olduğuna dair iddia yapılmadı. Başka projelerin demo işlemleri Megalord portföyünün kanıtı sayılamaz. Eksik demo hesabı yerine gerçek hesaba emir gönderilmez.

Bu nedenle şu anda başlayan şey ortak portföyün **koşullu ileri fiyat takibidir**. Net kâr doğrulama çalışmasının gerçek yürütme ve gider ayağı eksiktir. Bu eksik giderilmeden 180 gün geçmesi veya modelin kâr yazması net edge kanıtı oluşturmaz.

Mevcut Trawma denemelerinin iki `config.json` profilinde de aynı `FivePercentOnline-Real` terminali/sunucusu görüldü. Dosya adındaki “demo” ayrı ücretsiz demo doğrulaması sayılmadı; bu hesaplar Megalord emri için otomatik kullanılmadı. Megalord'a ait ayrı demo hesabı kurulup sunucu/hesap türü doğrulanmalı ve dolum–masraf akışı bu oturuma bağlanmalıdır. Bu dış kurulum eksikliği, yalnız yazılım testleriyle giderilemez.

Haber kontrolü yalıtılmış oturum önbelleğini kullanır; kök haber dosyasını değiştirmez. Güncel takvim dosyası, bütün haberlerin eksiksiz kapsandığının kanıtı değildir. Üst makro kaynağın nedenselliği de otomatik doğrulanmış sayılmaz. Bütün girdi/kapsam eksikleri açıkça raporlanmalıdır.

## Kayıtlar ve işletim

Oturumdaki `protocol.json` ve `source_manifest.json` kuralları/kodu dondurur. `decisions.jsonl` bütün denenen kararları, `events.jsonl` model giriş ve kısmi/nihai çıkışları, `ticks/` kullanılan fiyatları, `contexts.jsonl` makro/haber durumunu, `equity.jsonl` açık zararları da içeren özkaynağı saklar. `health.jsonl` sorunları geçmişten silmez. `state.json` güncel özettir; `complete_venue_net_r` ve nakit net sonuç bilinmiyorsa boş kalır.

Kaynak işçilerinin eski 30 günlük toplama bitişi, ilk portföy işlemi oluşmadan bu 180 günlük protokole uzatıldı. Eski protokol kopyası ve önce/sonra hash'leri `source_protocol_before_extension.json` / `source_extension.json` içinde korunur. Strateji parametreleri değişmedi. Önceki bağımsız fiyat kaydedicisi kendi 30 günlük kesiti olarak kalır; bu portföy kendi girdilerini ayrıca kaydeder.

Güncel durum ve kanıt değerlendirmesi: `python -m megalord.research.profitability_status`. Komut `profitability_review.json` üretir; eksik broker muhasebesini modelden tamamlamaz. Bu raporun tarihinden sonra ayrıca çalıştırılmazsa eskiyebilir.

Durdurmak için etkin portföy klasörüne `STOP` dosyası koyun. Açık model pozisyonları kapandı varsayılmaz; son durum saklanır. Yeniden başlatma ayrı kesit açar ve kesinti gizlenmez. Bilgisayar/terminal kapanırsa kesintisiz çalışma garantisi yoktur. Aynı kaynak oturumuna ikinci portföy dosya kilidiyle engellenir. Gerçek para pilotu ayrıca açık onay ve parasal kayıp bütçesi gerektirir; mevcut çalışma bunu açmaz.

Doğrulama: kök test paketinde **251/251** test geçti (`audit/forward_portfolio/tests-final.xml`). Son kaynak yalıtım düzeltmesinin odaklı kontrolleri **21/21** geçti; yalıtılmış Pragma ayrıca güncel kapalı mumlarla çalıştırıldı. Yeni testler klon bütçesini, kısmi çıkışın çift sayılmamasını, gap stopu, açık spread zararını, günlük reseti, geçmiş sinyalin ileri işlem sayılmamasını ve bilinmeyen maliyet/erken süreyle olumlu net karar verilememesini kapsıyor. Bunlar yazılım testidir; kârlılık verisi değildir.
