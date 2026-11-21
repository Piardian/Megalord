# Megalord ileri kayıt durumu — 23 Ekim 2026

**Son durum:** Ortak risk bütçeli ileri gölge fiyat portföyü de başlatıldı. Etkin kesit 23 Ekim 17:56:52 İstanbul başlangıçlıdır. [Kârlılık doğrulaması ve güncel oturum](PROFITABILITY_VALIDATION.md). Aşağıdaki 17:29 kaynak oturumu, sürüm değişiminde durdurulup yalıtılmış kaynak kopyalarıyla yenilendi; tarihsel not olarak korunmuştur. Broker demo dolumları ve net gider mutabakatı hâlâ eksiktir.

## Güncelleme: eski kaynak ve CLT aktarımı giderildi

23 Ekim **14:29:51 UTC / 17:29:51 İstanbul** itibarıyla dört motor için ayrı, salt okunur ileri tarama süreçleri başlatıldı. Süreç kaydı: `audit/forward_sources/current.json`. Etkin kaynak oturumu: `audit/forward_sources/20261023T142951_071758Z/`. Buna bağlı yeni fiyat/kaynak kayıt oturumu: `audit/forward/20261023T142955_189944Z/`.

Trawma, Pragma, Stigma ve ChecklistTrigger ilk taramalarını başarıyla tamamladı. Motorlar gerçek güncel kapalı mumlarla yerel karar fonksiyonlarını çalıştırıyor. Boş taramalar da heartbeat ve kullanılan mumların SHA-256 özetiyle kaydediliyor. Her döngüde yeni sinyal çıkması beklenmez; sıfır sinyal başarısız tarama demek değildir.

CLT kaynakları **ayrı gölge çalışma alanına** derlendi. Yerel risk kabulünden geçen sinyallerin mevcut `publishMegalordEntryIntent` fonksiyonundan dosyaya aktarımı bağlandı. Aynı köprünün kabul edilen bir tarihsel regresyon örneğiyle dosya ürettiği doğrulandı; bu örnek gerçek ileri kayıt alanına yazılmadı. Eski CLT web/bildirim servisi değiştirilmedi veya yeniden başlatılmadı. “Aktarım dağıtıldı” ifadesi bu ayrı gölge çalışma alanını anlatır.

Önceki eski Pragma/Trawma/Stigma dosyaları artık bu yeni ileri oturumun giriş kaynağı değildir. Eski dosyalar adli iz için gözlenmeye devam eder; yeni sinyaller yalnız dört ayrı işçinin `intents.jsonl` dosyalarından okunur. Eski arşiv sonuçları ileri işlem örneklemine taşınmaz.

İlk Pragma kontrolünde terminal eski CADCHF mum önbelleği döndürdü. Salt okunur yeniden sorgu eklendi; halen eski kalan veri kabul edilmez. Dört motor daha sonra güncel kapalı mumlarla başarılı kontrol verdi. Bilinmeyen veya eksik veri, sessizce geçerli sonuç yapılmaz.

Gölge süreçlerde emir, hesap değiştirme ve Telegram gönderimi kapalıdır. Yerel strateji/TP/SL parametreleri değiştirilmedi. Begonya ve Begonya00 mevcut politikanın kapalı motorları olarak korunuyor; makro dosyaları okunması bunların işlem motoru olarak açıldığı anlamına gelmez. Trawma'nın yerel izlediği fakat Megalord whitelist'i dışında kalan fırsatlar ayrı `excluded_intents.jsonl` dosyasına gider.

**Çözülen:** dört motorun güncel tarama kaynağı, boş tarama görünürlüğü ve CLT giriş aktarımı. **Henüz çözülmüş sayılmayan:** ortak risk tahsisiyle ileri portföy yürütümü ve ayrı demo hesapta doğrulanmış dolum/tam gider mutabakatı. Bu nedenle `source_pipeline_ready` sağlıklı dört heartbeat olduğunda true olabilir; `portfolio_trial_ready=false`, `net_return=null`, `live_approved=false` kalır. Kaynak aktarımı başarısı edge kanıtı değildir.

Kod/politika kopyası kaynak oturumunda donduruldu. Kaynak değişimi doğrulaması taramadan önce yapılır; değişmiş kaynakla yeni tarama yapılmaz. Yerel CLT derlemesi ayrıca kaynak hash'lerini kontrol eder; seçili karar bayrakları `selected_flags.json` içinde sabittir. Makro dosyasının tarama öncesi/sonrası kopyaları kaydedilir; tarama sırasında değişmişse ayrıca işaretlenir. Bu kontroller tüm üst kaynakların nedenselliğini kanıtlamaz.

Kanıtlar: her motorun `heartbeat.json`, `heartbeats.jsonl`, `bars/`, `intents.jsonl`, stdout/stderr dosyaları. CLT'de ayrıca `compiled_manifest.json`, `native_result.json`, `native_bridge.jsonl` (ilk kabul edilen sinyalde oluşur). Testler: kök pakette **237/237** geçti (`audit/forward_sources/tests.xml`); son odaklı kontroller **22/22** geçti (`focused_tests.xml`). CLT gerçek köprü regresyonu `audit/forward_sources/test_clt_handoff.cjs` ile geçti. Test örneği `fixture_bridge_only.jsonl` içindedir; ileri sonuç değildir.

Süreçler arka planda çalışır; bilgisayar/terminal kapanırsa kesintisiz devam garantisi yoktur. Aynı motor için ikinci süreç dosya kilidiyle engellenir. Tüm kaynak işçilerini durdurmak için kaynak oturumuna `STOP`; bağlı kaydediciyi durdurmak için yeni kayıt oturumuna `STOP` dosyası oluşturulur. Oturum 30 takvim gününde sona erer; bu yalnız altyapı gözlem süresidir ve otomatik edge/canlı kabul eşiği değildir.

## Önceki kayıt aşamasının tarihsel notu

**Aşağıdaki başlangıç durumu önceki oturuma aittir; üstteki güncelleme kaynak/CLT eksiklerini giderir.**

**Başlayan aşama: gerçek zamanlı kanıt toplama. Tam portföy gölge/demo icrası henüz başlamadı.**

Etkin kayıt oturumu: `audit/forward/20261023T141635_000302Z/`.
Başlangıç: 23 Ekim 2026 14:16:35 UTC / 17:16:35 İstanbul.
İlk tek döngülü kontrol ayrı `20261023T141516_663002Z/` oturumundadır; bunun sürekli kayıt olduğu iddia edilmez.

## Ne kaydediliyor?

- Açık MT5 terminalinden salt okunur bid/ask ve kontroller arasındaki tick dizileri. Broker zaman düzeltmesi her döngüde BTC heartbeat ile doğrulanıyor. Stale veya eksik fiyatlar görünür tutuluyor; dolum varsayılmıyor.
- Trawma, Pragma, Stigma, CLT giriş kaynaklarının değişen içerikleri, gözlem zamanı ve SHA-256 özeti. Makro/haber dosyaları da değiştikçe kaydediliyor. Gözlem zamanı geçmişe taşınmıyor.
- Kaynaklardan ilk görülen sinyaller. Eski ve kapanmış kayıtlar açıkça işaretleniyor; ileri işlem örneklemi sayılmıyor. Kaynağın kendi zaman damgası tek başına nedensellik kanıtı değil.
- Kod/politika kopyası ve sürüm kontrolü. Kod, whitelist veya politika değişirse mevcut kayıt oturumu duruyor; değişmiş sürüm aynı deney diye devam etmiyor. Dış motorların çalışan belleğinin bu kaynaklarla eşleştiği ayrıca kanıtlanmalıdır.

Kaydedici emir göndermez, hesap değiştirmez, sembol seçmez, Telegram mesajı göndermez ve başka servis başlatmaz. Kök Megalord durumu kullanılmaz. Bağlı hesap yalnız fiyat kaynağıdır; demo hesap olduğuna dair yeni bir iddia yoktur. Mevcut dış süreçlere müdahale edilmedi.

## İlk bulgular ve açık işler

İlk sürekli kayıt döngüsünün çok kısa ilk tick aralığında 15 boş sorgu döndü; sağlık kaydında korundu. Sonraki döngü hatasız veri topladı. İlk yüklemede 113 kaynak kaydı görüldü; bunlar 113 yeni ileri işlem değildir.

CLT giriş niyeti dosyası bulunmuyor: kaynak düzeltmesi çalışan derlenmiş servise henüz dağıtılmamış. Pragma dosyası yaklaşık beş saat, Trawma yaklaşık iki gün, Stigma yaklaşık bir gün eskiydi. Bu yaşlar sinyal çıkmadığını kanıtlamaz; üretici heartbeat'i ve boş tarama kayıtları olmadan kaynak canlılığı doğrulanamaz.

Tam testi başlatmak için gereken kalan teknik işler:

1. Dört etkin motorun çalışan sürümünü doğrulamak; boş taramalar dahil heartbeat ve kullanılabilir giriş niyeti kaydı üretmesini sağlamak. CLT köprüsünü kontrollü biçimde derleyip dağıtmak.
2. Bu akışı mevcut risk tahsisiyle birleştiren, bağımsız fırsat toplamı yerine ortak portföyü izleyen ileri gölge yürütücüsünü bağlamak ve doğrulamak. Eski daemon gölge sonuçları bu kanıtın yerine kullanılmaz.
3. Ayrı, açıkça doğrulanmış demo hesapta gerçek dolum ve hesap hareketi mutabakatını kurmak. Bilinmeyen komisyon/taşıma/işletme gideri sıfır kabul edilmeyecek.

Bu işler tamamlanana kadar `portfolio_trial_ready=false`, `net_return=null`, `live_approved=false` kalır. Haber dosyasının bulunması takvimin eksiksizliği demek değildir. Kâr/edge değerlendirmesi yapılmıyor.

## Çalıştırma ve kontrol

Kaydedici: `python -m megalord.research.forward_capture`. `--once` yalnız bir kontrol döngüsüdür. Her başlatma ayrı klasör açar; eski dönem devam ediyormuş gibi birleştirilmez. Aynı anda ikinci kaydedici açmayın.

İlk 30 takvim günü yalnız altyapı kapsamı incelemesi içindir; kâr görünce bitirme veya otomatik canlıya geçiş yoktur. Süre sonunda kaydedici durur. Bu süre bir edge kabul eşiği değildir. Bilgisayar/terminal kapanırsa kayıt kesilir; otomatik açılış veya kesintisiz çalışma garantisi kurulmadı. Kesintiler performans örnekleminden gizlenmemelidir.

Çalışma durumu: oturum içindeki `status.json` ve `health.jsonl`. Fiyatlar `quotes.jsonl` ve `ticks/`; kaynak kanıtları `source_observations.jsonl` ve `observations/`; protokol `protocol.json`, kaynak sürümleri `source_manifest.json`.

Durdurma: etkin oturum klasörüne `STOP` adlı boş dosya oluşturun. Sonraki döngüde salt okunur bağlantı kapanır. Başlatılan süreç kimliği `audit/forward/collector.pid`, konsol kayıtları `collector_stdout.log` ve `collector_stderr.log` dosyalarındadır; süreç kimliği zamanla eskiyebilir.

Doğrulama: yeni kayıt testleri 7/7; broker saat testleriyle birlikte 9/9 geçti. Daha önceki 217 test bu eklemelerden önceki toplamdır. Gerçek fiyat üzerinde başlangıç kontrolü ve ardışık kayıt döngüleri gözlendi; broker dolum testi yapılmadı.
