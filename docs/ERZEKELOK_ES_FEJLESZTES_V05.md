# V05: érzékelőnézet és az útkövetés továbbfejlesztése

Ez a dokumentum a **0.5-ös változat verziótörténeti műszaki leírása**. A mintavételezés, a helyzaj és az idealizált útkövetés alapfeltevései a V06-ban is érvényesek; a teljes fogadott szenzorarchívumot, hibakori diagnosztikát, radarnézetet és visszajátszást a [V06 észlelési leírás](ESZLELES_NAPLO_ES_RADAR_V06.md) tárgyalja. Az alábbi V05 ellenőrzési eredmények korábbi változathoz tartoznak.

## Mi tartja jelenleg az autót az úton?

A jármű mozgása a kijelölt útvonal mentén számított előrehaladásból származik. A modell a pozíciót és az irányt közvetlenül a vonalláncra helyezi; oldalirányú kormányzás és lekerekített kanyar nincs. A közeli autók és gyalogosok mintavételezett észlelése a fékezést befolyásolja. A koordinátor a parkolóhelyet és a konfliktuszónák használatát engedélyezi. Ezek együtt működő logisztikai folyamatot alkotnak, azonban nem igazolnak mérésből visszacsatolt sávkövetést.

A V05 külön megjeleníti a mintavételezett környezetet, a zajjal terhelt virtuális helyzetbecslést és az ismert útvonalhoz viszonyított eltérést. Ez az útkövetéshez szükséges megfigyelési adatok első változata. A helyzetbecslési zaj jelenleg diagnosztikai adat; nem módosítja a jármű pályáját vagy a meglévő fékezési döntést.

## Az érzékelők szerepe

Valós járműben a kamera vagy LiDAR az útjelölésekből, illetve környezeti geometriából segíthet helyzetet becsülni. A GNSS abszolút helyzetet, az IMU mozgásváltozást, a kerékodometria sebességet és megtett utat szolgáltathat; az egyes források eltérő környezeti korlátai miatt az összevetésük indokolt [2]. A prototípus ezek szerepét virtuális jelekkel szemlélteti:

| Funkció | A jelenleg működő jel | Értelmezés és korlát |
| --- | --- | --- |
| Saját helyzet meghatározása | Külön mintavételezett, korlátos zajjal terhelt X/Y helyzet. | Általános virtuális lokalizáció; nincs valódi GNSS, UWB vagy SLAM integráció. |
| Útvonalhoz igazodás vizsgálata | A becsült helyzet vetülete, előjeles oldaleltérés és irányeltérés az engedélyezett útvonalon. | Ismert térképi referencia; nincs kameraképből felismert útjelölés. |
| Környezet észlelése | A korábban is használt közeli járműpozíciók és gyalogosállapotok. | Sugár alapú virtuális észlelés; nincs sugárkövetés, takarás vagy látószögmodell. |
| Sebesség és irányváltozás | A mintavételkor rögzített sebesség, megtett út és irányváltozási ráta. | Ideális modellből származó odometriai helyettesítő jel; nem fizikai IMU vagy kerékjeladó. |

A zebra koordinátája ismert térképadat. A hozzá társított gyalogosállapot mintavételezett észlelés. A „nem észlelt gyalogost” állapot nem jelent általános bizonyítékot az átkelő akadálymentességére.

## Adateredet, időzítés és bizonytalanság

A `sensors` mező a szimulátor tényleges MQTT-telemetriájában és állapotképében szerepel. A műszerfal ebből rajzol; nem állít elő megfigyeléseket a böngészőben a teljes térképi igazságállapot alapján. A mintavételi idő `sample_sim_time_s`, az adat kora `age_s`; mindkettő szimulációs másodperc. A forráskódok és a mértékegységet jelölő mezőnevek a jel származását rögzítik.

A V05-ben a teljes `sensors` csomag csak a nyers MQTT-napló és a részletes telemetria megőrzési ablakában mentődött. A külön tömör útarchívum nem őrizte a teljes szenzorcsomagot, az érzékelőnézet csak aktív autóhoz tartozott. A V06 már külön, zlib-tömörített tartós archívumban őrzi a kijelző által feldolgozott fogadott csomagokat, és a mentett út idővonalán is megjeleníti őket. Régi V05-futásból csak a még megmaradt részletes telemetriához tartozó tényleges csomag állítható vissza; a már törölt észleléseket a program nem pótolja.

A konfigurált mintavételi periódus alapértéke 0,1 s, a helyi észlelés hatótávja 18 m, a frissességi határ 0,5 s. A helyzetbecslési hiba tengelyenként egyenletes, korlátos zaj. A konfigurált `position_uncertainty` alapértéke 0,1 m: ez tengelyenkénti ±0,1 m modellfeltevés, nem valós érzékelőspecifikáció vagy 95%-os konfidenciaintervallum. A kétdimenziós hiba felső korlátja √2·0,1 m. Külön véletlenszám-generátor teszi ismételhetővé a lokalizációs zajt anélkül, hogy a korábbi gyalogos- és fékezési forgatókönyvet megváltoztatná.

A referenciahelyzet a mintavételkori szimulált pozíció, a mozgáslépés előtt. Az általános jármű-telemetria X/Y értéke a lépés utáni pozíció, ezért a két adat kis eltérése normális. A becsült helyzet saját mezőket kap, és a vetítés ebből készül. Az oldaleltérés előre nézve jobbra pozitív, az SVG lefelé növekvő Y koordinátájában. Az irányeltérés fokban, −180° és +180° között értendő; a ±1° irányzaj virtuális feltevés. A vonallánc éles sarkain a legközelebbi szakasz kiválasztása irányugrást okozhat; azonos távolságnál a becsült irányhoz közelebbi szakasz kap elsőbbséget.

Érzékelőkieséskor az utolsó megfigyelés megmarad, az adat kora növekszik, a felület elavult állapotot jelez. A meglévő fékezési szabály a frissességi határ után megállítja az autót. Helyreálláskor új időbélyegű minta szükséges; a régi minta nem válik pusztán átnevezéssel frissé. Az útvonal és a fizikai jármű aktuális helyzete mozgás közben eltérhet az utolsó érzékelési mintától.

Az API `fresh` mezője az adatkor feltételét jelöli. A felület emellett a hibajelzést és a kapcsolatot is ellenőrzi, ezért érzékelőkiesést már a frissességi határ lejárta előtt hibás állapotként mutat.

## Szükségesek-e oszlopok?

Fedélzeti érzékelőkre épülő változatban a helyzetmeghatározás nem feltétlenül igényel új oszlopokat. Infrastruktúrával támogatott változatban épületre vagy meglévő tartószerkezetre telepített érzékelők is szóba jönnek; az elhelyezés és a lefedettség külön tervezési feladat. Gyári előkép a Bosch Automated Vehicle Maneuvering: telepített LiDAR, szükség esetén sztereókamera, peremszámítógép és a járműnek továbbított útvonal. A közlemény a gyártósor végétől a logisztikai parkolóig vezető feladatot is tárgyalja [1]. Ez nem bizonyít konkrét győri Audi-implementációt.

## Következő fejlesztések javasolt sorrendje

| Prioritás | Fejlesztés | Mérnöki érték | Vizsgálható bizonyíték |
| --- | --- | --- | --- |
| 1 | Korlátos kormányzású síkbeli járműmodell, lekerekített kanyarok és becsült helyzetből számított útkövető szabályozó. | Az úton maradás a szabályozás eredményévé válik; a kanyarok és helyzetbecslési hibák hatása mérhető. | Több azonosított maggal oldalhiba, úthatár-átlépés, parkolási siker és zajfüggés; összevetés az ideális modellel. |
| 2 | Geometriai kamera- és LiDAR-modell látószöggel, takarással és úthatár-megfigyeléssel. | Elkülönül az ismert térkép és a ténylegesen látható környezet. | Takarás és látómezőn kívüli objektumok célzott esetei, detektálási késés és téves észlelések. |
| 3 | Lokalizációs sodródás, forráskiesés és állapotbecslés egyszerű szűrővel. | A minőségromlás és a kiegészítő helyzetforrás haszna értékelhető. | Becslési hiba idősora, helyreállítási idő, lassítás/megállás minőségromláskor. |
| 4 | Teljes kihajtási út, több parkolóterület és több kijárat közös kiszolgálása. | A teljes járműéletút és az átmenő forgalom is mérhető. | Kapacitás, sorhossz, várakozás, zónák holtpontvizsgálata és útelőzmények teljessége. |

Az első fejlesztés érinti a jelenlegi útvonal-, fékezési és zónamodell geometriai feltevéseit. Önálló, összehasonlítható kísérleti változat és új tesztkampány indokolt. A jelenlegi nulla modellezett ütközés nem vihető át automatikusan erre a változatra.

## Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Verifikáció |
| --- | --- | --- |
| A nézet a ténylegesen mintavételezett környezetet mutassa. | A szimulátorból publikált, időbélyeges `sensors` mező; a megfigyelt objektumok elkülönülnek a térképi járművektől. | MQTT-telemetria és HTTP-állapot összevetése; hatótávolságon kívüli objektum tesztje. |
| Elavult megfigyelés ne jelenjen meg friss mérésként. | Eltárolt minta, adatkor, `fresh` és `fault_active` mezők; külön felületi jelzés. | Kieséskor változatlan minta, növekvő kor és fékezési megállás; helyreálláskor új minta. |
| A diagnosztika ne változtassa meg az ellenőrzött forgalmi viselkedést. | Külön lokalizációs véletlenszám-generátor, visszacsatolás nélküli diagnosztikai útvonalvetítés. | Azonos környezeti konfiguráció és mag mellett az eredeti és új lokalizációs zaj nélküli/eltérő lokalizációs zajú mozgás összevetése. |

A `python verify_units.py` a célzott érzékelőteszteket is futtatja. A `python -m tests.sensor_mqtt_smoke` valódi helyi MQTT-közvetítővel, külön munkafolyamatokkal és HTTP-lekérésekkel ellenőrzi a mérés, a kiesés, a fékezési megállás és a helyreállás láncát. A tényleges eredmény a `results/sensor-smoke-*/summary.json` fájlban található. A működés UML-áttekintése a `sensor_flow.puml` szerkeszthető forrásból készíthető; az ábra saját szerkesztés, a megvalósított adatút alapján.

A 2026. október 5-i futásban 121 egységteszt, köztük 11 célzott érzékelőteszt sikeres volt. A valódi MQTT/HTTP-szenzorteszt `6dcd7b8e1f3d` futásazonosítóval sikerült, nulla modellezett ütközéssel; a fogadott szenzortelemetria példakeretei is mentésre kerültek. A böngészőben a rétegkapcsolás, az aktív és archivált autó, az előnézet, a kiesés és helyreállás, valamint a 390 px széles mobilnézet ellenőrzése megtörtént. Egy tényleges mintában három közeli autó és egy átkelő gyalogoshoz társított zebraállapot volt látható. A hordozható eredmény a `results/SENSOR_MQTT_RENDSZERTESZT.json` fájlban szerepel.

## Irodalom

[1] Robert Bosch GmbH, “Driverless vehicles: Bosch’s automated vehicle maneuvering boosts automakers’ efficiency,” sajtóközlemény PI11665, 2023. ápr. 25., pp. 1–2. [Online]. Elérhető: https://www.bosch-presse.de/pressportal/de/media/dam_images/pi11665/pi11665_en.pdf. Hozzáférés: 2026. okt. 5.

[2] Autoware Foundation, “Localization component design,” *Autoware Documentation*, frissülő webdokumentáció. [Online]. Elérhető: https://docs.autoware.org/main/design/autoware-architecture-v1/components/localization/. Hozzáférés: 2026. okt. 5.
