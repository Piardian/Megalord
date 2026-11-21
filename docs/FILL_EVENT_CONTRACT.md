# Altı motorun dolum/gider olay sözleşmesi

`megalord.research.fill_ledger` ve `event_import` Trawma, Pragma, Stigma,
Begonya00, ChecklistTrigger ve Begonya için aynı kapalı işlem muhasebesini
kullanır. Bu bir emir gönderici veya yeni sinyal filtresi değildir.

Her olayın benzersiz kimliği, olay zamanı (`occurred_at`) ve bilinebildiği an
(`available_at`) UTC ve açık saat dilimiyle yazılır. Kapanış mumundan çıkan
OHLC sonucu mum açılışında biliniyormuş gibi girilmez. Tick zamanı bilinmiyorsa
üretici bunun model zamanı olduğunu belirtmeli; gerçek dolum diye sunmamalıdır.

`ENTRY`: pozitif miktar ve gerçek/model dolum fiyatı. Girişe gelecekteki
kazanılmış getiri eklenemez. İlk çıkıştan sonra ek giriş bu tek işlem
sözleşmesinde desteklenmez; bu pyramiding için ayrı olay/strateji tanımı gerekir.

`EXIT`: kapanan miktar, çıkış fiyatı ve **açıkça sağlanan** brüt nakit PnL.
Kısmi kapamalar ayrı olaydır. Kapanan miktar dolan miktarı aşamaz. İşlem
tamamen kapanmadan kapalı örnekleme alınmaz. İlk risk ve bütün nakit tutarları
aynı hesap para biriminde olmalıdır. Döviz dönüşümü veya ilk parasal risk bu
modülde uydurulmaz; veri üreticisi bunları doğrulayıp sağlamalıdır.

`COST`: spread, slippage, commission, carry veya conversion bileşenine ait
açık nakit tutarı. Negatif nakit gideri ayrı gelir/iadeye ayrılır. Maliyet
stresinde bu kredi büyütülmez; yalnızca pozitif giderler büyütülür.

Her bileşenin muamelesi ayrıca belirtilir:

- `in_fill_pnl`: fiyat/dolum PnL'sinde zaten bulunuyor; tekrar COST eklenemez.
- `cash_event`: ayrı nakit olayı zorunlu; sıfır ise açık sıfır olayı yazılır.
- `bundled_cash`: üretici bileşenleri toplu raporluyor. `cost_groups` ile
  örneğin `native_execution_fee: [commission, slippage]` bildirilir; tek grup
  nakit olayı kaydedilir. Bilinmeyen bileşen payları uydurulmaz veya ayrıca
  tekrar düşülmez.
- `not_applicable`: neden uygulanmadığı maliyet bazında belgelenir.
- `unknown`: kapsam çözülmemiştir; net performans örneğine geçiş engellenir.

Bu beyanlar kendi başına broker ölçümü kanıtı değildir. `cost_basis` kaynak,
venue, komisyon tarifesi, dönüşüm ve model varsayımlarını açıklamalıdır.
`initial_risk_cash` veya EXIT `gross_cash` değerlerinin ekonomik doğruluğu
modül tarafından bağımsız doğrulanmaz. Girdi hash'i, doğruladığı kaynak
belgesine bağlar; belgenin gerçeği temsil ettiğini garanti etmez.

Kapalı kanıtın `exit_time` alanı son net olayın **kullanılabilirlik anıdır**.
Bu kapalı sonuç replay'inde erken risk geri dönüşünü engelleyen muhafazakâr
bir tanımdır. Ara kısmi kapamaların nakit akışını ve gerçekleşmemiş riski
anlık simüle etmek için ham olaylar ayrıca kullanılmalıdır; kapalı sonuç
replay'i o işi yaptığını iddia etmez.

## Dosya arayüzü

Girdi JSON kökünde `schema_version: 1`, `source_kind` (`native_replay`,
`paper`, `broker_export`) ve `trades` listesi bulunur. Her işlemde `trade_id`,
motor, sembol, yön, ilk stop, ilk parasal risk, para birimi, maliyet beyanı,
maliyet açıklaması ve `events` vardır. API alanlarının tam örneği
`tests/test_event_import.py` içindeki **sentetik test girdisidir**.

```powershell
python -m megalord.research.event_import ham_olaylar.json yeni_sonuc.json
```

Çıktı mevcut dosyanın üzerine yazılmaz. Mükerrer işlem kimliğinde her iki
kopya da reddedilir; satır sırası iyi sonucu seçmez. Kabul edilmiş kapanışlar
ve reddetme gerekçeleri ayrı kaydedilir. Kanıt türü korunur; paper veya
model sonuçları broker kaydına dönüşmez. Araç broker/Telegram çağırmaz ve
`live_approved` her zaman kapalıdır.

Dolum PnL'sine gömülü giderler `requires_native_cost_replay=True` ile taşınır.
Bu kayıtlarda 1.5x stres için yalnızca nakit giderini çarpmak reddedilir;
üretici değişen maliyetle yeniden yürütülmelidir. Maliyetin giriş kararını
etkilediği modellerde, gider ayrı kaydedilmiş olsa da strateji stres testi
native üreticiyi yeniden çalıştırmalıdır.

Native modelin aynı anda giriş/çıkış kaydı varsa kapalı replay varsayılan
olarak reddeder. `allow_same_instant=True` açık seçeneğinde bütün eşzamanlı
girişlerin riski önce ayrılır, anlık kapanışlar sonra hesaplanır. Böylece
anlık kazanç aynı andaki başka bir girişe sermaye sağlayamaz. Bu açık model
kuralı gerçek intrabar dolum sırasının kanıtı değildir.
