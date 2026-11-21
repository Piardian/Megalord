# Megalord: altı motor için geliştirme sözleşmesi

Amaç, Trawma, Pragma, Stigma, Begonya00, ChecklistTrigger ve Begonya'nın
birleşiminde bütün maliyetlerden sonra pozitif beklenen getiri üretmek.
Tek pariteye kapanmak veya geçmişte iyi görünen motoru seçmek hedef değil.
Convexity varsa ayrı bir makro girdidir; altı motordan birinin yerine geçmez.

## Başarı tanımı

Daha uzak TP veya daha dar SL otomatik başarı değildir. Birlikte ölçülecek
sonuçlar: maliyet sonrası işlem beklentisi, toplam net sonuç, kayıp kuyruğu,
sermaye düşüşü, işlem sıklığı ve belirsizlik. Kazanan oranını artıran ama
nadir büyük kayıplarla beklentiyi bozan değişiklik reddedilir. Risk artırmak
veya daha çok eşzamanlı işlem açmak tek başına edge sayılmaz.

Geçmiş verilerin araştırmada görülmüş olması kaydedilir. Daha önce taranan
2020–2026 verileri sonradan dokunulmamış örneklem diye sunulmaz. Gelecek fiyat
ve dolumlar kontrol edilemez; mühendislik doğruluğu ve araştırma disiplini
sınanabilir, gelecekte net kâr garanti edilemez.

## 1. Eşit ölçüm zemini

Her motor için kaynak kodu ve veri hash'i, veri zaman dilimi, sinyalin gerçekten
bilindiği an, emir gönderim/iptal kuralı, ilk stop, hedefler, kısmi kapama,
BE ve zaman çıkışı kaydedilir. Açık pozisyona tamamlanmış sonuç atanmaz.
Eksik çıkış zamanı tahmini süreyle doldurulmaz; eksik getiri TP etiketiyle
kazanca çevrilmez. Epoch sayıları açık birim olmadan parse edilmez.

`megalord.research.trade_contract.MeasuredTrade` doğrulanmış kapalı işlem
için ilk ortak sözleşmedir. Henüz tüm motorların buna dönüştürüldüğü anlamına
gelmez. `inventory` yalnızca kaynak envanteridir; alanların bulunması,
işlemin nedensel veya doğru olması demek değildir. Açık pozisyonların burada
kapalı örnekleme alınmaması, canlı motorun açık pozisyon açamayacağı anlamına gelmez.

Maliyet defteri enstrüman, broker/venue, dönem ve emir tipi düzeyinde olmalı:
komisyon, bid/ask, kayma, swap/funding, kur dönüşümü. Orta fiyatla hesaplanan
brüt sonuçtan spread ve kayma düşülür. Gerçek alış/satış dolumlarından hesaplanan
sonuçta zaten bulunan spread/kayma ikinci kez düşülmez. Sıfır maliyet ancak
açıkça belgelenmişse kullanılabilir. 1x, 1.5x, 2x stresleri aynı tanımlı baz
defter üzerinden değerlendirilir; bilinmeyen maliyet sıfır kabul edilmez.

Ortak UTC olay kuyruğu tüm sembollerin açılış, emir, dolum ve kapama olaylarını
aynı sermaye üzerinde yürütmeli. Günlük bütçe yüzdesi ile R birimi açıkça
ayrılmalı. Aynı mumda hem TP hem SL varsa düşük zaman çözünürlüğünden kesin
yol icat edilmez; tick/alt mum veya muhafazakâr belirsizlik aralığı kullanılır.
Mevcut `MegalordSimulator` bunun yerine verilen eski sonuçları tahsis eder;
raporundaki `live_readiness_proven=False` bu ayrımı korur.

## 2. Avantajın nereden gelebileceğini sınama

Önce mevcut kuralları dondurarak her motor ve sembol için giriş sonrası fiyat
yolu çıkarılır: MFE, MAE, hedefe/stopa süre, sinyalin sönme hızı, limit emrinin
dolmama oranı ve maliyetin ilk riske oranı. Sadece dolan işlemler üzerinden
limit emir başarısı değerlendirilmez; dolmayan fırsatlar da ölçülür.

Ardından ayrı hipotezler sınanır:

1. **Giriş:** Sinyalin dayandığı olaydan sonra gerçekten yönlü fiyat hareketi
   var mı? Aynı sembol, saat ve nedensel rejimde eşleştirilmiş kontrol girişleri
   ile karşılaştırılır. Orderflow'un veri kaynağı hedef piyasayı temsil ediyor mu?
2. **Çıkış:** Stop hangi yapısal olayı geçersiz sayıyor? Önce o sınır tanımlanır,
   ardından pozisyon büyüklüğü hesaplanır. TP keyfi uzaklaştırılmaz; sinyalin
   öngördüğü hareket ve sönme süresiyle ilişkilendirilir. BE/TP1'in kazanan
   işlemleri erken kesip kesmediği ayrı hipotezdir. İlk temel ölçüm tamamlanmadan
   mevcut TP/SL parametreleri değiştirilmez.
3. **Birleşim:** Aynı yönde sinyal veren iki motorun ikinci sinyali, birincinin
   zaten bildiğine ek tahmin gücü sağlıyor mu? Farklı matematik aile etiketi
   istatistiksel bağımsızlık kanıtı değildir. Motorların aynı fiyat olayını
   tekrar sayması veya aynı kayıp günlerini paylaşması ölçülür.

Her hipotez için mekanizma, beklenen etki, sabit karşılaştırma, değiştirilecek
tek unsur, deneme sayısı ve reddetme kuralı sonuç görülmeden yazılır.
Başarısız denemeler tutulur. Sadece en yüksek backtest skoru raporlanmaz.

## 3. Önceden ilan edilen portföy karşılaştırmaları

Güvenilir ham olaylar hazır olduğunda ilk aile 13 senaryodur:
altı motor tek başına, altısının eşit toplam risk bütçeli birleşimi ve
birleşimden sırayla her motorun çıkarıldığı altı senaryo.
Hepsi aynı dönem, sembol kapsamı, yürütme kuralları ve maliyet defterini kullanır.
Portföyü altı kat risk aldırarak başarılı göstermek yasaktır. Sürekli açık
risk, günlük risk ve para birimi kümeleri ayrıca raporlanır.

Sembol/motor bazlı kayıplar birleşik ortalamada saklanmaz. Aynı günün ilişkili
işlemleri bağımsız gözlem sayılmaz; gün/hafta bloklarıyla belirsizlik ve
seri kayıp dağılımı ölçülür. Eğitim/test ayrımında pozisyonların etiket süresi
ayırma sınırını aşamaz; zaman sıralı ileri test, purging ve uygun embargo
uygulanır. Sonradan seçilen senaryonun güven aralığı seçim etkisinden bağımsızmış
gibi sunulmaz.

## 4. Canlıya geçiş kapıları

Altı motorun her biri aynı ortak replay/paper protokolüne bağlanmalı. Her barda
sinyal var/yok, emir, iptal, SL/TP, kısmi kapama ve zaman çıkışında parity
aranmalı; sadece kazanmış girişler kontrol edilmemeli. Model parity ile gerçek
broker dolumlarının eşitliği ayrı konulardır.

Dondurulan aday daha önce seçimde kullanılmamış ileri dönem paper/shadow
izlemede maliyet sonrası değerlendirilir. Yeterli örneklem, kayıp kuyruğu,
maliyet stresi ve broker yürütme kanıtı olmadan canlı onay verilmez.
Pozitif nokta tahmini tek başına geçiş ölçütü değildir. Olumlu sonuçta bile
gelecek dönem zarar ihtimali kalır; küçük riskli pilot ayrıca açık onay ister.

## Araçlar ve mevcut sınır

Kaynak envanteri:

```powershell
python -m megalord.research.inventory --output audit/yeni-envanter.json
```

Araç strateji çalıştırmaz, broker/Telegram çağırmaz; yalnızca kaynak okur ve
yeni rapor dosyası yazar. Araştırma sözleşmesi eksikleri reddeder fakat
kendisi kazançlı strateji, veri doğruluğu kanıtı veya otomatik canlı onay üretmez.
