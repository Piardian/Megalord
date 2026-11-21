# Megalord ileri test ve sermaye kararı

Durum: Taslak protokol; ileri test başlatılmadı, broker demo dolumları doğrulanmadı, gerçek sermaye onayı yok.

Amaç: Altı motorun fırsat üretimi ve insan destekli uygulamasının, aynı toplam risk bütçesinde bütün giderler sonrası tekrarlanabilir sonuç üretip üretmediğini ölçmek. Kârlılık garanti edilmez; başarısız veya belirsiz sonuç da geçerli sonuçtur.

22 Ekim 2026 kaynak kontrolü: `megalord/megalord_daemon.py`, istenen moddan bağımsız `self.mode = "shadow"` atıyor. Bu kilit kaldırılmadı. Shadow çıktısı broker demo emri veya gerçek dolum kanıtı değildir. Mevcut `simulated1M` hipotez toplamları bu protokolde gerçek M1 teyidi olarak kabul edilmez.

## Başlamadan sabitlenecekler

- Broker/venue, demo hesap türü, test edilen piyasa ve semboller; hedef canlı hesapla sözleşme büyüklüğü, kaldıraç, emir tipi ve ücret farkları.
- Kod/config sürümü, altı motorun rolü ve sembol kapsamı; hangi kararları insanın vereceği, teyit ve geçersizleşme tanımları.
- Ortak sanal sermaye, başlangıç risk bütçesi, toplam eşzamanlı açık risk, günlük ve toplam özkaynak düşüşü durdurma sınırları. Bu sayılar henüz kullanıcı sermaye/kayıp sınırlarıyla belirlenmedi; keyfi bir risk yüzdesi atanmadı.
- Önceden belirlenmiş gözlem süresi, örneklem yeterliliği yöntemi ve değerlendirme tarihleri. 30 gün veya 100 işlem otomatik yeterlilik sayılmaz. Bağımlı işlemler bloklar halinde değerlendirilir; her gün kâr görülünce testi bitirmek yok.
- Net beklenti, ekonomik olarak gerekli minimum getiri, giderlerin tanımı, maliyet stresi ve düşüş sınırları için kabul koşulları. Şimdiki pozitif örneklere göre eşik seçilmez.

## Aşama 1: Gerçek fiyat akışı ve broker demo icrası

Yalnız geçmiş replay değil, ileri akan bid/ask üzerinde bütün fırsatlar kaydedilir. Demo emir adaptörü açıkça demo hesabı doğrulamalı; gerçek hesapta emir göndermeyi reddetmelidir. Demo bağlantısı, emir gönderme yetkisi ve mutabakatı kurulup doğrulanmadan bu aşama başladı denmez.

Her opportunity_id için sinyalin bilindiği an, teslimat gecikmesi, karar anı, girildi/pas/izlenemedi/emir dolmadı, gerçek emir kimlikleri, miktar, dolum, kısmi kapama, iptal, ret ve broker hesap hareketleri saklanır. İnsan kararı sonuç oluşmadan kaydedilir. Pas geçilen kazançlar, kurala uygun stoplar, açık zararlar ve teknik arızalar dışlanmaz.

Aynı fırsatlar için mekanik referans ile hibrit politika ayrı izlenir; her ikisi aynı sermaye ve toplam risk bütçesiyle değerlendirilir. Altı motorun örtüşen sinyalleri bağımsız işlem gibi şişirilmez. Ana kabul adayı önceden seçilir; diğer karşılaştırmalar keşifseldir.

Net sonuç broker gerçekleşmiş hareketleriyle mutabık olmalı; açık pozisyonlar bid/ask ile özkaynağa dahil edilmelidir. Komisyon, swap/funding ve dönüşüm giderleri eklenir. Dolum fiyatında bulunan spread/kayma ikinci kez düşülmez. Sabit işletme giderleri portföyün net parasal sonucundan ayrıca düşülür. Demo ile canlı gider farkı belgelenir.

## Aşama 2: Önceden belirlenmiş değerlendirme

- Teknik geçerlilik: kayıp sinyal/karar, eksik veya yanlış dolum, uyuşmayan hesap hareketi bulunması durumunda güvenilir performans sonucu yok.
- Ekonomik geçerlilik: sabit protokolde gider sonrası getiri ve gün/hafta bağımlılığını dikkate alan belirsizlik aralığı incelenir. Alt sınır sıfırın üzerinde değilse pozitif edge doğrulandı denmez; bu durum tek başına edge kesin yok demek de değildir.
- Risk geçerliliği: açık zararlar dahil özkaynak düşüşü, kayıp serileri ve toplam risk önceden belirlenen sınırları aşmamalı. Net pozitif sonuç bu ihlalleri telafi etmez.
- Dayanıklılık: gider stresi ve zaman dilimleri raporlanır; tek işlem, tek gün veya seçilmiş bir motorun sonradan bütün sonucu taşıması açıkça gösterilir. Demo geçişi canlı kâr garantisi sayılmaz.

Sonuç üç kategoriden biridir: başarısız, yetersiz kanıt, sınırlı gerçek icra pilotuna aday. Başarısızlıktan sonra değiştirilen sürüm yeni ileri dönem doğrulaması gerektirir; önceki test sonradan yeniden etiketlenmez.

## Aşama 3: Ayrı onaylı sınırlı gerçek icra pilotu

Demo testinin ölçemediği gerçek dolum, likidite, kayma ve insan davranışı farkı ancak gerçek işlemle ölçülebilir. Bu aşama ücretsiz veya risksiz değildir. Yalnız açık kullanıcı onayı ve önceden belirlenmiş toplam parasal kayıp bütçesiyle yürütülebilir. Henüz bu onay/bütçe mevcut değil; gerçek emir başlatılmaz.

Pilotun amacı yeni bir optimizasyon değil, demo ile gerçek icra farkının ölçülmesidir. Pilot uygunluk göstermeden ana sermayeye geçiş önerilmez. Pilot sonrası bile gelecekte zarar olasılığı sıfırlanmaz.
